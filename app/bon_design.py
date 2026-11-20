"""Bons & Quittung (Verwaltung › Einstellungen › Bons & Quittung, nur Admin) – der Bon-Baukasten (T24).

Quittung und Küchen-/Getränkebon bestehen aus Bausteinen in Druckreihenfolge (Version 2 der Einstellung «bon_design»):
    {"version": 2, "receipt": {"blocks": [...], "template": "standard"}, "ticket": {"blocks": [...], "template": ""}}
Ein Baustein: {"type": "table", "on": true, "size": "3", "invert": true, ...}. Welche Bausteine es gibt, welche
Eigenschaften sie haben und welche Werte erlaubt sind, steht in SCHEMA – die Verwaltung baut ihre Oberfläche daraus.

Regeln (app/receipts.py):
  * PFLICHT-Bausteine lassen sich verschieben und gestalten, aber nicht ausblenden oder entfernen; fehlende ergänzt
    der Server. KOPIE/TEST steht immer zuoberst, die Prüfnummer immer zuletzt (Quittung), der Signalton am Schluss.
  * Die Gestaltung wird beim Ausstellen in der Quittung festgehalten (receipts.data.design): eine KOPIE sieht aus wie
    das Original. Ältere Quittungen (alte Form von design oder ganz ohne) drucken wie bisher.
  * Kopf und Fusszeile der Quittung stehen weiterhin in den Einstellungen receipt_header/receipt_footer (so landen sie
    beim Ausstellen in der Quittung); der Baukasten zeigt und speichert sie bei den Bausteinen «Kopf» und «Fusszeile».
  * Die alte Form (Version 1: feste Schalter) wird beim Lesen in Bausteine übersetzt – die Bons sehen gleich aus.
Gedruckt werden die Bausteine in app/print_service.py, die Vorschau ist ein Bild aus denselben Druckerdaten
(app/bon_preview.py).
"""
from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import re

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from .core import admin, db, rt
from .i18n import t
from .store import Store

admin_router = APIRouter(prefix="/api/admin/bon-design", dependencies=[Depends(admin)])

KEY = "bon_design"
LOGO_KEY = "bon_logo_image"
LOGO_BOX = (576, 300)                    # 80 mm = 576 Punkte; höher braucht ein Bon kein Logo
IMAGE_BOX = (576, 600)                   # eigenes Bild (Baustein «Bild»)
MAX_UPLOAD = 8 * 1024 * 1024
MAX_BLOCKS = 60
SPACING = {"compact": (34, 6), "normal": (40, 14), "wide": (48, 22)}     # Zeilenabstand, Luft nach jeder Position (Punkte)
KINDS = ("receipt", "ticket")
IMAGE_ID = re.compile(r"^[0-9a-f]{8,64}$")
_CTRL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")

# Alte Form (Version 1): feste Schalter
RECEIPT_V1 = {"logo": False, "head_big": True, "show_table": True, "show_order": True, "show_device": True,
              "qr": "", "qr_text": ""}
TICKET_V1 = {"logo": False, "table_size": "big", "categories": True, "time_device": True, "spacing": "normal",
             "beep": False}


# -- Bausteine ------------------------------------------------------------------------------------------
# Grössen: s = klein (Schrift B), 1 = normal, h = doppelt hoch, 2 = gross (doppelt), 3 = sehr gross, 4 = riesig
SIZE_KEYS = {"s": "admin.bon.o.size_s", "1": "admin.bon.o.size_1", "h": "admin.bon.o.size_h",
             "2": "admin.bon.o.size_2", "3": "admin.bon.o.size_3", "4": "admin.bon.o.size_4"}
ALIGN = [["left", "admin.bon.o.left"], ["center", "admin.bon.o.center"], ["right", "admin.bon.o.right"]]
WIDTH = [["13", "admin.bon.o.w13"], ["12", "admin.bon.o.w12"], ["23", "admin.bon.o.w23"]]


def _sizes(*vals: str) -> list:
    return [[v, SIZE_KEYS[v]] for v in vals]


def _seg(k: str, label: str, default, opts: list) -> dict:
    return {"k": k, "t": "seg", "l": label, "d": default, "o": opts}


def _flag(k: str, label: str, default: bool) -> dict:
    return {"k": k, "t": "bool", "l": label, "d": default}


def _text(k: str, label: str, n: int, lines: int = 1, placeholder: str | None = None) -> dict:
    return {"k": k, "t": "area" if lines > 1 else "text", "l": label, "d": "", "n": n, "lines": lines, "ph": placeholder}


BEFORE = _seg("before", "admin.bon.p.before", 0, [[0, "admin.bon.o.before_0"], [1, "admin.bon.o.before_1"],
                                                  [2, "admin.bon.o.before_2"]])
P_ALIGN = _seg("align", "admin.bon.p.align", "center", ALIGN)
P_ALIGN_LEFT = _seg("align", "admin.bon.p.align", "left", ALIGN)
P_WIDTH = _seg("width", "admin.bon.p.width", "23", WIDTH)
P_LINE = _flag("line", "admin.bon.p.line", True)


def _block(label: str, hint: str, props: list, *, req: bool = False, fixed: str | None = None, add: bool = False,
           most: int = 1, cond: str | None = None, on: bool = True) -> dict:
    return {"l": label, "h": hint, "props": props, "req": req, "fixed": fixed, "add": add, "max": most, "cond": cond,
            "on": on}


FREE = {
    "text": _block("admin.bon.blk.text", "admin.bon.blk.text_hint", [
        _text("text", "admin.bon.p.text", 300, 6), _seg("size", "admin.bon.p.size", "1", _sizes("s", "1", "h", "2", "3")),
        P_ALIGN, _flag("bold", "admin.bon.p.bold", False), _flag("ul", "admin.bon.p.ul", False),
        _flag("invert", "admin.bon.p.invert", False), BEFORE], add=True, most=10),
    "rule": _block("admin.bon.blk.rule", "admin.bon.blk.rule_hint", [
        _seg("char", "admin.bon.p.char", "-", [["-", None, "- - -"], ["=", None, "= = ="], ["*", None, "* * *"],
                                               [".", None, ". . ."]]), BEFORE], add=True, most=10),
    "space": _block("admin.bon.blk.space", "admin.bon.blk.space_hint", [
        _seg("lines", "admin.bon.p.lines", "1", [["h", "admin.bon.o.half"], ["1", "admin.bon.o.one"],
                                                  ["2", "admin.bon.o.two"]])], add=True, most=10),
    "image": _block("admin.bon.blk.image", "admin.bon.blk.image_hint", [
        {"k": "image", "t": "image", "l": "admin.bon.p.image", "d": None}, _seg("width", "admin.bon.p.width", "12", WIDTH),
        P_ALIGN, BEFORE], add=True, most=3),
    "qr": _block("admin.bon.blk.qr", "admin.bon.blk.qr_hint", [
        _text("data", "admin.bon.p.qr_data", 300, placeholder="admin.bon.qr_placeholder"),
        _text("caption", "admin.bon.p.caption", 60),
        _seg("qsize", "admin.bon.p.size", 6, [[4, "admin.bon.o.qr_s"], [6, "admin.bon.o.qr_m"], [8, "admin.bon.o.qr_l"]]),
        P_ALIGN, {**BEFORE, "d": 2}], add=True, most=3),
}

SCHEMA = {
    "receipt": {
        "top": _block("admin.bon.blk.r_top", "admin.bon.blk.r_top_hint", [], req=True, fixed="top"),
        "logo": _block("admin.bon.blk.logo", "admin.bon.blk.logo_hint", [P_ALIGN, P_WIDTH], on=False),
        "header": _block("admin.bon.blk.header", "admin.bon.blk.header_hint", [
            _text("text", "admin.bon.p.header_text", 400, 6, placeholder="admin.receipt.header_placeholder"),
            _seg("size", "admin.bon.p.first_size", "h", _sizes("1", "h", "2")), P_ALIGN, BEFORE], req=True),
        "vat_no": _block("admin.bon.blk.vat_no", "admin.bon.blk.vat_no_hint", [P_ALIGN, BEFORE], req=True,
                         cond="admin.bon.c.vat"),
        "title": _block("admin.bon.blk.title", "admin.bon.blk.title_hint", [
            _seg("size", "admin.bon.p.size", "h", _sizes("1", "h", "2")), _flag("bold", "admin.bon.p.bold", True), BEFORE],
            req=True),
        "info": _block("admin.bon.blk.info", "admin.bon.blk.info_hint", [
            _flag("table", "admin.bon.p.show_table", True), _flag("order", "admin.bon.p.show_order", True),
            _flag("device", "admin.bon.p.show_device", True), BEFORE], req=True),
        "guest": _block("admin.bon.blk.guest", "admin.bon.blk.guest_hint", [BEFORE], req=True, cond="admin.bon.c.guest"),
        "items": _block("admin.bon.blk.r_items", "admin.bon.blk.r_items_hint", [
            _flag("each", "admin.bon.p.each", True), BEFORE], req=True),
        "total": _block("admin.bon.blk.total", "admin.bon.blk.total_hint", [
            _seg("size", "admin.bon.p.size", "h", _sizes("1", "h", "2")), BEFORE], req=True),
        "vat": _block("admin.bon.blk.vat", "admin.bon.blk.vat_hint", [BEFORE], req=True, cond="admin.bon.c.vat"),
        "footer": _block("admin.bon.blk.footer", "admin.bon.blk.footer_hint", [
            _text("text", "admin.bon.p.footer_text", 200, 3, placeholder="admin.receipt.footer_placeholder"),
            _seg("size", "admin.bon.p.size", "1", _sizes("s", "1", "h", "2")), P_ALIGN, P_LINE, BEFORE]),
        "check": _block("admin.bon.blk.check", "admin.bon.blk.check_hint", [], req=True, fixed="bottom"),
        **FREE,
    },
    "ticket": {
        "top": _block("admin.bon.blk.t_top", "admin.bon.blk.t_top_hint", [], req=True, fixed="top"),
        "logo": _block("admin.bon.blk.logo", "admin.bon.blk.logo_hint", [P_ALIGN, P_WIDTH], on=False),
        "head": _block("admin.bon.blk.head", "admin.bon.blk.head_hint", [BEFORE]),
        "table": _block("admin.bon.blk.table", "admin.bon.blk.table_hint", [
            _seg("size", "admin.bon.p.size", "2", _sizes("2", "3", "4")), _flag("invert", "admin.bon.p.invert", False),
            _flag("word", "admin.bon.p.word", True), _flag("part", "admin.bon.p.part", True), P_ALIGN_LEFT, BEFORE],
            req=True),
        "delivery": _block("admin.bon.blk.delivery", "admin.bon.blk.delivery_hint", [
            # welche Lieferzeitpunkte gedruckt werden: None = Standard «alle ausser Sofort», "*" = alle
            {"k": "when", "t": "times", "l": "admin.bon.p.when", "d": None},
            _seg("size", "admin.bon.p.size", "2", _sizes("1", "2", "3")), _flag("invert", "admin.bon.p.invert", True),
            _flag("bold", "admin.bon.p.bold", True), P_ALIGN_LEFT, BEFORE], req=True),
        "time": _block("admin.bon.blk.time", "admin.bon.blk.time_hint", [BEFORE]),
        "items": _block("admin.bon.blk.t_items", "admin.bon.blk.t_items_hint", [
            _seg("size", "admin.bon.p.size", "h", _sizes("1", "h", "2")), _flag("xbold", "admin.bon.p.xbold", True),
            _seg("spacing", "admin.bon.spacing", "normal", [["compact", "admin.bon.sp_compact"],
                                                           ["normal", "admin.bon.sp_normal"], ["wide", "admin.bon.sp_wide"]]),
            _flag("cats", "admin.bon.categories", True), _flag("cat_bold", "admin.bon.p.cat_bold", False),
            _flag("cat_ul", "admin.bon.p.cat_ul", True),
            # Preise auf allen Küchen-/Getränkebons (bisher pro Drucker); None = wie der Drucker (alt)
            {"k": "prices", "t": "bool", "l": "admin.bon.p.prices", "d": None}, BEFORE], req=True),
        "sum": _block("admin.bon.blk.sum", "admin.bon.blk.sum_hint", [P_LINE, BEFORE], req=True),
        "beep": _block("admin.bon.blk.beep", "admin.bon.blk.beep_hint", [], fixed="bottom", cond="admin.bon.c.beep",
                       on=False),
        **FREE,
        "datetime": _block("admin.bon.blk.datetime", "admin.bon.blk.datetime_hint", [
            _seg("size", "admin.bon.p.size", "1", _sizes("1", "h", "2")), P_ALIGN_LEFT, BEFORE], add=True, most=2),
    },
}
STORED_TEXT = {"header": "receipt_header", "footer": "receipt_footer"}     # Text steht in den Einstellungen


def new_block(kind: str, typ: str, **values) -> dict:
    s = SCHEMA[kind][typ]
    return {"type": typ, "on": s["on"], **{p["k"]: copy.deepcopy(p["d"]) for p in s["props"]
                                           if p["k"] not in ("text",) or typ not in STORED_TEXT}, **values}


def _list(kind: str, spec: list) -> list[dict]:
    return [new_block(kind, typ, **vals) for typ, vals in spec]


DEFAULT = {
    "receipt": [("top", {}), ("logo", {}), ("header", {}), ("vat_no", {}), ("rule", {}), ("title", {}), ("info", {}),
                ("guest", {}), ("rule", {}), ("items", {}), ("rule", {}), ("total", {}), ("vat", {}), ("footer", {}),
                ("check", {})],
    "ticket": [("top", {}), ("logo", {}), ("head", {}), ("table", {}), ("delivery", {}), ("time", {}), ("rule", {}),
               ("items", {}), ("sum", {}), ("beep", {})],
}

TEMPLATES = {          # Vorlagen: Name -> (Bezeichnung, Bausteine)
    "receipt": {
        "standard": ("admin.bon.tpl.r_standard", DEFAULT["receipt"]),
        "logo": ("admin.bon.tpl.r_logo", [("top", {}), ("logo", {"on": True}), ("header", {"size": "2"}),
                                          ("vat_no", {}), ("rule", {"char": "="}), ("title", {}), ("info", {}),
                                          ("guest", {}), ("rule", {}), ("items", {}), ("rule", {}), ("total", {}),
                                          ("vat", {}), ("footer", {"size": "h", "line": False, "before": 2}),
                                          ("check", {})]),
        "compact": ("admin.bon.tpl.r_compact", [("top", {}), ("logo", {}), ("header", {"size": "1"}), ("vat_no", {}),
                                                ("rule", {}), ("title", {"size": "1"}),
                                                ("info", {"device": False}), ("guest", {}), ("rule", {}), ("items", {}),
                                                ("rule", {}), ("total", {}), ("vat", {}),
                                                ("footer", {"size": "s"}), ("check", {})]),
    },
    "ticket": {
        "standard": ("admin.bon.tpl.t_standard", DEFAULT["ticket"]),
        "big": ("admin.bon.tpl.t_big", [("top", {}), ("logo", {}), ("table", {"size": "3", "invert": True, "align": "center"}),
                                        ("head", {}), ("delivery", {"align": "center"}), ("rule", {"char": "="}),
                                        ("items", {"size": "2", "spacing": "wide"}), ("time", {"before": 1}),
                                        ("sum", {}), ("beep", {})]),
        "compact": ("admin.bon.tpl.t_compact", [("top", {}), ("logo", {}), ("head", {"on": False}), ("table", {}),
                                                ("delivery", {"size": "1"}), ("time", {}), ("rule", {}),
                                                ("items", {"spacing": "compact", "size": "1"}), ("sum", {"line": False}),
                                                ("beep", {})]),
    },
}


def template_blocks(kind: str, name: str) -> list[dict]:
    return _list(kind, TEMPLATES[kind][name][1])


# -- Prüfen -----------------------------------------------------------------------------------------------
class DesignError(ValueError):
    pass


def _value(p: dict, v, strict: bool):
    if v is None:
        return copy.deepcopy(p["d"])
    kind = p["t"]
    if kind == "seg":
        for opt in p["o"]:
            if isinstance(v, (int, str)) and not isinstance(v, bool) and str(v) == str(opt[0]):
                return opt[0]
    elif kind == "bool":
        if isinstance(v, bool):
            return v
    elif kind == "times":                            # Lieferzeitpunkte: Liste der Namen (höchstens 20)
        if isinstance(v, list) and len(v) <= 20 and all(isinstance(x, str) and 0 < len(x) <= 20 for x in v):
            return [_CTRL.sub("", x) for x in v]
    elif kind in ("text", "area"):
        if isinstance(v, str):
            v = v.replace("\r\n", "\n").replace("\r", "\n")
            if kind == "text":
                v = v.replace("\n", " ")
            v = _CTRL.sub("", v)
            if len(v) <= p["n"] or not strict:
                lines = v.split("\n")[:p["lines"]]
                return "\n".join(lines)[:p["n"]]
    elif kind == "image":
        if isinstance(v, str) and IMAGE_ID.match(v):
            return v
    if strict:
        raise DesignError(p["k"])
    return copy.deepcopy(p["d"])


def clean(kind: str, blocks: list, *, strict: bool = True) -> list[dict]:
    """Bausteine prüfen und vervollständigen. strict: Fehler melden (Verwaltung), sonst still korrigieren (gespeicherte
    Einstellung lesen). Fehlende Bausteine ergänzt, feste Stellen erzwungen (KOPIE/TEST zuoberst, Prüfnummer zuletzt)."""
    spec = SCHEMA[kind]
    if not isinstance(blocks, list) or (strict and len(blocks) > MAX_BLOCKS):
        if strict:
            raise DesignError("blocks")
        blocks = []
    out: list[dict] = []
    seen: set[str] = set()
    count: dict[str, int] = {}
    for raw in blocks[:MAX_BLOCKS]:
        typ = raw.get("type") if isinstance(raw, dict) else None
        s = spec.get(typ) if isinstance(typ, str) else None
        if s is None:
            if strict:
                raise DesignError(str(typ))
            continue
        count[typ] = count.get(typ, 0) + 1
        if count[typ] > s["max"]:
            if strict:
                raise DesignError(typ)
            continue
        seen.add(typ)
        on = raw.get("on", True)
        if not isinstance(on, bool):
            if strict:
                raise DesignError("on")
            on = True
        if s["req"] and not on:                      # PFLICHT: nicht ausblenden
            if strict:
                raise DesignError(typ)
            on = True
        blk = {"type": typ, "on": on}
        for p in s["props"]:
            blk[p["k"]] = _value(p, raw.get(p["k"]), strict)
        if typ == "delivery" and raw.get("when") is None and raw.get("show") == "always":
            blk["when"] = ["*"]                      # alte Einstellung «immer» (bis 2026.10.10-71)
        out.append(blk)
    defaults = DEFAULT[kind]
    for n, (typ, _vals) in enumerate(defaults):     # fehlende Bausteine ergänzen: nach dem Vorgänger aus der Vorlage
        if typ in seen or spec[typ]["add"]:
            continue
        blk = new_block(kind, typ)
        if spec[typ]["req"]:
            blk["on"] = True
        else:
            blk["on"] = False
        if "text" in {p["k"] for p in spec[typ]["props"]}:
            blk.setdefault("text", "")
        prev = [d[0] for d in defaults[:n] if not spec[d[0]]["add"]]
        at = 0
        for i, b in enumerate(out):
            if b["type"] in prev:
                at = i + 1
        out.insert(at, blk)
        seen.add(typ)
    order = [d[0] for d in defaults]
    tops = [b for b in out if spec[b["type"]]["fixed"] == "top"]
    bottoms = sorted((b for b in out if spec[b["type"]]["fixed"] == "bottom"), key=lambda b: order.index(b["type"]))
    return tops + [b for b in out if not spec[b["type"]]["fixed"]] + bottoms


# -- Alte Form (Version 1) übersetzen ----------------------------------------------------------------------
def receipt_from_v1(v: dict | None, *, legacy: bool = False) -> list[dict]:
    """Schalter der Version 1 -> Bausteine, die genau dieselbe Quittung drucken. legacy: Gestaltung einer älteren
    Quittung – das Logo stand damals über KOPIE/TEST, eine Kopie bleibt so."""
    v = {**RECEIPT_V1, **(v or {})}
    logo = v.get("logo")
    blocks = _list("receipt", [
        ("top", {}), ("logo", {"on": bool(logo), "image": logo if isinstance(logo, str) else None}),
        ("header", {"size": "h" if v.get("head_big", True) else "1"}), ("vat_no", {}), ("rule", {}), ("title", {}),
        ("info", {"table": bool(v.get("show_table", True)), "order": bool(v.get("show_order", True)),
                  "device": bool(v.get("show_device", True))}),
        ("guest", {}), ("rule", {}), ("items", {}), ("rule", {}), ("total", {}), ("vat", {}), ("footer", {})])
    if v.get("qr"):
        blocks.append(new_block("receipt", "qr", data=v["qr"], caption=v.get("qr_text") or "", qsize=6, before=2,
                                align="center"))
    blocks.append(new_block("receipt", "check"))
    if legacy:
        blocks[0], blocks[1] = blocks[1], blocks[0]
    return blocks


def ticket_from_v1(v: dict | None) -> list[dict]:
    v = {**TICKET_V1, **(v or {})}
    logo = v.get("logo")
    return _list("ticket", [
        ("top", {}), ("logo", {"on": bool(logo), "image": logo if isinstance(logo, str) else None}), ("head", {}),
        ("table", {"size": "3" if v.get("table_size") == "huge" else "2"}), ("delivery", {}),
        ("time", {"on": bool(v.get("time_device", True))}), ("rule", {}),
        ("items", {"cats": bool(v.get("categories", True)),
                   "spacing": v.get("spacing") if v.get("spacing") in SPACING else "normal"}),
        ("sum", {}), ("beep", {"on": bool(v.get("beep"))})])


def receipt_blocks(ds: dict | None) -> list[dict]:
    """Gestaltung einer Quittung (data.design) -> Bausteine; alte Formen wie damals."""
    if isinstance(ds, dict) and isinstance(ds.get("blocks"), list):
        return ds["blocks"]
    return receipt_from_v1(ds, legacy=True)


def ticket_blocks(design: dict | None) -> list[dict]:
    """Gestaltung der Küchenbons (Version 2 oder 1, None = Standard) -> Bausteine."""
    if isinstance(design, dict) and isinstance(design.get("blocks"), list):
        return design["blocks"]
    return ticket_from_v1(design)


# -- Einstellung lesen ------------------------------------------------------------------------------------
def _raw(store: Store) -> dict:
    try:
        raw = json.loads(store.setting(KEY, "") or "{}")
    except ValueError:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def load(store: Store) -> dict:
    """{"receipt": {"blocks": […], "template": …}, "ticket": {…}} – auch aus der alten Form."""
    raw = _raw(store)
    out = {}
    for kind in KINDS:
        part = raw.get(kind) if isinstance(raw.get(kind), dict) else {}
        if raw.get("version") == 2:
            blocks = clean(kind, part.get("blocks") or [], strict=False)
            tpl = part.get("template") if part.get("template") in TEMPLATES[kind] else ""
        else:
            v1 = {k: v for k, v in part.items() if k in (RECEIPT_V1 if kind == "receipt" else TICKET_V1)}
            blocks = clean(kind, receipt_from_v1(v1) if kind == "receipt" else ticket_from_v1(v1), strict=False)
            tpl = "standard" if blocks == clean(kind, template_blocks(kind, "standard"), strict=False) else ""
        if kind == "ticket":                         # Preise: bisher pro Drucker – einmal für alle übernehmen
            for b in blocks:
                if b["type"] == "items" and b.get("prices") is None:
                    b["prices"] = _printers_prices(store)
        out[kind] = {"blocks": blocks, "template": tpl}
    return out


def _printers_prices(store: Store) -> bool | None:
    """Übernahme: «Preise drucken» war pro Drucker – gilt jetzt für alle Küchen-/Getränkebons. Mit
    Preisen, wenn mindestens die Hälfte der Drucker im Einsatz sie hatte (so fällt bei «Getränke mit, Küche ohne»
    nichts weg). None = unbekannt (dann weiter wie der Drucker)."""
    try:
        rows = store.query("SELECT show_prices FROM printers WHERE enabled=1") or store.query("SELECT show_prices FROM printers")
    except (AttributeError, Exception):  # noqa: BLE001 – Testspeicher ohne Tabelle: wie bisher pro Drucker
        return None
    if not rows:
        return None
    on = sum(1 for r in rows if r["show_prices"])
    return on * 2 >= len(rows)


def logo_id(store: Store) -> str | None:
    """Logo für Bons: eigenes Bon-Logo, sonst das Logo der Verwaltung."""
    return store.setting(LOGO_KEY, "") or store.setting("logo_image", "") or None


def resolve(blocks: list[dict], logo: str | None) -> list[dict]:
    """Für den Druck: Logo-Baustein bekommt das Bild, das jetzt gilt; ausgeschaltete Bausteine fallen weg (ausser den
    festen – deren Inhalt hängt vom Bon ab); Texte aus den Einstellungen bleiben draussen."""
    out = []
    for b in blocks:
        b = {k: v for k, v in b.items() if not (k == "text" and b["type"] in STORED_TEXT)}
        if b["type"] == "logo":
            b["image"] = logo if b.get("on") else None
        out.append(b)
    return out


def receipt_snapshot(store: Store) -> dict:
    """Für receipts._snapshot: Gestaltung der Quittung zum Zeitpunkt des Ausstellens (mit dem Logo, das galt)."""
    return {"version": 2, "blocks": resolve(load(store)["receipt"]["blocks"], logo_id(store))}


def ticket(store: Store) -> dict:
    return {"version": 2, "blocks": resolve(load(store)["ticket"]["blocks"], logo_id(store))}


def image_ids(blocks: list[dict]) -> set[str]:
    return {b["image"] for b in blocks if b.get("on", True) and isinstance(b.get("image"), str)}


def logo_image(store: Store, image_id: str | None):
    """Bild als PIL-Bild – None, wenn keins da ist oder Pillow fehlt."""
    if not image_id:
        return None
    row = store.query_one("SELECT data FROM images WHERE id=?", (image_id,))
    if not row:
        return None
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(base64.b64decode(row["data"])))
        im.load()
    except Exception:  # noqa: BLE001
        return None
    return im


def load_images(store: Store, ids) -> dict:
    return {i: im for i in ids if (im := logo_image(store, i)) is not None}


def raster_logo(im, width_dots: int, frac: str = "23", max_h: int = 200):
    """Für den Thermodrucker: auf weiss (Transparenz), Graustufen, höchstens ⅓, ½ oder ⅔ der Papierbreite, gerastert."""
    from PIL import Image
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, "white")
        bg.alpha_composite(im)
        im = bg
    im = im.convert("L")
    maxw = {"13": width_dots // 3, "12": width_dots // 2}.get(frac, width_dots * 2 // 3)
    im.thumbnail((max(64, maxw), max_h))
    return im.convert("1").convert("L")                          # Floyd-Steinberg: Graustufen werden zu Punkten


# -- Verwaltung -----------------------------------------------------------------------------------------
class ReceiptV1(BaseModel):
    logo: bool = False
    head_big: bool = True
    show_table: bool = True
    show_order: bool = True
    show_device: bool = True
    qr: str = Field(default="", max_length=300, pattern=r"^[^\x00-\x1f\x7f]*$")
    qr_text: str = Field(default="", max_length=60, pattern=r"^[^\x00-\x1f\x7f]*$")


class TicketV1(BaseModel):
    logo: bool = False
    table_size: str = Field(default="big", pattern="^(big|huge)$")
    categories: bool = True
    time_device: bool = True
    spacing: str = Field(default="normal", pattern="^(compact|normal|wide)$")
    beep: bool = False


class DesignIn(BaseModel):
    receipt: dict
    ticket: dict
    receipt_printer_id: int | None = Field(default=None, ge=0)


def _blocks_in(kind: str, part: dict) -> list[dict]:
    """Bausteine aus einer Anfrage (auch alte Form) – geprüft; Fehler -> 422."""
    try:
        if isinstance(part.get("blocks"), list):
            return clean(kind, part["blocks"])
        v1 = (ReceiptV1 if kind == "receipt" else TicketV1).model_validate(part).model_dump()
    except DesignError as e:
        raise HTTPException(422, t("err.bon.invalid", what=str(e))) from None
    except ValueError:
        raise HTTPException(422, t("err.bon.invalid", what=kind)) from None
    blocks = receipt_from_v1(v1) if kind == "receipt" else ticket_from_v1(v1)
    return clean(kind, blocks, strict=False)


def _tidy(text: str) -> str:
    return "\n".join(" ".join(x.split()) for x in text.splitlines() if x.strip())


def _view(store: Store) -> dict:
    from . import ledger_base as lb
    from .orders import delivery_times
    d = load(store)
    for b in d["receipt"]["blocks"]:                 # Kopf und Fusszeile: Text aus den Einstellungen
        if b["type"] in STORED_TEXT:
            b["text"] = store.setting(STORED_TEXT[b["type"]], "") or ""
    own = store.setting(LOGO_KEY, "") or None
    app_logo = store.setting("logo_image", "") or None
    try:
        rp = int(store.setting("receipt_printer_id", "0") or 0)
    except ValueError:
        rp = 0
    return {"version": 2, **d,
            "schema": SCHEMA,
            "templates": {k: {n: {"l": v[0], "blocks": template_blocks(k, n)} for n, v in TEMPLATES[k].items()}
                          for k in KINDS},
            "logo": f"/media/{own}" if own else None, "app_logo": f"/media/{app_logo}" if app_logo else None,
            "receipt_printer_id": rp,
            "printers": [{"id": p["id"], "name": p["name"], "enabled": bool(p["enabled"]), "width": p["chars_per_line"]}
                         for p in store.query("SELECT id, name, enabled, chars_per_line FROM printers ORDER BY name")],
            "delivery_times": delivery_times(store),
            "table_word": store.setting("table_word", "Tisch") or "Tisch",
            "vat": lb.vat_enabled(store)}


@admin_router.get("")
async def get_design():
    return await db(_view, rt.store)


@admin_router.put("")
async def put_design(d: DesignIn, request: Request):
    from . import ledger, ledger_base as lb
    from .catalog import DROP_UNUSED_IMAGES
    rec, tic = _blocks_in("receipt", d.receipt), _blocks_in("ticket", d.ticket)
    stmts = []
    sent = {rb.get("type") for rb in d.receipt.get("blocks") or [] if isinstance(rb, dict) and isinstance(rb.get("text"), str)}
    for b in rec:                                    # Kopf und Fusszeile gehören in die Einstellungen (receipts.py)
        if b["type"] in STORED_TEXT:
            text = b.pop("text", None)
            if text is not None and b["type"] in sent:
                stmts.append(("INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET "
                              "value=excluded.value", (STORED_TEXT[b["type"]], _tidy(text))))
    if d.receipt_printer_id is not None:
        if d.receipt_printer_id and not await db(rt.store.scalar, "SELECT id FROM printers WHERE id=?",
                                                 (d.receipt_printer_id,)):
            raise HTTPException(422, t("err.receipt.printer_unknown"))
        stmts.append(("INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      ("receipt_printer_id", str(d.receipt_printer_id))))

    def tpl(kind: str, part: dict) -> str:
        name = part.get("template")
        return name if isinstance(name, str) and name in TEMPLATES[kind] else ""
    value = {"version": 2, "receipt": {"blocks": rec, "template": tpl("receipt", d.receipt)},
             "ticket": {"blocks": tic, "template": tpl("ticket", d.ticket)}}
    text = json.dumps(value, ensure_ascii=False)
    stmts += [("INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (KEY, text)),
              lb.audit_stmt(ledger.actor_of(request), "bon_design", receipt=[b["type"] for b in rec if b["on"]],
                            ticket=[b["type"] for b in tic if b["on"]]),
              DROP_UNUSED_IMAGES]
    await db(rt.store.batch, stmts)
    return await db(_view, rt.store)


async def _upload(file: UploadFile, box: tuple[int, int]) -> tuple[str, list]:
    from .catalog import process_image
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, t("err.file.too_large"))
    iid, mime, b64 = await asyncio.to_thread(process_image, raw, box)
    return iid, [("INSERT OR IGNORE INTO images(id,mime,data) VALUES (?,?,?)", (iid, mime, b64))]


@admin_router.post("/logo")
async def upload_logo(file: UploadFile = File(...)):
    from .catalog import DROP_UNUSED_IMAGES
    iid, stmts = await _upload(file, LOGO_BOX)
    await db(rt.store.batch, [*stmts, ("INSERT INTO settings(key,value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                                       "value=excluded.value", (LOGO_KEY, iid)), DROP_UNUSED_IMAGES])
    return await db(_view, rt.store)


@admin_router.delete("/logo")
async def delete_logo():
    from .catalog import DROP_UNUSED_IMAGES
    await db(rt.store.batch, [("DELETE FROM settings WHERE key=?", (LOGO_KEY,)), DROP_UNUSED_IMAGES])
    return await db(_view, rt.store)


@admin_router.post("/image")
async def upload_image(file: UploadFile = File(...)):
    """Bild für den Baustein «Bild». Bleibt erst mit dem Speichern des Baukastens (sonst räumt das nächste Speichern
    es wieder weg)."""
    iid, stmts = await _upload(file, IMAGE_BOX)
    await db(rt.store.batch, stmts)
    return {"id": iid, "url": f"/media/{iid}"}


# -- Vorschau und Probedruck ------------------------------------------------------------------------------
class PreviewIn(BaseModel):
    kind: str = Field(default="receipt", pattern="^(receipt|ticket)$")
    design: dict | None = None                                   # {"blocks": […]} (oder alte Form {receipt, ticket})
    width: int = Field(default=48, ge=24, le=64)                 # Zeichen pro Zeile (80 mm = 48, 58 mm = 32)
    delivery: str | None = Field(default=None, max_length=20, pattern=r"^[^\x00-\x1f\x7f]*$")   # None = Sofort


class SampleIn(PreviewIn):
    printer_id: int


def _preview_blocks(store: Store, p: PreviewIn) -> tuple[list[dict], dict]:
    """Bausteine für die Vorschau (noch nicht gespeichert) und Texte von Kopf/Fusszeile."""
    d = p.design
    part = None
    if isinstance(d, dict):
        part = d if isinstance(d.get("blocks"), list) else d.get(p.kind) if isinstance(d.get(p.kind), dict) else None
    if part is None:
        blocks = load(store)[p.kind]["blocks"]
    elif isinstance(part.get("blocks"), list):
        try:
            blocks = clean(p.kind, part["blocks"], strict=False)
        except DesignError:
            blocks = load(store)[p.kind]["blocks"]
    else:
        blocks = _blocks_in(p.kind, part)
    texts = {}
    for b in blocks:
        if b["type"] in STORED_TEXT and p.kind == "receipt":
            raw = b.get("text")
            texts[b["type"]] = raw if isinstance(raw, str) else store.setting(STORED_TEXT[b["type"]], "") or ""
    return blocks, texts


def sample(store: Store, p: PreviewIn, dev, lang: str, marks: list | None = None) -> bytes:
    """Beispiel-Quittung bzw. -Bon mit diesen Bausteinen, so wie der Drucker ihn druckt."""
    from . import ledger_base as lb
    from . import print_service as ps
    from .orders import delivery_times
    from .receipts import MAX_FOOTER_LINES, MAX_HEADER_LINES, _lines_of
    blocks, texts = _preview_blocks(store, p)
    blocks = resolve(blocks, logo_id(store))
    images = load_images(store, image_ids(blocks))
    if p.kind == "receipt":
        vat = lb.vat_enabled(store)
        head = _lines_of(texts.get("header", ""), MAX_HEADER_LINES) or [store.setting("event_name", "") or "Verein"]
        data = {"header": head, "footer": _lines_of(texts.get("footer", ""), MAX_FOOTER_LINES), "currency": "CHF",
                "table": "12", "table_word": store.setting("table_word", "Tisch"), "order_no": 42,
                "device": "XCover 1 (Anna)", "payment": "cash", "vat": vat,
                "vat_number": (store.setting("vat_number", "") or "CHE-123.456.789") if vat else None,
                "design": {"version": 2, "blocks": blocks}}
        lines = [{"line_no": 1, "name": "Bier 5dl", "qty": 2, "price": 650, "amount": 1300},
                 {"line_no": 2, "name": "Bratwurst", "options": "mit Senf", "qty": 1, "price": 800, "amount": 800}]
        if vat:
            rates = lb.vat_rates_on(store)
            lines[0].update(vat_code="normal", vat_bp=rates.get("normal", 810))
            lines[1].update(vat_code="normal", vat_bp=rates.get("normal", 810))
        rec = {"id": 7, "no": 123, "order_id": 42, "created_at": "2026-11-02T19:30:00", "total_cents": 2100,
               "data": json.dumps(data), "lines": json.dumps(lines), "digest": "h2:1a2b3c4d5e6f", "test": 0}
        return ps.render_receipt(dev, rec, lang=lang, images=images, marks=marks)
    items = [{"qty": 2, "name": "Bier 5dl", "price_cents": 650, "category": "Getränke"},
             {"qty": 1, "name": "Mineral", "price_cents": 400, "category": "Getränke", "note": "ohne Eis"},
             {"qty": 1, "name": "Bratwurst", "price_cents": 800, "category": "Essen", "options": "mit Senf"}]
    times = delivery_times(store)
    when = p.delivery if p.delivery and p.delivery != times[0] else None
    return ps.render_table_bon(dev, dev, event_name=store.setting("event_name", "") or "Vereinsfest", currency="CHF",
                               table="12", order_id=42, created_at="2026-11-02T19:30:00", items=items,
                               device_name="XCover 1 (Anna)", table_word=store.setting("table_word", "Tisch"),
                               delivery=when, now_label=times[0], lang=lang,
                               design={"version": 2, "blocks": blocks}, images=images, marks=marks)


def preview_png(store: Store, p: PreviewIn) -> tuple[bytes, list]:
    """Vorschau als PNG und wo jeder Baustein steht: [[Nummer, y oben, y unten], …] in Bildpunkten."""
    from . import bon_preview
    from . import print_service as ps
    from .i18n import default_lang
    lang = default_lang()
    dev = ps.PrinterCfg(id=0, name=t("admin.bon.sample_printer", lang), host="", port=9100, chars_per_line=p.width,
                        show_prices=True, enabled=True, language="")
    marks: list = []
    raw = sample(store, p, dev, lang, marks)
    ys: dict = {}
    png = bon_preview.render_png(raw, p.width * 12, ys=ys, offsets=[o for m in marks for o in m[1:]])
    out = [[i, ys.get(a, 0), ys.get(b, 0)] for i, a, b in marks if ys.get(b, 0) > ys.get(a, 0)]
    return png, out


@admin_router.post("/preview")
async def preview(p: PreviewIn):
    png, where = await db(preview_png, rt.store, p)
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store",
                                                          "X-Blocks": json.dumps(where, separators=(",", ":"))})


@admin_router.post("/test")
async def print_sample(p: SampleIn, request: Request):
    """Probe drucken: Beispiel mit diesen Bausteinen direkt auf einen Drucker, oben «MUSTER» – keine Quittung, keine
    Nummer, nichts in der Warteschlange."""
    from . import ledger, ledger_base as lb
    from . import print_service as ps
    from .escpos import PrinterAddr, PrinterError, send_raw
    dev = (await db(ps.load_printers, rt.store)).get(p.printer_id)
    if not dev:
        raise HTTPException(404, t("err.printer.not_found"))
    payload = ps.render_sample_banner(dev) + await db(sample, rt.store, p, dev, dev.lang)
    try:
        await send_raw(PrinterAddr(dev.host, dev.port), payload)
    except PrinterError as e:
        raise HTTPException(502, t("err.bon.sample_failed", printer=dev.name, error=e)) from None
    await db(lb.audit, rt.store, ledger.actor_of(request), "bon_sample", printer=dev.name, kind=p.kind)
    return {"printed": True, "printer": dev.name}
