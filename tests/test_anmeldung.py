"""Anmeldung der Verwaltung (Review 2026-10): Sperre gegen Raten pro Name, Platzhalter-Passwort, «Überall abmelden»,
ADMIN_FROM, keine Datenbank-/scrypt-Arbeit im Event-Loop."""
from __future__ import annotations

import asyncio

from tests.test_api import AUTH


def _token(client, name="admin", password="geheim"):
    r = client.post("/api/admin/login", json={"name": name, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _user(client, name="anna", role="view", password="anna-passwort"):
    assert client.post("/api/admin/users", auth=AUTH, json={"name": name, "role": role, "password": password}).status_code == 200
    return _token(client, name, password)


def test_valid_session_does_not_reset_guessing(client):
    """Wer ein eigenes Konto hat, darf zwischen Rateversuchen für «admin» die Zählung nicht auf null setzen."""
    from app import core
    anna = _user(client)
    core.login_throttle.clear()
    codes = []
    for i in range(core.LOGIN_MAX_FAILS + 2):
        codes.append(client.get("/api/admin/stats", auth=("admin", f"falsch{i}")).status_code)
        client.get("/api/admin/me", headers=anna)
    assert codes[:core.LOGIN_MAX_FAILS] == [401] * core.LOGIN_MAX_FAILS and codes[-1] == 429
    core.login_throttle.clear()


def test_own_typos_are_forgiven(client):
    """Eigene Tippfehler zählen nach der richtigen Anmeldung nicht mehr (nur die mit demselben Namen)."""
    from app import core
    core.login_throttle.clear()
    for _ in range(3):
        assert client.post("/api/admin/login", json={"name": "Admin", "password": "falsch"}).status_code == 401
        assert client.post("/api/admin/login", json={"name": "otto", "password": "falsch"}).status_code == 401
    _token(client)
    left = [n for _, n in core.login_throttle._fails["testclient"]]
    assert left == ["otto"] * 3
    core.login_throttle.clear()


def test_placeholder_password_blocks_emergency_login(client, monkeypatch):
    from app import core
    monkeypatch.setattr(core.Config, "admin_password", "bitte-aendern")
    r = client.post("/api/admin/login", json={"name": "admin", "password": "bitte-aendern"})
    assert r.status_code == 401 and "Platzhalter" in r.json()["detail"]
    assert client.get("/api/admin/stats", auth=("admin", "bitte-aendern")).status_code == 401
    monkeypatch.setattr(core.Config, "admin_password", "admin")
    assert client.get("/api/admin/stats", auth=("admin", "admin")).status_code == 401
    core.login_throttle.clear()


def test_warnings_for_admin_password(client, monkeypatch):
    from app import core
    monkeypatch.setattr(core, "PW_WARN_LEN", 16)                        # Regel wie auf dem Pi (Tests: 6, conftest)
    assert "admin_pw_short" in client.get("/api/admin/stats", auth=AUTH).json()["warnings"]     # «geheim»: 6 Zeichen
    assert "admin_pw_short" in client.get("/api/status", auth=AUTH).json()["warnings"]
    monkeypatch.setattr(core.Config, "admin_password", "ein-langes-geheimnis")
    warn = client.get("/api/admin/stats", auth=("admin", "ein-langes-geheimnis")).json()["warnings"]
    assert [w for w in warn if w != "backup_pw_unprinted"] == []       # T8: Sicherungs-Passwort nicht Thema hier
    monkeypatch.setattr(core.Config, "admin_password", "bitte-aendern")
    assert core.security_warnings() == ["admin_pw_placeholder"]


def test_logout_everywhere(client):
    from app import auth
    anna = _user(client)
    other = _token(client, "anna", "anna-passwort")
    assert client.post("/api/admin/me/logout-all", headers=anna).status_code == 200
    auth.forget()
    assert client.get("/api/admin/me", headers=anna).status_code == 401
    assert client.get("/api/admin/me", headers=other).status_code == 401
    assert client.get("/api/admin/me", headers=_token(client, "anna", "anna-passwort")).status_code == 200
    # Notfall-Login: eigener Stand in den Einstellungen
    em = _token(client)
    assert client.post("/api/admin/me/logout-all", headers=em).status_code == 200
    auth.forget()
    assert client.get("/api/admin/me", headers=em).status_code == 401
    assert client.get("/api/admin/me", headers=_token(client)).json()["emergency"] is True
    audit = client.get("/api/admin/audit", auth=AUTH).json()["entries"]
    assert sum(1 for e in audit if e["action"] == "logout_all") == 2


def test_admin_from(client, monkeypatch):
    """ADMIN_FROM: Verwaltung auf dem Verwaltungs-Port nur von dort; Zertifikat-Download und Pi untereinander offen."""
    from app import core
    assert core.ip_ranges("192.168.55.200-192.168.55.254, 10.0.0.0/24 1.2.3.4 kaputt") == [
        (3232249800, 3232249854), (167772160, 167772415), (16909060, 16909060)]
    monkeypatch.setattr(core.Config, "admin_from", "192.168.55.200-192.168.55.254")
    monkeypatch.setattr(core.Config, "peers", ["https://192.168.55.11:8443", "https://192.168.55.12:8443"])
    assert core.admin_allowed_from("192.168.55.201") and core.admin_allowed_from("127.0.0.1")
    assert core.admin_allowed_from("192.168.55.12")                       # anderer Pi
    assert not core.admin_allowed_from("192.168.55.100") and not core.admin_allowed_from(None)
    monkeypatch.setattr(core.Config, "phone_port", 443)                    # Testclient kommt «auf Port 80» = Verwaltung
    r = client.get("/api/admin/stats", auth=AUTH)
    assert r.status_code == 403 and "ADMIN_FROM" in r.json()["detail"]
    assert client.get("/admin.html").status_code == 403
    assert client.get("/api/device-cert/unbekannt").status_code == 404      # durchgelassen (Link ist nur verbraucht)
    assert client.get("/api/health").status_code == 200
    monkeypatch.setattr(core.Config, "admin_from", "")
    assert client.get("/api/admin/stats", auth=AUTH).status_code == 200


def test_auth_check_not_in_event_loop(client, monkeypatch):
    """Die Schranke vor der Verwaltung prüft Benutzer (Datenbank) und Passwort (scrypt) in einem Thread: ein
    hängender Pi im Cluster darf nicht den ganzen Server anhalten."""
    from app import core
    seen = []
    orig = core.check_admin

    def spy(request):
        try:
            asyncio.get_running_loop()
            seen.append("loop")
        except RuntimeError:
            seen.append("thread")
        return orig(request)
    monkeypatch.setattr(core, "check_admin", spy)
    assert client.get("/api/admin/stats", auth=AUTH).status_code == 200
    assert seen == ["thread"]                         # einmal (die Route nimmt den schon geprüften Benutzer)


def test_default_language_not_loaded_in_event_loop():
    from app import i18n
    calls = []

    def loader():
        try:
            asyncio.get_running_loop()
            calls.append("loop")
        except RuntimeError:
            calls.append("thread")
        return "fr"
    old = i18n._loader
    try:
        i18n.set_default_loader(loader)

        async def main():
            first = i18n.default_lang()               # sofort, ohne zu warten
            await asyncio.sleep(0.2)
            return first, i18n.default_lang()
        first, later = asyncio.run(main())
        assert calls == ["thread"] and later == "fr"
        assert i18n.default_lang() == "fr"            # ausserhalb des Event-Loops: wie bisher
    finally:
        i18n.set_default_loader(old)
        i18n._default.update(value=i18n.SOURCE, until=0.0)
