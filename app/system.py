"""Die Pi selbst: Infos pro Pi (Modell, Betriebssystem, Laufzeit, Temperatur, Speicher) und Herunterfahren /
Neu starten aus der Verwaltung – einzeln oder alle, auf Wunsch zusammen mit den Handys (über Fully, fully.py:
App oder Handy neu starten, Handy ausschalten, Bildschirm aus).

Ausführen darf das nur root: die App legt pro Pi einen Auftrag in die Datenbank (display_cfg.<pi>.power), jeder
Pi schreibt seinen Auftrag nach /var/lib/festpos/display.json (display._display_sync, alle 10 s), und
festpos-display (root) führt ihn zum gemeinsamen Zeitpunkt «at» aus. Alle Pi gleichzeitig: sonst bliebe der
letzte ohne Mehrheit übrig und könnte den Auftrag nicht mehr lesen.

Pi-Infos (T9): Der dauerhafte Teil (Version, Dienste, Sicherung …) steht in der Datenbank (node_info.<pi>) und wird nur
bei einer Änderung geschrieben. Den Rest holen sich die Pi alle 10 s direkt beim anderen (/api/internal/node).
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import secrets
import shutil
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import backup, fully, fully_apk, i18n, printer_net, update as update_mod
from .core import APP_VERSION, Config, Throttle, admin, cluster_info, db, log, require_cluster_secret, rt
from .display import _display_cfg, _display_nodes, _display_sync, _display_update

router = APIRouter()
admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])
peer_throttle = Throttle()

# T27: Herunterfahren/Neustart kündigt sich an – Bestell-App, Statusanzeige und Verwaltung zeigen dieselbe Meldung mit
# Countdown (Einstellung «power_notice»). Die Handys fragen alle 10 s nach, jeder Pi liest seinen Auftrag alle 10 s.
DELAY = 30          # Sekunden bis die Pi herunterfahren bzw. neu starten (einzeln oder alle)
PHONES_BEFORE = 5   # so viele Sekunden vor den Pi bekommen die Handys (falls gewählt) den Fully-Befehl
CANCEL_UNTIL = 12   # «Abbrechen» nur, solange noch so viel Zeit bleibt (jeder Pi muss den Abbruch noch lesen)
NOTICE_AFTER = 180  # nach dem Zeitpunkt noch so lange melden (Neustart dauert); danach gilt die Meldung als vorbei
NOTICE_KEY = "power_notice"
STARTED = time.time()                                 # Start dieses Servers: später gestartet = Auftrag erledigt
_phone_tasks: dict[str, asyncio.Task] = {}
PEER_MAX_AGE = 30   # Antwort eines anderen Pi so lange als «live» verwenden (er wird alle 10 s gefragt, T9)
# Was in die Datenbank kommt (node_info.<pi>), wenn es sich ändert. Temperatur, freier Speicher und Laufzeit ändern
# ständig: die gibt es nur live (dieser Pi selbst, die anderen über /api/internal/node). Jeder Schreibzugriff geht
# über rqlite auf die SD-Karten aller Pi (T9).
DURABLE = ("version", "kernel", "model", "os", "ip", "upd", "svc", "ram_total", "disk_total", "backup", "apk", "pnet",
           "harden", "osup", "krypt")
# Start nur von der SD-Karte, Bluetooth aus, keine Anmeldung an der Konsole: schreibt festpos-haertung (root) bei
# jedem Start und Update
HAERTUNG_STATE = os.environ.get("FESTPOS_HAERTUNG_STATE", "/run/festpos-haertung.json")
BOOT_STATES = ("ok", "open", "pending", "failed", "no_tool", "unreadable")


# -- Infos dieses Pi --------------------------------------------------------------------------------------
def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _net_status() -> dict | None:
    from . import update_net
    try:
        return update_net.keep_status()
    except Exception:  # noqa: BLE001 – Infos des Pi kommen trotzdem
        return None


def harden_status() -> dict | None:
    """Stand von festpos-haertung: {boot, bt_off, console_off} – None, wenn (noch) nicht geschrieben."""
    try:
        d = json.loads(_read(HAERTUNG_STATE) or "null")
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    boot = d.get("boot") if d.get("boot") in BOOT_STATES else "unreadable"
    return {"boot": boot, "bt_off": d.get("bt_off") is True, "console_off": d.get("console_off") is True}


# Betriebssystem (T-0-N/FestPOS#5): Stand schreibt festpos-os-update (root); ohne Stand das Datum der Paketdatenbank
OS_STAND = os.environ.get("FESTPOS_OS_STAND", "/var/lib/festpos-os/stand.json")
DPKG_STATUS = os.environ.get("FESTPOS_DPKG_STATUS", "/var/lib/dpkg/status")
REBOOT_FLAG = os.environ.get("FESTPOS_REBOOT_FLAG", "/run/reboot-required")
OS_OLD_DAYS = 183


def os_status() -> dict | None:
    """{date: Stand der Pakete (JJJJ-MM-TT), result, error, open, kernel, reboot} – None ohne dpkg (z.B. Tests)."""
    try:
        d = json.loads(_read(OS_STAND) or "{}")
    except ValueError:
        d = {}
    d = d if isinstance(d, dict) else {}
    date = d.get("date")
    if not date:
        try:
            date = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(DPKG_STATUS)))
        except OSError:
            return None
    return {"date": str(date)[:10], "result": d.get("result"), "error": str(d.get("error") or "")[:300],
            "open": d.get("open"), "kernel": platform.release(), "reboot": os.path.exists(REBOOT_FLAG)}


def _fw_status() -> dict | None:
    from . import firewall
    try:
        return firewall.local_status()
    except Exception:  # noqa: BLE001 – Infos des Pi kommen trotzdem
        return None


def _krypt_status() -> dict | None:
    from . import krypt
    try:
        return krypt.local_status()
    except Exception:  # noqa: BLE001 – Infos des Pi kommen trotzdem
        return None


def _tunnel_status() -> dict | None:
    from . import tunnel
    try:
        return tunnel.local_status()
    except Exception:  # noqa: BLE001 – Infos des Pi kommen trotzdem
        return None


def local_info() -> dict:
    info: dict = {"version": APP_VERSION, "kernel": platform.release(), "python": platform.python_version(),
                  "ip": Config.node_ip, "upd": update_mod.local_state(),     # upd: Update-Helfer, Schlüssel, Sicherung
                  # Dienste dieses Pi (Statusanzeige und Verwaltung zeigen sie pro Pi): {name: läuft?}
                  "svc": {x["name"]: bool(x.get("ok")) for x in (rt.monitor.services or [])},
                  "backup": backup.read_status(),                 # automatische Sicherung auf den USB-Stick
                  "apk": fully_apk.local_checksum(),              # welche Fully-APK dieser Pi hat (T6)
                  "net": _net_status(),                           # dauerhaft verbunden (Internet c), nur live
                  "tunnel": _tunnel_status(),                     # Tunnel zur QR-Bestellung auf diesem Pi (T22)
                  "pnet": printer_net.local_state(),              # Drucker-Netz: VLAN-Schnittstelle eingerichtet?
                  "harden": harden_status(),                      # Start nur von SD, Bluetooth, Konsole
                  "fw": _fw_status(),                             # Firewall ins Internet (#10), nur live
                  "osup": os_status(),                            # Updates des Betriebssystems (#5)
                  "krypt": _krypt_status()}                       # Daten verschlüsselt (#3)
    model = _read("/proc/device-tree/model").strip("\x00\n ")
    info["model"] = model or platform.machine()
    for line in _read("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            info["os"] = line.split("=", 1)[1].strip().strip('"')
    try:
        info["boot"] = int(time.time() - float(_read("/proc/uptime").split()[0]))
    except (ValueError, IndexError):
        pass
    try:
        info["temp"] = round(int(_read("/sys/class/thermal/thermal_zone0/temp").strip()) / 1000)
    except ValueError:
        pass
    mem = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        if k in ("MemTotal", "MemAvailable"):
            mem[k] = int(v.split()[0]) * 1024
    if mem.get("MemTotal"):
        info["ram_total"], info["ram_free"] = mem["MemTotal"], mem.get("MemAvailable")
    data = os.environ.get("FESTPOS_DATA", "/var/lib/festpos")
    try:
        du = shutil.disk_usage(data if os.path.isdir(data) else "/")
        info["disk_total"], info["disk_free"] = du.total, du.free
    except OSError:
        pass
    return info


_published: dict = {"at": 0.0, "static": None, "info": None}   # at, info: letzte Abfrage; static: zuletzt geschrieben
_publish_lock = threading.Lock()


def durable(info: dict) -> dict:
    """Der Teil der Infos, der in die Datenbank gehört. Bei der Sicherung zählt der Zeitpunkt nicht (alle 15 min neu),
    nur Ergebnis, letzte gelungene und Datei."""
    d = {k: info.get(k) for k in DURABLE}
    if isinstance(d["backup"], dict):
        d["backup"] = {k: v for k, v in d["backup"].items() if k != "at"}
    from . import krypt
    d["krypt"] = krypt.durable(d["krypt"])
    return d


def publish() -> None:
    """Alle 10 s (Monitor): Infos dieses Pi abfragen; in die Datenbank nur, wenn sich der dauerhafte Teil geändert
    hat (T9). Nacheinander (Sperre): sonst überschreibt ein Aufruf mit älteren Infos den neueren."""
    with _publish_lock:
        _publish()


def _publish() -> None:
    info = local_info()
    now = time.time()
    _published.update(at=now, info=info)
    static = durable(info)
    if static == _published["static"]:
        return
    rt.store.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (f"node_info.{Config.node}", json.dumps({**info, "seen": int(now)})))
    _published["static"] = static                  # erst nach dem Schreiben: misslingt es, beim nächsten Mal nochmals


def live_info() -> tuple[dict, float]:
    """Infos dieses Pi: die letzte Abfrage des Monitors (höchstens 30 s alt), sonst jetzt. -> (Infos, Zeitpunkt)"""
    if _published["info"] is not None and time.time() - _published["at"] <= PEER_MAX_AGE:
        return _published["info"], _published["at"]
    return local_info(), time.time()


def node_report() -> dict:
    """Antwort an die anderen Pi (/api/internal/node): Infos dieses Pi und, wenn er druckt, der Stand der Drucker."""
    info, at = live_info()
    leader = bool(rt.spooler.is_leader) if getattr(rt, "spooler", None) else False
    from . import update_net
    return {"node": Config.node, "at": at, "info": info, "leader": leader,
            "printers": rt.monitor.printers_here() if leader else None,
            "internet": update_net.keep_report()}               # dauerhaft verbunden / weitergegeben (Internet c)


@router.get("/api/internal/node")
async def internal_node(request: Request):
    """Für die anderen Pi (Monitor, alle 10 s). Mit CLUSTER_SECRET; kommt nicht in die Datenbank (T9)."""
    require_cluster_secret(request, peer_throttle)
    return await db(node_report)


def tick() -> None:
    """Zusatz-Aufgabe des Monitors: Bildschirm-/Power-Auftrag ablegen, Infos melden."""
    _display_sync()
    try:
        publish()
    except Exception:  # noqa: BLE001 – Infos sind nur Anzeige
        log.exception("Pi-Infos nicht gemeldet")


def _info_of(n: str, now: float) -> tuple[dict, int | None]:
    """(Infos, Alter in s) eines Pi: dieser selbst und erreichbare live, sonst der letzte Stand aus der Datenbank."""
    if n == Config.node:
        info, at = live_info()
        return {**info, "seen": int(at)}, int(now - at)
    peer = rt.monitor.peer(n, PEER_MAX_AGE)
    if peer and isinstance(peer["data"].get("info"), dict):
        return {**peer["data"]["info"], "seen": int(peer["at"])}, int(now - peer["at"])
    try:
        data = json.loads(rt.store.setting(f"node_info.{n}") or "{}")
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    last = max(rt.monitor.peer_seen(n) or 0, data.get("seen") or 0)    # zuletzt live gesehen oder geschrieben
    return data, int(now - last) if last else None


def nodes(info: dict | None = None) -> list[dict]:
    """Alle Pi mit Rolle, Erreichbarkeit und Infos (live bzw. zuletzt gemeldet)."""
    info = cluster_info() if info is None else info
    raw = (info.get("nodes") or {}) if info.get("mode") == "rqlite" else {}
    out = []
    now = time.time()
    for n in sorted(raw) or [Config.node]:
        data, age = _info_of(n, now)
        st = raw.get(n) or {}
        here = n == Config.node
        out.append({"id": n, "here": here, "leader": bool(st.get("leader")) if raw else rt.spooler.is_leader if here else False,
                    "reachable": bool(st.get("reachable")) if raw else True, "info": data, "info_age": age})
    return out


# -- Verwaltung -------------------------------------------------------------------------------------------
PHONE_ACTIONS = {"none": None, "restart_app": "restartApp", "reboot": "rebootDevice", "shutdown": "shutdownDevice",
                 "screen_off": "screenOff"}


class PowerIn(BaseModel):
    action: str = Field(pattern="^(reboot|poweroff)$")
    node: str = Field(default="all", max_length=40)          # "all" oder ein Pi (a, b, c)
    phones: str = Field(default="none", pattern="^(" + "|".join(PHONE_ACTIONS) + ")$")


def _phones_in_use(cfg: dict) -> list[dict]:
    return [t for t in cfg["targets"] if t.get("active", True)]


@admin_router.get("/netinfo")
async def netinfo():
    """FestPOS-Netz, VIP und Beispieladressen für Vorschläge in der Verwaltung (T-0-N/FestPOS#2)."""
    from . import netz
    return netz.info()


@admin_router.post("/os/online")
async def os_online():
    """Sicherheitsupdates mit Internet direkt über apt holen (nur Sicherheitsquellen, festpos-os-update --online über
    festpos-wiederherstellen). Läuft einige Minuten; Ergebnis im Stand des Pi (info.osup)."""
    from . import cards
    job = await db(cards.submit, "os_online", {})
    return {"started": True, "job": job}


@admin_router.get("/system")
async def system_overview():
    cfg = await db(fully.config)
    return {"nodes": await db(nodes), "phones_fully": len(_phones_in_use(cfg)), "fully_ready": bool(cfg["password"]),
            "phone_actions": list(PHONE_ACTIONS)}


def _notice_raw() -> dict | None:
    try:
        n = json.loads(rt.store.setting(NOTICE_KEY) or "null")
    except ValueError:
        return None
    return n if isinstance(n, dict) and n.get("token") and isinstance(n.get("at"), (int, float)) else None


def power_notice() -> dict | None:
    """Für Bestell-App, Statusanzeige und Verwaltung (T27): {action, left (Sekunden bis zum Zeitpunkt), token,
    cancel (lässt sich noch abbrechen)} – oder None. Ist dieser Server erst nach dem Zeitpunkt gestartet, ist der
    Auftrag erledigt: die Meldung wird gelöscht."""
    n = _notice_raw()
    if not n:
        return None
    now = time.time()
    if STARTED > n["at"]:                                 # neu gestartet: vorbei
        rt.store.execute("DELETE FROM settings WHERE key=? AND value=?", (NOTICE_KEY, json.dumps(n)))
        return None
    left = n["at"] - now
    if left < -NOTICE_AFTER:
        return None
    return {"action": n.get("action"), "left": max(0, round(left)), "token": n["token"], "cancel": left > CANCEL_UNTIL,
            "pis": list(n.get("nodes") or []), "all": n.get("all", True), "phones": bool(n.get("phones"))}


def phone_notice() -> dict | None:
    """Bestell-App (T32): nur, wenn das ganze System betroffen ist oder die Handhelds mit ausgeschaltet
    werden. Startet nur ein Pi von mehreren neu, geht Bestellen weiter – keine Meldung."""
    n = power_notice()
    return n if n and (n["all"] or n["phones"]) else None


async def _phones_later(token: str, at: float, cmd: str) -> None:
    """Die Handys erst kurz vor den Pi über Fully ausschalten – vorher sollen sie die Meldung zeigen. Wurde inzwischen
    abgebrochen (auch an einem anderen Pi), geht nichts hinaus."""
    try:
        await asyncio.sleep(max(0.0, at - PHONES_BEFORE - time.time()))
        if (await db(_notice_raw) or {}).get("token") != token:
            return
        cfg = await db(fully.config)
        res = await fully.command_all(_phones_in_use(cfg), cfg["port"], cfg["password"], cmd)
        log.warning("Herunterfahren: Fully-Befehl %s an %d Handys (%s)", cmd, len(res),
                    ", ".join(f"{x.get('name')}: {x.get('state')}" for x in res.values()))
    except asyncio.CancelledError:
        pass
    finally:
        _phone_tasks.pop(token, None)


@admin_router.post("/system/power")
async def system_power(p: PowerIn, request: Request):
    """Pi neu starten / herunterfahren (einzeln oder alle). T27: zuerst die Meldung an alle (Countdown), nach DELAY
    Sekunden die Pi; die Handys (falls gewählt) über Fully PHONES_BEFORE Sekunden vorher."""
    all_nodes = await db(_display_nodes)
    targets = all_nodes if p.node == "all" else [p.node]
    if not set(targets) <= set(all_nodes):
        raise HTTPException(404, i18n.t("err.display.unknown_pi"))
    cmd = PHONE_ACTIONS[p.phones]
    if cmd:
        cfg = await db(fully.config)
        if not cfg["password"] or not _phones_in_use(cfg):
            raise HTTPException(422, i18n.t("err.fully.none_ready"))
    at = int(time.time()) + DELAY
    token = f"{int(time.time())}-{secrets.token_hex(4)}"
    from .ledger import actor_of

    def run():
        rt.store.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (NOTICE_KEY, json.dumps({"action": p.action, "token": token, "at": at, "nodes": targets,
                                                  # T32: ganzes System (alle Pi oder der einzige) – nur dann die Handys
                                                  "all": set(targets) == set(all_nodes), "phones": bool(cmd),
                                                  "by": actor_of(request)})))
        for n in targets:
            _display_update(n, power={"action": p.action, "token": token, "at": at})
        if Config.node in targets:
            _display_sync()                                   # diesen Pi sofort, nicht erst in 10 s
    await db(run)
    if cmd:
        _phone_tasks[token] = asyncio.create_task(_phones_later(token, at, cmd))
    log.warning("Verwaltung: %s für %s um %s (Handys: %s)", p.action, ", ".join(targets),
                time.strftime("%H:%M:%S", time.localtime(at)), p.phones)
    return {"action": p.action, "nodes": targets, "at": at, "token": token,
            "phones_at": at - PHONES_BEFORE if cmd else None}


@admin_router.post("/system/power/cancel")
async def system_power_cancel(request: Request):
    """Herunterfahren/Neustart abbrechen, solange der Countdown noch läuft (T27)."""
    def run():
        n = _notice_raw()
        if not n or n["at"] - time.time() <= CANCEL_UNTIL:
            return None
        for node in n.get("nodes") or []:
            cur = _display_cfg(node).get("power") or {}
            if cur.get("token") == n["token"]:
                _display_update(node, power={**cur, "cancelled": True})
        rt.store.execute("DELETE FROM settings WHERE key=?", (NOTICE_KEY,))
        if Config.node in (n.get("nodes") or []):
            _display_sync()
        return n
    n = await db(run)
    if not n:
        raise HTTPException(409, i18n.t("err.power.too_late"))
    task = _phone_tasks.pop(n["token"], None)
    if task:
        task.cancel()
    from .ledger import actor_of
    log.warning("Verwaltung: %s abgebrochen von %s", n.get("action"), actor_of(request))
    return {"ok": True}
