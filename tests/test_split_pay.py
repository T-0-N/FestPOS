"""Teilen pro Person – Teilzahlungen sofort kassierter Bestellungen gehen an den Server (Zahlart pro
Person), zählen im Tagesabschluss nach ihrer Zahlart, lassen sich (jede, nicht nur die letzte) zurücknehmen."""
from __future__ import annotations

from tests.test_api import AUTH, setup_shop
from tests.test_ledger import _order, _pids, _storno


def _pays(c, cu, pays):
    return c.post("/api/orders/payments", json={"client_uuid": cu, "payments": pays})


def test_part_payments_count_by_their_payment_and_undo(client):
    setup_shop(client)
    shop = _pids(client)
    oid, cu = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 2}, {"product_id": shop["Mineral"], "qty": 1}],
                     payment="cash")                                          # 17.00
    p1 = {"key": "k-person-1", "amount_cents": 570, "payment": "cash", "person": 1}
    p2 = {"key": "k-person-2", "amount_cents": 565, "payment": "card", "person": 2}
    p3 = {"key": "k-person-3", "amount_cents": 565, "payment": "twint", "person": 3, "tip_cents": 50}
    assert _pays(client, cu, [p1, p2]).json() == {"ok": True, "changed": 2}
    assert _pays(client, cu, [p1, p2]).json()["changed"] == 0                   # nochmals: dieselben
    assert _pays(client, cu, [p1, p2, p3]).json()["changed"] == 1
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert s["by_payment"] == {"cash": 570, "card": 565, "twint": 565}
    # Person 2 (nicht die letzte) zurück: ihr Anteil zählt wieder zur Zahlart der Bestellung (bar)
    assert _pays(client, cu, [p1, p3]).json()["changed"] == 1
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert s["by_payment"] == {"cash": 1135, "twint": 565}
    log = client.get("/api/admin/audit?q=order_part", auth=AUTH).json()["entries"]
    assert {e["action"] for e in log} == {"order_part_pay", "order_part_undo"}
    # mehr als das Total: abgelehnt; unbekannte Bestellung: 404
    big = {"key": "k-person-9", "amount_cents": 1200, "payment": "cash", "person": 4}
    assert _pays(client, cu, [p1, p3, big]).status_code == 422
    assert _pays(client, "x" * 36, [p1]).status_code == 404
    assert _pays(client, cu, [{**p1, "payment": "gold"}]).status_code == 422
    # Storno nach Teilzahlungen: Summe stimmt weiter exakt
    assert _storno(client, oid, {2: 1}).status_code == 200                      # Mineral (4.00) storniert -> 13.00
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert sum(s["by_payment"].values()) == s["net"] == 1300
    # Abschluss: danach keine Änderung mehr
    r = client.post("/api/admin/closings", auth=AUTH, json={"float_cents": 0, "counted_cents": 0})
    assert r.status_code == 200, r.text
    assert _pays(client, cu, [p1]).status_code == 409


def test_reset_orders_removes_part_payments(client):
    from app import core
    setup_shop(client)
    client.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": "1357"})
    shop = _pids(client)
    _, cu = _order(client, [{"product_id": shop["Mineral"], "qty": 1}], test=True)
    assert _pays(client, cu, [{"key": "k-test-0001", "amount_cents": 400, "payment": "cash"}]).status_code == 200
    assert client.post("/api/admin/test-orders/delete?confirm=JA", auth=AUTH).status_code == 200
    assert core.rt.store.scalar("SELECT COUNT(*) FROM order_payments") == 0
