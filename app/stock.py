"""Ausverkauft und Restmenge.

Pro Produkt (Tabelle products):
  soldout     1 = von Hand als ausverkauft markiert (Verwaltung oder Handy mit der Rolle «Festleitung»)
  stock       Restmenge, NULL = ohne Bestand (Normalfall). Zählt beim Bestellen herunter (app/orders.py, im selben
              atomaren Batch wie die Bestellung); 0 = ausverkauft. Der CHECK (stock >= 0) in der Datenbank verhindert,
              dass zwei Handys gleichzeitig die letzten Stück verkaufen: die zweite Bestellung wird als Ganzes abgelehnt.
  stock_warn  ab dieser Restmenge warnt die Statusanzeige (NULL = nie)
Nicht verfügbar (Kachel grau, Server lehnt ab) = soldout oder stock = 0. Übungsbestellungen (Testmodus) prüfen mit,
zählen aber nicht herunter. Ein Storno bucht die Menge nicht zurück (die Küche hat vielleicht schon gekocht) –
die Restmenge stellt die Festleitung bei Bedarf von Hand richtig.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import devices
from . import ledger_base as lb
from .core import admin, db, rt
from .i18n import t
from .store import Store

router = APIRouter()                                     # Handy (Gerätezertifikat)
admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])

STOCK_MAX = 99_999


def available(p: dict) -> bool:
    return not p.get("soldout") and (p.get("stock") is None or p["stock"] > 0)


CHECK_MODES = ("off", "stock", "always")


def check_config(store: Store) -> dict:
    """Wartet das Handy vor dem Kassieren auf den Server? off = nie (sofort zur Kasse) | stock = nur, wenn ein Produkt
    mit Restmenge im Warenkorb ist | always = bei jeder Bestellung (Standard). wait = höchstens so viele Sekunden;
    kommt keine Antwort, geht es zur Kasse wie bisher. Der Server prüft in jedem Fall."""
    mode = store.setting("stock_check", "always")
    try:
        wait = max(1, min(10, int(store.setting("stock_check_wait", "2") or 2)))
    except ValueError:
        wait = 2
    return {"mode": mode if mode in CHECK_MODES else "always", "wait": wait}


def problems(products: dict[int, dict], need: dict[int, int]) -> list[dict]:
    """Alle Produkte, die nicht gehen: left = 0 (ausverkauft) oder die Restmenge, die noch da ist."""
    out = []
    for pid, qty in need.items():
        p = products[pid]
        if not available(p):
            out.append({"product_id": pid, "name": p["name"], "left": 0})
        elif p.get("stock") is not None and qty > p["stock"]:
            out.append({"product_id": pid, "name": p["name"], "left": p["stock"]})
    return sorted(out, key=lambda x: (x["left"] > 0, x["name"].lower()))


def message(probs: list[dict]) -> str:
    """«Ausverkauft: Bratwurst, Pommes; Von Bier 5dl hat es nur noch 1» – alle auf einmal."""
    out = [p["name"] for p in probs if p["left"] == 0]
    parts = [t("err.order.sold_out", names=", ".join(out))] if out else []
    parts += [t("err.order.stock_short", name=p["name"], count=p["left"]) for p in probs if p["left"] > 0]
    return "; ".join(parts)


def availability(store: Store) -> dict:
    """Für die Handys (alle paar Sekunden): ausverkaufte Produkte und Restmengen der Produkte mit Bestand."""
    rows = store.query("SELECT id, soldout, stock, stock_warn FROM products WHERE active=1 "
                       "AND (soldout=1 OR stock IS NOT NULL)")
    return {"soldout": [r["id"] for r in rows if r["soldout"]],                  # von Hand (Restmenge 0 steht in stock)
            "stock": {str(r["id"]): r["stock"] for r in rows if r["stock"] is not None},
            "warn": {str(r["id"]): r["stock_warn"] for r in rows if r["stock"] is not None and r["stock_warn"] is not None}}


def status_list(store: Store) -> list[dict]:
    """Für die Statusanzeige: ausverkaufte Produkte und solche, deren Restmenge die Warnschwelle erreicht hat."""
    rows = store.query("SELECT name, soldout, stock, stock_warn FROM products WHERE active=1 AND (soldout=1 OR "
                       "stock=0 OR (stock IS NOT NULL AND stock_warn IS NOT NULL AND stock <= stock_warn)) "
                       "ORDER BY name")
    return [{"name": r["name"], "soldout": not available(r), "stock": r["stock"]} for r in rows]


# -- Verwaltung -------------------------------------------------------------------------------------------------
class StockIn(BaseModel):
    soldout: bool = False
    stock: int | None = Field(default=None, ge=0, le=STOCK_MAX)        # None = ohne Bestand
    stock_warn: int | None = Field(default=None, ge=0, le=STOCK_MAX)   # None = keine Warnung


@admin_router.put("/products/{pid}/stock")
async def product_stock(pid: int, s: StockIn, request: Request):
    from .ledger import actor_of                         # spät: ledger braucht orders, orders braucht stock
    warn = s.stock_warn if s.stock is not None else None
    name = await db(rt.store.scalar, "SELECT name FROM products WHERE id=?", (pid,))
    if name is None:
        raise HTTPException(404, t("err.product.not_found"))
    res = await db(rt.store.batch, [
        ("UPDATE products SET soldout=?, stock=?, stock_warn=? WHERE id=?", (int(s.soldout), s.stock, warn, pid)),
        lb.audit_stmt(actor_of(request), "product_stock", product=pid, name=name, soldout=s.soldout, stock=s.stock, warn=warn)])
    if not res[0].rows_affected:
        raise HTTPException(404, t("err.product.not_found"))
    return {"ok": True, "soldout": int(s.soldout), "stock": s.stock, "stock_warn": warn}


# -- Handy mit der Rolle «Festleitung» ------------------------------------------------------------------------
class SoldoutIn(BaseModel):
    soldout: bool


@router.post("/api/phone/products/{pid}/soldout", dependencies=[Depends(devices.phone_only)])
async def phone_soldout(pid: int, s: SoldoutIn, request: Request):
    """Ausverkauft markieren (oder wieder freigeben) am Handy – nur mit der Rolle «Festleitung» (Verwaltung › Geräte).
    Wieder freigeben setzt eine Restmenge 0 auf «ohne Bestand» zurück, sonst bliebe die Kachel grau."""
    from .ledger import _app_name
    info = devices.client_cert(request)
    if not info or await db(lb.device_role, rt.store, info) != "lead":
        raise HTTPException(403, t("err.stock.no_right"))
    name = await db(rt.store.scalar, "SELECT name FROM products WHERE id=?", (pid,))
    if name is None:
        raise HTTPException(404, t("err.product.not_found"))
    sql = ("UPDATE products SET soldout=1 WHERE id=?" if s.soldout else
           "UPDATE products SET soldout=0, stock=CASE WHEN stock=0 THEN NULL ELSE stock END WHERE id=?")
    res = await db(rt.store.batch, [
        (sql, (pid,)),
        lb.audit_stmt(f"device:{lb.device_label(info['name'], _app_name(request))}", "product_soldout",
                      product=pid, name=name, soldout=s.soldout)])
    if not res[0].rows_affected:
        raise HTTPException(404, t("err.product.not_found"))
    return {"ok": True, **await db(availability, rt.store)}


class PhoneStockIn(BaseModel):
    stock: int | None = Field(default=None, ge=0, le=99999)      # None = ohne Restmenge (wieder frei verkaufen)


@router.post("/api/phone/products/{pid}/stock", dependencies=[Depends(devices.phone_only)])
async def phone_stock(pid: int, s: PhoneStockIn, request: Request):
    """Restmenge am Handy setzen («Ausverkauft melden» lange drücken) – nur mit der Rolle «Festleitung».
    0 = ausverkauft (Bestand 0), eine Zahl = noch so viele (die Kachel ist wieder frei), None = ohne Restmenge."""
    from .ledger import _app_name
    info = devices.client_cert(request)
    if not info or await db(lb.device_role, rt.store, info) != "lead":
        raise HTTPException(403, t("err.stock.no_right"))
    name = await db(rt.store.scalar, "SELECT name FROM products WHERE id=?", (pid,))
    if name is None:
        raise HTTPException(404, t("err.product.not_found"))
    res = await db(rt.store.batch, [
        ("UPDATE products SET soldout=?, stock=? WHERE id=?", (1 if s.stock == 0 else 0, s.stock, pid)),
        lb.audit_stmt(f"device:{lb.device_label(info['name'], _app_name(request))}", "product_stock",
                      product=pid, name=name, soldout=s.stock == 0, stock=s.stock)])
    if not res[0].rows_affected:
        raise HTTPException(404, t("err.product.not_found"))
    return {"ok": True, **await db(availability, rt.store)}
