"""Verwaltung: Abmelden nach Inaktivität statt fester 12 Stunden.

Das Sitzungs-Token trägt eine Frist ohne Bedienung (Einstellung admin_idle_minutes, 10–120, Standard 30). Danach gilt
es nicht mehr; POST /api/admin/me/refresh gibt nach Bedienung ein neues mit neuer Frist und gleichem Ablauf (12 Stunden
bleiben die Höchstdauer). Rolle Anzeige (Statusanzeige) ohne Frist. Eine abgelaufene Frist zählt nicht als Rateversuch."""
from __future__ import annotations

import time

from tests.test_api import AUTH


def _bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


_REAL_TIME = time.time


def _later(monkeypatch, seconds: float) -> None:
    """Uhr des Servers: so viele Sekunden nach jetzt (nicht nach dem letzten Aufruf)."""
    from app import core
    now = _REAL_TIME()
    monkeypatch.setattr(core.time, "time", lambda: now + seconds)


def test_login_gives_idle_deadline_and_refresh_extends_it(client, monkeypatch):
    from app import core
    core.login_throttle.clear()
    r = client.post("/api/admin/login", json={"password": "geheim"})
    d = r.json()
    assert d["idle_minutes"] == 30 and 30 * 60 - 5 <= d["idle_in"] <= 30 * 60
    assert d["idle_until"] < d["expires"]
    tok = d["token"]
    assert len(tok.split(".")) == 5 and "~" in tok.split(".")[3]      # ältere Pi lesen es weiter (Signatur gleich)
    _later(monkeypatch, 29 * 60)
    assert client.get("/api/admin/settings", headers=_bearer(tok)).status_code == 200
    r = client.post("/api/admin/me/refresh", headers=_bearer(tok))
    assert r.status_code == 200
    new = r.json()
    assert new["expires"] == d["expires"]                              # Höchstdauer bleibt
    assert new["idle_until"] > d["idle_until"] and new["token"] != tok
    _later(monkeypatch, 31 * 60)
    assert client.get("/api/admin/settings", headers=_bearer(tok)).status_code == 401       # altes Token: Frist um
    assert client.get("/api/admin/settings", headers=_bearer(new["token"])).status_code == 200
    _later(monkeypatch, 29 * 60 + 31 * 60 + 60)
    assert client.get("/api/admin/settings", headers=_bearer(new["token"])).status_code == 401
    assert client.post("/api/admin/me/refresh", headers=_bearer(new["token"])).status_code == 401   # zu spät


def test_refresh_never_beyond_twelve_hours(client, monkeypatch):
    from app import core
    tok = client.post("/api/admin/login", json={"password": "geheim"}).json()["token"]
    for step in range(1, 30):                                          # alle 29 Minuten bedient
        _later(monkeypatch, step * 29 * 60)
        r = client.post("/api/admin/me/refresh", headers=_bearer(tok))
        if step * 29 * 60 >= core.SESSION_HOURS * 3600:
            assert r.status_code == 401
            break
        assert r.status_code == 200, step
        tok = r.json()["token"]
        assert r.json()["idle_until"] <= r.json()["expires"]
    else:
        raise AssertionError("nie abgelaufen")


def test_setting_changes_deadline_and_is_checked(client):
    assert client.put("/api/admin/settings", auth=AUTH, json={"admin_idle_minutes": 5}).status_code == 422
    assert client.put("/api/admin/settings", auth=AUTH, json={"admin_idle_minutes": 121}).status_code == 422
    assert client.put("/api/admin/settings", auth=AUTH, json={"admin_idle_minutes": 10}).status_code == 200
    d = client.post("/api/admin/login", json={"password": "geheim"}).json()
    assert d["idle_minutes"] == 10 and d["idle_in"] <= 600


def test_display_role_stays_logged_in(client, monkeypatch):
    r = client.post("/api/admin/users", auth=AUTH, json={"name": "Anzeige1", "role": "view", "password": "anzeige-123"})
    assert r.status_code == 200, r.text
    d = client.post("/api/admin/login", json={"name": "Anzeige1", "password": "anzeige-123"}).json()
    assert d["idle_in"] is None and "~" not in d["token"].split(".")[3]
    _later(monkeypatch, 11 * 3600)
    assert client.get("/api/status", headers=_bearer(d["token"])).status_code == 200


def test_idle_does_not_count_as_guessing(client, monkeypatch):
    from app import core
    core.login_throttle.clear()
    tok = client.post("/api/admin/login", json={"password": "geheim"}).json()["token"]
    _later(monkeypatch, 31 * 60)
    for _ in range(core.LOGIN_MAX_FAILS + 3):
        assert client.get("/api/status", headers=_bearer(tok)).status_code == 401
    monkeypatch.undo()
    assert client.get("/api/admin/settings", auth=AUTH).status_code == 200


def test_forged_deadline_is_rejected(client):
    tok = client.post("/api/admin/login", json={"password": "geheim"}).json()["token"]
    exp, uid, gen, nonce, sig = tok.split(".")
    rnd, _, idle = nonce.rpartition("~")
    longer = f"{exp}.{uid}.{gen}.{rnd}~{int(idle) + 86400}.{sig}"
    assert client.get("/api/admin/settings", headers=_bearer(longer)).status_code == 401
    without = f"{exp}.{uid}.{gen}.{rnd}.{sig}"
    assert client.get("/api/admin/settings", headers=_bearer(without)).status_code == 401


def test_basic_auth_has_nothing_to_refresh(client):
    assert client.post("/api/admin/me/refresh", auth=AUTH).status_code == 409


def test_role_change_ends_open_sessions(client):
    """Anzeige (ohne Frist) wird Festleitung: das alte Token gilt nicht mehr (neue Rechte, neue Frist)."""
    r = client.post("/api/admin/users", auth=AUTH, json={"name": "Wechsel1", "role": "view", "password": "wechsel-123"})
    uid = r.json()["id"]
    tok = client.post("/api/admin/login", json={"name": "Wechsel1", "password": "wechsel-123"}).json()["token"]
    assert client.get("/api/status", headers=_bearer(tok)).status_code == 200
    assert client.put(f"/api/admin/users/{uid}", auth=AUTH, json={"role": "view"}).status_code == 200   # gleiche Rolle
    from app import auth
    auth.forget()
    assert client.get("/api/status", headers=_bearer(tok)).status_code == 200
    assert client.put(f"/api/admin/users/{uid}", auth=AUTH, json={"role": "lead"}).status_code == 200
    auth.forget()
    assert client.get("/api/status", headers=_bearer(tok)).status_code == 401
