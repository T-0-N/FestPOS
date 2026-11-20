"""T39: Kartenzahlung am Handy absichern – Bestätigen/Abbrechen/Abfragen nur startendes Handy, wer den Tisch abrechnen
darf, oder Festleitung; Bestätigung und Tischzahlung in einem Batch (fehlende Tischzahlung wird nachgetragen); nie mehr
als offen an den Tisch (Zuvielzahlung gemeldet); höchstens eine offene Zahlung pro Bestellung/Tisch und Teil;
Erfolg ohne Betrag gilt nicht als bezahlt."""
from __future__ import annotations

import uuid

from tests.test_api import AUTH
from tests.test_payrexx import _enable, _extras, _order, _result, _start_pay, pay  # noqa: F401 – Fixture
from tests.test_tabs import H, _reset, srv  # noqa: F401 – Fixture


def test_only_own_phone_may_touch(srv):  # noqa: F811
    c = srv
    _reset(c, "now")
    c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": "1357"})
    pid_bier = {p["name"]: p["id"] for p in c.get("/api/admin/products", auth=AUTH).json()}["Bier 5dl"]
    cu = str(uuid.uuid4())
    assert c.post("/api/orders", headers=H("XCover 1"), json={"client_uuid": cu, "table": "61", "device": "XCover",
                                                              "test": True, "lines": [{"product_id": pid_bier, "qty": 1}]}).status_code == 200
    r = c.post("/api/phone/payrexx", headers=H("XCover 1"), json={"client_uuid": cu, "amount_cents": 650,
                                                                   "part": "all", "key": "t39-own-000001"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    for path, method in ((f"/api/phone/payrexx/{pid}", "GET"), (f"/api/phone/payrexx/{pid}/manual", "POST"),
                         (f"/api/phone/payrexx/{pid}/cancel", "POST")):
        r = c.request(method, path, headers=H("XCover 2"))
        assert r.status_code == 403, (path, r.text)
    assert c.get(f"/api/phone/payrexx/{pid}", headers=H("XCover 1")).status_code == 200
    assert c.post(f"/api/phone/payrexx/{pid}/cancel", headers=H("XCover 1")).json()["status"] == "cancelled"


def _tab_setup(client):
    from app import core
    client.put("/api/admin/settings", auth=AUTH, json={"tab_mode": "open", "tab_who": "any"})
    cu = _order(client)                                                    # 2× Bier = 13.00 an Tisch 7
    tab_id = core.rt.store.scalar("SELECT tab_id FROM orders WHERE client_uuid=?", (cu,))
    assert tab_id
    return tab_id


def _start_tab(client, tab_id, amount, key, part="all"):
    r = client.post("/api/phone/payrexx", json={"tab_id": tab_id, "amount_cents": amount, "part": part, "key": key})
    assert r.status_code == 200, r.text
    return r.json()


def test_missing_tab_payment_is_repaired(client, pay):  # noqa: F811
    from app import core
    _enable(client)
    tab_id = _tab_setup(client)
    p = _start_tab(client, tab_id, 1300, "t39-rep-00001")
    x = _extras(p["launch"])
    r = client.post("/api/tap/result", json={"id": p["id"], "token": x["token"], "ok": True, "result": _result("13.00", x["ref"])})
    assert r.json()["status"] == "confirmed"
    assert core.rt.store.scalar("SELECT amount_cents FROM tab_payments WHERE key=?", (f"payrexx-{p['id']}",)) == 1300
    core.rt.store.execute("DELETE FROM tab_payments")                       # Stand vor T39: Tischzahlung fehlt
    assert client.get(f"/api/phone/payrexx/{p['id']}").json()["status"] == "confirmed"
    assert core.rt.store.scalar("SELECT amount_cents FROM tab_payments WHERE key=?", (f"payrexx-{p['id']}",)) == 1300
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='payrexx_paid'") == 1


def test_overpaid_is_not_booked_to_the_table(client, pay):  # noqa: F811
    from app import core, tabs
    _enable(client)
    tab_id = _tab_setup(client)
    p = _start_tab(client, tab_id, 1300, "t39-over-0001")
    tabs.pay(core.rt.store, tab_id=tab_id, key="cash-0000001", amount=1300, payment="cash")   # inzwischen bar bezahlt
    x = _extras(p["launch"])
    r = client.post("/api/tap/result", json={"id": p["id"], "token": x["token"], "ok": True, "result": _result("13.00", x["ref"])})
    assert r.json()["status"] == "confirmed" and r.json()["error"] == "overpaid"
    assert core.rt.store.scalar("SELECT SUM(amount_cents) FROM tab_payments WHERE tab_id=?", (tab_id,)) == 1300
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='payrexx_overpaid'") == 1
    client.get(f"/api/phone/payrexx/{p['id']}")                            # nicht nochmals melden/buchen
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='payrexx_overpaid'") == 1


def test_simultaneous_start_gives_one_waiting_payment(client, pay, monkeypatch):  # noqa: F811
    from app import core, payrexx
    _enable(client)
    tab_id = _tab_setup(client)
    first = _start_tab(client, tab_id, 1300, "t39-sim-00001")
    real = core.rt.store.query_one
    calls = {"n": 0}

    def stale(sql, params=()):                    # das zweite Handy hat vor dem ersten gelesen: «noch keine Zahlung»
        if "status IN ('waiting','confirmed')" in sql and calls["n"] == 0:
            calls["n"] += 1
            return None
        return real(sql, params)
    monkeypatch.setattr(core.rt.store, "query_one", stale)
    second = payrexx.start(None, 1300, "all", "t39-sim-00002", "XCover 2", 0, tab_id, "dev-2")
    assert second["id"] == first["id"]                                      # dieselbe Zahlung, keine zweite
    monkeypatch.setattr(core.rt.store, "query_one", real)
    assert core.rt.store.scalar("SELECT COUNT(*) FROM payrexx_payments WHERE status='waiting'") == 1


def test_success_without_amount_is_not_paid(client, pay):  # noqa: F811
    from app import core
    _enable(client)
    cu = _order(client)
    p = _start_pay(client, cu, 500, part="p1", key="t39-noamt-001")
    x = _extras(p["launch"])
    r = client.post("/api/tap/result", json={"id": p["id"], "token": x["token"], "ok": True, "result": _result("", x["ref"])})
    assert r.json()["status"] == "waiting" and r.json()["error"] == "unclear"
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='payrexx_unclear'") == 1
    r = client.post(f"/api/phone/payrexx/{p['id']}/manual", headers={"X-Festpos-Device": "XCover%201"})
    assert r.json()["status"] == "confirmed"


def test_conditional_audit_and_partial_index_on_both_databases(store):
    """changes() im Batch und die eindeutigen Teilindizes – auf SQLite und echtem rqlite."""
    from app import ledger_base as lb
    from app.store import StoreError
    store.execute("INSERT INTO payrexx_payments(key, order_id, part, amount_cents, status, created_at) "
                  "VALUES ('a-1', 1, 'all', 500, 'waiting', '2026-11-10T20:00:00')")
    audit = lb.audit_stmt("payrexx", "payrexx_paid", order=1)
    cond = (audit[0].replace(" VALUES (?,?,?,?)", " SELECT ?,?,?,? WHERE changes()=1"), audit[1])
    upd = ("UPDATE payrexx_payments SET status='confirmed' WHERE key='a-1' AND status='waiting'", ())
    assert store.batch([upd, cond])[1].rows_affected == 1
    assert store.batch([upd, cond])[1].rows_affected == 0                 # nichts geändert: kein Protokoll
    store.execute("INSERT INTO payrexx_payments(key, order_id, part, amount_cents, status, created_at) "
                  "VALUES ('a-2', 1, 'all', 500, 'waiting', '2026-11-10T20:01:00')")
    try:
        store.execute("INSERT INTO payrexx_payments(key, order_id, part, amount_cents, status, created_at) "
                      "VALUES (?, 1, 'all', 500, 'waiting', '2026-11-10T20:02:00')", (str(uuid.uuid4()),))
        raise AssertionError("zweite offene Zahlung angenommen")
    except StoreError as e:
        assert e.is_unique_violation
    store.execute("INSERT INTO payrexx_payments(key, order_id, part, amount_cents, status, created_at) "
                  "VALUES ('a-s1', 1, 's1', 250, 'waiting', '2026-11-10T20:03:00')")            # Teilen: erlaubt
    store.execute("INSERT INTO payrexx_payments(key, order_id, part, amount_cents, status, created_at) "
                  "VALUES ('a-s2', 1, 's1', 250, 'waiting', '2026-11-10T20:04:00')")


def test_waiting_payment_of_other_phone_is_not_handed_out(srv):  # noqa: F811
    c = srv
    _reset(c, "now")
    c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": "1357"})
    pid_bier = {p["name"]: p["id"] for p in c.get("/api/admin/products", auth=AUTH).json()}["Bier 5dl"]
    cu = str(uuid.uuid4())
    c.post("/api/orders", headers=H("XCover 1"), json={"client_uuid": cu, "table": "62", "device": "XCover", "test": True,
                                                       "lines": [{"product_id": pid_bier, "qty": 1}]})
    body = {"client_uuid": cu, "amount_cents": 650, "part": "all"}
    assert c.post("/api/phone/payrexx", headers=H("XCover 1"), json={**body, "key": "t39-two-00001"}).status_code == 200
    r = c.post("/api/phone/payrexx", headers=H("XCover 2"), json={**body, "key": "t39-two-00002"})
    assert r.status_code == 409                                   # nicht die Zahlung von XCover 1 zurückgeben


def test_rejection_answer_survives_failing_audit(client, monkeypatch):
    """Das Protokoll der Ablehnung (T42) darf die Antwort 422 nicht in einen 5xx verwandeln."""
    import uuid as _uuid

    from app import ledger_base
    from tests.test_api import setup_shop
    setup_shop(client)
    client.put("/api/admin/settings", auth=AUTH, json={"nachbuchen": "off"})
    pid = client.get("/api/bootstrap").json()["products"][0]["id"]

    def broken(*a, **k):
        raise RuntimeError("Datenbank weg")
    monkeypatch.setattr(ledger_base, "audit", broken)
    r = client.post("/api/orders", json={"client_uuid": str(_uuid.uuid4()), "table": "7", "no_print": True,
                                         "lines": [{"product_id": pid, "qty": 1}]})
    assert r.status_code == 422
