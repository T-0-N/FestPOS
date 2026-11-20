"""T37: Rückerstattungen der QR-Bestellung als Aufträge – im selben Batch wie das Storno, nie doppelt, unklare
Antworten von Payrexx werden nicht von selbst wiederholt, offene Beträge bleiben sichtbar."""
from __future__ import annotations

import json
import time

from tests.test_api import AUTH
from tests.test_gast import G, _order, _products, gast  # noqa: F401 – Fixture


def _paid_order(client, fake, qty=2, key="key-0000001"):
    from app import core
    beer = next(iter(_products(client).values()))
    o = _order(client, [{"product_id": beer["id"], "qty": qty}], key=key).json()
    gid = core.rt.store.scalar("SELECT id FROM gast_orders WHERE token=?", (o["token"],))
    fake.gateways[gid]["status"] = "confirmed"
    v = client.get(f"/api/gast/order/{o['token']}", headers=G).json()
    assert v["status"] == "ordered"
    return o, gid, v["nr"], beer


def _jobs(gid):
    from app import core
    return core.rt.store.query("SELECT key, amount_cents, status FROM gast_refunds WHERE gast_id=? ORDER BY id", (gid,))


def test_storno_creates_job_in_same_batch_and_survives_crash(client, gast, monkeypatch):  # noqa: F811
    from app import core, gast as g
    o, gid, nr, beer = _paid_order(client, gast)
    real = g.process_refunds
    monkeypatch.setattr(g, "process_refunds", lambda gid=None: None)          # Prozess endet nach dem Storno
    body = {"reason": "wrong", "client_uuid": "st-000000001", "lines": None}
    assert client.post(f"/api/admin/orders/{nr}/storno", auth=AUTH, json=body).status_code == 200
    assert gast.refunds == [] and _jobs(gid) == [{"key": "storno:st-000000001", "amount_cents": 2 * beer["price"],
                                                  "status": "open"}]
    assert core.rt.store.scalar("SELECT refund_open FROM gast_orders WHERE id=?", (gid,)) == 2 * beer["price"]
    monkeypatch.setattr(g, "process_refunds", real)
    # dasselbe Storno nochmals gesendet: Duplikat, aber der offene Auftrag geht jetzt raus – genau einmal
    assert client.post(f"/api/admin/orders/{nr}/storno", auth=AUTH, json=body).json()["duplicate"]
    assert gast.refunds == [("7001", 2 * beer["price"])]
    g.tick()
    assert len(gast.refunds) == 1 and [j["status"] for j in _jobs(gid)] == ["done"]
    assert client.get(f"/api/gast/order/{o['token']}", headers=G).json()["status"] == "refunded"


def test_timeout_is_unclear_and_never_retried_by_itself(client, gast, monkeypatch):  # noqa: F811
    from app import core, gast as g, payrexx_api
    o, gid, nr, beer = _paid_order(client, gast)
    monkeypatch.setattr(payrexx_api, "TIMEOUT", 0.3)
    gast.refund_delay = 1.0                                   # Payrexx erstattet zurück, antwortet aber zu spät
    assert client.post(f"/api/admin/gast/orders/{gid}/refund", auth=AUTH, json={}).status_code == 502
    time.sleep(1.0)
    gast.refund_delay = 0
    assert gast.refunds == [("7001", 2 * beer["price"])]     # Geld ist bei Payrexx schon zurück
    job = core.rt.store.query_one("SELECT * FROM gast_refunds WHERE gast_id=?", (gid,))
    assert job["status"] == "unclear" and job["error"] == "unclear"
    for _ in range(3):
        g.tick()
    assert len(gast.refunds) == 1                             # nie von selbst nochmals
    assert client.post(f"/api/admin/gast/orders/{gid}/refund", auth=AUTH, json={}).status_code == 409
    rows = client.get("/api/admin/gast/orders", auth=AUTH).json()["orders"]
    row = next(r for r in rows if r["id"] == gid)
    assert row["refund_open"] == 2 * beer["price"] and row["refunds"][0]["status"] == "unclear"
    assert "gast_refund_unclear" in json.dumps(client.get("/api/admin/audit", auth=AUTH).json())
    # im Payrexx-Konto nachgesehen: ist zurück -> «Ist erledigt»
    r = client.post(f"/api/admin/gast/refunds/{job['id']}/done", auth=AUTH, json={})
    assert r.status_code == 200 and r.json()["status"] == "done"
    v = client.get(f"/api/gast/order/{o['token']}", headers=G).json()
    assert v["status"] == "refunded" and v["refunded"] == 2 * beer["price"] and len(gast.refunds) == 1
    assert client.post(f"/api/admin/gast/refunds/{job['id']}/retry", auth=AUTH, json={}).status_code == 409
    assert "gast_refund_done" in json.dumps(client.get("/api/admin/audit", auth=AUTH).json())


def test_retry_button_sends_unclear_again(client, gast, monkeypatch):  # noqa: F811
    from app import core, payrexx_api
    _o, gid, _nr, beer = _paid_order(client, gast)
    monkeypatch.setattr(payrexx_api, "TIMEOUT", 0.3)
    gast.refund_delay = 1.0
    gast.refund_fails = True                                  # zu spät UND abgelehnt: kam nie an
    client.post(f"/api/admin/gast/orders/{gid}/refund", auth=AUTH, json={})
    time.sleep(1.0)
    gast.refund_delay, gast.refund_fails = 0, False
    job = core.rt.store.query_one("SELECT * FROM gast_refunds WHERE gast_id=?", (gid,))
    assert job["status"] == "unclear" and gast.refunds == []
    r = client.post(f"/api/admin/gast/refunds/{job['id']}/retry", auth=AUTH, json={})
    assert r.status_code == 200 and r.json()["status"] == "done" and gast.refunds == [("7001", 2 * beer["price"])]


def test_open_amount_stays_after_later_success(client, gast):  # noqa: F811
    """Teilrückzahlung scheitert, eine spätere gelingt: der erste Betrag bleibt offen (Befund 12)."""
    from app import core, gast as g
    _o, gid, _nr, beer = _paid_order(client, gast, qty=3)
    gast.refund_fails = True
    assert g.do_refund(gid, 100, "test") is False
    gast.refund_fails = False
    assert g.do_refund(gid, 200, "test") is True
    row = core.rt.store.query_one("SELECT refunded_cents, refund_open, error FROM gast_orders WHERE id=?", (gid,))
    assert row["refunded_cents"] == 200 and row["refund_open"] == 100 and row["error"] == "refund:http"
    # nie mehr als bezahlt: der Rest ist reserviert (gescheitert 100 + zurück 200)
    assert g.do_refund(gid, 10_000, "test") is True
    assert sum(a for _, a in gast.refunds) == 3 * beer["price"] - 100
    assert core.rt.store.scalar("SELECT SUM(amount_cents) FROM gast_refunds WHERE gast_id=?", (gid,)) == 3 * beer["price"]
    # «Zurückerstatten» sendet die gescheiterte nochmals
    r = client.post(f"/api/admin/gast/orders/{gid}/refund", auth=AUTH, json={})
    assert r.status_code == 200 and r.json()["refunded_cents"] == 3 * beer["price"]
    assert core.rt.store.scalar("SELECT refund_open FROM gast_orders WHERE id=?", (gid,)) == 0


def test_offline_stays_open_and_goes_out_later(client, gast, monkeypatch):  # noqa: F811
    """Payrexx gar nicht erreichbar (Anfrage kam sicher nicht an): bleibt offen, nächste Runde sendet."""
    from app import gast as g, payrexx_api
    _o, gid, _nr, beer = _paid_order(client, gast)
    base = payrexx_api.BASE
    monkeypatch.setattr(payrexx_api, "BASE", "http://127.0.0.1:9/v1.0")       # Port 9: Verbindung abgewiesen
    assert g.do_refund(gid, 2 * beer["price"], "test") is False
    assert [j["status"] for j in _jobs(gid)] == ["open"]
    assert client.post(f"/api/admin/gast/orders/{gid}/refund", auth=AUTH, json={}).status_code == 502   # nicht «schon zurück»
    monkeypatch.setattr(payrexx_api, "BASE", base)
    g.tick()
    assert [j["status"] for j in _jobs(gid)] == ["done"] and gast.refunds == [("7001", 2 * beer["price"])]


def test_stuck_sending_becomes_unclear(client, gast):  # noqa: F811
    from app import core, gast as g
    _o, gid, _nr, beer = _paid_order(client, gast)
    core.rt.store.batch(g.refund_stmts(gid, 100, "x:1", "test"))
    core.rt.store.execute("UPDATE gast_refunds SET status='sending', sent_at='2020-01-01T10:00:00'")
    g.tick()
    assert [j["status"] for j in _jobs(gid)] == ["unclear"] and gast.refunds == []


def test_legacy_amounts_are_kept_on_upgrade(client, gast):  # noqa: F811
    from app import core, gast as g
    _o, gid, _nr, beer = _paid_order(client, gast)
    st = core.rt.store
    st.execute("UPDATE gast_orders SET refunded_cents=150, refund_open=50 WHERE id=?", (gid,))   # Stand vor T37
    st.init_schema()
    st.init_schema()                                                                          # wiederholbar
    jobs = st.query("SELECT key, amount_cents, status FROM gast_refunds WHERE gast_id=? ORDER BY key", (gid,))
    assert jobs == [{"key": f"legacy-open:{gid}:50", "amount_cents": 50, "status": "failed"},
                    {"key": f"legacy:{gid}:150", "amount_cents": 150, "status": "done"}]
    st.batch(g.sync_stmts(gid))
    row = st.query_one("SELECT refunded_cents, refund_open FROM gast_orders WHERE id=?", (gid,))
    assert row == {"refunded_cents": 150, "refund_open": 50}
    # während des Updates bucht ein Pi mit der alten Version noch 100 dazu: nur die Differenz kommt dazu
    st.execute("UPDATE gast_orders SET refunded_cents=250 WHERE id=?", (gid,))
    st.init_schema()
    st.init_schema()
    assert st.scalar("SELECT SUM(amount_cents) FROM gast_refunds WHERE gast_id=? AND status='done'", (gid,)) == 250


def test_server_error_from_payrexx_is_unclear(client, gast):  # noqa: F811
    """5xx von Payrexx oder einem Proxy: die Rückerstattung kann ausgeführt sein – unklar, nie «gescheitert»."""
    from app import gast as g
    _o, gid, _nr, beer = _paid_order(client, gast)
    gast.refund_fails, gast.refund_fail_code = True, 502
    assert g.do_refund(gid, 100, "test") is False
    assert [j["status"] for j in _jobs(gid)] == ["unclear"]
    rid = __import__("app.core", fromlist=["rt"]).rt.store.scalar("SELECT id FROM gast_refunds WHERE gast_id=?", (gid,))
    assert client.post(f"/api/admin/gast/refunds/{rid}/done", auth=AUTH, json={}).status_code == 200
    assert client.post(f"/api/admin/gast/refunds/{rid}/retry", auth=AUTH, json={}).status_code == 409
    audit = json.dumps(client.get("/api/admin/audit", auth=AUTH).json())
    assert "gast_refund_done" in audit and "gast_refund_retry" not in audit          # 409: nichts protokolliert
