"""T44: Review des Stands 2026.10.10-81 – die Gegenproben als bleibende Tests: unklare NFC-Zahlung startet beim
Wiederöffnen nicht von selbst, hängender QR-Vorgang liefert nach Rückerstattung nicht mehr, Tagesabschluss mit offener
Rückerstattung, Doppelbelastung bleibt im Protokoll, Transaktionsnummern exakt verglichen."""
from __future__ import annotations

import json

import pytest

from tests.test_api import AUTH, setup_shop
from tests.test_gast import _order, _products, gast  # noqa: F401 – Fixture
from tests.test_gast_refunds import _paid_order
from tests.test_gast_t38 import _ago
from tests.test_payrexx import _enable, _extras, _order as px_order, _result, _start_pay, pay  # noqa: F401 – Fixture
from tests.test_tabs import srv  # noqa: F401 – Fixture


def test_closing_counts_promised_refunds(client, gast):  # noqa: F811
    """Befund 3: Storno, Rückerstattung offen (Payrexx weg), Abschluss, später zurück – der Abschluss stimmt."""
    from app import core, gast as g, ledger
    _o, gid, oid, beer = _paid_order(client, gast)
    gast.refund_fails, gast.refund_fail_code = True, 400                     # Payrexx lehnt vorerst ab
    ledger.cancel(core.rt.store, order_id=oid, lines=None, reason="wrong", client_uuid="t44-refund-1", by_user="admin")
    g.process_refunds(gid)
    assert core.rt.store.scalar("SELECT refund_open FROM gast_orders WHERE id=?", (gid,)) == 2 * beer["price"]
    c = ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn())
    assert c["summary"]["payrexx"]["cents"] == 0 and c["summary"]["net"] == 0
    gast.refund_fails = False
    client.post(f"/api/admin/gast/orders/{gid}/refund", json={}, auth=AUTH)
    saved = json.loads(core.rt.store.scalar("SELECT summary FROM closings WHERE id=?", (c["id"],)))
    assert saved["payrexx"]["cents"] == 0
    assert core.rt.store.scalar("SELECT refunded_cents FROM payrexx_payments WHERE order_id=?", (oid,)) == 2 * beer["price"]


def _paid_nfc(client):
    from app import payrexx as p
    setup_shop(client)
    _enable(client)
    cu = px_order(client)
    a = p.start(cu, 1300, "all", "t44-key", "Phone")
    ref = f"FestPOS-{a['order_id']:04d}-{a['id']}"
    p.tap_result(a["id"], a["link"], True, _result("13.00", ref, tx="TX-first"), None)
    return a, ref


def test_double_notice_survives_lost_response(client, monkeypatch):
    """Befund 4: Vermerk gespeichert, Antwort verloren – das Protokoll ist trotzdem da (gleicher Batch)."""
    from app import core, payrexx as p
    from app.store import StoreUnclear
    a, ref = _paid_nfc(client)
    real = core.rt.store.batch

    def lost(stmts, *args, **kw):
        res = real(stmts, *args, **kw)
        if stmts and stmts[0][0].startswith("UPDATE payrexx_payments SET extra_tx"):
            raise StoreUnclear("gespeichert, Antwort verloren")
        return res
    monkeypatch.setattr(core.rt.store, "batch", lost)
    with pytest.raises(StoreUnclear):
        p.tap_result(a["id"], a["link"], True, _result("13.00", ref, tx="TX-second"), None)
    monkeypatch.setattr(core.rt.store, "batch", real)
    p.tap_result(a["id"], a["link"], True, _result("13.00", ref, tx="TX-second"), None)
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='payrexx_double'") == 1


def test_transaction_ids_compared_exactly(client):
    """Befund 5: TX-123 ist nicht dasselbe wie TX-12345."""
    from app import core, payrexx as p
    a, ref = _paid_nfc(client)
    for tx in ("TX-12345", "TX-123", "TX-123"):
        p.tap_result(a["id"], a["link"], True, _result("13.00", ref, tx=tx), None)
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='payrexx_double'") == 2
    assert core.rt.store.scalar("SELECT extra_tx FROM payrexx_payments WHERE id=?", (a["id"],)) == "TX-12345 TX-123"


def test_stalled_qr_worker_does_not_deliver_refunded_order(client, gast, monkeypatch):  # noqa: F811
    """Befund 2: paid() hängt vor dem Anlegen, tick() lehnt ab und erstattet zurück, dann läuft paid() weiter –
    keine Bestellung, keine Bons, Rückerstattung bleibt."""
    from app import core, gast as g
    beer = next(iter(_products(client).values()))
    o = _order(client, [{"product_id": beer["id"], "qty": 2}]).json()
    row = core.rt.store.query_one("SELECT * FROM gast_orders WHERE token=?", (o["token"],))
    real = g.create_order

    def resume_after_recovery(*args, **kw):
        core.rt.store.execute("UPDATE gast_orders SET paid_at=? WHERE id=?", (_ago(3), row["id"]))
        g.tick()
        assert gast.refunds == [("7001", 2 * beer["price"])]
        return real(*args, **kw)
    monkeypatch.setattr(g, "create_order", resume_after_recovery)
    g.paid(row, "7001", "twint")
    assert core.rt.store.scalar("SELECT COUNT(*) FROM orders") == 0
    assert core.rt.store.scalar("SELECT COUNT(*) FROM print_jobs") == 0
    assert core.rt.store.query_one("SELECT status, order_id FROM gast_orders WHERE id=?", (row["id"],)) == \
        {"status": "rejected", "order_id": None}
    assert core.rt.store.scalar("SELECT COUNT(*) FROM payrexx_payments") == 0


def test_reject_never_refunds_an_order_that_exists(client, gast):  # noqa: F811
    """Befund 2, andere Reihenfolge: die Bestellung kam eben doch – die Ablehnung ändert nichts, kein Geld zurück."""
    from app import core, gast as g
    _o, gid, _oid, _beer = _paid_order(client, gast)
    row = core.rt.store.query_one("SELECT * FROM gast_orders WHERE id=?", (gid,))
    g._reject({**row, "status": "paid"}, row["transaction_id"], "order", "veralteter Stand")
    assert core.rt.store.scalar("SELECT status FROM gast_orders WHERE id=?", (gid,)) == "ordered"
    assert gast.refunds == [] and core.rt.store.scalar("SELECT COUNT(*) FROM gast_refunds") == 0


def test_reopen_unclear_does_not_offer_launch(client, pay):  # noqa: F811
    """Befund 1: unklare Zahlung erneut geöffnet – keine Startadresse, kein automatischer Start."""
    from app import core, payrexx as p
    _enable(client)
    cu = px_order(client)
    first = _start_pay(client, cu, 1300, key="t44-initial-key")
    assert first["autostart"] is True and first["launch"]                   # eben angelegt: startet
    p.tap_result(first["id"], _extras(first["launch"])["token"], True, _result("unknown", None), None)
    assert core.rt.store.scalar("SELECT error FROM payrexx_payments WHERE id=?", (first["id"],)) == "unclear"
    reopened = _start_pay(client, cu, 1300, key="t44-reopen-key")
    assert reopened["id"] == first["id"] and reopened["error"] == "unclear"
    assert not reopened.get("launch") and reopened["autostart"] is False


def test_reopen_waiting_shows_but_does_not_autostart(client, pay):  # noqa: F811
    """Eine bestehende offene Zahlung (anderer Schlüssel) wird angezeigt – «Nochmals» bleibt möglich, startet aber
    nicht von selbst; derselbe Schlüssel nochmals (Antwort verloren) startet wie beim ersten Mal."""
    _enable(client)
    cu = px_order(client)
    first = _start_pay(client, cu, 1300, key="t44-wait-1")
    again = _start_pay(client, cu, 1300, key="t44-wait-1")
    assert again["autostart"] is True
    other = _start_pay(client, cu, 1300, key="t44-wait-2")
    assert other["id"] == first["id"] and other["launch"] and other["autostart"] is False


def test_double_reaches_the_starting_phone_later(srv):  # noqa: F811
    """CodeRabbit PR #66: kommt die zweite Belastung erst nach «Bezahlt ✓», meldet /api/phone/status sie dem
    startenden Handy (und nur diesem)."""
    import uuid
    from tests.test_tabs import H, _reset
    c = srv
    _reset(c, "now")
    c.put("/api/admin/payrexx", auth=AUTH, json={"enabled": True})
    pid = {p["name"]: p["id"] for p in c.get("/api/admin/products", auth=AUTH).json()}["Bier 5dl"]
    cu = str(uuid.uuid4())
    c.post("/api/orders", headers=H("XCover 1"), json={"client_uuid": cu, "table": "64",
                                                       "lines": [{"product_id": pid, "qty": 1}]})
    p = c.post("/api/phone/payrexx", headers=H("XCover 1"),
               json={"client_uuid": cu, "amount_cents": 650, "part": "all", "key": "t44-phone-1"}).json()
    x = _extras(p["launch"])
    for tx in ("TX-a", "TX-b"):
        c.post("/api/tap/result", json={"id": p["id"], "token": x["token"], "ok": True,
                                        "result": _result("6.50", x["ref"], tx=tx)})
    mine = c.get("/api/phone/status", headers=H("XCover 1")).json()["payrexx_double"]
    assert [d["tx"] for d in mine if d["id"] == p["id"]] == ["TX-b"]
    assert c.get("/api/phone/status", headers=H("XCover 2")).json()["payrexx_double"] == []


def test_double_window_counts_from_the_double(client):
    """CodeRabbit PR #66: das 24-h-Fenster zählt ab der Meldung der zweiten Belastung, nicht ab der Zahlung."""
    from app import core, payrexx as p
    a, ref = _paid_nfc(client)
    core.rt.store.execute("UPDATE payrexx_payments SET paid_at='2020-01-01T10:00:00', device_id='dev-x' WHERE id=?",
                         (a["id"],))
    p.tap_result(a["id"], a["link"], True, _result("13.00", ref, tx="TX-late"), None)
    from app import tabs
    orig = tabs.owner_of
    try:
        tabs.owner_of = lambda store, cert: "dev-x"
        assert [d["tx"] for d in p.doubles_for(core.rt.store, {"serial": "x"})] == ["TX-late"]
    finally:
        tabs.owner_of = orig
