"""Test-Fixtures: jeder Test läuft gegen SQLite UND gegen echtes rqlite (falls rqlited vorhanden).

    RQLITED=/pfad/zu/rqlited pytest -q
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from app.store import RqliteStore, SQLiteStore

RQLITED = os.environ.get("RQLITED") or shutil.which("rqlited") or "/nonexistent/rqlited"   # fehlt: Cluster-Tests aus

# Unabhängig vom Testrechner: kein Ping ins echte Netz, keine Abfrage seiner Dienste (app/monitor.py, host_checks).
# Gilt auch für die Testserver, die die Tests als eigene Prozesse starten (sie erben die Umgebung).
os.environ.setdefault("FESTPOS_HOST_CHECKS", "0")

# Uhrzeit (T2): nie RTC, chrony, Zeit-Stand oder USB-Ergebnisse des Testrechners lesen. Feste Testordner: RTC, beim
# Start daraus gestellt (die Uhr gilt als sicher), Batteriespannung unbekannt (kein Protokolleintrag), chronyc fehlt.
_ZEIT = Path(tempfile.mkdtemp(prefix="festpos-zeit-"))
(_ZEIT / "rtc" / "device").mkdir(parents=True)
(_ZEIT / "rtc" / "hctosys").write_text("1\n")
for _k, _v in (("FESTPOS_RTC_DIR", _ZEIT / "rtc"), ("FESTPOS_CHRONYC", "/nonexistent/chronyc"),
               ("FESTPOS_ZEIT_STAND", _ZEIT / "zeit-stand"), ("FESTPOS_RTC_NEUSTART", _ZEIT / "rtc-neustart"),
               ("FESTPOS_HANDY_USB_DIR", _ZEIT / "handy-usb")):
    os.environ.setdefault(_k, str(_v))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """In der CI (FESTPOS_ALLE_TESTS=1) darf kein Test still wegfallen, nur weil rqlited oder Chromium fehlt."""
    outcome = yield
    rep = outcome.get_result()
    if os.environ.get("FESTPOS_ALLE_TESTS") == "1" and rep.skipped and \
            any(w in str(rep.longrepr) for w in ("rqlited", "Chromium")):
        rep.outcome = "failed"


@pytest.fixture(autouse=True)
def _reset_default_language():
    """Die Standardsprache (Einstellung einer früheren Test-App) darf nicht in den nächsten Test hinüberreichen –
    sonst kämen z.B. Bons eines Tests ohne App auf Rumantsch, nur weil vorher ein Sprachtest lief."""
    yield
    from app import i18n
    i18n.set_default_loader(None)


_used_ports: set[int] = set()


def free_port() -> int:
    """Freier Port, nie zweimal derselbe: Das System gibt einen eben geschlossenen Port gern wieder heraus (bei sechs
    Ports für drei Knoten etwa jedes 700. Mal) – dann startet ein rqlited nicht und der Test wartet vergeblich."""
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in _used_ports:
            _used_ports.add(port)
            return port


class RqliteNode:
    """Startet rqlited direkt (vereinfachte Regel für die Cluster-Tests):
    Daten vorhanden -> ohne Join; leer + Einzelbetrieb/Gründer -> gründen; leer + Cluster -> nur beitreten.
    Das echte Startskript deploy/festpos-rqlited (inkl. gescheitertem Beitritt) prüft test_rqlited_wrapper.py."""

    started: list[RqliteNode] = []          # Knoten dieses Tests: ihre Logs stehen in der Meldung von wait_for

    def __init__(self, node_id: str, data: Path, http_port: int, raft_port: int, join: list[str],
                 cluster_size: int = 3):
        self.node_id, self.data, self.http_port, self.raft_port = node_id, data, http_port, raft_port
        self.join = [j for j in join if not j.endswith(f":{raft_port}")]   # nur die ANDEREN
        self.cluster_size = cluster_size
        self.proc: subprocess.Popen | None = None

    def start(self, found: bool = False):
        args = [RQLITED, "-node-id", self.node_id, "-fk",
                "-http-addr", f"127.0.0.1:{self.http_port}", "-raft-addr", f"127.0.0.1:{self.raft_port}",
                "-raft-election-timeout", "500ms", "-raft-heartbeat-timeout", "500ms"]
        has_state = (self.data / "raft.db").exists()
        if not has_state and self.cluster_size == 3 and not found:
            # 60 s: mindestens so lange, wie die Tests auf den Cluster warten (sonst gibt der Knoten vorher auf)
            args += ["-join", ",".join(self.join), "-join-attempts", "120", "-join-interval", "500ms"]
        args.append(str(self.data))
        self.log = open(str(self.data) + ".log", "ab")
        self.proc = subprocess.Popen(args, stdout=self.log, stderr=subprocess.STDOUT)
        if self not in RqliteNode.started:
            RqliteNode.started.append(self)
        return self

    def log_tail(self, n: int = 15) -> str:
        try:
            lines = Path(str(self.data) + ".log").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return "(kein Log)"
        alive = "läuft" if self.proc and self.proc.poll() is None else f"beendet ({self.proc.returncode if self.proc else '–'})"
        return f"--- {self.node_id} (http {self.http_port}, raft {self.raft_port}, {alive})\n" + "\n".join(lines[-n:])

    def stop(self, hard: bool = True):
        if self.proc and self.proc.poll() is None:
            (self.proc.kill if hard else self.proc.terminate)()
            self.proc.wait(10)

    def store(self) -> RqliteStore:
        return RqliteStore("127.0.0.1", self.http_port)


def wait_for(cond, timeout: float, what: str):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if cond():
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.1)
    logs = "\n".join(nd.log_tail() for nd in RqliteNode.started)
    raise RuntimeError(f"Timeout: {what}" + (f"\n{logs}" if logs else ""))


@pytest.fixture(autouse=True)
def _forget_rqlite_nodes():
    RqliteNode.started.clear()
    yield


def start_cluster(tmp: Path, n: int) -> list[RqliteNode]:
    """Wie in Produktion: A gründet, B und C treten bei."""
    ports = [(free_port(), free_port()) for _ in range(n)]
    join = [f"127.0.0.1:{r}" for _, r in ports]
    size = 1 if n == 1 else 3
    nodes = [RqliteNode(f"n{i+1}", tmp / f"n{i+1}", h, r, join, size) for i, (h, r) in enumerate(ports)]
    nodes[0].start(found=True)
    wait_for(nodes[0].store().is_leader, 15, "Gründer wird Leader")
    for nd in nodes[1:]:
        nd.start()
    if n > 1:
        wait_for(lambda: len(nodes[0].store().cluster_info()["nodes"]) == n and
                 all(x["reachable"] for x in nodes[0].store().cluster_info()["nodes"].values()), 20, "Cluster komplett")
    return nodes


def load_app():
    """App-Module frisch laden (liest die Umgebung neu). Reihenfolge: core zuerst, main zuletzt."""
    import importlib

    from app import (cards, catalog, core, devices, dhcp, display, fully, fully_apk, gast, ledger, mac_change, main,
                     maintenance, payrexx, payrexx_api, plan, bon_design, printer_net, printers, restore_check, tabs, settings_admin, status, status_simple, stock, system, tables, test_mode, time_admin,
                     tunnel, update, update_net, uplink_admin, users, web)
    for mod in (core, devices, dhcp, mac_change, display, fully, fully_apk, uplink_admin, update, update_net, users, printer_net, system, ledger,
                test_mode, status, status_simple, stock, settings_admin, printers, catalog, tables, plan, cards, maintenance, restore_check,
                time_admin, payrexx, payrexx_api, tunnel, web, gast, tabs, bon_design, main):
        importlib.reload(mod)
    return main


@pytest.fixture
def client(tmp_path, monkeypatch):
    """App mit leerer SQLite-Datenbank und Admin-Passwort «geheim» (siehe tests.test_api.AUTH)."""
    from fastapi.testclient import TestClient
    monkeypatch.setenv("FESTPOS_DB", f"sqlite://{tmp_path}/api.db")
    monkeypatch.setenv("FESTPOS_ADMIN_PASSWORD", "geheim")
    monkeypatch.setenv("FESTPOS_STATIC", str(tmp_path / "nostatic"))
    main = load_app()
    with TestClient(main.app) as c:
        yield c


backends = ["sqlite"] + (["rqlite"] if Path(RQLITED).exists() else [])


@pytest.fixture(params=backends)
def store(request, tmp_path):
    if request.param == "sqlite":
        s = SQLiteStore(tmp_path / "t.db")
        s.init_schema()
        yield s
        return
    nodes = start_cluster(tmp_path, 1)
    s = nodes[0].store()
    s.init_schema()
    yield s
    nodes[0].stop()


@pytest.fixture
def cluster(tmp_path):
    if not Path(RQLITED).exists():
        pytest.skip("rqlited nicht gefunden")
    nodes = start_cluster(tmp_path, 3)
    yield nodes
    for nd in nodes:
        nd.stop()
