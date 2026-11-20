"""T46: Review 2026.10.10-83 (11.11.2026) – späte Kartenzahlung nach dem Abschluss (Entscheid: C, die
Verwaltung entscheidet bei Bestellungen bar/ohne Angabe; Karte, TWINT, Andere, Payrexx = zu viel bezahlt) und
gleichzeitige Rückerstattungen nach dem Abschluss (Aufteilung Umsatz/Trinkgeld)."""
from __future__ import annotations

import pytest

from tests.test_api import AUTH, setup_shop
from tests.test_gast import gast  # noqa: F401 – Fixture
from tests.test_payrexx import _enable, _order as px_order
from tests.test_t45 import _tip_order


def _late(client, payment, card=0):
    """Kartenzahlung gestartet, abgebrochen, Bestellung mit dieser Zahlart abgeschlossen, dann doch «bezahlt»."""
    from app import core, ledger, payrexx as p
    setup_shop(client)
    _enable(client)
    cu = px_order(client)
    total = core.rt.store.scalar("SELECT total_cents FROM orders WHERE client_uuid=?", (cu,))
    a = p.start(cu, total, "all", f"t46-{payment}", "XCover 1")
    p.cancel(a["id"])
    if payment:
        ledger.set_payment(core.rt.store, order_id=a["order_id"], payment=payment, actor="admin")
    first = ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn(float_cents=0, card_cents=card))
    assert p._confirm(p._row(a["id"]), "TX-late") is True
    return a["id"], total, first


def _audits(action):
    from app import core
    return core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action=?", (action,))


def test_concurrent_late_refunds_keep_goods_and_tip(client, gast):  # noqa: F811
    """Befund 1: zwei Rückerstattungen derselben abgeschlossenen Bestellung, beide vor dem Schreiben berechnet – die
    zweite scheitert (Stand geändert) statt Trinkgeld als Umsatz zu buchen; nochmals berechnet stimmt die Aufteilung."""
    from app import core, gast as g, ledger
    from app.store import StoreError
    gid, oid, beer, o = _tip_order(client, gast)
    ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn())
    first = g.late_refund_stmts(core.rt.store, gid, 1000, "t46-one", "admin:test", "admin")
    second = g.late_refund_stmts(core.rt.store, gid, 500, "t46-two", "admin:test", "admin")
    core.rt.store.batch(first)
    with pytest.raises(StoreError, match="NOT NULL"):
        core.rt.store.batch(second)
    assert core.rt.store.scalar("SELECT COUNT(*) FROM gast_refunds") == 1                 # nichts halb geschrieben
    core.rt.store.batch(g.late_refund_stmts(core.rt.store, gid, 500, "t46-two", "admin:test", "admin"))
    s = ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn())["summary"]
    goods = 2 * beer["price"]
    assert s["corrections"]["goods"] == goods and s["corrections"]["tip"] == 200
    assert s["corrections"]["cents"] == o["amount"]


def test_concurrent_late_refund_via_api_is_409(client, gast, monkeypatch):  # noqa: F811
    """Über die Verwaltung: kommt eine andere Rückerstattung dazwischen, 409 (nochmals versuchen) – kein Geld."""
    from app import core, gast as g, ledger
    gid, oid, beer, o = _tip_order(client, gast)
    ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn())
    real, done = g.late_refund_stmts, []

    def other_first(store, gid_, amount, key, why, actor):
        stmts = real(store, gid_, amount, key, why, actor)
        if not done:
            done.append(store.batch(real(store, gid_, 300, "t46-other", "admin:x", "admin")))
        return stmts
    monkeypatch.setattr(g, "late_refund_stmts", other_first)
    r = client.post(f"/api/admin/gast/orders/{gid}/refund", auth=AUTH, json={"amount_cents": 1000})
    assert r.status_code == 409, r.text
    assert core.rt.store.query("SELECT key FROM gast_refunds") == [{"key": "t46-other"}]


@pytest.mark.parametrize("payment", ["card", "twint", "other"])
def test_late_payment_after_external_payment_is_overpaid(client, payment):
    """Befund 2: Bestellung extern bezahlt (Karte, TWINT, Andere), dann kommt die abgebrochene Kartenzahlung doch –
    der Gast hat zweimal bezahlt: keine Korrektur, die Kasse bleibt, Zuvielzahlung gemeldet."""
    from app import core, ledger
    pid, total, first = _late(client, payment, card=0)
    assert core.rt.store.scalar("SELECT COUNT(*) FROM corrections") == 0
    row = core.rt.store.query_one("SELECT error, late FROM payrexx_payments WHERE id=?", (pid,))
    assert row == {"error": "overpaid", "late": None}
    assert _audits("payrexx_overpaid_late") == 1 and _audits("payrexx_late_open") == 0
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert s["payrexx_late"] == [] and s["late_payments"]["count"] == 0
    with pytest.raises(ledger.LedgerError):                                               # nichts zu buchen
        ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn(float_cents=0))


@pytest.mark.parametrize("payment", ["cash", None])
def test_late_payment_after_cash_waits_for_decision(client, payment):
    """C: Bestellung bar oder ohne Angabe – nichts wird automatisch gebucht; die Seite Tagesabschluss listet die
    Zahlung zum Entscheiden (sperrt den Abschluss nicht)."""
    from app import core
    pid, total, first = _late(client, payment)
    assert core.rt.store.scalar("SELECT COUNT(*) FROM corrections") == 0
    assert core.rt.store.query_one("SELECT error, late FROM payrexx_payments WHERE id=?", (pid,)) == {
        "error": None, "late": "open"}
    assert _audits("payrexx_late_open") == 1
    from app import payrexx as p
    p._confirm(p._row(pid), "TX-late")                                                    # nochmals: nichts Neues
    assert _audits("payrexx_late_open") == 1
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert [(x["id"], x["amount_cents"], x["closing_id"]) for x in s["payrexx_late"]] == [(pid, total, first["id"])]


def test_decision_guest_paid_cash_refunds(client):
    """«Gast hat bar bezahlt – zurückerstattet»: Kasse bleibt, keine Korrektur, Protokoll; nur einmal."""
    from app import core
    pid, total, first = _late(client, "cash")
    r = client.post(f"/api/admin/payrexx/payments/{pid}/late/refund", auth=AUTH)
    assert r.status_code == 200 and r.json()["late"] == "refund"
    assert core.rt.store.scalar("SELECT COUNT(*) FROM corrections") == 0
    assert _audits("payrexx_late_refund") == 1
    for action in ("refund", "book"):                                                     # schon entschieden
        assert client.post(f"/api/admin/payrexx/payments/{pid}/late/{action}", auth=AUTH).status_code == 409
    assert client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]["payrexx_late"] == []


def test_decision_guest_did_not_pay_books_correction(client):
    """«Gast hat nicht bar bezahlt – nachbuchen»: Korrektur im nächsten Abschluss, erwartete Kasse kleiner (gleicht
    den Fehlbetrag des alten Abschlusses aus); nur einmal."""
    from app import core, ledger
    pid, total, first = _late(client, "cash")
    assert first["expected_cents"] == total
    assert client.post(f"/api/admin/payrexx/payments/{pid}/late/book", auth=AUTH).status_code == 200
    assert client.post(f"/api/admin/payrexx/payments/{pid}/late/book", auth=AUTH).status_code == 409
    assert core.rt.store.query("SELECT kind, amount_cents, why FROM corrections") == [
        {"kind": "late_payment", "amount_cents": total, "why": "von:cash"}]
    assert _audits("payrexx_late_book") == 1
    second = ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn(float_cents=0))
    assert second["expected_cents"] == -total and second["summary"]["payrexx"]["cents"] == total
    assert core.rt.store.scalar("SELECT closing_id FROM payrexx_payments WHERE id=?", (pid,)) == second["id"]


def test_late_decision_unknown_action_and_roles(client):
    """Nur refund/book; Festleitung darf entscheiden (Seite Tagesabschluss), Anzeige nicht."""
    from app import auth, core
    from tests.test_users import PW, _hdr
    pid, total, first = _late(client, "cash")
    assert client.post(f"/api/admin/payrexx/payments/{pid}/late/other", auth=AUTH).status_code == 404
    assert auth.required_role("POST", f"/api/admin/payrexx/payments/{pid}/late/book") == "lead"
    for name, role in (("Anzeige", "view"), ("Leitung", "lead")):
        assert client.post("/api/admin/users", auth=AUTH, json={"name": name, "role": role, "password": PW}).status_code == 200
    path = f"/api/admin/payrexx/payments/{pid}/late/refund"
    assert client.post(path, headers=_hdr(client, "Anzeige")).status_code == 403
    assert core.rt.store.scalar("SELECT late FROM payrexx_payments WHERE id=?", (pid,)) == "open"
    assert client.post(path, headers=_hdr(client, "Leitung")).status_code == 200


def test_restart_keeps_open_late_payment_bookable(client):
    """CodeRabbit PR #70: die Nachtragung von closing_id läuft bei jedem Start – eine offene späte Zahlung bekommt
    dabei keinen alten Abschluss (sonst ginge «nachbuchen» nie mehr)."""
    from app import core
    pid, total, first = _late(client, "cash")
    core.rt.store.init_schema()                                                           # wie ein Neustart
    assert core.rt.store.scalar("SELECT closing_id FROM payrexx_payments WHERE id=?", (pid,)) is None
    assert client.post(f"/api/admin/payrexx/payments/{pid}/late/book", auth=AUTH).status_code == 200


def test_second_late_payment_does_not_block_booking_the_first(client):
    """CodeRabbit PR #70: zwei abgebrochene Kartenzahlungen kommen beide spät – die zweite ist zu viel (die erste ist
    offen); «nachbuchen» der ersten zählt die zweite nicht als Deckung."""
    from app import core, ledger, payrexx as p
    setup_shop(client)
    _enable(client)
    cu = px_order(client)
    total = core.rt.store.scalar("SELECT total_cents FROM orders WHERE client_uuid=?", (cu,))
    a = p.start(cu, total, "all", "t46-two-a", "XCover 1")
    p.cancel(a["id"])
    b = p.start(cu, total, "all", "t46-two-b", "XCover 1")
    p.cancel(b["id"])
    ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn(float_cents=0))
    assert p._confirm(p._row(a["id"]), "TX-A") and p._confirm(p._row(b["id"]), "TX-B")
    rows = {r["id"]: r for r in core.rt.store.query("SELECT id, late, error FROM payrexx_payments")}
    assert (rows[a["id"]]["late"], rows[b["id"]]["late"], rows[b["id"]]["error"]) == ("open", None, "overpaid")
    assert client.post(f"/api/admin/payrexx/payments/{a['id']}/late/book", auth=AUTH).status_code == 200
    assert core.rt.store.scalar("SELECT goods_cents FROM corrections") == total


def test_refunded_late_payment_is_no_coverage(client):
    """«Gast hat bar bezahlt – zurückerstattet» deckt nichts: kommt danach noch eine späte Zahlung, ist auch sie offen."""
    from app import core, ledger, payrexx as p
    setup_shop(client)
    _enable(client)
    cu = px_order(client)
    total = core.rt.store.scalar("SELECT total_cents FROM orders WHERE client_uuid=?", (cu,))
    a = p.start(cu, total, "all", "t46-ref-a", "XCover 1")
    p.cancel(a["id"])
    b = p.start(cu, total, "all", "t46-ref-b", "XCover 1")
    p.cancel(b["id"])
    ledger.create_closing(core.rt.store, "admin", ledger.ClosingIn(float_cents=0))
    assert p._confirm(p._row(a["id"]), "TX-A")
    assert client.post(f"/api/admin/payrexx/payments/{a['id']}/late/refund", auth=AUTH).status_code == 200
    assert p._confirm(p._row(b["id"]), "TX-B")
    assert core.rt.store.scalar("SELECT late FROM payrexx_payments WHERE id=?", (b["id"],)) == "open"
