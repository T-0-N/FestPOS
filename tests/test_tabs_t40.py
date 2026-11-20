"""T40: offener Tisch und Tagesabschluss – geprüft wird im Schreibbefehl, nicht vorher: gleichzeitige Zahlungen nie
über dem offenen Betrag, Schliessen nur ohne neue Bestellung/Zahlung seit dem Lesen, Tagesabschluss nie mit einem
eben eröffneten Tisch, Aufteilung der Zahlarten nie negativ."""
from __future__ import annotations

import random
import uuid

import pytest

from tests.test_api import AUTH, setup_shop


@pytest.fixture
def shop(client):
    setup_shop(client)
    client.put("/api/admin/settings", auth=AUTH, json={"tab_mode": "open", "tab_who": "any"})
    yield client


def _order(table="7", qty=2, test=False):
    from app import core
    from app.orders import create_order
    pid = core.rt.store.scalar("SELECT id FROM products WHERE name='Bier 5dl'")
    return create_order(core.rt.store, client_uuid=str(uuid.uuid4()), table_label=table,
                        lines=[{"product_id": pid, "qty": qty}], test=test)


def _tab(table="7"):
    from app import core
    return core.rt.store.scalar("SELECT id FROM tabs WHERE closed_at IS NULL AND table_label=?", (table,))


def test_concurrent_payments_never_exceed_open(shop, monkeypatch):
    from app import core, tabs
    _order()                                                                    # 13.00
    tid = _tab()
    stale = tabs._sums(core.rt.store, [tid])                                    # beide Handys lesen «13.00 offen»
    tabs.pay(core.rt.store, tab_id=tid, key="pay-a-000001", amount=1300, payment="cash")
    real = tabs._sums
    monkeypatch.setattr(tabs, "_sums", lambda store, ids: stale if ids == [tid] else real(store, ids))
    with pytest.raises(tabs.TabError) as e:                                     # Vorprüfung sieht noch 13.00 offen
        tabs.pay(core.rt.store, tab_id=tid, key="pay-b-000001", amount=1300, payment="card")
    assert e.value.status == 409
    assert core.rt.store.scalar("SELECT SUM(amount_cents) FROM tab_payments WHERE tab_id=?", (tid,)) == 1300
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='tab_pay'") == 1   # nichts Erfundenes


def test_close_refuses_when_order_came_in_between(shop, monkeypatch):
    from app import core, tabs
    _order()
    tid = _tab()
    tabs.pay(core.rt.store, tab_id=tid, key="pay-c-000001", amount=1300, payment="cash")
    stale = tabs._sums(core.rt.store, [tid])                                    # gelesen: bezahlt
    late = _order()                                                             # eine Bestellung kommt dazu
    assert core.rt.store.scalar("SELECT tab_id FROM orders WHERE id=?", (late.order_id,)) == tid
    real = tabs._sums
    monkeypatch.setattr(tabs, "_sums", lambda store, ids: stale if ids == [tid] else real(store, ids))
    with pytest.raises(tabs.TabError) as e:
        tabs.close(core.rt.store, tab_id=tid, by="test")
    assert e.value.status == 409
    monkeypatch.setattr(tabs, "_sums", real)
    assert core.rt.store.scalar("SELECT closed_at FROM tabs WHERE id=?", (tid,)) is None   # bleibt offen
    assert core.rt.store.scalar("SELECT payment FROM orders WHERE id=?", (late.order_id,)) is None   # nicht «bar»
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='tab_close'") == 0


def test_close_by_other_device_meanwhile_is_fine(shop):
    from app import core, tabs
    _order()
    tid = _tab()
    tabs.pay(core.rt.store, tab_id=tid, key="pay-d-000001", amount=1300, payment="cash")
    d = tabs.close(core.rt.store, tab_id=tid, by="a")
    assert d["closed_at"] and tabs.close(core.rt.store, tab_id=tid, by="b")["closed_at"] == d["closed_at"]
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='tab_close'") == 1
    with pytest.raises(tabs.TabError):
        tabs.undo_payment(core.rt.store, tab_id=tid, key="pay-d-000001", by="x")       # Tisch zu: nichts rückgängig
    assert core.rt.store.scalar("SELECT undone_at FROM tab_payments WHERE key='pay-d-000001'") is None


def test_closing_never_takes_a_tab_opened_meanwhile(shop, monkeypatch):
    from app import core, ledger, tabs
    o = _order()
    tid = _tab()
    tabs.pay(core.rt.store, tab_id=tid, key="pay-e-000001", amount=1300, payment="cash")
    tabs.close(core.rt.store, tab_id=tid, by="a")
    _order(table="8")                                                           # neuer Tisch …
    monkeypatch.setattr(tabs, "count_open", lambda store: 0)                    # … nach der Prüfung eröffnet
    with pytest.raises(ledger.LedgerError) as e:
        ledger.create_closing(core.rt.store, "test", ledger.ClosingIn())
    assert e.value.status == 409
    assert core.rt.store.scalar("SELECT COUNT(*) FROM closings") == 0
    assert core.rt.store.scalar("SELECT closing_id FROM orders WHERE id=?", (o.order_id,)) is None


def test_allocation_never_negative_and_exact():
    from app.ledger import _alloc_orders, _tab_split
    rnd = random.Random(40)
    kinds = ["cash", "card", "twint", "other"]
    for _ in range(3000):
        nets = [rnd.choice([0, 1, 5, 99, 650, 800, 1250, rnd.randint(1, 5000)]) for _ in range(rnd.randint(1, 7))]
        pays = [(rnd.choice(kinds), rnd.randint(1, 4000)) for _ in range(rnd.randint(0, 5))]
        alloc = dict(_tab_split(sum(nets), pays))
        parts = _alloc_orders(nets, alloc)
        for net, p in zip(nets, parts):
            assert all(v >= 0 for _, v in p), (nets, pays, parts)
            assert sum(v for _, v in p) == net, (nets, pays, parts)
        per_kind: dict[str, int] = {}
        for p in parts:
            for k, v in p:
                per_kind[k] = per_kind.get(k, 0) + v
        assert per_kind == {k: v for k, v in alloc.items() if v}, (nets, pays)


def test_summary_per_device_never_negative(shop):
    """Fall aus dem Review: vier Bestellungen, gemischt bezahlt – bisher −2 Rappen bar für eine Bedienung."""
    from app import core, ledger, tabs
    from app.orders import create_order
    pid = core.rt.store.scalar("SELECT id FROM products WHERE name='Bier 5dl'")
    for n in range(4):                                                          # 4× 6.50 von vier Bedienungen
        create_order(core.rt.store, client_uuid=str(uuid.uuid4()), table_label="7", device=f"XCover {n}",
                     lines=[{"product_id": pid, "qty": 1}])
    tid = _tab()
    tabs.pay(core.rt.store, tab_id=tid, key="pay-f-000001", amount=1, payment="card")       # bisher: −2 Rappen Karte
    tabs.pay(core.rt.store, tab_id=tid, key="pay-f-000002", amount=2599, payment="cash")
    rows = ledger._open_rows(core.rt.store)
    s = ledger.summarize(rows, ledger._test_ids(core.rt.store, rows))
    assert len(s["by_device"]) == 4
    for d in s["by_device"]:
        assert all(v >= 0 for v in d["by_payment"].values()), d
    assert s["by_payment"].get("card") == 1 and s["by_payment"].get("cash") == 2599


def test_limit_sql_on_both_databases(store):
    """LIMIT_SQL (offen bzw. bar gerundet) und changes() auf SQLite und echtem rqlite."""
    from app import tabs
    store.execute("INSERT INTO tabs(id, table_label, opened_at, test, open_key) VALUES (5, '7', '2026-11-10T20:00:00', 0, '0:7')")
    store.execute("INSERT INTO orders(client_uuid, table_label, total_cents, created_at, tab_id) VALUES "
                  "('t40-1', '7', 1298, '2026-11-10T20:00:00', 5)")
    assert store.scalar(f"SELECT {tabs.LIMIT_SQL}", tabs.limit_args(5)) == 1300          # bar: 12.98 -> 13.00
    store.execute("INSERT INTO tab_payments(key, tab_id, amount_cents, payment, created_at) VALUES "
                  "('t40-p', 5, 1000, 'cash', '2026-11-10T20:01:00')")
    assert store.scalar(f"SELECT {tabs.LIMIT_SQL}", tabs.limit_args(5)) == 300
    upd = ("UPDATE tab_payments SET undone_at='x' WHERE key='t40-p' AND undone_at IS NULL", ())
    aud = tabs._if_changed(("INSERT INTO audit_log(at, actor, action, details) VALUES (?,?,?,?)", ("t", "a", "b", "{}")))
    assert store.batch([upd, aud])[1].rows_affected == 1
    assert store.batch([upd, aud])[1].rows_affected == 0


def test_unpaid_cancels_only_orders_it_has_seen(shop, monkeypatch):
    from app import core, tabs
    _order()
    tid = _tab()
    stale = tabs._sums(core.rt.store, [tid])
    late = _order()                                                             # kommt nach dem Lesen dazu
    real = tabs._sums
    monkeypatch.setattr(tabs, "_sums", lambda store, ids: stale if ids == [tid] else real(store, ids))
    with pytest.raises(tabs.TabError):
        tabs.unpaid(core.rt.store, tab_id=tid, by_user="test", note=None)
    assert core.rt.store.scalar("SELECT COUNT(*) FROM cancellations WHERE order_id=?", (late.order_id,)) == 0
    assert core.rt.store.scalar("SELECT closed_at FROM tabs WHERE id=?", (tid,)) is None


def _between(monkeypatch, store, action):
    """Wettlauf: action läuft genau zwischen Lesen und Schreiben (vor dem ersten Batch des Aufrufers)."""
    real, done = store.batch, []

    def batch(stmts, **kw):
        if not done:
            done.append(1)
            action()
        return real(stmts, **kw)
    monkeypatch.setattr(store, "batch", batch)


def test_tab_storno_refuses_when_other_storno_came_in_between(shop, monkeypatch):
    """CodeRabbit PR 71: zwei Stornos verschiedener Bestellungen zugleich – zusammen nie mehr als offen."""
    from app import core, ledger, tabs
    st = core.rt.store
    first = _order()                                                            # 13.00
    _order()                                                                    # 13.00
    tid = _tab()
    tabs.pay(st, tab_id=tid, key="pay-e-000001", amount=1300, payment="cash")   # offen: 13.00
    bier = tabs.detail(st, tid)["items"][0]["id"]
    _between(monkeypatch, st, lambda: ledger.cancel(st, order_id=first.order_id, lines=None, reason="wrong", note=None,
                                                    by_user="admin", client_uuid="other-storno-1"))
    with pytest.raises(tabs.TabError) as e:
        tabs.storno(st, tab_id=tid, lines={bier: 2}, reason="wrong", note=None, by_device="XCover 1",
                    client_uuid="tab-storno-race-1")
    assert e.value.status == 409
    assert st.scalar("SELECT COUNT(*) FROM cancellations WHERE client_uuid LIKE 'tab-storno-race-1:%'") == 0
    s = tabs._sums(st, [tid])[tid]
    assert s["net"] == 1300 and s["paid"] == 1300                               # nie mehr bezahlt als übrig


def test_tab_storno_refuses_when_payment_was_swapped(shop, monkeypatch):
    """CodeRabbit PR 71: Zahlung zurückgenommen und gleich hoch neu gebucht, jetzt «Einzeln» für genau diese Stück."""
    from app import core, tabs
    st = core.rt.store
    _order()                                                                    # 2× Bier 13.00
    tid = _tab()
    tabs.pay(st, tab_id=tid, key="pay-f-000001", amount=650, payment="cash")    # eigener Betrag
    bier = tabs.detail(st, tid)["items"][0]["id"]

    def swap():
        tabs.undo_payment(st, tab_id=tid, key="pay-f-000001", by="b")
        tabs.pay(st, tab_id=tid, key="pay-f-000002", amount=650, payment="cash", part=1, items={str(bier): 2})
    _between(monkeypatch, st, swap)
    with pytest.raises(tabs.TabError) as e:
        tabs.storno(st, tab_id=tid, lines={bier: 1}, reason="wrong", note=None, by_device="XCover 1",
                    client_uuid="tab-storno-race-2")
    assert e.value.status == 409
    assert st.scalar("SELECT COUNT(*) FROM cancellations") == 0


def test_tab_storno_retry_is_scoped_and_exact(shop):
    """Wiederholung nur mit genau derselben Nummer an diesem Tisch – «%» in der Nummer ist kein Muster."""
    from app import core, tabs
    st = core.rt.store
    _order()
    tid = _tab()
    bier = tabs.detail(st, tid)["items"][0]["id"]
    r = tabs.storno(st, tab_id=tid, lines={bier: 1}, reason="wrong", note=None, by_device="X", client_uuid="abcd-1234")
    assert r["amount_cents"] == 650 and not r["duplicate"]
    assert tabs.storno(st, tab_id=tid, lines={bier: 1}, reason="wrong", note=None, by_device="X",
                       client_uuid="abcd-1234")["duplicate"]
    r = tabs.storno(st, tab_id=tid, lines={bier: 1}, reason="wrong", note=None, by_device="X", client_uuid="abcd%")
    assert not r["duplicate"] and tabs._sums(st, [tid])[tid]["net"] == 0


def test_tab_storno_refuses_when_payment_swapped_after_reading_paid_items(shop, monkeypatch):
    """CodeRabbit PR 72: Tausch genau nachdem die Zahlungen gelesen wurden (Zähler, Summe und «Einzeln» bezahlte Stück
    in einer Abfrage) – der Schreibbefehl merkt den Tausch."""
    from app import core, tabs
    st = core.rt.store
    _order()                                                                    # 2× Bier 13.00
    tid = _tab()
    tabs.pay(st, tab_id=tid, key="pay-g-000001", amount=650, payment="cash")
    bier = tabs.detail(st, tid)["items"][0]["id"]
    real, done = st.query, []

    def query(sql, *a, **kw):
        out = real(sql, *a, **kw)
        if sql.startswith("SELECT amount_cents, items, undone_at FROM tab_payments") and not done:
            done.append(1)
            tabs.undo_payment(st, tab_id=tid, key="pay-g-000001", by="b")
            tabs.pay(st, tab_id=tid, key="pay-g-000002", amount=650, payment="cash", part=1, items={str(bier): 2})
        return out
    monkeypatch.setattr(st, "query", query)
    with pytest.raises(tabs.TabError) as e:
        tabs.storno(st, tab_id=tid, lines={bier: 1}, reason="wrong", note=None, by_device="XCover 1",
                    client_uuid="tab-storno-race-3")
    assert done and e.value.status == 409
    assert st.scalar("SELECT COUNT(*) FROM cancellations") == 0
