"""Daten verschlüsselt, Schlüssel im Chip (T-0-N/FestPOS#3) – Verwaltung › Geräte & Netz › Pi.

Ausführen darf das nur root (deploy/festpos-krypt, Aufträge über festpos-wiederherstellen). Die App zeigt pro Pi den
Stand (info.krypt, aus /run/festpos-krypt/stand.json) und gibt die Aufträge weiter – an den eigenen Helfer oder an den
Pi, um den es geht (/api/internal/krypt, mit CLUSTER_SECRET, nur an ein geprüftes Zertifikat):
  - Umstellen: Wiederherstellungs-Schlüssel erzeugen und anzeigen; erst nach «ausgedruckt und aufbewahrt» wird
    umgestellt (beim nächsten Start, der Pi startet zweimal neu).
  - Neuer Pi: hat sich beim ersten Start selbst verschlüsselt, vorläufig; sein Wiederherstellungs-Schlüssel liegt bis
    zur Bestätigung im Container (festpos-krypt entpackt ihn nach OFFEN). Bestätigen -> Schlüssel in den Chip.
"""
from __future__ import annotations

import ipaddress
import json
import os
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import cards, geheim, ledger
from . import ledger_base as lb
from .core import Config, Throttle, admin, db, require_cluster_secret, rt
from .i18n import t

admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])
router = APIRouter()
peer_throttle = Throttle()

STAND = os.environ.get("FESTPOS_KRYPT_STAND", "/run/festpos-krypt/stand.json")
OFFEN = os.environ.get("FESTPOS_KRYPT_OFFEN", "/run/festpos-geheim/wiederherstellung")
STATES = ("aus", "vorlaeufig", "aktiv", "kein_chip", "gesperrt", "umstellen")
LETTERS = ("a", "b", "c")


def local_status() -> dict | None:
    """Stand dieses Pi: {state, chip, tool, offen, bestaetigt, error, size, used, free} – None ohne festpos-krypt."""
    try:
        with open(STAND, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("state") not in STATES:
        return None
    out = {"state": d["state"], "chip": d.get("chip") if d.get("chip") in ("set", "empty", "none") else None,
           "tool": d.get("tool") is True, "offen": d.get("offen") is True,
           "bestaetigt": str(d.get("bestaetigt") or "")[:10] or None, "error": str(d.get("error") or "")[:40] or None}
    for k in ("size", "used", "free"):
        if isinstance(d.get(k), int):
            out[k] = d[k]
    return out


def durable(d: dict | None) -> dict | None:
    """Für die Datenbank (node_info): ohne Belegung, die sich ständig ändert."""
    return {k: v for k, v in d.items() if k not in ("used", "free")} if isinstance(d, dict) else d


def pending_key() -> str | None:
    try:
        with open(OFFEN, encoding="utf-8") as f:
            return geheim.recovery_norm(f.read())
    except OSError:
        return None


# -- Aufträge an diesen Pi --------------------------------------------------------------------------------------------
def local_action(action: str, recovery: str = "") -> dict:
    """umstellen | bestaetigen | offen auf DIESEM Pi."""
    if action == "offen":
        key = pending_key()
        if not key:
            raise HTTPException(409, t("err.krypt.no_pending"))
        return {"recovery": key}
    if action == "umstellen":
        rec = geheim.recovery_norm(recovery)
        if not rec:
            raise HTTPException(422, t("err.krypt.recovery"))
        st = local_status() or {}
        if st.get("state") not in ("aus", None) or not st.get("tool"):
            raise HTTPException(409, t("err.krypt.not_possible"))
        job = cards.submit("krypt_umstellen", {"recovery": rec})
    elif action == "bestaetigen":
        if (local_status() or {}).get("state") != "vorlaeufig":
            raise HTTPException(409, t("err.krypt.not_possible"))
        job = cards.submit("krypt_bestaetigen", {})
    else:
        raise HTTPException(422)
    res = cards.wait(job, 120.0)
    if not res or not res.get("ok"):
        raise HTTPException(502, t("err.krypt.failed", code=(res or {}).get("code") or "timeout"))
    return {"ok": True, "restart": bool(res.get("restart"))}


# -- an einen anderen Pi ---------------------------------------------------------------------------------------------
def _peer_base(node: str) -> str:
    """Adresse des Pi mit diesem Buchstaben – nur einer aus FESTPOS_PEERS (keine beliebigen Ziele)."""
    p = rt.monitor.peer(node, max_age=120) if getattr(rt, "monitor", None) else None
    ip = ((p or {}).get("data") or {}).get("info", {}).get("ip") if p else None
    if not ip:
        try:
            ip = json.loads(rt.store.setting(f"node_info.{node}") or "{}").get("ip")
        except ValueError:
            ip = None
    try:
        ipaddress.ip_address(str(ip))
    except ValueError:
        raise HTTPException(503, t("err.krypt.unreachable", node=node.upper())) from None
    for base in Config.peers:
        if urlsplit(base).hostname == ip:
            return base
    raise HTTPException(503, t("err.krypt.unreachable", node=node.upper()))


def peer_action(node: str, action: str, recovery: str = "") -> dict:
    base = _peer_base(node)
    if not Config.cafile or not Config.cluster_secret:
        raise HTTPException(503, t("err.krypt.unreachable", node=node.upper()))
    ctx = ssl.create_default_context(cafile=Config.cafile)
    req = urllib.request.Request(f"{base.rstrip('/')}/api/internal/krypt", method="POST",
                                 data=json.dumps({"action": action, "recovery": recovery}).encode(),
                                 headers={"Content-Type": "application/json",
                                          "X-Festpos-Secret": Config.cluster_secret.encode().decode("latin-1")})
    try:
        with urllib.request.urlopen(req, timeout=150, context=ctx) as r:
            return json.loads(r.read(100_000))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read(10_000)).get("detail")
        except (ValueError, AttributeError):
            detail = None
        raise HTTPException(e.code if 400 <= e.code < 600 else 502,
                            detail or t("err.krypt.failed", code=str(e.code))) from None
    except Exception:  # noqa: BLE001 – Pi aus oder alt
        raise HTTPException(503, t("err.krypt.unreachable", node=node.upper())) from None


def action(node: str, what: str, recovery: str = "") -> dict:
    node = node.lower()
    if node not in LETTERS:
        raise HTTPException(422)
    if node == Config.node:
        return local_action(what, recovery)
    return peer_action(node, what, recovery)


# -- Verwaltung -------------------------------------------------------------------------------------------------------
class UmstellenIn(BaseModel):
    recovery: str = Field(min_length=32, max_length=60)
    printed: bool


class BestaetigenIn(BaseModel):
    printed: bool


@admin_router.post("/krypt/schluessel")
async def new_key():
    """Neuer Wiederherstellungs-Schlüssel zum Anzeigen und Drucken (gilt erst mit «Jetzt umstellen»)."""
    return {"recovery": geheim.recovery_new()}


@admin_router.post("/krypt/{node}/umstellen")
async def umstellen(node: str, body: UmstellenIn, request: Request):
    if not body.printed:
        raise HTTPException(422, t("err.krypt.not_printed"))
    res = await db(action, node, "umstellen", body.recovery)
    await db(lb.audit, rt.store, ledger.actor_of(request), "krypt_umstellen", node=node.lower())
    return res


@admin_router.get("/krypt/{node}/offen")
async def offen(node: str):
    """Noch nicht bestätigter Wiederherstellungs-Schlüssel eines neuen Pi."""
    return await db(action, node, "offen")


@admin_router.post("/krypt/{node}/bestaetigen")
async def bestaetigen(node: str, body: BestaetigenIn, request: Request):
    if not body.printed:
        raise HTTPException(422, t("err.krypt.not_printed"))
    res = await db(action, node, "bestaetigen")
    await db(lb.audit, rt.store, ledger.actor_of(request), "krypt_bestaetigt", node=node.lower())
    return res


class InternalIn(BaseModel):
    action: str = Field(pattern="^(umstellen|bestaetigen|offen)$")
    recovery: str = Field(default="", max_length=60)


@router.post("/api/internal/krypt")
async def internal(body: InternalIn, request: Request):
    """Vom Pi, an dem die Verwaltung offen ist. Mit CLUSTER_SECRET (die Kritik-Bestätigung hat er schon geprüft)."""
    require_cluster_secret(request, peer_throttle)
    return await db(local_action, body.action, body.recovery)
