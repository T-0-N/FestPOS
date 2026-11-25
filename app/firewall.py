"""Firewall für Verbindungen der Pi ins Internet: Modus in der Verwaltung (T-0-N/FestPOS#10).

Der Modus ist eine Einstellung (für alle Pi gleich, MODE_KEY/UNTIL_KEY). Jeder Pi schreibt ihn alle POLL_S Sekunden in
WISH; der root-Dienst festpos-firewall (deploy/) wendet ihn an und meldet den Stand in STAND (Zähler, verworfene
Ziele) – den zeigt die Verwaltung pro Pi (system.local_info «fw»). «Beobachten» und «Offen» gelten höchstens
ausgehend.MAX_HOURS: danach stellt jeder Pi die Einstellung wieder auf «strict». Liste und Regeln: app/ausgehend.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import ausgehend, i18n, ledger_base as lb, secretbox
from .core import admin, current_user, db, rt

log = logging.getLogger("festpos.firewall")
WISH = Path(os.environ.get("FESTPOS_FW_WISH", "/run/festpos/firewall/modus.json"))
STAND = Path(os.environ.get("FESTPOS_FW_STAND", "/run/festpos/firewall-stand/stand.json"))
MODE_KEY, UNTIL_KEY = "fw_mode", "fw_until"
POLL_S = 30
admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])
_task: asyncio.Task | None = None


def current() -> tuple[str, int]:
    """(Modus, bis) aus den Einstellungen – abgelaufen: strict."""
    store = rt.store
    mode = store.setting(MODE_KEY, ausgehend.DEFAULT) or ausgehend.DEFAULT
    try:
        until = int(store.setting(UNTIL_KEY, "0") or 0)
    except ValueError:
        until = 0
    if mode not in ausgehend.MODES or (mode != "strict" and until <= time.time()):
        return ausgehend.DEFAULT, 0
    return mode, until if mode != "strict" else 0


def write_wish(mode: str, until: int) -> bool:
    """Wunsch für festpos-firewall schreiben (nur wenn anders) -> geschrieben?"""
    data = json.dumps({"mode": mode, "until": until})
    try:
        if WISH.read_text() == data:
            return False
    except OSError:
        pass
    try:
        WISH.parent.mkdir(parents=True, exist_ok=True)
        tmp = WISH.with_name(".modus.neu")
        tmp.write_text(data)
        os.replace(tmp, WISH)
        return True
    except OSError as e:
        log.warning("Firewall-Modus nicht geschrieben: %s", e)
        return False


def sync() -> None:
    """Einstellung -> Wunsch; abgelaufenes «Beobachten»/«Offen» zurück auf strict (Protokoll)."""
    store = rt.store
    mode, until = current()
    raw = store.setting(MODE_KEY, ausgehend.DEFAULT) or ausgehend.DEFAULT
    if raw != mode:
        store.batch([(secretbox.UPSERT, (MODE_KEY, mode)), (secretbox.UPSERT, (UNTIL_KEY, "0")),
                     lb.audit_if_changed_stmt("FestPOS", "firewall", mode=mode, expired=raw)])
    write_wish(mode, until)


async def _loop() -> None:
    while True:
        try:
            await asyncio.to_thread(sync)
        except Exception as e:  # noqa: BLE001 – Datenbank kurz weg: nächster Lauf
            log.warning("Firewall-Modus: %s", e)
        await asyncio.sleep(POLL_S)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(), name="firewall")


async def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)
        _task = None


def local_status() -> dict | None:
    """Stand von festpos-firewall auf diesem Pi (für system.local_info)."""
    try:
        d = json.loads(STAND.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    seen = [{"ip": str(x.get("ip"))[:45], "proto": str(x.get("proto"))[:5], "port": x.get("port")}
            for x in (d.get("seen") or [])[:20] if isinstance(x, dict)]
    return {"mode": d.get("mode") if d.get("mode") in ausgehend.MODES else None, "at": d.get("at"),
            "unknown": d.get("unknown"), "seen": seen, "error": str(d.get("error") or "")[:200],
            "addrs": d.get("addrs")}


class FirewallIn(BaseModel):
    mode: str


@admin_router.get("/firewall")
async def get_firewall():
    mode, until = await db(current)
    return {"mode": mode, "until": until or None, "max_hours": ausgehend.MAX_HOURS,
            "hosts": [{"name": h, "what": w} for h, w in ausgehend.HOSTS.items()]}


@admin_router.put("/firewall")
async def put_firewall(f: FirewallIn, request: Request):
    """Modus wählen (kritisch: Passwort bestätigen). «watch»/«open» für MAX_HOURS, danach wieder strict."""
    if f.mode not in ausgehend.MODES:
        raise HTTPException(422, i18n.t("err.firewall.mode"))
    until = int(time.time()) + ausgehend.MAX_HOURS * 3600 if f.mode != "strict" else 0
    who = (current_user(request) or {}).get("name") or "?"
    await db(rt.store.batch, [(secretbox.UPSERT, (MODE_KEY, f.mode)), (secretbox.UPSERT, (UNTIL_KEY, str(until))),
                              lb.audit_stmt(who, "firewall", mode=f.mode, hours=ausgehend.MAX_HOURS if until else None)])
    await db(write_wish, f.mode, until)                    # dieser Pi sofort, die anderen beim nächsten Lauf (30 s)
    return {"mode": f.mode, "until": until or None}
