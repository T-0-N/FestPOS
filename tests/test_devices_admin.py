"""T5: Seite «Geräte» – was die Festleitung darf (hinzufügen, Name/IP, Rolle, «Im Einsatz», sperren, Fully-Aktionen)
und was beim Admin bleibt (entsperren, löschen, Einstellungen › Geräte); ein Handy-Eintrag ändern; WLAN-QR."""
from __future__ import annotations

import json

import pytest

from tests.test_api import AUTH
from tests.test_users import team  # noqa: F401  (Fixture)

PROV = {"apk_url": "https://example.org/fully.apk", "checksum": "h7lwsUsSv8aWEju9W6YPFR3ZgE5AH8VkXY9z407d-n0",
        "ssid": "FestPOS", "security": "WPA", "apk_local": "0", "wifi_password": "geheim;12:3"}


def _certs(*rows):
    from app import core
    for name, serial, until, revoked in rows:
        core.rt.store.execute("INSERT INTO device_certs(name, serial, created_at, expires_at, revoked_at) VALUES (?,?,?,?,?)",
                              (name, serial, "2026-09-01T10:00:00", until, revoked))


def _phones():
    from app import core
    return [d for d in json.loads(core.rt.store.setting("monitor_devices") or "[]") if d["kind"] == "phone"]


@pytest.fixture
def lead(client, team):  # noqa: F811
    return team["Lea"]


def test_lead_may_manage_devices_but_not_unlock_delete_or_settings(client, team):  # noqa: F811
    l_, a = team["Lea"], team["Ada"]
    _certs(("Service 1", "S1", "2036-01-01", None), ("Alt", "A1", "2020-01-01", None))
    d = client.get("/api/admin/devices", headers=l_)
    assert d.status_code == 200 and {c["name"] for c in d.json()["certs"]} == {"Service 1", "Alt"}
    assert "last_seen" in d.json()
    sid = next(c["id"] for c in d.json()["certs"] if c["name"] == "Service 1")
    old = next(c["id"] for c in d.json()["certs"] if c["name"] == "Alt")
    assert client.put("/api/admin/devices/role", headers=l_, json={"name": "Service 1", "role": "lead"}).status_code == 200
    assert client.post(f"/api/admin/devices/{sid}/revoke", headers=l_).status_code == 200
    assert client.post(f"/api/admin/devices/{sid}/revoke?undo=true", headers=l_).status_code == 403   # entsperren: Admin
    assert client.post(f"/api/admin/devices/{sid}/revoke?undo=true", headers=a).status_code == 200
    assert client.delete(f"/api/admin/devices/{old}", headers=l_).status_code == 403                   # löschen: Admin
    assert client.delete(f"/api/admin/devices/{old}", headers=a).status_code == 200
    r = client.post("/api/admin/devices/cert", headers=l_, json={"name": "Ersatz"})
    assert r.status_code != 403                                     # erlaubt (im Test fehlt nur die Geräte-CA)
    # Einstellungen › Geräte bleiben beim Admin
    assert client.put("/api/admin/fully/provision", headers=l_, json=PROV).status_code == 403
    assert client.put("/api/admin/fully/template/pin", headers=l_, json={"pin": "1234"}).status_code == 403
    assert client.post("/api/admin/fully/forget", headers=l_, json={"ip": "192.168.55.101"}).status_code == 403
    assert client.put("/api/admin/settings", headers=l_, json={"cert_days": 365}).status_code == 403
    assert client.get("/api/admin/fully/provision", headers=l_).status_code == 200                  # lesen: ob bereit
    log = [(e["action"], e["actor"]) for e in client.get("/api/admin/audit", auth=AUTH).json()["entries"]]
    assert ("device_revoke", "Lea") in log and ("device_unrevoke", "Ada") in log and ("device_role", "Lea") in log


def test_phone_entry_add_change_and_keep_fully_and_mac(client, lead):
    from app import core
    core.rt.store.execute("INSERT INTO settings(key,value) VALUES ('monitor_devices', ?)", (json.dumps([
        {"name": "Switch", "kind": "net", "ip": "192.168.55.30", "port": None, "active": True},
        {"name": "Service 1", "kind": "phone", "ip": "192.168.55.101", "port": None, "active": True,
         "fully_pin": "a" * 64, "fully_until": "2036-01-01", "mac": "aa:bb:cc:dd:ee:01"}]),))
    put = lambda body: client.put("/api/admin/devices/phone", headers=lead, json=body)  # noqa: E731
    assert put({"name": "Service 2", "ip": "192.168.55.102"}).status_code == 200                       # neu
    assert put({"name": "service 2", "ip": "192.168.55.109"}).status_code == 409                       # Name doppelt
    assert put({"name": "Service 3", "ip": "192.168.55.30"}).status_code == 409                        # IP vom Switch
    assert put({"name": "Service 3", "ip": "kaputt adresse"}).status_code == 422
    assert put({"old": "Gibt es nicht", "name": "X", "ip": "192.168.55.120"}).status_code == 404
    # «Im Einsatz» aus: Fully-Zertifikat und MAC bleiben
    assert put({"old": "Service 1", "name": "Service 1", "ip": "192.168.55.101", "active": False}).status_code == 200
    s1 = next(d for d in _phones() if d["name"] == "Service 1")
    assert s1["active"] is False and s1["fully_pin"] == "a" * 64 and s1["mac"] == "aa:bb:cc:dd:ee:01"
    # neue IP: Fully-Zertifikat gilt nicht mehr (gehört zur alten Adresse), die MAC bleibt für DHCP
    assert put({"old": "Service 1", "name": "Service 1", "ip": "192.168.55.111"}).status_code == 200
    s1 = next(d for d in _phones() if d["name"] == "Service 1")
    assert "fully_pin" not in s1 and s1["mac"] == "aa:bb:cc:dd:ee:01" and s1["ip"] == "192.168.55.111"
    stored = json.loads(core.rt.store.setting("monitor_devices"))
    assert {"name": "Switch", "kind": "net", "ip": "192.168.55.30", "port": None, "active": True} in stored
    assert [d["name"] for d in _phones()] == ["Service 2", "Service 1"]
    assert any(e["action"] == "device_phone" for e in client.get("/api/admin/audit", auth=AUTH).json()["entries"])


def test_wifi_qr_only_as_picture(client, lead):
    r = client.get("/api/admin/devices/wifi-qr", headers=lead)
    assert r.status_code == 200 and r.json()["svg"] is None                    # noch kein WLAN eingestellt
    assert client.put("/api/admin/fully/provision", auth=AUTH, json=PROV).status_code == 200
    r = client.get("/api/admin/devices/wifi-qr", headers=lead)
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["ssid"] == "FestPOS" and body["security"] == "WPA" and body["svg"].startswith("<svg")
    assert "geheim" not in r.text                                               # Passwort nur im Bild


def test_wifi_text_escapes_special_characters(client, monkeypatch):
    from app import qr
    seen = []
    monkeypatch.setattr(qr, "qr_svg", lambda text: seen.append(text) or "<svg/>")
    assert client.put("/api/admin/fully/provision", auth=AUTH, json={**PROV, "ssid": "Fest;POS"}).status_code == 200
    assert client.get("/api/admin/devices/wifi-qr", auth=AUTH).status_code == 200
    assert seen == [r"WIFI:T:WPA;S:Fest\;POS;P:geheim\;12\:3;;"]


def test_prepare_gives_a_one_time_password_bound_to_the_phone_ip(client, lead):
    from app import fully
    assert client.post("/api/admin/fully/prepare", headers=lead, json={"name": "Kasse 2"}).status_code == 422   # ohne Fully-Passwort
    assert client.put("/api/admin/settings", auth=AUTH, json={"fully_password": "echt-geheim"}).status_code == 200
    r = client.post("/api/admin/fully/prepare", headers=lead, json={"name": "Kasse 2"})
    assert r.status_code == 200
    token, temp = r.json()["id"], r.json()["temp_pw"]
    assert len(temp) == 8 and temp != "echt-geheim"
    assert fully.rollout_pw("192.168.55.120") is None
    fully._bind_prepared(token, "192.168.55.120")
    assert fully.rollout_pw("192.168.55.120") == temp                 # bootstrap nimmt es statt des echten
    fully._bind_prepared("x" * 32, "192.168.55.121")                  # unbekannter Code: nichts
    assert fully.rollout_pw("192.168.55.121") is None


def test_remove_blocked_device_keeps_the_block(client, team):  # noqa: F811
    from app import core
    l_, a = team["Lea"], team["Ada"]
    _certs(("Service 9", "S9", "2036-01-01", None))
    sid = next(c["id"] for c in client.get("/api/admin/devices", headers=a).json()["certs"] if c["name"] == "Service 9")
    assert client.post(f"/api/admin/devices/{sid}/remove", headers=a).status_code == 409              # noch gültig
    assert client.post(f"/api/admin/devices/{sid}/revoke", headers=l_).status_code == 200
    assert client.post(f"/api/admin/devices/{sid}/remove", headers=l_).status_code == 403             # nur Admin
    assert client.post(f"/api/admin/devices/{sid}/remove", headers=a).status_code == 200
    assert client.post(f"/api/admin/devices/{sid}/remove", headers=a).status_code == 404
    assert "Service 9" not in {c["name"] for c in client.get("/api/admin/devices", headers=a).json()["certs"]}
    row = core.rt.store.query_one("SELECT revoked_at, removed_at FROM device_certs WHERE serial='S9'")
    assert row["revoked_at"] and row["removed_at"]                                                    # Sperre bleibt
    assert client.post(f"/api/admin/devices/{sid}/revoke?undo=true", headers=a).status_code == 404
    log = [(e["action"], e["actor"]) for e in client.get("/api/admin/audit", auth=AUTH).json()["entries"]]
    assert ("device_remove", "Ada") in log


def test_empty_ip_takes_the_phone_off_the_status_display(client, lead):
    from app import core
    core.rt.store.execute("INSERT INTO settings(key,value) VALUES ('monitor_devices', ?)", (json.dumps([
        {"name": "Service 1", "kind": "phone", "ip": "192.168.55.101", "port": None, "active": True}]),))
    put = lambda body: client.put("/api/admin/devices/phone", headers=lead, json=body)  # noqa: E731
    assert put({"old": "Service 1", "name": "Service 1", "ip": ""}).status_code == 200
    assert _phones() == []
    assert put({"name": "Service 2", "ip": ""}).status_code == 422                                    # neu ohne IP: nein
