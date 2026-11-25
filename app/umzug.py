"""Netz umziehen im Betrieb (T-0-N/FestPOS#2, Teil 2) – Verwaltung › Geräte & Netz › Pi › «Netz umziehen».

Nur Admin, nur ohne offene Bestellungen (nach dem Tagesabschluss), mit frischer Sicherung, alle Pi erreichbar.
  1. Planen: neues Netz, VIP, Pi A/B/C, DHCP – Prüfung (netz.plan_errors) und Checkliste: was geht automatisch
     (Pi, Handhelds mit Fully, Einstellungen mit Adressen), was von Hand (Drucker, Switch, Router … mit fester Adresse).
  2. Start (kritische Aktion): neue Server-CA genau für das neue Netz und ein Serverzertifikat für den BISHERIGEN
     Server-Schlüssel (zertifikate.for_move, alle Pi gleich); die Geräte-CA bleibt. Die neue CA geht über Fully auf
     die Handhelds (installUserCa). Jeder Pi legt die Dateien bereit (Auftrag netz_vorbereiten -> festpos-netz) und
     stellt zum gemeinsamen Zeitpunkt «at» um: neue festpos.conf und Zertifikate, rqlite mit peers.json, Neustart.
     Kurz vorher rechnet der druckende Pi die Adressen in der Datenbank um (gleicher Abstand zur Netzadresse).
  3. Nach dem Neustart laufen alle Pi im neuen Netz; die alten Adressen (mit den alten Zertifikaten) bleiben 24 Stunden
     dazu – Handhelds, die aus waren, kommen so noch herein.
  4. Bestätigen über die NEUE Adresse bis «deadline» (Neustart + 10 Minuten) – sonst stellen alle Pi selbst zurück
     (festpos-netz --wache), die Datenbank wird zurückgerechnet, Protokoll. «Zurück ins alte Netz» geht auch von Hand.
  5. Danach bekommen die Handhelds über Fully die neue Start-Adresse (wiederholt, bis sie umgestellt sind); der neue
     ca.key wird verschlüsselt gesichert (Herunterladen), dann gelöscht.

Sicherung aus dem alten Netz eingespielt: Die Datenbank merkt sich ihr Netz («net_current»). Weicht es vom Netz der
Pi ab (und läuft kein Umzug), rechnet der druckende Pi die Adressen in den Einstellungen und Druckern um (Protokoll).
Schlüssel einer solchen Sicherung werden nie eingespielt (maintenance.apply_keys, festpos-wiederherstellen: tls_net).

Zustand in der Datenbank (alle Pi): «net_move» – Phase: prepared, switching, done, aborted, back_requested, back.
Pro Pi: «net_move.node.<x>» – vorbereitet, aktuelles Netz, Zeit.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import cards, ledger, netz, secretbox, zertifikate
from . import ledger_base as lb
from .core import Config, admin, current_user, db, log, rt
from .i18n import t

admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])

KEY = "net_move"
NODE_KEY = "net_move.node."
LETTERS = ("a", "b", "c")
LEAD_S = 90                 # bis zum Umstellen: CA auf die Handhelds, Dateien auf alle Pi
SWITCH_BEFORE_S = 25        # so lange vor «at» rechnet der druckende Pi die Datenbank um
BOOT_S = 240                # Neustart aller Pi
CONFIRM_S = 600             # danach so lange Zeit zum Bestätigen über die neue Adresse
TRANSITION_S = 24 * 3600    # alte Adressen zusätzlich
FRESH_S = 90                # Stand eines Pi gilt so lange
PHONES_EVERY_S = 60
SETTINGS_WITH_IPS = ("monitor_devices", "monitor_hosts", "update_net", "uplink")
UPSERT = "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
_local: dict = {"prepared": None, "back": None, "confirmed": None, "reported": 0.0, "phones": 0.0}


class PlanIn(BaseModel):
    net: str = Field(max_length=20)
    vip: str = Field(max_length=15)
    a: str = Field(max_length=15)
    b: str = Field(default="", max_length=15)
    c: str = Field(default="", max_length=15)
    dhcp_from: str = Field(default="", max_length=15)
    dhcp_to: str = Field(default="", max_length=15)


# -- Zustand ------------------------------------------------------------------------------------------------------
def _get(key: str) -> dict:
    try:
        d = json.loads(rt.store.setting(key) or "{}")
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def load() -> dict:
    return _get(KEY)


def _save(d: dict, *extra) -> None:
    rt.store.batch([(UPSERT, (KEY, json.dumps(d))), *extra])


_lock = threading.Lock()   # Lesen-Ändern-Schreiben auf diesem Pi nie gleichzeitig (Takt, Verwaltung, Handhelds)


def _change(m: dict, changes: dict, *extra) -> bool:
    """Speichert nur, wenn der Umzug noch derselbe ist wie m (id und Phase) – sonst nichts, -> False. So überschreibt
    ein später fertiger Schritt nie ein «Zurück» oder Bestätigen dazwischen."""
    with _lock:
        cur = load()
        if cur.get("id") != m.get("id") or cur.get("phase") != m.get("phase"):
            return False
        _save({**cur, **changes}, *extra)
        return True


def _set_phones(mid: str, phones: dict) -> None:
    """Zustand der Handhelds ergänzen – in jeder Phase, aber nur beim selben Umzug."""
    with _lock:
        cur = load()
        if cur.get("id") == mid:
            _save({**cur, "phones": phones})


def public(d: dict) -> dict:
    """Für die Verwaltung: ohne Schlüssel und ohne die alten Werte der Datenbank."""
    return {k: v for k, v in d.items() if k not in ("ca_key", "undo")}


def pis_now() -> dict[str, str]:
    """Adressen der Pi im jetzigen Netz: {a: ip, …} (FESTPOS_PEERS in der Reihenfolge a, b, c)."""
    if len(Config.peers) <= 1:
        return {Config.node if Config.node in LETTERS else "a": Config.node_ip}
    return {LETTERS[i]: urlsplit(u).hostname or "" for i, u in enumerate(Config.peers[:3])}


def old_plan() -> dict:
    n = netz.current() or netz.parse(netz.DEFAULT)
    return {"net": str(n), "vip": netz.vip(), **pis_now()}


def _nets(m: dict) -> tuple[ipaddress.IPv4Network, ipaddress.IPv4Network]:
    return ipaddress.IPv4Network(m["old"]["net"]), ipaddress.IPv4Network(m["new"]["net"])


# -- Planen -------------------------------------------------------------------------------------------------------
def _fresh_backup() -> tuple[bool, float | None]:
    try:
        st = cards.expand_state()
    except Exception:  # noqa: BLE001 – ohne Angaben: nicht frisch
        return False, None
    return bool(st["checks"].get("backup")), st.get("backup_at")


def check(p: PlanIn) -> dict:
    """Prüfen und Checkliste -> {errors, range, auto, manual, blockers, map}."""
    old = old_plan()
    letters = list(pis_now())
    cluster = len(letters)
    ips = {k: getattr(p, k).strip() for k in ("a", "b", "c")}
    use = {k: ips[k] for k in letters}
    fill = {k: use.get(k) or ips[k] or p.vip for k in ("a", "b", "c")}           # Einzelbetrieb: B/C unbenutzt
    if cluster == 1:
        free = [x for x in netz.defaults(p.net).values() if x not in (p.vip, use.get(letters[0]))]
        fill = {k: use.get(k) or free[i] for i, k in enumerate(("a", "b", "c"))}
    errors = netz.plan_errors(p.net, p.vip, fill["a"], fill["b"], fill["c"], p.dhcp_from, p.dhcp_to,
                              cluster_size=3 if cluster == 3 else 1, dhcp=bool(p.dhcp_from or p.dhcp_to),
                              avoid=[old["net"]])
    if any(not use.get(k) for k in letters):
        errors.append("ip_bad")
    out = {"errors": errors, "old": old, "new": {"net": p.net.strip(), "vip": p.vip.strip(), **use,
                                                 "dhcp_from": p.dhcp_from.strip(), "dhcp_to": p.dhcp_to.strip()}}
    n = netz.parse(p.net)
    if n:
        out["range"] = {"first": str(n.network_address), "last": str(n.broadcast_address),
                        "hosts": n.num_addresses - 2}
    on, nn = netz.parse(old["net"]), n
    manual, missing = [], []
    from . import fully
    phones = [x["name"] for x in fully.config()["targets"]]
    if on and nn and not errors:
        for r in rt.store.query("SELECT name, host FROM printers ORDER BY id"):
            name, host = r["name"], r["host"]
            new_ip = netz.move_ip(host, on, nn)
            if new_ip:
                manual.append({"kind": "printer", "name": name, "old": host, "new": new_ip})
            elif _in(host, on):
                missing.append(host)
        for name, ip in _hosts():
            new_ip = netz.move_ip(ip, on, nn)
            if new_ip:
                manual.append({"kind": "device", "name": name, "old": ip, "new": new_ip})
            elif _in(ip, on):
                missing.append(ip)
        gw = (_get("update_net").get("gateway") or "")
        if gw and netz.move_ip(gw, on, nn):
            manual.append({"kind": "router", "name": "", "old": gw, "new": netz.move_ip(gw, on, nn)})
        for d in _get_list("monitor_devices"):
            if _in(d.get("ip"), on) and not netz.move_ip(d.get("ip"), on, nn):
                missing.append(str(d.get("ip")))
        if Config.admin_from:
            manual.append({"kind": "admin_from", "name": "", "old": Config.admin_from,
                           "new": netz.move_text(Config.admin_from, on, nn)[0]})
    if missing:
        errors.append("ip_no_room")
    out["manual"], out["missing"] = manual, sorted(set(missing))
    out["auto"] = {"pis": cluster, "phones": len(phones), "dhcp": len(_get_list("monitor_devices"))}
    blockers = []
    if _running(load()):
        blockers.append("running")
    if ledger.open_orders(rt.store):
        blockers.append("open_orders")
    fresh, at = _fresh_backup()
    if not fresh:
        blockers.append("backup")
    from . import system
    nodes = system.nodes()
    if cluster > 1 and sum(1 for x in nodes if x.get("reachable")) < cluster:
        blockers.append("pis")
    out["blockers"], out["backup_at"] = blockers, at
    return out


def _in(ip, net) -> bool:
    try:
        return ipaddress.IPv4Address(str(ip).strip()) in net
    except ValueError:
        return False


def _get_list(key: str) -> list[dict]:
    try:
        v = json.loads(rt.store.setting(key) or "[]")
    except ValueError:
        return []
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _hosts() -> list[tuple[str, str]]:
    from . import monitor as monitor_mod
    return monitor_mod.parse_hosts(rt.store.setting("monitor_hosts", monitor_mod.DEFAULT_HOSTS))


def _running(m: dict) -> bool:
    return m.get("phase") in ("prepared", "switching", "back_requested")


# -- Datenbank umrechnen (und zurück) -----------------------------------------------------------------------------
def translate(m: dict) -> dict:
    """Adressen des alten Netzes in Einstellungen und Druckern umrechnen -> {undo} zum Zurücknehmen."""
    on, nn = _nets(m)
    undo: dict = {"settings": {}, "printers": {}}
    stmts = []
    for key in SETTINGS_WITH_IPS:
        raw = rt.store.scalar("SELECT value FROM settings WHERE key=?", (key,))
        if raw is None:
            continue
        new, _ = netz.move_text(raw, on, nn)
        if new != raw:
            undo["settings"][key] = raw
            stmts.append((UPSERT, (key, new)))
    for r in rt.store.query("SELECT id, host FROM printers"):
        new_ip = netz.move_ip(r["host"], on, nn)
        if new_ip:
            undo["printers"][str(r["id"])] = r["host"]
            stmts.append(("UPDATE printers SET host=? WHERE id=?", (new_ip, r["id"])))
    if stmts:
        rt.store.batch(stmts)
    return undo


def untranslate(undo: dict) -> None:
    stmts = [(UPSERT, (k, v)) for k, v in (undo.get("settings") or {}).items()]
    stmts += [("UPDATE printers SET host=? WHERE id=?", (h, int(i))) for i, h in (undo.get("printers") or {}).items()]
    if stmts:
        rt.store.batch(stmts)


# -- Ablauf pro Pi (Monitor, alle 10 s) ---------------------------------------------------------------------------
def _node_status() -> dict[str, dict]:
    return {x: _get(NODE_KEY + x) for x in pis_now()}


def _report(m: dict, **kw) -> None:
    cur = {"id": m.get("id"), "net": str(netz.current() or ""), "at": int(time.time())}
    old = _get(NODE_KEY + Config.node)
    new = {**(old if old.get("id") == m.get("id") else {}), **cur, **kw}
    if kw or time.time() - _local["reported"] > 30 or old.get("net") != new["net"] or old.get("id") != new["id"]:
        rt.store.execute(UPSERT, (NODE_KEY + Config.node, json.dumps(new)))
        _local["reported"] = time.time()


def _job(kind: str, payload: dict, timeout: float = 90.0) -> dict:
    if not cards.available():
        return {"ok": False, "code": "helper"}
    return cards.wait(cards.submit(kind, payload), timeout) or {"ok": False, "code": "timeout"}


def _all_new(m: dict, fresh: bool = True) -> bool:
    st = _node_status()
    now = time.time()
    return all(s.get("id") == m.get("id") and s.get("net") == m["new"]["net"] and
               (not fresh or now - (s.get("at") or 0) < FRESH_S) for s in st.values())


def net_guard(m: dict) -> None:
    """Druckender Pi: Netz der Datenbank merken; stammt sie aus einem anderen Netz (Sicherung von vor einem Umzug),
    die Adressen umrechnen."""
    cur = netz.current()
    if not cur:
        return
    saved = rt.store.setting("net_current")
    if saved == str(cur):
        return
    old = netz.parse(saved or "")
    if old and not old.overlaps(cur) and not _running(m) and not (m.get("phase") == "done" and
                                                                  m.get("new", {}).get("net") == str(cur)):
        undo = translate({"old": {"net": str(old)}, "new": {"net": str(cur)}})
        if undo["settings"] or undo["printers"]:
            rt.store.execute(*lb.audit_stmt("system", "net_restore_moved", old=str(old), new=str(cur)))
            log.warning("Datenbank aus dem Netz %s: Adressen auf %s umgerechnet", old, cur)
    rt.store.execute(UPSERT, ("net_current", str(cur)))


def tick() -> None:
    m = load()
    if getattr(rt, "spooler", None) and rt.spooler.is_leader and not _running(m):
        net_guard(m)
    if not m.get("id"):
        return
    phase, now = m.get("phase"), time.time()
    leader = bool(getattr(rt, "spooler", None) and rt.spooler.is_leader)
    if phase in ("prepared", "switching", "done", "back_requested"):
        _report(m)
    if phase == "prepared" and _local["prepared"] != m["id"]:
        res = _job("netz_vorbereiten", {k: m[k] for k in ("id", "at", "deadline", "until", "old", "new", "ca_crt",
                                                          "server_crt")})
        if res.get("ok"):
            _local["prepared"] = m["id"]
            _report(m, prepared=int(now))
        else:
            _report(m, err=str(res.get("code") or "?"))
            log.warning("Netz umziehen: Pi %s nicht vorbereitet (%s)", Config.node, res.get("code"))
    if phase in ("aborted", "back_requested") and _local["back"] != m["id"]:
        if _job("netz_zurueck", {"id": m["id"]}).get("ok"):
            _local["back"] = m["id"]
    if phase == "done" and _local["confirmed"] != m["id"]:
        if _job("netz_bestaetigt", {"id": m["id"]}).get("ok"):
            _local["confirmed"] = m["id"]
    if not leader:
        return
    if phase == "prepared" and now >= m["at"] - SWITCH_BEFORE_S:
        st = _node_status()
        if all(s.get("id") == m["id"] and s.get("prepared") for s in st.values()):
            with _lock:
                saved = load()
                if saved.get("id") != m["id"] or saved.get("phase") != "prepared":
                    return                                   # inzwischen abgebrochen: nichts umrechnen
                undo = translate(m)
                _save({**saved, "phase": "switching", "undo": undo},
                      lb.audit_stmt(m.get("by"), "net_move_switch", old=m["old"]["net"], new=m["new"]["net"]))
            log.info("Netz umziehen: alle Pi vorbereitet – Umstellen um %s", time.strftime("%H:%M:%S",
                                                                                        time.localtime(m["at"])))
        else:
            missing = sorted(k for k, s in st.items() if not (s.get("id") == m["id"] and s.get("prepared")))
            if _change(m, {"phase": "aborted", "reason": "not_prepared", "missing": missing},
                       lb.audit_stmt(m.get("by"), "net_move_aborted", missing=",".join(missing))):
                log.warning("Netz umziehen abgebrochen: nicht vorbereitet: %s", ",".join(missing))
    cur = str(netz.current() or "")
    if phase in ("switching", "back_requested") and cur == m["old"]["net"] and \
            (now > m["deadline"] or phase == "back_requested") and now > m["at"] + 30:
        with _lock:
            saved = load()
            if saved.get("id") != m["id"] or saved.get("phase") != phase:
                return
            untranslate(m.get("undo") or {})                 # zurück im alten Netz: Datenbank wie vorher
            _save({**saved, "phase": "back", "back_at": int(now)},
                  lb.audit_stmt(m.get("by"), "net_move_back", reason="manual" if phase == "back_requested" else "timeout"))
        log.warning("Netz umziehen: zurück im alten Netz %s", cur)
    if phase == "done" and now < (m.get("until") or 0) and now - _local["phones"] >= PHONES_EVERY_S:
        _local["phones"] = now
        try:
            m["phones"] = asyncio.run(phones_url(m))
            _set_phones(m["id"], m["phones"])
        except Exception:  # noqa: BLE001 – nächster Versuch in einer Minute
            log.exception("Netz umziehen: Handhelds nicht umgestellt")


# -- Handhelds über Fully -----------------------------------------------------------------------------------------
async def phones_ca(m: dict, host: str) -> dict:
    """Vor dem Umstellen: neue CA auf jedes Handheld (installUserCa). -> {Name: Zustand}"""
    from . import fully
    cfg = await db(fully.config)
    out = {}

    async def one(tg):
        args = (tg["ip"], cfg["port"], tg["pin"], cfg["password"])
        try:
            res = await fully.deliver(args, host, {fully.CA_FILE: m["ca_crt"].encode()})
            if res["state"] == "ok":
                res = await fully.setup_command(*args, "installUserCa", url=f"file://{fully.FILES_DIR}/{fully.CA_FILE}")
            out[tg["name"]] = {"ca": res["state"]}
        except Exception:  # noqa: BLE001 – Handy aus: «nicht umgestellt», später wieder
            out[tg["name"]] = {"ca": "unreachable"}
    if cfg["password"]:
        await asyncio.gather(*(one(tg) for tg in cfg["targets"] if tg.get("active", True)))
    return out


async def phones_url(m: dict) -> dict:
    """Nach dem Bestätigen: neue Start-Adresse und Whitelist, App neu starten – über die neue Adresse des Handys,
    sonst die alte (die Pi haben sie in der Übergangszeit noch). -> {Name: {ca, url}}"""
    from . import fully
    cfg = await asyncio.to_thread(fully.config)
    on, nn = _nets(m)
    url = f"https://{m['new']['vip']}"
    phones = dict(m.get("phones") or {})

    async def one(tg):
        st = dict(phones.get(tg["name"]) or {})
        if st.get("url") == "ok" and st.get("ca") == "ok":    # fertig nur mit beidem
            return
        old_ip = _old_ip(tg["ip"], on, nn)
        for ip in dict.fromkeys(x for x in (tg["ip"], old_ip) if x):
            args = (ip, cfg["port"], tg["pin"], cfg["password"])
            try:
                if st.get("ca") != "ok":
                    res = await fully.deliver(args, m["new"]["vip"], {fully.CA_FILE: m["ca_crt"].encode()})
                    if res["state"] == "ok":
                        res = await fully.setup_command(*args, "installUserCa",
                                                        url=f"file://{fully.FILES_DIR}/{fully.CA_FILE}")
                    st["ca"] = res["state"]
                if st["ca"] != "ok":                      # ohne neue CA nie die neue Adresse: sie wäre nicht vertraut
                    st["url"] = st["ca"]                  # gleicher Grund wie bei der CA
                    continue
                r1 = await fully.setup_command(*args, "setStringSetting", key="startURL", value=url)
                r2 = await fully.setup_command(*args, "setStringSetting", key=fully.WHITELIST_KEY,
                                               value=fully.whitelist_value(url))
                if r1["state"] == r2["state"] == "ok":
                    await fully.setup_command(*args, "restartApp")
                    st["url"] = "ok"
                    break
                st["url"] = r1["state"] if r1["state"] != "ok" else r2["state"]
            except Exception:  # noqa: BLE001 – Handy aus oder noch mit alter Adresse
                st["url"] = "unreachable"
        phones[tg["name"]] = st
    if cfg["password"]:
        await asyncio.gather(*(one(tg) for tg in cfg["targets"] if tg.get("active", True)))
    return phones


def _old_ip(ip: str, on, nn) -> str | None:
    try:
        a = ipaddress.IPv4Address(ip)
    except ValueError:
        return None
    if a not in nn:
        return None
    off = int(a) - int(nn.network_address)
    return str(on.network_address + off) if 0 < off < on.num_addresses - 1 else None


# -- Verwaltung ---------------------------------------------------------------------------------------------------
def view() -> dict:
    m = load()
    now = time.time()
    st = _node_status() if m.get("id") else {}
    return {"move": public(m), "nodes": st, "old": old_plan(), "now": now,
            "defaults": netz.defaults("10.20.0.0/22" if old_plan()["net"] != "10.20.0.0/22" else "192.168.55.0/24"),
            "all_new": bool(m.get("id")) and _all_new(m) if m.get("new") else False}


@admin_router.get("/umzug")
async def get_view():
    return await db(view)


@admin_router.post("/umzug/pruefen")
async def plan_check(p: PlanIn):
    return await db(check, p)


@admin_router.post("/umzug/start")
async def start(p: PlanIn, request: Request):
    """Kritisch: neue CA, Handhelds, alle Pi vorbereiten; umgestellt wird in LEAD_S Sekunden."""
    who = (current_user(request) or {}).get("name") or "?"
    res = await db(check, p)
    if res["errors"]:
        raise HTTPException(422, t("err.umzug.plan", errors=", ".join(res["errors"])))
    if res["blockers"]:
        raise HTTPException(409, t("err.umzug.blocked", reasons=", ".join(res["blockers"])))
    from pathlib import Path
    try:
        server_crt = await db(Path(Config.server_cert or "/etc/festpos/tls/server.crt").read_bytes)
    except OSError:
        raise HTTPException(503, t("err.umzug.no_cert")) from None
    new = res["new"]
    ips = [new["vip"], *[new[k] for k in pis_now()]]
    certs = await db(zertifikate.for_move, new["net"], ips, server_crt)
    now = int(time.time())
    m = {"id": secrets.token_hex(8), "phase": "prepared", "old": res["old"], "new": new, "at": now + LEAD_S,
         "deadline": now + LEAD_S + BOOT_S + CONFIRM_S, "until": now + LEAD_S + BOOT_S + CONFIRM_S + TRANSITION_S,
         "ca_crt": certs["ca.crt"].decode(), "server_crt": certs["server.crt"].decode(),
         "ca_key": secretbox.seal("net_move_ca_key", certs["ca.key"].decode()), "by": who, "started": now,
         "backup_at": res.get("backup_at"), "manual": res["manual"], "fp": zertifikate.fingerprint(certs["ca.crt"])}
    await db(_save, m, lb.audit_stmt(who, "net_move_start", old=res["old"]["net"], new=new["net"]))
    from . import fully
    host = fully._pi_host(request)

    async def ca_to_phones():
        try:
            phones = await phones_ca(m, host)
            await db(_set_phones, m["id"], phones)
        except Exception:  # noqa: BLE001 – nach dem Umstellen nochmals
            log.exception("Netz umziehen: CA auf die Handhelds gescheitert")
    asyncio.create_task(ca_to_phones())
    return public(m)


def _confirm(request_host: str, who: str) -> dict:
    with _lock:
        return _confirm_locked(request_host, who)


def _confirm_locked(request_host: str, who: str) -> dict:
    m = load()
    if m.get("phase") != "switching":
        raise HTTPException(409, t("err.umzug.not_running"))
    if not _in(request_host, ipaddress.IPv4Network(m["new"]["net"])):
        raise HTTPException(409, t("err.umzug.use_new", url=f"https://{m['new']['vip']}:8443"))
    if not _all_new(m):
        raise HTTPException(409, t("err.umzug.pis_missing"))
    now = int(time.time())
    m = {**m, "phase": "done", "confirmed_at": now, "until": now + TRANSITION_S, "by_confirm": who}
    _save(m, lb.audit_stmt(who, "net_move_done", old=m["old"]["net"], new=m["new"]["net"]))
    return public(m)


@admin_router.post("/umzug/bestaetigen")
async def confirm(request: Request):
    """Kritisch, nur über die neue Adresse: Umzug gilt (kein Rückfall mehr)."""
    host = (request.url.hostname or "").strip("[]")
    return await db(_confirm, host, (current_user(request) or {}).get("name") or "?")


@admin_router.post("/umzug/zurueck")
async def back(request: Request):
    """Kritisch: zurück ins alte Netz (vor dem Bestätigen)."""
    who = (current_user(request) or {}).get("name") or "?"

    def run():
        with _lock:
            return run_locked()

    def run_locked():
        m = load()
        if m.get("phase") not in ("prepared", "switching"):
            raise HTTPException(409, t("err.umzug.not_running"))
        phase = "aborted" if m["phase"] == "prepared" else "back_requested"
        _save({**m, "phase": phase, "reason": "manual"}, lb.audit_stmt(who, "net_move_back_requested"))
        return public(load())
    return await db(run)


class CaKeyIn(BaseModel):
    password: str = Field(min_length=12, max_length=200)


@admin_router.post("/umzug/ca-key")
async def ca_key(p: CaKeyIn, request: Request):
    """Neuer ca.key (verschlüsselt mit dem Passwort) zum Aufbewahren – danach wird er auf dem Pi gelöscht."""
    who = (current_user(request) or {}).get("name") or "?"

    def run():
        with _lock:
            return run_locked()

    def run_locked():
        m = load()
        pem = secretbox.unseal("net_move_ca_key", m.get("ca_key") or "")
        if m.get("phase") != "done" or not pem:
            raise HTTPException(409, t("err.umzug.no_ca_key"))
        data = zertifikate.encrypt_key_pem(pem.encode(), p.password)
        m.pop("ca_key", None)
        _save({**m, "ca_key_saved": int(time.time())}, lb.audit_stmt(who, "net_move_ca_key"))
        return data
    data = await db(run)
    return Response(data, media_type="application/x-pem-file",
                    headers={"Content-Disposition": 'attachment; filename="ca.key"', "Cache-Control": "no-store"})


@admin_router.get("/umzug/ca.crt")
async def ca_crt():
    m = await db(load)
    if not m.get("ca_crt"):
        raise HTTPException(404)
    return Response(m["ca_crt"], media_type="application/x-x509-ca-cert",
                    headers={"Content-Disposition": 'attachment; filename="festpos-ca-neu.crt"'})

