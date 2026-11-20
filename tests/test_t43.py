"""T43: Nachreview der Zahlungs-Fixes – die Gegenproben des Reviews (10.11.2026) als bleibende Tests, angepasst, wo
das Verhalten bewusst anders ist (fremde Zahlung: 409 statt 403; QR-Abschluss im selben Batch wie die Bestellung)."""
from __future__ import annotations

import io
import json
import urllib.error
import uuid

import pytest

from tests.test_api import AUTH, setup_shop
from tests.test_gast import G, _order, _products, gast  # noqa: F401 – Fixture
from tests.test_gast_refunds import _paid_order
from tests.test_gast_t38 import _ago
from tests.test_payrexx import _enable, _order as px_order, _result
from tests.test_tabs import H, _reset, srv  # noqa: F401 – Fixture
from tests.test_tabs_t40 import _order as tab_order, _tab, shop  # noqa: F401 – Fixture


def _lost_after_order(monkeypatch):
    """Die Bestellung (mit allem im selben Batch) ist gespeichert, nur die Antwort geht verloren."""
    from app import core
    from app.store import StoreUnclear
    real = core.rt.store.batch

    def lost(stmts, *a, **k):
        res = real(stmts, *a, **k)
        if any(s[0].startswith("INSERT INTO orders(") for s in stmts):
            raise StoreUnclear("Antwort verloren")
        return res
    monkeypatch.setattr(core.rt.store, "batch", lost)
    return real


def test_storno_right_after_unclear_order_still_refunds(client, gast, monkeypatch):  # noqa: F811
    """Befund 3A: Storno in der Zeit, bevor tick() aufräumt – die Rückerstattung geht trotzdem raus."""
    from app import core, gast as g, ledger
    beer = next(iter(_products(client).values()))
    o = _order(client, [{"product_id": beer["id"], "qty": 2}]).json()
    st = core.rt.store
    row = st.query_one("SELECT * FROM gast_orders WHERE token=?", (o["token"],))
    real = _lost_after_order(monkeypatch)
    g.paid(row, "7001", "twint")                                   # Anlegen «unklar» – aber alles steht
    monkeypatch.setattr(core.rt.store, "batch", real)
    oid = st.scalar("SELECT id FROM orders WHERE client_uuid=?", ("gast-" + o["token"],))
    assert st.query_one("SELECT status, order_id FROM gast_orders WHERE id=?", (row["id"],)) == \
        {"status": "ordered", "order_id": oid}                     # Verknüpfung im selben Batch
    ledger.cancel(st, order_id=oid, lines=None, reason="wrong", client_uuid="t43-storno-01", by_user="admin")
    st.execute("UPDATE gast_orders SET paid_at=?", (_ago(3),))
    g.tick()
    assert gast.refunds == [("7001", 2 * beer["price"])]


def test_closing_right_after_unclear_order_counts_payrexx(client, gast, monkeypatch):  # noqa: F811
    """Befund 3B: Tagesabschluss in derselben Lücke zählt die Payrexx-Zahlung mit."""
    from app import core, gast as g, ledger
    beer = next(iter(_products(client).values()))
    o = _order(client, [{"product_id": beer["id"], "qty": 2}]).json()
    row = core.rt.store.query_one("SELECT * FROM gast_orders WHERE token=?", (o["token"],))
    real = _lost_after_order(monkeypatch)
    g.paid(row, "7001", "twint")
    monkeypatch.setattr(core.rt.store, "batch", real)
    res = ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn())
    assert res["summary"]["payrexx"]["cents"] == 2 * beer["price"]


def test_unpaid_with_payment_coming_in_cancels_nothing(shop, monkeypatch):  # noqa: F811
    """Befund 4: «Nicht bezahlt», während eine Zahlung eingeht – nichts bleibt storniert."""
    from app import core, ledger, tabs
    tab_order()
    tab_order()                                                    # zwei Bestellungen: alles oder nichts
    tid = _tab()
    real = ledger.cancel
    calls = {"n": 0}

    def payment_between(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:                                        # nach dem ersten Storno kommt die Zahlung
            tabs.pay(core.rt.store, tab_id=tid, key="t43-race-0001", amount=1300, payment="cash")
        return real(*a, **k)
    monkeypatch.setattr(ledger, "cancel", payment_between)
    with pytest.raises(tabs.TabError):
        tabs.unpaid(core.rt.store, tab_id=tid, by_user="admin", note=None)
    assert core.rt.store.scalar("SELECT COUNT(*) FROM cancellations") == 0
    assert core.rt.store.scalar("SELECT closed_at FROM tabs WHERE id=?", (tid,)) is None


def test_unpaid_still_works(shop):  # noqa: F811
    from app import core, tabs
    tab_order()
    tab_order()
    tid = _tab()
    tabs.unpaid(core.rt.store, tab_id=tid, by_user="admin", note="weg")
    assert core.rt.store.scalar("SELECT COUNT(*) FROM cancellations") == 2
    assert core.rt.store.scalar("SELECT close_reason FROM tabs WHERE id=?", (tid,)) == "unpaid"
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='tab_unpaid'") == 1


def test_foreign_phone_never_gets_the_start_token(srv):  # noqa: F811
    """Befund 1 (schon mit 3b9c6ab behoben): fremdes Handy bekommt weder Zahlung noch Startadresse."""
    c = srv
    _reset(c, "now")
    _enable(c)
    pid = {p["name"]: p["id"] for p in c.get("/api/admin/products", auth=AUTH).json()}["Bier 5dl"]
    cu = str(uuid.uuid4())
    assert c.post("/api/orders", headers=H("XCover 1"), json={"client_uuid": cu, "table": "63",
                                                              "lines": [{"product_id": pid, "qty": 1}]}).status_code == 200
    body = {"client_uuid": cu, "amount_cents": 650, "part": "all", "key": "t43-owner-key"}
    p = c.post("/api/phone/payrexx", headers=H("XCover 1"), json=body).json()
    assert c.post(f"/api/phone/payrexx/{p['id']}/manual", headers=H("XCover 2")).status_code == 403
    other = c.post("/api/phone/payrexx", headers=H("XCover 2"), json={**body, "key": "t43-other-key"})
    assert other.status_code == 409 and "launch" not in other.json()


def test_refund_done_then_http500_not_sent_again(client, gast, monkeypatch):  # noqa: F811
    """Befund 5 (schon behoben): Payrexx erstattet zurück und antwortet 500 – nie ein zweites Mal senden."""
    from app import gast as g, payrexx_api as px
    _o, gid, _nr, beer = _paid_order(client, gast)
    real = px.urllib.request.urlopen
    sent = []

    def done_then_500(req, *a, **k):
        if "/refund" in req.full_url:
            sent.append(req.data)
            raise urllib.error.HTTPError(req.full_url, 500, "nach dem Ausführen", {}, io.BytesIO(b'{"status":"error"}'))
        return real(req, *a, **k)
    monkeypatch.setattr(px.urllib.request, "urlopen", done_then_500)
    assert g.do_refund(gid, 2 * beer["price"], "t43") is False
    client.post(f"/api/admin/gast/orders/{gid}/refund", auth=AUTH, json={})
    g.tick()
    assert len(sent) == 1


def test_recheck_window_starts_at_cancellation(client, gast):  # noqa: F811
    """Befund 6: nach langem Payrexx-Ausfall abgebrochen – die 2 h Nachkontrolle beginnen erst jetzt."""
    from app import core, gast as g
    beer = next(iter(_products(client).values()))
    o = _order(client, [{"product_id": beer["id"], "qty": 1}]).json()
    st = core.rt.store
    gid = st.scalar("SELECT id FROM gast_orders WHERE token=?", (o["token"],))
    st.execute("UPDATE gast_orders SET created_at=? WHERE id=?", (_ago(180), gid))
    g.tick()                                                       # Payrexx wieder da: nicht bezahlt -> abbrechen
    assert st.scalar("SELECT status FROM gast_orders WHERE id=?", (gid,)) == "cancelled"
    gast.gateways[gid]["status"] = "confirmed"                     # Zahlung kam doch noch durch
    st.execute("UPDATE gast_orders SET checked_at=? WHERE id=?", (_ago(6), gid))
    g.tick()
    assert gast.refunds == [("7001", beer["price"])]


def test_lost_confirm_answer_repairs_payment_of_whole_order(client, monkeypatch):
    """Befund 7: Bestätigung gespeichert, Antwort verloren – die Zahlart der Bestellung stimmt trotzdem."""
    from app import core, payrexx as p
    from app.store import StoreUnclear
    setup_shop(client)
    _enable(client)
    cu = px_order(client)
    r = p.start(cu, 1300, "all", "t43-unclear-db", "XCover 1")
    real = core.rt.store.batch

    def committed(stmts, *a, **k):
        res = real(stmts, *a, **k)
        if stmts[0][0].startswith("UPDATE payrexx_payments SET status='confirmed'"):
            raise StoreUnclear("gespeichert, Antwort verloren")
        return res
    monkeypatch.setattr(core.rt.store, "batch", committed)
    with pytest.raises(StoreUnclear):
        p._confirm(r, "TX-t43")
    monkeypatch.setattr(core.rt.store, "batch", real)
    assert core.rt.store.scalar("SELECT payment FROM orders WHERE client_uuid=?", (cu,)) == "payrexx"   # im Batch
    core.rt.store.execute("UPDATE orders SET payment=NULL WHERE client_uuid=?", (cu,))                # Stand vor T43
    assert p.refresh(r["id"])["status"] == "confirmed"
    assert core.rt.store.scalar("SELECT payment FROM orders WHERE client_uuid=?", (cu,)) == "payrexx"   # repariert


def test_second_successful_transaction_is_reported(client):
    """Befund 2: zwei erfolgreiche Transaktionen für dieselbe Zahlung – die zweite wird gemeldet, nie still verworfen."""
    from app import core, payrexx as p
    setup_shop(client)
    _enable(client)
    cu = px_order(client)
    a = p.start(cu, 1300, "all", "t43-first", "XCover 1", device_id="one")
    b = p.start(cu, 1300, "all", "t43-second", "XCover 2", device_id="two")
    assert a["id"] == b["id"]
    ref = f"FestPOS-{a['order_id']:04d}-{a['id']}"
    p.tap_result(a["id"], a["link"], True, _result("13.00", ref, tx="TX-first"), None)
    v = p.tap_result(b["id"], b["link"], True, _result("13.00", ref, tx="TX-second"), None)
    assert v["status"] == "confirmed" and v["double"] is True and v["transaction"] == "TX-first"
    row = core.rt.store.query_one("SELECT extra_tx FROM payrexx_payments WHERE id=?", (a["id"],))
    assert row["extra_tx"] == "TX-second"
    p.tap_result(b["id"], b["link"], True, _result("13.00", ref, tx="TX-second"), None)      # nochmals gemeldet
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='payrexx_double'") == 1
    p.tap_result(a["id"], a["link"], True, _result("13.00", ref, tx="TX-first"), None)        # dieselbe: nichts
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='payrexx_double'") == 1


def test_old_v1_receipt_checked_with_frozen_rule(client):
    """T41: alte Quittung mit der Regel von Version -79 (hier unabhängig nachgebaut) – bleibt «in Ordnung»."""
    from app import core, ledger_base as lb
    from tests.test_ledger import _order as l_order
    from tests.test_receipts import _phone, _setup
    setup_shop(client)
    _setup(client)
    bier = core.rt.store.scalar("SELECT id FROM products WHERE name='Bier 5dl'")
    _oid, cu = l_order(client, [{"product_id": bier, "qty": 2}], payment="cash")
    rid = _phone(client, cu).json()["receipts"][0]["id"]
    st = core.rt.store
    r = st.query_one("SELECT * FROM receipts WHERE id=?", (rid,))
    lines = json.loads(r["lines"])
    v1_body = {"no": r["no"], "req": r["request_id"], "order": r["order_uuid"], "at": r["created_at"],   # wie -79
               "part": r["part"], "total": r["total_cents"], "test": int(bool(r["test"])),
               "lines": sorted([[x["line_no"], x["name"], x["price"], x["qty"], x.get("vat_bp")]
                                + ([x["order_uuid"]] if x.get("order_uuid") else []) for x in lines])}
    st.execute("UPDATE receipts SET digest=?, digest_v=NULL WHERE id=?", (lb._mac(v1_body), rid))
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert s["check"]["bad"] == []
    st.execute("UPDATE receipts SET total_cents=1 WHERE id=?", (rid,))                       # geändert: fällt auf
    assert client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]["check"]["bad"] == ["Q1"]


def test_manual_confirm_then_real_result_is_not_a_double(client):
    """Bedienung bestätigt von Hand («Payrexx zeigte bezahlt»), danach kommt das echte Ergebnis: keine Doppelbelastung,
    die Transaktionsnummer wird übernommen."""
    from app import core, payrexx as p
    setup_shop(client)
    _enable(client)
    cu = px_order(client)
    a = p.start(cu, 1300, "all", "t43-manual-1", "XCover 1")
    assert p.manual(a["id"], "XCover 1")["status"] == "confirmed"
    ref = f"FestPOS-{a['order_id']:04d}-{a['id']}"
    v = p.tap_result(a["id"], a["link"], True, _result("13.00", ref, tx="TX-real"), None)
    assert v["double"] is False and v["transaction"] == "TX-real"
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='payrexx_double'") == 0


def test_two_results_after_manual_confirm_race(client, monkeypatch):
    """Zwei verschiedene Ergebnisse nach einer Bestätigung von Hand, gleichzeitig: eines wird die Transaktion, das
    andere als Doppelbelastung gemeldet – keines geht verloren."""
    from app import core, payrexx as p
    setup_shop(client)
    _enable(client)
    cu = px_order(client)
    a = p.start(cu, 1300, "all", "t43-race-1", "XCover 1")
    p.manual(a["id"], "XCover 1")
    stale = p._row(a["id"])                                       # beide lasen «keine Transaktion»
    p._double(stale, "TX-one")
    p._double(stale, "TX-two")
    row = core.rt.store.query_one("SELECT transaction_id, extra_tx FROM payrexx_payments WHERE id=?", (a["id"],))
    assert row == {"transaction_id": "TX-one", "extra_tx": "TX-two"}
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='payrexx_double'") == 1
