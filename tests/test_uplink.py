"""Version 2026.10.10-22: Empfang des Mobilfunk-Routers auf der Statusanzeige. Treiber-Aufbau (weitere Modelle
später), MikroTik RouterOS 7 über REST/HTTPS. Ein nachgebauter Router prüft vor allem: Benutzer und Passwort
gehen nur an das übernommene Zertifikat, nie unverschlüsselt, nie an ein anderes Gerät."""
from __future__ import annotations

import asyncio
import json

import pytest

from app import uplink
from tests.fake_router import MONITOR, NOSIM, PASSWORD, USER, FakeRouter
from tests.test_api import AUTH


@pytest.fixture
def router():
    r = FakeRouter()
    yield r
    r.stop()


def _cfg(r: FakeRouter, pin=None, password=PASSWORD, iface="lte1") -> dict:
    return {"kind": "mikrotik", "host": "127.0.0.1", "port": r.port, "user": USER, "password": password,
            "iface": iface, "pin": pin}


# ------------------------------------------------------------------ Werte und Bewertung (ohne Netzwerk)
def test_monitor_values_are_normalized_without_sim_identifiers():
    d = uplink.MikroTik.monitor(MONITOR)
    assert d == {"sim_status": "ok", "registered": True, "status": "registered", "operator": "Swisscom", "tech": "LTE", "rssi": -63,
                 "rsrp": -92, "rsrq": -11.0, "sinr": 14.0, "cqi": 11, "cell": "1234567", "session": 2 * 3600 + 14 * 60 + 9,
                 "band": "B3", "bw": 20, "ca": ["B20"]}
    text = json.dumps(d)
    for secret in (MONITOR["imei"], MONITOR["imsi"], MONITOR["uicc"], MONITOR["subscriber-number"]):
        assert secret not in text                                     # Kennungen von SIM und Modem bleiben weg
    # Einheiten, Unsinn, andere Schreibweisen
    d = uplink.MikroTik.monitor({"rsrp": "-104dBm", "sinr": "-2.5dB", "rssi": "99", "registration-status": "searching",
                                 "access-technology": "5G NSA", "current-operator": "Salt", "roaming": "true",
                                 "primary-band": "n78@100Mhz", "ca-band": ["B3@20Mhz earfcn: 1300", "B7@20Mhz earfcn: 3350"]})
    assert d == {"problem": "searching", "rsrp": -104, "sinr": -2.5, "registered": False, "status": "searching", "tech": "5G NSA",
                 "operator": "Salt", "roaming": True, "band": "n78", "bw": 100, "ca": ["B3", "B7"]}
    assert uplink.MikroTik.monitor({"rsrp": "", "current-operator": "\x1b[31mX", "registration-status": "not registered"}) == \
        {"operator": "[31mX", "registered": False, "status": "not registered"}


@pytest.mark.parametrize("mon,problem", [
    (NOSIM, "no_sim"), ({"pin-status": "sim not inserted"}, "no_sim"),
    ({"pin-status": "PIN required"}, "pin"), ({"pin-status": "PUK required"}, "puk"),
    ({"pin-status": "ok", "functionality": "minimum"}, "radio_off"),
    ({"pin-status": "ok", "functionality": "full", "registration-status": "searching"}, "searching"),
    ({"pin-status": "ok", "registration-status": "registration denied"}, "denied"),
    ({"pin-status": "ok", "functionality": "full", "registration-status": "registered"}, None),
    ({"pin-status": "no password required", "registration-state": "registered"}, None),
])
def test_why_no_network(mon, problem):
    """Ohne SIM, mit PIN-Sperre, Funk aus …: nie «Verbunden, alles gut» (Fehler aus dem Test mit dem LtAP ohne SIM)."""
    d = uplink.MikroTik.monitor(mon)
    assert d.get("problem") == problem
    r = uplink.rate(d)
    if problem:
        assert (r["level"], r["problem"]) == ("bad", problem)
    else:
        assert r["level"] == "ok" and d["registered"] is True


def test_no_values_is_never_ok():
    assert uplink.rate({})["level"] == "bad"                                       # nichts gemeldet: kein Netz
    assert uplink.rate({"registered": True})["level"] == "ok"                      # angemeldet, nur keine Werte
    assert uplink.rate({"registered": True, "link": False}) == {"bars": 0, "level": "bad", "word": "none",
                                                                "problem": "not_registered"}
    r = uplink.rate({"registered": True, "rsrp": -85, "sinr": 20, "link": False})    # Netz gut, aber keine Daten
    assert (r["bars"], r["level"], r["word"], r["problem"]) == (3, "bad", "good", "no_data")
    raw = uplink.raw_view({**MONITOR, ".id": "*1", "ca-band": ["B20@10Mhz", "B1@5Mhz"]})
    assert "imei" not in raw and "imsi" not in raw and "uicc" not in raw and "subscriber-number" not in raw
    assert ".id" not in raw and raw["ca-band"] == "B20@10Mhz, B1@5Mhz" and raw["rsrp"] == "-92"


@pytest.mark.parametrize("vals,bars,level", [
    ({"rsrp": -75, "sinr": 20}, 4, "ok"), ({"rsrp": -85, "sinr": 20}, 3, "ok"), ({"rsrp": -92, "sinr": 14}, 2, "ok"),
    ({"rsrp": -105}, 1, "wait"), ({"rsrp": -118}, 0, "wait"),
    ({"rsrp": -75, "sinr": 3}, 2, "ok"), ({"rsrp": -75, "sinr": -3}, 1, "wait"),     # gut, aber stark gestört
    ({"rssi": -70}, 3, "ok"),                                                         # ohne RSRP (3G): RSSI
    ({"nr_rsrp": -85, "nr_sinr": 20}, 3, "ok"),                                       # nur 5G-Werte
    ({"registered": False, "rsrp": -70}, 0, "bad"),
])
def test_rating_in_bars(vals, bars, level):
    r = uplink.rate(vals)
    assert (r["bars"], r["level"]) == (bars, level)


def test_durations_and_config():
    assert uplink._dur("1w2d3h4m5s") == 7 * 86400 + 2 * 86400 + 3 * 3600 + 4 * 60 + 5
    assert uplink._dur("2d03:04:05") == 2 * 86400 + 3 * 3600 + 4 * 60 + 5
    assert uplink._dur("") is None and uplink._dur("bald") is None
    assert uplink.parse_config(None, "x") is None
    assert uplink.parse_config(json.dumps({"kind": "gibtsnicht", "host": "192.168.55.1"}), "x") is None
    assert uplink.parse_config(json.dumps({"kind": "mikrotik", "host": "-s 1"}), "x") is None       # keine Optionen als Host
    c = uplink.parse_config(json.dumps({"kind": "mikrotik", "host": "192.168.55.1", "port": True, "iface": "lte1;rm",
                                        "user": "a:b", "pin": "kurz"}), "pw")
    assert c == {"kind": "mikrotik", "name": None, "host": "192.168.55.1", "port": 443, "user": "", "password": "pw",
                 "iface": "lte1", "pin": None, "pin_at": None}


def test_drivers_share_one_interface():
    """Neue Modelle: nur eine Klasse wie MikroTik – alles andere (Verbindung, Bewertung, Anzeige) ist gemeinsam."""
    for key, d in uplink.DRIVERS.items():
        assert isinstance(d, uplink.Driver) and d.key == key and d.label and d.default_port
        assert type(d).read is not uplink.Driver.read
        assert "PASSWORT" in d.setup("192.168.55.1")                  # Einrichtung zeigt nie ein echtes Passwort
    assert "address=192.168.55.0/24" in uplink.DRIVERS["mikrotik"].setup("192.168.55.1")


# ------------------------------------------------------------------ Verbindung (nachgebauter Router)
def test_password_only_to_accepted_certificate(router):
    res = asyncio.run(uplink.poll(_cfg(router)))
    assert res == {"state": "cert_new", "pin": router.pin}            # noch nicht übernommen: nur Fingerabdruck
    assert router.heads == [] or not router.authorized                # nichts gesendet
    res = asyncio.run(uplink.poll(_cfg(router, pin=router.pin)))
    assert res["state"] == "ok"
    d = res["data"]
    assert (d["rsrp"], d["operator"], d["band"], d["rx_bytes"], d["model"], d["version"], d["sim"]) == \
        (-92, "Swisscom", "B3", 48211337, "LtAP mini LTE", "7.16.1", "down")
    assert len(router.authorized) == 4                                 # Monitor, Schnittstelle, Router, SIM
    fast = asyncio.run(uplink.poll(_cfg(router, pin=router.pin), slow=False))
    assert "model" not in fast["data"] and fast["data"]["rsrp"] == -92
    # anderes Zertifikat (anderes Gerät unter derselben Adresse): nichts mehr senden
    old, before = router.pin, len(router.authorized)
    router.new_cert()
    res = asyncio.run(uplink.poll(_cfg(router, pin=old)))
    assert res == {"state": "cert", "pin": router.pin} and len(router.authorized) == before
    assert asyncio.run(uplink.fingerprint("127.0.0.1", router.port)) == router.pin


def test_errors_are_named(router):
    pin = router.pin
    assert asyncio.run(uplink.poll(_cfg(router, pin, password="falsch")))["state"] == "auth"
    assert asyncio.run(uplink.poll(_cfg(router, pin, password="")))["state"] == "auth"
    assert asyncio.run(uplink.poll(_cfg(router, pin, iface="lte9")))["state"] == "iface"
    router.mode = "rights"
    assert asyncio.run(uplink.poll(_cfg(router, pin)))["state"] == "rights"
    router.mode = "old"                                                # ohne Zusatzwerte: Empfang trotzdem da
    res = asyncio.run(uplink.poll(_cfg(router, pin)))
    assert res["state"] == "ok" and res["data"]["rsrp"] == -92 and "model" not in res["data"]
    plain = FakeRouter("http")
    try:
        assert asyncio.run(uplink.poll(_cfg(plain, pin)))["state"] == "no_https"
        assert plain.authorized == []                                  # unverschlüsselt: nie Zugangsdaten
    finally:
        plain.stop()
    port = router.port
    router.stop()
    assert asyncio.run(uplink.poll({**_cfg(router, pin), "port": port}, timeout=2))["state"] in ("unreachable", "timeout")
    router.stop = lambda: None                                         # Fixture: schon gestoppt


def test_router_without_sim(router):
    router.mode = "nosim"
    res = asyncio.run(uplink.poll(_cfg(router, pin=router.pin)))
    d = res["data"]
    assert res["state"] == "ok" and d["problem"] == "no_sim" and d["link"] is False and d["sim_status"] == "SIM not inserted"
    assert "imei" not in d["raw"] and d["raw"]["pin-status"] == "SIM not inserted" and d["raw"]["status"] == "radio off"
    tr = uplink.Tracker()
    cfg = {**_cfg(router), "name": None}
    tr.store(cfg, res)
    v, va = tr.view(cfg), tr.view(cfg, admin=True)
    assert v["rating"]["problem"] == "no_sim" and v["rating"]["level"] == "bad"     # nicht «Funk aus»: Ursache ist die SIM
    assert "raw" not in v and "raw" not in v["data"] and va["raw"]["functionality"] == "tx and rx rf circuit disabled"   # Rohdaten nur Verwaltung


def test_tracker_history_and_stale_values():
    cfg = {"kind": "mikrotik", "host": "192.168.55.1", "port": 443, "iface": "lte1", "name": None}
    tr = uplink.Tracker()
    assert tr.view(None) is None and tr.view(cfg)["state"] == "pending"
    data = {**uplink.MikroTik.monitor(MONITOR), "model": "LtAP", "uptime": 100}
    tr.store(cfg, {"state": "ok", "data": data}, now=1000)
    tr.store(cfg, {"state": "ok", "data": {**data, "rsrp": -101}}, now=1010)
    tr.store(cfg, {"state": "timeout"}, now=1020)
    v = tr.view(cfg, now=1025)
    assert v["state"] == "timeout" and v["data"]["rsrp"] == -101 and v["data_seconds"] == 15 and "rating" not in v
    assert v["data"]["model"] == "LtAP" and v["data"]["uptime"] == 115           # seit der letzten Meldung weiter
    assert v["hist"] == [[25, -92, 14.0], [15, -101, 14.0], [5, None, None]]
    tr.store(cfg, {"state": "ok", "data": data}, now=1030)
    assert tr.view(cfg, now=1030)["rating"]["bars"] == 2
    tr.store(cfg, {"state": "cert", "pin": "a" * 64}, now=1040)
    assert "offered_pin" not in tr.view(cfg) and tr.view(cfg, admin=True)["offered_pin"] == "a" * 64
    tr.store({**cfg, "host": "192.168.55.2"}, {"state": "ok", "data": data}, now=1050)   # anderer Router: neu
    assert len(tr.view({**cfg, "host": "192.168.55.2"}, now=1050)["hist"]) == 1
    tr.store({**cfg, "host": "192.168.55.2"}, {"state": "timeout"}, now=1050 + uplink.DATA_MAX_AGE + 1)
    assert "data" not in tr.view({**cfg, "host": "192.168.55.2"})                # zu alt: nicht mehr zeigen


# ------------------------------------------------------------------ Verwaltung und Statusanzeige
def test_admin_setup_check_trust_and_status(client, router):
    from app import core
    r = client.get("/api/admin/uplink", auth=AUTH).json()
    assert r["config"]["kind"] == "" and r["result"] is None and [d["key"] for d in r["drivers"]] == ["mikrotik"]
    body = {"kind": "mikrotik", "name": "Mobilfunk Zelt", "host": "127.0.0.1", "port": router.port, "user": USER,
            "password": PASSWORD, "iface": "lte1"}
    r = client.put("/api/admin/uplink", auth=AUTH, json=body).json()
    assert r["config"]["password_set"] and PASSWORD not in json.dumps(r) and "PASSWORT" in r["setup"]
    s = client.get("/api/admin/settings", auth=AUTH).json()
    assert "uplink" not in s and "uplink_password" not in s and PASSWORD not in json.dumps(s)
    r = client.post("/api/admin/uplink/check", auth=AUTH).json()
    assert r["result"]["state"] == "cert_new" and r["result"]["offered_pin"] == router.pin and not router.authorized
    assert client.post("/api/admin/uplink/trust", auth=AUTH, json={"pin": "b" * 64}).status_code == 409
    r = client.post("/api/admin/uplink/trust", auth=AUTH, json={"pin": router.pin}).json()
    assert r["config"]["pin"] == router.pin and r["result"]["state"] == "ok" and r["result"]["rating"]["bars"] == 2
    st = client.get("/api/status", auth=AUTH).json()["uplink"]
    assert st["name"] == "Mobilfunk Zelt" and st["state"] == "ok" and st["data"]["operator"] == "Swisscom"
    assert "offered_pin" not in st and "password" not in json.dumps(st) and st["hist"]
    # Passwort leer = unverändert; Name ändern behält das Zertifikat, anderer Port nicht
    r = client.put("/api/admin/uplink", auth=AUTH, json={**body, "password": "", "name": "Router"}).json()
    assert r["config"]["pin"] == router.pin and core.rt.store.setting("uplink_password") == PASSWORD
    r = client.put("/api/admin/uplink", auth=AUTH, json={**body, "port": router.port + 1}).json()
    assert r["config"]["pin"] is None
    assert client.put("/api/admin/uplink", auth=AUTH, json={**body, "host": "-x"}).status_code == 422
    assert client.put("/api/admin/uplink", auth=AUTH, json={**body, "kind": "teltonika"}).status_code == 422
    assert client.put("/api/admin/uplink", auth=AUTH, json={**body, "user": "a:b"}).status_code == 422
    # Protokoll: wer was geändert hat, nie das Passwort
    audit = client.get("/api/admin/audit", auth=AUTH).json()["entries"]
    acts = [a["action"] for a in audit]
    assert "uplink" in acts and "uplink_trust" in acts and PASSWORD not in json.dumps(audit)
    # ausschalten: Statusanzeige zeigt keinen Router mehr
    client.put("/api/admin/uplink", auth=AUTH, json={"kind": ""})
    assert client.get("/api/status", auth=AUTH).json()["uplink"] is None
    assert client.post("/api/admin/uplink/check", auth=AUTH).status_code == 422


def test_roles_for_router(client, router):
    from tests.test_users import PW, _hdr
    from app import core
    core.login_throttle.clear()
    for name, role in (("Vera", "view"), ("Lea", "lead")):
        assert client.post("/api/admin/users", auth=AUTH, json={"name": name, "role": role, "password": PW}).status_code == 200
    vera, lea = _hdr(client, "Vera"), _hdr(client, "Lea")
    body = {"kind": "mikrotik", "host": "127.0.0.1", "port": router.port, "user": USER, "password": PASSWORD}
    assert client.put("/api/admin/uplink", headers=lea, json=body).status_code == 403          # ändern: nur Admin
    assert client.put("/api/admin/uplink", auth=AUTH, json=body).status_code == 200
    assert client.get("/api/admin/uplink", headers=vera).status_code == 200                    # ansehen: Anzeige
    assert client.post("/api/admin/uplink/check", headers=vera).status_code == 403
    r = client.post("/api/admin/uplink/check", headers=lea)                                  # prüfen: Festleitung
    assert r.status_code == 200 and r.json()["result"]["state"] == "cert_new"
    assert client.post("/api/admin/uplink/trust", headers=lea, json={"pin": router.pin}).status_code == 403


def test_router_not_in_use(client, router):
    """2026.10.10-27: «Im Einsatz» aus – nicht abgefragt, nicht auf der Statusanzeige; Zugang und Zertifikat bleiben."""
    from app import core, uplink_admin
    from tests.test_users import PW, _hdr
    body = {"kind": "mikrotik", "name": "Mobilfunk", "host": "127.0.0.1", "port": router.port, "user": USER,
            "password": PASSWORD, "iface": "lte1"}
    client.put("/api/admin/uplink", auth=AUTH, json=body)
    client.post("/api/admin/uplink/trust", auth=AUTH, json={"pin": router.pin})
    assert client.get("/api/status", auth=AUTH).json()["uplink"]["state"] == "ok"
    r = client.post("/api/admin/uplink/active", auth=AUTH, json={"active": False}).json()
    assert r["config"]["active"] is False and r["config"]["pin"] == router.pin and r["config"]["password_set"]
    assert client.get("/api/status", auth=AUTH).json()["uplink"] is None               # weg von der Statusanzeige
    assert uplink_admin.config() is None and uplink_admin.config(even_inactive=True)["host"] == "127.0.0.1"
    asked = len(router.heads)
    core.rt.monitor.uplink_now()
    import asyncio
    asyncio.run(core.rt.monitor.poll_uplink(uplink_admin.config()))                     # Hintergrund: fragt nicht
    assert len(router.heads) == asked
    assert client.post("/api/admin/uplink/check", auth=AUTH).json()["result"]["state"] == "ok"   # prüfen geht trotzdem
    r = client.put("/api/admin/uplink", auth=AUTH, json={**body, "password": "", "name": "Router"}).json()
    assert r["config"]["active"] is False                                               # Speichern lässt es aus
    acts = [a for a in client.get("/api/admin/audit", auth=AUTH).json()["entries"] if a["action"] == "uplink_active"]
    assert acts and acts[0]["details"] == {"active": False}
    core.login_throttle.clear()
    client.post("/api/admin/users", auth=AUTH, json={"name": "Lea", "role": "lead", "password": PW})
    assert client.post("/api/admin/uplink/active", headers=_hdr(client, "Lea"), json={"active": True}).status_code == 403
    assert client.post("/api/admin/uplink/active", auth=AUTH, json={"active": True}).json()["config"]["active"] is True
    assert client.get("/api/status", auth=AUTH).json()["uplink"]["name"] == "Router"
    client.put("/api/admin/uplink", auth=AUTH, json={"kind": ""})
    assert client.post("/api/admin/uplink/active", auth=AUTH, json={"active": False}).status_code == 422


def test_router_down_is_red_only_when_needed(client, router):
    """Router ohne Verbindung ist rot (Ampel, Statusanzeige, Handhelds), wenn Karte/TWINT (Payrexx) oder
    die QR-Bestellung an ist oder der Pi dauerhaft über den Router ins Internet geht – sonst nur gelb."""
    body = {"kind": "mikrotik", "name": "Mobilfunk", "host": "127.0.0.1", "port": router.port, "user": USER,
            "password": PASSWORD, "iface": "lte1"}
    client.put("/api/admin/uplink", auth=AUTH, json=body)
    client.post("/api/admin/uplink/trust", auth=AUTH, json={"pin": router.pin})
    assert client.get("/api/admin/lamp", auth=AUTH).json()["level"] == "ok"
    router.values = dict(NOSIM)                                           # Router erreichbar, aber kein Netz
    assert client.post("/api/admin/uplink/check", auth=AUTH).json()["result"]["rating"]["level"] == "bad"
    lamp = client.get("/api/admin/lamp", auth=AUTH).json()
    assert lamp["wait"] == [{"key": "status.uplink.p_down", "name": "Mobilfunk"}] and not lamp["bad"]   # nicht gebraucht
    assert client.get("/api/status", auth=AUTH).json()["uplink_needed"] is False
    assert client.get("/api/phone/status").json()["uplink"] is None
    assert client.put("/api/admin/payrexx", auth=AUTH, json={"enabled": True, "method": "", "show_result": False}).status_code == 200
    lamp = client.get("/api/admin/lamp", auth=AUTH).json()
    assert lamp["level"] == "bad" and lamp["bad"] == [{"key": "status.uplink.p_down", "name": "Mobilfunk"}]
    assert client.get("/api/status", auth=AUTH).json()["uplink_needed"] is True
    assert client.get("/api/phone/status").json()["uplink"] == {"name": "Mobilfunk"}           # auch am Handheld
