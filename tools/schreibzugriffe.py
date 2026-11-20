#!/usr/bin/env python3
"""Schreibzugriffe im Cluster zählen (T9) – in der Entwicklungsumgebung, ohne Pi.

Startet 3 echte rqlited und 3 App-Instanzen über HTTPS (127.0.0.1–3, wie die Pi mit CLUSTER_SECRET und eigener CA),
einen simulierten Drucker und zählt die Raft-Einträge: jeder Eintrag wird auf JEDER Karte geschrieben.

    python3 tools/schreibzugriffe.py [MINUTEN]      Standard 10: je so lange Leerlauf und Betrieb (1 Bestellung/min)

Ausgabe pro Stunde: Raft-Einträge, davon Wechsel von node_info.*, printer_state, display_status.*, Bestellungen
und Druckaufträge; write_bytes pro rqlited. Dazu die Probe «Papier leer»: alle drei Pi müssen es anzeigen.
Braucht rqlited im PATH (tools/setup-dev.sh). Läuft nicht in der CI (dauert).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.conftest import RQLITED, RqliteNode, free_port, wait_for  # noqa: E402  (setzt auch die Testumgebung)
from tests.fake_printer import FakePrinter  # noqa: E402
from tests.tls_util import make_certs  # noqa: E402

import httpx2 as httpx  # noqa: E402

NODES = ("a", "b", "c")
PASSWORD = "geheim"
AUTH = ("admin", PASSWORD)
KEYS = "key='printer_state' OR key LIKE 'node_info.%' OR key LIKE 'display_status.%'"


class Printer:
    """Simulierter Drucker in einem eigenen Thread."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.fake = asyncio.run_coroutine_threadsafe(FakePrinter().start(), self.loop).result(10)


def rq(port: int, path: str, **q):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}?{urllib.parse.urlencode(q)}", timeout=5) as r:
        return json.load(r)


def raft_index(port: int) -> int:
    return int(rq(port, "/status")["store"]["raft"]["last_log_index"])


def query(port: int, sql: str) -> list:
    return rq(port, "/db/query", level="none", q=sql)["results"][0].get("values") or []


def wbytes(pid: int) -> int:
    with open(f"/proc/{pid}/io") as f:
        return next(int(x.split()[1]) for x in f if x.startswith("write_bytes"))


class Counter(threading.Thread):
    """Alle 2 s die regelmässig geschriebenen Einstellungen lesen (lokal, kein Raft-Eintrag) und Wechsel zählen."""

    def __init__(self, port: int):
        super().__init__(daemon=True)
        self.port, self.values, self.changes, self.stop = port, {}, {}, threading.Event()

    def run(self):
        while not self.stop.wait(2):
            try:
                rows = query(self.port, f"SELECT key, value FROM settings WHERE {KEYS}")
            except OSError:
                continue
            for k, v in rows:
                group = k.split(".")[0]
                if k in self.values and self.values[k] != v:
                    self.changes[group] = self.changes.get(group, 0) + 1
                self.values[k] = v


def phase(title: str, minutes: float, rq_nodes, counter: Counter, client: httpx.Client | None, pids) -> dict:
    port = rq_nodes[0].http_port
    i0, w0 = raft_index(port), [wbytes(p) for p in pids]
    o0, j0 = (query(port, f"SELECT count(*) FROM {t}")[0][0] for t in ("orders", "print_jobs"))
    counter.changes = {}
    t0, orders = time.time(), 0
    while time.time() - t0 < minutes * 60:
        if client:                                                      # Betrieb: 1 Bestellung pro Minute
            boot = client.get("/api/bootstrap").json()
            pid = boot["products"][0]["id"]
            r = client.post("/api/orders", json={"client_uuid": str(uuid.uuid4()), "table": "7", "device": "Messung",
                                                 "lines": [{"product_id": pid, "qty": 1}]})
            orders += r.status_code == 200
        time.sleep(min(60, max(0, minutes * 60 - (time.time() - t0))))
    h = (time.time() - t0) / 3600
    o1, j1 = (query(port, f"SELECT count(*) FROM {t}")[0][0] for t in ("orders", "print_jobs"))
    entries = raft_index(port) - i0
    kb = [(wbytes(p) - w) / 1024 for p, w in zip(pids, w0)]
    known = sum(counter.changes.values())
    res = {"phase": title, "entries_h": entries / h, "node_info_h": counter.changes.get("node_info", 0) / h,
           "printer_state_h": counter.changes.get("printer_state", 0) / h,
           "display_status_h": counter.changes.get("display_status", 0) / h, "rest_h": (entries - known) / h,
           "orders": o1 - o0, "jobs": j1 - j0, "kb_h": sum(kb) / len(kb) / h,
           "kb_per_entry": (sum(kb) / len(kb) / entries) if entries else 0.0}
    print(f"\n{title} ({minutes:g} min, hochgerechnet pro Stunde)")
    print(f"  Raft-Einträge          {res['entries_h']:7.0f}")
    print(f"    node_info.*          {res['node_info_h']:7.0f}")
    print(f"    printer_state        {res['printer_state_h']:7.0f}")
    print(f"    display_status.*     {res['display_status_h']:7.0f}")
    print(f"    übrige               {res['rest_h']:7.0f}   (Aufräumrunde, Bestellungen, Druck)")
    print(f"  Bestellungen / Druckaufträge in der Phase: {res['orders']} / {res['jobs']}")
    print(f"  rqlited write_bytes    {res['kb_h'] / 1024:7.2f} MB pro Pi ({res['kb_per_entry']:.0f} KB pro Eintrag)")
    sys.stdout.flush()
    return res


def paper_check(printer: Printer, clients: dict[str, httpx.Client]) -> bool:
    """Papier leer am Drucker: zeigen es alle drei Pi in der Statusanzeige? Danach wieder voll."""
    def shown(c):
        return any(p.get("paper_out") for p in c.get("/api/status", auth=AUTH).json()["printers"])
    printer.fake.paper_out = True
    try:
        wait_for(lambda: all(shown(c) for c in clients.values()), 60, "Papier leer auf allen Pi")
        ok = True
    except RuntimeError:
        ok = False
    print(f"\nProbe «Papier leer» auf allen Pi: {'ok' if ok else 'FEHLT'} – " +
          ", ".join(f"{n}: {'ja' if shown(c) else 'nein'}" for n, c in clients.items()))
    printer.fake.paper_out = False
    return ok


def main() -> int:
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 10
    if not Path(RQLITED).exists():
        print("rqlited fehlt (tools/setup-dev.sh)")
        return 2
    for k in ("NO_PROXY", "no_proxy"):                  # die «Pi» nie über einen Proxy (auch nicht die Apps)
        os.environ[k] = ",".join(x for x in (os.environ.get(k), "127.0.0.1,127.0.0.2,127.0.0.3") if x)
    tmp = Path(tempfile.mkdtemp(prefix="festpos-schreib-"))
    ca, cert, key = make_certs(tmp)
    ports = [(free_port(), free_port()) for _ in NODES]
    join = [f"127.0.0.1:{r}" for _, r in ports]
    rq_nodes = [RqliteNode(n, tmp / n, h, r, join) for n, (h, r) in zip(NODES, ports)]
    apps: list[subprocess.Popen] = []
    try:
        rq_nodes[0].start(found=True)
        wait_for(rq_nodes[0].store().is_leader, 15, "Gründer wird Leader")
        for nd in rq_nodes[1:]:
            nd.start()
        wait_for(lambda: all(x["reachable"] for x in rq_nodes[0].store().cluster_info()["nodes"].values()) and
                 len(rq_nodes[0].store().cluster_info()["nodes"]) == 3, 30, "Cluster komplett")
        web = free_port()
        peers = ",".join(f"https://127.0.0.{i}:{web}" for i in (1, 2, 3))
        for i, (n, nd) in enumerate(zip(NODES, rq_nodes), start=1):
            env = {**os.environ, "FESTPOS_DB": f"rqlite://127.0.0.1:{nd.http_port}", "FESTPOS_NODE": n,
                   "NODE_IP": f"127.0.0.{i}", "FESTPOS_PEERS": peers, "FESTPOS_CA": str(ca),
                   "FESTPOS_CLUSTER_SECRET": "messung-gemeinsam", "FESTPOS_ADMIN_PASSWORD": PASSWORD,
                   "FESTPOS_STATIC": str(ROOT / "static"), "FESTPOS_DATA": str(tmp), "FESTPOS_TLS_KEY": str(key)}
            apps.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", f"127.0.0.{i}",
                                          "--port", str(web), "--ssl-certfile", str(cert), "--ssl-keyfile", str(key),
                                          "--log-level", "warning"], cwd=ROOT, env=env,
                                         stdout=open(tmp / f"app-{n}.log", "wb"), stderr=subprocess.STDOUT))
        clients = {n: httpx.Client(base_url=f"https://127.0.0.{i}:{web}", verify=str(ca), timeout=10, trust_env=False)
                   for i, n in enumerate(NODES, start=1)}
        for c in clients.values():
            wait_for(lambda c=c: c.get("/api/health").json().get("ready"), 60, "App bereit")
        printer = Printer()
        a = clients["a"]
        for nm in ("Getränkeausgabe", "Küche"):
            a.post("/api/admin/printers", auth=AUTH, json={"name": nm, "host": "127.0.0.1", "port": printer.fake.port})
        csv = "name;preis;kategorie;drucker\r\nBier 5dl;6.50;Getränke;Getränkeausgabe\r\n".encode()
        a.post("/api/admin/import/products?commit=true", auth=AUTH, files={"file": ("p.csv", csv)}).raise_for_status()
        a.post("/api/admin/tables", auth=AUTH, json={"range_from": 1, "range_to": 20})
        settle = min(120.0, minutes * 60)
        print(f"Cluster bereit ({tmp}); {settle:.0f} s einschwingen …")
        sys.stdout.flush()
        time.sleep(settle)
        counter = Counter(rq_nodes[0].http_port)
        counter.start()
        time.sleep(3)
        pids = [nd.proc.pid for nd in rq_nodes]
        phase("Leerlauf", minutes, rq_nodes, counter, None, pids)
        phase("Betrieb", minutes, rq_nodes, counter, a, pids)
        ok = paper_check(printer, clients)
        return 0 if ok else 1
    finally:
        for p in apps:
            p.terminate()
        for p in apps:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        for nd in rq_nodes:
            nd.stop()


if __name__ == "__main__":
    sys.exit(main())
