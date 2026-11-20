"""T15: Drucker, der die Statusabfrage früher beantwortet hat und jetzt schweigt (Epson mit Werkseinstellung «Zustand
BUSY = Offline oder Empfangspuffer voll» bei leerem Papier): nicht blind senden, Bons warten und kommen in der richtigen
Reihenfolge, sobald er wieder antwortet. Drucker, die nie geantwortet haben, werden wie bisher bedient."""
from __future__ import annotations

import asyncio

import pytest

from app.escpos import PrinterAddr, PrinterError, send_raw
from app.monitor import Monitor
from app.print_service import PrintSpooler, mark_status_seen, queue_status
from tests.fake_printer import FakePrinter
from tests.test_printing import drain, order, run, seed, wait_leader


def test_send_raw_require_status():
    async def t():
        pr = await FakePrinter().start()
        addr = PrinterAddr("127.0.0.1", pr.port)
        assert await send_raw(addr, b"A\n") is True                 # antwortet: True
        pr.silent = True
        with pytest.raises(PrinterError) as e:
            await send_raw(addr, b"B\n", status_timeout=0.3, require_status=True)
        assert e.value.code == "no_status" and not e.value.sent
        assert await send_raw(addr, b"C\n", status_timeout=0.3) is False   # nie geantwortet: wie bisher senden
        await asyncio.sleep(0.1)
        await pr.stop()
        return pr
    pr = run(t())
    assert [r.endswith(b"A\n") or r.endswith(b"C\n") for r in pr.received] == [True, True]
    assert not any(r.endswith(b"B\n") for r in pr.received)


def test_silent_after_answer_waits_and_prints_in_order(store):
    async def t():
        bar = await FakePrinter().start()
        kitchen = await FakePrinter().start()
        seed(store, bar.port, kitchen.port)
        sp = PrintSpooler(store, backoff=(0.05, 0.1), leader_poll=0.05)
        await sp.start(); await wait_leader(sp)
        order(store, sp, "1", [(4, 1)])                             # Küche druckt und antwortet -> gemerkt
        await drain(store)
        for _ in range(200):                                        # gemerkt wird kurz NACH «gedruckt» (drain)
            if store.scalar("SELECT status_seen FROM printers WHERE id=2") == 1:
                break
            await asyncio.sleep(0.01)
        assert store.scalar("SELECT status_seen FROM printers WHERE id=2") == 1
        assert store.scalar("SELECT status_seen FROM printers WHERE id=1") == 0      # (hat noch nichts gedruckt)
        kitchen.silent = True                                       # Papier leer, Werkseinstellung: schweigt
        order(store, sp, "2", [(4, 1)])
        order(store, sp, "3", [(4, 2)])
        for _ in range(200):
            job = store.query_one("SELECT status, error_code FROM print_jobs WHERE id=2")
            if job["status"] == "pending" and job["error_code"] == "no_status" and sp.is_down(2):
                break                                               # «gestört» setzt der Spooler kurz nach dem Job
            await asyncio.sleep(0.01)
        assert job == {"status": "pending", "error_code": "no_status"}
        assert sp.is_down(2) and sp.down_code(2) == "no_status"
        assert len(kitchen.bons) == 1
        kitchen.silent = False                                      # Papier eingelegt
        await drain(store)
        await sp.stop(); await bar.stop(); await kitchen.stop()
        return kitchen
    kitchen = run(t())
    assert len(kitchen.bons) == 3 and kitchen.buffered == []
    assert ["TISCH 1" in kitchen.bons[0], "TISCH 2" in kitchen.bons[1], "TISCH 3" in kitchen.bons[2]] == [True] * 3


def test_never_answered_printer_still_prints(store):
    async def t():
        bar = await FakePrinter().start()
        kitchen = await FakePrinter().start()
        kitchen.silent = True                                       # Modell ohne Echtzeit-Status
        seed(store, bar.port, kitchen.port)
        sp = PrintSpooler(store, backoff=(0.05, 0.1), leader_poll=0.05)
        await sp.start(); await wait_leader(sp)
        order(store, sp, "5", [(4, 1)])
        await drain(store)
        await sp.stop(); await bar.stop(); await kitchen.stop()
        return kitchen
    kitchen = run(t())
    assert len(kitchen.bons) == 1
    assert store.scalar("SELECT status_seen FROM printers WHERE id=2") == 0


def test_monitor_marks_and_reports(store):
    async def t():
        pr = await FakePrinter().start()
        p = {"id": 1, "host": "127.0.0.1", "port": pr.port, "enabled": True, "busy": False, "status_seen": False}
        r1 = await Monitor._printer(p)
        pr.silent = True
        r2 = await Monitor._printer(p)
        r3 = await Monitor._printer({**p, "status_seen": True})
        busy = await Monitor._printer({**p, "status_seen": True, "busy": True})
        await pr.stop()
        return r1, r2, r3, busy
    r1, r2, r3, busy = run(t())
    assert r1["answered"] and r1["ok"] and not r1["no_status"]
    assert not r2["answered"] and not r2["no_status"]               # nie geantwortet: kein Alarm
    assert r3["no_status"] and not r3["ok"]                         # hat geantwortet, schweigt jetzt: rot
    assert busy == {}                                               # druckt gerade: nicht abfragen (kein Fehlalarm)


def test_mark_status_seen_writes_once(store):
    seed(store, 9100, 9101)
    mark_status_seen(store, 1)
    assert {r["id"]: r["status_seen"] for r in queue_status(store)} == {1: 1, 2: 0}
    before = store.scalar("SELECT COUNT(*) FROM printers WHERE status_seen=1")
    mark_status_seen(store, 1)                                      # zweites Mal: kein Treffer (T9)
    assert store.scalar("SELECT COUNT(*) FROM printers WHERE status_seen=1") == before
