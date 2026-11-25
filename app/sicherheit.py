"""Sicherheitsereignisse in Protokoll und Übersicht (T-0-N/FestPOS#11) – Teil der App.

Jeder Pi liest alle POLL_S Sekunden die Datei, die der root-Dienst festpos-ereignisse schreibt (app/ereignisse.py:
SSH-Anmeldung, sudo, Neustart, geänderte Boot-Partition, fremd signiertes Update), und trägt neue Einträge ins
Protokoll ein (Aktion «sec_<art>», Wer = «Pi X»). Dazu kommen Ereignisse der App selbst: Anmeldesperre nach zu
vielen Fehlversuchen (core.Throttle) und jeder Notfall-Login. Über die Datenbank sieht jeder Pi alle – die Übersicht
fragt alle 5 Sekunden nach: so steht eine SSH-Anmeldung auf Pi B innert einer Minute in der Verwaltung von Pi A.

Übersicht: gelbe Karte mit den Ereignissen seit dem letzten «Gesehen» (Einstellung SEEN_KEY = höchste gesehene
Nummer im Protokoll, für alle gleich).
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

from fastapi import APIRouter, Depends, Query

from . import ereignisse, ledger_base as lb
from .core import Config, admin, db, login_throttle, rt

log = logging.getLogger("festpos.sicherheit")
FILE = ereignisse.OUT                                     # schreibt der root-Dienst festpos-ereignisse
POLL_S = 10
SEEN_KEY = "security_seen"
APP_KINDS = ("lockout",)                                  # aus der App selbst
ACTIONS = tuple(f"sec_{k}" for k in (*ereignisse.KINDS, *APP_KINDS)) + ("emergency_login",)
SHOW_MAX = 30
admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])

_queue: list[dict] = []                                   # Ereignisse der App, bis der nächste Lauf sie einträgt
_qlock = threading.Lock()
_task: asyncio.Task | None = None


def _pos_key() -> str:
    return f"security_pos.{Config.node}"                  # pro Pi: bis wohin die Datei schon eingetragen ist


def note(kind: str, **details) -> None:
    """Ereignis der App (z.B. Anmeldesperre) – ohne Datenbank im Aufruf, eingetragen beim nächsten Lauf."""
    with _qlock:
        if len(_queue) < 200:
            _queue.append({"kind": kind, **details})


# Anmeldesperre der Verwaltung (zu viele Fehlversuche von einer Adresse oder für einen Namen)
login_throttle.on_lock = lambda ip, name, how: note("lockout", ip=ip, name=name[:60], by=how)


def import_new() -> int:
    """Neue Ereignisse aus der Datei und der App ins Protokoll -> Anzahl."""
    store, who = rt.store, f"Pi {str(Config.node).upper()}"
    d = ereignisse.read(FILE)
    try:
        pos = json.loads(store.setting(_pos_key(), "{}") or "{}")
    except ValueError:
        pos = {}
    seq = int(pos.get("seq") or 0) if pos.get("run") == d.get("run") else 0
    new = [e for e in d.get("events", []) if isinstance(e, dict) and int(e.get("seq") or 0) > seq
           and e.get("kind") in ereignisse.KINDS]
    with _qlock:
        app_events = [dict(e) for e in _queue]
    stmts = []
    for e in new:
        details = {k: v for k, v in e.items() if k not in ("seq", "kind", "at")}
        try:                                  # wann es geschah (kann vor dem Eintragen liegen, z.B. App noch nicht da)
            details["time"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(int(e.get("at"))))
        except (TypeError, ValueError, OverflowError):
            pass
        stmts.append(lb.audit_stmt(who, f"sec_{e['kind']}", node=Config.node, **details))
    for e in app_events:
        stmts.append(lb.audit_stmt(who, f"sec_{e.pop('kind')}", node=Config.node, **e))
    if new:
        stmts.append(("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (_pos_key(), json.dumps({"run": d.get("run"), "seq": max(int(e["seq"]) for e in new)}))))
    if stmts:
        store.batch(stmts)                    # Fehler (Datenbank kurz weg): alles bleibt für den nächsten Lauf
        with _qlock:
            del _queue[:len(app_events)]      # nur hinten angehängt: die eingetragenen sind vorne
    return len(new) + len(app_events)


async def _loop() -> None:
    while True:
        try:
            await asyncio.to_thread(import_new)
        except Exception as e:  # noqa: BLE001 – nie aufgeben
            log.warning("Sicherheitsereignisse nicht eingetragen: %s", e)
        await asyncio.sleep(POLL_S)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(), name="sicherheit")


async def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)
        _task = None


def _marks() -> str:
    return ",".join("?" * len(ACTIONS))


def recent() -> dict:
    """Ereignisse seit dem letzten «Gesehen» (neueste zuerst) für die Übersicht."""
    store = rt.store
    try:
        seen = int(store.setting(SEEN_KEY, "0") or 0)
    except ValueError:
        seen = 0
    rows = store.query(f"SELECT id, at, actor, action, details FROM audit_log WHERE id > ? AND action IN ({_marks()}) "
                       "ORDER BY id DESC LIMIT ?", (seen, *ACTIONS, SHOW_MAX))
    count = store.scalar(f"SELECT COUNT(*) FROM audit_log WHERE id > ? AND action IN ({_marks()})", (seen, *ACTIONS))
    for r in rows:
        try:
            r["details"] = json.loads(r["details"] or "{}")
        except ValueError:
            r["details"] = {}
    return {"count": int(count or 0), "events": rows, "last": rows[0]["id"] if rows else seen}


@admin_router.get("/security")
async def security():
    return await db(recent)


@admin_router.post("/security/seen")
async def seen(upto: int = Query(..., ge=0)):
    """«Gesehen»: die Karte verschwindet (für alle), bis wieder etwas passiert."""
    def run():
        rt.store.batch([("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET "
                         "value=MAX(CAST(value AS INTEGER), CAST(excluded.value AS INTEGER))", (SEEN_KEY, str(upto)))])
    await db(run)
    return {"ok": True}
