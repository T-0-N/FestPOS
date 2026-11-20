"""Verwaltung › Drucker und Druckaufträge: Drucker anlegen, prüfen, umleiten; Bons nachdrucken oder erledigen.
Der Druckdienst selbst steht in print_service.py."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

import os

from . import i18n, ledger, monitor as monitor_mod, print_service as ps, receipts
from .core import LOCAL_HOSTS, Config, admin, db, rt
from .escpos import PrinterAddr, query_status
from .i18n import t

admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])


# -- Drucker ----------------------------------------------------------------
class PrinterIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    host: str = Field(min_length=1, max_length=64)
    port: int = Field(default=9100, ge=1, le=65535)
    chars_per_line: int = Field(default=48, ge=24, le=64)
    show_prices: bool = True
    enabled: bool = True
    fail_mode: str = Field(default="wait", pattern=r"^(wait|reroute|warn)$")   # wenn nicht erreichbar
    fail_target: int | None = None                                           # Ersatz-/Warndrucker
    fail_after: int = Field(default=60, ge=10, le=3600)                      # Sekunden bis dahin
    language: str = Field(default="", max_length=12)                         # Bon-Sprache, "" = Standard


# Dienste dieses Pi: nie als «Drucker» (Review 2026-11, S11). Sonst schickte FestPOS Bons und Statusabfragen an die
# eigene Datenbank (rqlite, ohne Anmeldung), an SSH oder an sich selbst.
OWN_PORTS = frozenset({22, 53, 80, 443, 4001, 4002, 8090, 8443})


def _check_addr(p: PrinterIn) -> None:
    """Adresse eines Druckers: IP oder Hostname, und kein Dienst dieses Pi."""
    host = p.host.strip()
    if not monitor_mod.HOST_RE.match(host):
        raise HTTPException(422, t("err.printer.host"))
    own = {*LOCAL_HOSTS, Config.node_ip, os.environ.get("FESTPOS_VIP", ""), "0.0.0.0"} - {""}
    if (host.lower() in own or host.startswith("127.")) and \
            p.port in OWN_PORTS | {Config.phone_port, Config.admin_port}:
        raise HTTPException(422, t("err.printer.host_own"))


def _printer_lang(p: PrinterIn) -> str | None:
    if not p.language:
        return None
    code = i18n.normalize(p.language)
    if not code:
        raise HTTPException(422, t("err.i18n.unknown", lang=p.language))
    return code


@admin_router.get("/printers")
async def list_printers():
    rows = await db(ps.queue_status, rt.store)
    return [{**r, "down": rt.spooler.is_down(r["id"])} for r in rows]


@admin_router.post("/printers")
async def add_printer(p: PrinterIn):
    _check_addr(p)
    r = await db(rt.store.execute, "INSERT INTO printers(name,host,port,chars_per_line,show_prices,enabled,"
                                "fail_mode,fail_target,fail_after,language) VALUES (?,?,?,?,?,?,?,?,?,?) RETURNING id",
                 (p.name, p.host.strip(), p.port, p.chars_per_line, int(p.show_prices), int(p.enabled),
                  p.fail_mode, p.fail_target, p.fail_after, _printer_lang(p)))
    rt.spooler.notify(r.rows[0]["id"])
    return {"id": r.rows[0]["id"]}


@admin_router.put("/printers/{pid}")
async def update_printer(pid: int, p: PrinterIn):
    if p.fail_target == pid:
        raise HTTPException(422, t("err.printer.self_target"))
    _check_addr(p)
    # Andere Adresse = womöglich ein anderer Drucker: ob er die Statusabfrage beantwortet, neu lernen (T15)
    r = await db(rt.store.execute, "UPDATE printers SET name=?, host=?, port=?, chars_per_line=?, show_prices=?, "
                                "enabled=?, fail_mode=?, fail_target=?, fail_after=?, language=?, "
                                "status_seen=CASE WHEN host=? AND port=? THEN status_seen ELSE 0 END WHERE id=?",
                 (p.name, p.host.strip(), p.port, p.chars_per_line, int(p.show_prices), int(p.enabled),
                  p.fail_mode, p.fail_target, p.fail_after, _printer_lang(p), p.host, p.port, pid))
    if not r.rows_affected:
        raise HTTPException(404)
    rt.spooler.notify(pid)
    return {"ok": True}


@admin_router.delete("/printers/{pid}")
async def delete_printer(pid: int):
    """Löschen würde alle Bons dieses Druckers mitlöschen (auch umgeleitete). Solange noch etwas offen
    oder unklar ist, wird es verweigert (REV-014)."""
    res = await db(rt.store.batch, [
        ("DELETE FROM printers WHERE id=? AND NOT EXISTS (SELECT 1 FROM print_jobs WHERE (printer_id=? OR station_id=?) "
         "AND status IN ('pending','printing','failed','uncertain'))", (pid, pid, pid)),
        ("UPDATE printers SET fail_target=NULL WHERE fail_target=? AND NOT EXISTS (SELECT 1 FROM printers WHERE id=?)",
         (pid, pid)),
    ])
    if res[0].rows_affected:
        return {"ok": True}
    n = await db(ps.open_jobs, rt.store, pid)
    if not n:
        raise HTTPException(404, t("err.printer.not_found"))
    raise HTTPException(409, t("err.printer.has_jobs", count=n))


@admin_router.get("/printers/{pid}/status")
async def printer_status(pid: int):
    p = await db(rt.store.query_one, "SELECT host, port, status_seen FROM printers WHERE id=?", (pid,))
    if not p:
        raise HTTPException(404)
    st = await query_status(PrinterAddr(p["host"], p["port"]))
    if st.online and not st.no_answer and not p["status_seen"]:
        await db(ps.mark_status_seen, rt.store, pid)
    # status_seen: hat früher geantwortet – schweigt er jetzt, druckt FestPOS nicht (Papier/Deckel prüfen)
    return {**st.__dict__, "ok": st.ok, "status_seen": bool(p["status_seen"]) or (st.online and not st.no_answer)}


@admin_router.post("/printers/{pid}/test")
async def printer_test(pid: int):
    jid = await db(ps.enqueue_test, rt.store, pid)
    rt.spooler.notify(pid)
    return {"job_id": jid}


@admin_router.post("/printers/{pid}/reroute")
async def printer_reroute(pid: int, to: int = Query(...)):
    target = await db(rt.store.query_one, "SELECT id, enabled FROM printers WHERE id=?", (to,))
    if to == pid or not target or not target["enabled"]:          # sonst warteten die Bons bei einem toten Ziel
        raise HTTPException(422, t("err.printer.reroute_target"))
    n = await db(ps.reroute_pending, rt.store, pid, to)
    rt.spooler.notify(to)
    return {"moved": n}


# -- Druckjobs ----------------------------------------------------------------
@admin_router.get("/jobs")
async def jobs(status: str | None = None, limit: int = Query(200, ge=1, le=2000)):
    return await db(ps.list_jobs, rt.store, status, limit)


@admin_router.post("/jobs/{jid}/reprint")
async def job_reprint(jid: int, request: Request):
    try:
        new = await db(ps.reprint, rt.store, jid)
    except ps.JobBusy as e:
        raise HTTPException(409, str(e)) from None
    if not new:
        raise HTTPException(404)
    await db(receipts.on_reprint, rt.store, jid, new, ledger.actor_of(request))     # Quittung: Original oder KOPIE
    pid = await db(rt.store.scalar, "SELECT printer_id FROM print_jobs WHERE id=?", (new,))
    rt.spooler.notify(pid)
    return {"job_id": new}


@admin_router.post("/jobs/{jid}/resolve")
async def job_resolve(jid: int):
    try:
        return {"ok": await db(ps.resolve, rt.store, jid)}
    except ps.JobBusy as e:
        raise HTTPException(409, str(e)) from None
