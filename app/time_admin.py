"""Uhrzeit in der Verwaltung und zwischen den Pi (T2): Zeitstand aller Pi, Bestätigung, übernehmen/manuell setzen,
Abweichung der Handys, Zeitserver per USB (deploy/festpos-handy-usb). Regeln in timesync.py.

Nur Festleitung und Admin sehen und stellen die Uhrzeit (auth.RULES); die Rolle Anzeige bekommt 403.
"""
from __future__ import annotations

import json
import os
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import devices, device_ids, i18n, ledger_base as lb, timesync
from .core import Config, Throttle, admin, current_user, db, log, require_cluster_secret, rt

router = APIRouter()
admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])

USB_DIR = os.environ.get("FESTPOS_HANDY_USB_DIR", "/run/festpos/handy-usb")   # Ergebnisse von festpos-handy-usb
USB_KEY = "phone_ntp"                    # Einstellung: Zeitserver per USB gesetzt, pro Handy (alle Pi sehen es)
PHONE_CLOCK_TTL = 6 * 3600               # Abweichung einer Handy-Uhr so lange anzeigen
TICK_EVERY = 60                          # Zeit-Aufgaben im Monitor-Takt höchstens so oft

settime_throttle = Throttle()
peer_throttle = Throttle()


class TimeIn(BaseModel):
    epoch_ms: int
    source: str = Field("", max_length=12)


class AdminTimeIn(BaseModel):
    """Uhrzeit setzen: epoch_ms (Uhr dieses Browsers) oder local («2026-10-28T15:49:12», Zeitzone des Pi)."""
    epoch_ms: int | None = None
    local: str | None = Field(None, max_length=25)


def _peer_ips() -> list[str]:
    from urllib.parse import urlsplit
    return [urlsplit(b).hostname or "" for b in Config.peers]


def _secret_ok(request: Request, throttle: Throttle) -> None:
    require_cluster_secret(request, throttle)


def _actor(request: Request) -> str:
    u = current_user(request)
    return (u or {}).get("name") or "?"


def local_state() -> dict:
    return {**timesync.state(Config.node, _peer_ips()), "ip": Config.node_ip}


def all_states() -> list[dict]:
    """Zeitstand dieses Pi (offset 0) und der anderen (über /api/internal/time). Nicht erreichbare Pi fehlen."""
    me = {**local_state(), "offset": 0.0, "here": True}
    others = timesync.peer_states(Config.peers, Config.cluster_secret, Config.cafile, Config.node_ip)
    return [me] + [s for s in others.values() if s]


# -- Handys: Abweichung der Uhr (nur Anzeige) ------------------------------------------------------------------
_phone_clock: dict[str, tuple[float, float, bool]] = {}    # Geräte-ID bzw. Name -> (Abweichung s, Zeitpunkt, Android?)
_phone_lock = threading.Lock()


def _phone_key(request: Request) -> str:
    info = devices.client_cert(request)
    if info:
        did = device_ids.device_id_of(rt.store, info["serial"])
        return did or "cert:" + info["serial"]
    return "dev:" + (request.headers.get("X-Festpos-Device") or (request.client.host if request.client else "?"))


def phone_clocks() -> dict[str, dict]:
    now = time.time()
    with _phone_lock:
        return {k: {"offset": round(o), "at": round(at), "android": android} for k, (o, at, android) in _phone_clock.items()
                if now - at < PHONE_CLOCK_TTL}


@router.post("/api/time/sync")
async def time_sync(t: TimeIn, request: Request, _: None = Depends(devices.phone_only)):
    """Das Handy meldet seine Uhr. Seit T2 stellt das den Pi NIE mehr (die Handys holen ihre Zeit vom Pi); die
    Abweichung erscheint nur unter Geräte – so sieht man, ob der Zeitserver auf dem Handy wirkt."""
    diff = t.epoch_ms / 1000 - time.time()
    key = await db(_phone_key, request)
    with _phone_lock:
        # Android? (T31: Zeitserver per USB gibt es nur dort) – aus der Kennung des Browsers bzw. von Fully
        _phone_clock[key] = (diff, time.time(), "android" in (request.headers.get("user-agent") or "").lower())
        if len(_phone_clock) > 300:                         # Speicher begrenzen
            _phone_clock.pop(next(iter(_phone_clock)))
    return {"changed": False, "diff_s": round(diff, 1)}


# -- zwischen den Pi ----------------------------------------------------------------------------------------
@router.get("/api/internal/time")
async def internal_time(request: Request):
    """Zeitstand dieses Pi für die anderen (Verwaltung, Abgleich beim Start). Mit CLUSTER_SECRET."""
    _secret_ok(request, peer_throttle)
    return await db(local_state)


@router.post("/api/internal/settime")
async def internal_settime(t: TimeIn, request: Request):
    """Von einem anderen Pi (broadcast). Geschützt mit CLUSTER_SECRET und gegen Raten gesperrt (REV-013)."""
    _secret_ok(request, settime_throttle)
    ok, msg = await db(timesync.set_local, t.epoch_ms, 10.0, t.source)
    if not ok:
        raise HTTPException(500, msg or i18n.t("err.time.set_failed"))
    return {"ok": True}


# -- Verwaltung ---------------------------------------------------------------------------------------------
def usb_results() -> dict[str, dict]:
    """Zeitserver per USB, pro Handy (Schlüssel: Seriennummer des Handys)."""
    try:
        d = json.loads(rt.store.setting(USB_KEY) or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def overview() -> dict:
    states = all_states()
    conf = timesync.confirmation(rt.store)
    floor = timesync.time_floor(rt.store)
    return {"this_node": Config.node, "server_time": time.time(), "pis": states, "confirmed": conf,
            "reasons": timesync.prompt_reasons(states, conf), "due": timesync.due(conf),
            "restart_after": timesync.CONFIRM_RESTART_S, "threshold": timesync.THRESHOLD_S, "floor": floor,
            "phones": phone_clocks(), "usb": usb_results()}


@admin_router.get("/time")
async def time_overview():
    """Zeitstand aller Pi, Bestätigung und warum nachgefragt wird (Karte «Uhrzeit», Hinweis nach der Anmeldung)."""
    return await db(overview)


def _set_all(epoch_ms: int, received: float, how: str, actor: str) -> dict:
    before = time.time()
    res = timesync.broadcast(Config.peers, epoch_ms, Config.cluster_secret, Config.cafile, Config.node_ip,
                             received, source=how)
    ok = res.get("lokal") == "ok"
    stmts = [lb.audit_stmt(actor, "time_set", how=how, diff_s=round(epoch_ms / 1000 - before),
                           nodes={k: v for k, v in res.items()})]
    if ok or how == "ok":                 # «Zeit stimmt» bestätigt auch, wenn der Zeit-Dienst nicht antwortet
        stmts.append(timesync.confirm_stmt(how, actor, Config.node))
    rt.store.batch(stmts)
    log.warning("Uhrzeit gesetzt (%s, %s): Abweichung %.0f s, %s", how, actor, epoch_ms / 1000 - before, res)
    return {"nodes": res, "server_time": time.time()}


@admin_router.post("/time")
async def admin_set_time(t: AdminTimeIn, request: Request):
    """Uhrzeit auf allen Pi setzen: von diesem Gerät übernehmen (epoch_ms) oder von Hand (local) – auch bei kleiner
    Abweichung. Zählt als Bestätigung."""
    received = time.monotonic()
    if t.local:
        try:
            epoch = timesync.local_to_epoch(t.local)
        except ValueError:
            raise HTTPException(400, i18n.t("err.time.invalid")) from None
        epoch_ms, how = int(epoch * 1000), "manual"
    elif t.epoch_ms is not None:
        epoch_ms, how = t.epoch_ms, "take"
    else:
        raise HTTPException(400, i18n.t("err.time.invalid"))
    if not timesync.FLOOR_EPOCH - 86400 <= epoch_ms / 1000 <= 4102444800:
        raise HTTPException(400, i18n.t("err.time.invalid"))
    return await db(_set_all, epoch_ms, received, how, _actor(request))


@admin_router.post("/time/confirm")
async def admin_confirm_time(request: Request):
    """«Zeit stimmt»: Alle Pi auf die Uhr dieses Pi setzen (sie liegen ohnehin nah beieinander) – so gilt die Zeit
    überall als gestellt, auch auf einem Pi mit leerer RTC-Batterie – und als bestätigt merken."""
    return await db(_set_all, int(time.time() * 1000), time.monotonic(), "ok", _actor(request))


# -- im Monitor-Takt (jeder Pi) -------------------------------------------------------------------------------
_last_tick = 0.0
_rtc_logged = False


def _sync_from_peer(me: dict) -> None:
    """Uhr dieses Pi unsicher (nicht aus dem RTC gestartet, nicht gestellt): von einem Pi mit sicherer Uhr holen.
    So zieht ein Pi mit leerer Batterie die anderen nicht über chrony auf seine falsche Zeit."""
    for base, st in timesync.peer_states(Config.peers, Config.cluster_secret, Config.cafile, Config.node_ip).items():
        if not st or not st.get("sure"):
            continue
        epoch_ms = int((time.time() + st["offset"]) * 1000)
        ok, msg = timesync.set_local(epoch_ms, source="peer")
        lb.audit(rt.store, "system", "time_set", how="peer", node=Config.node, source=st.get("node") or base,
                 diff_s=round(st["offset"]), result="ok" if ok else msg)
        log.warning("Uhr von Pi %s übernommen (%+.0f s): %s", st.get("node") or base, st["offset"], msg or "ok")
        return


def _confirm_by_internet(me: dict) -> None:
    if me.get("ntp") != "internet":
        return
    conf = timesync.confirmation(rt.store)
    if conf and time.time() - conf["at"] < timesync.NTP_CONFIRM_EVERY_S:
        return
    rt.store.batch([timesync.confirm_stmt("ntp", "system", Config.node),
                    lb.audit_stmt("system", "time_confirmed", how="ntp", node=Config.node)])


def _log_rtc_once(me: dict) -> None:
    """Einmal pro Start und Pi: Stand der RTC-Batterie ins Protokoll (zeigt nach dem Einlagern, ob sie hält)."""
    global _rtc_logged
    rtc, boot = me.get("rtc"), me.get("boot")
    _rtc_logged = True
    if not rtc or not boot or rtc.get("battery_uv") is None:
        return
    tag = f'"boot": {int(boot) // 60}'
    if rt.store.scalar("SELECT 1 FROM audit_log WHERE action='rtc_start' AND details LIKE ?",
                       (f'%"node": "{Config.node}"%{tag}%',)):
        return
    volt = lambda uv: round(uv / 1e6, 2) if uv else None   # noqa: E731
    lb.audit(rt.store, "system", "rtc_start", node=Config.node, boot=int(boot) // 60, rtc_boot=rtc["boot"],
             battery_v=volt(rtc.get("battery_uv")), charging_v=volt(rtc.get("charging_uv")), rtc_drift=rtc.get("drift"))


_usb_lock = threading.Lock()


def _ingest_usb() -> None:
    """Ergebnisse von festpos-handy-usb (dieser Pi) in die Datenbank, damit alle Pi sie sehen. Nacheinander (Sperre):
    Monitor-Takt und Verwaltung rufen das gleichzeitig auf – sonst übernähmen beide dasselbe Handy (zwei Einträge im
    Protokoll)."""
    with _usb_lock:
        _ingest_usb_locked()


def _ingest_usb_locked() -> None:
    try:
        names = [n for n in os.listdir(USB_DIR) if n.endswith(".json")]
    except OSError:
        return
    known = usb_results()
    changed = []
    for n in sorted(names)[-50:]:
        try:
            with open(os.path.join(USB_DIR, n), encoding="utf-8") as f:
                r = json.loads(f.read(4000))
            serial = str(r["serial"])[:40]
            entry = {k: r.get(k) for k in ("at", "ok", "ip", "model", "android", "error", "steps")}
        except (OSError, ValueError, KeyError, TypeError):
            continue
        entry["node"] = Config.node
        # nur Neueres übernehmen: hing das Handy zuerst an diesem Pi und danach an einem anderen, darf das alte
        # Ergebnis hier (liegt bis zum Neustart in /run) das neuere des anderen Pi nicht wieder überschreiben
        if (known.get(serial, {}).get("at") or 0) >= (entry.get("at") or 0):
            continue
        known[serial] = entry
        changed.append((serial, entry))
    if not changed:
        return
    stmts = [("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (USB_KEY, json.dumps(dict(list(known.items())[-200:]))))]
    for serial, e in changed:
        stmts.append(lb.audit_stmt("system", "phone_ntp", serial=serial, ip=e.get("ip"), model=e.get("model"),
                                   node=Config.node, result="ok" if e.get("ok") else e.get("error")))
    rt.store.batch(stmts)


def tick() -> None:
    """Aus dem Monitor-Takt (main._tick, im Thread). Liest nur, ausser beim Stellen und Bestätigen."""
    global _last_tick
    if time.monotonic() - _last_tick < TICK_EVERY:
        return
    _last_tick = time.monotonic()
    try:
        me = local_state()
        if not _rtc_logged:
            _log_rtc_once(me)
        if not me["sure"] and len(Config.peers) > 1:
            _sync_from_peer(me)
        _confirm_by_internet(me)
        _ingest_usb()
    except Exception as e:  # noqa: BLE001 – nie den Monitor stören
        log.warning("Zeit-Aufgaben: %s", e)


@admin_router.get("/time/usb")
async def time_usb(since: float = Query(0, ge=0)):
    """Zeitserver per USB: Ergebnisse ab «since» (Einrichtung wartet darauf), auch die dieses Pi, die noch nicht in
    der Datenbank stehen."""
    await db(_ingest_usb)
    return {"results": {k: v for k, v in (await db(usb_results)).items() if (v.get("at") or 0) >= since},
            "server_time": time.time()}
