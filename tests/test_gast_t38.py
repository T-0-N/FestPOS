"""T38: QR-Bestellung robust – nie auf den offenen Tisch, Abschluss nachholen statt zurückerstatten, Abbruch erst nach
einer Antwort von Payrexx, bezahlt nach dem Abbruch = automatisch zurück, Preise wie bezahlt, gescheiterter Druck."""
from __future__ import annotations

import json

from tests.test_api import AUTH
from tests.test_gast import G, _order, _products, gast  # noqa: F401 – Fixture

DEAD = "http://127.0.0.1:9/v1.0"                      # Port 9: Verbindung abgewiesen (Payrexx nicht erreichbar)


def _gid(token):
    from app import core
    return core.rt.store.scalar("SELECT id FROM gast_orders WHERE token=?", (token,))


def test_never_on_open_tab(client, gast):  # noqa: F811
    from app import core
    client.put("/api/admin/settings", auth=AUTH, json={"tab_mode": "open"})
    beer = next(iter(_products(client).values()))
    o = _order(client, [{"product_id": beer["id"], "qty": 2}]).json()
    gast.gateways[_gid(o["token"])]["status"] = "confirmed"
    v = client.get(f"/api/gast/order/{o['token']}", headers=G).json()
    assert v["status"] == "ordered"
    order = core.rt.store.query_one("SELECT tab_id, payment FROM orders WHERE id=?", (v["nr"],))
    assert order == {"tab_id": None, "payment": "payrexx"}
    assert core.rt.store.scalar("SELECT COUNT(*) FROM tabs") == 0                  # kein offener Tisch eröffnet
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert s["open_tabs"] == 0 and s["payrexx"]["cents"] == 2 * beer["price"]


def test_finish_is_caught_up_instead_of_refund(client, gast):  # noqa: F811
    """Bestellung steht, Abschluss fehlt (Daten aus der Zeit vor T43; seither im selben Batch): tick() holt ihn nach."""
    from app import core, gast as g
    beer = next(iter(_products(client).values()))
    o = _order(client, [{"product_id": beer["id"], "qty": 1}]).json()
    gid = _gid(o["token"])
    gast.gateways[gid]["status"] = "confirmed"
    client.get(f"/api/gast/order/{o['token']}", headers=G)
    st = core.rt.store
    st.execute("UPDATE gast_orders SET status='paid', order_id=NULL WHERE id=?", (gid,))     # Stand vor T43
    st.execute("DELETE FROM payrexx_payments WHERE key=?", (f"gast-{gid}",))
    assert st.scalar("SELECT COUNT(*) FROM orders") == 1                                    # Bestellung und Bons stehen
    st.execute("UPDATE gast_orders SET paid_at=? WHERE id=?", (_ago(3), gid))               # > 2 min, < Druckfrist
    g.tick()
    v = client.get(f"/api/gast/order/{o['token']}", headers=G).json()
    assert v["status"] == "ordered" and v["nr"] and gast.refunds == []
    assert st.scalar("SELECT COUNT(*) FROM payrexx_payments WHERE key=?", (f"gast-{gid}",)) == 1


def test_cancel_without_payrexx_waits_then_refunds_late_payment(client, gast, monkeypatch):  # noqa: F811
    from app import core, gast as g, payrexx_api
    beer = next(iter(_products(client).values()))
    o = _order(client, [{"product_id": beer["id"], "qty": 1}]).json()
    gid = _gid(o["token"])
    base = payrexx_api.BASE
    monkeypatch.setattr(payrexx_api, "BASE", DEAD)
    v = client.post(f"/api/gast/order/{o['token']}/cancel", headers=G).json()
    assert v["status"] == "waiting" and v["cancelling"] and v["link"] is None     # nicht einfach abgebrochen
    g.tick()
    assert core.rt.store.scalar("SELECT status FROM gast_orders WHERE id=?", (gid,)) == "waiting"
    monkeypatch.setattr(payrexx_api, "BASE", base)
    gast.gateways[gid]["status"] = "confirmed"                                    # der Gast hatte schon bezahlt
    g.tick()
    v = client.get(f"/api/gast/order/{o['token']}", headers=G).json()
    assert v["status"] == "rejected" and v["reason"] == "late" and v["refunded"] == beer["price"]
    assert gast.refunds == [("7001", beer["price"])] and core.rt.store.scalar("SELECT COUNT(*) FROM orders") == 0
    assert "gast_paid_late" in json.dumps(client.get("/api/admin/audit", auth=AUTH).json())


def test_paid_right_after_cancel_is_refunded(client, gast):  # noqa: F811
    from app import core, gast as g
    beer = next(iter(_products(client).values()))
    o = _order(client, [{"product_id": beer["id"], "qty": 1}]).json()
    gid = _gid(o["token"])
    assert client.post(f"/api/gast/order/{o['token']}/cancel", headers=G).json()["status"] == "cancelled"
    gast.gateways[gid]["status"] = "confirmed"                                    # Zahlung kam doch noch durch
    g.tick()
    assert gast.refunds == []                                                     # erst nach 5 Minuten wieder fragen
    core.rt.store.execute("UPDATE gast_orders SET checked_at='2020-01-01T10:00:00' WHERE id=?", (gid,))
    g.tick()
    v = client.get(f"/api/gast/order/{o['token']}", headers=G).json()
    assert v["status"] == "rejected" and v["reason"] == "late" and gast.refunds == [("7001", beer["price"])]
    assert core.rt.store.scalar("SELECT COUNT(*) FROM orders") == 0


def _ago(minutes):
    from datetime import datetime, timedelta
    return (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")


def test_expiry_only_after_an_answer(client, gast, monkeypatch):  # noqa: F811
    from app import core, gast as g, payrexx_api
    beer = next(iter(_products(client).values()))
    o = _order(client, [{"product_id": beer["id"], "qty": 1}]).json()
    gid = _gid(o["token"])
    core.rt.store.execute("UPDATE gast_orders SET created_at=? WHERE id=?", (_ago(40), gid))     # über 30 min
    base = payrexx_api.BASE
    monkeypatch.setattr(payrexx_api, "BASE", DEAD)
    g.tick()
    assert core.rt.store.scalar("SELECT status FROM gast_orders WHERE id=?", (gid,)) == "waiting"   # keine Antwort
    monkeypatch.setattr(payrexx_api, "BASE", base)
    g.tick()                                                                      # Payrexx: nicht bezahlt
    row = core.rt.store.query_one("SELECT status, reason FROM gast_orders WHERE id=?", (gid,))
    assert row == {"status": "cancelled", "reason": "expired"} and gast.gateways[gid]["status"] == "cancelled"
    # über 24 h ohne jede Antwort: geschlossen, im Protokoll
    o2 = _order(client, [{"product_id": beer["id"], "qty": 1}], key="key-0000002").json()
    gid2 = _gid(o2["token"])
    core.rt.store.execute("UPDATE gast_orders SET created_at=? WHERE id=?", (_ago(25 * 60), gid2))
    monkeypatch.setattr(payrexx_api, "BASE", DEAD)
    g.tick()
    assert core.rt.store.scalar("SELECT status FROM gast_orders WHERE id=?", (gid2,)) == "cancelled"
    assert "gast_unresolved" in json.dumps(client.get("/api/admin/audit", auth=AUTH).json())


def test_price_snapshot(client, gast):  # noqa: F811
    from app import core
    beer = next(iter(_products(client).values()))
    o = _order(client, [{"product_id": beer["id"], "qty": 2}]).json()
    core.rt.store.execute("UPDATE products SET price_cents=price_cents+100 WHERE id=?", (beer["id"],))   # teurer
    gast.gateways[_gid(o["token"])]["status"] = "confirmed"
    v = client.get(f"/api/gast/order/{o['token']}", headers=G).json()
    assert v["status"] == "ordered"
    order = core.rt.store.query_one("SELECT total_cents FROM orders WHERE id=?", (v["nr"],))
    item = core.rt.store.query_one("SELECT price_cents FROM order_items WHERE order_id=?", (v["nr"],))
    assert order["total_cents"] == 2 * beer["price"] == o["amount"] and item["price_cents"] == beer["price"]


def test_failed_print_is_refunded_without_storno_bon(client, gast):  # noqa: F811
    from app import core, gast as g
    beer = next(iter(_products(client).values()))
    o = _order(client, [{"product_id": beer["id"], "qty": 1}]).json()
    gid = _gid(o["token"])
    gast.gateways[gid]["status"] = "confirmed"
    nr = client.get(f"/api/gast/order/{o['token']}", headers=G).json()["nr"]
    core.rt.store.execute("UPDATE print_jobs SET status='failed', error_code='render' WHERE order_id=?", (nr,))
    core.rt.store.execute("UPDATE gast_orders SET paid_at='2020-01-01T10:00:00' WHERE id=?", (gid,))
    g.tick()
    v = client.get(f"/api/gast/order/{o['token']}", headers=G).json()
    assert v["status"] == "refunded" and v["reason"] == "printer" and gast.refunds == [("7001", beer["price"])]
    jobs = core.rt.store.query("SELECT kind, status FROM print_jobs WHERE order_id=?", (nr,))
    assert all(j["kind"] != "storno" for j in jobs) and all(j["status"] == "cancelled" for j in jobs)
