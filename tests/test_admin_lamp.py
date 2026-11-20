"""In der Verwaltung zeigt der Knopf «Statusanzeige» eine Ampel (live) – dieselben Störungen und Hinweise
zu Druckern und Pi wie die Statusanzeige, als Schlüssel aus der Sprachdatei (GET /api/admin/lamp)."""
from __future__ import annotations

from tests.test_api import AUTH


def _printers(monkeypatch, rows, checks, down=None):
    from app import core, status
    monkeypatch.setattr(status.ps, "queue_status_cached", lambda store: rows)
    monkeypatch.setattr(core.rt.monitor, "printers", checks)
    down = down or {}
    monkeypatch.setattr(core.rt.spooler, "is_down", lambda pid: pid in down)
    monkeypatch.setattr(core.rt.spooler, "down_code", lambda pid: down.get(pid))
    monkeypatch.setattr(status.system_mod, "nodes", lambda info=None: [{"id": "a", "reachable": True}])


def _row(pid, name, enabled=True, failed=0):
    return {"id": pid, "name": name, "enabled": enabled, "failed": failed, "uncertain": 0, "pending": 0, "printing": 0}


def test_lamp_levels(client, monkeypatch):
    from app import status
    _printers(monkeypatch, [_row(1, "Küche"), _row(2, "Bar")], {1: {"online": True}, 2: {"online": True}})
    assert client.get("/api/admin/lamp", auth=AUTH).json() == {"level": "ok", "bad": [], "wait": [], "notice": None}   # T27: notice
    # Papier fast leer: Hinweis (gelb), wie in der Statusanzeige
    _printers(monkeypatch, [_row(1, "Küche"), _row(2, "Bar")], {1: {"online": True, "paper_near_end": True}, 2: {"online": True}})
    d = status.lamp()
    assert d["level"] == "wait" and d["wait"] == [{"key": "status.prn.w_paper_low", "name": "Küche"}]
    # gestört geht vor: Papier leer (vom Spooler) und nicht erreichbar; ausgeschaltete Drucker zählen nicht
    _printers(monkeypatch, [_row(1, "Küche"), _row(2, "Bar"), _row(3, "Alt", enabled=False)],
              {1: {"online": True}, 2: {"online": False}, 3: {"online": False}}, down={1: "paper_out"})
    d = status.lamp()
    assert d["level"] == "bad"
    assert d["bad"] == [{"key": "status.prn.p_paper", "name": "Küche"}, {"key": "status.prn.p_down", "name": "Bar"}]
    # Bons nicht gedruckt: prüfen; Pi nicht erreichbar: Störung
    _printers(monkeypatch, [_row(1, "Küche", failed=2)], {1: {"online": True}})
    monkeypatch.setattr(status.system_mod, "nodes", lambda info=None: [{"id": "a", "reachable": True}, {"id": "b", "reachable": False}])
    d = status.lamp()
    assert d["wait"] == [{"key": "status.prn.w_check", "name": "Küche"}]
    assert d["level"] == "bad" and d["bad"] == [{"key": "status.cluster.p_unreachable", "pi": "b"}]


def test_lamp_for_every_role(client):
    r = client.post("/api/admin/users", auth=AUTH, json={"name": "Vera", "role": "view", "password": "Vera-passwort"})
    assert r.status_code == 200, r.text
    assert client.get("/api/admin/lamp", auth=("Vera", "Vera-passwort")).status_code == 200
    assert client.get("/api/admin/lamp").status_code == 401
