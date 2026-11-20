"""Trinkgeld: an der Kasse erfassen (Schalter in der Verwaltung), kein Umsatz und keine MWST,
eigene Zeilen im Tagesabschluss (pro Zahlart = Zahlart der Bestellung, pro Handy, ausbezahlt), in CSV/PDF und damit in
der Prüfsumme; Karte/TWINT mit NFC über Payrexx und QR-Bestellung am Tisch mit Trinkgeld im Payrexx-Betrag."""
from __future__ import annotations

import base64
import gzip
import uuid

from tests.test_api import AUTH, setup_shop
from tests.test_gast import G, _products, _sig, gast  # noqa: F401  (gast = Fixture)
from tests.test_ledger import _order, _pids, _storno
from tests.test_payrexx import _extras, _result, pay  # noqa: F401  (pay = Fixture)


def _tip(c, cu, cents):
    return c.post("/api/orders/tip", json={"client_uuid": cu, "tip_cents": cents})


def test_tips_count_apart_from_turnover_and_close(client):
    from app import core
    setup_shop(client)
    shop = _pids(client)
    assert client.get("/api/bootstrap").json()["tips"] is False                      # Standard: aus
    assert client.put("/api/admin/settings", auth=AUTH, json={"tips_enabled": True, "test_enabled": True,
                                                              "test_pin": "1357"}).status_code == 200
    assert client.get("/api/bootstrap").json()["tips"] is True
    a, cu_a = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 2}], payment="cash", device="Anna")
    b, cu_b = _order(client, [{"product_id": shop["Mineral"], "qty": 1}], payment="card", device="Beat")
    c, cu_c = _order(client, [{"product_id": shop["Mineral"], "qty": 1}], device="Anna")      # Zahlart nicht angegeben
    _, cu_t = _order(client, [{"product_id": shop["Mineral"], "qty": 1}], test=True)
    for cu, cents in ((cu_a, 200), (cu_b, 150), (cu_b, 300), (cu_c, 100), (cu_t, 999)):    # B geändert: ersetzt
        assert _tip(client, cu, cents).status_code == 200
    assert _tip(client, "x" * 36, 100).status_code == 404
    assert _tip(client, cu_a, 100_001).status_code == 422
    assert _storno(client, a).status_code == 200                                       # Storno lässt Trinkgeld stehen
    o = client.get("/api/admin/closings/open", auth=AUTH).json()
    s = o["summary"]
    assert s["net"] == 800 and s["tips_enabled"] is True                               # Umsatz ohne Trinkgeld
    tp = s["tips"]
    assert tp["total"] == 600 and tp["by_payment"] == {"cash": 200, "card": 300, "none": 100}
    assert {d["device"]: d["amount"] for d in tp["by_device"]} == {"Anna": 300, "Beat": 300}
    assert all(v["gross"] <= 1300 for v in s["by_vat"])                               # keine MWST aufs Trinkgeld
    # Kassensturz: Karte laut Abrechnung MIT Trinkgeld, 2.50 Trinkgeld bar ausbezahlt
    card = s["by_payment"]["card"] + 300
    expected = 5000 + s["net"] + 600 - card - 250
    r = client.post("/api/admin/closings", auth=AUTH, json={"float_cents": 5000, "card_cents": card, "tip_out_cents": 250,
                                                            "counted_cents": expected})
    assert r.status_code == 200, r.text
    cl = r.json()
    assert cl["expected_cents"] == expected and cl["difference_cents"] == 0
    assert cl["summary"]["tips"]["paid_out"] == 250 and "list" not in cl["summary"]["tips"]
    assert client.get(f"/api/admin/closings/{cl['id']}/verify", auth=AUTH).json()["ok"] is True
    raw = core.rt.store.scalar("SELECT orders_csv FROM closings WHERE id=?", (cl["id"],))
    csv = gzip.decompress(base64.b64decode(raw)).decode("utf-8-sig")
    rows = [x.split(";") for x in csv.splitlines() if x.startswith("trinkgeld")]
    assert sorted((int(x[1]), x[5], x[11]) for x in rows) == [(a, "cash", "2.00"), (b, "card", "3.00"), (c, "none", "1.00")]
    pdf = client.get(f"/api/admin/closings/{cl['id']}/pdf", auth=AUTH)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert _tip(client, cu_b, 500).status_code == 409                                  # abgeschlossen
    # Zeitraum danach: nichts mehr offen, Übungs-Trinkgeld zählt nie
    assert client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]["tips"]["total"] == 0


def test_old_closing_without_tips_still_verifies(client):
    """Ältere Abschlüsse haben keine Trinkgeld-Zeilen: Prüfsumme und PDF wie bisher."""
    setup_shop(client)
    shop = _pids(client)
    _order(client, [{"product_id": shop["Mineral"], "qty": 1}])
    cl = client.post("/api/admin/closings", auth=AUTH, json={"counted_cents": 400}).json()
    assert cl["difference_cents"] == 0 and cl["summary"]["tips"] == {"total": 0, "by_payment": {}, "by_device": [],
                                                                      "paid_out": None}
    assert client.get(f"/api/admin/closings/{cl['id']}/verify", auth=AUTH).json()["ok"] is True


def test_tip_with_payrexx_nfc(client, pay):  # noqa: F811
    from app import core
    client.put("/api/admin/payrexx", auth=AUTH, json={"enabled": True})
    pid = client.get("/api/bootstrap").json()["products"][0]["id"]
    cu = str(uuid.uuid4())
    assert client.post("/api/orders", json={"client_uuid": cu, "table": "7", "lines": [{"product_id": pid, "qty": 2}]}).status_code == 200
    total = core.rt.store.scalar("SELECT total_cents FROM orders WHERE client_uuid=?", (cu,))
    too_much = client.post("/api/phone/payrexx", json={"client_uuid": cu, "amount_cents": total + 300, "tip_cents": 100,
                                                       "key": "k-tip-00001"})
    assert too_much.status_code == 409                                               # ohne Trinkgeld mehr als das Total
    p = client.post("/api/phone/payrexx", json={"client_uuid": cu, "amount_cents": total + 200, "tip_cents": 200,
                                                "key": "k-tip-00002"}).json()
    assert p["tip"] == 200
    x = _extras(p["launch"])
    assert x["amount"] == f"{(total + 200) / 100:.2f}"                               # der Gast zahlt mit Trinkgeld
    r = client.post("/api/tap/result", json={"id": p["id"], "token": x["token"], "ok": True,
                                             "result": _result(x["amount"], x["ref"])})
    assert r.json()["status"] == "confirmed"
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert s["payrexx"]["cents"] == total + 200 and s["tips"]["by_payment"] == {"payrexx": 200}
    cl = client.post("/api/admin/closings", auth=AUTH, json={"counted_cents": 0}).json()
    assert cl["expected_cents"] == 0 and cl["difference_cents"] == 0                 # nichts davon liegt in der Kasse


def test_tip_with_qr_order(client, gast):  # noqa: F811
    from app import core
    p = list(_products(client).values())
    lines = [{"product_id": p[0]["id"], "qty": 2}, {"product_id": p[1]["id"], "qty": 1}]
    total = 2 * p[0]["price"] + p[1]["price"]
    off = client.post("/api/gast/order", headers=G, json={"t": "7", "s": _sig("7"), "key": "key-tip-0001", "lines": lines,
                                                         "tip_cents": 150}).json()
    assert off["amount"] == total and off["tip"] == 0                                  # ausgeschaltet: kein Trinkgeld
    client.put("/api/admin/settings", auth=AUTH, json={"tips_enabled": True})
    assert client.get("/api/gast/menu", params={"t": "7", "s": _sig("7")}, headers=G).json()["tips"] is True
    assert client.post("/api/gast/order", headers=G, json={"t": "7", "s": _sig("7"), "key": "key-tip-0002", "lines": lines,
                                                           "tip_cents": total + 1}).status_code == 409
    o = client.post("/api/gast/order", headers=G, json={"t": "7", "s": _sig("7"), "key": "key-tip-0003", "lines": lines,
                                                       "tip_cents": 150}).json()
    assert o["amount"] == total + 150 and o["tip"] == 150
    gid = max(gast.gateways)
    assert gast.gateways[gid]["params"]["amount"] == str(total + 150)
    gast.gateways[gid]["status"] = "confirmed"
    nr = client.get(f"/api/gast/order/{o['token']}", headers=G).json()["nr"]
    assert core.rt.store.scalar("SELECT total_cents FROM orders WHERE id=?", (nr,)) == total      # Umsatz ohne Trinkgeld
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert s["payrexx"]["cents"] == total + 150 and s["tips"]["by_payment"] == {"payrexx": 150}
    # Teilstorno: nur die Position zurück, das Trinkgeld bleibt
    items = core.rt.store.query("SELECT line_no, price_cents, qty FROM order_items WHERE order_id=? ORDER BY line_no", (nr,))
    one = next(i for i in items if i["qty"] == 1)
    assert _storno(client, nr, {one["line_no"]: 1}).status_code == 200
    assert gast.refunds[-1][1] == one["price_cents"]
    assert client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]["tips"]["total"] == 150
    # Rest storniert: ganze Bestellung zurück, mit Trinkgeld
    assert _storno(client, nr).status_code == 200
    assert sum(x[1] for x in gast.refunds if x[0] == gast.refunds[-1][0]) == total + 150
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert s["payrexx"]["cents"] == 0 and s["tips"]["total"] == 0


def test_reset_orders_clears_tips(client):
    """Nach «Alle Bestellungen löschen» beginnen die Nummern neu: altes Trinkgeld darf nicht an neuen Bestellungen hängen."""
    from app import core
    setup_shop(client)
    shop = _pids(client)
    client.put("/api/admin/settings", auth=AUTH, json={"tips_enabled": True})
    _, cu = _order(client, [{"product_id": shop["Mineral"], "qty": 1}])
    _tip(client, cu, 300)
    client.post("/api/admin/closings", auth=AUTH, json={})
    assert client.post("/api/admin/reset-orders", params={"confirm": "JA"}, auth=AUTH).status_code == 200
    assert core.rt.store.scalar("SELECT COUNT(*) FROM tips") == 0
    _order(client, [{"product_id": shop["Mineral"], "qty": 1}])
    assert client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]["tips"]["total"] == 0


def test_closing_recomputes_when_tip_came_in_between(client, monkeypatch):
    """Während der Abschluss rechnet, kommt ein Trinkgeld: der Abschluss rechnet neu und enthält es."""
    from app import core, ledger
    setup_shop(client)
    shop = _pids(client)
    _, cu = _order(client, [{"product_id": shop["Mineral"], "qty": 1}], payment="cash")
    real, calls = ledger.period_csv, []

    def tip_meanwhile(rows, *rest):
        calls.append(1)
        if len(calls) == 1:
            assert _tip(client, cu, 250).status_code == 200
        return real(rows, *rest)
    monkeypatch.setattr(ledger, "period_csv", tip_meanwhile)
    c = ledger.create_closing(core.rt.store, "Lea", ledger.ClosingIn(counted_cents=650))
    assert len(calls) == 2 and c["summary"]["tips"]["total"] == 250 and c["difference_cents"] == 0
    assert ledger.verify_closing(core.rt.store, c["id"])["ok"] is True
