"""Version 2026.10.10-27: «Im Einsatz» – Geräte bleiben eingetragen, werden aber nur geprüft und angezeigt, wenn sie
im Einsatz sind (Handys, Netzwerkgeräte, Drucker). Dazu meldet jeder Pi seine Dienste (Statusanzeige pro Pi)."""
from __future__ import annotations

import asyncio
import json
import time

from tests.test_api import AUTH, setup_shop

DEVICES = [{"name": "XCover 1", "kind": "phone", "ip": "192.168.55.101"},
           {"name": "XCover 2", "kind": "phone", "ip": "192.168.55.102", "active": False},
           {"name": "Switch", "kind": "net", "ip": "192.168.55.30"},
           {"name": "AP Zelt", "kind": "net", "ip": "192.168.55.41", "active": False}]


def test_inactive_devices_stay_listed_but_are_not_shown(client):
    setup_shop(client)
    assert client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": DEVICES}).status_code == 200
    stored = json.loads(client.get("/api/admin/settings", auth=AUTH).json()["monitor_devices"])
    assert [(d["name"], d["active"]) for d in stored] == [("XCover 1", True), ("XCover 2", False), ("Switch", True),
                                                          ("AP Zelt", False)]
    s = client.get("/api/status", auth=AUTH).json()
    assert [d["name"] for d in s["devices"]] == ["XCover 1"] and s["network_count"] == 1
    # alle Handys aus: keine «gesehenen» Handys als Ersatz (es sind ja welche eingetragen)
    client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": [{**d, "active": False} for d in DEVICES]})
    s = client.get("/api/status", auth=AUTH).json()
    assert s["devices"] == [] and s["network_count"] == 0
    # ausgeschalteter Drucker: nicht mehr auf der Statusanzeige (heute: «aus»)
    pid = client.get("/api/admin/printers", auth=AUTH).json()[0]["id"]
    p = client.get("/api/admin/printers", auth=AUTH).json()[0]
    assert client.put(f"/api/admin/printers/{pid}", auth=AUTH, json={**{k: p[k] for k in ("name", "host", "port")},
                                                                       "enabled": False}).status_code == 200
    names = [x["name"] for x in client.get("/api/status", auth=AUTH).json()["printers"]]
    assert p["name"] not in names and len(names) == 1


def test_monitor_checks_only_devices_in_use(monkeypatch):
    monkeypatch.setenv("FESTPOS_HOST_CHECKS", "1")       # Netzwerkgeräte prüfen wie auf dem Pi (Prüfung ist unten ersetzt)
    from app import monitor as monitor_mod
    m = monitor_mod.Monitor()
    targets = monitor_mod.parse_targets(json.dumps(DEVICES))
    assert [t["active"] for t in targets] == [True, False, True, False]
    checked = []

    async def fake_check(t):
        checked.append(t["name"])
        return True
    m._get_targets, m._get_printers = (lambda: targets), (lambda: [])
    m._check_target = fake_check
    m._services = lambda: asyncio.sleep(0, [])
    asyncio.run(m.check())
    assert checked == ["XCover 1", "Switch"]
    assert [h["name"] for h in m.hosts] == ["Switch"]
    # alte Textform ohne «active»: im Einsatz
    assert monitor_mod.parse_targets(None, "Switch=192.168.55.30")[0]["active"] is True


def test_fully_and_power_use_only_phones_in_use(client):
    from app import core, fully
    pin = "a" * 64
    devices = [{**d, "fully_pin": pin} if d["kind"] == "phone" else d for d in DEVICES]
    core.rt.store.execute("INSERT INTO settings(key,value) VALUES ('monitor_devices', ?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(devices),))
    cfg = fully.config()
    assert [(t["name"], t["active"]) for t in cfg["targets"]] == [("XCover 1", True), ("XCover 2", False)]
    assert client.get("/api/admin/system", auth=AUTH).json()["phones_fully"] == 1
    # Speichern über die Verwaltung behält Zertifikat und «nicht im Einsatz»
    client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": DEVICES})
    stored = json.loads(client.get("/api/admin/settings", auth=AUTH).json()["monitor_devices"])
    assert stored[1]["active"] is False and stored[1]["fully_pin"] == pin


def test_each_pi_reports_its_services(client, monkeypatch):
    from app import core, system
    svc = [{"name": "rqlited", "ok": True}, {"name": "nftables", "ok": False}]

    async def services():                                  # auch der Monitor im Hintergrund sieht diese Dienste
        return list(svc)
    monkeypatch.setattr(core.rt.monitor, "_services", services)
    core.rt.monitor.services = list(svc)
    info = system.local_info()
    assert info["svc"] == {"rqlited": True, "nftables": False}
    system._published.update(at=0.0, static=None)
    system.publish()
    stored = json.loads(core.rt.store.setting(f"node_info.{core.Config.node}"))
    assert stored["svc"] == {"rqlited": True, "nftables": False}
    # Ein Durchgang des Monitors, der VOR dem Ersetzen von _services begann, kann danach die echten Dienste
    # zurückschreiben (in der CI keine) – dann nochmals melden; spätere Durchgänge sehen nur noch svc
    for _ in range(50):
        pis = client.get("/api/status", auth=AUTH).json()["pis"]
        if "nftables" in pis[0]["info"]["svc"]:
            break
        core.rt.monitor.services = list(svc)
        system.publish()
        time.sleep(0.1)
    assert pis[0]["info"]["svc"]["nftables"] is False
    # Änderung eines Dienstes: sofort gemeldet (nicht erst nach einer Minute)
    svc[1] = {"name": "nftables", "ok": True}
    core.rt.monitor.services = list(svc)
    system.publish()
    assert json.loads(core.rt.store.setting(f"node_info.{core.Config.node}"))["svc"]["nftables"] is True


def test_publish_from_monitor_never_overwrites_newer_info(client, monkeypatch):
    """Der Monitor meldet in einem eigenen Thread. Rechnet er noch mit dem alten Stand, während ein anderer Aufruf
    den neuen schreibt, darf er danach nicht den alten zurückschreiben."""
    import threading
    import time

    from app import core, system
    state = {"nftables": False}
    started = threading.Event()
    real = system.local_info

    def local_info():
        info = {**real(), "svc": dict(state)}
        if threading.current_thread().name == "monitor":
            started.set()
            time.sleep(0.3)                                # alter Stand, schreibt erst nach dem anderen Aufruf
        return info
    monkeypatch.setattr(system, "local_info", local_info)
    monkeypatch.setattr(core.rt.monitor, "_extra", None)   # der echte Monitor im Hintergrund stört hier nicht
    system._published.update(at=0.0, static=None)
    monitor = threading.Thread(target=system.publish, name="monitor")
    monitor.start()
    assert started.wait(5)
    state["nftables"] = True
    system.publish()
    monitor.join()
    assert json.loads(core.rt.store.setting(f"node_info.{core.Config.node}"))["svc"]["nftables"] is True
