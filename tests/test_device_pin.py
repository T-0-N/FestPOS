"""T14: PIN pro Gerät – eigene PIN statt der gemeinsamen, nur als gesalzener Hash, jedes Gerät bekommt nur seinen
eigenen Hash, nur Admin, Protokoll ohne PIN, Geräte ohne eigene PIN behalten die gemeinsame."""
from __future__ import annotations

import hashlib

import pytest

from tests.test_api import AUTH, make_device_ca


@pytest.fixture(autouse=True)
def device_ca(client, tmp_path):
    import app.main as main
    crt, key, _ = make_device_ca(tmp_path)
    main.Config.device_ca, main.Config.device_ca_key = str(crt), str(key)


def _cert(client, name):
    r = client.post("/api/admin/devices/cert", auth=AUTH, json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _pin_for(monkeypatch, serial):
    from app import devices, main
    monkeypatch.setattr(devices, "client_cert", lambda request: {"serial": serial} if serial else None)
    return main.pin_config(object())


def test_own_pin_per_device(client, monkeypatch):
    from app import core
    a, b = _cert(client, "XCover 1"), _cert(client, "XCover 2")
    client.put("/api/admin/settings", auth=AUTH, json={"pin": "1111", "pin_enabled": True, "pin_idle_minutes": 5})
    assert client.put("/api/admin/devices/pin", auth=AUTH, json={"device": a["device_id"], "pin": "12"}).status_code == 422
    r = client.put("/api/admin/devices/pin", auth=AUTH, json={"device": a["device_id"], "pin": "2468"})
    assert r.status_code == 200, r.text
    pa, pb = _pin_for(monkeypatch, a["serial"]), _pin_for(monkeypatch, b["serial"])
    assert pa["own"] and pa["length"] == 4 and pa["idle_minutes"] == 5
    assert pa["hash"] == hashlib.sha256((pa["salt"] + "2468").encode()).hexdigest()
    common = core.rt.store.setting("pin_hash")
    assert pa["hash"] != common and pb["hash"] == common and not pb.get("own")      # nur der eigene Hash
    # Gemeinsame PIN aus: das Gerät mit eigener PIN bleibt gesperrt, das andere nicht
    client.put("/api/admin/settings", auth=AUTH, json={"pin_enabled": False})
    assert _pin_for(monkeypatch, a["serial"])["enabled"] and not _pin_for(monkeypatch, b["serial"])["enabled"]
    # Verwaltung: Anzeige nur gesetzt/gemeinsam/keine, nie Hash oder PIN
    d = client.get("/api/admin/devices", auth=AUTH).json()
    assert d["pins"] == [a["device_id"]] and d["pin_common"] is False
    s = client.get("/api/admin/settings", auth=AUTH).json()
    assert not any(k.startswith("device_pin.") for k in s)
    audit = [e for e in client.get("/api/admin/audit", auth=AUTH).json()["entries"] if e["action"] == "device_pin"]
    assert audit and "2468" not in str(audit) and audit[0]["details"]["how"] == "set"
    # entfernen -> wieder gemeinsam
    assert client.put("/api/admin/devices/pin", auth=AUTH, json={"device": a["device_id"]}).status_code == 200
    client.put("/api/admin/settings", auth=AUTH, json={"pin_enabled": True})
    assert _pin_for(monkeypatch, a["serial"])["hash"] == common
    assert client.put("/api/admin/devices/pin", auth=AUTH, json={"device": "gibtsnicht"}).status_code == 404


def test_only_admin_and_removed_with_device(client):
    from app import core
    a = _cert(client, "XCover 3")
    client.post("/api/admin/users", auth=AUTH, json={"name": "Lea", "role": "lead", "password": "Lea-Passwort-1"})
    r = client.put("/api/admin/devices/pin", auth=("Lea", "Lea-Passwort-1"), json={"device": a["device_id"], "pin": "1234"})
    assert r.status_code == 403
    client.put("/api/admin/devices/pin", auth=AUTH, json={"device": a["device_id"], "pin": "1234"})
    lin = client.get("/api/admin/devices", auth=AUTH).json()
    cid = next(c["id"] for c in lin["certs"] if c["serial"] == a["serial"])
    client.post(f"/api/admin/devices/{cid}/revoke", auth=AUTH)
    assert client.post(f"/api/admin/devices/{cid}/remove", auth=AUTH).status_code == 200
    assert not core.rt.store.setting("device_pin." + a["device_id"])
