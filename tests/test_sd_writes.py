"""T9: Im Leerlauf keine regelmässigen Schreibzugriffe – jeder geht über rqlite auf die SD-Karten aller Pi.
Druckerstand und Pi-Infos holen sich die Pi direkt beim anderen (/api/internal/node, mit CLUSTER_SECRET)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_api import AUTH

ROOT = Path(__file__).resolve().parent.parent
SECRET = "Grüessli-vom-Pi-2026"


def _count_writes(monkeypatch, store) -> list:
    """Jeder Schreibzugriff geht über store.batch (execute ruft batch): pro Aufruf [(SQL, Parameter)]."""
    writes: list = []
    real = store.batch

    def batch(stmts, *a, **kw):
        writes.append([(s[0], tuple(s[1]) if len(s) > 1 else ()) for s in stmts])
        return real(stmts, *a, **kw)
    monkeypatch.setattr(store, "batch", batch)
    return writes


def test_idle_monitor_writes_nothing(client, monkeypatch):
    """Monitor-Durchläufe im Leerlauf (Pi-Infos, Bildschirm, Drucker, DHCP, Zeit …): nach dem ersten Stand nichts mehr –
    auch nicht nach einer Stunde (früher: Pi-Infos und Druckerstand jede Minute)."""
    from app import core
    m = core.rt.monitor
    for _ in range(2):                                      # erster Stand darf geschrieben werden
        asyncio.run(m.check())
    writes = _count_writes(monkeypatch, core.rt.store)
    later = time.time() + 3600
    monkeypatch.setattr(time, "time", lambda: later)
    for _ in range(3):
        asyncio.run(m.check())
    assert writes == []


def test_node_info_only_on_durable_change(client, monkeypatch):
    """Temperatur, Speicher, Laufzeit und der Zeitpunkt der Sicherung (alle 15 min) schreiben nichts; eine neue Version
    oder ein anderes Ergebnis der Sicherung schon – genau einmal."""
    from app import core, system
    base = {**system.local_info(), "temp": 50, "ram_free": 1, "boot": 1000,
            "backup": {"state": "ok", "at": 100, "last_ok": 100, "file": "x.db"}}
    cur = dict(base)
    monkeypatch.setattr(system, "local_info", lambda: dict(cur))
    system._published.update(static=None)
    system.publish()
    writes = _count_writes(monkeypatch, core.rt.store)
    cur.update(temp=61, ram_free=2, boot=1001, disk_free=5, backup={**base["backup"], "at": 1000})
    system.publish()
    mine = f"node_info.{core.Config.node}"
    assert [w for w in writes if mine in w[0][1]] == []              # (der Monitor im Hintergrund schreibt evtl. anderes)
    assert system.nodes()[0]["info"]["temp"] == 61                     # dieser Pi: live
    cur.update(backup={"state": "failed", "at": 1900, "last_ok": 100, "file": "x.db", "code": "write"})
    system.publish()
    system.publish()
    assert len([w for w in writes if mine in w[0][1]]) == 1
    stored = json.loads(core.rt.store.setting(f"node_info.{core.Config.node}"))
    assert stored["backup"]["state"] == "failed"


def test_sweep_reads_first(client, monkeypatch):
    """Aufräumrunde des Spoolers (alle 30 s): ohne hängenden Auftrag kein UPDATE (wäre trotzdem ein Raft-Eintrag)."""
    from app import core, print_service as ps
    from tests.test_api import setup_shop
    setup_shop(client)
    st = core.rt.store
    sp = ps.PrintSpooler(st, "aufraeumen")                  # eigener, nicht laufender Spooler: kein Wettlauf
    writes = _count_writes(monkeypatch, st)
    asyncio.run(sp._sweep_stuck())
    assert writes == []
    pid = client.get("/api/bootstrap").json()["products"][0]["id"]
    client.post("/api/orders", json={"client_uuid": "aufraeumen-1", "table": "1", "lines": [{"product_id": pid, "qty": 1}]})
    job = st.scalar("SELECT id FROM print_jobs ORDER BY id DESC LIMIT 1")
    st.execute("UPDATE print_jobs SET status='printing', claimed_by=? WHERE id=?", (sp.node_id, job))
    writes.clear()
    asyncio.run(sp._sweep_stuck())
    assert len([w for w in writes if "uncertain" in w[0][0]]) == 1         # (der laufende Spooler druckt nebenher)
    assert st.scalar("SELECT status FROM print_jobs WHERE id=?", (job,)) == "uncertain"


def test_internal_node_needs_secret(client, monkeypatch):
    from app import core
    monkeypatch.setattr(core.Config, "cluster_secret", SECRET)
    assert client.get("/api/internal/node").status_code == 403
    assert client.get("/api/internal/node", headers={"X-Festpos-Secret": "falsch"}).status_code == 403
    hdr = {"X-Festpos-Secret": SECRET.encode()}
    monkeypatch.setattr(core.rt, "spooler", SimpleNamespace(is_leader=False))   # druckt nicht: kein Druckerstand
    d = client.get("/api/internal/node", headers=hdr).json()
    assert d["node"] == core.Config.node and "version" in d["info"] and d["printers"] is None
    monkeypatch.setattr(core.rt, "spooler", SimpleNamespace(is_leader=True))    # druckt: Stand aus eigener Abfrage
    monkeypatch.setattr(core.rt.monitor, "printers_here", lambda: {"2": {"online": True, "paper_out": True}})
    assert client.get("/api/internal/node", headers=hdr).json()["printers"] == {"2": {"online": True, "paper_out": True}}


def test_other_pi_live_then_from_database(client, monkeypatch):
    """Anderer Pi: Infos aus seiner Antwort (live). Antwortet er nicht mehr, der letzte Stand aus der Datenbank mit dem
    Alter seit der letzten Antwort."""
    from app import core, system
    monkeypatch.setattr(core.Config, "node", "a")
    info = {"mode": "rqlite", "nodes": {"a": {"leader": True, "reachable": True}, "b": {"leader": False, "reachable": True}}}
    now = time.time()
    core.rt.store.execute("INSERT INTO settings(key,value) VALUES ('node_info.b', ?)",
                          (json.dumps({"version": "alt", "seen": int(now - 5000)}),))
    core.rt.monitor.peers["b"] = {"at": now, "data": {"node": "b", "info": {"version": "neu", "temp": 48}}}
    b = system.nodes(info)[1]
    assert b["info"]["temp"] == 48 and b["info_age"] <= 1
    core.rt.monitor.peers["b"]["at"] = now - 100
    b = system.nodes(info)[1]
    assert b["info"]["version"] == "alt" and 99 <= b["info_age"] <= 102
    core.rt.monitor.peers.pop("b")
    assert system.nodes(info)[1]["info_age"] >= 5000


def test_peers_store_logs_only_changes(caplog):
    from app import monitor as mon
    m = mon.Monitor()
    m._printers_here, m._printers_legacy = (lambda: False), None
    base = "https://10.0.0.2:8443"
    with caplog.at_level(logging.INFO, logger=mon.log.name):
        for _ in range(3):
            m.peers_store({base: "timed out"})
        m.peers_store({base: ({"node": "b", "leader": False}, 0.0, 0.01)})
    msgs = [r.getMessage() for r in caplog.records if base in r.getMessage()]
    assert len(msgs) == 2 and "nicht" in msgs[0] and "wieder" in msgs[1]


def test_leader_printers_fallbacks():
    """Kein Pi meldet «druckt»: der alte Eintrag aus der Datenbank (Update mit altem Pi), sonst unbekannt."""
    from app import monitor as mon
    m = mon.Monitor()
    m._printers_here = lambda: False
    m.peers = {"b": {"at": time.time(), "data": {"node": "b", "leader": False}}}
    m._printers_legacy = lambda: {1: {"paper_out": True}}
    assert m.leader_printers() == {1: {"paper_out": True}}
    m._printers_legacy = lambda: (_ for _ in ()).throw(RuntimeError("keine Mehrheit"))
    assert m.leader_printers() == {}
    m.peers["c"] = {"at": time.time() - 60, "data": {"node": "c", "leader": True, "printers": {"1": {"ok": True}}}}
    assert m.leader_printers() == {}                                  # zu alt


def test_ask_peers_without_secret_asks_nobody():
    from app import timesync
    res = timesync.ask_peers("/api/internal/node", ["https://10.255.255.1:8443"], "", "/x/ca.crt")
    assert list(res) == ["https://10.255.255.1:8443"] and isinstance(res["https://10.255.255.1:8443"], str)


# -- echter Cluster: zwei App-Instanzen über HTTPS -------------------------------------------------------------
def test_cluster_paper_out_on_other_pi_without_database(tmp_path, monkeypatch):
    """Druckender Pi meldet «Papier leer» – der andere zeigt es, ohne dass printer_state in der Datenbank steht; dazu
    die Infos des druckenden Pi live."""
    from tests.conftest import RQLITED, free_port, start_cluster, wait_for
    if not Path(RQLITED).exists():
        pytest.skip("rqlited fehlt")
    httpx = pytest.importorskip("httpx2")
    from tests.fake_printer import FakePrinter
    from tests.tls_util import make_certs
    for k in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(k, ",".join(x for x in (os.environ.get(k), "127.0.0.1,127.0.0.2") if x))
    ca, cert, key = make_certs(tmp_path)
    nodes = start_cluster(tmp_path, 3)
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    printer = asyncio.run_coroutine_threadsafe(FakePrinter().start(), loop).result(10)
    web, apps, clients = free_port(), [], []
    peers = f"https://127.0.0.1:{web},https://127.0.0.2:{web}"
    try:
        for i, nd in enumerate(nodes[:2], start=1):
            env = {**os.environ, "FESTPOS_DB": f"rqlite://127.0.0.1:{nd.http_port}", "FESTPOS_NODE": nd.node_id,
                   "NODE_IP": f"127.0.0.{i}", "FESTPOS_PEERS": peers, "FESTPOS_CA": str(ca), "FESTPOS_CLUSTER_SECRET": SECRET,
                   "FESTPOS_ADMIN_PASSWORD": AUTH[1], "FESTPOS_STATIC": str(ROOT / "static"), "FESTPOS_DATA": str(tmp_path)}
            apps.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", f"127.0.0.{i}",
                                          "--port", str(web), "--ssl-certfile", str(cert), "--ssl-keyfile", str(key)],
                                         cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        ctx = ssl.create_default_context(cafile=str(ca))
        clients += [httpx.Client(base_url=f"https://127.0.0.{i}:{web}", verify=ctx, timeout=10, trust_env=False)
                    for i in (1, 2)]
        a, b = clients
        for c in (a, b):
            wait_for(lambda c=c: c.get("/api/health").json().get("ready"), 60, "App bereit")
        a.post("/api/admin/printers", auth=AUTH, json={"name": "Bar", "host": "127.0.0.1", "port": printer.port})
        printer.paper_out = True

        def paper_out_on_b():
            return any(p.get("paper_out") for p in b.get("/api/status", auth=AUTH).json()["printers"])
        wait_for(paper_out_on_b, 60, "Papier leer auf dem anderen Pi")
        assert nodes[1].store().setting("printer_state", None) is None
        pis = {p["id"]: p for p in b.get("/api/status", auth=AUTH).json()["pis"]}
        assert pis["n1"]["info"].get("version") and pis["n1"]["info_age"] is not None and pis["n1"]["info_age"] < 30
    finally:
        for c in clients:                                     # offene Verbindungen halten uvicorn beim Beenden auf
            c.close()
        for p in apps:
            p.terminate()
        for p in apps:
            try:
                p.wait(20)
            except subprocess.TimeoutExpired:
                p.kill()
                pytest.fail("App beendet sich nicht innert 20 s")
        asyncio.run_coroutine_threadsafe(printer.stop(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        for nd in nodes:
            nd.stop()
