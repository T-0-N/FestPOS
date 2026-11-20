"""Tischplan: Bereiche (Saal, Terrasse …) mit eigenem Plan, wahlweise auf einem hochgeladenen Grundriss.

Koordinaten: Jeder Plan ist 1000 Einheiten breit und plan_areas.ratio Einheiten hoch (mit Grundriss: wie das Bild).
Ein Tisch hat Lage (px, py = linke obere Ecke), Grösse (pw, ph), Drehung (rot, Grad, um die Mitte) und Form
(bench = Festbank, rect = eckig, round = rund). Ein Tisch ohne px ist nicht im Plan (am Handy nur über die Nummer).
Beschriftungen (Bühne, Bar …) stehen als JSON in plan_areas.labels. Weil alles relativ zur Breite gilt, bleiben die
Tische an ihrem Platz, wenn ein neuer Grundriss mit demselben Ausschnitt hochgeladen wird.

Am Handy kommt der Plan mit /api/bootstrap (plan_for_phone); der Grundriss über /media/… (Service Worker: offline).
"""
from __future__ import annotations

import asyncio
import base64
import io
import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .catalog import DROP_UNUSED_IMAGES, process_image
from .core import admin, db, rt
from .i18n import t
from .store import Store

admin_router = APIRouter(prefix="/api/admin/plan", dependencies=[Depends(admin)])

WIDTH = 1000                    # Breite jedes Plans in Plan-Einheiten
RATIO_DEFAULT = 1400            # ohne Grundriss: hochkant wie ein Handy
RATIO_MIN, RATIO_MAX = 250, 4000
IMAGE_BOX = (2000, 2000)        # Grundriss: höchstens so gross (WebP), scharf genug zum Zoomen am Handy
MAX_IMAGE = 15 * 1024 * 1024    # Upload (Foto oder Scan eines Plans)
SHAPES = ("bench", "rect", "round")
STARTS = ("plan", "number", "off")
MAX_LABELS = 60


def start_mode(store: Store) -> str:
    """Womit die Tischwahl am Handy beginnt: plan | number | off (kein Plan, wie früher)."""
    v = store.setting("plan_start", "plan")
    return v if v in STARTS else "plan"


def _labels(raw: str | None) -> list[dict]:
    try:
        v = json.loads(raw or "[]")
    except ValueError:
        return []
    return v if isinstance(v, list) else []


def _area_out(r: dict) -> dict:
    return {"id": r["id"], "name": r["name"], "sort": r["sort"], "ratio": r["ratio"], "opacity": r["opacity"],
            "image": f"/media/{r['image_id']}" if r.get("image_id") else None, "labels": _labels(r.get("labels"))}


def _table_out(r: dict) -> dict:
    placed = r.get("px") is not None and r.get("area_id") is not None
    return {"id": r["id"], "label": r["label"], "sort": r["sort"], "area_id": r.get("area_id") if placed else None,
            "x": r.get("px"), "y": r.get("py"), "w": r.get("pw"), "h": r.get("ph"), "rot": r.get("rot") or 0,
            "shape": r.get("shape") or "rect", "placed": placed}


def load(store: Store) -> dict:
    areas = store.query("SELECT * FROM plan_areas ORDER BY sort, name")
    tables = store.query("SELECT id, label, sort, area_id, px, py, pw, ph, rot, shape FROM tables ORDER BY sort, label")
    return {"areas": [_area_out(a) for a in areas], "tables": [_table_out(x) for x in tables], "start": start_mode(store)}


def plan_for_phone(store: Store) -> dict | None:
    """Für /api/bootstrap: nur Bereiche mit mindestens einem Tisch im Plan; None = kein Plan (Tischwahl wie früher)."""
    if start_mode(store) == "off":
        return None
    areas = {a["id"]: a for a in store.query("SELECT * FROM plan_areas ORDER BY sort, name")}
    tables: dict[int, list] = {}
    for r in store.query("SELECT label, area_id, px, py, pw, ph, rot, shape FROM tables "
                         "WHERE px IS NOT NULL AND area_id IS NOT NULL ORDER BY sort, label"):
        if r["area_id"] in areas:
            tables.setdefault(r["area_id"], []).append(
                {"l": r["label"], "x": r["px"], "y": r["py"], "w": r["pw"], "h": r["ph"], "r": r["rot"] or 0,
                 "s": r["shape"] or "rect"})
    out = []
    for aid, a in areas.items():
        if aid in tables:
            area = _area_out(a)
            out.append({"id": aid, "name": area["name"], "ratio": area["ratio"], "opacity": area["opacity"],
                        "image": area["image"], "labels": area["labels"], "tables": tables[aid]})
    return {"start": start_mode(store), "areas": out} if out else None


# -- Verwaltung ----------------------------------------------------------------------------------------
@admin_router.get("")
async def get_plan():
    return await db(load, rt.store)


class AreaIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)


@admin_router.post("/areas")
async def add_area(a: AreaIn):
    name = " ".join(a.name.split())
    if not name:
        raise HTTPException(422, t("err.plan.name"))
    base = await db(rt.store.scalar, "SELECT COALESCE(MAX(sort), 0) FROM plan_areas") or 0
    await db(rt.store.execute, "INSERT INTO plan_areas(name, sort, ratio, opacity) VALUES (?,?,?,?)",
             (name, base + 1, RATIO_DEFAULT, 50))
    return await db(load, rt.store)


class LabelIn(BaseModel):
    t: str = Field(min_length=1, max_length=30)
    x: int = Field(ge=-WIDTH, le=2 * WIDTH)
    y: int = Field(ge=-RATIO_MAX, le=2 * RATIO_MAX)
    w: int = Field(ge=10, le=WIDTH)
    h: int = Field(ge=10, le=RATIO_MAX)


class TablePosIn(BaseModel):
    id: int
    x: int = Field(ge=-WIDTH, le=2 * WIDTH)
    y: int = Field(ge=-RATIO_MAX, le=2 * RATIO_MAX)
    w: int = Field(ge=10, le=WIDTH)
    h: int = Field(ge=10, le=RATIO_MAX)
    rot: int = Field(default=0, ge=-360, le=360)
    shape: str = Field(default="rect", pattern="^(" + "|".join(SHAPES) + ")$")


class AreaPut(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    sort: int | None = Field(default=None, ge=0, le=100000)
    opacity: int | None = Field(default=None, ge=0, le=100)
    ratio: int | None = Field(default=None, ge=RATIO_MIN, le=RATIO_MAX)
    labels: list[LabelIn] | None = Field(default=None, max_length=MAX_LABELS)
    tables: list[TablePosIn] | None = Field(default=None, max_length=1000)   # Lage dieser Tische (in diesem Bereich)
    remove: list[int] | None = Field(default=None, max_length=1000)          # diese Tische aus dem Plan nehmen


def _rot(v: int) -> int:
    v = v % 360
    return v - 360 if v > 180 else v


def save_area(store: Store, aid: int, p: AreaPut) -> None:
    if not store.query_one("SELECT id FROM plan_areas WHERE id=?", (aid,)):
        raise HTTPException(404, t("err.plan.not_found"))
    stmts = []
    sets, args = [], []
    if p.name is not None:
        name = " ".join(p.name.split())
        if not name:
            raise HTTPException(422, t("err.plan.name"))
        sets.append("name=?"), args.append(name)
    for col in ("sort", "opacity", "ratio"):
        if getattr(p, col) is not None:
            sets.append(f"{col}=?"), args.append(getattr(p, col))
    if p.labels is not None:
        sets.append("labels=?")
        args.append(json.dumps([{"t": " ".join(x.t.split()), "x": x.x, "y": x.y, "w": x.w, "h": x.h} for x in p.labels],
                               ensure_ascii=False))
    if sets:
        stmts.append((f"UPDATE plan_areas SET {', '.join(sets)} WHERE id=?", (*args, aid)))
    for x in p.tables or []:
        stmts.append(("UPDATE tables SET area_id=?, px=?, py=?, pw=?, ph=?, rot=?, shape=? WHERE id=?",
                      (aid, x.x, x.y, x.w, x.h, _rot(x.rot), x.shape, x.id)))
    for tid in p.remove or []:
        stmts.append(("UPDATE tables SET area_id=NULL, px=NULL, py=NULL, pw=NULL, ph=NULL, rot=0 WHERE id=? AND area_id=?",
                      (tid, aid)))
    if stmts:
        store.batch(stmts, transaction=True)


@admin_router.put("/areas/{aid}")
async def put_area(aid: int, p: AreaPut):
    await db(save_area, rt.store, aid, p)
    return await db(load, rt.store)


@admin_router.delete("/areas/{aid}")
async def delete_area(aid: int):
    """Bereich weg: seine Tische bleiben (nur nicht mehr im Plan), der Grundriss wird gelöscht."""
    await db(rt.store.batch, [
        ("UPDATE tables SET area_id=NULL, px=NULL, py=NULL, pw=NULL, ph=NULL, rot=0 WHERE area_id=?", (aid,)),
        ("DELETE FROM plan_areas WHERE id=?", (aid,)), DROP_UNUSED_IMAGES], transaction=True)
    return await db(load, rt.store)


def image_ratio(b64: str) -> int | None:
    """Höhe des Plans (Breite 1000) nach dem Seitenverhältnis des Bildes."""
    try:
        from PIL import Image
        w, h = Image.open(io.BytesIO(base64.b64decode(b64))).size
    except Exception:  # noqa: BLE001  (ohne Pillow oder unlesbar: Verhältnis bleibt)
        return None
    return max(RATIO_MIN, min(RATIO_MAX, round(WIDTH * h / w))) if w else None


@admin_router.post("/areas/{aid}/image")
async def upload_image(aid: int, file: UploadFile = File(...)):
    if not await db(rt.store.query_one, "SELECT id FROM plan_areas WHERE id=?", (aid,)):
        raise HTTPException(404, t("err.plan.not_found"))
    raw = await file.read(MAX_IMAGE + 1)
    if len(raw) > MAX_IMAGE:
        raise HTTPException(413, t("err.file.too_large"))
    iid, mime, b64 = await asyncio.to_thread(process_image, raw, IMAGE_BOX)
    ratio = await asyncio.to_thread(image_ratio, b64)
    stmts = [("INSERT OR IGNORE INTO images(id,mime,data) VALUES (?,?,?)", (iid, mime, b64)),
             ("UPDATE plan_areas SET image_id=? WHERE id=?", (iid, aid))]
    if ratio:
        stmts.append(("UPDATE plan_areas SET ratio=? WHERE id=?", (ratio, aid)))
    stmts.append(DROP_UNUSED_IMAGES)
    await db(rt.store.batch, stmts, transaction=True)
    return await db(load, rt.store)


@admin_router.delete("/areas/{aid}/image")
async def delete_image(aid: int):
    await db(rt.store.batch, [("UPDATE plan_areas SET image_id=NULL WHERE id=?", (aid,)), DROP_UNUSED_IMAGES],
             transaction=True)
    return await db(load, rt.store)


class StartIn(BaseModel):
    start: str = Field(pattern="^(" + "|".join(STARTS) + ")$")


@admin_router.put("/settings")
async def put_settings(s: StartIn):
    await db(rt.store.execute, "INSERT INTO settings(key, value) VALUES ('plan_start', ?) "
             "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (s.start,))
    return await db(load, rt.store)
