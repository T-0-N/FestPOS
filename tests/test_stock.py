"""Ausverkauft und Restmenge (app/stock.py): Verwaltung, Handy mit Rolle «Festleitung», Bestellung, Statusanzeige."""
from __future__ import annotations

import uuid

import pytest

from tests.test_api import AUTH, setup_shop


def _post(c, lines, test=False):
    body = {"client_uuid": str(uuid.uuid4()), "table": "5", "device": "XCover 1", "lines": lines}
    if test:
        body["test"] = True
    return c.post("/api/orders", json=body)


@pytest.fixture
def shop(client):
    setup_shop(client)
    return {p["name"]: p["id"] for p in client.get("/api/bootstrap").json()["products"]}


def _jobs():
    from app import core
    return core.rt.store.scalar("SELECT COUNT(*) FROM print_jobs")


def test_stock_counts_down_and_refuses(client, shop):
    wurst = shop["Bratwurst"]
    # ohne Bestand: wird nicht gezählt
    assert _post(client, [{"product_id": wurst, "qty": 5}]).status_code == 200
    r = client.put(f"/api/admin/products/{wurst}/stock", auth=AUTH, json={"stock": 3, "stock_warn": 1})
    assert r.status_code == 200 and r.json() == {"ok": True, "soldout": 0, "stock": 3, "stock_warn": 1}
    # zwei Positionen desselben Produkts (mit/ohne Option) zählen zusammen
    assert _post(client, [{"product_id": wurst, "qty": 1}, {"product_id": wurst, "qty": 1, "note": "scharf"}]).status_code == 200
    st = client.get("/api/phone/status").json()["stock"]
    assert st == {"soldout": [], "stock": {str(wurst): 1}, "warn": {str(wurst): 1}}
    assert client.get("/api/status", auth=AUTH).json()["stock"] == [{"name": "Bratwurst", "soldout": False, "stock": 1}]
    # zu viel: ganze Bestellung abgelehnt, keine Bons, Restmenge unverändert
    jobs = _jobs()
    r = _post(client, [{"product_id": shop["Bier 5dl"], "qty": 1}, {"product_id": wurst, "qty": 2}])
    assert r.status_code == 422 and r.json()["detail"] == "Von Bratwurst hat es nur noch 1"
    assert _jobs() == jobs
    # Übung im Testmodus: prüft mit, zählt aber nicht herunter
    client.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": "1357"})
    assert _post(client, [{"product_id": wurst, "qty": 1}], test=True).status_code == 200
    assert _post(client, [{"product_id": wurst, "qty": 1}]).status_code == 200                # das letzte Stück
    boot = {p["id"]: p for p in client.get("/api/bootstrap").json()["products"]}
    assert boot[wurst]["stock"] == 0 and boot[wurst]["soldout"] == 0
    r = _post(client, [{"product_id": wurst, "qty": 1}])
    assert r.status_code == 422 and r.json()["detail"] == "Ausverkauft: Bratwurst"
    assert client.get("/api/status", auth=AUTH).json()["stock"] == [{"name": "Bratwurst", "soldout": True, "stock": 0}]
    # zurück auf «ohne Bestand»: Warnschwelle fällt mit weg
    r = client.put(f"/api/admin/products/{wurst}/stock", auth=AUTH, json={"stock": None, "stock_warn": 5})
    assert r.json()["stock_warn"] is None
    assert _post(client, [{"product_id": wurst, "qty": 1}]).status_code == 200
    log = client.get("/api/admin/audit?q=product_stock", auth=AUTH).json()["entries"]
    assert log[0]["details"]["name"] == "Bratwurst" and log[0]["details"]["stock"] is None


def test_soldout_by_admin_and_lead_phone(client, shop, monkeypatch):
    from app import core, devices
    bier = shop["Bier 5dl"]
    assert client.put("/api/admin/products/999/stock", auth=AUTH, json={"soldout": True}).status_code == 404
    assert client.put(f"/api/admin/products/{bier}/stock", auth=AUTH, json={"soldout": True}).status_code == 200
    assert client.get("/api/phone/status").json()["stock"]["soldout"] == [bier]
    r = _post(client, [{"product_id": bier, "qty": 1}])
    assert r.status_code == 422 and "Bier 5dl" in r.json()["detail"] and _jobs() == 0
    # Handy: nur mit der Rolle «Festleitung»
    url = f"/api/phone/products/{bier}/soldout"
    assert client.post(url, json={"soldout": False}).status_code == 403                      # ohne Zertifikat
    monkeypatch.setattr(devices, "client_cert", lambda request: {"name": "XCover 1", "serial": "abc", "days_left": 900})
    boot = client.get("/api/bootstrap").json()
    assert boot["can_soldout"] is False and boot["can_storno"] is False
    assert client.post(url, json={"soldout": False}).status_code == 403                      # Bedienung
    client.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "storno"})
    assert client.post(url, json={"soldout": False}).status_code == 403                      # Bedienung + Storno
    assert client.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "lead"}).status_code == 200
    boot = client.get("/api/bootstrap").json()
    assert boot["can_soldout"] is True and boot["can_storno"] is True                       # Festleitung darf auch stornieren
    r = client.post(url, json={"soldout": False}, headers={"X-Festpos-Device": "Anna"})
    assert r.status_code == 200 and r.json()["soldout"] == []
    assert _post(client, [{"product_id": bier, "qty": 1}]).status_code == 200
    # Restmenge 0 wieder freigeben = ohne Bestand (sonst bliebe die Kachel grau)
    core.rt.store.execute("UPDATE products SET stock=0 WHERE id=?", (bier,))
    assert client.post(url, json={"soldout": True}).json()["soldout"] == [bier]
    r = client.post(url, json={"soldout": False}).json()
    assert r["soldout"] == [] and r["stock"] == {}
    assert client.post("/api/phone/products/999/soldout", json={"soldout": True}).status_code == 404
    log = client.get("/api/admin/audit?q=product_soldout", auth=AUTH).json()["entries"]
    assert log[-1]["actor"] == "device:XCover 1 (Anna)" and log[-1]["details"] == {"product": bier, "name": "Bier 5dl",
                                                                                  "soldout": False}
    # Verwaltung: Festleitung darf den Bestand pflegen, Anzeige nicht
    core.login_throttle.clear()
    for name, role in (("Lea", "lead"), ("Vera", "view")):
        client.post("/api/admin/users", auth=AUTH, json={"name": name, "role": role, "password": "sicher-123"})
    hdr = {n: {"Authorization": "Bearer " + client.post("/api/admin/login", json={"name": n, "password": "sicher-123"}).json()["token"]}
           for n in ("Lea", "Vera")}
    assert client.put(f"/api/admin/products/{bier}/stock", headers=hdr["Lea"], json={"stock": 10}).status_code == 200
    assert client.put(f"/api/admin/products/{bier}/stock", headers=hdr["Vera"], json={"stock": 10}).status_code == 403


def test_last_pieces_race_on_store_backends(store):
    """Zwei Handys verkaufen gleichzeitig die letzten Stück: der CHECK in der Datenbank lehnt die zweite Bestellung als
    Ganzes ab (auch mit echtem rqlite) – keine Bestellung, keine Bons, Restmenge nie negativ."""
    from app.orders import OrderError, create_order
    from tests.test_printing import seed
    store.init_schema()
    seed(store, 9, 9)
    store.execute("UPDATE products SET stock=2 WHERE id=3")
    create_order(store, client_uuid=str(uuid.uuid4()), table_label="5", lines=[(3, 1)])
    assert store.scalar("SELECT stock FROM products WHERE id=3") == 1

    real = store.batch

    def other_phone_first(stmts, transaction=True):    # zwischen Prüfen und Schreiben kommt ein anderes Handy
        if any("INSERT INTO orders" in s for s, _ in stmts):
            real([("UPDATE products SET stock=0 WHERE id=3", ())])
        return real(stmts, transaction)
    store.batch = other_phone_first
    orders = store.scalar("SELECT COUNT(*) FROM orders")
    jobs = store.scalar("SELECT COUNT(*) FROM print_jobs")
    with pytest.raises(OrderError, match="Ausverkauft: Bratwurst mit Bürli"):
        create_order(store, client_uuid=str(uuid.uuid4()), table_label="6", lines=[(1, 1), (3, 1)])
    store.batch = real
    assert store.scalar("SELECT COUNT(*) FROM orders") == orders
    assert store.scalar("SELECT COUNT(*) FROM print_jobs") == jobs
    assert store.scalar("SELECT stock FROM products WHERE id=3") == 0


def test_rejection_lists_all_products_and_new_state(client, shop):
    wurst, bier = shop["Bratwurst"], shop["Bier 5dl"]
    client.put(f"/api/admin/products/{wurst}/stock", auth=AUTH, json={"soldout": True})
    client.put(f"/api/admin/products/{bier}/stock", auth=AUTH, json={"stock": 1, "stock_warn": 3})
    r = _post(client, [{"product_id": bier, "qty": 2}, {"product_id": bier, "qty": 1, "note": "kalt"},
                       {"product_id": wurst, "qty": 2}, {"product_id": shop["Mineral"], "qty": 1}])
    assert r.status_code == 422 and _jobs() == 0
    j = r.json()
    assert j["detail"] == "Ausverkauft: Bratwurst; Von Bier 5dl hat es nur noch 1"
    assert j["stock_problems"] == [{"product_id": wurst, "name": "Bratwurst", "left": 0},
                                   {"product_id": bier, "name": "Bier 5dl", "left": 1}]
    assert j["stock"] == {"soldout": [wurst], "stock": {str(bier): 1}, "warn": {str(bier): 3}}   # Kacheln sofort grau
    # andere Ablehnungen: wie bisher nur der Text
    client.put("/api/admin/settings", auth=AUTH, json={"tables_mode": "strict"})
    body = {"client_uuid": str(uuid.uuid4()), "table": "999", "lines": [{"product_id": shop["Mineral"], "qty": 1}]}
    assert client.post("/api/orders", json=body).json() == {"detail": "Tisch 999 ist nicht erfasst"}


def test_check_setting(client, shop):
    assert client.get("/api/bootstrap").json()["stock_check"] == {"mode": "always", "wait": 2}       # Standard
    assert client.put("/api/admin/settings", auth=AUTH, json={"stock_check": "stock", "stock_check_wait": 5}).status_code == 200
    assert client.get("/api/bootstrap").json()["stock_check"] == {"mode": "stock", "wait": 5}
    assert client.put("/api/admin/settings", auth=AUTH, json={"stock_check": "off"}).status_code == 200
    assert client.get("/api/bootstrap").json()["stock_check"]["mode"] == "off"
    assert client.put("/api/admin/settings", auth=AUTH, json={"stock_check": "manchmal"}).status_code == 422
    assert client.put("/api/admin/settings", auth=AUTH, json={"stock_check_wait": 60}).status_code == 422


def _import(c, csv: str, commit=False):
    r = c.post(f"/api/admin/import/products?commit={'true' if commit else 'false'}", auth=AUTH,
               files={"file": ("p.csv", csv.encode("utf-8"))})
    assert r.status_code == 200, r.text
    return r.json()


def test_csv_export_and_import_overwrite_stock(client, shop):
    wurst = shop["Bratwurst"]
    client.put(f"/api/admin/products/{wurst}/stock", auth=AUTH, json={"stock": 37, "stock_warn": 15})
    client.put(f"/api/admin/products/{shop['Mineral']}/stock", auth=AUTH, json={"soldout": True})
    lines = client.get("/api/admin/export/products.csv", auth=AUTH).content.decode("utf-8-sig").splitlines()
    head = lines[0].split(";")
    assert head[-3:] == ["bestand", "warnen_ab", "ausverkauft"]
    rows = {ln.split(";")[0]: ln.split(";") for ln in lines[1:]}
    assert rows["Bratwurst"][-3:] == ["37", "15", ""] and rows["Mineral"][-3:] == ["", "", "ja"]
    assert rows["Bier 5dl"][-3:] == ["", "", ""]
    # die eigene Exportdatei wieder einlesen: nichts zu ändern
    assert _import(client, "\r\n".join(lines))["summary"]["update"] == 0
    # überschreiben: Restmenge, ohne Bestand, ausverkauft weg; neues Produkt mit Bestand
    csv = ("name;preis;bestand;warnen_ab;ausverkauft\r\n"
           "Bratwurst;8.00;120;15;\r\nMineral;4.00;;;nein\r\nBier 5dl;6.50;40;;\r\nPommes;5.00;80;10;\r\n")
    plan = _import(client, csv)
    ch = {r["name"]: r["changes"] for r in plan["rows"]}
    assert ch["Bratwurst"] == {"stock": [37, 120]}
    assert ch["Mineral"] == {"soldout": [True, False]} and ch["Bier 5dl"] == {"stock": ["", 40]}
    _import(client, csv, commit=True)
    p = {x["name"]: x for x in client.get("/api/admin/products", auth=AUTH).json()}
    assert (p["Bratwurst"]["stock"], p["Bratwurst"]["stock_warn"]) == (120, 15)
    assert p["Mineral"]["soldout"] == 0 and (p["Bier 5dl"]["stock"], p["Bier 5dl"]["stock_warn"]) == (40, None)
    assert (p["Pommes"]["stock"], p["Pommes"]["stock_warn"], p["Pommes"]["soldout"]) == (80, 10, 0)
    # leere Zelle = ohne Bestand (Warnschwelle fällt weg); Spalte fehlt = unverändert
    _import(client, "name;preis;bestand\r\nBratwurst;8.00;\r\n", commit=True)
    _import(client, "name;preis\r\nPommes;5.50\r\n", commit=True)
    p = {x["name"]: x for x in client.get("/api/admin/products", auth=AUTH).json()}
    assert (p["Bratwurst"]["stock"], p["Bratwurst"]["stock_warn"]) == (None, None)
    assert p["Pommes"]["stock"] == 80 and p["Pommes"]["price_cents"] == 550
    # Unsinn wird gemeldet
    bad = _import(client, "name;preis;bestand;warnen_ab\r\nBratwurst;8.00;viele;-1\r\n")
    assert bad["summary"]["error"] == 1 and len(bad["rows"][0]["errors"]) == 2
    assert "ganze Zahl" in bad["rows"][0]["errors"][0]


def test_lead_phone_sets_remaining_stock(client, shop, monkeypatch):
    """Rückmeldung Punkt 5: «Ausverkauft melden» lange drücken -> Restmenge. Nur Festleitung; 0 = ausverkauft,
    eine Zahl = noch so viele (Kachel wieder frei), null = ohne Restmenge."""
    from app import devices
    bier = shop["Bier 5dl"]
    url = f"/api/phone/products/{bier}/stock"
    assert client.post(url, json={"stock": 3}).status_code == 403                             # ohne Zertifikat
    monkeypatch.setattr(devices, "client_cert", lambda request: {"name": "XCover 1", "serial": "abc", "days_left": 900})
    assert client.post(url, json={"stock": 3}).status_code == 403                             # Bedienung
    client.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "lead"})
    client.put(f"/api/admin/products/{bier}/stock", auth=AUTH, json={"soldout": True})
    r = client.post(url, json={"stock": 3}).json()
    assert r["soldout"] == [] and r["stock"] == {str(bier): 3}
    for _ in range(3):
        assert _post(client, [{"product_id": bier, "qty": 1}]).status_code == 200
    assert _post(client, [{"product_id": bier, "qty": 1}]).status_code == 422                 # mehr gibt es nicht
    r = client.post(url, json={"stock": 0}).json()
    assert bier in r["soldout"]
    r = client.post(url, json={"stock": None}).json()
    assert r["soldout"] == [] and str(bier) not in r["stock"]
    assert client.post(url, json={"stock": -1}).status_code == 422
    assert client.post("/api/phone/products/999/stock", json={"stock": 1}).status_code == 404
    log = client.get("/api/admin/audit?q=product_stock", auth=AUTH).json()["entries"]
    mine = [e for e in log if e["actor"].startswith("device:XCover 1")]
    assert [e["details"]["stock"] for e in mine] in ([3, 0, None], [None, 0, 3])
