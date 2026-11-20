"""Verwaltung › Einstellungen › Mobilfunk-Router: Modell und Zugang eintragen, Verbindung prüfen, Zertifikat
übernehmen. Die Abfrage selbst und die Treiber stehen in uplink.py.

Gespeichert:
  uplink           – JSON {kind, name, host, port, user, iface, pin, pin_at, active} (pin = Fingerabdruck, öffentlich;
                     active False = nicht im Einsatz: nicht abgefragt, nicht auf der Statusanzeige, alles bleibt)
  uplink_password  – Passwort des Router-Benutzers; nur setzen, nie zurückgeben
Rollen: ansehen ab Anzeige, «Verbindung prüfen» ab Festleitung, ändern und Zertifikat übernehmen nur Admin.
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import i18n, ledger_base as lb, uplink
from .core import admin, current_user, db, rt

router = APIRouter(prefix="/api/admin/uplink", dependencies=[Depends(admin)])


def _raw() -> dict:
    try:
        c = json.loads(rt.store.setting("uplink") or "{}")
    except ValueError:
        c = {}
    return c if isinstance(c, dict) else {}


def config(even_inactive: bool = False) -> dict | None:
    """Für die Abfrage im Hintergrund und die Statusanzeige (mit Passwort – nie an den Browser geben).
    Nicht im Einsatz: None (ausser even_inactive – «Verbindung prüfen» geht trotzdem)."""
    if not even_inactive and _raw().get("active") is False:
        return None
    return uplink.parse_config(rt.store.setting("uplink"), rt.store.setting("uplink_password"))


def _public() -> dict:
    """Was die Verwaltung sehen darf: alles ausser dem Passwort."""
    c = _raw()
    pin = str(c.get("pin") or "")
    return {"kind": c.get("kind") or "", "name": c.get("name") or "", "host": c.get("host") or "",
            "port": c.get("port"), "user": c.get("user") or "", "iface": c.get("iface") or "",
            "pin": pin if uplink.PIN_RE.match(pin) else None, "pin_at": c.get("pin_at"),
            "password_set": bool(rt.store.setting("uplink_password")), "active": c.get("active") is not False}


def _drivers() -> list[dict]:
    return [{"key": d.key, "label": d.label, "port": d.default_port, "iface": d.default_iface}
            for d in uplink.DRIVERS.values()]


def _overview() -> dict:
    cfg = config(even_inactive=True)
    pub = _public()
    drv = uplink.DRIVERS.get(pub["kind"])
    return {"config": pub, "drivers": _drivers(),
            "setup": drv.setup(pub["host"] or "192.168.55.1") if drv else "",
            "result": rt.monitor.uplink.view(cfg, admin=True)}


def _save(c: dict, password: str | None = None) -> None:
    stmts = [("INSERT INTO settings(key,value) VALUES ('uplink', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (json.dumps(c, ensure_ascii=False),))]
    if password is not None:
        stmts.append(("INSERT INTO settings(key,value) VALUES ('uplink_password', ?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (password,)))
    rt.store.batch(stmts)


def _actor(request: Request) -> str:
    return (current_user(request) or {}).get("name") or "?"


@router.get("")
async def overview():
    return await db(_overview)


class UplinkIn(BaseModel):
    kind: str = Field(default="", max_length=20)                        # "" = kein Router
    name: str = Field(default="", max_length=30, pattern=r"^[^\x00-\x1f\x7f]*$")
    host: str = Field(default="", max_length=63)
    port: int | None = Field(default=None, ge=1, le=65535)
    user: str = Field(default="", max_length=40, pattern=uplink.USER_RE.pattern)
    password: str | None = Field(default=None, max_length=100, pattern=r"^[^\x00-\x1f\x7f]*$")   # leer = unverändert
    iface: str = Field(default="", max_length=32)


@router.put("")
async def save(u: UplinkIn, request: Request):
    """Nur Admin (nicht in auth.RULES). Anderer Router (Modell, Adresse, Port): übernommenes Zertifikat gilt nicht mehr."""
    if u.kind and u.kind not in uplink.DRIVERS:
        raise HTTPException(422, i18n.t("err.uplink.kind"))
    host = u.host.strip()
    if u.kind and not uplink.HOST_RE.match(host):
        raise HTTPException(422, i18n.t("err.uplink.host"))
    iface = u.iface.strip()
    if iface and not uplink.IFACE_RE.match(iface):
        raise HTTPException(422, i18n.t("err.uplink.iface"))
    old = await db(_raw)
    new = {"kind": u.kind, "name": " ".join(u.name.split()), "host": host, "port": u.port, "user": u.user.strip(),
           "iface": iface, "active": old.get("active") is not False}
    same = all(old.get(k) == new[k] for k in ("kind", "host", "port"))
    if same and old.get("pin"):
        new.update(pin=old["pin"], pin_at=old.get("pin_at"))
    password = u.password or None
    await db(_save, new, password)
    shown = {**new, **({"password": "***"} if password else {})}
    shown.pop("pin", None)
    shown.pop("pin_at", None)
    shown.pop("active", None)
    if not same and old.get("pin"):
        shown["certificate"] = "reset"
    await db(lb.audit, rt.store, _actor(request), "uplink", **shown)
    rt.monitor.uplink_now()
    return await db(_overview)


@router.post("/check")
async def check():
    """Jetzt abfragen (ab Festleitung). Ohne übernommenes Zertifikat: zeigt nur dessen Fingerabdruck."""
    cfg = await db(config, True)
    if not cfg:
        raise HTTPException(422, i18n.t("err.uplink.none"))
    res = await uplink.poll(cfg, slow=True)
    rt.monitor.uplink.store(cfg, res)
    return await db(_overview)


class TrustIn(BaseModel):
    pin: str = Field(pattern=uplink.PIN_RE.pattern)


@router.post("/trust")
async def trust(t: TrustIn, request: Request):
    """Zertifikat des Routers übernehmen (nur Admin) – nur wenn der Router es jetzt immer noch zeigt."""
    cfg = await db(config, True)
    if not cfg:
        raise HTTPException(422, i18n.t("err.uplink.none"))
    try:
        now_pin = await uplink.fingerprint(cfg["host"], cfg["port"])
    except uplink.UplinkError as e:
        raise HTTPException(502, i18n.t("err.uplink.unreachable", why=i18n.t(f"common.uplink.st_{e.state}"))) from None
    if now_pin != t.pin:
        raise HTTPException(409, i18n.t("err.uplink.changed"))
    c = await db(_raw)
    c.update(pin=t.pin, pin_at=time.strftime("%Y-%m-%d"))
    await db(_save, c)
    await db(lb.audit, rt.store, _actor(request), "uplink_trust", host=cfg["host"], port=cfg["port"],
             fingerprint=t.pin[:16] + "…")
    cfg = await db(config, True)
    rt.monitor.uplink.store(cfg, await uplink.poll(cfg, slow=True))
    return await db(_overview)


class ActiveIn(BaseModel):
    active: bool


@router.post("/active")
async def set_active(a: ActiveIn, request: Request):
    """Im Einsatz ein/aus (nur Admin): aus = nicht abfragen, nicht auf der Statusanzeige; Zugang und Zertifikat bleiben."""
    c = await db(_raw)
    if not c.get("kind"):
        raise HTTPException(422, i18n.t("err.uplink.none"))
    c["active"] = a.active
    await db(_save, c)
    await db(lb.audit, rt.store, _actor(request), "uplink_active", active=a.active)
    rt.monitor.uplink_now()
    return await db(_overview)
