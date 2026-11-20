"""Verwaltung › Tische: Liste der Tische, Tischkarten mit QR-Code (Bondrucker oder A4-PDF)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import i18n, print_service as ps, qr
from .core import admin, db, rt
from .i18n import t

admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])


# -- Tische -------------------------------------------------------------------
class TablesIn(BaseModel):
    labels: list[str] = Field(default_factory=list, max_length=1000)
    range_from: int | None = Field(default=None, ge=0, le=99999)
    range_to: int | None = Field(default=None, ge=0, le=99999)
    replace: bool = False


@admin_router.get("/tables")
async def list_tables():
    return await db(rt.store.query, "SELECT * FROM tables ORDER BY sort, label")


@admin_router.post("/tables")
async def set_tables(t: TablesIn):
    labels = []
    if t.range_from is not None and t.range_to is not None:
        if t.range_to - t.range_from >= 1000:
            raise HTTPException(422, i18n.t("err.tables.too_many"))
        labels += [str(i) for i in range(t.range_from, t.range_to + 1)]
    labels += [l.strip()[:40] for l in t.labels if l.strip()]
    base = 0 if t.replace else (await db(rt.store.scalar, "SELECT COALESCE(MAX(sort), 0) FROM tables") or 0)
    stmts = [("DELETE FROM tables", ())] if t.replace else []
    stmts += [("INSERT OR IGNORE INTO tables(label, sort) VALUES (?,?)", (l, base + i + 1)) for i, l in enumerate(labels)]
    await db(rt.store.batch, stmts)
    return await list_tables()


class TableQrIn(BaseModel):
    printer_id: int
    labels: list[str] = Field(default_factory=list)       # leer = alle Tische


@admin_router.post("/tables/print-qr")
async def print_table_qr(t: TableQrIn):
    labels = t.labels or [r["label"] for r in await db(rt.store.query, "SELECT label FROM tables ORDER BY sort, label")]
    if not labels:
        raise HTTPException(422, i18n.t("err.tables.none"))
    n = await db(ps.enqueue_table_qr, rt.store, t.printer_id, labels)
    rt.spooler.notify(t.printer_id)
    return {"queued": n}


@admin_router.get("/tables/qr.pdf")
async def tables_qr_pdf(labels: str = ""):
    """Tischkarten mit QR-Code als A4-PDF (6 pro Seite). labels: kommagetrennt, leer = alle."""
    wanted = [l.strip() for l in labels.split(",") if l.strip()]
    if not wanted:
        wanted = [r["label"] for r in await db(rt.store.query, "SELECT label FROM tables ORDER BY sort, label")]
    if not wanted:
        raise HTTPException(422, t("err.tables.none"))
    pdf = await db(qr.tables_pdf, wanted, await db(rt.store.setting, "event_name"),
                   await db(rt.store.setting, "table_word", "Tisch"))
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="tischkarten.pdf"'})


@admin_router.delete("/tables/{tid}")
async def delete_table(tid: int):
    await db(rt.store.execute, "DELETE FROM tables WHERE id=?", (tid,))
    return {"ok": True}
