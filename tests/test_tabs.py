"""Offene Tische (app/tabs.py): Bestellungen sammeln sich am Tisch, Zahlungen auf dem Server, Rechte, Übergeben,
«nicht bezahlt», Tagesabschluss (gesperrt, solange ein Tisch offen ist; Zahlart nach den Zahlungen aufgeteilt).
Der Server läuft hinter tests/asgi_testphone.py: X-Test-Cert spielt das Gerätezertifikat."""
from __future__ import annotations

import uuid

import pytest

from tests.test_api import AUTH
from tests.test_browser_p4 import _start


def H(name: str) -> dict:
    return {"X-Test-Cert": name, "X-Test-Client": "192.168.55.101", "X-Festpos-Device": name.split()[0]}


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("tabs"), target="tests.asgi_testphone:app")
    c.put("/api/admin/settings", auth=AUTH, json={"payment_mode": "optional"})
    yield c
    proc.terminate()
    proc.wait(10)


def _products(c) -> dict:
    return {p["name"]: p["id"] for p in c.get("/api/admin/products", auth=AUTH).json()}


def _order(c, table: str, who: str, lines: list[tuple[str, int]], test: bool = False) -> dict:
    p = _products(c)
    body = {"client_uuid": str(uuid.uuid4()), "table": table, "device": who.split()[0],
            "lines": [{"product_id": p[n], "qty": q} for n, q in lines]}
    if test:
        body["test"] = True
    r = c.post("/api/orders", json=body, headers=H(who))
    assert r.status_code == 200, r.text
    return r.json()


def _tabs(c, who: str = "XCover 1") -> list[dict]:
    r = c.get("/api/tabs", headers=H(who))
    assert r.status_code == 200, r.text
    return r.json()["tabs"]


def _pay(c, tab: int, who: str, cents: int, payment: str | None = "cash", tip: int = 0):
    return c.post(f"/api/tabs/{tab}/pay", headers=H(who),
                  json={"key": str(uuid.uuid4()), "amount_cents": cents, "payment": payment, "tip_cents": tip})


def _reset(c, mode: str = "open", who: str = "own_lead"):
    c.put("/api/admin/settings", auth=AUTH, json={"tab_mode": mode, "tab_who": who})
    for t in c.get("/api/admin/tabs", auth=AUTH).json()["tabs"]:
        r = c.post(f"/api/admin/tabs/{t['id']}/settle", auth=AUTH, json={"payment": "cash"})
        assert r.status_code == 200, r.text
    c.post("/api/admin/closings", auth=AUTH, json={})


def test_now_mode_is_unchanged(srv):
    c = srv
    _reset(c, "now")
    _order(c, "3", "XCover 1", [("Bier 5dl", 1)])
    assert _tabs(c) == []
    assert c.get("/api/bootstrap", headers=H("XCover 1")).json()["tabs"]["mode"] == "now"


def test_orders_collect_on_one_table_and_rights(srv):
    c = srv
    _reset(c, "open", "own_lead")
    _order(c, "12", "XCover 1", [("Bier 5dl", 2)])                       # 13.00, eröffnet den Tisch
    _order(c, "12", "XCover 2", [("Mineral", 1)])                        # anderes Handy: kommt auf denselben Tisch
    _order(c, "7", "XCover 2", [("Mineral", 2)])
    tabs = {t["table"]: t for t in _tabs(c)}
    assert set(tabs) == {"12", "7"}
    t12 = tabs["12"]
    assert t12["orders"] == 2 and t12["net"] == 1700 and t12["open"] == 1700 and t12["mine"] and t12["can_pay"]
    assert "XCover 1" in t12["owner_name"]
    other = {t["table"]: t for t in _tabs(c, "XCover 2")}["12"]
    assert not other["mine"] and not other["can_pay"]
    r = _pay(c, t12["id"], "XCover 2", 500)
    assert r.status_code == 403 and "XCover 1" in r.json()["detail"]
    # «jedes Handy» darf
    c.put("/api/admin/settings", auth=AUTH, json={"tab_who": "any"})
    assert _pay(c, t12["id"], "XCover 2", 500).status_code == 200
    # Detail: Positionen zusammengefasst, Zahlung sichtbar
    d = c.get(f"/api/tabs/{t12['id']}", headers=H("XCover 1")).json()
    assert {(i["name"], i["qty"]) for i in d["items"]} == {("Bier 5dl", 2), ("Mineral", 1)}
    assert d["paid"] == 500 and d["open"] == 1200 and len(d["payments"]) == 1
    # mehr als offen geht nicht, zu früh schliessen auch nicht
    assert _pay(c, t12["id"], "XCover 1", 5000).status_code == 409
    assert c.post(f"/api/tabs/{t12['id']}/close", headers=H("XCover 1")).status_code == 409
    # Tagesabschluss gesperrt, solange Tische offen sind
    r = c.post("/api/admin/closings", auth=AUTH, json={})
    assert r.status_code == 409 and "offen" in r.json()["detail"]
    # Rest mit Karte und Trinkgeld, dann zu: die nächste Bestellung eröffnet einen neuen Tisch
    assert _pay(c, t12["id"], "XCover 1", 1200, "card", tip=300).status_code == 200
    closed = c.post(f"/api/tabs/{t12['id']}/close", headers=H("XCover 1")).json()
    assert closed["closed_at"] and closed["open"] == 0
    _order(c, "12", "XCover 2", [("Bier 5dl", 1)])
    new12 = {t["table"]: t for t in _tabs(c)}["12"]
    assert new12["id"] != t12["id"] and "XCover 2" in new12["owner_name"]
    # Tagesabschluss-Vorschau: Tisch 12 nach den Zahlungen aufgeteilt (5.00 bar, 12.00 Karte), Trinkgeld mit Karte
    s = c.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert s["by_payment"].get("card", 0) >= 1200
    assert s["tips"]["by_payment"].get("card") == 300


def test_admin_unpaid_transfer_settle_and_closing(srv):
    c = srv
    _reset(c, "open", "own")
    _order(c, "20", "XCover 1", [("Bier 5dl", 1), ("Mineral", 1)])     # 10.50
    t = {x["table"]: x for x in _tabs(c)}["20"]
    jobs_before = len(c.get("/api/admin/jobs", auth=AUTH).json())
    r = c.post(f"/api/admin/tabs/{t['id']}/unpaid", auth=AUTH, json={"note": "gegangen"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["closed_at"] and d["close_reason"] == "unpaid" and d["net"] == 0
    assert len(c.get("/api/admin/jobs", auth=AUTH).json()) == jobs_before          # keine Storno-Bons
    # teilweise bezahlt: nicht ausbuchen
    _order(c, "21", "XCover 1", [("Bier 5dl", 2)])
    t21 = {x["table"]: x for x in _tabs(c)}["21"]
    assert _pay(c, t21["id"], "XCover 1", 600).status_code == 200
    assert c.post(f"/api/admin/tabs/{t21['id']}/unpaid", auth=AUTH, json={}).status_code == 409
    # Verwaltung rechnet den Rest ab (Karte)
    r = c.post(f"/api/admin/tabs/{t21['id']}/settle", auth=AUTH, json={"payment": "card"})
    assert r.status_code == 200 and r.json()["closed_at"] and r.json()["paid"] == 1300
    # Tagesabschluss geht jetzt; Zahlart nach den Zahlungen: 6.00 bar, 7.00 Karte
    s = c.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    r = c.post("/api/admin/closings", auth=AUTH, json={"counted_cents": 0, "float_cents": 0})
    assert r.status_code == 200, r.text
    assert s["by_payment"]["card"] >= 700
    csv = c.get(f"/api/admin/closings/{r.json()['id']}/csv", auth=AUTH).content.decode("utf-8-sig")
    assert "tischzahlung" in csv


def test_test_mode_tabs_are_separate(srv):
    c = srv
    _reset(c, "open", "own")
    c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": "1357"})
    _order(c, "30", "XCover 1", [("Bier 5dl", 1)])
    _order(c, "30", "XCover 1", [("Mineral", 1)], test=True)
    real = {x["table"]: x for x in _tabs(c)}["30"]
    test = {x["table"]: x for x in c.get("/api/tabs?test=true", headers=H("XCover 1")).json()["tabs"]}["30"]
    assert real["id"] != test["id"] and real["net"] == 650 and test["net"] == 400
    assert [x["table"] for x in c.get("/api/admin/tabs", auth=AUTH).json()["tabs"]] == ["30"]   # nur echte


def test_receipts_over_the_whole_table(srv):
    """Quittung über einen offenen Tisch: alle Bestellungen, fortlaufende Nummer, jede Position nur einmal im Original
    (auch wenn die Verwaltung bei einer der Bestellungen quittiert), Kopie, pro Person, über CHF 400 mit MWST."""
    c = srv
    _reset(c, "open", "own")
    pid = c.get("/api/admin/printers", auth=AUTH).json()[0]["id"]
    c.put("/api/admin/settings", auth=AUTH, json={"receipt_printer_id": pid})
    o1 = _order(c, "40", "XCover 1", [("Bier 5dl", 2)])
    _order(c, "40", "XCover 1", [("Mineral", 1)])
    t40 = {x["table"]: x for x in _tabs(c)}["40"]
    p = _products(c)
    # pro Person: 1 Bier
    r = c.post("/api/orders/receipt", headers=H("XCover 1"), json={"tab_id": t40["id"], "request_id": "rq-person-1",
               "part": 1, "items": [{"product_id": p["Bier 5dl"], "qty": 1}], "payment": "card"})
    assert r.status_code == 200, r.text
    first = r.json()["receipts"][0]
    assert first["total_cents"] == 650 and first["no"]
    # ganzer Tisch: nur noch, was offen ist (1 Bier + 1 Mineral) – über beide Bestellungen
    r = c.post("/api/orders/receipt", headers=H("XCover 1"), json={"tab_id": t40["id"], "request_id": "rq-whole-1"})
    rest = r.json()["receipts"][0]
    assert rest["total_cents"] == 1050 and rest["no"] == first["no"] + 1
    # Verwaltung bei der ersten Bestellung: alles quittiert -> Kopien, keine neue Nummer
    r = c.post(f"/api/admin/orders/{o1['order_id']}/receipt", auth=AUTH, json={"request_id": "rq-admin-1"})
    assert r.status_code == 200 and r.json()["copy"] is True
    # die Bestellung zeigt die Quittungen des Tisches
    d = c.get(f"/api/admin/orders/{o1['order_id']}", auth=AUTH).json()
    assert {x["no"] for x in d["receipts"]} == {first["no"], rest["no"]} and all(x["tab"] for x in d["receipts"])
    # Kopie über das Handy
    r = c.post("/api/orders/receipt", headers=H("XCover 1"), json={"tab_id": t40["id"], "request_id": "rq-copy-1",
               "copy_of": rest["id"]})
    assert r.status_code == 200 and r.json()["copy"] is True
    # anderes Handy ohne Recht: keine Quittung über den Tisch
    r = c.post("/api/orders/receipt", headers=H("XCover 2"), json={"tab_id": t40["id"], "request_id": "rq-other-1"})
    assert r.status_code == 403
    # Prüfsummen der Quittungen stimmen (Tagesabschluss-Prüfung)
    assert _pay(c, t40["id"], "XCover 1", 1700, "card").status_code == 200
    c.post(f"/api/tabs/{t40['id']}/close", headers=H("XCover 1"))
    s = c.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert not s["check"]["bad"], s["check"]
    assert s["receipts"]["count"] >= 2


def test_receipt_over_400_with_vat_needs_recipient(srv):
    c = srv
    _reset(c, "open", "own")
    c.put("/api/admin/vat", auth=AUTH, json={"enabled": True, "number": "CHE-123.456.789 MWST"})
    try:
        o = _order(c, "41", "XCover 1", [("Bier 5dl", 40)])               # 260.00
        _order(c, "41", "XCover 1", [("Bier 5dl", 40)])                   # zusammen 520.00
        tab = {x["table"]: x for x in _tabs(c)}["41"]
        r = c.post("/api/orders/receipt", headers=H("XCover 1"), json={"tab_id": tab["id"], "request_id": "rq-big-1"})
        assert r.status_code == 409                                       # am Handy nicht: in der Verwaltung mit Empfänger
        r = c.post(f"/api/admin/orders/{o['order_id']}/receipt", auth=AUTH, json={"request_id": "rq-big-2"})
        assert r.status_code == 422
        r = c.post(f"/api/admin/orders/{o['order_id']}/receipt", auth=AUTH,
                   json={"request_id": "rq-big-3", "recipient": "Anna Muster\nDorfstrasse 1\n8000 Zürich"})
        assert r.status_code == 200 and r.json()["receipts"][0]["total_cents"] == 52000
    finally:
        c.put("/api/admin/vat", auth=AUTH, json={"enabled": False, "number": ""})


def test_payrexx_pays_into_the_table(srv):
    """Payrexx (NFC) an einem offenen Tisch: bezahlt = Zahlung des Tisches (Zahlart payrexx), nie mehr als offen.
    Im Testmodus simuliert (nach 3 Sekunden bezahlt)."""
    import time
    c = srv
    _reset(c, "open", "own")
    c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": "1357"})
    _order(c, "50", "XCover 1", [("Bier 5dl", 2)], test=True)                # 13.00 (Übung)
    tab = {x["table"]: x for x in c.get("/api/tabs?test=true", headers=H("XCover 1")).json()["tabs"]}["50"]
    too_much = c.post("/api/phone/payrexx", headers=H("XCover 1"), json={"tab_id": tab["id"], "amount_cents": 5000,
                                                                          "part": "all", "key": "tapkey-too-much"})
    assert too_much.status_code == 409
    assert c.post("/api/phone/payrexx", headers=H("XCover 2"), json={"tab_id": tab["id"], "amount_cents": 650,
                                                                      "part": "p1", "key": "tapkey-other"}).status_code == 403
    r = c.post("/api/phone/payrexx", headers=H("XCover 1"), json={"tab_id": tab["id"], "amount_cents": 750, "tip_cents": 100,
                                                                   "part": "p1", "key": "tapkey-person-1"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    time.sleep(3.2)
    assert c.get(f"/api/phone/payrexx/{pid}", headers=H("XCover 1")).json()["status"] == "confirmed"
    d = c.get(f"/api/tabs/{tab['id']}", headers=H("XCover 1")).json()
    assert d["paid"] == 650 and d["open"] == 650
    pay = d["payments"][0]
    assert pay["payment"] == "payrexx" and pay["key"] == f"payrexx-{pid}" and pay["tip_cents"] == 0 and pay["part"] == 1


def _storno(c, tab: int, who: str, lines: list[tuple[int, int]], key: str | None = None):
    return c.post(f"/api/tabs/{tab}/storno", headers=H(who), json={
        "client_uuid": key or str(uuid.uuid4()), "lines": [{"id": i, "qty": q} for i, q in lines], "reason": "wrong"})


def test_storno_at_open_table(srv):
    """Storno beim Abrechnen am offenen Tisch: nur mit Storno-Recht, auf die neueste Bestellung zuerst, ganz Storniertes
    bleibt mit Nummer in der Liste, «Einzeln» bezahlte Stück und mehr als offen gehen nicht, Wiederholung bucht nichts."""
    c = srv
    _reset(c, "open", "own")
    _order(c, "30", "XCover 1", [("Bier 5dl", 2), ("Mineral", 1)])     # 17.00
    _order(c, "30", "XCover 1", [("Bier 5dl", 1)])                       # 6.50
    t = {x["table"]: x for x in _tabs(c)}["30"]
    d = c.get(f"/api/tabs/{t['id']}", headers=H("XCover 1")).json()
    ids = {i["name"]: i["id"] for i in d["items"]}
    c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "service"})
    assert _storno(c, t["id"], "XCover 1", [(ids["Bier 5dl"], 1)]).status_code == 403     # ohne Storno-Recht
    c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "storno"})
    c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 2", "role": "storno"})
    assert _storno(c, t["id"], "XCover 2", [(ids["Bier 5dl"], 1)]).status_code == 403     # nicht sein Tisch
    # Person 1 zahlt «Einzeln» 2 Bier: davon lässt sich nur noch 1 stornieren
    r = c.post(f"/api/tabs/{t['id']}/pay", headers=H("XCover 1"), json={
        "key": str(uuid.uuid4()), "amount_cents": 1300, "payment": "cash", "part": 1, "items": {str(ids["Bier 5dl"]): 2}})
    assert r.status_code == 200, r.text
    r = _storno(c, t["id"], "XCover 1", [(ids["Bier 5dl"], 2)])
    assert r.status_code == 409 and "höchstens 1" in r.json()["detail"]
    key = str(uuid.uuid4())
    r = _storno(c, t["id"], "XCover 1", [(ids["Bier 5dl"], 1), (ids["Mineral"], 1)], key)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["amount_cents"] == 1050 and not j["duplicate"]
    o1, o2 = sorted(j["order_ids"]) if len(j["order_ids"]) == 2 else (None, None)
    assert o1 and o2                                                       # Bier aus der neueren, Mineral aus der ersten
    items = {i["name"]: i for i in j["tab"]["items"]}
    assert items["Bier 5dl"]["qty"] == 2 and items["Bier 5dl"]["cancelled"] == 1
    assert items["Mineral"]["qty"] == 0 and items["Mineral"]["cancelled"] == 1 and items["Mineral"]["id"] == ids["Mineral"]
    assert j["tab"]["net"] == 1300 and j["tab"]["open"] == 0
    second = c.get(f"/api/admin/orders/{o2}", auth=AUTH).json()
    assert second["storno_cents"] == 650 and second["cancellations"][0]["by_device"].startswith("XCover 1")
    jobs = c.get("/api/admin/jobs?limit=50", auth=AUTH).json()
    assert o1 in {x["order_id"] for x in jobs if x["kind"] == "storno"}                # Storno-Bon wie gewohnt
    assert all(x["status"] == "cancelled" for x in jobs if x["order_id"] == o2 and x["kind"] != "storno" and
               x["status"] in ("pending", "cancelled"))                       # ganz storniert, noch nicht gedruckt
    # nochmals gesendet (gleiche Nummer): nichts Neues
    r = _storno(c, t["id"], "XCover 1", [(ids["Bier 5dl"], 1), (ids["Mineral"], 1)], key)
    assert r.status_code == 200 and r.json()["duplicate"] and r.json()["amount_cents"] == 1050
    assert c.get(f"/api/tabs/{t['id']}", headers=H("XCover 1")).json()["net"] == 1300
    # nichts mehr offen: kein weiteres Storno (die bezahlten Bier sind gesperrt)
    assert _storno(c, t["id"], "XCover 1", [(ids["Bier 5dl"], 1)]).status_code == 409
    assert _storno(c, t["id"], "XCover 1", [(99, 1)]).status_code == 422
    # Zahlung mit eigenem Betrag: mehr als offen ist, lässt sich nicht stornieren
    _order(c, "31", "XCover 1", [("Bier 5dl", 2)])
    t31 = {x["table"]: x for x in _tabs(c)}["31"]
    assert _pay(c, t31["id"], "XCover 1", 1000).status_code == 200                   # offen: 3.00
    bier = c.get(f"/api/tabs/{t31['id']}", headers=H("XCover 1")).json()["items"][0]["id"]
    r = _storno(c, t31["id"], "XCover 1", [(bier, 1)])
    assert r.status_code == 409 and "3.00" in r.json()["detail"]
    c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "service"})
    c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 2", "role": "service"})


def test_new_sql_on_sqlite_and_rqlite(store):
    """Neue SQL-Befehle auf beiden Datenbanken: offener Tisch im selben Batch wie die Bestellung (ON CONFLICT … DO
    NOTHING, Unterabfrage für tab_id) und das Aufräumen der Bilder mit json_extract (Logo auf Quittungen)."""
    from app import tabs
    from app.catalog import DROP_UNUSED_IMAGES
    store.execute("INSERT INTO settings(key, value) VALUES ('tab_mode', 'open')")
    for n, uid in enumerate(("sql-tab-1", "sql-tab-2")):
        stmts, key = tabs.order_stmts(store, table_label="7", test=False, owner="dev-1", owner_name="XCover 1", ts=f"2026-11-02T20:0{n}:00")
        stmts.append(("INSERT INTO orders(client_uuid, table_label, total_cents, created_at, tab_id) VALUES "
                      "(?,?,?,?,(SELECT id FROM tabs WHERE open_key=?))", (uid, "7", 500, f"2026-11-02T20:0{n}:00", key)))
        store.batch(stmts, transaction=True)
    rows = store.query("SELECT tab_id FROM orders WHERE client_uuid LIKE 'sql-tab-%'")
    assert len({r["tab_id"] for r in rows}) == 1 and rows[0]["tab_id"]
    assert store.scalar("SELECT COUNT(*) FROM tabs") == 1 and store.scalar("SELECT uuid FROM tabs")
    store.batch([("INSERT INTO images(id, mime, data) VALUES ('keep1', 'image/png', 'eA==')", ()),
                 ("INSERT INTO images(id, mime, data) VALUES ('gone1', 'image/png', 'eA==')", ()),
                 ("INSERT INTO receipts(no, request_id, order_id, order_uuid, lines, total_cents, data, created_at) VALUES "
                  "(1, 'rq-sql-1', 1, 'x', '[]', 0, ?, '2026-11-02T20:00:00')", ('{"design": {"logo": "keep1"}}',)),
                 DROP_UNUSED_IMAGES])
    assert {r["id"] for r in store.query("SELECT id FROM images")} == {"keep1"}
