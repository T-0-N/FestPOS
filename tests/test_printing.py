import asyncio
import uuid

import pytest

from app.escpos import Bon, PrinterAddr, PrinterError, query_status, send_raw
from app.orders import OrderError, create_order
from app.print_service import PrintSpooler, list_jobs, money, queue_status, reprint
from tests.fake_printer import FakePrinter, to_text


def run(coro):
    return asyncio.run(coro)


def seed(store, bar_port: int, kitchen_port: int):
    store.batch([
        ("INSERT INTO printers(id,name,host,port) VALUES (1,'Getränkeausgabe','127.0.0.1',?)", (bar_port,)),
        ("INSERT INTO printers(id,name,host,port,show_prices) VALUES (2,'Küche','127.0.0.1',?,0)", (kitchen_port,)),
        ("INSERT INTO categories(id,name) VALUES (1,'Getränke'),(2,'Essen')", ()),
        ("INSERT INTO products(id,name,price_cents,category_id,printer_id,single_ticket,sort) VALUES "
         "(1,'Bier 5dl',650,1,1,0,1),(2,'Mineral',400,1,1,0,2),"
         "(3,'Bratwurst mit Bürli',800,2,2,1,1),(4,'Pommes',500,2,2,0,2)", ()),
    ])


def order(store, spooler=None, table="12", lines=((1, 1),)):
    r = create_order(store, client_uuid=str(uuid.uuid4()), table_label=table, lines=list(lines))
    if spooler:
        for pid in r.printer_ids:
            spooler.notify(pid)
    return r


async def drain(store, timeout=8.0):
    async def _w():
        while any(p["pending"] or p["printing"] for p in await asyncio.to_thread(queue_status, store)):
            await asyncio.sleep(0.05)
    await asyncio.wait_for(_w(), timeout)


async def wait_leader(spooler, timeout=5.0):
    async def _w():
        while not spooler.is_leader:
            await asyncio.sleep(0.05)
    await asyncio.wait_for(_w(), timeout)


# ---------------------------------------------------------------- Builder / Transport

def test_money():
    assert money(1750) == "17.50" and money(5) == "0.05" and money(0) == "0.00"


def test_bon_columns_and_cut():
    data = Bon(32).columns("2x Bier", "13.00").cut().build()
    assert data.startswith(b"\x1b@\x1bt\x13") and data.endswith(b"\x1dVB\x03")
    (text,) = to_text(data)
    assert len(text) == 32 and text.endswith("13.00")


def test_umlaute_cp858():
    (text,) = to_text(Bon().line("Bürli Käse € é").cut().build())
    assert text == "Bürli Käse € é"


def test_send_raw_offline_raises_fast():
    with pytest.raises(PrinterError):
        run(send_raw(PrinterAddr("127.0.0.1", 1), b"x", connect_timeout=1))


def test_status_query():
    async def t():
        p = await FakePrinter().start()
        st = await query_status(PrinterAddr("127.0.0.1", p.port))
        await p.stop()
        return st
    assert run(t()).ok


# ---------------------------------------------------------------- Bestellung & Routing

def test_routing_and_single_tickets(store):
    async def t():
        bar, kitchen = await FakePrinter().start(), await FakePrinter().start()
        seed(store, bar.port, kitchen.port)
        sp = PrintSpooler(store, leader_poll=0.1)
        await sp.start()
        await wait_leader(sp)
        res = order(store, sp, lines=[(1, 2), (2, 1), (3, 3), (4, 2)])
        await drain(store)
        await sp.stop()
        await bar.stop(); await kitchen.stop()
        return res, bar.bons, kitchen.bons

    res, bar, kitchen = run(t())
    assert res.total_cents == 5100
    # Preise: eine Einstellung für alle Küchen-/Getränkebons; übernommen von den Druckern – hier einer mit
    # und einer ohne Preise: mit (mindestens die Hälfte), so fällt auf dem Getränkebon nichts weg
    assert len(bar) == 1 and "TISCH 12" in bar[0] and "CHF 17.00" in bar[0]
    assert len(kitchen) == 4 and "2x Pommes" in kitchen[0] and "CHF" in kitchen[0]
    assert "1 von 4" in kitchen[0]                                     # Sammelbon zählt mit
    assert [("Bratwurst" in b and f"{n} von 4" in b) for n, b in enumerate(kitchen[1:], 2)] == [True] * 3


def test_idempotent_resubmit(store):
    seed(store, 1, 1)
    a = create_order(store, client_uuid="abc", table_label="5", lines=[(1, 1)])
    b = create_order(store, client_uuid="abc", table_label="5", lines=[(1, 1)])
    assert b.duplicate and a.order_id == b.order_id
    assert store.scalar("SELECT COUNT(*) FROM print_jobs") == 1


def test_invalid_orders(store):
    seed(store, 1, 1)
    for kw in (dict(lines=[]), dict(lines=[(999, 1)]), dict(table_label=" ", lines=[(1, 1)])):
        with pytest.raises(OrderError):
            create_order(store, client_uuid=str(uuid.uuid4()), **{"table_label": "1", **kw})
    assert store.scalar("SELECT COUNT(*) FROM orders") == 0


def test_atomic_rollback(store):
    """Fehler mitten im Batch -> nichts bleibt hängen."""
    seed(store, 1, 1)
    from app.store import StoreError
    with pytest.raises(StoreError):
        store.batch([("INSERT INTO tables(label) VALUES ('A')", ()),
                     ("INSERT INTO tables(label) VALUES ('A')", ())])
    assert store.scalar("SELECT COUNT(*) FROM tables") == 0


# ---------------------------------------------------------------- Ausfallsicherheit (ein Knoten)

def test_printer_offline_then_back(store):
    async def t():
        kitchen = await FakePrinter().start()
        bar = await FakePrinter().start(); bar_port = bar.port; await bar.stop()   # Bar aus
        seed(store, bar_port, kitchen.port)
        sp = PrintSpooler(store, backoff=(0.05, 0.1), leader_poll=0.1)
        await sp.start(); await wait_leader(sp)
        order(store, sp, "1", [(1, 1), (4, 1)])
        order(store, sp, "2", [(1, 1), (4, 1)])
        await asyncio.sleep(0.6)
        st = {p["name"]: p for p in queue_status(store)}
        bar_q = st["Getränkeausgabe"]                    # der erste Bon wird gerade evtl. erneut versucht («printing»)
        assert bar_q["pending"] + bar_q["printing"] == 2 and bar_q["pending"] >= 1 and bar_q["failed"] == 0
        assert store.scalar("SELECT COUNT(*) FROM print_jobs WHERE printer_id=? AND last_error IS NOT NULL", (bar_q["id"],))
        assert st["Küche"]["pending"] == 0 and len(kitchen.bons) == 2
        bar.port = bar_port
        await bar.start()
        await drain(store)
        await sp.stop(); await bar.stop(); await kitchen.stop()
        return bar.bons
    bons = run(t())
    assert len(bons) == 2 and "TISCH 1" in bons[0] and "TISCH 2" in bons[1]


def test_promote_marks_inflight_uncertain_and_reprint(store):
    seed(store, 1, 1)
    order(store)
    store.execute("UPDATE print_jobs SET status='printing', claimed_by='alter-leader'")

    async def t():
        sp = PrintSpooler(store, leader_poll=0.1)
        await sp.start(); await wait_leader(sp); await sp.stop()
    run(t())
    (job,) = list_jobs(store)
    assert job["status"] == "uncertain" and job["label"].startswith("#0001 Tisch 12")
    new_id = reprint(store, job["id"])
    assert new_id and store.scalar("SELECT status FROM print_jobs WHERE id=?", (new_id,)) == "pending"


def test_backup_restore(store):
    seed(store, 1, 1)
    order(store, table="Backup")
    data = store.backup()
    assert data.startswith(b"SQLite format 3\x00")
    store.batch([("DELETE FROM print_jobs", ()), ("DELETE FROM order_items", ()), ("DELETE FROM orders", ()),
                 ("DELETE FROM products", ())])
    assert store.scalar("SELECT COUNT(*) FROM products") == 0
    store.restore(data)
    assert store.scalar("SELECT COUNT(*) FROM products") == 4
    assert store.scalar("SELECT table_label FROM orders") == "Backup"



def test_default_printer(store):
    seed(store, 1, 1)
    store.execute("INSERT INTO products(id,name,price_cents,printer_id) VALUES (9,'Ohne Drucker',300,NULL)")
    r = create_order(store, client_uuid="d1", table_label="1", lines=[(9, 1)])
    assert r.printer_ids == {1} and not r.warnings                     # automatisch: erster Drucker
    store.execute("INSERT INTO settings(key,value) VALUES ('default_printer_id','2') "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    r = create_order(store, client_uuid="d2", table_label="1", lines=[(9, 1)])
    assert r.printer_ids == {2}                                         # gewählter Standarddrucker
    assert store.scalar("SELECT printer_id FROM order_items WHERE order_id=?", (r.order_id,)) == 2
    store.execute("UPDATE settings SET value='99' WHERE key='default_printer_id'")   # gelöschter Drucker
    assert create_order(store, client_uuid="d3", table_label="1", lines=[(9, 1)]).printer_ids == {1}


def test_failover_reroute(store):
    """Küche aus, Einstellung 'umleiten auf Getränkeausgabe': Bons kommen dort mit UMGELEITET-Hinweis."""
    async def t():
        bar = await FakePrinter().start()
        k = await FakePrinter().start(); kport = k.port; await k.stop()
        seed(store, bar.port, kport)
        store.execute("UPDATE printers SET fail_mode='reroute', fail_target=1, fail_after=0 WHERE id=2")
        sp = PrintSpooler(store, backoff=(0.05, 0.1), leader_poll=0.1)
        await sp.start(); await wait_leader(sp)
        order(store, sp, "4", [(4, 2), (3, 1)])            # Pommes (Sammelbon) + Bratwurst (Einzelbon)
        await drain(store)
        assert sp.is_down(2)
        order(store, sp, "5", [(4, 1)])                    # weiterhin gestört: sofort umleiten
        await drain(store)
        await sp.stop(); await bar.stop()
        return bar.bons
    bons = run(t())
    assert len(bons) == 3
    assert all("Umgeleitet - Küche - Drucker gestört" in b for b in bons)
    assert any("TISCH 5" in b for b in bons)


def test_failover_warn_and_wait(store):
    """'Warnung drucken': ein Warnbon auf dem anderen Drucker, die Bons warten und kommen später."""
    async def t():
        bar = await FakePrinter().start()
        k = await FakePrinter().start(); kport = k.port; await k.stop()
        seed(store, bar.port, kport)
        store.execute("UPDATE printers SET fail_mode='warn', fail_target=1, fail_after=0 WHERE id=2")
        sp = PrintSpooler(store, backoff=(0.05, 0.1), leader_poll=0.1)
        await sp.start(); await wait_leader(sp)
        order(store, sp, "7", [(4, 1)])
        order(store, sp, "8", [(4, 1)])
        await asyncio.sleep(0.8)
        warn = [b for b in bar.bons if "ACHTUNG" in b]
        assert len(warn) == 1 and "Küche" in warn[0] and "nicht erreichbar" in warn[0]
        k.port = kport
        await k.start()
        await drain(store)
        await sp.stop(); await bar.stop(); await k.stop()
        return k.bons
    bons = run(t())
    assert len(bons) == 2 and not any("Umgeleitet" in b for b in bons)


def test_bons_have_more_room_and_bold_options():
    """Rückmeldung (Vorschlag B): Zeilenabstand 40 Punkte, Luft nach jeder Position, Optionen und Bemerkung fett –
    nur auf Bons, nicht auf Quittungen."""
    from app.escpos import motion_units
    from app.print_service import BON_LINE_DOTS, ITEM_GAP_DOTS, PrinterCfg, render_table_bon
    p = PrinterCfg(1, "Küche", "x", 9100, 48, True, True)
    data = render_table_bon(p, p, event_name="Fest", currency="CHF", table="12", order_id=7,
                            created_at="2026-11-01T19:34:00",
                            items=[{"qty": 2, "name": "Bratwurst", "price_cents": 800, "category": "Essen",
                                    "options": "ohne Senf", "note": "extra scharf"},
                                   {"qty": 1, "name": "Pommes", "price_cents": 600, "category": "Essen"}])
    # Punkte -> 1/360 Zoll (Bewegungseinheit des TM-T20III): 40 Punkte = 71 Einheiten
    assert motion_units(BON_LINE_DOTS) == 71 and b"\x1b3" + bytes([71]) in data
    assert data.count(b"\x1bJ" + bytes([motion_units(ITEM_GAP_DOTS)])) == 2       # nach jeder Position
    assert b"\x1bE\x01   ohne Senf\n" in data and "\x1bE\x01   » extra scharf\n".encode("cp858") in data
