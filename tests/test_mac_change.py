"""T4: Gerät mit bekanntem Zertifikat, aber neuer MAC – feste IP umschreiben, Alarm bei zwei Geräten, Entscheiden von
Hand, Protokoll, Statusanzeige. Ohne echten dnsmasq, ARP und Ping: beides nachgebildet, die Zeit wird vorgegeben."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.test_api import AUTH
from tests.test_users import team  # noqa: F401  (Fixture)

OLD, NEW, THIRD = "a4:bb:cc:dd:ee:01", "6a:11:22:33:44:55", "6e:99:88:77:66:55"
FIXED, POOL, POOL2 = "192.168.55.101", "192.168.55.150", "192.168.55.151"


class Net:
    """Nachgebildetes Netz: ARP-Tabelle (IP -> MAC), Antworten auf den Ping, Meldungen der Bestell-App."""

    def __init__(self, monkeypatch, tmp_path: Path):
        from app import core, devices, dhcp, mac_change
        self.core, self.mc, self.dhcp = core, mac_change, dhcp
        self.arp: dict[str, str] = {}
        self.ping: dict[str, str] = {}
        self.dir = tmp_path / "dhcp"
        self.dir.mkdir()
        monkeypatch.setattr(dhcp, "arp_table", lambda arp=None: dict(self.arp))
        monkeypatch.setattr(mac_change, "ping_mac", lambda ip: self.ping.get(ip))
        monkeypatch.setattr(dhcp, "HOSTS_DIR", self.dir)
        monkeypatch.setattr(dhcp, "_written", None)
        monkeypatch.setattr(devices, "_ids_cache", (0.0, {}))
        mac_change.reset()
        self.t0 = time.time()
        mac_change._since = self.t0 - 7200                  # dieser Pi läuft schon lange
        core.rt.monitor.devices.clear()
        core.rt.monitor.targets = []

    def report(self, serial: str, ip: str, at: float) -> None:
        """Bestell-App meldet sich mit Zertifikat von dieser IP (wie monitor.seen, mit vorgegebener Zeit)."""
        e = self.core.rt.monitor.devices.setdefault(f"c:{serial}", {"serial": serial, "name": "", "ips": {}, "ip": ip,
                                                                      "last_seen": at, "outbox": 0, "version": ""})
        e["ips"][ip] = at
        e.update(ip=ip, last_seen=max(e["last_seen"], at))

    def answers(self, ip: str, mac: str | None) -> None:
        """Feste IP antwortet im Monitor auf den Ping (mit dieser MAC in der ARP-Tabelle) – oder nicht (None)."""
        self.core.rt.monitor.targets = [{"kind": "phone", "ip": ip, "port": None, "ok": mac is not None}]
        if mac:
            self.arp[ip] = mac

    def tick(self, dt: float) -> None:
        self.mc.tick(self.t0 + dt)

    def phone(self, pid: str = "p000000000001") -> dict:
        return next(d for d in json.loads(self.core.rt.store.setting("monitor_devices")) if d.get("id") == pid)

    def state(self) -> dict:
        return self.mc.load_state().get("S1") or {}

    def audit(self, action: str) -> list[dict]:
        rows = self.core.rt.store.query("SELECT actor, details FROM audit_log WHERE action=? ORDER BY id", (action,))
        return [{**json.loads(r["details"]), "actor": r["actor"]} for r in rows]

    def hosts(self) -> str:
        f = self.dir / "festpos-handys"
        return f.read_text() if f.exists() else ""


def _setup(client, extra_phones=()):
    from app import core, main
    client.portal.call(main.monitor.stop)                   # kein Monitor im Hintergrund: die Runden gibt der Test vor
    core.rt.store.execute("INSERT INTO device_certs(name, serial, created_at, expires_at, device_id) VALUES (?,?,?,?,?)",
                          ("XCover 1", "S1", "2026-09-01T10:00:00", "2099-01-01", "S1"))
    phones = [{"id": "p000000000001", "name": "XCover 1", "kind": "phone", "ip": FIXED, "port": None, "active": True,
               "cert": "S1", "mac": OLD}, *extra_phones]
    core.rt.store.execute("INSERT INTO settings(key,value) VALUES ('monitor_devices', ?) ON CONFLICT(key) DO UPDATE "
                          "SET value=excluded.value", (json.dumps(phones),))


@pytest.fixture
def net(client, monkeypatch, tmp_path):
    _setup(client)
    return Net(monkeypatch, tmp_path)


def _new_mac_appears(net, at: float = 0) -> None:
    """Das Handy meldet sich zweimal (20 s Abstand) mit der neuen MAC von einer Adresse aus dem Bereich."""
    net.arp[POOL] = NEW
    net.report("S1", POOL, net.t0 + at)
    net.tick(at)
    net.report("S1", POOL, net.t0 + at + 20)
    net.tick(at + 20)


def test_switch_after_quiet_minutes(net):
    net.arp[FIXED] = OLD
    net.report("S1", FIXED, net.t0 - 60)                    # vorher: feste IP, alte MAC
    net.tick(-60)
    net.arp[POOL] = NEW
    net.report("S1", POOL, net.t0)
    net.tick(0)
    assert net.state() == {}                                # einmal gesehen: noch kein Kandidat (veralteter ARP-Eintrag?)
    net.report("S1", POOL, net.t0 + 20)
    net.tick(20)
    st = net.state()
    assert st["state"] == "wait" and st["old"] == OLD and st["new"] == NEW and st["new_ip"] == POOL
    assert st["due"] == int(net.t0 + 20) + 600              # 10 Minuten ab dem ersten Auftauchen der neuen
    for s in range(40, 600, 20):
        net.report("S1", POOL, net.t0 + s)
        net.tick(s)
    assert net.phone()["mac"] == OLD and net.state()["state"] == "wait"
    net.report("S1", POOL, net.t0 + 630)
    net.tick(630)
    assert net.phone()["mac"] == NEW and net.state() == {}
    assert f"{NEW},{FIXED}," in net.hosts()                  # Zuteilung für dnsmasq gleich geschrieben
    [a] = net.audit("device_mac_change")
    assert a == {"name": "XCover 1", "old": OLD, "new": NEW, "ip": FIXED, "via": "auto", "actor": "FestPOS"}


def test_both_active_raises_alarm_instead_of_switch(net, client):
    _new_mac_appears(net)
    net.arp[FIXED] = OLD
    net.report("S1", FIXED, net.t0 + 60)                    # die alte meldet sich NACH der neuen: zwei Geräte
    net.tick(60)
    st = net.state()
    assert st["state"] == "alarm" and st["reason"] == "both"
    for s in range(80, 1500, 20):                           # bleibt Alarm, auch nach der Wartezeit
        net.report("S1", POOL, net.t0 + s)
        net.tick(s)
    assert net.phone()["mac"] == OLD and net.state()["state"] == "alarm"
    assert len(net.audit("device_mac_conflict")) == 1        # einmal, nicht jede Runde
    assert not net.audit("device_mac_change")
    stats = client.get("/api/admin/stats", auth=AUTH).json()
    assert stats["mac_alarms"] == [{"device": "S1", "name": "XCover 1"}]
    dev = client.get("/api/status", auth=AUTH).json()["devices"]
    assert [d["mac_alarm"] for d in dev] == [True]
    view = client.get("/api/admin/devices", auth=AUTH).json()["mac"]["S1"]
    assert view["state"] == "alarm" and view["old"] == OLD and view["new"] == NEW


def test_quick_change_of_the_same_phone_is_no_alarm(net):
    net.arp[FIXED] = OLD
    net.report("S1", FIXED, net.t0)                         # letzte Meldung der alten MAC …
    net.tick(0)
    net.arp[POOL] = NEW                                     # … 10 s später dasselbe Handy mit neuer MAC
    net.report("S1", POOL, net.t0 + 10)
    net.tick(10)
    net.report("S1", POOL, net.t0 + 30)
    net.tick(30)
    assert net.state()["state"] == "wait"
    net.report("S1", POOL, net.t0 + 640)
    net.tick(640)
    assert net.phone()["mac"] == NEW


def test_fixed_ip_still_answering_with_old_mac_blocks_the_switch(net):
    _new_mac_appears(net)
    net.ping[FIXED] = OLD                                   # Ping direkt vor dem Umschreiben: die alte ist noch da
    net.report("S1", POOL, net.t0 + 630)
    net.tick(630)
    assert net.phone()["mac"] == OLD
    net.report("S1", POOL, net.t0 + 640)
    net.tick(640)
    assert net.state()["state"] == "alarm"


def test_ping_of_the_fixed_ip_counts_only_for_the_old_mac(net):
    _new_mac_appears(net)
    net.answers(FIXED, OLD)                                 # Monitor: feste IP antwortet mit der alten MAC
    net.report("S1", POOL, net.t0 + 90)
    net.tick(90)
    assert net.state()["state"] == "alarm"


def test_foreign_device_on_the_fixed_ip_never_takes_the_reservation(net):
    net.answers(FIXED, THIRD)                               # fremdes Gerät mit von Hand gesetzter IP, ohne Zertifikat
    for s in range(0, 1300, 20):
        net.tick(s)
    assert net.phone()["mac"] == OLD and net.state() == {}


def test_new_mac_of_another_phone_is_an_alarm(client, monkeypatch, tmp_path):
    _setup(client, [{"id": "p000000000002", "name": "XCover 2", "kind": "phone", "ip": "192.168.55.102", "port": None,
                     "active": True, "mac": NEW}])
    net = Net(monkeypatch, tmp_path)
    _new_mac_appears(net)
    st = net.state()
    assert st["state"] == "alarm" and st["reason"] == "taken" and st["other"] == "XCover 2"
    r = client.post("/api/admin/devices/mac", auth=AUTH, json={"device": "S1", "action": "take", "mac": NEW})
    assert r.status_code == 409 and "XCover 2" in r.json()["detail"]


def test_off_only_asks_and_manual_take(net, client):
    net.core.rt.store.execute("UPDATE settings SET value='0' WHERE key='mac_change_minutes'")
    _new_mac_appears(net)
    for s in range(40, 1300, 20):
        net.report("S1", POOL, net.t0 + s)
        net.tick(s)
    assert net.state()["state"] == "ask" and net.phone()["mac"] == OLD
    r = client.post("/api/admin/devices/mac", auth=AUTH, json={"device": "S1", "action": "take", "mac": NEW})
    assert r.status_code == 200
    assert net.phone()["mac"] == NEW and net.state() == {}
    [a] = net.audit("device_mac_change")
    assert a["via"] == "manual" and a["actor"] == "admin"
    r = client.post("/api/admin/devices/mac", auth=AUTH, json={"device": "S1", "action": "take", "mac": NEW})
    assert r.status_code == 409                             # schon entschieden


def test_keep_in_alarm_rejects_the_other_mac_for_a_day(net, client):
    _new_mac_appears(net)
    net.arp[FIXED] = OLD
    net.report("S1", FIXED, net.t0 + 60)
    net.tick(60)
    assert net.state()["state"] == "alarm"
    assert client.post("/api/admin/devices/mac", auth=AUTH, json={"device": "S1", "action": "keep", "mac": NEW}).status_code == 200
    assert net.state()["state"] == "ignore" and net.state()["new"] == NEW
    for s in range(80, 1300, 20):                           # die abgewiesene meldet sich weiter: kein neuer Alarm
        net.report("S1", POOL, net.t0 + s)
        net.report("S1", FIXED, net.t0 + s)
        net.tick(s)
    assert net.state()["state"] == "ignore" and net.phone()["mac"] == OLD
    assert len(net.audit("device_mac_conflict")) == 1
    [k] = net.audit("device_mac_keep")
    assert k["kept"] == OLD and k["rejected"] == NEW


def test_take_in_alarm_rejects_the_old_mac(net, client):
    _new_mac_appears(net)
    net.arp[FIXED] = OLD
    net.report("S1", FIXED, net.t0 + 60)
    net.tick(60)
    assert client.post("/api/admin/devices/mac", auth=AUTH, json={"device": "S1", "action": "take", "mac": NEW}).status_code == 200
    assert net.phone()["mac"] == NEW
    st = net.state()
    assert st["state"] == "ignore" and st["old"] == NEW and st["new"] == OLD
    for s in range(80, 300, 20):                            # das andere Gerät mit der alten MAC bleibt abgewiesen
        net.report("S1", FIXED, net.t0 + s)
        net.tick(s)
    assert net.state()["state"] == "ignore"


def test_restart_waits_from_the_start_of_this_pi(net):
    _new_mac_appears(net)
    first = net.state()["first"]
    net.mc.reset()                                          # Neustart: nicht bekannt, wann die alte zuletzt aktiv war
    net.mc._since = net.t0 + 300
    net.arp[POOL] = NEW
    net.report("S1", POOL, net.t0 + 300)
    net.tick(300)
    net.report("S1", POOL, net.t0 + 320)
    net.tick(320)
    st = net.state()
    assert st["first"] == first and st["due"] == int(net.t0 + 300) + 600
    net.report("S1", POOL, net.t0 + 640)
    net.tick(640)
    assert net.phone()["mac"] == OLD                        # nach dem alten Plan wäre es jetzt so weit
    net.report("S1", POOL, net.t0 + 910)
    net.tick(910)
    assert net.phone()["mac"] == NEW


def test_no_flip_flop_when_the_old_mac_comes_back(net):
    _new_mac_appears(net)
    net.report("S1", POOL, net.t0 + 630)
    net.tick(630)
    assert net.phone()["mac"] == NEW
    net.arp[POOL2] = OLD                                    # die alte MAC kommt wieder (z. B. WLAN-Einstellung zurück)
    for s in (700, 720, 740):
        net.report("S1", POOL2, net.t0 + s)
        net.tick(s)
    assert net.phone()["mac"] == NEW and net.state()["state"] == "wait" and net.state()["new"] == OLD


def test_revoked_device_is_left_alone(net, client):
    net.core.rt.store.execute("UPDATE device_certs SET revoked_at='2026-10-01T10:00:00' WHERE serial='S1'")
    _new_mac_appears(net)
    for s in range(40, 1300, 20):
        net.report("S1", POOL, net.t0 + s)
        net.tick(s)
    assert net.phone()["mac"] == OLD and net.state() == {}


def test_writes_only_on_change(net, monkeypatch):
    """SD-Karten: in der Wartezeit kein Schreibzugriff pro Runde – nur beim neuen Zustand und beim Umschreiben."""
    store = net.core.rt.store
    writes = []
    for name in ("execute", "batch"):
        orig = getattr(store, name)

        def wrap(sql, *a, _orig=orig, **kw):
            text = sql if isinstance(sql, str) else " ".join(str(x[0]) for x in sql)
            if not text.lstrip().upper().startswith("SELECT"):
                writes.append(text)
            return _orig(sql, *a, **kw)
        monkeypatch.setattr(store, name, wrap)
    for s in range(0, 600, 10):                             # 10 Minuten nichts Neues
        net.tick(s)
    assert writes == []
    _new_mac_appears(net, 600)
    for s in range(640, 1200, 10):
        net.report("S1", POOL, net.t0 + s)
        net.tick(s)
    assert len(writes) == 1                                  # nur der Zustand «wartet»
    net.report("S1", POOL, net.t0 + 1230)
    net.tick(1230)
    assert net.phone()["mac"] == NEW and len(writes) == 2    # und einmal das Umschreiben (ein batch)


def test_status_shows_the_phone_online_on_a_pool_address(net, client):
    net.core.rt.monitor.seen("Anna", POOL, "0", "1", serial="S1", cert_name="XCover 1")
    dev = client.get("/api/status", auth=AUTH).json()["devices"]
    assert len(dev) == 1 and dev[0]["online"] is True and dev[0]["mac_alarm"] is False


def test_lead_may_decide(net, client, team):  # noqa: F811
    net.core.rt.store.execute("UPDATE settings SET value='0' WHERE key='mac_change_minutes'")
    _new_mac_appears(net)
    r = client.post("/api/admin/devices/mac", headers=team["Lea"], json={"device": "S1", "action": "keep", "mac": NEW})
    assert r.status_code == 200
    r = client.post("/api/admin/devices/mac", headers=team["Vera"], json={"device": "S1", "action": "take", "mac": NEW})
    assert r.status_code == 403


def test_setting_minutes(client):
    assert client.get("/api/admin/settings", auth=AUTH).json()["mac_change_minutes"] == "10"
    assert "mac_change_state" not in client.get("/api/admin/settings", auth=AUTH).json()
    assert client.put("/api/admin/settings", auth=AUTH, json={"mac_change_minutes": 241}).status_code == 422
    assert client.put("/api/admin/settings", auth=AUTH, json={"mac_change_minutes": 30}).status_code == 200
    assert client.get("/api/admin/settings", auth=AUTH).json()["mac_change_minutes"] == "30"


def test_arp_table_and_random_mac(tmp_path):
    from app import dhcp
    arp = tmp_path / "arp"
    arp.write_text("IP address       HW type     Flags       HW address            Mask     Device\n"
                   "192.168.55.150   0x1         0x2         6A:11:22:33:44:55     *        eth0\n"
                   "192.168.55.151   0x1         0x0         00:00:00:00:00:00     *        eth0\n"
                   "192.168.55.152   0x1         0x0         a4:bb:cc:dd:ee:01     *        eth0\n"
                   "kaputt\n")
    assert dhcp.arp_table(arp) == {"192.168.55.150": "6a:11:22:33:44:55"}
    assert dhcp.arp_table(tmp_path / "fehlt") == {}
    assert dhcp.is_random("6a:11:22:33:44:55") and dhcp.is_random("AE-00-00-00-00-01")
    assert not dhcp.is_random("a4:bb:cc:dd:ee:01") and not dhcp.is_random(None) and not dhcp.is_random("x")
