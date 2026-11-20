"""Version 2026.10.10-24: Ist ein Pi ausgesteckt, darf die Statusanzeige nicht hängen. rqlite wartet bei «/nodes»
ohne Angabe 30 s auf einen Pi, der nicht antwortet – die Statusanzeige gab nach 4 s auf («Server antwortet nicht»),
obwohl Bestellen und Drucken liefen. Jetzt: kurze Zeitgrenze und ein Zwischenstand im Hintergrund."""
from __future__ import annotations

import os
import signal
import time
from pathlib import Path

import pytest

from tests.test_api import AUTH

NODES = {"a": {"id": "a", "reachable": False, "leader": False, "error": "timeout waiting for node to respond"},
         "b": {"id": "b", "reachable": True, "leader": True},
         "c": {"id": "c", "reachable": True, "leader": False}}


def test_unplugged_pi_answers_within_seconds(tmp_path):
    """Echtes rqlite: ein Pi antwortet nicht mehr (angehalten wie ausgesteckt) – die Abfrage dauert nur Sekunden."""
    from tests.conftest import RQLITED, start_cluster, wait_for
    if not Path(RQLITED).exists():
        pytest.skip("rqlited fehlt")
    nodes = start_cluster(tmp_path, 3)
    try:
        os.kill(nodes[0].proc.pid, signal.SIGSTOP)
        wait_for(lambda: nodes[1].store().is_leader() or nodes[2].store().is_leader(), 30, "neuer Leader")
        t = time.monotonic()
        info = nodes[1].store().cluster_info()
        took = time.monotonic() - t
        assert took < 4, took                                       # vorher: 10 s und dann ein Fehler
        assert info["nodes"]["n1"]["reachable"] is False and info["nodes"]["n2"]["reachable"] is True
        os.kill(nodes[0].proc.pid, signal.SIGCONT)
        wait_for(lambda: nodes[1].store().cluster_info()["nodes"]["n1"]["reachable"], 20, "n1 wieder da")
    finally:
        try:
            os.kill(nodes[0].proc.pid, signal.SIGCONT)
        except OSError:
            pass
        for n in nodes:
            n.stop()


def _freeze_background(monkeypatch):
    """Der Monitor erneuert den Zwischenstand alle paar Sekunden im Hintergrund (mit der beim Start gemerkten
    Abfrage) – im Test darf er den eingesetzten Stand nicht überschreiben."""
    from app import core

    async def no_refresh():
        return None
    monkeypatch.setattr(core.rt.monitor, "refresh_cluster", no_refresh)
    monkeypatch.setattr(core.rt.monitor, "_cluster_fn", lambda: (_ for _ in ()).throw(RuntimeError("eingefroren")))
    time.sleep(0.1)                                      # eine gerade laufende Abfrage zu Ende kommen lassen


def test_status_uses_background_state_not_the_database(client, monkeypatch):
    """Statusanzeige und Verwaltung lesen den Zwischenstand des Monitors – auch wenn die Abfrage gerade hängt."""
    from app import core

    def hangs():
        raise AssertionError("Statusanzeige darf nicht auf /nodes warten")

    _freeze_background(monkeypatch)
    monkeypatch.setattr(core.rt.store, "cluster_info", hangs)
    core.rt.monitor.cluster = (time.time(), {"mode": "rqlite", "nodes": NODES})
    t = time.monotonic()
    s = client.get("/api/status", auth=AUTH).json()
    assert time.monotonic() - t < 2
    assert [(n["id"], n["reachable"], n["leader"]) for n in s["cluster"]] == [("a", False, False), ("b", True, True),
                                                                              ("c", True, False)]
    assert [p["id"] for p in s["pis"]] == ["a", "b", "c"]
    assert client.get("/api/admin/cluster", auth=AUTH).json()["nodes"]["a"]["reachable"] is False
    assert [n["id"] for n in client.get("/api/admin/system", auth=AUTH).json()["nodes"]] == ["a", "b", "c"]


def test_stale_background_state_falls_back(client, monkeypatch):
    from app import core, monitor
    calls = []
    _freeze_background(monkeypatch)
    monkeypatch.setattr(core.rt.store, "cluster_info", lambda: calls.append(1) or {"mode": "sqlite"})
    core.rt.monitor.cluster = (time.time() - monitor.CLUSTER_MAX_AGE - 1, {"mode": "rqlite", "nodes": NODES})
    assert client.get("/api/status", auth=AUTH).json()["cluster"] is None and calls      # zu alt: direkt gefragt


def test_monitor_keeps_last_state_when_query_fails():
    import asyncio

    from app.monitor import Monitor
    m = Monitor()
    results = [{"mode": "rqlite", "nodes": NODES}, RuntimeError("rqlite weg")]

    def fn():
        r = results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    m._cluster_fn = fn
    asyncio.run(m.refresh_cluster())
    first = m.cluster
    asyncio.run(m.refresh_cluster())                              # Fehler: alter Stand bleibt
    assert m.cluster == first and m.cluster_cached()["nodes"]["b"]["leader"] is True
    assert m.cluster_cached(max_age=-1) is None
