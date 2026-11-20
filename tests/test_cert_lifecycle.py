"""T3: Lebenszyklus der Gerätezertifikate – Sperren wirkt an einer Stelle für alle Routen der App und sofort im Cluster,
gesperrte Geräte (auch mit fester IP) stehen unter «Gesperrt», Handys mit Fully erneuert der Pi selbst."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import time

from tests.test_api import AUTH, make_device_ca


def _ca(monkeypatch, tmp_path):
    from app import core
    crt, key, _ = make_device_ca(tmp_path)
    monkeypatch.setattr(core.Config, "device_ca", str(crt))
    monkeypatch.setattr(core.Config, "device_ca_key", str(key))


def _as_phone(monkeypatch, serial, name="Kellner 1"):
    from app import devices
    info = {"serial": serial, "name": name, "days_left": 3000, "expires_at": "2036-01-01"}
    monkeypatch.setattr(devices, "client_cert", lambda request: dict(info))


def _cert(client, name="Kellner 1", **extra) -> dict:
    r = client.post("/api/admin/devices/cert", auth=AUTH, json={"name": name, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _row(serial: str) -> dict:
    from app import core
    return core.rt.store.query_one("SELECT * FROM device_certs WHERE serial=?", (serial,))


# ------------------------------------------------------------------ Punkt 1: Sperren wirkt überall und sofort
def test_revoked_cert_is_refused_on_every_app_route(client, monkeypatch, tmp_path):
    """Jede Route der App (auch eine künftige) weist ein gesperrtes Zertifikat ab – ausser dem Zustand des Zertifikats
    und was eigenen Schutz hat (devices.REVOKED_OPEN)."""
    from fastapi.routing import APIRoute
    from app import devices, main
    _ca(monkeypatch, tmp_path)
    c = _cert(client)
    did = client.get("/api/admin/devices", auth=AUTH).json()["certs"][0]["id"]
    assert client.post(f"/api/admin/devices/{did}/revoke", auth=AUTH).status_code == 200
    _as_phone(monkeypatch, c["serial"])
    def walk(routes):                                       # eingebundene Router (FastAPI fasst sie zusammen)
        for r in routes:
            if isinstance(r, APIRoute):
                yield r
            elif hasattr(r, "original_router"):
                yield from walk(r.original_router.routes)
    checked = 0
    for route in walk(main.app.routes):
        if not route.path.startswith("/api/") or route.path.startswith(devices.REVOKED_OPEN):
            continue
        path = route.path
        for p in route.param_convertors:
            path = path.replace("{" + p + "}", "1")
        for method in route.methods - {"HEAD"}:
            r = client.request(method, path, json={})
            assert r.status_code == 403, (method, path, r.status_code)
            assert "gesperrt" in r.json()["detail"].lower(), (method, path)
            checked += 1
    assert checked >= 12                                   # Bestellen, Zahlen, Storno, Zeit, Statuszeile …
    st = client.get("/api/device/cert").json()             # die App erfährt, dass sie gesperrt ist
    assert st["revoked"] is True and st["name"] == "Kellner 1"


def test_revoked_phone_is_not_seen_as_online(client, monkeypatch, tmp_path):
    from app import core
    _ca(monkeypatch, tmp_path)
    c = _cert(client)
    did = client.get("/api/admin/devices", auth=AUTH).json()["certs"][0]["id"]
    client.post(f"/api/admin/devices/{did}/revoke", auth=AUTH)
    _as_phone(monkeypatch, c["serial"])
    assert client.get("/api/phone/status", headers={"X-Festpos-Device": "Anna"}).status_code == 403
    assert not [d for d in core.rt.monitor.device_list() if d.get("serial") == c["serial"]]


def test_revoke_tells_the_other_pi_at_once(client, monkeypatch, tmp_path):
    """Sperren: dieser Pi liest die Sperrliste sofort neu, die anderen Pi bekommen Bescheid (CLUSTER_SECRET, nur an ein
    geprüftes Zertifikat) – die 15 s Zwischenspeicher sind nur noch der Rückfall."""
    import ssl
    import urllib.request
    from app import core, devices
    _ca(monkeypatch, tmp_path)
    sent: list[tuple[str, str]] = []

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0, context=None):
        sent.append((req.full_url, req.get_header("X-festpos-secret")))
        return Answer()
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ssl, "create_default_context", lambda **kw: None)
    monkeypatch.setattr(core.Config, "peers", ["https://192.168.55.11", "https://192.168.55.12"])
    monkeypatch.setattr(core.Config, "node_ip", "192.168.55.11")
    monkeypatch.setattr(core.Config, "cluster_secret", "ein-langes-gemeinsames-geheimnis")
    monkeypatch.setattr(core.Config, "cafile", str(tmp_path / "ca.crt"))
    c = _cert(client)
    assert c["serial"] not in devices.revoked_serials()     # Sperrliste jetzt im Zwischenspeicher
    did = client.get("/api/admin/devices", auth=AUTH).json()["certs"][0]["id"]
    client.post(f"/api/admin/devices/{did}/revoke", auth=AUTH)
    assert c["serial"] in devices.revoked_serials()         # hier sofort
    end = time.time() + 5
    while not sent and time.time() < end:
        time.sleep(0.05)
    assert sent == [("https://192.168.55.12/api/internal/revoked", "ein-langes-gemeinsames-geheimnis")]   # nicht an sich selbst
    # Empfänger: nur mit dem Geheimnis
    assert client.post("/api/internal/revoked").status_code == 403
    assert client.post("/api/internal/revoked", headers={"X-Festpos-Secret": "falsch"}).status_code == 403
    devices.revoked_serials()
    devices._revoked_cache = (time.monotonic(), set())       # alter Stand im Speicher
    r = client.post("/api/internal/revoked", headers={"X-Festpos-Secret": "ein-langes-gemeinsames-geheimnis"})
    assert r.status_code == 200 and c["serial"] in devices.revoked_serials()
    # ohne CA-Datei geht das Geheimnis nirgends hin
    sent.clear()
    monkeypatch.setattr(core.Config, "cafile", None)
    client.post(f"/api/admin/devices/{did}/revoke?undo=true", auth=AUTH)
    time.sleep(0.3)
    assert sent == [] and c["serial"] not in devices.revoked_serials()


def test_first_use_retires_the_whole_chain(client, monkeypatch, tmp_path):
    """Ein Zertifikat, das ersetzt wurde, bevor es je benutzt wurde (z. B. Fully konnte es nicht laden): benutzt das Handy
    das nächste, sind ALLE Vorgänger gesperrt – sonst gälte das ursprüngliche weiter."""
    from app import devices
    _ca(monkeypatch, tmp_path)
    c1 = _cert(client)
    d = c1["device_id"]
    c2 = _cert(client, renew=True, device=d)
    c3 = _cert(client, renew=True, device=d)
    assert _row(c3["serial"])["replaces"] == c2["serial"] and _row(c2["serial"])["replaces"] == c1["serial"]
    _as_phone(monkeypatch, c3["serial"])
    assert client.post("/api/time/sync", json={"epoch_ms": int(time.time() * 1000)}).status_code == 200
    assert {c1["serial"], c2["serial"]} <= devices.revoked_serials() and c3["serial"] not in devices.revoked_serials()


# ------------------------------------------------------------------ Punkt 2: «Gesperrt» auch mit fester IP
def _phone_with_cert(client, device_id, name="Kellner 1", ip="192.168.55.101", mac="aa:bb:cc:dd:ee:01", **extra):
    from app import core, device_ids
    r = client.put("/api/admin/devices/phone", auth=AUTH, json={"name": name, "ip": ip, "cert": device_id})
    assert r.status_code == 200, r.text
    items = device_ids.phones_raw(core.rt.store)
    for x in items:
        if x.get("id") == r.json()["id"]:
            x.update(mac=mac, **extra)
    core.rt.store.execute(*device_ids.save_phones(items))
    return r.json()["id"]


def test_blocked_device_keeps_its_phone_entry_but_leaves_status_and_dhcp(client, monkeypatch, tmp_path):
    from app import core, device_ids, dhcp, status
    _ca(monkeypatch, tmp_path)
    c = _cert(client)
    pid = _phone_with_cert(client, c["device_id"])
    monkeypatch.setattr(dhcp, "HOSTS_DIR", tmp_path)
    monkeypatch.setattr(dhcp, "_written", None)
    hosts = lambda: (dhcp.sync(), (tmp_path / dhcp.HOSTS_FILE).read_text())[1]          # noqa: E731
    on_status = lambda: [t["ip"] for t in status._monitor_targets() if t["kind"] == "phone"]   # noqa: E731
    assert "aa:bb:cc:dd:ee:01,192.168.55.101" in hosts() and on_status() == ["192.168.55.101"]
    row = client.get("/api/admin/devices", auth=AUTH).json()["certs"][0]["id"]
    client.post(f"/api/admin/devices/{row}/revoke", auth=AUTH)
    assert device_ids.blocked(device_ids.lineages(core.rt.store)) == {c["device_id"]}
    assert "aa:bb:cc:dd:ee:01" not in hosts() and on_status() == []
    assert [x["id"] for x in device_ids.phones_raw(core.rt.store)] == [pid]                # Eintrag bleibt fürs Entsperren
    client.post(f"/api/admin/devices/{row}/revoke?undo=true", auth=AUTH)
    assert "aa:bb:cc:dd:ee:01,192.168.55.101" in hosts() and on_status() == ["192.168.55.101"]
    # endgültig löschen: auch der Handy-Eintrag (feste IP, MAC) geht
    client.post(f"/api/admin/devices/{row}/revoke", auth=AUTH)
    assert client.post(f"/api/admin/devices/{row}/remove", auth=AUTH).status_code == 200
    assert device_ids.phones_raw(core.rt.store) == [] and "aa:bb:cc:dd:ee:01" not in hosts()
    from app import devices
    assert c["serial"] in devices.revoked_serials()                                      # Sperre gilt weiter


def test_overview_counts_only_certs_someone_has_to_renew(client, monkeypatch, tmp_path):
    """Die Test-CA gilt 30 Tage – jedes Zertifikat läuft also «bald» ab. Gesperrte zählen nicht, ebenso Fully-Handys,
    solange der Pi sie selbst erneuert."""
    _ca(monkeypatch, tmp_path)
    count = lambda: client.get("/api/admin/stats", auth=AUTH).json()["certs_expiring"]      # noqa: E731
    a = _cert(client, "Kellner 1")
    b = _cert(client, "Kellner 2")
    assert count() == 2
    rows = {x["serial"]: x["id"] for x in client.get("/api/admin/devices", auth=AUTH).json()["certs"]}
    client.post(f"/api/admin/devices/{rows[a['serial']]}/revoke", auth=AUTH)
    assert count() == 1
    _phone_with_cert(client, b["device_id"], "Kellner 2", fully_pin="ab" * 32)
    assert count() == 0
    devs = client.get("/api/admin/devices", auth=AUTH).json()
    assert devs["renew"][b["device_id"]]["mode"] == "auto" and devs["renew_days"] == 730
    client.put("/api/admin/settings", auth=AUTH, json={"cert_renew_days": 0})          # Automatik aus
    assert count() == 1
    assert client.get("/api/admin/devices", auth=AUTH).json()["renew"][b["device_id"]]["mode"] == "manual"


def test_renew_setting(client):
    s = client.get("/api/admin/settings", auth=AUTH).json()
    assert s["cert_renew_days"] == "730" and "cert_renew_state" not in s
    assert client.put("/api/admin/settings", auth=AUTH, json={"cert_renew_days": 3650}).status_code == 422
    r = client.put("/api/admin/settings", auth=AUTH, json={"cert_days": 365, "cert_renew_days": 365})
    assert r.status_code == 422 and "kürzer" in r.json()["detail"]
    assert client.put("/api/admin/settings", auth=AUTH, json={"cert_days": 365, "cert_renew_days": 60}).status_code == 200
    assert client.put("/api/admin/settings", auth=AUTH, json={"cert_renew_days": 0}).json()["cert_renew_days"] == "0"
    assert client.put("/api/admin/settings", auth=AUTH, json={"cert_renew_days": -1}).status_code == 422


def test_app_hint_only_without_automatic_renewal(client, monkeypatch, tmp_path):
    _ca(monkeypatch, tmp_path)
    a = _cert(client, "Kellner 1")
    b = _cert(client, "Kellner 2")
    _phone_with_cert(client, b["device_id"], "Kellner 2", fully_pin="ab" * 32)
    _as_phone(monkeypatch, a["serial"], "Kellner 1")
    st = client.get("/api/device/cert").json()
    assert (st["auto"], st["fully"]) == (False, False)                  # ohne Fully: «Jetzt erneuern» in der App
    _as_phone(monkeypatch, b["serial"], "Kellner 2")
    st = client.get("/api/device/cert").json()
    assert (st["auto"], st["fully"]) == (True, True)                    # erneuert der Pi selbst: kein Hinweis
    client.put("/api/admin/settings", auth=AUTH, json={"cert_renew_days": 0})
    st = client.get("/api/device/cert").json()
    assert (st["auto"], st["fully"]) == (False, True)                   # Hinweis: über die Verwaltung erneuern


# ------------------------------------------------------------------ Punkt 3: automatisch erneuern über Fully
def _fully_device(client, tmp_path, monkeypatch):
    """Handy 127.0.0.1 mit Fully und Gerätezertifikat (über Fully eingerichtet), das bald abläuft und nicht neu ist."""
    from app import core
    from tests.test_fully import _fully_phone
    srv, _ = _fully_phone(client, tmp_path, monkeypatch)
    r = client.post("/api/admin/fully/device-cert", auth=AUTH, json={"ip": "127.0.0.1", "name": "XCover 9"}).json()
    assert r["step"] == "restart"
    made = (dt.datetime.now() - dt.timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%S")
    until = (dt.date.today() + dt.timedelta(days=5)).isoformat()
    core.rt.store.execute("UPDATE device_certs SET created_at=?, expires_at=?", (made, until))
    return srv, r["serial"], r["device_id"]


def _round(screen_on=False) -> str | None:
    """Eine Runde wie monitor._fully_loop: Fully hat geantwortet, dann auto_renew."""
    from app import core, fully
    cfg = fully.config()
    for t in cfg["targets"]:
        core.rt.monitor.fully_store(t["name"], t["ip"], {"state": "ok", "data": {"screen_on": screen_on}})
    return asyncio.run(fully.auto_renew(cfg))


def _state(did: str) -> dict:
    from app import devices
    return devices.renew_state().get(did, {})


def test_auto_renew_via_fully(client, tmp_path, monkeypatch):
    from app import core, devices
    srv, old, did = _fully_device(client, tmp_path, monkeypatch)
    try:
        pw_before = srv.settings["clientCaPassword"]
        assert _round() == did
        st = _state(did)
        assert st["state"] == "running" and st["from"] == old and st["tries"] == 1
        new = st["serial"]
        assert _row(new)["replaces"] == old and _row(new)["device_id"] == did
        assert srv.settings["clientCaPassword"] != pw_before and srv.commands[-1] == "restartApp"
        assert old not in devices.revoked_serials()                 # das alte gilt, bis das Handy das neue benutzt
        assert _round() is None and _state(did)["state"] == "running"      # wartet auf das Handy, nichts Neues
        _as_phone(monkeypatch, new, "XCover 9")                     # Handy meldet sich mit dem neuen Zertifikat
        client.post("/api/time/sync", json={"epoch_ms": int(time.time() * 1000)})
        assert old in devices.revoked_serials()
        assert _round() is None and _state(did)["state"] == "ok"
        assert client.get("/api/admin/devices", auth=AUTH).json()["renew"][did]["mode"] == "auto"
        log = core.rt.store.query("SELECT actor, details FROM audit_log WHERE action='device_cert' ORDER BY id")
        assert log[-1]["actor"] == "FestPOS" and json.loads(log[-1]["details"])["via"] == "auto"
    finally:
        srv.stop()


def test_auto_renew_tries_again_when_fully_does_not_load_it(client, tmp_path, monkeypatch):
    from app import devices
    srv, old, did = _fully_device(client, tmp_path, monkeypatch)
    try:
        assert _round() == did
        first = _state(did)["serial"]
        st = devices.renew_state()
        st[did]["at"] = time.time() - 700                           # Handy hat sich 10 Minuten nicht mit dem neuen gemeldet
        devices.save_renew_state(did, st[did])
        assert _round() == did                                      # das neue weg, gleich nochmals eingespielt
        assert first in devices.revoked_serials() and old not in devices.revoked_serials()
        st = _state(did)
        assert st["state"] == "running" and st["tries"] == 2 and _row(st["serial"])["replaces"] == old
        st["at"] = time.time() - 700
        devices.save_renew_state(did, st)
        assert _round() is None                                     # zweimal gescheitert: heute nicht mehr
        assert _state(did)["state"] == "failed" and old not in devices.revoked_serials()
        m = client.get("/api/admin/devices", auth=AUTH).json()["renew"][did]
        assert m["mode"] == "failed" and m["step"] == "restart"
        assert client.get("/api/admin/stats", auth=AUTH).json()["certs_expiring"] == 1      # jetzt von Hand
    finally:
        srv.stop()


def test_auto_renew_failed_download_counts_as_try(client, tmp_path, monkeypatch):
    from app import core
    srv, old, did = _fully_device(client, tmp_path, monkeypatch)
    try:
        srv.fetch = False
        assert _round() == did and _state(did)["state"] == "retry" and _state(did)["step"] == "download"
        assert _round() == did and _state(did)["state"] == "failed"
        assert _round() is None
        assert core.rt.store.scalar("SELECT COUNT(*) FROM device_certs") == 1          # nichts Halbes zurückgeblieben
        fails = core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='device_cert_auto_failed'")
        assert fails == 2
    finally:
        srv.stop()


def test_auto_renew_does_not_disturb(client, tmp_path, monkeypatch):
    """Nur, wenn das Handy niemand braucht: keine wartende Bestellung, 10 Minuten nichts bestellt – und bei
    eingeschaltetem Bildschirm 10 Minuten keine Bestellung im ganzen Fest."""
    from app import core, fully
    srv, old, did = _fully_device(client, tmp_path, monkeypatch)
    now = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    order = lambda cert: core.rt.store.execute(                     # noqa: E731
        "INSERT INTO orders(client_uuid, table_label, device, device_cert, total_cents, created_at, test) VALUES (?,?,?,?,?,?,0)",
        (str(time.time_ns()), "1", "Anna", cert, 100, now))
    try:
        core.rt.monitor.seen("Anna", "127.0.0.1", "2", "x", serial=old, cert_name="XCover 9")      # 2 Bestellungen warten
        assert _round() is None
        core.rt.monitor.seen("Anna", "127.0.0.1", "0", "x", serial=old, cert_name="XCover 9")
        order("XCover 9")                                           # eben selbst bestellt
        assert _round() is None
        core.rt.store.execute("DELETE FROM orders")
        order("XCover 7")                                           # ein anderes Handy bestellt gerade
        assert _round(screen_on=True) is None                       # Bildschirm an: jemand könnte es gleich brauchen
        fully._installing.add("127.0.0.1")                          # Knopf in der Verwaltung läuft gerade
        assert _round(screen_on=False) is None
        fully._installing.discard("127.0.0.1")
        assert _round(screen_on=False) == did                       # Bildschirm aus: jetzt
    finally:
        srv.stop()


def test_auto_renew_only_when_due_enabled_and_clock_plausible(client, tmp_path, monkeypatch):
    from app import core
    srv, old, did = _fully_device(client, tmp_path, monkeypatch)
    try:
        client.put("/api/admin/settings", auth=AUTH, json={"cert_renew_days": 0})
        assert _round() is None                                     # aus
        client.put("/api/admin/settings", auth=AUTH, json={"cert_renew_days": 2})
        assert _round() is None                                     # noch 5 Tage: nicht fällig
        client.put("/api/admin/settings", auth=AUTH, json={"cert_renew_days": 730})
        fresh = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        core.rt.store.execute("UPDATE device_certs SET created_at=?", (fresh,))
        assert _round() is None                                     # eben ausgestellt: nie gleich wieder
        made = (dt.datetime.now() - dt.timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%S")
        core.rt.store.execute("UPDATE device_certs SET created_at=?", (made,))
        later = (dt.datetime.now() + dt.timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")
        core.rt.store.execute("INSERT INTO orders(client_uuid, table_label, device, total_cents, created_at, test) "
                              "VALUES ('u1','1','x',100,?,0)", (later,))
        assert _round() is None                                     # Uhr steht vor der letzten Bestellung: sicher falsch
        core.rt.store.execute("DELETE FROM orders")
        items = json.loads(core.rt.store.setting("monitor_devices"))
        items[0]["active"] = False
        client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": items})
        assert _round() is None                                     # nicht im Einsatz
        items[0]["active"] = True
        client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": items})
        assert _round() == did
    finally:
        srv.stop()
