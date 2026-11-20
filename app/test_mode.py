"""Testmodus (Üben) der Bestell-App: wer gerade übt, Beenden von aussen, Protokoll (T7).

Ob ein Handy im Testmodus ist, meldet die App mit jeder Anfrage (Kopfzeile X-Festpos-Test, monitor.seen); seit
wann, merkt sich der Monitor im Speicher. Beenden aus der Statusanzeige oder der Verwaltung legt pro Gerät einen
Zeitpunkt in die Einstellung `test_exit` (ein Schreibzugriff pro Klick, «*» = alle). Die App fragt alle 10 Sekunden
/api/phone/status; liegt ein Zeitpunkt nach dem Beginn ihres Testmodus, bekommt sie `test_exit` und beendet ihn.

Schlüssel eines Geräts: `c:<Geräte-ID>` mit Zertifikat (T1), sonst der Schlüssel des Monitors (`a:<IP>|<Name>`) bzw.
`ip:<IP>` für ein eingetragenes Handy ohne Zertifikat. Die App prüft alle, die auf sie passen.
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import devices, ledger
from . import ledger_base as lb
from .core import LOCAL_HOSTS, Config, admin, db, rt
from .i18n import t
from .monitor import ONLINE_SECONDS, Monitor

router = APIRouter()                                     # Handy und Statusanzeige am Pi
admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])

SETTING = "test_exit"
KEEP_SECONDS = 12 * 3600                                 # ältere Zeitpunkte braucht niemand mehr


def requests_() -> dict[str, float]:
    try:
        v = json.loads(rt.store.setting(SETTING) or "{}")
    except ValueError:
        return {}
    return {str(k): float(x) for k, x in v.items() if isinstance(x, (int, float))} if isinstance(v, dict) else {}


def due(reqs: dict[str, float], keys: list[str], since: float | None) -> bool:
    """Gilt für dieses Gerät ein «beenden», das nach dem Beginn seines Testmodus kam?"""
    at = max([reqs.get(k, 0.0) for k in [*keys, "*"]])
    return since is not None and at > since


def phone_keys(ip: str | None, name: str, serial: str | None) -> list[str]:
    keys = [Monitor.key_for(ip, name, serial)]
    did = devices.serial_map().get(serial or "", (None, None))[0] if serial else None
    if did:
        keys.append(f"c:{did}")
    if ip:
        keys.append(f"ip:{ip}")
    return keys


def entry_for(ip: str | None, name: str, serial: str | None) -> dict | None:
    return rt.monitor.devices.get(Monitor.key_for(ip, name, serial)) if rt.monitor else None


def active() -> list[dict]:
    """Handys, die gerade im Testmodus sind (online, dieser Pi): Schlüssel, Gerätename, Name aus der App, IP,
    seit wann und ob das Beenden schon unterwegs ist."""
    ids = devices.serial_map()
    reqs: dict[str, float] | None = None
    out: dict[str, dict] = {}
    for d in rt.monitor.device_list() if rt.monitor else []:
        if not d.get("test") or not d["online"]:
            continue
        did, cert_name = ids.get(d.get("serial") or "", (None, None))
        key = f"c:{did}" if did else d["key"]
        if key in out and out[key]["seconds"] <= d["seconds"]:
            continue
        if reqs is None:
            reqs = requests_()
        keys = [d["key"], key] + ([f"ip:{d['ip']}"] if d.get("ip") else [])
        out[key] = {"key": key, "name": cert_name or d.get("cert_name") or d["name"] or d.get("ip") or "?",
                    "app_name": d["name"] or None, "ip": d.get("ip"), "seconds": d["seconds"],
                    "since": d.get("test_since"), "ending": due(reqs, keys, d.get("test_since"))}
    for x in out.values():
        x.pop("seconds")
    return sorted(out.values(), key=lambda x: (x["name"].casefold(), x["key"]))


def exit_stmts(keys: list[str], actor: str, names: list[str]) -> list:
    now = time.time()
    reqs = {k: v for k, v in requests_().items() if now - v < KEEP_SECONDS}
    for k in keys:
        reqs[k] = now
    return [("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
             (SETTING, json.dumps(reqs))),
            lb.audit_stmt(actor, "test_exit", devices=", ".join(names) or "*")]


class ExitIn(BaseModel):
    key: str | None = Field(default=None, min_length=1, max_length=200)   # ein Gerät (Schlüssel siehe oben)
    all: bool = False                                                     # alle Handys
    name: str | None = Field(default=None, max_length=80)                 # fürs Protokoll


async def _exit(b: ExitIn, actor: str) -> dict:
    if not b.all and not b.key:
        raise HTTPException(422, t("err.test.which"))
    if b.all:
        names = [x["name"] for x in await db(active)]
        await db(rt.store.batch, exit_stmts(["*"], actor, names or [t("status.detail.test_all")]))
    else:
        await db(rt.store.batch, exit_stmts([b.key], actor, [b.name or b.key]))
    return {"ok": True, "seconds": 10}


@admin_router.post("/devices/test-exit")
async def admin_test_exit(b: ExitIn, request: Request):
    """Testmodus eines Handys (oder aller) beenden – Verwaltung und Statusanzeige mit Anmeldung, ab Festleitung."""
    return await _exit(b, ledger.actor_of(request))


@router.post("/api/status/test-exit")
async def status_test_exit(b: ExitIn, request: Request):
    """Dasselbe am Bildschirm des Pi ohne Anmeldung (beenden ist die sichere Richtung, T7 F2) – nur vom Pi selbst."""
    if not (request.client and request.client.host in LOCAL_HOSTS) or request.headers.get("authorization"):
        raise HTTPException(403, t("err.status.local_only"))
    return await _exit(b, f"status:{Config.node}")


class PhoneTestIn(BaseModel):
    on: bool
    how: str = Field(default="app", pattern=r"^(app|tour|start|remote|admin)$")   # wie: am Handy, Einführung, …


@router.post("/api/phone/test", dependencies=[Depends(devices.phone_only)])
async def phone_test(b: PhoneTestIn, request: Request):
    """Das Handy meldet Beginn und Ende seines Testmodus fürs Protokoll (nur bei einem Wechsel)."""
    from .ledger import _app_name
    info = devices.client_cert(request)
    who = f"device:{lb.device_label(info['name'] if info else None, _app_name(request)) or '?'}"
    await db(lb.audit, rt.store, who, "test_on" if b.on else "test_off", how=b.how)
    return {"ok": True}


def phone_status_extra(request: Request) -> dict:
    """Für /api/phone/status: `test_exit`, wenn dieses Handy im Testmodus ist und beendet werden soll."""
    from urllib.parse import unquote
    info = devices.client_cert(request)
    ip = request.client.host if request.client else None
    serial = info["serial"] if info else None
    name = unquote(request.headers.get("X-Festpos-Device") or "").strip()
    name = (name if (name or serial) else (ip or "")).strip()[:40]        # wie device_heartbeat und monitor.seen
    e = entry_for(ip, name, serial)
    if not e or not e.get("test") or time.time() - e["last_seen"] > ONLINE_SECONDS:
        return {}
    return {"test_exit": True} if due(requests_(), phone_keys(ip, name, serial), e.get("test_since")) else {}
