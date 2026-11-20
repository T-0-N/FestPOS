"""Nachbuchen ohne Bon (app/orders.py no_print): schon Geliefertes verbuchen, ohne dass Küche oder Bar einen Bon
bekommen. Umsatz, Restmenge und offener Tisch zählen wie bei jeder Bestellung; die Einstellung «nachbuchen»
(aus / alle / nur mit Storno-Recht) entscheidet, welches Handy es darf. Server hinter tests/asgi_testphone.py."""
from __future__ import annotations

import uuid

import pytest

from tests.test_api import AUTH
from tests.test_browser_p4 import _start
from tests.test_tabs import H, _products, _reset, _tabs


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("nachbuchen"), target="tests.asgi_testphone:app")
    yield c
    proc.terminate()
    proc.wait(10)


def _post(c, table: str, lines: list[tuple[str, int]], who: str = "XCover 1", no_print: bool = False):
    p = _products(c)
    body = {"client_uuid": str(uuid.uuid4()), "table": table, "device": who.split()[0],
            "lines": [{"product_id": p[n], "qty": q} for n, q in lines]}
    if no_print:
        body["no_print"] = True
    return c.post("/api/orders", json=body, headers=H(who))


def _jobs(c, order_id: int) -> list[dict]:
    return [j for j in c.get("/api/admin/jobs?limit=200", auth=AUTH).json() if j["order_id"] == order_id]


def test_no_print_books_without_bons(srv):
    c = srv
    _reset(c, "now")
    normal = _post(c, "5", [("Bier 5dl", 1)])
    assert normal.status_code == 200, normal.text
    assert _jobs(c, normal.json()["order_id"])                         # gewöhnliche Bestellung: Bons
    r = _post(c, "5", [("Bier 5dl", 2), ("Mineral", 1)], no_print=True)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["total_cents"] == 2 * 650 + 400 and not j["warnings"]
    assert _jobs(c, j["order_id"]) == []                               # keine Bons
    orders = {o["id"]: o for o in c.get("/api/admin/orders", auth=AUTH).json()["orders"]}
    assert orders[j["order_id"]]["no_print"] == 1 and orders[normal.json()["order_id"]]["no_print"] == 0
    d = c.get(f"/api/admin/orders/{j['order_id']}", auth=AUTH).json()
    assert d["no_print"] == 1 and d["total_cents"] == 1700
    # Storno einer nachgebuchten Bestellung: kein Storno-Bon (es wurde ja nichts gedruckt)
    s = c.post(f"/api/admin/orders/{j['order_id']}/storno", auth=AUTH,
               json={"client_uuid": str(uuid.uuid4()), "reason": "wrong", "lines": None})
    assert s.status_code == 200, s.text
    assert _jobs(c, j["order_id"]) == []


def test_no_print_counts_stock(srv):
    c = srv
    _reset(c, "now")
    pid = _products(c)["Mineral"]
    assert c.put(f"/api/admin/products/{pid}/stock", auth=AUTH, json={"stock": 3}).status_code == 200
    assert _post(c, "6", [("Mineral", 2)], no_print=True).status_code == 200
    prod = next(p for p in c.get("/api/admin/products", auth=AUTH).json() if p["id"] == pid)
    assert prod["stock"] == 1                                          # Restmenge zählt herunter
    r = _post(c, "6", [("Mineral", 2)], no_print=True)                 # nicht genug: abgelehnt wie sonst auch
    assert r.status_code == 422 and r.json()["stock_problems"]
    c.put(f"/api/admin/products/{pid}/stock", auth=AUTH, json={"stock": None})


def test_no_print_on_open_table_is_marked(srv):
    c = srv
    _reset(c, "open", "any")
    assert _post(c, "12", [("Bier 5dl", 2)]).status_code == 200
    assert _post(c, "12", [("Bier 5dl", 1)], no_print=True).status_code == 200
    t12 = {t["table"]: t for t in _tabs(c)}["12"]
    assert t12["orders"] == 2 and t12["net"] == 3 * 650
    d = c.get(f"/api/tabs/{t12['id']}", headers=H("XCover 1")).json()
    # nachgebucht: eigene Position mit Markierung, die bisherige behält ihre Nummer (Einzeln bezahlt)
    assert [(i["id"], i["qty"], i.get("nb", False)) for i in d["items"]] == [(1, 2, False), (2, 1, True)]
    _reset(c, "now")


def test_setting_decides_who_may(srv):
    c = srv
    _reset(c, "now")
    boot = lambda who: c.get("/api/bootstrap", headers=H(who)).json()["can_nachbuchen"]  # noqa: E731
    assert boot("XCover 1") is True                                    # Vorgabe: alle Bedienungen
    assert c.put("/api/admin/settings", auth=AUTH, json={"nachbuchen": "storno"}).status_code == 200
    c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "service"})
    c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 2", "role": "storno"})
    assert boot("XCover 1") is False and boot("XCover 2") is True
    r = _post(c, "8", [("Bier 5dl", 1)], who="XCover 1", no_print=True)
    assert r.status_code == 422 and "nicht erlaubt" in r.json()["detail"]   # 422: auf dem Handy «abgelehnt»
    assert _post(c, "8", [("Bier 5dl", 1)], who="XCover 2", no_print=True).status_code == 200
    assert _post(c, "8", [("Bier 5dl", 1)], who="XCover 1").status_code == 200   # gewöhnlich bestellen geht immer
    c.put("/api/admin/settings", auth=AUTH, json={"nachbuchen": "off"})
    assert boot("XCover 2") is False
    assert _post(c, "8", [("Bier 5dl", 1)], who="XCover 2", no_print=True).status_code == 422
    assert c.put("/api/admin/settings", auth=AUTH, json={"nachbuchen": "nie"}).status_code == 422
    c.put("/api/admin/settings", auth=AUTH, json={"nachbuchen": "all"})
