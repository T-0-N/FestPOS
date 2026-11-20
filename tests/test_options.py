"""Produkt-Optionen: Definition, CSV, Bestellung mit konfigurierten Positionen, Bon-Inhalt."""
import uuid

from app import options as om
from app.print_service import PrinterCfg, load_printers, render_job
from tests.fake_printer import to_text
from tests.test_api import AUTH, setup_shop


def test_parse_and_format_text():
    o = om.parse_text("Zucker*: ohne|1 Zucker|2 Zucker; Milch+: Hafer|laktosefrei", free=True)
    assert o == {"groups": [{"name": "Zucker", "choices": ["ohne", "1 Zucker", "2 Zucker"], "required": True, "multi": False},
                            {"name": "Milch", "choices": ["Hafer", "laktosefrei"], "required": False, "multi": True}],
                 "free": True}
    assert om.parse_text(om.to_text(o), True) == o
    assert om.parse_text("", False) is None
    assert om.parse_text("", True) == {"groups": [], "free": True}


def _bons(client, order_id):
    import app.main as main
    printers = load_printers(main.store)
    jobs = main.store.query("SELECT * FROM print_jobs WHERE order_id=? ORDER BY id", (order_id,))
    return [(printers[j["station_id"]].name, to_text(render_job(main.store, j, printers))[0]) for j in jobs]


def test_options_order_and_bon(client):
    setup_shop(client)
    prods = {p["name"]: p for p in client.get("/api/admin/products", auth=AUTH).json()}
    kaffee_id = client.post("/api/admin/products", auth=AUTH, json={
        "name": "Kaffee", "price_cents": 350, "category_id": prods["Bier 5dl"]["category_id"],
        "printer_id": prods["Bier 5dl"]["printer_id"]}).json()["id"]
    r = client.put(f"/api/admin/products/{kaffee_id}/options", auth=AUTH, json={
        "groups": [{"name": "Zucker", "choices": ["ohne", "1 Zucker", "2 Zucker"], "required": True}], "free": True})
    assert r.status_code == 200 and r.json()["options"]["groups"][0]["required"] is True
    assert client.put(f"/api/admin/products/{kaffee_id}/options", auth=AUTH,
                      json={"groups": [{"name": "Leer", "choices": []}]}).status_code == 422

    boot = {p["name"]: p for p in client.get("/api/bootstrap").json()["products"]}
    assert boot["Kaffee"]["options"]["free"] is True and boot["Bier 5dl"]["options"] is None

    wurst = prods["Bratwurst"]["id"]                     # Einzelbon pro Stück
    body = {"client_uuid": str(uuid.uuid4()), "table": "12", "device": "XCover 1", "lines": [
        {"product_id": kaffee_id, "qty": 2, "options": ["ohne"]},
        {"product_id": kaffee_id, "qty": 1, "options": ["2 Zucker"], "note": "extra heiss"},
        {"product_id": kaffee_id, "qty": 1, "options": ["ohne"]},            # gleich wie Zeile 1 -> zusammen
        {"product_id": prods["Bier 5dl"]["id"], "qty": 3},
        {"product_id": wurst, "qty": 1, "options": ["Senf"]},
        {"product_id": wurst, "qty": 1},
    ]}
    r = client.post("/api/orders", json=body)
    assert r.status_code == 200, r.text
    oid = r.json()["order_id"]
    assert r.json()["total_cents"] == 4 * 350 + 3 * 650 + 2 * 800

    bons = _bons(client, oid)
    bar = [t for n, t in bons if n == "Getränkeausgabe"]
    kitchen = [t for n, t in bons if n == "Küche"]
    assert len(bar) == 1 and len(kitchen) == 2
    b = bar[0].splitlines()
    assert b[0].startswith("Vereinsfest") and b[0].endswith("Getränkeausgabe")
    assert b[1].strip() == "TISCH 12"
    assert b[2].endswith("XCover 1")
    text = bar[0]
    assert "3x Kaffee" in text and "   ohne" in text
    assert "1x Kaffee" in text and "   2 Zucker" in text and "» extra heiss" in text
    assert "3x Bier 5dl" in text and "TOTAL" in text
    assert "3 Positionen / 7 Stück" in text and f"#{oid:04d}" in text

    # Einzelbons: je ein Bon pro Stück, mit n/m in der Tischzeile und eigener Option
    k1 = [t for t in kitchen if "Senf" in t]
    k2 = [t for t in kitchen if "Senf" not in t]
    assert len(k1) == 1 and len(k2) == 1
    assert k1[0].splitlines()[1].rstrip().endswith("1 von 2")          # n/m über beide Würste gezählt
    assert k2[0].splitlines()[1].rstrip().endswith("2 von 2")
    assert text.index("3x Kaffee") < text.index("1x Kaffee")        # Reihenfolge wie im Warenkorb
    assert "1x Bratwurst" in k1[0] and "1 Position" in k1[0]   # Preise: eine Einstellung für alle Bons


def test_csv_options_roundtrip(client):
    setup_shop(client)
    csv = ("name;preis;kategorie;drucker;optionen;freitext\r\n"
           'Kaffee;3.50;Getränke;Getränkeausgabe;"Zucker*: ohne|1 Zucker|2 Zucker; Milch+: Hafer|laktosefrei";ja\r\n'
           "Bier 5dl;6.50;Getränke;Getränkeausgabe;;\r\n").encode()
    r = client.post("/api/admin/import/products?commit=false", auth=AUTH, files={"file": ("p.csv", csv)}).json()
    assert r["summary"]["new"] == 1 and r["summary"]["unchanged"] == 1, r
    client.post("/api/admin/import/products?commit=true", auth=AUTH, files={"file": ("p.csv", csv)})
    p = {x["name"]: x for x in client.get("/api/admin/products", auth=AUTH).json()}
    assert p["Kaffee"]["options"]["groups"][1] == {"name": "Milch", "choices": ["Hafer", "laktosefrei"],
                                                   "required": False, "multi": True}
    exp = client.get("/api/admin/export/products.csv", auth=AUTH).content.decode("utf-8-sig")
    assert '"Zucker*: ohne|1 Zucker|2 Zucker; Milch+: Hafer|laktosefrei";1' in exp

    # Optionen ändern -> Vorschau zeigt lesbaren Unterschied
    csv2 = csv.replace(b"2 Zucker; Milch", b"2 Zucker|3 Zucker; Milch")
    r = client.post("/api/admin/import/products?commit=false", auth=AUTH, files={"file": ("p.csv", csv2)}).json()
    row = [x for x in r["rows"] if x["name"] == "Kaffee"][0]
    assert row["action"] == "update" and "3 Zucker" in row["changes"]["options"][1]
    client.post("/api/admin/import/products?commit=true", auth=AUTH, files={"file": ("p.csv", csv2)})
    p = {x["name"]: x for x in client.get("/api/admin/products", auth=AUTH).json()}
    assert "3 Zucker" in p["Kaffee"]["options"]["groups"][0]["choices"]


def test_old_style_lines_still_work(client):
    """Ältere App-Version im Offline-Puffer schickt Positionen ohne Optionen."""
    setup_shop(client)
    pid = client.get("/api/admin/products", auth=AUTH).json()[0]["id"]
    r = client.post("/api/orders", json={"client_uuid": str(uuid.uuid4()), "table": "3",
                                         "lines": [{"product_id": pid, "qty": 2}]})
    assert r.status_code == 200


def test_long_names_wrap():
    from app.print_service import render_table_bon
    st = PrinterCfg(1, "Getränkeausgabe", "x", 9100, 32, True, True)
    data = render_table_bon(st, st, event_name="Ein sehr langer Anlassname", currency="CHF", table="Terrasse 1",
                            order_id=7, created_at="2026-10-09T19:00:00", device_name="XCover 2",
                            items=[dict(qty=12, name="Mineral mit Kohlensäure 5dl extra", price_cents=400,
                                        options="Zitrone, Eis, ohne Strohhalm, im grossen Glas bitte", note=None,
                                        category="Getränke")])
    txt = to_text(data)[0]
    assert all(len(l) <= 32 for l in txt.splitlines() if not l.startswith("TISCH")), txt


def test_table_word_and_recent_tables(client):
    setup_shop(client)
    b = client.get("/api/bootstrap").json()
    assert b["table_word"] == "Tisch" and b["show_recent_tables"] is True
    assert client.put("/api/admin/settings", auth=AUTH, json={"table_word": "Stuhl"}).status_code == 422
    client.put("/api/admin/settings", auth=AUTH, json={"table_word": "Platz", "show_recent_tables": False})
    b = client.get("/api/bootstrap").json()
    assert b["table_word"] == "Platz" and b["show_recent_tables"] is False and b["show_history"] is True
    pid = client.get("/api/admin/products", auth=AUTH).json()[0]["id"]
    oid = client.post("/api/orders", json={"client_uuid": str(uuid.uuid4()), "table": "7",
                                           "lines": [{"product_id": pid, "qty": 1}]}).json()["order_id"]
    assert all(t.splitlines()[1].startswith("PLATZ 7") for _, t in _bons(client, oid))
    pdf = client.get("/api/admin/tables/qr.pdf", auth=AUTH)
    assert pdf.status_code == 200


def test_tables_mode(client):
    setup_shop(client)                                         # Tische 1..20
    pid = client.get("/api/admin/products", auth=AUTH).json()[0]["id"]
    order = lambda t: client.post("/api/orders", json={"client_uuid": str(uuid.uuid4()), "table": t,
                                                       "lines": [{"product_id": pid, "qty": 1}]})
    assert client.get("/api/bootstrap").json()["tables_mode"] == "free"
    assert order("Bühne").status_code == 200                   # Standard: freie Wahl
    client.put("/api/admin/settings", auth=AUTH, json={"tables_mode": "warn"})
    assert client.get("/api/bootstrap").json()["tables_mode"] == "warn"
    assert order("Bühne").status_code == 200                   # Warnen: App fragt, Server nimmt an
    client.put("/api/admin/settings", auth=AUTH, json={"tables_mode": "strict"})
    r = order("Bühne")
    assert r.status_code == 422 and "nicht erfasst" in r.json()["detail"]
    assert order("12").status_code == 200
    client.post("/api/admin/tables", auth=AUTH, json={"labels": ["Bar"]})
    assert order("bar").json()["table"] == "Bar"                # Schreibweise wie erfasst
    assert client.put("/api/admin/settings", auth=AUTH, json={"tables_mode": "egal"}).status_code == 422


def test_printer_failover_settings(client):
    setup_shop(client)
    pr = {p["name"]: p for p in client.get("/api/admin/printers", auth=AUTH).json()}
    k = pr["Küche"]
    body = {"name": "Küche", "host": "127.0.0.1", "port": 1, "show_prices": False,
            "fail_mode": "reroute", "fail_target": pr["Getränkeausgabe"]["id"], "fail_after": 120}
    assert client.put(f"/api/admin/printers/{k['id']}", auth=AUTH, json=body).status_code == 200
    k2 = {p["name"]: p for p in client.get("/api/admin/printers", auth=AUTH).json()}["Küche"]
    assert (k2["fail_mode"], k2["fail_target"], k2["fail_after"]) == ("reroute", pr["Getränkeausgabe"]["id"], 120)
    assert client.put(f"/api/admin/printers/{k['id']}", auth=AUTH, json={**body, "fail_target": k["id"]}).status_code == 422
    assert client.put(f"/api/admin/printers/{k['id']}", auth=AUTH, json={**body, "fail_mode": "x"}).status_code == 422


def test_product_color(client):
    setup_shop(client)
    p = client.get("/api/admin/products", auth=AUTH).json()[0]
    assert client.put(f"/api/admin/products/{p['id']}/color", auth=AUTH, json={"color": "#12AB34"}).json()["color"]
    boot = {x["id"]: x for x in client.get("/api/bootstrap").json()["products"]}
    assert boot[p["id"]]["color"] == "#12ab34"
    assert client.put(f"/api/admin/products/{p['id']}/color", auth=AUTH, json={"color": "rot"}).status_code == 422
    client.put(f"/api/admin/products/{p['id']}/color", auth=AUTH, json={"color": None})
    assert {x["id"]: x for x in client.get("/api/bootstrap").json()["products"]}[p["id"]]["color"] is None
    # CSV: Spalte farbe setzen und wieder leeren
    csv = f"name;preis;farbe\r\n{p['name']};6.50;e4002b\r\n".encode()
    client.post("/api/admin/import/products?commit=true", auth=AUTH, files={"file": ("p.csv", csv)})
    assert {x["id"]: x for x in client.get("/api/admin/products", auth=AUTH).json()}[p["id"]]["color"] == "#e4002b"
    assert "#e4002b" in client.get("/api/admin/export/products.csv", auth=AUTH).content.decode("utf-8-sig")
    csv = f"name;preis;farbe\r\n{p['name']};6.50;\r\n".encode()
    client.post("/api/admin/import/products?commit=true", auth=AUTH, files={"file": ("p.csv", csv)})
    assert {x["id"]: x for x in client.get("/api/admin/products", auth=AUTH).json()}[p["id"]]["color"] is None


def test_delivery_times(client):
    """Lieferzeit: eigene Bons pro Zeitpunkt, n/m zählt pro Zeitpunkt über alle Einzelbon-Produkte."""
    setup_shop(client)
    assert client.get("/api/bootstrap").json()["delivery_times"] == ["Sofort", "Pause", "Schluss"]
    prods = {p["name"]: p for p in client.get("/api/admin/products", auth=AUTH).json()}
    kitchen = prods["Bratwurst"]["printer_id"]
    ids = {}
    for name in ("Faustbrot Käse", "Faustbrot Salami", "Fleischteller"):
        ids[name] = client.post("/api/admin/products", auth=AUTH, json={
            "name": name, "price_cents": 600, "printer_id": kitchen, "single_ticket": True, "delivery": True}).json()["id"]
    bier = prods["Bier 5dl"]["id"]                              # Lieferzeit nicht wählbar
    body = {"client_uuid": str(uuid.uuid4()), "table": "101", "device": "XCover 3", "lines": [
        {"product_id": ids["Faustbrot Käse"], "qty": 1, "when": "Schluss"},
        {"product_id": ids["Faustbrot Salami"], "qty": 1, "when": "schluss"},
        {"product_id": ids["Fleischteller"], "qty": 1, "when": "Schluss"},
        {"product_id": ids["Fleischteller"], "qty": 2, "when": "Sofort"},
        {"product_id": ids["Faustbrot Käse"], "qty": 1, "when": "Pause"},
        {"product_id": bier, "qty": 2, "when": "Schluss"},                     # -> sofort
    ]}
    r = client.post("/api/orders", json=body)
    assert r.status_code == 200, r.text
    bons = _bons(client, r.json()["order_id"])
    k = [t for n, t in bons if n == "Küche"]
    schluss = [t for t in k if "SCHLUSS" in t]
    pause = [t for t in k if "PAUSE" in t]
    sofort = [t for t in k if "SCHLUSS" not in t and "PAUSE" not in t]
    assert len(schluss) == 3 and len(pause) == 1 and len(sofort) == 2
    assert [t.splitlines()[1].rstrip()[-7:] for t in schluss] == ["1 von 3", "2 von 3", "3 von 3"]
    assert pause[0].splitlines()[1].rstrip().endswith("1 von 1")
    assert [t.splitlines()[1].rstrip()[-7:] for t in sofort] == ["1 von 2", "2 von 2"]
    bar = [t for n, t in bons if n == "Getränkeausgabe"]
    assert len(bar) == 1 and "2x Bier 5dl" in bar[0] and "SCHLUSS" not in bar[0]
    # Einstellung ändern/bereinigen
    client.put("/api/admin/settings", auth=AUTH, json={"delivery_times": " Sofort ;Pause, Nach Vorstellung,pause"})
    assert client.get("/api/bootstrap").json()["delivery_times"] == ["Sofort", "Pause", "Nach Vorstellung"]
    assert client.put("/api/admin/settings", auth=AUTH, json={"delivery_times": " , "}).status_code == 422


def test_delivery_per_product(client):
    """Pro Produkt: aus / wählbar / Pflicht / nur bestimmte Zeitpunkte (einer = erzwungen) – Server setzt durch."""
    setup_shop(client)
    prods = {p["name"]: p for p in client.get("/api/admin/products", auth=AUTH).json()}
    kitchen = prods["Bratwurst"]["printer_id"]
    mk = lambda n: client.post("/api/admin/products", auth=AUTH, json={"name": n, "price_cents": 500, "printer_id": kitchen,
                                                                      "single_ticket": True}).json()["id"]
    frei, pflicht, nur_schluss, pause_schluss = mk("Frei"), mk("Pflicht"), mk("NurSchluss"), mk("PauseSchluss")
    put = lambda pid, **kw: client.put(f"/api/admin/products/{pid}/delivery", auth=AUTH, json=kw).json()
    put(frei, enabled=True)
    put(pflicht, enabled=True, required=True)
    assert put(nur_schluss, enabled=True, allowed=["schluss"])["delivery_allowed"] == "Schluss"
    put(pause_schluss, enabled=True, allowed=["Pause", "Schluss"])
    boot = {p["name"]: p for p in client.get("/api/bootstrap").json()["products"]}
    assert boot["Pflicht"]["delivery_required"] == 1 and boot["NurSchluss"]["delivery_allowed"] == "Schluss"
    lines = [{"product_id": frei, "qty": 1, "when": "Pause"},
             {"product_id": nur_schluss, "qty": 1},                      # ohne Angabe -> erzwungen Schluss
             {"product_id": nur_schluss, "qty": 1, "when": "Sofort"},    # nicht erlaubt -> Schluss
             {"product_id": pause_schluss, "qty": 1, "when": "Sofort"},  # nicht erlaubt -> Pause (erster erlaubter)
             {"product_id": prods["Bratwurst"]["id"], "qty": 1, "when": "Schluss"}]   # aus -> sofort
    oid = client.post("/api/orders", json={"client_uuid": str(uuid.uuid4()), "table": "3", "lines": lines}).json()["order_id"]
    import app.main as main
    rows = {r["name"]: r for r in main.store.query("SELECT name, delivery, qty FROM order_items WHERE order_id=?", (oid,))}
    assert rows["Frei"]["delivery"] == "Pause"
    assert rows["NurSchluss"]["delivery"] == "Schluss" and rows["NurSchluss"]["qty"] == 2
    assert rows["PauseSchluss"]["delivery"] == "Pause"
    assert rows["Bratwurst"]["delivery"] is None
    # CSV: Text-Format hin und zurück
    exp = client.get("/api/admin/export/products.csv", auth=AUTH).content.decode("utf-8-sig")
    tail = ";;;\r\n"                                     # danach: bestand, warnen_ab, ausverkauft (leer)
    assert all(f";{x}{tail}" in exp for x in ("alle", "alle*", "Schluss", "Pause|Schluss"))
    csv = "name;preis;lieferzeit\r\nFrei;5.00;Schluss\r\nPflicht;5.00;Pause|Schluss*\r\nNurSchluss;5.00;nein\r\nX;1.00;Mittag\r\n"
    r = client.post("/api/admin/import/products?commit=false", auth=AUTH, files={"file": ("p.csv", csv.encode())}).json()
    assert any("Mittag" in e for row in r["rows"] for e in row["errors"])
    csv = csv.replace("X;1.00;Mittag\r\n", "")
    client.post("/api/admin/import/products?commit=true", auth=AUTH, files={"file": ("p.csv", csv.encode())})
    boot = {p["name"]: p for p in client.get("/api/bootstrap").json()["products"]}
    assert (boot["Frei"]["delivery"], boot["Frei"]["delivery_allowed"]) == (1, "Schluss")
    assert (boot["Pflicht"]["delivery_allowed"], boot["Pflicht"]["delivery_required"]) == ("Pause, Schluss", 1)
    assert boot["NurSchluss"]["delivery"] == 0


def test_numbering_table_and_single_bons(client):
    """Pro Drucker und Auftrag zählt n/m über Sammelbon UND Einzelbons."""
    setup_shop(client)
    prods = {p["name"]: p for p in client.get("/api/admin/products", auth=AUTH).json()}
    kitchen = prods["Bratwurst"]["printer_id"]
    pommes = client.post("/api/admin/products", auth=AUTH, json={"name": "Pommes", "price_cents": 500,
                                                                 "printer_id": kitchen}).json()["id"]
    oid = client.post("/api/orders", json={"client_uuid": str(uuid.uuid4()), "table": "8", "lines": [
        {"product_id": pommes, "qty": 2}, {"product_id": prods["Bratwurst"]["id"], "qty": 2},
        {"product_id": prods["Bier 5dl"]["id"], "qty": 1}]}).json()["order_id"]
    bons = _bons(client, oid)
    k = [t for n, t in bons if n == "Küche"]
    assert [t.splitlines()[1].rstrip()[-7:] for t in k] == ["1 von 3", "2 von 3", "3 von 3"]
    assert "2x Pommes" in k[0] and "Bratwurst" in k[1] and "Bratwurst" in k[2]
    bar = [t for n, t in bons if n == "Getränkeausgabe"]
    assert bar[0].splitlines()[1].strip() == "TISCH 8"              # nur ein Bon: keine Nummer


def test_status_endpoint(client):
    setup_shop(client)
    assert client.get("/api/status").status_code == 401            # nicht vom Pi selbst: Passwort nötig
    pid = client.get("/api/admin/products", auth=AUTH).json()[0]["id"]
    client.post("/api/orders", json={"client_uuid": str(uuid.uuid4()), "table": "3", "device": "XCover 2",
                                     "lines": [{"product_id": pid, "qty": 2}]},
                headers={"X-Festpos-Device": "XCover%202", "X-Festpos-Outbox": "1"})
    client.get("/api/bootstrap", headers={"X-Festpos-Device": "XCover%201", "X-Festpos-Outbox": "0"})
    s = client.get("/api/status", auth=AUTH).json()
    assert s["totals"]["count"] == 1 and s["totals"]["last_hour"] == 1
    assert s["recent"][0]["table_label"] == "3" and s["recent"][0]["items"] == 2
    assert {p["station"] for p in s["recent"][0]["print"]} <= {"Getränkeausgabe", "Küche"}
    dev = {d["name"]: d for d in s["devices"]}
    assert dev["XCover 1"]["online"] and dev["XCover 2"]["outbox"] == 1
    assert {p["name"] for p in s["printers"]} == {"Getränkeausgabe", "Küche"}
    assert s["cluster"] is None and s["version"]
    client.put("/api/admin/settings", auth=AUTH, json={"monitor_hosts": "Switch=10.0.0.2; AP=10.0.0.3"})
    from app.monitor import parse_hosts
    assert parse_hosts("Switch=10.0.0.2; AP=10.0.0.3") == [("Switch", "10.0.0.2"), ("AP", "10.0.0.3")]


def test_monitor_pings_devices(monkeypatch):
    import asyncio
    from app import monitor as mon
    m = mon.Monitor()
    m.seen("XCover 1", "192.168.55.101", "0", "v")
    m.seen("XCover 2", "192.168.55.102", "0", "v")
    m.seen("Lokal", "127.0.0.1", "0", "v")
    calls = []

    async def fake_ping(ip):
        calls.append(ip)
        return ip.endswith("101")
    monkeypatch.setattr(mon.Monitor, "_ping", staticmethod(fake_ping))
    asyncio.run(m._ping_devices())
    d = {x["name"]: x for x in m.device_list()}
    assert d["XCover 1"]["ping"] is True and d["XCover 2"]["ping"] is False
    assert d["XCover 1"]["ip"] == "192.168.55.101"
    assert "127.0.0.1" not in calls
    m.seen("XCover 1", "192.168.55.101", "0", "v")          # gleiche IP: Ping-Ergebnis bleibt
    assert {x["name"]: x for x in m.device_list()}["XCover 1"]["ping"] is True
    m.seen("XCover 1", "192.168.55.150", "0", "v")          # neue IP: noch unbekannt
    assert {x["name"]: x for x in m.device_list()}["XCover 1"]["ping"] is None


def test_monitor_devices_setting(client):
    setup_shop(client)
    r = client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": [
        {"name": "XCover 1", "kind": "phone", "ip": "testclient", "port": None},
        {"name": "XCover 2", "kind": "phone", "ip": "192.168.55.102"},
        {"name": "Switch", "kind": "net", "ip": "192.168.55.30", "port": 80}]})
    assert r.status_code == 200
    assert client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": [
        {"name": "x", "kind": "phone", "ip": "1.2.3.4; rm -rf /"}]}).status_code == 422
    client.get("/api/bootstrap", headers={"X-Festpos-Device": "Bar-Handy", "X-Festpos-Outbox": "2"})
    s = client.get("/api/status", auth=AUTH).json()
    dev = {d["name"]: d for d in s["devices"]}
    assert set(dev) == {"XCover 1", "XCover 2"}                     # nur die eingetragenen, keine App-Namen
    assert dev["XCover 1"]["online"] and dev["XCover 1"]["outbox"] == 2 and dev["XCover 1"]["ip"] == "testclient"
    assert not dev["XCover 2"]["online"] and dev["XCover 2"]["ip"] == "192.168.55.102"
    from app.monitor import parse_targets
    import json as _j
    t = parse_targets(_j.dumps([{"name": "Switch", "kind": "net", "ip": "192.168.55.30", "port": 80}]))
    assert t == [{"name": "Switch", "kind": "net", "ip": "192.168.55.30", "port": 80, "active": True}]
    assert parse_targets(None, "Switch=10.0.0.2; AP=10.0.0.3:8080") == [
        {"name": "Switch", "kind": "net", "ip": "10.0.0.2", "port": None, "active": True},
        {"name": "AP", "kind": "net", "ip": "10.0.0.3", "port": 8080, "active": True}]


def test_display_settings(client, tmp_path, monkeypatch):
    from app import main as m
    from app import display
    monkeypatch.setattr(display, "DISPLAY_FILE", str(tmp_path / "display.json"))
    monkeypatch.setattr(display, "DISPLAY_STATUS_FILE", str(tmp_path / "status.json"))
    setup_shop(client)
    d = client.get("/api/admin/display", auth=AUTH).json()
    node = d["nodes"][0]["node"]
    assert "tft35" in d["types"] and d["nodes"][0]["cfg"]["type"] is None
    assert client.put(f"/api/admin/display/{node}", auth=AUTH, json={"type": "tft35", "rotate": 45}).status_code == 422
    assert client.put(f"/api/admin/display/{node}", auth=AUTH, json={"type": "kaputt"}).status_code == 422
    assert client.put("/api/admin/display/xyz", auth=AUTH, json={"type": "hdmi"}).status_code == 404
    r = client.put(f"/api/admin/display/{node}", auth=AUTH, json={"type": "tft35", "rotate": 270, "size": "320x480"})
    assert r.status_code == 200 and r.json()["rotate"] == 270
    m._display_sync()
    import json as _j
    assert _j.loads((tmp_path / "display.json").read_text()) == {"type": "tft35", "reboot": "", "cursor": "auto"}
    # Mauszeiger: automatisch / an / aus, sonst abgelehnt
    assert client.put(f"/api/admin/display/{node}", auth=AUTH, json={"type": "tft35", "cursor": "blinkend"}).status_code == 422
    r = client.put(f"/api/admin/display/{node}", auth=AUTH, json={"type": "tft35", "rotate": 270, "size": "320x480", "cursor": "on"})
    assert r.json()["cursor"] == "on"
    m._display_sync()
    assert _j.loads((tmp_path / "display.json").read_text())["cursor"] == "on"
    assert client.post(f"/api/admin/display/{node}/touch", auth=AUTH, json={"action": "calibrate"}).json()["touchtest"]
    # Statusanzeige vom Pi selbst bekommt die Einstellung, von aussen nicht
    s = m._status_data(True)
    assert s["kiosk"]["rotate"] == 270 and s["kiosk"]["touchtest"] is True
    assert m._status_data(False)["kiosk"] is None
    # Touch-Einrichtung nur vom Pi selbst
    assert client.post("/api/status/touchcal", json={"cal": [1, 0, 0, 0, 1, 0]}).status_code == 403
    m._display_update(node, cal=[0, 1, 0, 1, 0, 0], touchtest=False)
    assert m._display_cfg(node)["cal"] == [0, 1, 0, 1, 0, 0]
    tok = client.post(f"/api/admin/display/{node}/reboot", auth=AUTH).json()["reboot"]
    m._display_sync()
    assert _j.loads((tmp_path / "display.json").read_text())["reboot"] == tok
    st = client.get("/api/admin/display", auth=AUTH).json()["nodes"][0]["status"]
    assert "connectors" in st and st["seen"]


def test_monitor_check_fills_system_and_extra():
    import asyncio
    from app import monitor as mon
    m = mon.Monitor()
    called = []
    m._get_targets, m._get_printers, m._cluster = (lambda: []), (lambda: []), False
    m._extra = lambda: called.append(1)
    asyncio.run(m.check())
    assert called and isinstance(m.system, dict) and "uptime" in m.system


def test_admin_login_throttle(client):
    from app import main as m
    m.login_throttle.clear()
    bad = ("admin", "falsch")
    for _ in range(m.LOGIN_MAX_FAILS):
        assert client.get("/api/admin/settings", auth=bad).status_code == 401
    r = client.get("/api/admin/settings", auth=bad)
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert client.get("/api/admin/settings", auth=AUTH).status_code == 429     # auch richtig: erst nach der Sperre
    m.login_throttle.clear()
    assert client.get("/api/admin/settings", auth=AUTH).status_code == 200
    assert client.get("/api/admin/settings").status_code == 401                  # ohne Daten zählt nicht
    assert not m.login_throttle._fails
