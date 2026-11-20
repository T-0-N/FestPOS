"""Version 2026.10.10-20: Kassenbuch – Zahlart, Storno (Gegenbuchung, Storno-Bon), Tagesabschluss mit Kassensturz,
Prüfsummen, MWST, Protokoll, Rollen der Handys, Statusanzeige für Handys."""
from __future__ import annotations

import base64
import gzip
import json
import uuid

import pytest

from tests.test_api import AUTH, setup_shop


def _order(c, lines, table="5", device="XCover 1", payment=None, test=False):
    body = {"client_uuid": str(uuid.uuid4()), "table": table, "device": device, "lines": lines}
    if payment:
        body["payment"] = payment
    if test:
        body["test"] = True
    r = c.post("/api/orders", json=body)
    assert r.status_code == 200, r.text
    return r.json()["order_id"], body["client_uuid"]


def _pids(c):
    return {p["name"]: p["id"] for p in c.get("/api/bootstrap").json()["products"]}


def _storno(c, oid, lines=None, reason="wrong", note="", auth=AUTH):
    body = {"client_uuid": str(uuid.uuid4()), "reason": reason, "note": note}
    if lines is not None:
        body["lines"] = [{"line_no": k, "qty": v} for k, v in lines.items()]
    return c.post(f"/api/admin/orders/{oid}/storno", auth=auth, json=body)


@pytest.fixture
def shop(client):
    setup_shop(client)
    return _pids(client)


def test_payment_at_order_phone_and_admin(client, shop):
    oid, cu = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 2}], payment="twint")
    assert client.get(f"/api/admin/orders/{oid}", auth=AUTH).json()["payment"] == "twint"
    oid2, cu2 = _order(client, [{"product_id": shop["Mineral"], "qty": 1}])
    assert client.get(f"/api/admin/orders/{oid2}", auth=AUTH).json()["payment"] is None      # nicht angegeben
    assert client.post("/api/orders/payment", json={"client_uuid": cu2, "payment": "card"}).status_code == 200
    assert client.get(f"/api/admin/orders/{oid2}", auth=AUTH).json()["payment"] == "card"
    assert client.post("/api/orders/payment", json={"client_uuid": "x" * 36, "payment": "card"}).status_code == 404
    assert client.post("/api/orders/payment", json={"client_uuid": cu2, "payment": "gold"}).status_code == 422
    # Verwaltung ändert -> Protokoll
    assert client.put(f"/api/admin/orders/{oid2}/payment", auth=AUTH, json={"payment": "cash"}).status_code == 200
    log = client.get("/api/admin/audit", auth=AUTH).json()["entries"]
    assert log[0]["action"] == "payment" and log[0]["details"] == {"order": oid2, "old": "card", "new": "cash"}
    boot = client.get("/api/bootstrap").json()
    assert boot["payment_mode"] == "off" and boot["can_storno"] is False
    client.put("/api/admin/settings", auth=AUTH, json={"payment_mode": "required"})
    assert client.get("/api/bootstrap").json()["payment_mode"] == "required"


def test_storno_partial_full_and_bons(client, shop):
    from app import core
    oid, _ = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 3}, {"product_id": shop["Bratwurst"], "qty": 2}])
    st = core.rt.store
    # Bons tun so, als wären sie gedruckt
    st.execute("UPDATE print_jobs SET status='done' WHERE order_id=?", (oid,))
    d = client.get(f"/api/admin/orders/{oid}", auth=AUTH).json()
    beer = next(i for i in d["items"] if i["name"] == "Bier 5dl")
    r = _storno(client, oid, {beer["line_no"]: 1}, "wrong", "1 zu viel")
    assert r.status_code == 200, r.text
    assert r.json()["amount_cents"] == 650 and r.json()["full"] is False
    jobs = st.query("SELECT kind, station_id, status FROM print_jobs WHERE order_id=? AND kind='storno'", (oid,))
    assert len(jobs) == 1                                         # nur an die Getränkeausgabe
    # Storno-Bon enthält STORNO, Tisch und Grund
    from app import print_service as ps
    job = st.query_one("SELECT * FROM print_jobs WHERE order_id=? AND kind='storno'", (oid,))
    data = ps.render_job(st, job, ps.load_printers(st))
    raw = data.decode("cp858", "replace")
    assert "STORNO" in raw and "TISCH 5" in raw and "1 zu viel" in raw
    # Produkt durchgestrichen: als Rasterbild (GS v 0), nicht als Text
    from tests.fake_printer import to_text
    assert b"\x1dv0\x00" in data and "Bier 5dl" not in raw and "[Bild]" in to_text(data)[0]
    assert _storno(client, oid, {beer["line_no"]: 3}).status_code == 422          # nur noch 2 offen
    d = client.get(f"/api/admin/orders/{oid}", auth=AUTH).json()
    assert d["storno_cents"] == 650 and d["net_cents"] == 3 * 650 + 2 * 800 - 650
    assert next(i for i in d["items"] if i["name"] == "Bier 5dl")["cancelled"] == 1
    # der Rest: alles (2 Bier, 2 Wurst) -> an beide Stationen
    r = _storno(client, oid, None, "unpaid")
    assert r.status_code == 200 and r.json()["amount_cents"] == 2 * 650 + 2 * 800 and r.json()["full"] is True
    assert len(r.json()["printer_ids"]) == 2
    assert _storno(client, oid, None).status_code == 422                            # nichts mehr offen
    # ganz storniert, bevor gedruckt: Bons zurückhalten, kein Storno-Bon
    oid2, _ = _order(client, [{"product_id": shop["Bratwurst"], "qty": 1}])
    r = _storno(client, oid2)
    assert r.status_code == 200 and r.json()["printer_ids"] == []
    statuses = {j["status"] for j in st.query("SELECT status FROM print_jobs WHERE order_id=?", (oid2,))}
    assert statuses == {"cancelled"}
    # Umsatz in der Übersicht nach Storno
    stats = client.get("/api/admin/stats", auth=AUTH).json()
    assert stats["total_cents"] == 0 and stats["open_orders"] == 2


def test_phone_storno_needs_role(client, shop, monkeypatch):
    from app import devices
    oid, cu = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 2}])
    body = {"client_uuid": str(uuid.uuid4()), "order_uuid": cu, "lines": [{"line_no": 1, "qty": 1}], "reason": "wrong"}
    assert client.post("/api/orders/storno", json=body).status_code == 403            # ohne Gerätezertifikat
    monkeypatch.setattr(devices, "client_cert", lambda request: {"name": "XCover 1", "serial": "abc", "days_left": 900})
    assert client.post("/api/orders/storno", json=body).status_code == 403            # Rolle «Bedienung»
    assert client.get("/api/bootstrap").json()["can_storno"] is False
    assert client.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "storno"}).status_code == 200
    # Zertifikat, das dieses System nicht ausgestellt hat (nicht in device_certs): Rolle wie vor T1 nach dem Namen
    from app import core
    assert core.rt.store.scalar("SELECT role FROM device_roles WHERE name='XCover 1'") == "storno"
    assert client.get("/api/bootstrap").json()["can_storno"] is True
    r = client.post("/api/orders/storno", json=body)
    assert r.status_code == 200 and r.json()["amount_cents"] == 650
    # Positionen fürs Storno-Fenster: mit dem, was schon storniert ist
    d = client.get(f"/api/orders/lines?uuid={cu}").json()
    assert d["closed"] is False and d["storno_cents"] == 650 and d["net_cents"] == 650
    assert d["items"] == [{"line_no": 1, "product_id": 1, "name": "Bier 5dl", "options": None, "note": None, "price_cents": 650,
                           "qty": 2, "cancelled": 1}]
    assert client.get("/api/orders/lines?uuid=unbekannt-123").status_code == 404
    again = client.post("/api/orders/storno", json=body)                             # gleiche Nummer: nur einmal
    assert again.status_code == 200 and again.json()["duplicate"] is True
    d = client.get(f"/api/admin/orders/{oid}", auth=AUTH).json()
    assert d["storno_cents"] == 650 and d["cancellations"][0]["by_device"] == "XCover 1"
    log = client.get("/api/admin/audit?q=storno", auth=AUTH).json()["entries"]
    assert log[0]["actor"] == "device:XCover 1"
    # Rolle nur für Admin
    from app import core
    core.login_throttle.clear()
    client.post("/api/admin/users", auth=AUTH, json={"name": "Lea", "role": "lead", "password": "sicher-123"})
    tok = client.post("/api/admin/login", json={"name": "Lea", "password": "sicher-123"}).json()["token"]
    lea = {"Authorization": f"Bearer {tok}"}
    # Rolle eines Handys setzen darf die Festleitung seit T5 (Seite «Geräte»), Benutzer verwalten weiterhin nicht
    assert client.put("/api/admin/devices/role", headers=lea, json={"name": "X", "role": "storno"}).status_code == 200
    assert client.get("/api/admin/users", headers=lea).status_code == 403
    assert _storno(client, oid, None, auth=None).status_code == 401
    r = client.post(f"/api/admin/orders/{oid}/storno", headers=lea,
                    json={"client_uuid": str(uuid.uuid4()), "reason": "complaint"})
    assert r.status_code == 200                                   # Festleitung darf stornieren


def test_closing_cash_check_vat_and_integrity(client, shop):
    from app import core
    st = core.rt.store
    client.put("/api/admin/vat", auth=AUTH, json={"enabled": True, "number": "CHE-123.456.789 MWST"})
    o1, _ = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 2}], device="XCover 1", payment="cash")   # 13.00
    o2, _ = _order(client, [{"product_id": shop["Bratwurst"], "qty": 1}], device="XCover 2", payment="card")  # 8.00
    o3, _ = _order(client, [{"product_id": shop["Mineral"], "qty": 1}], device="XCover 2")                    # 4.00
    client.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": "1357"})
    _order(client, [{"product_id": shop["Mineral"], "qty": 5}], test=True)               # Übung: zählt nicht
    assert _storno(client, o1, {1: 1}).status_code == 200                                # −6.50
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert (s["orders"], s["gross"], s["storno"], s["net"]) == (3, 2500, 650, 1850)
    assert s["by_payment"] == {"cash": 650, "card": 800, "none": 400}
    dev = {d["device"]: d for d in s["by_device"]}
    assert dev["XCover 1"]["net"] == 650 and dev["XCover 2"]["orders"] == 2 and dev["XCover 2"]["net"] == 1200
    assert s["by_vat"] == [{"code": "normal", "bp": 810, "gross": 1850, "vat": 139, "net": 1711}]
    assert s["check"]["ok"] == 4 and s["check"]["bad"] == [] and s["check"]["gaps"] == []
    # nachträglich in der Datenbank geändert -> fällt auf
    st.execute("UPDATE order_items SET price_cents=100 WHERE order_id=?", (o3,))
    assert client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]["check"]["bad"] == [f"#{o3:04d}"]
    st.execute("UPDATE order_items SET price_cents=400 WHERE order_id=?", (o3,))
    # Abschluss: Wechselgeld 100.–, gezählt 106.00, Karte 8.–, TWINT 0
    r = client.post("/api/admin/closings", auth=AUTH, json={"float_cents": 10000, "counted_cents": 10600,
                                                            "card_cents": 800, "twint_cents": 0, "note": "Abend 1"})
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["id"] == 1 and c["net_cents"] == 1850 and c["expected_cents"] == 10000 + 1850 - 800
    assert c["difference_cents"] == 10600 - 11050
    assert client.post("/api/admin/closings", auth=AUTH, json={}).status_code == 409    # nichts mehr offen
    assert client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]["orders"] == 0
    assert _storno(client, o2).status_code == 409                                         # abgeschlossen
    # PDF, CSV, Prüfung
    pdf = client.get("/api/admin/closings/1/pdf", auth=AUTH)
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    text = client.get("/api/admin/closings/1/csv", auth=AUTH).content.decode("utf-8-sig")
    assert text.startswith("typ;nummer;") and "storno" in text and ";-1;" in text and "-6.50" in text
    assert client.get("/api/admin/closings/1/verify", auth=AUTH).json()["ok"] is True
    # zweiter Abschluss ist mit dem ersten verkettet
    o4, _ = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 1}])
    c2 = client.post("/api/admin/closings", auth=AUTH, json={}).json()
    row = st.query_one("SELECT prev_hash FROM closings WHERE id=2")
    assert c2["id"] == 2 and row["prev_hash"] == c["chain_hash"]
    st.execute("UPDATE closings SET net_cents=99999 WHERE id=1")                          # Manipulation
    v = client.get("/api/admin/closings/1/verify", auth=AUTH).json()
    assert v["ok"] is False and v["hash_ok"] is False
    assert [x["id"] for x in client.get("/api/admin/closings", auth=AUTH).json()["closings"]] == [2, 1]
    log = [e["action"] for e in client.get("/api/admin/audit", auth=AUTH).json()["entries"]]
    assert log.count("closing") == 2 and "vat" in log and "storno" in log


def test_reset_only_after_closing_and_closings_stay(client, shop):
    _order(client, [{"product_id": shop["Bier 5dl"], "qty": 1}])
    r = client.post("/api/admin/reset-orders?confirm=JA", auth=AUTH)
    assert r.status_code == 409 and "Tagesabschluss" in r.json()["detail"]
    client.post("/api/admin/closings", auth=AUTH, json={})
    assert client.post("/api/admin/reset-orders?confirm=JA", auth=AUTH).json()["deleted_orders"] == 1
    assert len(client.get("/api/admin/closings", auth=AUTH).json()["closings"]) == 1
    csv = client.get("/api/admin/closings/1/csv", auth=AUTH).content.decode("utf-8-sig")
    assert "Bier 5dl" in csv                                       # Bestellungen bleiben im Abschluss
    assert client.get("/api/admin/closings/1/verify", auth=AUTH).json()["ok"] is True
    assert "reset_orders" in [e["action"] for e in client.get("/api/admin/audit", auth=AUTH).json()["entries"]]


def test_vat_rates_and_snapshot(client, shop):
    from datetime import date, timedelta

    from app import core
    client.put("/api/admin/vat", auth=AUTH, json={"enabled": True, "number": ""})
    cats = {c["name"]: c for c in client.get("/api/admin/categories", auth=AUTH).json()}
    essen = cats["Essen"]
    assert client.put(f"/api/admin/categories/{essen['id']}", auth=AUTH,
                      json={"name": "Essen", "color": essen["color"], "sort": essen["sort"], "vat_code": "reduced"}
                      ).status_code == 200
    client.put(f"/api/admin/products/{shop['Mineral']}/vat", auth=AUTH, json={"vat_code": "lodging"})
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    v = client.post("/api/admin/vat/rates", auth=AUTH, json={"code": "normal", "rate_bp": 850, "valid_from": tomorrow}).json()
    assert v["current"]["normal"] == 810                                  # gilt erst ab morgen
    assert client.post("/api/admin/vat/rates", auth=AUTH,
                       json={"code": "normal", "rate_bp": 850, "valid_from": tomorrow}).status_code == 409
    oid, _ = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 1}, {"product_id": shop["Bratwurst"], "qty": 1},
                             {"product_id": shop["Mineral"], "qty": 1}])
    items = {i["name"]: (i["vat_code"], i["vat_bp"]) for i in
             core.rt.store.query("SELECT name, vat_code, vat_bp FROM order_items WHERE order_id=?", (oid,))}
    assert items == {"Bier 5dl": ("normal", 810), "Bratwurst": ("reduced", 260), "Mineral": ("lodging", 380)}
    rates = {(r["code"], r["valid_from"]): r["id"] for r in v["rates"]}
    past = rates[("normal", "2024-01-01")]
    assert client.delete(f"/api/admin/vat/rates/{past}", auth=AUTH).status_code == 409   # gehört zu Bestellungen
    assert client.delete(f"/api/admin/vat/rates/{rates[('normal', tomorrow)]}", auth=AUTH).status_code == 200
    # ohne MWST-Pflicht: nichts gespeichert
    client.put("/api/admin/vat", auth=AUTH, json={"enabled": False, "number": ""})
    oid2, _ = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 1}])
    assert core.rt.store.scalar("SELECT vat_bp FROM order_items WHERE order_id=?", (oid2,)) is None


def test_vat_split_rounding():
    from app.ledger_base import vat_split
    assert vat_split(650, 810) == (49, 601)
    assert vat_split(10810, 810) == (810, 10000)
    assert vat_split(-650, 810) == (-49, -601)
    assert vat_split(0, 260) == (0, 0)


def test_audit_masks_secrets_and_logs_prices(client, shop):
    client.put("/api/admin/settings", auth=AUTH, json={"pin_enabled": True, "pin": "4711", "event_name": "Fest"})
    e = client.get("/api/admin/audit?q=settings", auth=AUTH).json()["entries"][0]
    assert e["details"]["pin"] == "***" and e["details"]["event_name"] == "Fest" and "4711" not in json.dumps(e)
    p = next(x for x in client.get("/api/admin/products", auth=AUTH).json() if x["name"] == "Bier 5dl")
    client.put(f"/api/admin/products/{p['id']}", auth=AUTH, json={"name": p["name"], "price_cents": 700,
               "category_id": p["category_id"], "printer_id": p["printer_id"]})
    e = client.get("/api/admin/audit?q=product_update", auth=AUTH).json()["entries"][0]
    assert e["details"]["old"]["price"] == 650 and e["details"]["new"]["price"] == 700 and e["actor"] == "admin"


def test_roles_for_ledger_routes(client, shop):
    from app import core
    core.login_throttle.clear()
    client.post("/api/admin/users", auth=AUTH, json={"name": "Vera", "role": "view", "password": "sicher-123"})
    tok = client.post("/api/admin/login", json={"name": "Vera", "password": "sicher-123"}).json()["token"]
    vera = {"Authorization": f"Bearer {tok}"}
    oid, _ = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 1}])
    assert client.get("/api/admin/orders", headers=vera).status_code == 200            # ansehen ja
    assert client.get(f"/api/admin/orders/{oid}", headers=vera).status_code == 200
    assert client.post(f"/api/admin/orders/{oid}/storno", headers=vera,
                       json={"client_uuid": str(uuid.uuid4()), "reason": "wrong"}).status_code == 403
    assert client.get("/api/admin/closings/open", headers=vera).status_code == 403
    assert client.get("/api/admin/audit", headers=vera).status_code == 403
    assert client.put("/api/admin/vat", headers=vera, json={"enabled": True}).status_code == 403
    lst = client.get("/api/admin/orders?q=5", auth=AUTH).json()["orders"]
    assert [o["id"] for o in lst] == [oid] and lst[0]["storno_cents"] == 0


def test_status_for_phones_with_certificate(client, shop, monkeypatch):
    from app import core, devices
    _order(client, [{"product_id": shop["Bier 5dl"], "qty": 2}])
    monkeypatch.setattr(core.Config, "phone_port", 443)
    url = "https://testserver:443/api/status"
    assert client.get(url).status_code == 401                                            # ohne Zertifikat
    monkeypatch.setattr(devices, "client_cert", lambda request: {"name": "XCover 1", "serial": "abc", "days_left": 900})
    s = client.get(url).json()
    assert s["viewer"] == "device" and s["nav"] == {"app": True, "admin": True} and s["me"] is None
    s = client.get("/api/status", auth=AUTH).json()                                      # Verwaltungs-Port, angemeldet
    assert s["viewer"] == "user" and s["nav"] == {"app": False, "admin": True}
    assert client.get("https://testserver:443/api/nav").json() == {"phone": True, "phone_port": 443, "tabs": False}
    assert client.get("/api/nav").json() == {"phone": False, "phone_port": 443, "tabs": False}
    core.rt.store.execute("UPDATE device_certs SET revoked_at='x'")                      # (keine Zertifikate da)
    monkeypatch.setattr(devices, "revoked_serials", lambda: {"abc"})
    assert client.get(url).status_code == 401                                            # gesperrt


def test_closing_survives_backup_and_csv_is_complete(client, shop):
    from app import ledger
    oid, _ = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 2}], payment="cash")
    _storno(client, oid, {1: 1})
    c = client.post("/api/admin/closings", auth=AUTH, json={"counted_cents": 650}).json()
    assert c["difference_cents"] == 0
    from app import core
    raw = core.rt.store.scalar("SELECT orders_csv FROM closings WHERE id=?", (c["id"],))
    lines = gzip.decompress(base64.b64decode(raw)).decode("utf-8-sig").strip().splitlines()
    assert lines[0].split(";") == ledger.CSV_HEADER and len(lines) == 3


def test_storno_closing_and_vat_on_store_backends(store):
    """Dieselbe Logik direkt auf dem Store – auch mit echtem rqlite (Transaktion, RETURNING, Unterabfragen)."""
    from app import ledger
    from app import ledger_base as lb
    from app.orders import create_order
    from tests.test_printing import seed
    seed(store, 9, 9)
    store.execute("INSERT INTO settings(key,value) VALUES ('vat_enabled','1')")
    a = create_order(store, client_uuid=str(uuid.uuid4()), table_label="5", lines=[(1, 2), (3, 1)], payment="card")
    b = create_order(store, client_uuid=str(uuid.uuid4()), table_label="6", lines=[(2, 1)])
    items = store.query("SELECT line_no, name, vat_bp FROM order_items WHERE order_id=? ORDER BY line_no", (a.order_id,))
    assert [i["vat_bp"] for i in items] == [810, 810]
    beer = next(i["line_no"] for i in items if i["name"] == "Bier 5dl")
    cu = str(uuid.uuid4())
    r = ledger.cancel(store, order_id=a.order_id, lines={beer: 1}, reason="wrong", by_user="Lea", client_uuid=cu)
    assert r["amount_cents"] == 650 and r["full"] is False
    again = ledger.cancel(store, order_id=a.order_id, lines={beer: 1}, reason="wrong", by_user="Lea", client_uuid=cu)
    assert again["duplicate"] is True                                     # gleiche Nummer: nur einmal gebucht
    assert ledger.cancelled_qty(store, a.order_id) == {store.scalar(
        "SELECT id FROM order_items WHERE order_id=? AND line_no=?", (a.order_id, beer)): 1}
    ledger.set_payment(store, payment="cash", order_id=b.order_id, actor="Lea")
    s = ledger.open_summary(store)
    assert s["orders"] == 2 and s["gross"] == 2 * 650 + 800 + 400 and s["storno"] == 650
    assert s["by_payment"] == {"cash": 400, "card": 650 + 800}
    assert s["check"]["ok"] == 3 and not s["check"]["bad"]
    c = ledger.create_closing(store, "Lea", ledger.ClosingIn(float_cents=10000, counted_cents=10400))
    assert c["expected_cents"] == 10000 + s["net"] and c["difference_cents"] == 10400 - (10000 + s["net"])
    assert ledger.verify_closing(store, c["id"])["ok"] is True
    with pytest.raises(ledger.LedgerError):                               # abgeschlossen: kein Storno mehr
        ledger.cancel(store, order_id=a.order_id, lines=None, reason="other", by_user="Lea", client_uuid=str(uuid.uuid4()))
    assert ledger.open_orders(store) == 0
    assert [e["action"] for e in store.query("SELECT action FROM audit_log ORDER BY id")] == ["storno", "payment", "closing"]
    assert lb.vat_split(-1000, 810) == (-75, -925)


def test_order_shows_rolled_out_device_and_operator(client, shop, monkeypatch):
    """Version 2026.10.10-21: «XCover101 (Anna)» – Gerät aus dem Zertifikat, in Klammern der Name aus der App."""
    from app import core, devices
    from app import ledger_base as lb
    from app import print_service as ps
    assert lb.device_label("XCover101", "Anna") == "XCover101 (Anna)"
    assert lb.device_label("XCover101", "xcover101") == "XCover101" and lb.device_label(None, "Anna") == "Anna"
    assert lb.device_label("XCover101", "  ") == "XCover101"
    old, _ = _order(client, [{"product_id": shop["Mineral"], "qty": 1}], device="Ohne Zertifikat")
    monkeypatch.setattr(devices, "client_cert", lambda request: {"name": "XCover101", "serial": "abc", "days_left": 900})
    oid, cu = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 2}], device="Anna")
    st = core.rt.store
    assert st.scalar("SELECT device_cert FROM orders WHERE id=?", (oid,)) == "XCover101"
    lst = {o["id"]: o for o in client.get("/api/admin/orders", auth=AUTH).json()["orders"]}
    assert lst[oid]["device"] == "XCover101 (Anna)" and lst[old]["device"] == "Ohne Zertifikat"
    assert client.get(f"/api/admin/orders/{oid}", auth=AUTH).json()["device"] == "XCover101 (Anna)"
    assert [o["id"] for o in client.get("/api/admin/orders?q=xcover101", auth=AUTH).json()["orders"]] == [oid]
    s = client.get("/api/status", auth=AUTH).json()
    assert next(o for o in s["recent"] if o["id"] == oid)["device"] == "XCover101 (Anna)"
    job = st.query_one("SELECT * FROM print_jobs WHERE order_id=? AND kind='table'", (oid,))
    assert "XCover101 (Anna)" in ps.render_job(st, job, ps.load_printers(st)).decode("cp858", "replace")
    # Storno am Handy: wer storniert hat, ebenso
    client.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover101", "role": "storno"})
    r = client.post("/api/orders/storno", headers={"X-Festpos-Device": "Anna"},
                    json={"client_uuid": str(uuid.uuid4()), "order_uuid": cu, "lines": None, "reason": "wrong"})
    assert r.status_code == 200
    assert client.get(f"/api/admin/orders/{oid}", auth=AUTH).json()["cancellations"][0]["by_device"] == "XCover101 (Anna)"
    # Abschluss: pro Gerät und Bedienung, Prüfsummen (neue mit Gerät, alte ohne) in Ordnung
    summ = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert {d["device"] for d in summ["by_device"]} == {"XCover101 (Anna)", "Ohne Zertifikat"}
    assert not summ["check"]["bad"]
    st.execute("UPDATE orders SET device_cert='XCover202' WHERE id=?", (oid,))          # nachträglich verändert
    assert client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]["check"]["bad"] == [f"#{oid:04d}"]
    csv_text = client.get("/api/admin/export/orders.csv", auth=AUTH).content.decode("utf-8-sig")
    assert "XCover202 (Anna)" in csv_text


def test_order_states_for_the_phone_history(client, shop):
    """der Verlauf auf dem Handy fragt nach, was aus seinen Bestellungen geworden ist – offen, ganz
    storniert, abgeschlossen, gelöscht (Tagesabschluss bzw. «Bestellungen löschen» auf dem Pi). Nur der Stand."""
    st = lambda *u: client.post("/api/orders/status", json={"uuids": list(u)}).json()["states"]  # noqa: E731
    assert client.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": "2468"}).status_code == 200
    o1, u1 = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 1}])
    o2, u2 = _order(client, [{"product_id": shop["Mineral"], "qty": 2}])
    o3, u3 = _order(client, [{"product_id": shop["Mineral"], "qty": 1}], test=True)
    assert st(u1, u2, u3, "unbekannt-123") == {u1: "open", u2: "open", u3: "open", "unbekannt-123": "gone"}
    assert _storno(client, o2).status_code == 200                                    # ganz storniert
    assert st(u2) == {u2: "storno"}
    assert client.post("/api/admin/test-orders/delete?confirm=JA", auth=AUTH).status_code == 200
    assert st(u3) == {u3: "gone"}                                                    # Übung gelöscht
    assert client.post("/api/admin/closings", auth=AUTH, json={}).status_code == 200
    assert st(u1, u2) == {u1: "closed", u2: "closed"}                                # im Tagesabschluss
    assert client.post("/api/admin/reset-orders?confirm=JA", auth=AUTH).status_code == 200
    assert st(u1, u2) == {u1: "gone", u2: "gone"}
    # nur Kennungen, nie mehr als STATUS_MAX; Ungültiges fällt weg
    assert st("x y", "") == {} and st() == {}
    assert client.post("/api/orders/status", json={"uuids": ["a" * 10] * 61}).status_code == 422
