"""Referenz-Bons für T24 (Bon-Baukasten): dieselben Aufrufe wie vor dem Umbau, mit der alten Form des Bon-Designs
(Version 1). Die Druckerdaten von damals stehen in tests/bon_v1_golden.json – nach dem Umbau müssen dieselben Aufrufe
Byte für Byte dasselbe liefern (tests/test_bon_blocks.py).

Neu erzeugen (nur wenn sich ein Bon absichtlich ändert):  python3 -m tests.bon_golden
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

GOLDEN = Path(__file__).with_name("bon_v1_golden.json")

RECEIPT_V1 = {"logo": False, "head_big": True, "show_table": True, "show_order": True, "show_device": True,
              "qr": "", "qr_text": ""}
TICKET_V1 = {"logo": False, "table_size": "big", "categories": True, "time_device": True, "spacing": "normal",
             "beep": False}


def logo():
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (300, 120), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle((10, 10, 290, 110), fill="black")
    ImageDraw.Draw(im).ellipse((60, 30, 140, 90), fill="white")
    return im


def _dev(width: int, pid: int = 1, name: str = "Küche"):
    from app import print_service as ps
    return ps.PrinterCfg(pid, name, "x", 9100, width, True, True)


def _rec(design: dict | None, *, vat: bool = False, test: int = 0, orders=None, recipient=None, part=None,
         footer=("Merci und bis bald!",), header=("Verein Muster", "Fest 2026", "6000 Luzern")):
    data = {"header": list(header), "footer": list(footer), "currency": "CHF", "table": "12", "table_word": "Tisch",
            "order_no": 42, "device": "XCover 1 (Anna)", "payment": "cash", "part": part, "recipient": recipient,
            "vat": vat, "vat_number": "CHE-123.456.789 MWST" if vat else None}
    if orders:
        data["orders"] = orders
    if design is not None:
        data["design"] = design
    lines = [{"line_no": 1, "name": "Bier 5dl", "qty": 2, "price": 650, "amount": 1300},
             {"line_no": 2, "name": "Bratwurst", "options": "mit Senf", "qty": 1, "price": 800, "amount": 800}]
    if vat:
        lines[0].update(vat_code="normal", vat_bp=810)
        lines[1].update(vat_code="reduced", vat_bp=260)
    return {"id": 7, "no": 123 if not test else None, "order_id": 42, "created_at": "2026-11-02T19:30:00",
            "total_cents": 2103, "data": json.dumps(data), "lines": json.dumps(lines), "digest": "h2:1a2b3c4d5e6f",
            "test": test}


ITEMS = [{"qty": 2, "name": "Bier 5dl", "price_cents": 650, "category": "Getränke"},
         {"qty": 1, "name": "Mineral", "price_cents": 400, "category": "Getränke", "note": "ohne Eis"},
         {"qty": 1, "name": "Bratwurst mit einem sehr langen Namen für schmale Drucker", "price_cents": 800,
          "category": "Essen", "options": "mit Senf", "cancelled": 0}]


def scenarios() -> dict:
    """Name -> Funktion ohne Argumente, die die Druckerdaten liefert."""
    from app import print_service as ps
    out = {}
    lg = logo()
    common = dict(event_name="Vereinsfest", currency="CHF", table="12", order_id=42,
                  created_at="2026-11-02T19:30:00", device_name="XCover 1 (Anna)")
    gone = [dict(ITEMS[0], cancelled=1), dict(ITEMS[1], cancelled=1)]
    nocat = [{k: v for k, v in i.items() if k != "category"} for i in ITEMS]
    for w in (48, 32):
        # Quittungen: ohne Gestaltung (ganz alt), mit Gestaltung Version 1 in verschiedenen Varianten
        rv = {
            "none": (None, {}),
            "default": (dict(RECEIPT_V1), {}),
            "small_head": ({**RECEIPT_V1, "head_big": False, "show_table": False, "show_order": False,
                            "show_device": False}, {}),
            "qr": ({**RECEIPT_V1, "qr": "https://example.org/spende", "qr_text": "Danke fürs Spenden"}, {}),
            "vat": (dict(RECEIPT_V1), {"vat": True}),
            "vat_guest": (dict(RECEIPT_V1), {"vat": True, "recipient": ["Muster AG", "Bahnhofstr. 1, 6000 Luzern"],
                                             "part": 2}),
            "tab": ({**RECEIPT_V1, "show_device": False}, {"orders": [41, 42, 43]}),
            "test": (dict(RECEIPT_V1), {"test": 1}),
            "nofoot": (dict(RECEIPT_V1), {"footer": ()}),
        }
        for name, (design, kw) in rv.items():
            for copy in (False, True):
                out[f"receipt-{name}-{'copy' if copy else 'orig'}-{w}"] = (
                    lambda design=design, kw=kw, copy=copy, w=w: ps.render_receipt(_dev(w), _rec(design, **kw), copy=copy))
        # Quittung mit Logo (Kopie: Logo über KOPIE, wie bisher)
        for copy in (False, True):
            out[f"receipt-logo-{'copy' if copy else 'orig'}-{w}"] = (
                lambda copy=copy, w=w: ps.render_receipt(_dev(w), _rec({**RECEIPT_V1, "logo": "abc123"}), copy=copy,
                                                         logo=lg))
        # Küchen- und Getränkebons
        tv = {
            "none": (None, {}),
            "default": (dict(TICKET_V1), {}),
            "huge": ({**TICKET_V1, "table_size": "huge"}, {}),
            "compact": ({**TICKET_V1, "spacing": "compact", "categories": False}, {}),
            "wide": ({**TICKET_V1, "spacing": "wide", "time_device": False, "beep": True}, {}),
            "delivery": (dict(TICKET_V1), {"delivery": "Pause"}),
            "part": (dict(TICKET_V1), {"n": 2, "of": 3}),
            "longtable": (dict(TICKET_V1), {"table": "Terrasse hinten links", "n": 1, "of": 2}),
            "reprint": (dict(TICKET_V1), {"reprint": True}),
            "test": (dict(TICKET_V1), {"test": True}),
            "seat": (dict(TICKET_V1), {"table_word": "Platz"}),
        }
        for name, (design, kw) in tv.items():
            out[f"table-{name}-{w}"] = (lambda design=design, kw=kw, w=w: ps.render_table_bon(
                _dev(w), _dev(w), items=ITEMS, design=design, **{**common, **kw}))
        out[f"table-logo-{w}"] = (lambda w=w: ps.render_table_bon(_dev(w), _dev(w), items=ITEMS,
                                                                  design={**TICKET_V1, "logo": "abc"}, logo=lg, **common))
        out[f"table-gone-{w}"] = (lambda w=w: ps.render_table_bon(_dev(w), _dev(w), items=gone, design=dict(TICKET_V1),
                                                                  **common))
        out[f"table-nocat-{w}"] = (lambda w=w: ps.render_table_bon(_dev(w), _dev(w), items=nocat, design=dict(TICKET_V1),
                                                                   **common))
        for name, (design, kw) in {"default": (dict(TICKET_V1), {}), "huge": ({**TICKET_V1, "table_size": "huge"}, {}),
                                   "delivery": (dict(TICKET_V1), {"delivery": "Schluss"}),
                                   "cancelled": (dict(TICKET_V1), {"item": dict(ITEMS[0], cancelled=1)}),
                                   "allgone": (dict(TICKET_V1), {"item": dict(ITEMS[1], cancelled=1)})}.items():
            out[f"single-{name}-{w}"] = (lambda design=design, kw=kw, w=w: ps.render_single_bon(
                _dev(w), _dev(w), design=design, n=1, of=2, **{"item": ITEMS[0], **common, **kw}))
        # Storno-Bon: ohne Gestaltung (bleibt fest)
        for test in (False, True):
            out[f"storno-{'test' if test else 'normal'}-{w}"] = (lambda test=test, w=w: ps.render_storno_bon(
                _dev(w), _dev(w), event_name="Vereinsfest", table="12", order_id=42, created_at="2026-11-02T19:30:00",
                by="XCover 1", reason="Gast will nicht mehr", items=ITEMS[:2], test=test))
    return out


def generate() -> dict[str, str]:
    return {name: base64.b64encode(fn()).decode() for name, fn in scenarios().items()}


if __name__ == "__main__":
    GOLDEN.write_text(json.dumps(generate(), indent=0, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{GOLDEN}: {len(json.loads(GOLDEN.read_text()))} Bons")
