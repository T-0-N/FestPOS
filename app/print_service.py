"""Bon-Layout, Einzelbon-Logik und der cluster-sichere Druck-Spooler.

Sicherheitsprinzip "höchstens einmal drucken":
  1. Nur der Raft-Leader startet Druck-Worker.
  2. Vor jedem Versand wird der Job per Compare-and-Set reserviert:
        UPDATE ... SET status='printing' WHERE id=? AND status='pending'
     Das ist ein Raft-Commit – ein Knoten ohne Mehrheit kann also gar nicht drucken,
     und zwei Knoten können nie denselben Job reservieren.
  3. Jobs, die beim Leaderwechsel auf 'printing' stehen, werden 'uncertain'
     (evtl. gedruckt) und NICHT automatisch wiederholt – Admin entscheidet.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
import time
from datetime import datetime
from pathlib import Path

from . import ledger_base as lb
from .escpos import Bon, PrinterAddr, PrinterError, send_raw
from .i18n import default_lang, t
from .store import Store, StoreError, now_iso

log = logging.getLogger("festpos.print")


# --------------------------------------------------------------------------
# Formatierung
# --------------------------------------------------------------------------

def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


@dataclass
class PrinterCfg:
    id: int
    name: str
    host: str
    port: int
    chars_per_line: int
    show_prices: bool
    enabled: bool
    language: str = ""                  # Sprache der Bons ("" = Standardsprache)

    @classmethod
    def from_row(cls, r: dict) -> "PrinterCfg":
        return cls(r["id"], r["name"], r["host"], r["port"], r["chars_per_line"],
                   bool(r["show_prices"]), bool(r["enabled"]), r.get("language") or "")

    @property
    def lang(self) -> str:
        return self.language or default_lang()


def table_word_of(setting: str, lang: str | None) -> str:
    """Einstellung «Tisch»/«Platz» (Bezeichnung) in der Sprache des Bons."""
    return t("bon.word.seat" if setting == "Platz" else "bon.word.table", lang)


def _hm(created_at: str) -> str:
    try:
        return datetime.fromisoformat(created_at).strftime("%H:%M")
    except (TypeError, ValueError):
        return str(created_at)


# --------------------------------------------------------------------------
# Bausteine (T24, app/bon_design.py): Quittung und Küchen-/Getränkebon bestehen aus Bausteinen in Druckreihenfolge.
# Jeder Baustein schreibt seinen Teil; «Abstand davor» kommt nur, wenn er etwas druckt. Die Standard-Bausteine
# (auch die aus der alten Form übersetzten) drucken Byte für Byte dasselbe wie vorher (tests/test_bon_blocks.py).
# --------------------------------------------------------------------------

SIZE = {"s": (1, 1), "1": (1, 1), "h": (1, 2), "2": (2, 2), "3": (3, 3), "4": (4, 4)}


class _Ctx:
    """Was die Bausteine eines Bons brauchen: Bon, Sprache, Bilder, Daten des Bons und die aktuelle Ausrichtung."""

    def __init__(self, b: Bon, lang: str | None, images: dict | None = None, logo=None, **kw):
        self.b, self.lang, self.images, self.logo = b, lang, images or {}, logo
        self.align = "left"
        self.__dict__.update(kw)

    def img(self, k: dict):
        im = self.images.get(k.get("image")) if k.get("image") else None
        return self.logo if im is None and k.get("type") == "logo" else im


def _al(c: _Ctx, where: str) -> None:
    """Ausrichtung nur schreiben, wenn sie ändert."""
    if c.align != where:
        c.b.align(where)
        c.align = where


def _run(c: _Ctx, blocks: list[dict], fns: dict, marks: list | None) -> None:
    """Bausteine der Reihe nach drucken. marks: (Nummer, Anfang, Ende) in den Druckerdaten (Vorschau)."""
    for i, k in enumerate(blocks):
        fn = fns.get(k.get("type"))
        if fn is None or not k.get("on", True):
            continue
        b = c.b
        pos0, align0 = b.pos, c.align
        before = k.get("before", 0)
        if before == 1:
            b.gap(12)
        elif before == 2:
            b.feed(1)
        start = b.pos
        fn(c, k)
        if b.pos == start:                                   # nichts gedruckt (z.B. MWST aus): auch kein Abstand
            b.truncate(pos0)
            c.align = align0
        elif marks is not None:
            marks.append((i, start, b.pos))


def _sized(b: Bon, size: str) -> int:
    """Grösse einschalten -> Zeichen pro Zeile (klein = Schrift B)."""
    w, h = SIZE.get(size, (1, 1))
    if size == "s":
        b.font(True)
    b.size(w, h)
    return (b.width * 12 // 9 if size == "s" else b.width) // w


def _unsized(b: Bon, size: str) -> None:
    b.size()
    if size == "s":
        b.font(False)


def _k_logo(c: _Ctx, k: dict) -> None:
    """Logo, gerastert (app/bon_design.py) – fehlt Pillow oder ist das Bild kaputt: ohne Logo."""
    im = c.img(k)
    if im is None:
        return
    try:
        from . import bon_design
        img = bon_design.raster_logo(im, c.b.width * 12, k.get("width", "23"))
    except Exception:  # noqa: BLE001
        return
    _al(c, k.get("align", "center"))
    c.b.raster(img)
    _al(c, "left")
    c.b.gap(12)


def _k_image(c: _Ctx, k: dict) -> None:
    im = c.img(k)
    if im is None:
        return
    try:
        from . import bon_design
        img = bon_design.raster_logo(im, c.b.width * 12, k.get("width", "12"), max_h=400)
    except Exception:  # noqa: BLE001
        return
    _al(c, k.get("align", "center"))
    c.b.raster(img)
    _al(c, "left")


def _k_text(c: _Ctx, k: dict) -> None:
    text = k.get("text") or ""
    if not text.strip():
        return
    b, size, inv = c.b, k.get("size", "1"), bool(k.get("invert"))
    _al(c, k.get("align", "center"))
    if inv:
        b.invert()
    if k.get("bold"):
        b.bold()
    if k.get("ul"):
        b.underline()
    cols = _sized(b, size)
    for para in text.split("\n"):
        for chunk in _wrap(para, cols - 2 if inv else cols) if para.strip() else [""]:
            b.line(f" {chunk} " if inv and chunk else chunk)
    _unsized(b, size)
    if k.get("ul"):
        b.underline(False)
    if k.get("bold"):
        b.bold(False)
    if inv:
        b.invert(False)
    _al(c, "left")


def _k_rule(c: _Ctx, k: dict) -> None:
    _al(c, "left")
    c.b.rule(k.get("char") or "-")


def _k_space(c: _Ctx, k: dict) -> None:
    c.b.gap({"h": 12, "1": 24, "2": 48}.get(k.get("lines"), 24))


def _k_qr(c: _Ctx, k: dict) -> None:
    if not (k.get("data") or "").strip():
        return
    b = c.b
    _al(c, k.get("align", "center"))
    b.qr(k["data"], module=int(k.get("qsize") or 6))
    if k.get("caption"):
        for chunk in _wrap(k["caption"], b.width):
            b.line(chunk)
    _al(c, "left")


def _k_datetime(c: _Ctx, k: dict) -> None:
    b, size = c.b, k.get("size", "1")
    _al(c, k.get("align", "left"))
    _sized(b, size)
    b.line(_dt(c.created_at))
    _unsized(b, size)
    _al(c, "left")


FREE_BLOCKS = {"logo": _k_logo, "image": _k_image, "text": _k_text, "rule": _k_rule, "space": _k_space, "qr": _k_qr}


# -- Küchen- und Getränkebon --------------------------------------------------------------------------------
def _t_top(c: _Ctx, k: dict) -> None:
    """Fest zuoberst: TEST (Übungsbestellung), NACHDRUCK, Umgeleitet."""
    b, w, lang = c.b, c.b.width, c.lang
    if c.test:                                               # Übungsbestellung: unübersehbar (Testmodus der App)
        _test_banner(b, lang)
    if c.reprint:                                            # Nachdruck: nicht mit dem Original verwechseln (REV-044)
        b.invert().bold().size(2, 1).line(f" {t('bon.head.reprint', lang)} ".center(w // 2)).size().bold(False).invert(False)
    if c.rerouted:                                           # automatisch/manuell umgeleitet
        b.invert().bold().size(1, 2)                         # doppelt hoch, weiss auf schwarz
        for chunk in _wrap(t("bon.head.rerouted", lang, printer=c.station.name), w - 2):
            b.line(f" {chunk} ".ljust(w))
        b.size().bold(False).invert(False)


def _t_head(c: _Ctx, k: dict) -> None:
    _al(c, "left")
    c.b.columns(c.event_name or "", c.station.name)


def _just(text: str, width: int, align: str) -> str:
    return {"center": text.center, "right": text.rjust}.get(align, text.ljust)(width)


def _t_table(c: _Ctx, k: dict) -> None:
    """TISCH 12 (gross, fett), daneben «1 von 3»; langer Tischname: umbrechen, «2 von 3» in eine eigene Zeile (REV-043)."""
    b, w, lang = c.b, c.b.width, c.lang
    _al(c, "left")
    word = table_word_of(c.table_word, lang).upper() if k.get("word", True) else ""
    big = f"{word} {c.table}" if word else str(c.table)
    nm = t("bon.head.part", lang, n=c.n, of=c.of) if c.n else ""     # «1 von 3» in der Sprache des Druckers (T21)
    z = int(k.get("size") or 2)
    half = w // z                            # breite Schrift: entsprechend weniger Zeichen pro Zeile
    inv, align = bool(k.get("invert")), k.get("align", "left")
    if not inv and align == "left" and k.get("part", True) and len(big) * z + (3 + len(nm) * 2 if nm else 0) <= w:
        b.size(z, z).bold().text(big).bold(False)
        if nm:                               # gross, nicht fett, mit kleinem Abstand
            b.size().text("   ").size(2, 2).text(nm)
        b.line().size()
        return
    b.size(z, z).bold()
    if inv:
        b.invert()
        for chunk in _wrap(big, half - 2):
            b.line(_just(f" {chunk} ", half, align))
        b.invert(False)
    else:
        _al(c, align)
        for chunk in _wrap(big, half):
            b.line(chunk)
        _al(c, "left")
    b.bold(False).size(2, 2)
    for chunk in _wrap(nm, w // 2) if nm else ():
        b.line(chunk.rjust(w // 2))
    b.size()


def _t_delivery(c: _Ctx, k: dict) -> None:
    """Lieferzeitpunkt (später liefern: unübersehbar). Gedruckt werden die gewählten Zeitpunkte:
    Standard alle ausser «Sofort», "*" = alle (auch «Sofort»); umbrechen, nie abschneiden."""
    when, sel = c.delivery, k.get("when")
    if sel is None and k.get("show") == "always":         # alte Form (bis 2026.10.10-71)
        sel = ["*"]
    if sel is None:                                        # Standard: nicht bei «Sofort»
        if not when:
            return
    else:
        label = when or c.now_label
        if not label or ("*" not in sel and label.casefold() not in {x.casefold() for x in sel}):
            return
        when = label
    b = c.b
    _al(c, "left")
    z = int(k.get("size") or 2)
    cols = b.width // z
    inv, bold = k.get("invert", True), k.get("bold", True)
    if inv:
        b.invert()
    if bold:
        b.bold()
    b.size(z, z)
    for chunk in _wrap(when.upper(), max(4, cols - 2)):
        b.line(_just(f" {chunk} ", cols, k.get("align", "left")))
    b.size()
    if bold:
        b.bold(False)
    if inv:
        b.invert(False)


def _t_time(c: _Ctx, k: dict) -> None:
    _al(c, "left")
    c.b.columns(_hm(c.created_at), c.device_name or "")


def _t_sum(c: _Ctx, k: dict) -> None:
    """Summe (falls der Drucker Preise zeigt), Anzahl Positionen/Stück und Bestellnummer; im Testmodus «nicht
    verrechnen»."""
    b, lang, station, items = c.b, c.lang, c.station, c.sum_items
    _al(c, "left")
    if k.get("line", True):
        b.rule("-")
    if getattr(c, "prices", station.show_prices):
        total = sum(i["price_cents"] * i["qty"] for i in items)
        b.size(1, 2).bold().columns(t("bon.foot.total", lang), f"{c.currency} {money(total)}").bold(False).size()
    pos, pcs = len(items), sum(i["qty"] for i in items)
    left = t("bon.foot.positions", lang, count=pos) + (" / " + t("bon.foot.pieces", lang, count=pcs) if pcs != pos else "")
    b.columns(left, f"#{c.order_id:04d}")
    if c.test:
        b.invert().bold().line(f" {t('bon.test.footer', lang)} "[:b.width].center(b.width)).bold(False).invert(False)


def _t_beep(c: _Ctx, k: dict) -> None:
    c.b.beep()


TICKET_BLOCKS = {**FREE_BLOCKS, "top": _t_top, "head": _t_head, "table": _t_table, "delivery": _t_delivery,
                 "time": _t_time, "sum": _t_sum, "beep": _t_beep, "datetime": _k_datetime}


def _ticket_setup(design: dict | None) -> tuple[list[dict], dict]:
    from . import bon_design
    blocks = bon_design.ticket_blocks(design)
    items = next((k for k in blocks if k.get("type") == "items"), {})
    return blocks, items


def _prices(ik: dict, station: PrinterCfg) -> bool:
    """Preise auf dem Küchen-/Getränkebon: Einstellung im Baustein «Positionen» (Bons & Quittung); fehlt sie (alte
    Gestaltung), wie bisher die Einstellung des Druckers."""
    p = ik.get("prices")
    return station.show_prices if p is None else bool(p)


def _test_banner(b: Bon, lang: str | None) -> None:
    """Weiss auf schwarz, doppelt gross: «TEST», darunter was das heisst (nicht zubereiten, nicht verrechnen)."""
    w = b.width
    b.invert().bold().size(2, 2)
    for chunk in _wrap(t("bon.test.banner", lang), w // 2 - 2):
        b.line(f" {chunk} ".center(w // 2))
    b.size()
    for chunk in _wrap(t("bon.test.note", lang), w - 2):          # schmale Drucker / längere Sprachen: umbrechen
        b.line(f" {chunk} ".center(w))
    b.bold(False).invert(False)


# Bons besser lesbar (Vorschlag B): etwas mehr Zeilenabstand, Luft nach jeder Position, Optionen fett.
# Gilt für Sammel-, Einzel- und Storno-Bons – nicht für Quittungen (receipts.py) und den Testdruck.
BON_LINE_DOTS = 40                   # statt 1/6 Zoll (~34 Punkte)
ITEM_GAP_DOTS = 14                   # nach jeder Position (~2 mm)
SPACING = {"compact": (34, 6), "normal": (BON_LINE_DOTS, ITEM_GAP_DOTS), "wide": (48, 22)}   # Baustein «Positionen»


def _bon(width: int, spacing: str | None = None) -> Bon:
    return Bon(width).line_spacing(SPACING.get(spacing or "normal", SPACING["normal"])[0])


def _gap(spacing: str | None) -> int:
    return SPACING.get(spacing or "normal", SPACING["normal"])[1]


def _item(b: Bon, qty: int, name: str, right: str, options: str | None, note: str | None, gap: int = ITEM_GAP_DOTS,
          size: str = "h", xbold: bool = True) -> None:
    """**2x** Kaffee                 7.00
          **2 Zucker, Hafermilch**
          **» extra heiss**
    size: Grösse der ersten Zeile (h = doppelt hoch wie bisher), xbold: Zusatz und Bemerkung fett."""
    sw, sh = SIZE.get(size, (1, 2))
    w = b.width // sw
    q = f"{qty}x"
    indent = " " * (len(q) + 1)
    room = w - len(indent) - (len(right) + 1 if right else 0)
    names = _wrap(name, max(8, room))
    b.size(sw, sh).bold().text(q).bold(False)
    first = " " + names[0]
    b.text(first + (" " * (w - len(q) - len(first) - len(right)) + right if right else "")).line()
    for cont in names[1:]:
        b.line(indent + cont)
    b.size()
    ind = " " * (len(indent) * sw)
    for extra in (options, f"» {note}" if note else None):
        if extra:                                            # fett: «ohne Senf» fällt in der Küche sofort auf
            if xbold:
                b.bold()
            for chunk in _wrap(extra, b.width - len(ind)):
                b.line(ind + chunk)
            if xbold:
                b.bold(False)
    b.gap(gap)


def _cat(b: Bon, k: dict, text: str) -> None:
    """Kategorie als Überschrift (Abstand darüber; unterstrichen, auf Wunsch fett)."""
    b.gap(16)
    if k.get("cat_ul", True):
        b.underline()
    if k.get("cat_bold"):
        b.bold()
    b.line(text)
    if k.get("cat_bold"):
        b.bold(False)
    if k.get("cat_ul", True):
        b.underline(False)


# Durchgestrichene Schrift kennt ESC/POS nicht: Die stornierten Positionen gehen als Rasterbild (GS v 0) an den
# Drucker. Schrift: Bitstream Vera aus reportlab (ist ohnehin installiert, auch auf dem Pi ohne Schriften-Paket).
def _vera(bold: bool = True) -> str:
    import reportlab
    return str(Path(reportlab.__file__).parent / "fonts" / ("VeraBd.ttf" if bold else "Vera.ttf"))


def _px_wrap(text: str, font, width: float) -> list[str]:
    out, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}" if cur else word
        if cur and font.getlength(trial) > width:
            out.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        out.append(cur)
    res = []
    for line in out:                                         # ein einzelnes zu langes Wort hart trennen
        while font.getlength(line) > width and len(line) > 1:
            cut = len(line)
            while cut > 1 and font.getlength(line[:cut]) > width:
                cut -= 1
            res.append(line[:cut])
            line = line[cut:]
        res.append(line)
    return res or [""]


def struck_image(width: int, qty: int, name: str, extras: list[str]):
    """«2x ~~Bier 5dl~~» – Menge normal, Produkt (und Optionen/Bemerkung kleiner darunter) durchgestrichen."""
    from PIL import Image, ImageDraw, ImageFont
    big, small = ImageFont.truetype(_vera(True), 34), ImageFont.truetype(_vera(False), 24)
    q = f"{qty}x "
    qx = big.getlength(q)
    room = width - qx - 6
    rows = [(line, big, 4) for line in _px_wrap(name, big, room)]
    rows += [(line, small, 2) for extra in extras for line in _px_wrap(extra, small, room)]
    heights = [sum(f.getmetrics()) + (8 if f is big else 6) for _, f, _ in rows]
    img = Image.new("L", (width, sum(heights) + 4), 255)
    d = ImageDraw.Draw(img)
    d.text((0, 0), q, font=big, fill=0)
    y = 0
    for (line, font, thick), h in zip(rows, heights):
        d.text((qx, y), line, font=font, fill=0)
        _, top, _, bottom = font.getbbox("x")                 # Mitte der Kleinbuchstaben
        mid = y + (top + bottom) // 2 + 1
        d.rectangle([qx - 3, mid - thick // 2, qx + font.getlength(line) + 3, mid - thick // 2 + thick - 1], fill=0)
        y += h
    return img


def _struck_item(b: Bon, qty: int, name: str, options: str | None, note: str | None) -> None:
    extras = [x for x in (options, f"» {note}" if note else None) if x]
    try:
        img = struck_image(min(b.width * 12, 576), qty, name, extras)
    except Exception:  # noqa: BLE001 – ohne Schrift/Pillow: lieber normal drucken als gar nicht
        log.exception("Storno-Bon: durchgestrichene Schrift nicht möglich")
        _item(b, qty, name, "", options, note)
        return
    b.raster(img)


def _wrap(text: str, width: int) -> list[str]:
    out, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}" if cur else word
    if cur:
        out.append(cur)
    return [l[i:i + width] for l in out for i in range(0, len(l), width)] or [""]


def _ticket_ctx(b: Bon, device: PrinterCfg, station: PrinterCfg, *, lang, images, logo, **kw) -> _Ctx:
    return _Ctx(b, lang, images, logo, station=station, rerouted=device.id != station.id, **kw)


def render_table_bon(device: PrinterCfg, station: PrinterCfg, *, event_name: str, currency: str,
                     table: str, order_id: int, created_at: str, items: list[dict],
                     device_name: str | None = None, table_word: str = "Tisch", delivery: str | None = None,
                     n: int | None = None, of: int | None = None, reprint: bool = False, test: bool = False,
                     lang: str | None = None, design: dict | None = None, logo=None, images: dict | None = None,
                     now_label: str | None = None, marks: list | None = None) -> bytes:
    """Sammelbon einer Station. Layout/Breite/Sprache vom physischen Drucker, Kopf & Preise von der Station.
    items mit «cancelled» (schon storniert): oben nur, was noch gilt; das Stornierte darunter durchgestrichen –
    ein Nachdruck oder ein Bon, der erst nach einem Teil-Storno gedruckt wird, zeigt so den aktuellen Stand.
    design: Bausteine (app/bon_design.py, auch alte Form); now_label: Name von «Sofort» (Lieferzeit «immer»)."""
    lang = lang or device.lang
    blocks, ik = _ticket_setup(design)
    b = _bon(device.chars_per_line, ik.get("spacing"))
    live = [{**i, "qty": i["qty"] - int(i.get("cancelled") or 0)} for i in items]
    live = [i for i in live if i["qty"] > 0]
    gone = [{**i, "qty": min(i["qty"], int(i.get("cancelled") or 0))} for i in items if i.get("cancelled")]

    def body(c: _Ctx, k: dict) -> None:
        _al(c, "left")
        cats: list[str | None] = []
        for it in live:
            if it.get("category") not in cats:
                cats.append(it.get("category"))
        show_cat = (len(cats) > 1 or (cats and cats[0])) and k.get("cats", True)
        for cat in cats:
            if show_cat:                                     # Abstand über jeder Kategorie
                _cat(b, k, cat or t("bon.category.other", lang))
            for it in (i for i in live if i.get("category") == cat):
                right = money(it["price_cents"] * it["qty"]) if _prices(ik, station) else ""
                _item(b, it["qty"], it["name"], right, it.get("options"), it.get("note"), _gap(k.get("spacing")),
                      k.get("size", "h"), k.get("xbold", True))
        if gone:
            b.gap(16).underline().line(t("bon.storno.section", lang)).underline(False)
            for it in gone:
                _struck_item(b, it["qty"], it["name"], it.get("options"), it.get("note"))

    c = _ticket_ctx(b, device, station, lang=lang, images=images, logo=logo, event_name=event_name, table=table,
                    created_at=created_at, device_name=device_name, n=n if of and of > 1 else None, of=of,
                    table_word=table_word, delivery=delivery, now_label=now_label, reprint=reprint, test=test,
                    currency=currency, order_id=order_id, sum_items=live, prices=_prices(ik, station))
    b.align("left")
    _run(c, blocks, {**TICKET_BLOCKS, "items": body}, marks)
    return b.cut().build()


def render_single_bon(device: PrinterCfg, station: PrinterCfg, *, event_name: str, currency: str,
                      table: str, order_id: int, created_at: str, item: dict, n: int, of: int,
                      device_name: str | None = None, table_word: str = "Tisch", delivery: str | None = None,
                      reprint: bool = False, test: bool = False, lang: str | None = None, design: dict | None = None,
                      logo=None, images: dict | None = None, now_label: str | None = None,
                      marks: list | None = None) -> bytes:
    """Einzelbon/Abriss für genau 1 Stück (z.B. Essensmarke), mit n/m neben dem Tisch."""
    lang = lang or device.lang
    blocks, ik = _ticket_setup(design)
    b = _bon(device.chars_per_line, ik.get("spacing"))
    one = {**item, "qty": 1}
    cancelled = int(item.get("cancelled") or 0)
    if cancelled >= item["qty"]:                             # alle Stück dieser Position storniert
        one["qty"] = 0

    def body(c: _Ctx, k: dict) -> None:
        _al(c, "left")
        if item.get("category") and k.get("cats", True):
            _cat(b, k, item["category"])
        if not one["qty"]:
            _struck_item(b, 1, item["name"], item.get("options"), item.get("note"))
            return
        _item(b, 1, item["name"], money(item["price_cents"]) if _prices(ik, station) else "",
              item.get("options"), item.get("note"), _gap(k.get("spacing")), k.get("size", "h"), k.get("xbold", True))
        if cancelled:                                        # welches Stück, weiss nur die Bedienung
            b.invert().bold().line(f" {t('bon.storno.partial', lang, count=cancelled, total=item['qty'])} "[:b.width])
            b.bold(False).invert(False)

    c = _ticket_ctx(b, device, station, lang=lang, images=images, logo=logo, event_name=event_name, table=table,
                    created_at=created_at, device_name=device_name, n=n, of=of, table_word=table_word,
                    delivery=delivery, now_label=now_label, reprint=reprint, test=test, currency=currency,
                    order_id=order_id, sum_items=[one] if one["qty"] else [], prices=_prices(ik, station))
    b.align("left")
    _run(c, blocks, {**TICKET_BLOCKS, "items": body}, marks)
    return b.cut().build()


def render_storno_bon(device: PrinterCfg, station: PrinterCfg, *, event_name: str, table: str, order_id: int,
                      created_at: str, by: str | None, reason: str, items: list[dict], table_word: str = "Tisch",
                      test: bool = False, lang: str | None = None, design: dict | None = None,
                      images: dict | None = None, logo=None) -> bytes:
    """Storno-Bon an die Station: unübersehbar STORNO, Tisch, was nicht mehr zubereitet/geliefert werden soll.
    Bleibt fest (T24, Frage 4); vom Bon-Baukasten übernimmt er nur das Logo und die Tischnummer."""
    lang = lang or device.lang
    b = _bon(device.chars_per_line)
    w = b.width
    b.align("left")
    blocks = _ticket_setup(design)[0] if design else []
    lk = next((k for k in blocks if k.get("type") == "logo" and k.get("on")), None)
    tk = next((k for k in blocks if k.get("type") == "table"), None)
    c = _Ctx(b, lang, images, logo, table=table, table_word=table_word, n=None, of=None)
    if lk:
        _k_logo(c, lk)
    if test:
        _test_banner(b, lang)
    b.invert().bold().size(2, 2).line(f" {t('bon.storno.title', lang)} ".center(w // 2)).size().bold(False).invert(False)
    b.columns(event_name or "", station.name)
    if tk and (tk.get("size", "2") != "2" or tk.get("invert") or not tk.get("word", True)
               or tk.get("align", "left") != "left"):
        _t_table(c, tk)
    else:
        big = f"{table_word_of(table_word, lang).upper()} {table}"
        b.size(2, 2).bold()
        for chunk in _wrap(big, w // 2):
            b.line(chunk)
        b.bold(False).size()
    b.columns(_hm(created_at), by or "")
    b.rule("-")
    for it in items:                                         # Produkt durchgestrichen (als Bild, siehe _struck_item)
        _struck_item(b, it["qty"], it["name"], it.get("options"), it.get("note"))
    b.rule("-")
    for chunk in _wrap(t("bon.storno.reason", lang, reason=reason), w):
        b.line(chunk)
    b.columns(t("bon.storno.of_order", lang, no=f"#{order_id:04d}"), "")
    return b.beep().cut().build()


def render_sample_banner(device: PrinterCfg) -> bytes:
    """Probedruck aus dem Bon-Baukasten: oben «MUSTER» (keine Quittung, nicht zubereiten). Ohne Schnitt – der Bon
    folgt direkt."""
    lang = device.lang
    b = Bon(device.chars_per_line)
    w = b.width
    b.invert().bold().size(2, 2)
    for chunk in _wrap(t("bon.sample.banner", lang), w // 2 - 2):
        b.line(f" {chunk} ".center(w // 2))
    b.size()
    for chunk in _wrap(t("bon.sample.note", lang), w - 2):
        b.line(f" {chunk} ".center(w))
    b.bold(False).invert(False)
    return b.build()


def render_test_bon(printer: PrinterCfg) -> bytes:
    lang = printer.lang
    b = Bon(printer.chars_per_line)
    b.align("center").size(2, 2).bold().line(t("bon.testprint.title", lang)).bold(False).size()
    b.line(printer.name).line(f"{printer.host}:{printer.port}")
    b.line(datetime.now().strftime(t("bon.testprint.datetime", lang)))
    b.align("left").rule().line(t("bon.testprint.chars", lang)).columns(t("bon.testprint.left", lang),
                                                                         t("bon.testprint.right", lang))
    b.rule().line(("1234567890" * 10)[:printer.chars_per_line])
    return b.cut().build()


# Quittung für den Gast (app/receipts.py). Gedruckt wird der beim Ausstellen festgehaltene Inhalt – jeder weitere
# Druck ist dieselbe Quittung mit «KOPIE». MWST-Arten mit Buchstaben (A, B, C) an den Positionen.
VAT_LETTER = {"normal": "A", "reduced": "B", "lodging": "C"}
RECEIPT_PAY = {"cash": "bon.receipt.pay_cash", "card": "bon.receipt.pay_card", "twint": "bon.receipt.pay_twint",
               "other": "bon.receipt.pay_other"}


def _round5(c: int) -> int:
    return int((c + 2) // 5 * 5) if c >= 0 else -_round5(-c)


def _dt(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return str(iso or "")


def _pct(bp: int) -> str:
    return f"{bp / 100:.2f}".rstrip("0").rstrip(".") + "%"


def _r_top(c: _Ctx, k: dict) -> None:
    """Fest zuoberst: TEST (Testmodus) und KOPIE (jeder weitere Druck)."""
    b, w = c.b, c.b.width
    if c.rec.get("test"):
        _test_banner(b, c.lang)
    if c.copy:                                               # Kopie: nicht mit dem Original verwechseln
        b.invert().bold().size(2, 2).line(f" {t('bon.receipt.copy', c.lang)} ".center(w // 2)).size().bold(False).invert(False)


def _r_header(c: _Ctx, k: dict) -> None:
    """Kopf: Verein und Adresse (Einstellung receipt_header, beim Ausstellen festgehalten), erste Zeile fett."""
    b, w, d = c.b, c.b.width, c.d
    head = d.get("header") or ([d["event"]] if d.get("event") else [])
    size = k.get("size", "h")
    sw, sh = SIZE.get(size, (1, 2))
    _al(c, k.get("align", "center"))
    for n, txt in enumerate(head):
        if n == 0:
            b.bold()
            if (sw, sh) != (1, 1):
                b.size(sw, sh)
            for chunk in _wrap(txt, w // sw):
                b.line(chunk)
            if (sw, sh) != (1, 1):
                b.size()
            b.bold(False)
        else:
            for chunk in _wrap(txt, w):
                b.line(chunk)


def _r_vat_no(c: _Ctx, k: dict) -> None:
    d = c.d
    if d.get("vat") and d.get("vat_number"):                 # «CHE-123.456.789 MWST» -> «MWST-Nr. CHE-123.456.789»
        _al(c, k.get("align", "center"))
        num = re.sub(r"\s*(MWST|TVA|IVA|VAT)\s*$", "", d["vat_number"], flags=re.I)
        for chunk in _wrap(t("bon.receipt.vat_number", c.lang, number=num), c.b.width):
            c.b.line(chunk)


def _sized_columns(b: Bon, left: str, right: str, size: str, bold: bool) -> None:
    sw, sh = SIZE.get(size, (1, 2))
    if bold:
        b.bold()
    if (sw, sh) != (1, 1):
        b.size(sw, sh)
    b.columns(left, right, width=b.width // sw)
    if (sw, sh) != (1, 1):
        b.size()
    if bold:
        b.bold(False)


def _r_title(c: _Ctx, k: dict) -> None:
    rec = c.rec
    _al(c, "left")
    no = t("bon.receipt.no", c.lang, no=f"{rec['no']:05d}") if rec.get("no") else t("bon.test.banner", c.lang)
    _sized_columns(c.b, t("bon.receipt.title", c.lang), no, k.get("size", "h"), k.get("bold", True))


def _r_info(c: _Ctx, k: dict) -> None:
    """Datum (Pflicht), Tisch, Bestellnummer(n), Handy; bei «Einzeln» die Person."""
    b, w, d, rec, lang = c.b, c.b.width, c.d, c.rec, c.lang
    _al(c, "left")
    show_table, show_order, show_dev = k.get("table", True), k.get("order", True), k.get("device", True)
    b.columns(_dt(rec["created_at"]), f"{table_word_of(d.get('table_word', 'Tisch'), lang)} {d.get('table', '')}" if show_table else "")
    dev = (d.get("device") or "") if show_dev else ""
    if len(d.get("orders") or []) > 1:                       # offener Tisch: alle Bestellungen, das Gerät darunter
        if show_order:
            nos = ", ".join(f"#{int(n):04d}" for n in d["orders"])
            for chunk in _wrap(t("bon.receipt.orders", lang, nos=nos), w):
                b.line(chunk)
        if dev:
            b.line(dev[:w])
    elif show_order or dev:
        b.columns(t("bon.receipt.order", lang, no=f"#{int(d.get('order_no') or rec['order_id']):04d}") if show_order else "",
                  dev[:w // 2])
    if d.get("part"):
        b.line(t("bon.receipt.part", lang, n=d["part"]))


def _r_guest(c: _Ctx, k: dict) -> None:
    """Name und Adresse des Gastes (über CHF 400 mit MWST, app/receipts.py)."""
    if c.d.get("recipient"):
        _al(c, "left")
        c.b.line(t("bon.receipt.recipient", c.lang))
        for txt in c.d["recipient"]:
            c.b.line(f"  {txt}"[:c.b.width])


def _r_items(c: _Ctx, k: dict) -> None:
    b, w = c.b, c.b.width
    _al(c, "left")
    for x in c.lines:
        right = money(x["amount"]) + (f" {VAT_LETTER.get(x.get('vat_code') or 'normal', 'A')}" if c.vat else "")
        if x.get("share"):                         # Teilen: Anteil am Total (in der Sprache des Druckers)
            b.columns(t("bon.receipt.share_line", c.lang, total=money(int((c.d.get("share") or {}).get("total") or 0))), right)
            continue
        b.columns(f"{x['qty']} x {x['name']}", right)
        extra = [t("bon.receipt.each", c.lang, price=money(x["price"]))] if x["qty"] > 1 and k.get("each", True) else []
        if x.get("options"):
            extra.append(x["options"])
        for txt in extra:
            for chunk in _wrap(txt, w - 4):
                b.line(f"    {chunk}")


def _r_total(c: _Ctx, k: dict) -> None:
    b, d, lang = c.b, c.d, c.lang
    _al(c, "left")
    total = int(c.rec["total_cents"])
    _sized_columns(b, t("bon.receipt.total", lang, cur=d.get("currency") or "CHF"), money(total), k.get("size", "h"), True)
    pay = d.get("payment")
    if pay in (None, "cash") and _round5(total) != total:   # bar auf 5 Rappen gerundet (wie an der Kasse)
        b.columns(t("bon.receipt.cash_rounded", lang), money(_round5(total)))
    if pay in RECEIPT_PAY:
        b.columns(t("bon.receipt.paid_with", lang), t(RECEIPT_PAY[pay], lang))


def _r_vat(c: _Ctx, k: dict) -> None:
    """MWST pro Satz: Brutto und enthaltene MWST."""
    if not c.vat:
        return
    b, w, lang = c.b, c.b.width, c.lang
    _al(c, "left")
    groups: dict[tuple, int] = {}
    for x in c.lines:
        if x.get("vat_bp") is not None:
            key = (x.get("vat_code") or "normal", int(x["vat_bp"]))
            groups[key] = groups.get(key, 0) + int(x["amount"])
    b.rule("-")
    col = max(9, (w - 12) // 2)
    b.line(t("bon.receipt.vat_head", lang)[:w - 2 * col].ljust(w - 2 * col)
           + t("bon.receipt.vat_gross", lang).rjust(col)[:col] + t("bon.receipt.vat_amount", lang).rjust(col)[:col])
    vat_sum = 0
    for (code, bp), gross in sorted(groups.items(), key=lambda kv: VAT_LETTER.get(kv[0][0], "Z")):
        v, _net = lb.vat_split(gross, bp)
        vat_sum += v
        label = f"{VAT_LETTER.get(code, '?')} {_pct(bp)}"
        b.line(label[:w - 2 * col].ljust(w - 2 * col) + money(gross).rjust(col) + money(v).rjust(col))
    b.columns(t("bon.receipt.vat_total", lang), money(vat_sum))
    b.line(t("bon.receipt.vat_incl", lang)[:w])


def _r_footer(c: _Ctx, k: dict) -> None:
    """Fusszeile (Einstellung receipt_footer, beim Ausstellen festgehalten)."""
    foot = c.d.get("footer") or []
    if not foot:
        return
    b, size = c.b, k.get("size", "1")
    if k.get("line", True):
        b.rule("-")
    _al(c, k.get("align", "center"))
    plain = size == "1"
    cols = b.width if plain else _sized(b, size)
    for txt in foot:
        for chunk in _wrap(txt, cols):
            b.line(chunk)
    if not plain:
        _unsized(b, size)
    _al(c, "left")


def _r_check(c: _Ctx, k: dict) -> None:
    """Fest zuletzt: Prüfnummer (Prüfsumme der Quittung) und interne Nummer."""
    _al(c, "left")
    c.b.feed(1).columns(t("bon.receipt.check", c.lang, code=lb.short_check(c.rec.get("digest"))), f"Q{c.rec['id']}")


RECEIPT_BLOCKS = {**FREE_BLOCKS, "top": _r_top, "header": _r_header, "vat_no": _r_vat_no, "title": _r_title,
                  "info": _r_info, "guest": _r_guest, "items": _r_items, "total": _r_total, "vat": _r_vat,
                  "footer": _r_footer, "check": _r_check}


def render_receipt(device: PrinterCfg, rec: dict, *, copy: bool = False, lang: str | None = None, logo=None,
                   images: dict | None = None, marks: list | None = None) -> bytes:
    """Quittung. Gestaltung (Bausteine) wie beim Ausstellen festgehalten (data.design, app/bon_design.py); ältere
    Quittungen (alte Form oder ohne design): wie damals. Die Pflichtangaben stehen immer. images: Bilder der Bausteine
    nach ID; logo: Logo einer älteren Quittung."""
    from . import bon_design
    lang = lang or device.lang
    d = json.loads(rec["data"] or "{}")
    lines = json.loads(rec["lines"] or "[]")
    blocks = bon_design.receipt_blocks(d.get("design"))
    b = Bon(device.chars_per_line)
    c = _Ctx(b, lang, images, logo, rec=rec, d=d, lines=lines, copy=copy,
             vat=bool(d.get("vat")) and any(x.get("vat_bp") is not None for x in lines))
    b.align("left")
    _run(c, blocks, RECEIPT_BLOCKS, marks)
    return b.cut().build()


# Warnbon: Text wird erst beim Drucken übersetzt (Sprache des Druckers). In der Datenbank steht er als
# {"key": …, "params": {…}}; ältere Warnbons enthalten fertigen Text.
WARN_WHAT = {"paper_out": "bon.warn.what_paper", "cover_open": "bon.warn.what_cover", "no_status": "bon.warn.what_no_status",
             "printer_error": "bon.warn.what_error"}


def warn_note(printer: str, since: str, code: str, waiting: int) -> str:
    return json.dumps({"key": "bon.warn.text", "params": {"printer": printer, "since": since, "code": code,
                                                          "waiting": waiting}}, ensure_ascii=False)


def note_text(note: str | None, lang: str | None) -> str:
    """Gespeicherter Warn-Hinweis -> Text in der gewünschten Sprache."""
    try:
        data = json.loads(note or "")
    except ValueError:
        return note or ""
    if not isinstance(data, dict) or data.get("key") != "bon.warn.text":
        return note or ""
    p = dict(data.get("params") or {})
    p["what"] = t(WARN_WHAT.get(p.pop("code", ""), "bon.warn.what_unreachable"), lang)
    p["count"] = p.get("waiting", 0)
    return t("bon.warn.text", lang, **p)


def render_warn_bon(device: PrinterCfg, *, event_name: str, text: str, created_at: str) -> bytes:
    """Hinweis, dass ein anderer Drucker nicht erreichbar ist (Einstellung "Warnung drucken")."""
    lang = device.lang
    text = note_text(text, lang)
    b = Bon(device.chars_per_line)
    b.align("left").columns(event_name or "", device.name)
    b.align("center").size(2, 2).bold().line(t("bon.warn.title", lang)).bold(False).size()
    b.align("left").rule("=")
    for chunk in _wrap(text, device.chars_per_line):
        b.size(1, 2).line(chunk).size()
    b.rule("=").columns(_hm(created_at), "")
    return b.beep().cut().build()


def render_password_bon(device: PrinterCfg, *, password: str, node: str, when: str) -> bytes:
    """Sicherungs-Passwort (T8) – geht direkt an den Drucker, nie über die Warteschlange (sonst stünde es in
    print_jobs und damit in der Datenbank aller Pi)."""
    lang = device.lang
    b = Bon(device.chars_per_line)
    b.align("center").size(2, 2).bold().line(t("bon.backup_pw.title", lang)).bold(False).size()
    b.line(t("bon.backup_pw.sub", lang, node=node.upper(), when=when)).align("left").rule()
    groups = password.split("-")
    b.align("center").size(2, 2).bold()
    for i in range(0, len(groups), 3):
        b.line("-".join(groups[i:i + 3]))
    b.bold(False).size().feed(1)
    b.qr(password, module=8)
    b.feed(1).align("left").rule()
    for chunk in _wrap(t("bon.backup_pw.hint", lang), device.chars_per_line):
        b.line(chunk)
    b.rule().align("center")
    for chunk in _wrap(t("bon.backup_pw.fade", lang), device.chars_per_line):
        b.line(chunk)
    return b.cut().build()


def render_qr_bon(device: PrinterCfg, *, event_name: str, label: str, table_word: str = "Tisch") -> bytes:
    """Tisch-Kärtchen mit QR-Code ("table:<label>") zum Aufkleben/Hinstellen."""
    lang = device.lang
    b = Bon(device.chars_per_line)
    b.align("center")
    if event_name:
        b.line(event_name)
    b.feed(1).size(2, 2).bold().line(table_word_of(table_word, lang).upper()).size(4, 4).line(label).bold(False).size().feed(1)
    b.qr(f"table:{label}", module=10)
    b.feed(1).line(t("bon.qr.hint", lang))
    return b.cut().build()


# --------------------------------------------------------------------------
# Job-Planung (bei Bestellung) und Rendern (beim Druck)
# --------------------------------------------------------------------------

@dataclass
class JobSpec:
    station_id: int
    kind: str                    # 'table' | 'single'
    line_no: int | None = None   # Position in der Bestellung (bei 'single')
    n: int = 1
    of_n: int = 1
    delivery: str | None = None  # Lieferzeit (None = sofort)


def plan_jobs(items: list[dict], printer_ids: set[int]) -> tuple[list[JobSpec], list[str]]:
    """Pro Station und Lieferzeit (sofort / Pause / Schluss …): 1 Sammelbon für die normalen
    Positionen + 1 Einzelbon je Stück (single_ticket). n/m zählt über ALLE Bons dieser Station
    und Lieferzeit – Sammelbon zuerst, dann die Einzelbons (z.B. 1/3 Sammelbon, 2/3 und 3/3 Würste)."""
    jobs: list[JobSpec] = []
    warnings: list[str] = []
    groups: dict[tuple[int, str | None], list[dict]] = {}
    for it in items:
        pid = it.get("printer_id")
        if pid not in printer_ids:
            warnings.append(t("err.order.no_printer", name=it["name"]))
            continue
        groups.setdefault((pid, it.get("delivery")), []).append(it)
    for (pid, when), its in groups.items():
        has_table = any(not i["single_ticket"] for i in its)
        singles = [i for i in its if i["single_ticket"]]
        total = int(has_table) + sum(i["qty"] for i in singles)
        n = 0
        if has_table:
            n += 1
            jobs.append(JobSpec(pid, "table", n=n, of_n=total, delivery=when))
        for it in singles:
            for _ in range(it["qty"]):
                n += 1
                jobs.append(JobSpec(pid, "single", it["line_no"], n, total, delivery=when))
    return jobs, warnings


def load_printers(store: Store) -> dict[int, PrinterCfg]:
    return {r["id"]: PrinterCfg.from_row(r) for r in store.query("SELECT * FROM printers")}


def render_job(store: Store, job: dict, printers: dict[int, PrinterCfg]) -> bytes:
    device = printers[job["printer_id"]]
    station = printers.get(job["station_id"], device)
    if job["kind"] == "test":
        return render_test_bon(device)
    if job["kind"] == "warn":
        return render_warn_bon(device, event_name=store.setting("event_name"), text=job["note"] or "",
                               created_at=job["created_at"])
    if job["kind"] == "qr":
        return render_qr_bon(device, event_name=store.setting("event_name"), label=job["note"] or "?",
                             table_word=store.setting("table_word", "Tisch"))

    if job["kind"] == "receipt":
        rec = store.query_one("SELECT * FROM receipts WHERE request_id=?", (job["note"],))
        if rec is None:
            raise ValueError(t("srv.job.receipt_missing"))
        from . import bon_design                     # Bilder, die beim Ausstellen galten (bleiben in der Datenbank)
        blocks = bon_design.receipt_blocks(json.loads(rec["data"] or "{}").get("design"))
        return render_receipt(device, rec, copy=bool(job.get("reprint")), lang=device.lang,
                              images=bon_design.load_images(store, bon_design.image_ids(blocks)))

    if job["kind"] == "storno":
        c = store.query_one("SELECT c.*, o.table_label, o.test FROM cancellations c JOIN orders o ON o.id=c.order_id "
                            "WHERE c.client_uuid=?", (job["note"],))
        if c is None:
            raise ValueError(t("srv.job.order_missing", id=job["order_id"]))
        items = store.query("SELECT ci.qty, i.name, i.options, i.note, i.printer_id FROM cancellation_items ci "
                            "JOIN order_items i ON i.id=ci.item_id WHERE ci.cancellation_id=? ORDER BY i.line_no",
                            (c["id"],))
        mine = [i for i in items if i["printer_id"] == job["station_id"]] or items
        by = c.get("by_device") or c.get("by_user")
        from . import bon_design                     # Storno-Bon: vom Baukasten nur Logo und Tischnummer
        design = bon_design.ticket(store)
        return render_storno_bon(device, station, event_name=store.setting("event_name"), table=c["table_label"],
                                 order_id=c["order_id"], created_at=c["created_at"], by=by, reason=c["reason"],
                                 items=mine, table_word=store.setting("table_word", "Tisch"), test=bool(c["test"]),
                                 lang=device.lang, design=design,
                                 images=bon_design.load_images(store, bon_design.image_ids(design["blocks"])))

    order = store.query_one("SELECT * FROM orders WHERE id=?", (job["order_id"],))
    if order is None:
        raise ValueError(t("srv.job.order_missing", id=job["order_id"]))
    from . import bon_design                         # Bon-Baukasten: Bausteine, Bilder, Name von «Sofort»
    from .orders import delivery_times
    design = bon_design.ticket(store)
    common = dict(event_name=store.setting("event_name"), currency=store.setting("currency", "CHF"),
                  design=design, images=bon_design.load_images(store, bon_design.image_ids(design["blocks"])),
                  now_label=delivery_times(store)[0],
                  table=order["table_label"], order_id=order["id"], created_at=order["created_at"],
                  device_name=lb.device_label(order.get("device_cert"), order["device"]),
                  table_word=store.setting("table_word", "Tisch"),
                  reprint=bool(job.get("reprint")), test=bool(order.get("test")), lang=device.lang)

    # schon Storniertes (Gegenbuchung): der Bon zeigt den aktuellen Stand – sonst käme z.B. ein Nachdruck wieder mit
    # allem, was die Küche nicht mehr zubereiten soll
    cancelled = {r["item_id"]: int(r["q"] or 0) for r in store.query(
        "SELECT ci.item_id, SUM(ci.qty) AS q FROM cancellation_items ci JOIN cancellations c ON c.id=ci.cancellation_id "
        "WHERE c.order_id=? GROUP BY ci.item_id", (order["id"],))}
    if job["kind"] == "table":
        items = store.query("SELECT * FROM order_items WHERE order_id=? AND printer_id=? AND single_ticket=0 "
                            "AND COALESCE(delivery,'')=COALESCE(?,'') "
                            "ORDER BY COALESCE(line_no, 999999), cat_sort, sort, name",
                            (order["id"], job["station_id"], job.get("delivery")))
        items = [{**i, "cancelled": cancelled.get(i["id"], 0)} for i in items]
        return render_table_bon(device, station, items=items, delivery=job.get("delivery"),
                                n=job.get("n"), of=job.get("of_n"), **common)
    if job["kind"] == "single":
        item = store.query_one("SELECT * FROM order_items WHERE id=?", (job["item_id"],))
        item = {**item, "cancelled": cancelled.get(item["id"], 0)}
        return render_single_bon(device, station, item=item, n=job["n"], of=job["of_n"],
                                 delivery=job.get("delivery"), **common)
    raise ValueError(t("srv.job.unknown_kind", kind=job["kind"]))


def job_label(j: dict, table_word: str = "Tisch") -> str:
    """Bezeichnung eines Druckauftrags für die Verwaltung (in deren Sprache)."""
    word = t("admin.word.seat" if table_word == "Platz" else "admin.word.table")
    when = f" – {j['delivery']}" if j.get("delivery") else ""
    test = t("admin.jobs.test_prefix") if j.get("test") else ""
    if j["kind"] == "test":
        return t("admin.jobs.kind_test")
    if j["kind"] == "qr":
        return t("admin.jobs.kind_qr", word=word, label=j.get("note") or "")
    if j["kind"] == "warn":
        return t("admin.jobs.kind_warn", text=note_text(j.get("note"), None))
    no = f"#{(j.get('order_id') or 0):04d}"
    if j["kind"] == "receipt":
        rno = j.get("receipt_no")
        return t("admin.jobs.kind_receipt", no=f"{rno:05d}" if rno else "TEST",
                 order=no, word=word, table=j.get("table_label") or "?") + (t("admin.jobs.copy_suffix") if j.get("reprint") else "")
    if j["kind"] == "storno":
        return test + t("admin.jobs.kind_storno", no=no, word=word, table=j.get("table_label") or "?")
    if j["kind"] == "table":
        return test + t("admin.jobs.kind_table", no=no, word=word, table=j.get("table_label") or "?") + when
    return test + t("admin.jobs.kind_single", no=no, word=word, table=j.get("table_label") or "?",
                    item=j.get("item_name") or "?", n=j.get("n"), of=j.get("of_n")) + when


def list_jobs(store: Store, status: str | None = None, limit: int = 200) -> list[dict]:
    where, params = ("WHERE j.status = ?", (status,)) if status else ("", ())
    rows = store.query(f"""
        SELECT j.id, j.kind, j.status, j.attempts, j.last_error, j.error_code, j.created_at, j.printed_at,
               j.order_id, j.claimed_by, j.note, j.n, j.of_n, j.delivery, j.reprint, o.table_label, o.test,
               i.name AS item_name, p.name AS printer, s.name AS station,
               (SELECT r.no FROM receipts r WHERE j.kind='receipt' AND r.request_id=j.note) AS receipt_no
        FROM print_jobs j
        JOIN printers p ON p.id = j.printer_id
        JOIN printers s ON s.id = j.station_id
        LEFT JOIN orders o ON o.id = j.order_id
        LEFT JOIN order_items i ON i.id = j.item_id
        {where} ORDER BY j.id DESC LIMIT ?""", (*params, limit))
    word = store.setting("table_word", "Tisch")
    return [{**r, "test": bool(r.get("test")), "label": job_label(r, word)} for r in rows]


_queue_cache: dict = {"at": 0.0, "store": None, "rows": None}


def mark_status_seen(store: Store, printer_id: int) -> None:
    """Drucker hat die Statusabfrage gültig beantwortet (T15): einmal pro Drucker festhalten – in der Datenbank, damit
    es nach einem Neustart und auf jedem Pi gilt. Nur schreiben, wenn es noch nicht gesetzt ist (T9)."""
    store.execute("UPDATE printers SET status_seen=1 WHERE id=? AND status_seen=0", (printer_id,))
    queue_status_forget()


def queue_status_cached(store: Store, max_age: float = 1.5) -> list[dict]:
    """queue_status für Anzeigen, die alle paar Sekunden fragen (jedes Handy, Statusanzeige): höchstens max_age alt."""
    now = time.monotonic()
    c = _queue_cache
    if c["store"] is store and c["rows"] is not None and now - c["at"] < max_age:
        return [dict(r) for r in c["rows"]]
    rows = queue_status(store)
    c.update(at=now, store=store, rows=rows)
    return [dict(r) for r in rows]


def queue_status_forget() -> None:
    """Nach Änderungen in der Druckschlange (neue Bons, gedruckt, umgeleitet) gleich frisch lesen."""
    _queue_cache["rows"] = None


def queue_status(store: Store) -> list[dict]:
    rows = store.query("""
        SELECT p.id, p.name, p.host, p.port, p.enabled, p.chars_per_line, p.show_prices,
               p.fail_mode, p.fail_target, p.fail_after, p.status_seen,
               COALESCE(SUM(j.status='pending'),0)   AS pending,
               COALESCE(SUM(j.status='printing'),0)  AS printing,
               COALESCE(SUM(j.status='failed'),0)    AS failed,
               COALESCE(SUM(j.status='uncertain'),0) AS uncertain,
               (SELECT last_error FROM print_jobs x
                 WHERE x.printer_id=p.id AND x.status='pending' AND x.last_error IS NOT NULL
                 ORDER BY x.id LIMIT 1) AS last_error,
               (SELECT error_code FROM print_jobs x
                 WHERE x.printer_id=p.id AND x.status='pending' AND x.last_error IS NOT NULL
                 ORDER BY x.id LIMIT 1) AS last_error_code,
               p.language
        FROM printers p LEFT JOIN print_jobs j ON j.printer_id = p.id
        GROUP BY p.id ORDER BY p.name""")
    return rows


# --------------------------------------------------------------------------
# Spooler
# --------------------------------------------------------------------------

class PrintSpooler:
    """Läuft auf jedem Knoten, druckt aber nur, solange dieser Knoten Raft-Leader ist."""

    def __init__(self, store: Store, node_id: str = "local", *,
                 backoff: tuple[float, ...] = (1, 2, 3, 5, 10, 15),
                 idle_poll: float = 10.0, leader_poll: float = 1.0, demote_after: int = 2,
                 sweep_every: int = 30):
        self.store = store
        self.node_id = node_id
        self.backoff = backoff
        self.idle_poll = idle_poll
        self.leader_poll = leader_poll
        self.demote_after = demote_after      # so oft hintereinander sicher "kein Leader", dann Druck aus
        self.sweep_every = sweep_every        # alle so viele Runden hängende eigene Jobs aufräumen
        self.is_leader = False
        self._inflight: set[int] = set()      # Jobs, die DIESER Prozess gerade an einen Drucker sendet
        self._events: dict[int, asyncio.Event] = {}
        self._workers: dict[int, asyncio.Task] = {}
        self._leader_task: asyncio.Task | None = None
        self._stopping = False
        self._down: dict[int, tuple[float, str]] = {}   # Drucker -> (seit monotonic, seit Uhrzeit)
        self._warned: set[int] = set()
        self._reason: dict[int, str] = {}                # Drucker -> letzter Fehler ("Papier leer", ...)
        self._reason_code: dict[int, str] = {}           # Drucker -> Fehlercode (paper_out, …) – zum Entscheiden

    async def _db(self, fn, *args):
        """DB-Aufrufe (HTTP zu rqlite) nicht im Event-Loop blockieren."""
        return await asyncio.to_thread(fn, *args)

    # -- Lebenszyklus -----------------------------------------------------
    async def start(self) -> None:
        self._leader_task = asyncio.create_task(self._leader_loop(), name="leader-watch")

    async def stop(self) -> None:
        """Laufende Sendevorgänge dürfen fertig werden (kein halber Bon)."""
        self._stopping = True
        self.is_leader = False
        if self._leader_task:
            self._leader_task.cancel()
        for ev in self._events.values():
            ev.set()
        await asyncio.gather(*self._workers.values(), return_exceptions=True)
        if self._leader_task:
            await asyncio.gather(self._leader_task, return_exceptions=True)

    async def _leader_loop(self) -> None:
        """Leader-Rolle beobachten. Darf nie sterben: jeder Fehler wird geloggt, dann neuer Versuch.
        "Unbekannt" (Timeout, Fehler) ändert nichts; abgegeben wird erst nach mehreren sicheren Antworten."""
        misses = rounds = 0
        while not self._stopping:
            try:
                state = await self._db(self.store.leader_state)
                if state is True:
                    misses = 0
                    if not self.is_leader:
                        await self._on_promote()
                    else:
                        rounds += 1
                        if rounds % self.sweep_every == 0:
                            await self._sweep_stuck()
                elif state is False:
                    misses += 1
                    if self.is_leader and misses >= self.demote_after:
                        log.warning("[%s] Leader-Rolle verloren – Druck pausiert", self.node_id)
                        self.is_leader = False          # Worker beenden sich nach laufendem Job
            except asyncio.CancelledError:
                raise
            except Exception:                           # noqa: BLE001 – Überwachung muss weiterlaufen
                log.exception("[%s] Fehler in der Leader-Überwachung – neuer Versuch", self.node_id)
            await asyncio.sleep(self.leader_poll)

    async def _sweep_stuck(self) -> None:
        """Als Leader regelmässig: eigene Jobs in 'printing', die gerade niemand sendet, hängen fest
        (z.B. Statusupdate nach dem Druck endgültig gescheitert). Nicht nachdrucken – 'uncertain' zeigen.
        Zuerst nur lesen: auch ein UPDATE ohne Treffer wäre ein Schreibzugriff auf allen Pi (T9)."""
        rows = await self._db(self.store.query, "SELECT id FROM print_jobs WHERE status='printing' AND claimed_by=?",
                              (self.node_id,))
        stuck = sorted({r["id"] for r in rows} - self._inflight)
        if not stuck:
            return
        sql = ("UPDATE print_jobs SET status='uncertain', last_error=?, error_code='uncertain' "
               f"WHERE status='printing' AND claimed_by=? AND id IN ({','.join('?' * len(stuck))})")
        mine = sorted(self._inflight)                     # inzwischen begonnen: in Ruhe lassen
        if mine:
            sql += f" AND id NOT IN ({','.join('?' * len(mine))})"
        res = await self._db(self.store.execute, sql, (t("srv.job.uncertain"), self.node_id, *stuck, *mine))
        if res.rows_affected:
            log.warning("[%s] %d hängende(r) Job(s) als 'uncertain' markiert", self.node_id, res.rows_affected)

    async def _on_promote(self) -> None:
        """Neu Leader: Jobs, die ein ANDERER (oder ein früherer) Druckprozess mitten im Senden verlassen hat,
        werden 'uncertain'. Was dieser Prozess gerade selbst sendet, bleibt unberührt."""
        mine = sorted(self._inflight)
        sql = "UPDATE print_jobs SET status='uncertain', last_error=?, error_code='uncertain' WHERE status='printing'"
        if mine:
            sql += f" AND id NOT IN ({','.join('?' * len(mine))})"
        res = await self._db(self.store.execute, sql, (t("srv.job.leader_change"), *mine))
        if res.rows_affected:
            log.warning("[%s] %d Job(s) als 'uncertain' markiert", self.node_id, res.rows_affected)
        printers = await self._db(self.store.query, "SELECT id FROM printers")
        self.is_leader = True                           # erst wenn alles geklappt hat
        log.info("[%s] ist Leader – Druck aktiv", self.node_id)
        for row in printers:
            self.notify(row["id"])

    def notify(self, printer_id: int) -> None:
        """Neue Jobs oder Konfig-Änderung: Worker wecken bzw. starten (nur als Leader)."""
        queue_status_forget()
        ev = self._events.setdefault(printer_id, asyncio.Event())
        ev.set()
        if not self.is_leader or self._stopping:
            return
        t = self._workers.get(printer_id)
        if t is None or t.done():
            self._workers[printer_id] = asyncio.create_task(self._worker(printer_id),
                                                            name=f"printer-{printer_id}")

    async def _wait(self, printer_id: int, timeout: float) -> None:
        ev = self._events[printer_id]
        try:
            await asyncio.wait_for(ev.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        ev.clear()

    # -- Worker -----------------------------------------------------------
    async def _worker(self, printer_id: int) -> None:
        fails = 0
        while self.is_leader and not self._stopping:
            try:
                fails = await self._step(printer_id, fails)
                queue_status_forget()
            except StoreError as e:                      # Mehrheit kurz weg o.ä.
                log.warning("[%s] DB-Fehler im Druck-Worker: %s", self.node_id, e)
                await self._wait(printer_id, 1.0)
            except Exception:
                log.exception("[%s] Unerwarteter Fehler im Druck-Worker %s", self.node_id, printer_id)
                await self._wait(printer_id, 2.0)

    async def _step(self, printer_id: int, fails: int) -> int:
        q = self.store.query_one
        prow = await self._db(q, "SELECT * FROM printers WHERE id=?", (printer_id,))
        if prow is None:                                  # Drucker gelöscht
            await self._wait(printer_id, self.idle_poll)
            return 0
        printer = PrinterCfg.from_row(prow)
        job = await self._db(q, "SELECT * FROM print_jobs WHERE printer_id=? AND status='pending' "
                                "ORDER BY id LIMIT 1", (printer_id,))
        if job is None or not printer.enabled:
            await self._wait(printer_id, self.idle_poll)
            return 0

        # Rendern vor dem Reservieren: Renderfehler blockieren nicht die Schlange
        try:
            printers = await self._db(load_printers, self.store)
            payload = await self._db(render_job, self.store, job, printers)
        except StoreError:
            raise
        except Exception as e:
            log.exception("Job %d nicht renderbar", job["id"])
            await self._db(self.store.execute,
                           "UPDATE print_jobs SET status='failed', last_error=?, error_code='render' "
                           "WHERE id=? AND status='pending'",
                           (t("srv.job.render_failed", error=e), job["id"]))
            return fails

        # Compare-and-Set: nur wenn noch 'pending', noch für diesen Drucker UND wir sicher Leader sind.
        # "Unbekannt" heisst hier: jetzt nicht drucken, gleich nochmals schauen (die Rolle gibt nur die
        # Leader-Überwachung ab, damit ein einzelner langsamer Statusaufruf nichts durcheinanderbringt).
        if not self.is_leader:
            return fails
        state = await self._db(self.store.leader_state)
        if state is not True:
            await self._wait(printer_id, 1.0)
            return fails
        self._inflight.add(job["id"])                     # VOR dem Reservieren: die Aufräumrunde lässt ihn in Ruhe
        try:
            claim = await self._db(self.store.execute,
                "UPDATE print_jobs SET status='printing', attempts=attempts+1, claimed_by=?, claimed_at=? "
                "WHERE id=? AND status='pending' AND printer_id=?", (self.node_id, utc_iso(), job["id"], printer_id))
        except BaseException:
            self._inflight.discard(job["id"])
            raise
        if claim.rows_affected != 1:
            self._inflight.discard(job["id"])
            return fails                                  # anderer Knoten / Admin / Umleitung war schneller

        try:
            answered = await send_raw(PrinterAddr(printer.host, printer.port), payload,
                                      require_status=bool(prow.get("status_seen")))
        except PrinterError as e:
            self._inflight.discard(job["id"])
            self._reason[printer_id] = str(e)
            self._reason_code[printer_id] = e.code
            fails += 1
            if e.sent:                                    # mitten im Senden abgebrochen: evtl. (halb) gedruckt –
                await self._db(self.store.execute,        # nicht blind nochmals, die Verwaltung entscheidet
                    "UPDATE print_jobs SET status='uncertain', last_error=?, error_code='uncertain', claimed_by=NULL "
                    "WHERE id=? AND status='printing' AND claimed_by=?",
                    (t("srv.job.send_aborted", error=str(e)), job["id"], self.node_id))
            else:
                await self._db(self.store.execute,
                    "UPDATE print_jobs SET status='pending', last_error=?, error_code=?, claimed_by=NULL "
                    "WHERE id=? AND status='printing' AND claimed_by=?", (str(e), e.code, job["id"], self.node_id))
            log.warning("[%s] %s: Job %d Versuch %d: %s", self.node_id, printer.name, job["id"],
                        job["attempts"] + 1, e)
            await self._failover(prow)
            await self._wait(printer_id, self.backoff[min(fails, len(self.backoff)) - 1])
            return fails
        except BaseException:                             # Abbruch (Dienst stoppt) o.ä.
            self._inflight.discard(job["id"])
            raise

        # Gedruckt. Erfolg festhalten – bei DB-Problemen mehrfach versuchen; scheitert es endgültig,
        # bleibt der Job 'printing' und wird beim nächsten Leader 'uncertain' (nie doppelt gedruckt).
        try:
            for attempt in range(5):
                try:
                    await self._db(self.store.execute,
                        "UPDATE print_jobs SET status='done', last_error=NULL, error_code=NULL, printed_at=? "
                        "WHERE id=? AND status='printing' AND claimed_by=?", (now_iso(), job["id"], self.node_id))
                    break
                except StoreError:
                    await asyncio.sleep(0.5 * (attempt + 1))
        finally:
            self._inflight.discard(job["id"])
        log.info("[%s] %s: Job %d gedruckt", self.node_id, printer.name, job["id"])
        if answered and not prow.get("status_seen"):
            await self._db(mark_status_seen, self.store, printer_id)
        self._down.pop(printer_id, None)
        self._warned.discard(printer_id)
        self._reason.pop(printer_id, None)
        self._reason_code.pop(printer_id, None)
        return 0

    def is_down(self, printer_id: int) -> bool:
        return printer_id in self._down

    def mark_ok(self, printer_id: int) -> None:
        """Statusabfrage (Überwachung) meldet den Drucker wieder bereit – z.B. Papier nachgefüllt, während
        seine Bons umgeleitet wurden und er selbst nichts zu drucken hatte."""
        if printer_id in self._down:
            log.info("[%s] Drucker %s meldet sich wieder bereit", self.node_id, printer_id)
        self._down.pop(printer_id, None)
        self._warned.discard(printer_id)
        self._reason.pop(printer_id, None)
        self._reason_code.pop(printer_id, None)

    def down_reason(self, printer_id: int) -> str | None:
        """Warum der Drucker gestört ist (z.B. "Papier leer"), solange er als gestört gilt."""
        return self._reason.get(printer_id) if printer_id in self._down else None

    def down_code(self, printer_id: int) -> str | None:
        """Dasselbe als Code (unreachable, paper_out, cover_open, printer_error, no_status) – Text ist übersetzt."""
        return self._reason_code.get(printer_id, "unreachable") if printer_id in self._down else None

    async def _failover(self, prow: dict) -> None:
        """Drucker nicht erreichbar: nach fail_after Sekunden je nach Einstellung umleiten oder warnen."""
        pid = prow["id"]
        since, since_hm = self._down.setdefault(pid, (time.monotonic(), datetime.now().strftime("%H:%M")))
        mode, target = prow.get("fail_mode") or "wait", prow.get("fail_target")
        if mode not in ("reroute", "warn") or not target or target == pid:
            return
        after = prow.get("fail_after")
        if time.monotonic() - since < (60 if after is None else after):
            return
        if target in self._down:                          # Ziel selbst gestört: nicht im Kreis schicken
            return
        tgt = await self._db(self.store.query_one, "SELECT id, enabled FROM printers WHERE id=?", (target,))
        if not tgt or not tgt["enabled"]:
            return
        code = self._reason_code.get(pid, "unreachable")
        what = t(WARN_WHAT.get(code, "bon.warn.what_unreachable"))
        if mode == "reroute":
            r = await self._db(self.store.execute,
                "UPDATE print_jobs SET printer_id=?, last_error=?, error_code='rerouted' "
                "WHERE printer_id=? AND status='pending'",
                (target, t("srv.job.rerouted", printer=prow["name"], what=what), pid))
            if r.rows_affected:
                log.warning("[%s] %s nicht erreichbar seit %s – %d Bon(s) umgeleitet", self.node_id,
                            prow["name"], since_hm, r.rows_affected)
                self.notify(target)
        elif pid not in self._warned:
            waiting = await self._db(self.store.scalar,
                "SELECT COUNT(*) FROM print_jobs WHERE printer_id=? AND status='pending'", (pid,))
            await self._db(self.store.execute,
                "INSERT INTO print_jobs(station_id, printer_id, kind, note, created_at) VALUES (?,?,'warn',?,?)",
                (target, target, warn_note(prow["name"], since_hm, code, int(waiting or 0)), now_iso()))
            self._warned.add(pid)
            log.warning("[%s] %s nicht erreichbar – Warnbon an Drucker %s", self.node_id, prow["name"], target)
            self.notify(target)


# --------------------------------------------------------------------------
# Admin-Aktionen
# --------------------------------------------------------------------------

def enqueue_test(store: Store, printer_id: int) -> int:
    return store.execute(
        "INSERT INTO print_jobs(station_id, printer_id, kind, created_at) VALUES (?,?,'test',?)",
        (printer_id, printer_id, now_iso())).last_id


def enqueue_table_qr(store: Store, printer_id: int, labels: list[str]) -> int:
    ts = now_iso()
    store.batch([("INSERT INTO print_jobs(station_id, printer_id, kind, note, created_at) VALUES (?,?,'qr',?,?)",
                  (printer_id, printer_id, l, ts)) for l in labels])
    return len(labels)


# Ein Auftrag in «druckt» gilt nach so vielen Sekunden als hängengeblieben (Senden dauert höchstens ~15 s):
# dann darf ihn die Verwaltung nachdrucken oder als erledigt markieren (REV-016).
STUCK_AFTER_S = 120
_OPEN = "status IN ('failed','uncertain','pending')"
# claimed_at in UTC («…Z», springt bei der Zeitumstellung nicht); ältere Einträge in Ortszeit ohne «Z»
_STUCK = ("(status='printing' AND (claimed_at IS NULL OR (claimed_at LIKE '%Z' AND claimed_at < ?) "
          "OR (claimed_at NOT LIKE '%Z' AND claimed_at < ?)))")


class JobBusy(Exception):
    """Der Auftrag wird gerade gesendet – Nachdruck/Erledigt erst, wenn er fertig ist oder hängt."""


def utc_iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _stuck_cutoff() -> tuple[str, str]:
    """Grenze für hängende Aufträge: (UTC mit «Z», Ortszeit für ältere Einträge)."""
    t0 = time.time() - STUCK_AFTER_S
    return utc_iso(t0), datetime.fromtimestamp(t0).strftime("%Y-%m-%dT%H:%M:%S")


def reprint(store: Store, job_id: int) -> int | None:
    """Nachdruck als NEUER Job (Original bleibt für die Nachvollziehbarkeit).
    Offene, unklare, fehlgeschlagene und hängende Originale werden dabei storniert – in EINER Transaktion,
    damit ein Original, das der Druckdienst gerade reserviert, nicht zusätzlich gedruckt wird."""
    res = store.batch([
        (f"UPDATE print_jobs SET status='cancelled', last_error=?, error_code='reprinted' "
         f"WHERE id=? AND ({_OPEN} OR {_STUCK})", (t("srv.job.reprinted"), job_id, *_stuck_cutoff())),
        ("INSERT INTO print_jobs(order_id, station_id, printer_id, kind, item_id, n, of_n, note, delivery, created_at, reprint) "
         "SELECT order_id, station_id, printer_id, kind, item_id, n, of_n, note, delivery, ?, 1 FROM print_jobs "
         "WHERE id=? AND status IN ('done','cancelled')",
         (now_iso(), job_id)),
    ])
    if res[1].rows_affected:
        return res[1].last_id
    if store.scalar("SELECT status FROM print_jobs WHERE id=?", (job_id,)) == "printing":
        raise JobBusy(t("err.job.busy_reprint", minutes=STUCK_AFTER_S // 60))
    return None


def resolve(store: Store, job_id: int) -> bool:
    """Offen/unklar/fehlgeschlagen/hängend als erledigt markieren (Bon war doch da)."""
    n = store.execute(f"UPDATE print_jobs SET status='cancelled', last_error=?, error_code='resolved' "
                      f"WHERE id=? AND ({_OPEN} OR {_STUCK})", (t("srv.job.resolved"), job_id, *_stuck_cutoff())).rows_affected
    if not n and store.scalar("SELECT status FROM print_jobs WHERE id=?", (job_id,)) == "printing":
        raise JobBusy(t("err.job.busy_resolve", minutes=STUCK_AFTER_S // 60))
    return n == 1


def open_jobs(store: Store, printer_id: int) -> int:
    """Aufträge, die beim Löschen des Druckers verloren gingen (als Ziel ODER als Station)."""
    return int(store.scalar("SELECT COUNT(*) FROM print_jobs WHERE (printer_id=? OR station_id=?) "
                            "AND status IN ('pending','printing','failed','uncertain')",
                            (printer_id, printer_id)) or 0)


def reroute_pending(store: Store, from_printer: int, to_printer: int) -> int:
    """Notfall: offene Bons eines defekten Druckers auf einen anderen umleiten."""
    return store.execute("UPDATE print_jobs SET printer_id=?, status='pending', claimed_by=NULL, last_error=NULL, "
                         "error_code=NULL "
                         "WHERE printer_id=? AND status IN ('pending','failed')",
                         (to_printer, from_printer)).rows_affected
