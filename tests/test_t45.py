"""T45: Tagesabschluss und Geld, das sich kurz davor, währenddessen oder danach bewegt (Review 2026.10.10-82, Entscheide
A2 Korrekturbuchung, B1 Sperre bei offener Kartenzahlung)."""
from __future__ import annotations

import gzip
import base64
import json

import pytest

from tests.test_api import AUTH, setup_shop
from tests.test_gast import G, _products, _sig, gast  # noqa: F401 – Fixture
from tests.test_gast_refunds import _paid_order
from tests.test_payrexx import _enable, _order as px_order


def _tip_order(client, gast, key="t45-tip-1", qty=2, tip=200):  # noqa: F811
    from app import core
    beer = next(iter(_products(client).values()))
    client.put("/api/admin/settings", auth=AUTH, json={"tips_enabled": True})
    o = client.post("/api/gast/order", headers=G, json={"t": "7", "s": _sig("7"), "key": key, "tip_cents": tip,
                                                         "lines": [{"product_id": beer["id"], "qty": qty}]}).json()
    gid = core.rt.store.scalar("SELECT id FROM gast_orders WHERE token=?", (o["token"],))
    gast.gateways[gid]["status"] = "confirmed"
    oid = client.get(f"/api/gast/order/{o['token']}", headers=G).json()["nr"]
    return gid, oid, beer, o


def test_promised_full_refund_removes_tip_too(client, gast):  # noqa: F811
    """Befund 1: Vollrückerstattung zugesagt (noch nicht ausgeführt) – Payrexx UND Trinkgeld 0, Kasse stimmt."""
    from app import core, gast as g, ledger
    gid, oid, beer, o = _tip_order(client, gast)
    assert o["amount"] == 2 * beer["price"] + 200
    gast.refund_fails, gast.refund_fail_code = True, 400
    ledger.cancel(core.rt.store, order_id=oid, lines=None, reason="wrong", client_uuid="t45-full-1")
    g.process_refunds(gid)
    c = ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn(float_cents=0, counted_cents=0))
    s = c["summary"]
    assert s["payrexx"]["cents"] == 0 and s["tips"]["total"] == 0 and s["net"] == 0
    assert c["expected_cents"] == 0 and c["difference_cents"] == 0


def test_refund_after_closing_books_a_correction(client, gast):  # noqa: F811
    """Befund 2 (A2): nach dem Abschluss zurückerstattet – der alte Abschluss bleibt, der nächste bucht die Korrektur:
    Umsatz, MWST, Trinkgeld und Payrexx minus, die Kasse unverändert."""
    from app import core, ledger
    client.put("/api/admin/vat", auth=AUTH, json={"enabled": True, "number": "CHE-123.456.789 MWST"})
    gid, oid, beer, o = _tip_order(client, gast)
    first = ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn(float_cents=0, counted_cents=0))
    assert first["summary"]["payrexx"]["cents"] == o["amount"]
    r = client.post(f"/api/admin/gast/orders/{gid}/refund", auth=AUTH, json={})
    assert r.status_code == 200, r.text
    assert gast.refunds == [("7001", o["amount"])]
    assert core.rt.store.scalar("SELECT COUNT(*) FROM cancellations") == 0                 # kein Storno (abgeschlossen)
    old = json.loads(core.rt.store.scalar("SELECT summary FROM closings WHERE id=?", (first["id"],)))
    assert old["payrexx"]["cents"] == o["amount"]                                         # alter Abschluss unverändert
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    goods = 2 * beer["price"]
    assert s["corrections"] == {**s["corrections"], "count": 1, "cents": o["amount"], "goods": goods, "tip": 200}
    assert s["net"] == -goods and s["payrexx"]["cents"] == -o["amount"] and s["tips"]["total"] == -200
    assert sum(v["gross"] for v in s["by_vat"]) == -goods                                 # MWST mitgekorrigiert
    assert s["check"]["bad"] == []
    second = ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn(float_cents=0, counted_cents=0))
    assert second["expected_cents"] == 0 and second["difference_cents"] == 0              # Kasse: kein Bargeld bewegt
    assert core.rt.store.scalar("SELECT closing_id FROM corrections") == second["id"]
    csv = gzip.decompress(base64.b64decode(core.rt.store.scalar("SELECT orders_csv FROM closings WHERE id=?",
                                                                 (second["id"],)))).decode("utf-8-sig")
    assert "korrektur;" in csv and "korrektur_trinkgeld;" in csv
    assert client.get(f"/api/admin/closings/{second['id']}/pdf", auth=AUTH).status_code == 200
    assert client.post(f"/api/admin/gast/orders/{gid}/refund", auth=AUTH, json={}).status_code == 409   # nichts mehr
    core.rt.store.execute("UPDATE corrections SET amount_cents=1")                       # nachträglich geändert …
    core.rt.store.execute("UPDATE corrections SET closing_id=NULL")
    assert client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]["check"]["bad"] == ["K1"]   # … fällt auf


def test_partial_refund_after_closing_goods_first(client, gast):  # noqa: F811
    from app import core, ledger
    gid, oid, beer, o = _tip_order(client, gast, key="t45-tip-2")
    ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn())
    assert client.post(f"/api/admin/gast/orders/{gid}/refund", auth=AUTH, json={"amount_cents": 500}).status_code == 200
    c = core.rt.store.query_one("SELECT amount_cents, goods_cents, tip_cents FROM corrections")
    assert c == {"amount_cents": 500, "goods_cents": 500, "tip_cents": 0}                 # zuerst der Umsatz


def test_not_printed_after_closing_refunds_with_correction(client, gast):  # noqa: F811
    """«Nicht gedruckt» nach einem Abschluss: kein Storno möglich – Bons zurückhalten, Korrektur, Geld zurück."""
    from app import core, gast as g, ledger
    _o, gid, oid, beer = _paid_order(client, gast)
    core.rt.store.execute("UPDATE print_jobs SET status='failed', error_code='render' WHERE order_id=?", (oid,))
    ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn())
    core.rt.store.execute("UPDATE gast_orders SET paid_at='2020-01-01T10:00:00' WHERE id=?", (gid,))
    g.tick()
    assert gast.refunds == [("7001", 2 * beer["price"])]
    assert core.rt.store.scalar("SELECT goods_cents FROM corrections") == 2 * beer["price"]
    assert {j["status"] for j in core.rt.store.query("SELECT status FROM print_jobs WHERE order_id=?", (oid,))} == \
        {"cancelled"}
    g.tick()
    assert len(gast.refunds) == 1                                                         # nicht nochmals


def test_waiting_card_payment_blocks_closing(client):
    """B1: offene Kartenzahlung sperrt den Abschluss; in der Verwaltung abbrechen, dann geht er."""
    from app import core, ledger, payrexx as p
    setup_shop(client)
    _enable(client)
    cu = px_order(client)
    a = p.start(cu, 1300, "all", "t45-wait-1", "XCover 1")
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert [w["id"] for w in s["payrexx_waiting"]] == [a["id"]]
    with pytest.raises(ledger.LedgerError) as e:
        ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn())
    assert e.value.status == 409 and "Kartenzahlung" in str(e.value)
    r = client.post(f"/api/admin/payrexx/payments/{a['id']}/cancel", auth=AUTH, json={})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert client.post(f"/api/admin/payrexx/payments/{a['id']}/cancel", auth=AUTH, json={}).status_code == 409
    assert "payrexx_cancel_admin" in json.dumps(client.get("/api/admin/audit", auth=AUTH).json())
    assert ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn())["id"]


def test_confirmation_during_closing_is_counted(client, monkeypatch):
    """Befund 3: eine (abgebrochene) Kartenzahlung wird genau während des Rechnens doch bestätigt – der Abschluss
    rechnet neu und zählt sie (Fingerabdruck im Schreibbefehl)."""
    from app import core, ledger, payrexx as p
    setup_shop(client)
    _enable(client)
    cu = px_order(client)
    a = p.start(cu, 1300, "all", "t45-race-1", "XCover 1")
    p.cancel(a["id"])                                                    # abgebrochen: sperrt nicht mehr
    real = p.period
    calls = {"n": 0}

    def confirm_after_read(store, ids):
        res = real(store, ids)
        calls["n"] += 1
        if calls["n"] == 1:
            p._confirm(p._row(a["id"]), "TX-late")                       # Geld ist doch geflossen
        return res
    monkeypatch.setattr(p, "period", confirm_after_read)
    c = ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn())
    assert calls["n"] == 2 and c["summary"]["payrexx"]["cents"] == 1300
    assert c["summary"]["by_payment"].get("payrexx") == 1300                              # Zahlart mitgezählt


def test_fingerprint_on_both_databases(store):
    """FINGERPRINT_SQL läuft auf SQLite und echtem rqlite und ändert sich mit einer bestätigten Zahlung."""
    from app import ledger
    before = store.scalar(f"SELECT {ledger.FINGERPRINT_SQL}")
    store.execute("INSERT INTO payrexx_payments(key, order_id, part, amount_cents, status, created_at) "
                  "VALUES ('fp-1', 1, 'all', 500, 'confirmed', '2026-11-10T20:00:00')")
    assert store.scalar(f"SELECT {ledger.FINGERPRINT_SQL}") != before


def test_card_paid_after_cancel_and_closing_is_booked_later(client):
    """CodeRabbit PR #67: abgebrochen, abgeschlossen, dann kommt doch «bezahlt». T46 (C): erst offen; «Gast hat nicht
    bar bezahlt – nachbuchen» – der nächste Abschluss bucht die Zahlung nach (Payrexx plus, Zahlart wechselt, Umsatz
    bleibt, erwartete Kasse entsprechend kleiner)."""
    from app import core, ledger, payrexx as p
    setup_shop(client)
    _enable(client)
    cu = px_order(client)
    total = core.rt.store.scalar("SELECT total_cents FROM orders WHERE client_uuid=?", (cu,))
    a = p.start(cu, total, "all", "t45-late-1", "XCover 1")
    p.cancel(a["id"])
    first = ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn(float_cents=0))
    assert first["summary"]["payrexx"]["cents"] == 0 and first["expected_cents"] == total     # galt als Bargeld
    assert p._confirm(p._row(a["id"]), "TX-late") is True                                     # Geld ist doch geflossen
    p._confirm(p._row(a["id"]), "TX-late")                                                    # nochmals: nicht doppelt
    assert core.rt.store.scalar("SELECT COUNT(*) FROM corrections") == 0                      # T46: erst entscheiden
    assert client.post(f"/api/admin/payrexx/payments/{a['id']}/late/book", auth=AUTH).status_code == 200
    assert core.rt.store.scalar("SELECT COUNT(*) FROM corrections WHERE kind='late_payment'") == 1
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert s["late_payments"]["cents"] == total and s["payrexx"]["cents"] == total and s["net"] == 0
    assert s["by_payment"].get("payrexx") == total and s["by_payment"].get("none") == -total
    second = ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn(float_cents=0))
    assert second["expected_cents"] == -total                                                 # Kasse: das Bargeld fehlt
    assert second["summary"]["check"]["bad"] == []
    assert core.rt.store.scalar("SELECT closing_id FROM payrexx_payments WHERE id=?", (a["id"],)) == second["id"]


def test_lead_may_cancel_waiting_payment(client):
    """CodeRabbit PR #67: die Festleitung (Seite Tagesabschluss) darf eine offene Kartenzahlung abbrechen."""
    from app import auth
    assert auth.required_role("POST", "/api/admin/payrexx/payments/7/cancel") == "lead"


def test_refund_after_closing_conflict_is_409(client, gast, monkeypatch):  # noqa: F811
    """CodeRabbit PR #67: fehlt die Korrektur (gleichzeitig anders reserviert), 409 statt 500 – und kein Geld."""
    from app import core, gast as g, ledger
    _o, gid, oid, beer = _paid_order(client, gast)
    ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn())
    real = g.late_refund_stmts

    def stale(store, gid_, amount, key, why, actor):
        stmts = real(store, gid_, amount, key, why, actor)
        return [s for s in stmts if not s[0].startswith("INSERT OR IGNORE INTO corrections")]
    monkeypatch.setattr(g, "late_refund_stmts", stale)
    assert client.post(f"/api/admin/gast/orders/{gid}/refund", auth=AUTH, json={}).status_code == 409
    assert gast.refunds == [] and core.rt.store.scalar("SELECT COUNT(*) FROM gast_refunds") == 0


def test_payment_counted_before_closing_is_never_late(client):
    """CodeRabbit PR #67: vor dem Abschluss bestätigt und gezählt – Abfragen oder nochmals gemeldet danach bucht
    keine «Kartenzahlung früherer Tage»."""
    from app import core, ledger, payrexx as p
    from tests.test_payrexx import _result
    setup_shop(client)
    _enable(client)
    cu = px_order(client)
    total = core.rt.store.scalar("SELECT total_cents FROM orders WHERE client_uuid=?", (cu,))
    a = p.start(cu, total, "all", "t45-counted-1", "XCover 1")
    ref = f"FestPOS-{a['order_id']:04d}-{a['id']}"
    p.tap_result(a["id"], a["link"], True, _result(f"{total / 100:.2f}", ref, tx="TX-1"), None)
    c = ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn())
    assert c["summary"]["payrexx"]["cents"] == total
    assert core.rt.store.scalar("SELECT closing_id FROM payrexx_payments WHERE id=?", (a["id"],)) == c["id"]
    p.refresh(a["id"])                                                       # Handy fragt nochmals ab
    p.tap_result(a["id"], a["link"], True, _result(f"{total / 100:.2f}", ref, tx="TX-1"), None)
    p._confirm(p._row(a["id"]), "TX-1")
    assert core.rt.store.scalar("SELECT COUNT(*) FROM corrections") == 0


def test_partial_refund_racing_the_closing_books_a_correction(client, gast, monkeypatch):  # noqa: F811
    """CodeRabbit PR #67: der Abschluss kommt zwischen Lesen und Schreiben der Teilrückerstattung – sie wird nicht als
    gewöhnlicher Auftrag geschrieben (fehlte sonst in allen Abschlüssen), sondern als Korrektur."""
    from app import core, gast as g, ledger
    _o, gid, oid, beer = _paid_order(client, gast)
    real, done = g.refund_stmts, []

    def closing_first(*a, **kw):
        if not done:
            done.append(ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn()))
        return real(*a, **kw)
    monkeypatch.setattr(g, "refund_stmts", closing_first)
    r = client.post(f"/api/admin/gast/orders/{gid}/refund", auth=AUTH, json={"amount_cents": 500})
    assert r.status_code == 200, r.text
    assert done and gast.refunds == [("7001", 500)]
    assert core.rt.store.query("SELECT kind, amount_cents FROM corrections") == [{"kind": "refund", "amount_cents": 500}]
    assert core.rt.store.scalar("SELECT COUNT(*) FROM gast_refunds") == 1


def _second_payment(order_id, amount, part, key):
    from app import core
    return core.rt.store.execute(
        "INSERT INTO payrexx_payments(key, order_id, part, amount_cents, status, created_at) "
        "VALUES (?, ?, ?, ?, 'cancelled', '2026-11-10T20:00:00')", (key, order_id, part, amount)).last_id


def test_late_payment_already_covered_by_card_is_overpaid(client):
    """CodeRabbit PR #67: die Bestellung ist über Payrexx schon ganz bezahlt, eine abgebrochene zweite Kartenzahlung
    kommt nach dem Abschluss doch – keine Korrektur (die Kasse bleibt), sondern Zuvielzahlung gemeldet."""
    from app import core, ledger, payrexx as p
    setup_shop(client)
    _enable(client)
    cu = px_order(client)
    o = core.rt.store.query_one("SELECT id, total_cents FROM orders WHERE client_uuid=?", (cu,))
    a = p.start(cu, o["total_cents"], "all", "t45-cov-1", "XCover 1")
    assert p._confirm(p._row(a["id"]), "TX-A")
    b = _second_payment(o["id"], o["total_cents"], "p1", "t45-cov-2")
    first = ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn(float_cents=0))
    assert first["summary"]["payrexx"]["cents"] == o["total_cents"] and first["expected_cents"] == 0
    assert p._confirm(p._row(b), "TX-B")
    p._confirm(p._row(b), "TX-B")                                                             # nochmals: einmal gemeldet
    assert core.rt.store.scalar("SELECT COUNT(*) FROM corrections") == 0
    assert core.rt.store.scalar("SELECT error FROM payrexx_payments WHERE id=?", (b,)) == "overpaid"
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='payrexx_overpaid_late'") == 1
    with pytest.raises(ledger.LedgerError):                                                  # nichts zu buchen:
        ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn(float_cents=0))         # Kasse unverändert


def test_late_payment_partly_covered_books_only_the_rest(client):
    """Teilweise über Payrexx bezahlt: die Korrektur nimmt nur den ungedeckten Teil, der Rest ist Zuvielzahlung."""
    from app import core, ledger, payrexx as p
    setup_shop(client)
    _enable(client)
    cu = px_order(client)
    o = core.rt.store.query_one("SELECT id, total_cents FROM orders WHERE client_uuid=?", (cu,))
    half = o["total_cents"] // 2
    a = p.start(cu, half, "p1", "t45-part-1", "XCover 1")
    assert p._confirm(p._row(a["id"]), "TX-A")
    b = _second_payment(o["id"], o["total_cents"], "p2", "t45-part-2")
    ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn(float_cents=0))
    assert p._confirm(p._row(b), "TX-B")
    assert client.post(f"/api/admin/payrexx/payments/{b}/late/book", auth=AUTH).status_code == 200   # T46
    rest = o["total_cents"] - half
    assert core.rt.store.query("SELECT kind, amount_cents FROM corrections") == [
        {"kind": "late_payment", "amount_cents": rest}]
    assert core.rt.store.scalar("SELECT error FROM payrexx_payments WHERE id=?", (b,)) == "overpaid"
    second = ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn(float_cents=0))
    assert second["expected_cents"] == -rest and second["summary"]["payrexx"]["cents"] == rest
