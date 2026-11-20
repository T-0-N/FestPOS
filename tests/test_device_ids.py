"""T1: Doppelte Gerätenamen. Geräte werden über eine ID erkannt, nie über den Namen: der Name in der Bestell-App darf
doppelt sein (Statusanzeige bleibt stabil), der Gerätename nicht (Server prüft); gesperrte Geräte geben ihren Namen frei.
Altbestand: Migration (device_id, Rollen, Handys) und Markierung gleichnamiger Geräte."""
from __future__ import annotations

import json

import pytest

from tests.test_api import AUTH, make_device_ca


@pytest.fixture
def ca(client, tmp_path, monkeypatch):
    from app import main as m
    crt, key, _ = make_device_ca(tmp_path)
    monkeypatch.setattr(m.Config, "device_ca", str(crt))
    monkeypatch.setattr(m.Config, "device_ca_key", str(key))
    return client


def _store():
    from app import core
    return core.rt.store


def _phones(*items):
    _store().execute("INSERT INTO settings(key,value) VALUES ('monitor_devices', ?) ON CONFLICT(key) DO UPDATE "
                     "SET value=excluded.value", (json.dumps(list(items)),))


def _cert(c, name, **kw):
    r = c.post("/api/admin/devices/cert", auth=AUTH, json={"name": name, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _row_id(c, serial):
    return next(x["id"] for x in c.get("/api/admin/devices", auth=AUTH).json()["certs"] if x["serial"] == serial)


# ------------------------------------------------------------------ Statusanzeige: Name aus der App darf doppelt sein
def test_same_app_name_on_two_phones_gives_two_stable_entries(client):
    from app import core
    m = core.rt.monitor
    for _ in range(3):                                    # abwechselnd melden – früher sprang der eine Eintrag hin und her
        m.seen("Bar", "192.168.55.101", "0", "1", battery="80", serial="a1", cert_name="XCover 1")
        m.seen("Bar", "192.168.55.102", "2", "1", battery="20", serial="b2", cert_name="XCover 2")
        dev = client.get("/api/status", auth=AUTH).json()["devices"]
        assert [(d["name"], d["ip"], d["app_name"], d["battery"], d["outbox"]) for d in dev] == [
            ("XCover 1", "192.168.55.101", "Bar", 80, 0), ("XCover 2", "192.168.55.102", "Bar", 20, 2)]
        assert len({d["id"] for d in dev}) == 2


def test_same_app_name_with_listed_phones_both_online(client):
    from app import core
    _phones({"name": "XCover 1", "kind": "phone", "ip": "192.168.55.101", "port": None},
            {"name": "XCover 2", "kind": "phone", "ip": "192.168.55.102", "port": None})
    for _ in range(3):
        core.rt.monitor.seen("Bar", "192.168.55.101", "0", "1", serial="a1", cert_name="XCover 1")
        core.rt.monitor.seen("Bar", "192.168.55.102", "0", "1", serial="b2", cert_name="XCover 2")
        dev = {d["name"]: d for d in client.get("/api/status", auth=AUTH).json()["devices"]}
        assert dev["XCover 1"]["online"] and dev["XCover 2"]["online"]
        assert dev["XCover 1"]["app_name"] == dev["XCover 2"]["app_name"] == "Bar"
        assert dev["XCover 1"]["id"] != dev["XCover 2"]["id"]


def test_without_certificate_phones_are_told_apart_by_ip(client):
    from app import core
    core.rt.monitor.seen("Bar", "192.168.55.101", "0", "1")
    core.rt.monitor.seen("Bar", "192.168.55.102", "0", "1")
    dev = client.get("/api/status", auth=AUTH).json()["devices"]
    assert sorted(d["ip"] for d in dev) == ["192.168.55.101", "192.168.55.102"]


def test_renewed_certificate_counts_as_the_same_device(ca):
    from app import core
    old = _cert(ca, "Kasse")
    new = _cert(ca, "Kasse", renew=True)
    assert new["device_id"] == old["device_id"] == old["serial"]
    core.rt.monitor.seen("", "192.168.55.101", "0", "1", serial=old["serial"], cert_name="Kasse")
    core.rt.monitor.seen("", "192.168.55.101", "0", "1", serial=new["serial"], cert_name="Kasse")
    from app import devices
    devices._ids_cache = (0.0, {})
    dev = ca.get("/api/status", auth=AUTH).json()["devices"]
    assert [d["name"] for d in dev] == ["Kasse"]
    assert list(ca.get("/api/admin/devices", auth=AUTH).json()["last_seen"]) == [old["device_id"]]


# ------------------------------------------------------------------ Gerätename eindeutig
def test_device_name_must_be_unique_but_revoked_frees_it(ca):
    from app import ledger_base as lb
    first = _cert(ca, "Service 1")
    assert ca.put("/api/admin/devices/role", auth=AUTH, json={"device": first["device_id"], "role": "storno"}).status_code == 200
    r = ca.post("/api/admin/devices/cert", auth=AUTH, json={"name": " service  1 "})
    assert r.status_code == 409 and "Service 1" in r.json()["detail"]
    rid = _row_id(ca, first["serial"])
    assert ca.post(f"/api/admin/devices/{rid}/revoke", auth=AUTH).status_code == 200
    second = _cert(ca, "Service 1")                                     # Ersatzgerät: Name sofort wieder frei
    assert second["device_id"] != first["device_id"]
    assert lb.device_role(_store(), {"serial": second["serial"], "name": "Service 1"}) == "service"   # erbt nichts
    assert lb.device_role(_store(), {"serial": first["serial"], "name": "Service 1"}) == "storno"
    r = ca.post(f"/api/admin/devices/{rid}/revoke?undo=true", auth=AUTH)  # altes entsperren: Name belegt
    assert r.status_code == 409 and "Service 1" in r.json()["detail"]
    # Sperren trifft nur das eigene Gerät, nicht das gleichnamige andere
    assert ca.post(f"/api/admin/devices/{_row_id(ca, second['serial'])}/revoke", auth=AUTH).json()["changed"] == 1
    assert ca.post(f"/api/admin/devices/{rid}/revoke?undo=true", auth=AUTH).status_code == 200   # jetzt frei
    # endgültig löschen nimmt nur die Rolle dieses Geräts weg
    assert ca.post(f"/api/admin/devices/{_row_id(ca, second['serial'])}/remove", auth=AUTH).status_code == 200
    assert lb.device_role(_store(), {"serial": first["serial"], "name": "Service 1"}) == "storno"


def test_rename_by_new_certificate_keeps_device_and_role(ca):
    from app import ledger_base as lb
    a = _cert(ca, "Alt", role="lead")
    _cert(ca, "Belegt")
    assert ca.post("/api/admin/devices/cert", auth=AUTH, json={"name": "Belegt", "renew": True,
                                                                "device": a["device_id"]}).status_code == 409
    b = _cert(ca, "Neu", renew=True, device=a["device_id"])
    assert b["device_id"] == a["device_id"]
    assert lb.device_role(_store(), {"serial": b["serial"], "name": "Neu"}) == "lead"
    assert ca.post("/api/admin/devices/cert", auth=AUTH, json={"name": "X", "device": "unbekannt1"}).status_code == 404


def test_phone_entry_name_checked_against_certificates(ca):
    _cert(ca, "Kasse")
    put = lambda body: ca.put("/api/admin/devices/phone", auth=AUTH, json=body)  # noqa: E731
    assert put({"name": "KASSE", "ip": "192.168.55.110"}).status_code == 409
    r = put({"name": "Bar 1", "ip": "192.168.55.111", "role": "storno"})
    assert r.status_code == 200 and r.json()["id"].startswith("p")
    phone = r.json()["id"]
    assert _cert(ca, "Bar 1", phone=phone)                               # Zertifikat für dieses Handy: kein Konflikt
    stored = [d for d in json.loads(_store().setting("monitor_devices")) if d["id"] == phone][0]
    roles = ca.get("/api/admin/devices", auth=AUTH).json()["roles"]
    assert stored["cert"] in roles and roles[stored["cert"]] == "storno" and "role" not in stored  # Rolle ging mit
    assert put({"id": phone, "name": "Bar 2", "ip": "192.168.55.111"}).status_code == 200     # Name ändern über die ID
    assert put({"id": "p000000000000", "name": "Bar 3", "ip": "192.168.55.112"}).status_code == 404


def test_settings_list_rejects_duplicate_phone_names_and_keeps_ids(client):
    phones = [{"name": "Bar", "kind": "phone", "ip": "192.168.55.101", "port": None},
              {"name": "bar", "kind": "phone", "ip": "192.168.55.102", "port": None}]
    assert client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": phones}).status_code == 409
    assert client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": phones[:1]}).status_code == 200
    pid = json.loads(_store().setting("monitor_devices"))[0]["id"]
    assert client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": phones[:1]}).status_code == 200
    assert json.loads(_store().setting("monitor_devices"))[0]["id"] == pid                   # ID bleibt
    net = [{"name": "Switch", "kind": "net", "ip": "192.168.55.30", "port": None}, {**phones[0], "name": "Switch"}]
    assert client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": net}).status_code == 200  # andere Art


def test_fully_setup_rejects_taken_names(ca):
    _cert(ca, "Kasse")
    from tests.test_devices_admin import PROV
    assert ca.put("/api/admin/settings", auth=AUTH, json={"fully_password": "echt-geheim"}).status_code == 200
    assert ca.put("/api/admin/fully/provision", auth=AUTH, json=PROV).status_code == 200
    assert ca.post("/api/admin/fully/prepare", auth=AUTH, json={"name": "kasse"}).status_code == 409
    r = ca.post("/api/admin/fully/rollout", auth=AUTH, json={"name": "Kasse", "ip": "192.168.55.120", "cert": "Kasse"})
    assert r.status_code == 409
    r = ca.post("/api/admin/fully/rollout", auth=AUTH, json={"name": "Bar", "ip": "192.168.55.120", "role": "storno"})
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ Altbestand: Migration und Konflikt
def _legacy(*rows):
    s = _store()
    for name, serial, until, revoked, replaces in rows:
        s.execute("INSERT INTO device_certs(name, serial, created_at, expires_at, revoked_at, replaces) VALUES (?,?,?,?,?,?)",
                  (name, serial, f"2026-01-0{len(serial)}T10:00:00", until, revoked, replaces))


def _ids():
    return {r["serial"]: r["device_id"] for r in _store().query("SELECT serial, device_id FROM device_certs")}


def test_migration_assigns_device_ids_roles_and_links_phones(client):
    from app import device_ids
    _legacy(("Kellner", "a", "2036-01-01", None, None),            # zwei gleichzeitig gültige: zwei Geräte
            ("Kellner", "bb", "2036-01-01", None, None),
            ("Kasse", "ccc", "2025-01-01", "2025-01-01T10:00:00", None),   # Erneuerung vor «replaces»: ein Gerät
            ("Kasse", "dddd", "2036-01-01", None, None),
            ("Bar", "eeeee", "2036-01-01", "2026-02-01T10:00:00", None),   # Kette über replaces
            ("Bar", "ffffff", "2036-01-01", None, "eeeee"))
    _store().execute("INSERT INTO device_roles(name, role) VALUES ('Kellner', 'storno')")
    _phones({"name": "Kasse", "kind": "phone", "ip": "192.168.55.101", "port": None},
            {"name": "Kellner", "kind": "phone", "ip": "192.168.55.102", "port": None},
            {"name": "Switch", "kind": "net", "ip": "192.168.55.30", "port": None})
    _store().init_schema()                                         # wie beim Start und nach «Sicherung einspielen»
    ids = _ids()
    assert ids["a"] == "a" and ids["bb"] == "bb"
    assert ids["ccc"] == ids["dddd"] == "ccc" and ids["eeeee"] == ids["ffffff"] == "eeeee"
    roles = {r["device_id"]: r["role"] for r in _store().query("SELECT device_id, role FROM device_role_ids")}
    assert roles == {"a": "storno", "bb": "storno"}                # wie vor T1: beide hatten die Rolle über den Namen
    items = json.loads(_store().setting("monitor_devices"))
    kasse, kellner, switch = items
    assert kasse["cert"] == "ccc" and device_ids.PHONE_ID_RE.fullmatch(kasse["id"])
    assert "cert" not in kellner                                    # zwei Kandidaten: nicht raten
    assert "id" not in switch
    assert device_ids.conflicts(_store()) == ["Kellner"]
    assert "device_names_dup" in client.get("/api/admin/stats", auth=AUTH).json()["warnings"]
    before = _store().setting("monitor_devices")
    _store().init_schema()                                         # wiederholbar: nichts ändert sich
    assert _ids() == ids and _store().setting("monitor_devices") == before
    # Sperren eines der beiden «Kellner» trifft nur dieses Gerät; danach kein Konflikt mehr
    rid = _row_id(client, "a")
    assert client.post(f"/api/admin/devices/{rid}/revoke", auth=AUTH).json()["changed"] == 1
    assert "bb" not in {r["serial"] for r in _store().query("SELECT serial FROM device_certs WHERE revoked_at IS NOT NULL")}
    assert device_ids.conflicts(_store()) == []


def test_unique_dhcp_hostnames_for_legacy_duplicates():
    from app import dhcp
    out = dhcp.hosts([{"name": "Bar", "kind": "phone", "ip": "192.168.55.101", "mac": "aa:bb:cc:dd:ee:01"},
                      {"name": "Bar", "kind": "phone", "ip": "192.168.55.102", "mac": "aa:bb:cc:dd:ee:02"}])
    assert ",bar," in out and ",bar-2," in out


# ------------------------------------------------------------------ Rückmeldung zur -57
def test_fixed_ip_is_reserved_with_the_mac_the_phone_really_has(ca, monkeypatch):
    """Feste IP nachträglich eingetragen: die MAC nie über die feste IP holen (die hat evtl. ein anderes Gerät aus dem
    Pool), sondern von der Adresse, von der sich das Handy mit seinem Zertifikat meldet – dann steht die Zuteilung."""
    from app import core, devices, dhcp
    arp = {"192.168.55.110": "aa:aa:aa:aa:aa:aa", "192.168.55.150": "6a:11:22:33:44:55"}   # .110 hat ein anderes Gerät
    monkeypatch.setattr(dhcp, "mac_for_ip", lambda ip: arp.get(ip))
    c = _cert(ca, "Kasse")
    r = ca.put("/api/admin/devices/phone", auth=AUTH, json={"name": "Kasse", "ip": "192.168.55.110", "cert": c["device_id"]})
    assert r.status_code == 200
    phone = json.loads(_store().setting("monitor_devices"))[0]
    assert phone["cert"] == c["device_id"] and "mac" not in phone          # nicht die MAC des fremden Geräts
    devices.learn_macs()                                                  # noch nicht gemeldet: nichts
    assert "mac" not in json.loads(_store().setting("monitor_devices"))[0]
    core.rt.monitor.seen("Anna", "192.168.55.150", "0", "1", serial=c["serial"], cert_name="Kasse")
    devices._ids_cache = (0.0, {})
    assert ca.get("/api/admin/devices", auth=AUTH).json()["ip_now"] == {c["device_id"]: "192.168.55.150"}
    devices.learn_macs()
    items = json.loads(_store().setting("monitor_devices"))
    assert items[0]["mac"] == "6a:11:22:33:44:55"
    assert "6a:11:22:33:44:55,192.168.55.110,kasse," in dhcp.hosts(items)
    # «Fully auf vorhandenem Handy»: die eingetragene IP IST die des Handys (here) – MAC gleich von dort
    assert ca.put("/api/admin/devices/phone", auth=AUTH, json={"name": "Bar", "ip": "192.168.55.150", "here": True}).status_code == 200
    stored = {d["name"]: d for d in json.loads(_store().setting("monitor_devices"))}
    assert "mac" not in stored["Bar"]                                     # MAC gehört schon «Kasse»: nicht doppelt
    arp["192.168.55.151"] = "6a:00:00:00:00:01"
    assert ca.put("/api/admin/devices/phone", auth=AUTH, json={"name": "Theke", "ip": "192.168.55.151", "here": True}).status_code == 200
    assert next(d for d in json.loads(_store().setting("monitor_devices")) if d["name"] == "Theke")["mac"] == "6a:00:00:00:00:01"


def test_blocked_device_with_phone_entry_frees_its_name(ca, monkeypatch):
    """E3 auch mit Handy-Eintrag: gesperrt (nicht gelöscht) – der Name ist frei; ein Ersatz per Werksreset auf derselben
    IP kommt nicht in die alte Kette und erbt die Rolle nicht."""
    from tests.test_devices_admin import PROV
    old = _cert(ca, "XCover 3", role="storno")
    r = ca.put("/api/admin/devices/phone", auth=AUTH, json={"name": "XCover 3", "ip": "192.168.55.103", "cert": old["device_id"]})
    assert r.status_code == 200
    assert ca.post("/api/admin/devices/cert", auth=AUTH, json={"name": "XCover 3"}).status_code == 409
    assert ca.post(f"/api/admin/devices/{_row_id(ca, old['serial'])}/revoke", auth=AUTH).status_code == 200
    assert ca.put("/api/admin/settings", auth=AUTH, json={"fully_password": "echt-geheim"}).status_code == 200
    assert ca.put("/api/admin/fully/provision", auth=AUTH, json=PROV).status_code == 200
    assert ca.post("/api/admin/fully/rollout", auth=AUTH, json={"name": "XCover 3", "ip": "192.168.55.103"}).status_code == 200
    phone = json.loads(_store().setting("monitor_devices"))[0]
    assert "cert" not in phone and "role" not in phone                    # nicht mehr am gesperrten Gerät
    # Liste mit dem Handy des gesperrten Geräts und einem neuen gleichen Namens speichern geht (gesperrtes zählt nicht)
    phone["cert"] = old["device_id"]
    _phones(phone)
    both = [{"id": phone["id"], "name": "XCover 3", "kind": "phone", "ip": "192.168.55.103", "port": None},
            {"name": "XCover 3", "kind": "phone", "ip": "192.168.55.104", "port": None}]
    assert ca.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": both}).status_code == 200
