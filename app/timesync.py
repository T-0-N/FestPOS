"""Uhrzeit der Pi (T2).

- Woher die Zeit kommt: beim Start aus dem RTC (Pi 5 mit Batterie), mit Internet von chrony, sonst von den anderen
  Pi (chrony; ein Pi ohne gültiges RTC holt sie beim Start auch selbst, peer_sync) und von der Festleitung
  (Uhrzeit übernehmen, manuell). Die Zeitzone stellt festpos-node.sh bei jedem Start ein (TZ, Europe/Zurich).
- Die Handys stellen den Pi nicht mehr (T2): sie holen ihre Zeit umgekehrt vom Pi (Zeitserver per USB/adb,
  deploy/festpos-handy-usb). /api/time/sync meldet nur noch, wie weit die Uhr eines Handys abweicht.
- Bestätigung (Einstellung time_confirmed): wann die Zeit zuletzt aus dem Internet kam oder von einem Menschen
  gesetzt bzw. bestätigt wurde. Ist das zu lange her, war seither ein Neustart oder ist eine Uhr unsicher, fragt die
  Verwaltung die Festleitung nach der Anmeldung (prompt_reasons).
- Setzen geschieht über den root-Dienst festpos-settime.path (Auftrag in /run/festpos/zeit, kein sudo). Er notiert
  in /run/festpos/zeit-stand, dass die Uhr seit dem Start gestellt wurde (nach einem Neustart ist /run leer).
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import ssl
import subprocess
import threading
import time
import urllib.request
from urllib.parse import urlsplit

log = logging.getLogger("festpos.time")
THRESHOLD_S = 120               # ab dieser Abweichung (Browser, Handy) gilt eine Uhr als falsch
CLUSTER_SPREAD_S = 5            # so weit dürfen die Pi untereinander auseinander liegen
CONFIRM_RESTART_S = 7 * 86400   # nach einem Neustart nachfragen, wenn die Bestätigung älter ist
CONFIRM_MAX_S = 60 * 86400      # ohne Neustart nachfragen, wenn die Bestätigung älter ist
NTP_CONFIRM_EVERY_S = 86400     # Internet-Zeit höchstens einmal pro Tag als Bestätigung schreiben
RTC_WEAK_UV = 2_500_000         # Batterie darunter: schwach (Mikrovolt, wie /sys meldet)
FLOOR_EPOCH = 1791590400        # 10.10.2026 – früher kann es nicht sein, diese Version gab es da noch nicht
PLACEHOLDER_SECRETS = {"", "admin", "bitte-aendern", "bitte-aendern-lang-und-zufaellig"}
SOURCE_RE = re.compile(r"[a-z]{1,12}")   # Quelle eines Auftrags (take, ok, manual, peer) – geht an den root-Dienst

RTC_DIR = os.environ.get("FESTPOS_RTC_DIR", "/sys/class/rtc/rtc0")
RTC_DRIFT_S = 120                     # RTC weicht mehr ab: hat die Zeit nicht gehalten (T16)
ZEIT_STAND = os.environ.get("FESTPOS_ZEIT_STAND", "/run/festpos/zeit-stand")
CHRONYC = os.environ.get("FESTPOS_CHRONYC", "chronyc")
RTC_NEUSTART = os.environ.get("FESTPOS_RTC_NEUSTART", "/run/festpos/rtc-neustart")   # config.txt geändert (festpos-node)
RTC_AKKU = os.environ.get("FESTPOS_RTC_AKKU", "0") == "1"      # festpos.conf: aufladbare Batterie (SC1163)


def secret_usable(secret: str | None) -> bool:
    return (secret or "").strip() not in PLACEHOLDER_SECRETS


def time_floor(store) -> float:
    """Frühester plausibler Zeitpunkt: letzte Bestellung, letztes ausgestelltes Gerätezertifikat oder das
    Erscheinen dieser Version. Steht eine Uhr davor, ist sie sicher falsch."""
    floor = float(FLOOR_EPOCH)
    row = store.query_one("SELECT (SELECT MAX(created_at) FROM orders) AS o, "
                          "(SELECT MAX(created_at) FROM device_certs) AS c")
    for v in (row or {}).values():
        floor = max(floor, local_epoch(v) or 0.0)
    return floor


def local_epoch(v) -> float | None:
    """Zeitstempel der Datenbank («2026-10-10T18:30:00», Ortszeit) -> Sekunden; None, wenn keiner."""
    try:
        return time.mktime(time.strptime(str(v)[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError, OverflowError):
        return None


def local_to_epoch(text: str) -> float:
    """«2026-10-28T15:49» bzw. mit Sekunden (Feld datetime-local) in der Zeitzone DIESES Pi -> Sekunden.
    So verschiebt ein Browser in einer anderen Zeitzone nichts. ValueError bei Unsinn."""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?", (text or "").strip())
    if not m:
        raise ValueError(text)
    y, mo, d, h, mi, s = (int(x or 0) for x in m.groups())
    t = time.mktime((y, mo, d, h, mi, s, 0, 0, -1))
    if time.localtime(t)[:5] != (y, mo, d, h, mi):        # 31.02. oder eine Uhrzeit, die es (Sommerzeit) nicht gibt
        raise ValueError(text)
    return t


# -- Zustand dieses Pi ---------------------------------------------------------------------------------------
def _read(path: str) -> str | None:
    try:
        with open(path, encoding="ascii", errors="replace") as f:
            return f.read(200).strip()
    except OSError:
        return None


def _int(v: str | None) -> int | None:
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def rtc_state() -> dict | None:
    """RTC des Pi 5: beim Start daraus gestellt (hctosys), Batterie und Laden (Mikrovolt; Laden 0 = aus), ob die
    aufladbare Batterie angegeben ist (RTC_AKKU) und ob das Laden erst nach dem nächsten Neustart gilt.
    None ohne RTC (anderer Rechner, Pi ohne RTC)."""
    if not os.path.isdir(RTC_DIR):
        return None
    rtc_now = _int(_read(os.path.join(RTC_DIR, "since_epoch")))
    drift = rtc_now - int(time.time()) if rtc_now is not None else None
    boot = _read(os.path.join(RTC_DIR, "hctosys")) == "1"
    # T16: «lost» = die Uhr kam beim Start nicht aus der RTC, oder die RTC geht jetzt weit daneben (sie hat die Zeit
    # beim Stromunterbruch nicht gehalten, z. B. auf 2000/1970 zurückgefallen) – dann gilt die Uhr als unsicher.
    lost = not boot or (drift is not None and abs(drift) > RTC_DRIFT_S)
    return {"boot": boot, "battery_uv": _int(_rtc_value("battery_voltage")),
            "charging_uv": _int(_rtc_value("charging_voltage")), "drift": drift, "lost": lost,
            "akku": RTC_AKKU, "restart": os.path.exists(RTC_NEUSTART), "weak_uv": RTC_WEAK_UV}


def _rtc_value(name: str) -> str | None:
    """Batteriewerte: beim Pi 5 direkt unter rtc0/ (T16), bei älteren Kerneln unter rtc0/device/."""
    v = _read(os.path.join(RTC_DIR, name))
    return v if v is not None else _read(os.path.join(RTC_DIR, "device", name))


def set_state() -> dict | None:
    """Seit dem Start gestellt? (festpos-settime schreibt zeit-stand: {"at": Sekunden, "source": …})"""
    try:
        with open(ZEIT_STAND, encoding="utf-8") as f:
            d = json.loads(f.read(500))
        return {"at": float(d["at"]), "source": str(d.get("source") or "")[:12]}
    except (OSError, ValueError, KeyError, TypeError):
        return None


_chrony_cache: tuple[float, str | None] = (0.0, None)
_chrony_lock = threading.Lock()


def chrony_source(peer_ips: list[str] | tuple = ()) -> str | None:
    """Woher chrony die Zeit nimmt: "internet", "peer:<ip>" (anderer Pi), "local" (eigene Uhr, Orphan-Modus) oder
    None (nicht synchron, chrony fehlt). Lesende Abfrage ohne root (UDP 127.0.0.1:323), 30 s zwischengespeichert."""
    global _chrony_cache
    with _chrony_lock:
        at, val = _chrony_cache
        if time.monotonic() - at < 30 and at:
            return val
        try:
            out = subprocess.run([CHRONYC, "-n", "-c", "tracking"], capture_output=True, text=True, timeout=2).stdout
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        val = parse_tracking(out, peer_ips)
        _chrony_cache = (time.monotonic(), val)
        return val


def parse_tracking(out: str, peer_ips: list[str] | tuple = ()) -> str | None:
    """Ausgabe von «chronyc -n -c tracking» (Referenz-ID, Adresse, Stratum, … , Schaltsekunden-Status)."""
    f = (out or "").strip().splitlines()[0].split(",") if (out or "").strip() else []
    if len(f) < 3 or "not synchronised" in f[-1].lower():
        return None
    ref, ip = f[0].upper(), f[1].strip()
    if ref in ("7F7F0101", "4C4F434C") or ip in ("", "127.127.1.1") or f[2] == "0":   # LOCL: eigene Uhr
        return "local"
    return f"peer:{ip}" if ip in peer_ips else "internet"


def tz_info(now: float | None = None) -> dict:
    """Zeitzone dieses Pi (aus TZ bzw. /etc/localtime), Abstand zu UTC und ob Sommerzeit gilt."""
    name = os.environ.get("TZ", "").lstrip(":")
    if not name:
        real = os.path.realpath("/etc/localtime")
        name = real.split("zoneinfo/", 1)[1] if "zoneinfo/" in real else time.tzname[0]
    lt = time.localtime(now)
    return {"tz": name, "offset_min": lt.tm_gmtoff // 60, "dst": lt.tm_isdst > 0}


def boot_epoch() -> float | None:
    try:
        return round(time.time() - float((_read("/proc/uptime") or "").split()[0]))
    except (ValueError, IndexError):
        return None


def state(node: str, peer_ips: list[str] | tuple = ()) -> dict:
    """Zeitstand dieses Pi. «sure»: Die Uhr kommt aus einer verlässlichen Quelle (RTC beim Start, seither gestellt
    oder Internet). Sonst kam sie beim Start von fake-hwclock (RTC-Batterie leer) und kann weit daneben liegen."""
    now = time.time()
    rtc, st, src = rtc_state(), set_state(), chrony_source(peer_ips)
    return {"node": node, "time": now, **tz_info(now), "boot": boot_epoch(), "rtc": rtc, "set": st, "ntp": src,
            "sure": bool((rtc and not rtc["lost"]) or st or src == "internet")}


# -- Bestätigung -----------------------------------------------------------------------------------------------
CONFIRM_KEY = "time_confirmed"


def confirmation(store) -> dict | None:
    """{"at": Sekunden, "how": ok|take|manual|ntp, "by": Name, "node": Pi} oder None (noch nie bestätigt)."""
    try:
        d = json.loads(store.setting(CONFIRM_KEY) or "null")
        return {"at": float(d["at"]), "how": str(d.get("how") or ""), "by": str(d.get("by") or ""),
                "node": str(d.get("node") or "")}
    except (ValueError, KeyError, TypeError):
        return None


def confirm_stmt(how: str, by: str, node: str, at: float | None = None) -> tuple[str, tuple]:
    value = json.dumps({"at": round(at if at is not None else time.time()), "how": how, "by": by, "node": node})
    return ("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (CONFIRM_KEY, value))


def prompt_reasons(states: list[dict], conf: dict | None, now: float | None = None) -> list[str]:
    """Warum die Festleitung die Uhrzeit prüfen soll (leer: nicht nötig). states: Zeitstand jedes erreichbaren Pi,
    mit «offset» = Abweichung zu diesem Pi in Sekunden. Die Abweichung zum Browser prüft die Verwaltung selbst."""
    now = time.time() if now is None else now
    out = []
    if not conf:
        out.append("never")
    else:
        age = now - conf["at"]
        if age > CONFIRM_MAX_S:
            out.append("old")
        elif age > CONFIRM_RESTART_S and any((s.get("boot") or 0) > conf["at"] for s in states):
            out.append("restart")
    if any(not s.get("sure") for s in states):
        out.append("unsure")
    offsets = [s.get("offset") or 0.0 for s in states]
    if offsets and max(offsets) - min(offsets) > CLUSTER_SPREAD_S:
        out.append("cluster")
    return out


def due(conf: dict | None) -> float | None:
    """Wann ohne Neustart spätestens wieder nachgefragt wird (Sekunden) – für die Anzeige."""
    return conf["at"] + CONFIRM_MAX_S if conf else None


# -- Stellen ------------------------------------------------------------------------------------------------
ZEIT_DIR = os.environ.get("FESTPOS_ZEIT_DIR", "/run/festpos/zeit")                  # Aufträge (gehört festpos)
ZEIT_NEU = os.environ.get("FESTPOS_ZEIT_NEU", "/run/festpos/zeit-neu")               # zum Schreiben (festpos)
ZEIT_ERGEBNIS = os.environ.get("FESTPOS_ZEIT_ERGEBNIS", "/run/festpos/zeit-ergebnis")  # Antworten (gehört root)


def set_local(epoch_ms: int, wait_s: float = 10.0, source: str = "") -> tuple[bool, str]:
    """Auftrag an den root-Dienst festpos-settime.path (kein sudo, die App läuft mit NoNewPrivileges).
    Jeder Auftrag hat eine eigene Datei und eine eigene Antwort – gleichzeitige Aufträge gehen nicht verloren."""
    if not os.path.isdir(ZEIT_DIR) or not os.path.isdir(ZEIT_NEU):
        return False, "Zeit-Dienst fehlt (festpos-settime.path) – update.sh ausführen bzw. Pi neu starten"
    auftrag = secrets.token_hex(8)
    tmp = os.path.join(ZEIT_NEU, auftrag)
    src = f" {source}" if SOURCE_RE.fullmatch(source or "") else ""
    try:
        with open(tmp, "w") as f:                  # erst fertig schreiben, dann in einem Schritt ablegen
            f.write(f"{auftrag} {int(epoch_ms) // 1000}{src}\n")
        os.replace(tmp, os.path.join(ZEIT_DIR, f"anfrage-{auftrag}"))
    except OSError as e:
        return False, f"Auftrag nicht ablegbar: {e}"
    antwort = os.path.join(ZEIT_ERGEBNIS, auftrag)
    ende = time.monotonic() + wait_s
    while time.monotonic() < ende:
        try:
            with open(antwort) as f:
                teile = f.read().strip().split(" ", 1)
            return teile[0] == "ok", teile[1] if len(teile) > 1 else ""
        except OSError:
            time.sleep(0.2)
    return False, "Zeit-Dienst antwortet nicht"


def _others(peers: list[str], self_ip: str) -> list[str]:
    return [b for b in peers if not self_ip or urlsplit(b).hostname != self_ip]


def broadcast(peers: list[str], epoch_ms: int, secret: str, cafile: str | None, self_ip: str = "",
              received: float | None = None, source: str = "") -> dict[str, str]:
    """Zuerst an die anderen Pi, dann sich selbst stellen.

    Die Zeit seit dem Empfang wird mit der monotonen Uhr gemessen (sie springt nicht, wenn eine Systemuhr
    gestellt wird) und dazugerechnet, damit späte Empfänger nicht nachgehen."""
    t0 = time.monotonic() if received is None else received

    def now_ms() -> int:
        return int(epoch_ms + (time.monotonic() - t0) * 1000)

    others = _others(peers, self_ip)
    out: dict[str, str] = {}
    if others and not secret_usable(secret):
        for base in others:
            out[base] = "übersprungen: CLUSTER_SECRET nicht gesetzt (chrony gleicht die Pi an)"
        others = []
    if others and not cafile:                          # Geheimnis nur an ein geprüftes Zertifikat
        for base in others:
            out[base] = "übersprungen: FESTPOS_CA fehlt, Zertifikat nicht prüfbar (chrony gleicht die Pi an)"
        others = []
    ctx = ssl.create_default_context(cafile=cafile) if others else None
    for base in others:
        body = json.dumps({"epoch_ms": now_ms(), "source": source}).encode()
        req = urllib.request.Request(f"{base.rstrip('/')}/api/internal/settime", data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              # UTF-8-Bytes, damit jedes Zeichen im Geheimnis geht
                                              "X-Festpos-Secret": secret.encode().decode("latin-1")})
        try:
            with urllib.request.urlopen(req, timeout=3, context=ctx) as resp:
                out[base] = f"ok ({resp.status})"
        except Exception as e:  # noqa: BLE001 – Peer evtl. aus, das ist ok
            out[base] = f"fehlgeschlagen: {e}"
    ok, msg = set_local(now_ms(), source=source)
    out["lokal"] = "ok" if ok else (msg or "fehlgeschlagen")
    return out


def ask_peers(path: str, peers: list[str], secret: str, cafile: str | None, self_ip: str = "",
              timeout: float = 2.0) -> dict[str, tuple[dict, float, float] | str]:
    """GET path bei allen anderen Pi, parallel, mit CLUSTER_SECRET und nur an ein geprüftes Zertifikat (Zeit, T2;
    Pi-Infos und Drucker, T9). Pro Pi: (Antwort, gesendet um, Antwortzeit s) oder der Fehler als Text."""
    others = _others(peers, self_ip)
    if not secret_usable(secret) or not cafile:
        return {b: "CLUSTER_SECRET oder FESTPOS_CA fehlt" for b in others}
    out: dict[str, tuple[dict, float, float] | str] = {b: "keine Antwort" for b in others}
    ctx = ssl.create_default_context(cafile=cafile)

    def ask(base: str) -> None:
        req = urllib.request.Request(f"{base.rstrip('/')}{path}",
                                     headers={"X-Festpos-Secret": secret.encode().decode("latin-1")})
        t0, w0 = time.monotonic(), time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                d = json.loads(resp.read(200_000))
        except Exception as e:  # noqa: BLE001 – Pi aus oder alt
            out[base] = str(e) or type(e).__name__
            return
        out[base] = (d, w0, time.monotonic() - t0) if isinstance(d, dict) else "keine gültige Antwort"
    threads = [threading.Thread(target=ask, args=(b,), daemon=True) for b in others]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 1)
    return out


def peer_states(peers: list[str], secret: str, cafile: str | None, self_ip: str = "",
                timeout: float = 2.0) -> dict[str, dict | None]:
    """Zeitstand der anderen Pi (/api/internal/time), parallel. «offset»: Abweichung zu diesem Pi, um die halbe
    Antwortzeit korrigiert. None: nicht erreichbar bzw. ohne Geheimnis/CA nicht abfragbar."""
    out: dict[str, dict | None] = {}
    for base, res in ask_peers("/api/internal/time", peers, secret, cafile, self_ip, timeout).items():
        out[base] = None
        if isinstance(res, str):
            if secret_usable(secret) and cafile:
                log.info("Zeitstand von %s nicht lesbar: %s", base, res)
            continue
        d, w0, rtt = res
        if isinstance(d.get("time"), (int, float)):
            d["offset"] = round(d["time"] - (w0 + rtt / 2), 2)
            out[base] = d
    return out
