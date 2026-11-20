"""Verwaltung › Preisliste: Kategorien, Produkte (Farbe, Lieferzeit, Optionen, Bild), Logo sowie Import und
Export (CSV, Bilder als ZIP). Die Bestell-App liest die Preisliste über /api/bootstrap (main.py)."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import time
import zipfile
import zlib
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import csv_import, ledger, options as options_mod
from . import ledger_base as lb
from .core import admin, db, rt
from .i18n import t
from .orders import delivery_times

admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])


# -- Kategorien ---------------------------------------------------------------
class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    color: str = Field(default="#3a3a3a", pattern=r"^#[0-9a-fA-F]{6}$")
    sort: int = 0
    vat_code: str | None = Field(default=None, pattern=r"^(normal|reduced|lodging)$")   # MWST (NULL = normal)


class OrderIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=5000)      # neue Reihenfolge (Drag & Drop in der Verwaltung)


def _reorder(table: str, ids: list[int], actor: str) -> None:
    """Reihenfolge in einem Schritt speichern: sort = 10, 20, 30 … in der Reihenfolge der Liste."""
    rt.store.batch([(f"UPDATE {table} SET sort=? WHERE id=?", ((i + 1) * 10, pid)) for i, pid in enumerate(ids)]
                   + [lb.audit_stmt(actor, f"{table}_order", count=len(ids))], transaction=True)


@admin_router.put("/products/order")
async def products_order(b: OrderIn, request: Request):
    await db(_reorder, "products", b.ids, ledger.actor_of(request))
    return {"ok": True}


@admin_router.put("/categories/order")
async def categories_order(b: OrderIn, request: Request):
    await db(_reorder, "categories", b.ids, ledger.actor_of(request))
    return {"ok": True}


def az_key(name: str) -> tuple:
    """Sortierschlüssel «wie man es erwartet» (T26): Gross/klein egal, «Ä» wie «A», Zahlen der Grösse nach
    («Bier 3dl» vor «Bier 5dl», «Wein 2dl» vor «Wein 10dl»)."""
    import re
    import unicodedata
    plain = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().casefold().strip()
    return tuple((0, int(x), "") if x.isdigit() else (1, 0, x) for x in re.findall(r"\d+|\D+", plain)) + ((2, 0, name),)


def _sort_az(actor: str) -> dict:
    """Kategorien A–Z, Produkte innerhalb jeder Kategorie A–Z (ohne Kategorie am Schluss) – einmalig, danach lässt
    sich wieder frei ziehen."""
    cats = sorted(rt.store.query("SELECT id, name FROM categories"), key=lambda c: az_key(c["name"]))
    pos = {c["id"]: i for i, c in enumerate(cats)}
    prods = sorted(rt.store.query("SELECT id, name, category_id FROM products"),
                   key=lambda p: (pos.get(p["category_id"], len(cats)), az_key(p["name"])))
    rt.store.batch([("UPDATE categories SET sort=? WHERE id=?", ((i + 1) * 10, c["id"])) for i, c in enumerate(cats)]
                   + [("UPDATE products SET sort=? WHERE id=?", ((i + 1) * 10, p["id"])) for i, p in enumerate(prods)]
                   + [lb.audit_stmt(actor, "catalog_sort_az", categories=len(cats), products=len(prods))],
                   transaction=True)
    return {"categories": len(cats), "products": len(prods)}


@admin_router.post("/catalog/sort-az")
async def catalog_sort_az(request: Request):
    return await db(_sort_az, ledger.actor_of(request))


@admin_router.get("/categories")
async def list_categories():
    return await db(rt.store.query, "SELECT * FROM categories ORDER BY sort, name")


@admin_router.post("/categories")
async def add_category(c: CategoryIn, request: Request):
    r = await db(rt.store.batch, [("INSERT INTO categories(name,color,sort,vat_code) VALUES (?,?,?,?) RETURNING id",
                                (c.name, c.color, c.sort, c.vat_code)),
                               lb.audit_stmt(ledger.actor_of(request), "category_create", name=c.name,
                                             vat_code=c.vat_code)])
    return {"id": r[0].rows[0]["id"]}


@admin_router.put("/categories/{cid}")
async def update_category(cid: int, c: CategoryIn, request: Request):
    old = await db(rt.store.query_one, "SELECT name, vat_code FROM categories WHERE id=?", (cid,))
    # MWST nur ändern, wenn mitgeschickt (ältere Aufrufe ohne das Feld setzen sie nicht zurück)
    vat = c.vat_code if "vat_code" in c.model_fields_set else (old or {}).get("vat_code")
    await db(rt.store.execute, "UPDATE categories SET name=?, color=?, sort=?, vat_code=? WHERE id=?",
             (c.name, c.color, c.sort, vat, cid))
    if old and (old["name"] != c.name or old["vat_code"] != vat):
        await db(lb.audit, rt.store, ledger.actor_of(request), "category_update", id=cid, old=old,
                 new={"name": c.name, "vat_code": vat})
    return {"ok": True}


@admin_router.delete("/categories/{cid}")
async def delete_category(cid: int, request: Request):
    old = await db(rt.store.query_one, "SELECT name FROM categories WHERE id=?", (cid,))
    await db(rt.store.execute, "DELETE FROM categories WHERE id=?", (cid,))
    if old:
        await db(lb.audit, rt.store, ledger.actor_of(request), "category_delete", id=cid, name=old["name"])
    return {"ok": True}


# -- Produkte -----------------------------------------------------------------
class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    price_cents: int = Field(ge=0, le=1_000_000)
    category_id: int | None = None
    printer_id: int | None = None
    single_ticket: bool = False
    active: bool = True
    sort: int = 0
    delivery: bool | None = None              # Lieferzeit wählbar (None = unverändert)


@admin_router.get("/products")
async def list_products():
    rows = await db(rt.store.query, """
        SELECT p.*, c.name AS category, pr.name AS printer FROM products p
        LEFT JOIN categories c ON c.id=p.category_id LEFT JOIN printers pr ON pr.id=p.printer_id
        ORDER BY c.sort, p.sort, p.name""")
    return [{**r, "options": options_mod.loads(r["options"])} for r in rows]


class OptionGroupIn(BaseModel):
    name: str = Field(default="", max_length=60)
    choices: list[str] = Field(default_factory=list, max_length=40)
    required: bool = False
    multi: bool = False


class OptionsIn(BaseModel):
    groups: list[OptionGroupIn] = Field(default_factory=list, max_length=20)
    free: bool = False


class ColorIn(BaseModel):
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")   # None = Farbe der Kategorie


@admin_router.put("/products/{pid}/color")
async def set_product_color(pid: int, c: ColorIn):
    r = await db(rt.store.execute, "UPDATE products SET color=? WHERE id=?", (c.color.lower() if c.color else None, pid))
    if not r.rows_affected:
        raise HTTPException(404)
    return {"ok": True, "color": c.color}


class DeliveryIn(BaseModel):
    enabled: bool = False                      # Lieferzeit gilt für dieses Produkt
    allowed: list[str] = Field(default_factory=list, max_length=10)   # leer = alle Zeitpunkte
    required: bool = False                     # muss aktiv gewählt werden


@admin_router.put("/products/{pid}/delivery")
async def set_product_delivery(pid: int, d: DeliveryIn):
    times = await db(delivery_times, rt.store)
    allowed = [t for t in times if t.lower() in {a.strip().lower() for a in d.allowed}]
    raw = None if not allowed or len(allowed) == len(times) else ", ".join(allowed)
    r = await db(rt.store.execute, "UPDATE products SET delivery=?, delivery_allowed=?, delivery_required=? WHERE id=?",
                 (int(d.enabled), raw, int(d.required and d.enabled), pid))
    if not r.rows_affected:
        raise HTTPException(404)
    return {"ok": True, "delivery": int(d.enabled), "delivery_allowed": raw, "delivery_required": int(d.required and d.enabled)}


@admin_router.put("/products/{pid}/options")
async def set_product_options(pid: int, o: OptionsIn):
    """Optionen eines Produkts (z.B. Zucker: ohne / 1 / 2, Pflicht; Freitext erlaubt)."""
    try:
        raw = options_mod.dumps(o.model_dump())
    except options_mod.OptionsError as e:
        raise HTTPException(422, str(e)) from None
    r = await db(rt.store.execute, "UPDATE products SET options=? WHERE id=?", (raw, pid))
    if not r.rows_affected:
        raise HTTPException(404)
    return {"ok": True, "options": options_mod.loads(raw)}


@admin_router.post("/products")
async def add_product(p: ProductIn, request: Request):
    r = await db(rt.store.batch, [("INSERT INTO products(name,price_cents,category_id,printer_id,single_ticket,active,"
                                "sort,delivery) VALUES (?,?,?,?,?,?,?,?) RETURNING id",
                                (p.name, p.price_cents, p.category_id, p.printer_id, int(p.single_ticket),
                                 int(p.active), p.sort, int(bool(p.delivery)))),
                               lb.audit_stmt(ledger.actor_of(request), "product_create", name=p.name,
                                             price=p.price_cents, active=p.active)])
    return {"id": r[0].rows[0]["id"]}


@admin_router.put("/products/{pid}")
async def update_product(pid: int, p: ProductIn, request: Request):
    old = await db(rt.store.query_one, "SELECT name, price_cents, active FROM products WHERE id=?", (pid,))
    r = await db(rt.store.execute, "UPDATE products SET name=?, price_cents=?, category_id=?, printer_id=?, "
                                "single_ticket=?, active=?, sort=?, delivery=COALESCE(?, delivery) WHERE id=?",
                 (p.name, p.price_cents, p.category_id, p.printer_id, int(p.single_ticket), int(p.active), p.sort,
                  None if p.delivery is None else int(p.delivery), pid))
    if not r.rows_affected:
        raise HTTPException(404)
    if old and (old["name"] != p.name or old["price_cents"] != p.price_cents or bool(old["active"]) != p.active):
        await db(lb.audit, rt.store, ledger.actor_of(request), "product_update", id=pid,
                 old={"name": old["name"], "price": old["price_cents"], "active": bool(old["active"])},
                 new={"name": p.name, "price": p.price_cents, "active": p.active})
    return {"ok": True}


@admin_router.delete("/products/{pid}")
async def delete_product(pid: int, request: Request):
    """Hart löschen ist ok: Bestellungen haben einen Snapshot. Meist reicht aber aktiv=0."""
    old = await db(rt.store.query_one, "SELECT name, price_cents FROM products WHERE id=?", (pid,))
    await db(rt.store.execute, "DELETE FROM products WHERE id=?", (pid,))
    if old:
        await db(lb.audit, rt.store, ledger.actor_of(request), "product_delete", id=pid, name=old["name"],
                 price=old["price_cents"])
    return {"ok": True}


MAX_UPLOAD = 8 * 1024 * 1024


IMAGE_PX = 480          # gross genug für «grosse Bilder» auf der Kachel (Handy mit 2,6-facher Pixeldichte)
# Mehr Bildpunkte nimmt FestPOS nicht an (Review 2026-11, S11): eine kleine Datei kann sonst ausgepackt den ganzen
# Arbeitsspeicher des Pi füllen. 60 Megapixel reichen für jedes Handyfoto und jeden Scan eines Grundrisses.
MAX_PIXELS = 60_000_000


def process_image(raw: bytes, box: tuple[int, int] = (IMAGE_PX, IMAGE_PX)) -> tuple[str, str, str]:
    """→ (id, mime, base64). Mit Pillow: auf höchstens «box» verkleinern, WebP (mit Transparenz).
    Ohne Pillow: Original (max 300 KB)."""
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(raw))
        if im.width * im.height > MAX_PIXELS:              # steht im Kopf der Datei: vor dem Auspacken prüfen
            raise HTTPException(413, t("err.image.too_many_pixels", mp=MAX_PIXELS // 1_000_000))
        # JPEG gleich verkleinert lesen (nicht alle Pixel eines Handyfotos in den Speicher); gross genug für
        # beide Richtungen, gedreht wird erst danach
        im.draft(None, (max(box), max(box)))
        im = ImageOps.exif_transpose(im)
        im.thumbnail(box)
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        out = io.BytesIO()
        im.save(out, "WEBP", quality=80, method=4)
        data, mime = out.getvalue(), "image/webp"
    except ImportError:
        if len(raw) > 300_000:
            raise HTTPException(413, t("err.image.too_large_raw")) from None
        data = raw
        mime = "image/png" if raw[:4] == b"\x89PNG" else "image/webp" if raw[8:12] == b"WEBP" else "image/jpeg"
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, t("err.image.unreadable", error=e)) from None
    return hashlib.sha256(data).hexdigest()[:20], mime, base64.b64encode(data).decode()


# Bilder, die kein Produkt, nicht das Logo und kein Grundriss (Tischplan) mehr verwendet, gleich mit entfernen
# (sie liegen in der Datenbank, auf allen Pi)
DROP_UNUSED_IMAGES = ("DELETE FROM images WHERE id NOT IN (SELECT image_id FROM products WHERE image_id IS NOT NULL) "
                      "AND id NOT IN (SELECT value FROM settings WHERE key='logo_image') "
                      "AND id NOT IN (SELECT value FROM settings WHERE key='web_image') "   # Bild oben auf der Webseite (app/web.py)
                      "AND id NOT IN (SELECT image_id FROM plan_areas WHERE image_id IS NOT NULL) "
                      "AND id NOT IN (SELECT value FROM settings WHERE key='bon_logo_image') "   # Bon-Logo (app/bon_design.py)
                      # Logo, das auf einer Quittung stand: eine KOPIE druckt dasselbe Logo
                      "AND id NOT IN (SELECT json_extract(data, '$.design.logo') FROM receipts "
                      "WHERE json_extract(data, '$.design.logo') IS NOT NULL) "
                      # Bon-Baukasten (T24): Bilder der Bausteine – in der Einstellung und auf jeder Quittung
                      "AND id NOT IN (SELECT json_extract(j.value, '$.image') FROM settings s, "
                      "json_each(s.value, '$.receipt.blocks') j WHERE s.key='bon_design' AND json_valid(s.value) "
                      "AND json_extract(j.value, '$.image') IS NOT NULL) "
                      "AND id NOT IN (SELECT json_extract(j.value, '$.image') FROM settings s, "
                      "json_each(s.value, '$.ticket.blocks') j WHERE s.key='bon_design' AND json_valid(s.value) "
                      "AND json_extract(j.value, '$.image') IS NOT NULL) "
                      "AND id NOT IN (SELECT json_extract(j.value, '$.image') FROM receipts r, "
                      "json_each(r.data, '$.design.blocks') j WHERE json_extract(j.value, '$.image') IS NOT NULL)", ())
LOGO_BOX = (600, 240)   # Logo: höchstens so gross (Statusanzeige am TV, Handys mit hoher Pixeldichte)


@admin_router.post("/logo")
async def upload_logo(file: UploadFile = File(...)):
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, t("err.file.too_large"))
    iid, mime, b64 = await asyncio.to_thread(process_image, raw, LOGO_BOX)
    await db(rt.store.batch, [("INSERT OR IGNORE INTO images(id,mime,data) VALUES (?,?,?)", (iid, mime, b64)),
                           ("INSERT INTO settings(key,value) VALUES ('logo_image', ?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (iid,)),
                           DROP_UNUSED_IMAGES])
    return {"logo": f"/media/{iid}"}


@admin_router.delete("/logo")
async def delete_logo():
    await db(rt.store.batch, [("DELETE FROM settings WHERE key='logo_image'", ()), DROP_UNUSED_IMAGES])
    return {"ok": True}


def image_stmts(raw: bytes, product_where: str, params: tuple) -> tuple[str, list]:
    iid, mime, b64 = process_image(raw)
    return iid, [("INSERT OR IGNORE INTO images(id,mime,data) VALUES (?,?,?)", (iid, mime, b64)),
                 (f"UPDATE products SET image_id=? WHERE {product_where}", (iid, *params)),
                 DROP_UNUSED_IMAGES]


@admin_router.post("/products/{pid}/image")
async def upload_image(pid: int, file: UploadFile = File(...)):
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, t("err.file.too_large"))
    iid, stmts = await db(image_stmts, raw, "id=?", (pid,))
    res = await db(rt.store.batch, stmts)
    if not res[1].rows_affected:
        raise HTTPException(404, t("err.product.not_found"))
    return {"image_id": iid, "url": f"/media/{iid}"}


@admin_router.delete("/products/{pid}/image")
async def delete_image(pid: int):
    """Bild vom Produkt entfernen (und aus der Datenbank, wenn es kein anderes Produkt mehr verwendet)."""
    res = await db(rt.store.batch, [("UPDATE products SET image_id=NULL WHERE id=?", (pid,)), DROP_UNUSED_IMAGES])
    if not res[0].rows_affected:
        raise HTTPException(404, t("err.product.not_found"))
    return {"ok": True}


# -- CSV / Bilder Import & Export ---------------------------------------------
@admin_router.post("/import/products")
async def import_products(request: Request, file: UploadFile = File(...), commit: bool = False,
                          deactivate_missing: bool = False):
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, t("err.file.too_large"))
    plan = await db(csv_import.preview, rt.store, raw, deactivate_missing)
    body = {"summary": plan.summary(),
            "rows": [{"line": r.line, "name": r.name, "action": r.action, "changes": r.changes,
                      "errors": r.errors} for r in plan.rows if r.action != "unchanged"],
            "deactivate": [r.name for r in plan.deactivate], "committed": False}
    if commit:
        if not plan.ok:
            return JSONResponse(body, status_code=422)
        await db(csv_import.apply, rt.store, plan)
        body["committed"] = True
        await db(lb.audit, rt.store, ledger.actor_of(request), "import", **plan.summary(),
                 changes=[{"name": r.name, "action": r.action, "changes": r.changes} for r in plan.rows
                          if r.action != "unchanged"][:200])
    return body


@admin_router.post("/import/images")
async def import_images(file: UploadFile = File(...)):
    """ZIP mit Bildern; Dateiname (ohne Endung) = Produktname, z.B. 'Bier 5dl.jpg'."""
    raw = await file.read(64 * 1024 * 1024)
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HTTPException(422, t("err.import.zip_invalid")) from None
    names = {p["name"].lower(): p["id"] for p in await db(rt.store.query, "SELECT id, name FROM products")}
    matched, unmatched, failed = [], [], []
    for info in zf.infolist():
        fname = Path(info.filename).name
        if info.is_dir() or fname.startswith(".") or info.file_size > MAX_UPLOAD:
            continue
        pid = names.get(Path(fname).stem.strip().lower())
        if pid is None:
            unmatched.append(fname)
            continue
        # ein defekter Eintrag oder ein unlesbares Bild: melden, die übrigen trotzdem übernehmen
        try:
            _, stmts = await db(image_stmts, zf.read(info), "id=?", (pid,))
        except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError, HTTPException):
            failed.append(fname)
            continue
        await db(rt.store.batch, stmts)
        matched.append(fname)
    return {"matched": matched, "unmatched": unmatched, "failed": failed}


@admin_router.get("/export/products.csv")
async def export_products():
    return Response(await db(csv_import.export_products, rt.store), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="preisliste.csv"'})


@admin_router.get("/export/orders.csv")
async def export_orders():
    return Response(await db(csv_import.export_orders, rt.store), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="bestellungen-{time.strftime("%Y%m%d")}.csv"'})


@admin_router.get("/import/template.csv")
async def import_template():
    """CSV-Vorlage mit den tatsächlich vorhandenen Druckern und Kategorien."""
    return Response(await db(csv_import.template, rt.store), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="preisliste-vorlage.csv"'})
