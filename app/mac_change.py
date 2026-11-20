"""MAC-Wechsel (T4): ein Handy mit bekanntem Zertifikat meldet sich mit einer neuen WLAN-Adresse (MAC).

Die feste IP hängt im DHCP an der MAC (dhcp.py). Android wechselt die MAC z. B. mit «Zufällige MAC», wenn das WLAN
entfernt und neu verbunden wird – das Handy bekäme dann eine Adresse aus dem DHCP-Bereich. Zugeordnet wird über das
Gerätezertifikat (Geräte-ID, T1), nie über die IP:

- Beobachten (im Speicher, jede Runde des Monitors): Meldungen der Bestell-App mit Zertifikat -> MAC aus der
  ARP-Tabelle; dazu, mit welcher MAC die feste IP auf den Ping antwortet. Nur Meldungen der App schlagen eine neue MAC
  vor; der Ping zählt nur als Lebenszeichen (sonst übernähme ein fremdes Gerät mit derselben IP die Reservierung).
- Wechsel, wenn die alte MAC seit mac_change_minutes (Standard 10) nicht mehr aktiv war – gezählt ab dem ersten
  Auftauchen der neuen und frühestens ab dem Start dieses Pi (im Zweifel später wechseln, nie früher). Direkt davor
  wird die feste IP noch einmal angepingt.
- Alarm statt Wechsel, wenn die alte MAC NACH dem Auftauchen der neuen noch aktiv war (zwei Geräte mit demselben
  Zertifikat) oder die neue MAC schon einem anderen Handy gehört. Entschieden wird von Hand in der Verwaltung:
  neue übernehmen, alte behalten (die andere wird 24 Stunden lang nicht mehr gemeldet) oder Gerät sperren (T3).
- Zustand pro Gerät in der Einstellung mac_change_state (über alle Pi gleich), geschrieben nur bei einer Änderung –
  nicht jede Runde (SD-Karten). Entscheiden und aufräumen tut nur ein Pi, der das Gerät selbst sieht.

Die alte Lease gibt festpos-dhcp-reload frei, sobald die Zuteilung umgeschrieben ist (dhcp.sync); das Handy bekommt
die feste IP beim nächsten Erneuern seiner Adresse aus dem Bereich (Lease 2 Minuten, dnsmasq antwortet mit NAK).
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time

from . import device_ids, devices, dhcp, ledger_base as lb
from .core import rt

log = logging.getLogger("festpos.mac")

STATE = "mac_change_state"          # Einstellung: {Geräte-ID: Zustand}, siehe tick
MINUTES = "mac_change_minutes"      # Einstellung: Wartezeit in Minuten, 0 = nie automatisch (nur fragen)
DEFAULT_MINUTES = 10
ACTOR = "FestPOS"                   # im Protokoll, wenn der Pi selbst umschreibt
FRESH = 30                          # App-Meldungen bis so alt (s) über die ARP-Tabelle übersetzen (Takt 10 s, App 20 s)
GRACE = 30                          # so lange nach dem Auftauchen der neuen MAC zählt die alte noch nicht als gleichzeitig
NEW_ACTIVE = 90                     # umgeschrieben wird nur, wenn sich die neue MAC so kürzlich gemeldet hat (s)
STABLE = 15                         # neue MAC zählt erst, wenn sie über so viele Sekunden gesehen wurde (veraltete ARP-Einträge)
FORGET = 24 * 3600                  # Kandidaten und abgewiesene MAC vergessen, wenn so lange nicht mehr gesehen

_app: dict[str, dict[str, tuple[float, str, float]]] = {}   # Geräte-ID -> {MAC: (zuletzt, IP, zuerst)} aus Meldungen der App
_ping: dict[str, dict[str, float]] = {}               # Geräte-ID -> {MAC: zuletzt}: so hat die feste IP geantwortet
_since = time.time()                                   # ab hier beobachtet dieser Pi (davor: unbekannt)


def reset() -> None:
    """Beobachtungen vergessen (Tests; ein Neustart des Pi hat dieselbe Wirkung)."""
    global _since
    _app.clear()
    _ping.clear()
    _since = time.time()


def minutes() -> int:
    try:
        return max(0, int(rt.store.setting(MINUTES, str(DEFAULT_MINUTES)) or 0))
    except ValueError:
        return DEFAULT_MINUTES


def load_state() -> dict[str, dict]:
    try:
        v = json.loads(rt.store.setting(STATE, "{}") or "{}")
    except ValueError:
        return {}
    return {k: x for k, x in v.items() if isinstance(x, dict) and x.get("state")} if isinstance(v, dict) else {}


def _state_stmt(state: dict[str, dict]) -> tuple[str, tuple]:
    return ("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (STATE, json.dumps(state, sort_keys=True)))


def _last(did: str, m: str | None) -> float:
    """Zuletzt aktiv (App oder Ping), 0 = auf diesem Pi nicht gesehen."""
    if not m:
        return 0.0
    return max(_app.get(did, {}).get(m, (0.0, "", 0.0))[0], _ping.get(did, {}).get(m, 0.0))


def ping_mac(ip: str) -> str | None:
    """Feste IP jetzt anpingen -> MAC, die antwortet (None: keine Antwort oder Ping hier nicht möglich)."""
    from .monitor import _local, host_checks
    if not shutil.which("ping") or not (host_checks() or _local(ip)):
        return None
    try:
        rc = subprocess.run(["ping", "-c", "1", "-W", "1", "-n", "--", ip], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=3, check=False).returncode
    except (OSError, subprocess.TimeoutExpired):
        return None
    return dhcp.arp_table().get(ip) if rc == 0 else None


def observe(now: float, phones: list[dict]) -> None:
    """Meldungen der App (mit Zertifikat) und Antworten der festen IPs in MAC übersetzen (im Speicher)."""
    arp = dhcp.arp_table()
    if arp:
        ids = devices.serial_map()
        for serial, ip, at in rt.monitor.recent_ips(FRESH, now):
            did, m = ids.get(serial, (None, None))[0], arp.get(ip)
            if not did or not m:
                continue
            last, _, first = _app.setdefault(did, {}).get(m, (0.0, "", at))
            if last < at:
                _app[did][m] = (at, ip, min(first, at))
        fixed = {d["ip"]: d["cert"] for d in phones if d.get("ip")}
        for t in rt.monitor.targets:
            if t.get("kind") == "phone" and t.get("ok") and t.get("ip") in fixed and arp.get(t["ip"]):
                _ping.setdefault(fixed[t["ip"]], {})[arp[t["ip"]]] = now
    for mem in (_app, _ping):                              # Altes vergessen (begrenzt den Speicher)
        for did in list(mem):
            mem[did] = {m: v for m, v in mem[did].items() if now - (v[0] if isinstance(v, tuple) else v) <= FORGET}
            if not mem[did]:
                del mem[did]


def tick(now: float | None = None) -> None:
    """Monitor, alle 10 s (main._tick): beobachten, dann pro Handy mit Zertifikat und reservierter MAC entscheiden.

    Zustand pro Gerät: {old: reservierte MAC, new: andere MAC, new_ip, first: zuerst gesehen, state, …}
      wait   – neue MAC gesehen, Wechsel ab «due», wenn die alte bis dahin still bleibt
      ask    – Wartezeit «aus»: nur von Hand übernehmen
      alarm  – zwei Geräte gleichzeitig («both») oder neue MAC gehört einem anderen Handy («taken», other)
      ignore – von Hand entschieden: «new» ist abgewiesen, bis sie 24 Stunden nicht mehr gesehen wurde
    """
    now = time.time() if now is None else now
    items = device_ids.phones_raw(rt.store)
    phones = [d for d in items if d.get("kind") == "phone" and d.get("cert")]
    state = load_state()
    if not phones and not state:
        return
    observe(now, phones)
    before = json.dumps(state, sort_keys=True)
    lins = device_ids.lineages(rt.store)
    off = device_ids.blocked(lins)
    wait = minutes()
    owner = {dhcp.mac(d.get("mac")): d for d in items if d.get("kind") == "phone" and dhcp.mac(d.get("mac"))}
    audit: list[tuple[str, tuple]] = []
    due: list[tuple[dict, dict]] = []
    listed = set()
    for d in phones:
        did, old = d["cert"], dhcp.mac(d.get("mac"))
        if did in listed:
            continue
        listed.add(did)
        if not old or did not in lins or lins[did]["removed"] or did in off:
            state.pop(did, None)                           # ohne Reservierung lernt learn_macs; gesperrt: nichts
            continue
        if did not in _app and did not in state:
            continue                                       # dieses Gerät sieht ein anderer Pi (oder keiner)
        st = state.get(did)
        if st and st.get("old") != old:
            st = None                                      # Reservierung inzwischen anders (anderer Pi, von Hand)
        seen = _app.get(did, {})
        cands = sorted((at, m, ip) for m, (at, ip, first) in seen.items()
                       if m != old and now - at <= NEW_ACTIVE and at - first >= STABLE)
        new = cands[-1] if cands else None
        if st and st["state"] in ("ignore", "alarm") and (not new or new[1] == st["new"]):
            if st["state"] == "ignore" and did in _app and now - max(_last(did, st["new"]), st.get("since", 0)) > FORGET:
                st = None                                  # abgewiesene MAC 24 Stunden nicht mehr gesehen
            _put(state, did, st)
            continue                                       # Alarm bleibt, bis jemand entscheidet
        if st and st["state"] == "alarm":
            _put(state, did, st)
            continue                                       # noch eine dritte MAC: der Alarm zeigt die erste
        if not new:
            if st and did in _app and now - max(_last(did, st["new"]), st["first"]) > FORGET:
                st = None
            _put(state, did, st)
            continue
        _, m, ip = new
        if not st or st["new"] != m:
            st = {"old": old, "new": m, "new_ip": ip, "first": int(now), "state": "wait"}
        st = {**st, "new_ip": ip}
        old_at = _last(did, old)
        other = owner.get(m)
        if other is not None and other is not d:
            st = _alarm(st, d, "taken", audit, other=str(other.get("name") or ""))
        elif old_at > st["first"] + GRACE:
            st = _alarm(st, d, "both", audit)
        elif wait <= 0:
            st = {**st, "state": "ask"}
            st.pop("due", None)
        else:
            quiet = max(old_at, st["first"], _since)
            st = {**st, "state": "wait", "due": int(quiet + wait * 60)}
            if now >= quiet + wait * 60:
                due.append((d, st))
        _put(state, did, st)
    for did in [k for k in state if k not in listed]:      # Handy-Eintrag weg (gelöscht, ohne Zertifikat)
        del state[did]
    if json.dumps(state, sort_keys=True) != before or audit:
        rt.store.batch([_state_stmt(state), *audit])
    for d, st in due:                                      # nach dem Speichern: take liest den Zustand neu
        answer = ping_mac(str(d.get("ip") or ""))
        if answer and answer == st["old"]:                 # die feste IP antwortet noch mit der alten MAC
            _ping.setdefault(d["cert"], {})[answer] = now
            continue                                       # nächste Runde: gleichzeitig aktiv -> Alarm
        take(d["cert"], st["new"], via="auto", actor=ACTOR)


def _put(state: dict, did: str, st: dict | None) -> None:
    if st is None:
        state.pop(did, None)
    else:
        state[did] = st


def _alarm(st: dict, d: dict, reason: str, audit: list, other: str = "") -> dict:
    """Alarm (einmal ins Protokoll, nicht jede Runde)."""
    if st.get("state") != "alarm":
        audit.append(lb.audit_stmt(ACTOR, "device_mac_conflict", name=d.get("name"), old=st["old"], new=st["new"],
                                   ip=d.get("ip"), now_ip=st.get("new_ip"), reason=reason))
        log.warning("Gerät %s: MAC %s und %s mit demselben Zertifikat (%s) – kein Wechsel, Alarm", d.get("name"),
                    st["old"], st["new"], reason)
    out = {**st, "state": "alarm", "reason": reason, "since": int(time.time())}
    out.pop("due", None)
    if other:
        out["other"] = other
    return out


class Refused(Exception):
    """Wechsel nicht möglich: code = gone (kein Handy/keine neue MAC mehr) | taken (MAC gehört einem anderen Handy)."""

    def __init__(self, code: str, name: str = ""):
        super().__init__(code)
        self.code, self.name = code, name


def take(did: str, new: str, via: str, actor: str) -> bool:
    """Reservierung dieses Geräts auf die neue MAC umschreiben (Automatik und Knopf «Neue MAC übernehmen»), mit
    Protokoll, dann gleich die Zuteilungen für dnsmasq schreiben. War ein Alarm offen (von Hand entschieden), gilt die
    alte MAC danach als abgewiesen. -> False, wenn inzwischen anders entschieden (Automatik)."""
    new = dhcp.mac(new) or ""
    items = device_ids.phones_raw(rt.store)
    d = next((x for x in items if x.get("kind") == "phone" and x.get("cert") == did), None)
    state = load_state()
    st = state.get(did) or {}
    old = dhcp.mac(d.get("mac")) if d else None
    if not d or not new or old == new or (via == "auto" and (st.get("new") != new or st.get("old") != old)):
        if via == "auto":
            return False
        raise Refused("gone")
    other = next((x for x in items if x is not d and x.get("kind") == "phone" and dhcp.mac(x.get("mac")) == new), None)
    if other is not None:
        if via == "auto":
            return False
        raise Refused("taken", str(other.get("name") or ""))
    d["mac"] = new
    if st.get("state") == "alarm" and old:                  # beide aktiv: die alte ist jetzt die abgewiesene
        state[did] = {"old": new, "new": old, "state": "ignore", "since": int(time.time())}
    else:
        state.pop(did, None)
    rt.store.batch([device_ids.save_phones(items), _state_stmt(state),
                    lb.audit_stmt(actor, "device_mac_change", name=d.get("name"), old=old, new=new, ip=d.get("ip"), via=via)])
    log.info("Gerät %s: feste IP %s von MAC %s auf %s umgeschrieben (%s)", d.get("name"), d.get("ip"), old, new, via)
    dhcp.sync()
    return True


def keep(did: str, rejected: str, actor: str) -> None:
    """«Alte MAC behalten»: kein Wechsel, die andere MAC 24 Stunden lang nicht mehr melden."""
    rejected = dhcp.mac(rejected) or ""
    items = device_ids.phones_raw(rt.store)
    d = next((x for x in items if x.get("kind") == "phone" and x.get("cert") == did), None)
    old = dhcp.mac(d.get("mac")) if d else None
    if not d or not old or not rejected or rejected == old:
        raise Refused("gone")
    state = load_state()
    state[did] = {"old": old, "new": rejected, "state": "ignore", "since": int(time.time())}
    rt.store.batch([_state_stmt(state),
                    lb.audit_stmt(actor, "device_mac_keep", name=d.get("name"), kept=old, rejected=rejected, ip=d.get("ip"))])


def view(now: float | None = None) -> dict[str, dict]:
    """Für die Geräteansicht: Zustand pro Gerät, dazu vor wie vielen Sekunden alte/neue MAC aktiv waren (dieser Pi)."""
    now = time.time() if now is None else now
    out = {}
    for did, st in load_state().items():
        o, n = _last(did, st.get("old")), _last(did, st.get("new"))
        out[did] = {**st, "old_seen": int(now - o) if o else None, "new_seen": int(now - n) if n else None}
    return out


def alarms() -> dict[str, dict]:
    """Geräte mit Alarm: Geräte-ID -> Zustand (Übersicht, Statusanzeige)."""
    return {k: x for k, x in load_state().items() if x.get("state") == "alarm"}
