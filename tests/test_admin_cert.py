"""Rückmeldung Punkt 3: «Verwaltung nur mit Gerätezertifikat». Der Verwaltungs-Port fragt nach dem Zertifikat
(app/serve.py, freiwillig auf TLS-Ebene); eingeschaltet weist die App /api/admin/… ohne gültiges Zertifikat ab –
auch die Anmeldung. Notweg: der Pi selbst (localhost). Einschalten nur mit Zertifikat (kein Aussperren)."""
from __future__ import annotations

from types import SimpleNamespace

from tests.test_api import AUTH

CERT = {"X-T-Cert": "PC Kasse"}


def _fake_certs(monkeypatch):
    from app import core, devices
    monkeypatch.setattr(core.Config, "phone_port", 443)                # Testclient kommt «auf Port 80» = Verwaltung

    def cert(request):
        name = request.headers.get("x-t-cert")
        return {"name": name, "serial": "s-" + name.replace(" ", ""), "days_left": 300} if name else None
    monkeypatch.setattr(devices, "client_cert", cert)
    core.forget_cert_only()


def test_off_by_default_and_switch_on_only_with_certificate(client, monkeypatch):
    from app import core
    _fake_certs(monkeypatch)
    assert client.get("/api/admin/stats", auth=AUTH).status_code == 200            # aus: wie bisher, nur Passwort
    me = client.get("/api/admin/me", auth=AUTH).json()
    assert me["cert"] is None
    r = client.put("/api/admin/settings", auth=AUTH, json={"admin_cert_only": True})
    assert r.status_code == 409 and "Gerätezertifikat" in r.json()["detail"]       # ohne Zertifikat: nicht einschalten
    assert client.get("/api/admin/me", auth=AUTH, headers=CERT).json()["cert"] == {"name": "PC Kasse", "ok": True}
    assert client.put("/api/admin/settings", auth=AUTH, headers=CERT, json={"admin_cert_only": True}).status_code == 200
    # jetzt: ohne Zertifikat nichts mehr, auch keine Anmeldung
    r = client.get("/api/admin/stats", auth=AUTH)
    assert r.status_code == 403 and "Gerätezertifikat" in r.json()["detail"]
    assert client.post("/api/admin/login", json={"name": "admin", "password": "geheim"}).status_code == 403
    assert client.get("/api/admin/stats", auth=AUTH, headers=CERT).status_code == 200
    assert client.post("/api/admin/login", headers=CERT, json={"name": "admin", "password": "geheim"}).status_code == 200
    assert client.get("/api/status", auth=AUTH).status_code == 200                 # Statusanzeige ist keine Verwaltung
    assert client.get("/api/health").status_code == 200
    # gesperrtes Zertifikat zählt nicht
    core.rt.store.execute("INSERT INTO device_certs(name, serial, created_at, revoked_at) VALUES (?,?,?,?)",
                          ("PC Kasse", "s-PCKasse", "2026-01-01", "2026-11-15T10:00:00"))
    from app import devices
    devices._revoked_cache = (0.0, set())
    assert client.get("/api/admin/stats", auth=AUTH, headers=CERT).status_code == 403
    assert client.get("/api/admin/stats", auth=AUTH, headers={"X-T-Cert": "XCover 3"}).status_code == 200
    # ausschalten geht (mit Zertifikat erreichbar)
    assert client.put("/api/admin/settings", auth=AUTH, headers={"X-T-Cert": "XCover 3"},
                      json={"admin_cert_only": False}).status_code == 200
    assert client.get("/api/admin/stats", auth=AUTH).status_code == 200


def test_pi_itself_and_phone_port_need_no_extra_certificate(client, monkeypatch):
    from app import core
    _fake_certs(monkeypatch)
    core.rt.store.execute("INSERT INTO settings(key, value) VALUES ('admin_cert_only', '1') "
                          "ON CONFLICT(key) DO UPDATE SET value='1'")
    core.forget_cert_only()

    def req(host, port, headers=None):
        return SimpleNamespace(client=SimpleNamespace(host=host), scope={"server": ("192.168.55.10", port)},
                               headers=headers or {})
    assert core.admin_cert_needed(req("192.168.55.200", 8443)) is True
    assert core.admin_cert_needed(req("127.0.0.1", 8443)) is False                 # Notweg: am Pi / SSH-Tunnel
    assert core.admin_cert_needed(req("192.168.55.101", 443)) is True              # Handy-Port: auch dort prüfen …
    assert core.admin_cert_needed(req("192.168.55.101", 443, {"x-t-cert": "XCover 1"})) is False
    core.rt.store.execute("INSERT INTO device_certs(name, serial, created_at, revoked_at) VALUES (?,?,?,?)",
                          ("XCover 1", "s-XCover1", "2026-01-01", "2026-11-15T10:00:00"))
    from app import devices
    devices._revoked_cache = (0.0, set())
    assert core.admin_cert_needed(req("192.168.55.101", 443, {"x-t-cert": "XCover 1"})) is True   # … gesperrt: nein
    assert core.admin_cert_needed(req("192.168.55.200", 8443, {"x-t-cert": "PC"})) is False
    monkeypatch.setattr(core.Config, "phone_port", 0)                               # Entwicklung ohne Zertifikate
    assert core.admin_cert_needed(req("192.168.55.200", 8443)) is False
