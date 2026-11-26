"""Härtetest 3-Knoten-Cluster: Leader fällt aus, Druck geht weiter, kein Bon doppelt.

Auf jedem Knoten läuft ein eigener PrintSpooler gegen sein lokales rqlited – genau wie auf den Pis.
"""
import asyncio
import re
import shutil
import time
import uuid
from collections import Counter

from app.orders import create_order
from app.print_service import PrintSpooler, queue_status
from app.store import RqliteStore, StoreError
from tests.fake_printer import FakePrinter


def bon_key(text: str) -> tuple:
    """Eindeutige Identität eines Bons aus dem gedruckten Text."""
    station = text.split("\n")[1].strip()
    order_no = re.search(r"#(\d{4})", text).group(1)
    part = re.search(r"(\d+)/(\d+)", text.split("\n")[5] if "TISCH" not in text else "")
    return station, order_no, part.group(0) if part else "sammel"


def seed(store, p1, p2):
    store.init_schema()
    store.batch([
        ("INSERT INTO printers(id,name,host,port) VALUES (1,'Bar','127.0.0.1',?)", (p1,)),
        ("INSERT INTO printers(id,name,host,port) VALUES (2,'Küche','127.0.0.1',?)", (p2,)),
        ("INSERT INTO products(id,name,price_cents,printer_id,single_ticket) VALUES "
         "(1,'Bier',650,1,0),(2,'Wurst',800,2,1)", ()),
    ])


def place(stores, n, start=0):
    """n Bestellungen, reihum über verschiedene Knoten (wie Handys an der VIP nach Failover)."""
    done = 0
    for i in range(start, start + n):
        for s in stores[i % len(stores):] + stores[:i % len(stores)]:
            try:
                create_order(s, client_uuid=str(uuid.uuid4()), table_label=str(i + 1), lines=[(1, 1), (2, 2)])
                done += 1
                break
            except StoreError:
                continue
    return done


async def settle(store, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            q = await asyncio.to_thread(queue_status, store)
            if not any(p["pending"] or p["printing"] for p in q):
                return
        except StoreError:
            pass
        await asyncio.sleep(0.1)
    raise TimeoutError(f"Queue leert sich nicht: {q}")


def test_failover_no_duplicates(cluster, tmp_path):
    async def t():
        bar, kitchen = await FakePrinter().start(), await FakePrinter().start()
        stores = [n.store() for n in cluster]
        seed(stores[0], bar.port, kitchen.port)
        spoolers = [PrintSpooler(s, node_id=n.node_id, backoff=(0.05,), leader_poll=0.1)
                    for n, s in zip(cluster, stores)]
        for sp in spoolers:
            await sp.start()
        await asyncio.sleep(0.5)
        assert sum(sp.is_leader for sp in spoolers) == 1, "genau ein druckender Knoten"

        # Phase 1: Normalbetrieb
        await asyncio.to_thread(place, stores, 10)
        for sp in spoolers:                       # Handys treffen per VIP den Leader -> notify dort
            for pid in (1, 2):
                sp.notify(pid)
        await settle(stores[0])

        # Phase 2: weitere Bestellungen, Leader stirbt HART mitten im Drucken
        leader_i = next(i for i, sp in enumerate(spoolers) if sp.is_leader)
        await asyncio.to_thread(place, stores, 10, 10)
        for pid in (1, 2):
            spoolers[leader_i].notify(pid)
        await asyncio.sleep(0.05)
        t_kill = time.time()
        cluster[leader_i].stop(hard=True)
        survivors = [i for i in range(3) if i != leader_i]

        # Neuer Leader muss übernehmen; Handys bestellen über die Überlebenden weiter
        while not any(spoolers[i].is_leader for i in survivors):
            await asyncio.sleep(0.05)
        failover_s = time.time() - t_kill
        n3 = await asyncio.to_thread(place, [stores[i] for i in survivors], 10, 20)
        for i in survivors:
            for pid in (1, 2):
                spoolers[i].notify(pid)
        await settle(stores[survivors[0]])

        # Phase 3: ausgefallener Knoten kommt mit LEERER SD-Karte zurück (Kartentausch)
        dead = cluster[leader_i]
        shutil.rmtree(dead.data)
        dead.start()
        rejoined = None
        local = RqliteStore("127.0.0.1", dead.http_port, read_level="none")   # NUR lokale Daten lesen
        t0 = time.time()
        while time.time() - t0 < 20:
            try:
                c = local.query("SELECT COUNT(*) AS c FROM orders")[0]["c"]
                if c == 30:
                    rejoined = time.time() - t0
                    break
            except StoreError:
                pass
            await asyncio.sleep(0.2)

        for sp in spoolers:
            await sp.stop()
        await bar.stop(); await kitchen.stop()
        s = stores[survivors[0]]
        stats = {r["status"]: r["c"] for r in s.query("SELECT status, COUNT(*) c FROM print_jobs GROUP BY status")}
        return bar.bons + kitchen.bons, stats, failover_s, n3, rejoined

    bons, stats, failover_s, n3, rejoined = asyncio.run(t())
    keys = Counter(bon_key(b) for b in bons)
    dups = {k: c for k, c in keys.items() if c > 1}
    print(f"\nFailover: {failover_s:.2f}s | Bons: {len(bons)} | Jobs: {stats} | "
          f"Bestellungen nach Ausfall: {n3}/10 | Rejoin leere Karte: {rejoined and f'{rejoined:.1f}s'}")

    assert n3 == 10, "nach Failover muss Bestellen weiter funktionieren"
    assert not dups, f"Doppelt gedruckt: {dups}"
    total_jobs = 30 * 3
    assert sum(stats.values()) == total_jobs
    assert stats.get("done", 0) + stats.get("uncertain", 0) == total_jobs
    assert len(bons) >= stats.get("done", 0)          # jeder 'done' Job liegt auf Papier
    assert len(bons) <= total_jobs
    assert rejoined is not None, "Knoten mit leerer Karte hat sich nicht synchronisiert"


def test_grow_from_single_pi(tmp_path):
    """Start mit EINEM Pi (Einzelbetrieb), später B und C dazu – ohne Datenverlust, ohne Split-Brain."""
    from pathlib import Path

    import pytest as _pt

    from tests.conftest import RQLITED, RqliteNode, free_port, wait_for
    if not Path(RQLITED).exists():
        _pt.skip("rqlited fehlt")
    ports = [(free_port(), free_port()) for _ in range(3)]
    join = [f"127.0.0.1:{r}" for _, r in ports]
    a = RqliteNode("a", tmp_path / "a", *ports[0], join, cluster_size=1).start()
    b = RqliteNode("b", tmp_path / "b", *ports[1], join, cluster_size=3)
    c = RqliteNode("c", tmp_path / "c", *ports[2], join, cluster_size=3)
    try:
        s = a.store()
        wait_for(s.is_leader, 10, "A Leader")
        s.init_schema()
        s.execute("INSERT INTO tables(label) VALUES ('Einzelbetrieb')")

        # CLUSTER_SIZE=3: A wird neu gestartet (hat Daten -> ohne Join), B und C (leer) treten bei.
        # Absichtlich OHNE zu warten – das war der Fall, der vorher Split-Brain erzeugte.
        a.stop(hard=False)
        a.cluster_size = 3
        a.start(); b.start(); c.start()
        wait_for(lambda: len(a.store().cluster_info()["nodes"]) == 3 and
                 all(n["reachable"] for n in a.store().cluster_info()["nodes"].values()), 30, "3 Knoten")
        leaders = {n["id"] for nd in (a, b, c) for n in nd.store().cluster_info()["nodes"].values() if n["leader"]}
        assert len(leaders) == 1, f"Split-Brain! Leader: {leaders}"
        for nd in (b, c):        # Daten aus dem Einzelbetrieb sind repliziert
            local = RqliteStore("127.0.0.1", nd.http_port, read_level="none")
            wait_for(lambda local=local: local.scalar("SELECT COUNT(*) FROM tables") == 1, 10, f"Daten auf {nd.node_id}")
        # und A darf jetzt ausfallen
        a.stop(hard=True)
        wait_for(lambda: b.store().execute("INSERT INTO tables(label) VALUES ('nach Ausfall A')"), 10,
                 "Schreiben ohne A")
        # A kommt mit LEERER Karte zurück (Kartentausch beim Gründer!) -> tritt bei, gründet nicht neu
        shutil.rmtree(a.data)
        a.start()
        local_a = RqliteStore("127.0.0.1", a.http_port, read_level="none")

        def a_synced():
            # Wie systemd (rqlited.service: Restart=always): Schickt der Leader seine Daten, bevor der Beitritt fertig
            # ist, beendet sich rqlited («clustering failure») – nach dem Neustart läuft A mit diesen Daten weiter.
            if a.proc.poll() is not None:
                a.start()
            return local_a.scalar("SELECT COUNT(*) FROM tables") == 2
        wait_for(a_synced, 20, "A synchronisiert")
    finally:
        for nd in (a, b, c):
            nd.stop()
