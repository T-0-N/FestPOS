"""T24 Bon-Baukasten: Übernahme der alten Form. Die Bons von vor dem Umbau (tests/bon_v1_golden.json, erzeugt mit
tests/bon_golden.py vor dem Umbau) müssen Byte für Byte gleich bleiben – für Quittungen (auch ältere mit Logo und
KOPIE), Sammel-, Einzel- und Storno-Bons, 80 und 58 mm. Ebenso, wenn die alte Einstellung (Version 1) gespeichert
ist und über die Bausteine gedruckt wird."""
from __future__ import annotations

import base64
import json

import pytest

from tests.bon_golden import GOLDEN, ITEMS, RECEIPT_V1, TICKET_V1, _dev, _rec, scenarios

GOLD = json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(GOLD))
def test_same_bytes_as_before(name):
    assert scenarios()[name]() == base64.b64decode(GOLD[name]), name


class _Store:
    """Nur die Einstellungen (load/ticket/receipt_snapshot brauchen nicht mehr)."""

    def __init__(self, **kv):
        self.kv = kv

    def setting(self, key, default=None):
        return self.kv.get(key, default)


@pytest.mark.parametrize("v1", [TICKET_V1, {**TICKET_V1, "table_size": "huge", "spacing": "wide", "beep": True},
                                {**TICKET_V1, "categories": False, "time_device": False, "spacing": "compact"}])
@pytest.mark.parametrize("w", [48, 32])
def test_saved_v1_ticket_setting_prints_the_same(v1, w):
    """Gespeicherte Einstellung der alten Form -> Bausteine (app/bon_design.ticket) -> gleiche Druckerdaten."""
    from app import bon_design as bd, print_service as ps
    st = _Store(bon_design=json.dumps({"receipt": RECEIPT_V1, "ticket": v1}))
    common = dict(event_name="Vereinsfest", currency="CHF", table="12", order_id=42, created_at="2026-11-02T19:30:00",
                  device_name="XCover 1 (Anna)", items=ITEMS)
    for kw in ({}, {"delivery": "Pause"}, {"n": 1, "of": 2}):
        old = ps.render_table_bon(_dev(w), _dev(w), design=v1, **common, **kw)
        assert ps.render_table_bon(_dev(w), _dev(w), design=bd.ticket(st), now_label="Sofort", **common, **kw) == old
    old = ps.render_single_bon(_dev(w), _dev(w), design=v1, item=ITEMS[2], n=1, of=1, **{k: v for k, v in common.items()
                                                                                          if k != "items"})
    new = ps.render_single_bon(_dev(w), _dev(w), design=bd.ticket(st), item=ITEMS[2], n=1, of=1,
                               **{k: v for k, v in common.items() if k != "items"})
    assert new == old


@pytest.mark.parametrize("v1", [RECEIPT_V1, {**RECEIPT_V1, "head_big": False, "show_device": False,
                                             "qr": "https://example.org", "qr_text": "Spende"}])
def test_saved_v1_receipt_setting_prints_the_same(v1):
    """Neue Quittung nach dem Update (Gestaltung in neuer Form festgehalten) = Quittung mit der alten Form."""
    from app import bon_design as bd, print_service as ps
    st = _Store(bon_design=json.dumps({"receipt": v1, "ticket": TICKET_V1}))
    snap = bd.receipt_snapshot(st)
    assert snap["version"] == 2
    for kw in ({}, {"vat": True}, {"test": 1}):
        for copy in (False, True):
            old = ps.render_receipt(_dev(48), _rec(dict(v1), **kw), copy=copy)
            assert ps.render_receipt(_dev(48), _rec(snap, **kw), copy=copy) == old


def test_templates_and_clean():
    from app import bon_design as bd
    for kind in bd.KINDS:
        core = {typ for typ, s in bd.SCHEMA[kind].items() if not s["add"]}
        assert {b["type"] for b in bd.clean(kind, [])} == core          # fehlende Bausteine werden ergänzt
        for name in bd.TEMPLATES[kind]:
            blocks = bd.clean(kind, bd.template_blocks(kind, name))
            assert bd.clean(kind, blocks) == blocks                          # Vorlagen sind gültig
            assert blocks[0]["type"] == "top"
    rec = bd.clean("receipt", [{"type": "check"}, {"type": "header", "text": "a\nb\nc\nd\ne\nf\ng"}, {"type": "top"}])
    assert [b["type"] for b in rec][0] == "top" and rec[-1]["type"] == "check"
    assert all(b["on"] for b in rec if bd.SCHEMA["receipt"][b["type"]]["req"])
    assert next(b for b in rec if b["type"] == "header")["text"].count("\n") == 5      # höchstens 6 Zeilen
    with pytest.raises(bd.DesignError):
        bd.clean("receipt", [{"type": "vat", "on": False}])
    with pytest.raises(bd.DesignError):
        bd.clean("ticket", [{"type": "image", "image": "../etc/passwd"}])
    # still korrigieren (gespeicherte Einstellung lesen): nichts geht verloren, nichts Ungültiges bleibt
    fixed = bd.clean("ticket", [{"type": "table", "size": "9", "on": False}, {"type": "nope"}], strict=False)
    t = next(b for b in fixed if b["type"] == "table")
    assert t["size"] == "2" and t["on"] is True and "nope" not in [b["type"] for b in fixed]
