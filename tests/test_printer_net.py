"""Drucker-Netz: Drucker in einem eigenen VLAN (app/printer_net.py, deploy/festpos-druckernetz.py). Ohne echten
NetworkManager: nmcli ist ein Ersatzskript, das die Aufrufe mitschreibt."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from tests.test_api import AUTH

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
HELPER = DEPLOY / "festpos-druckernetz.py"


@pytest.fixture
def pn(client, monkeypatch, tmp_path):
    from app import core, printer_net
    monkeypatch.setattr(core.Config, "node_ip", "192.168.55.11")
    monkeypatch.setattr(core.Config, "peers", ["https://192.168.55.11:8443", "https://192.168.55.12:8443",
                                               "https://192.168.55.13:8443"])
    monkeypatch.setattr(printer_net, "DIR", tmp_path / "druckernetz")
    monkeypatch.setattr(printer_net, "_written", None)
    (tmp_path / "druckernetz").mkdir()
    return printer_net


def test_switch_on_and_off(client, pn, tmp_path):
    r = client.put("/api/admin/printer-net", json={"vlan": 56, "net": "192.168.56"}, auth=AUTH)
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["vlan"], d["net"]) == (56, "192.168.56")
    assert d["pis"] == ["192.168.56.11", "192.168.56.12", "192.168.56.13"]
    here = next(n for n in d["nodes"] if n["here"])
    assert here["pnet"]["vlan"] == 56 and here["pnet"]["ip"] == "192.168.56.11"
    f = tmp_path / "druckernetz" / "druckernetz.json"
    assert json.loads(f.read_text()) == {"vlan": 56, "net": "192.168.56"}       # dieser Pi gleich beim Speichern
    audit = client.get("/api/admin/audit?q=printer_net", auth=AUTH).json()["entries"]
    assert audit[0]["action"] == "printer_net" and audit[0]["details"] == {"vlan": 56, "net": "192.168.56.0/24"}

    r = client.put("/api/admin/printer-net", json={"vlan": 0, "net": ""}, auth=AUTH)
    assert r.status_code == 200 and r.json()["vlan"] == 0
    assert r.json()["net"] == "192.168.56"                                       # bleibt zum Wiedereinschalten
    assert json.loads(f.read_text()) == {"vlan": 0, "net": ""}
    assert r.json()["pis"] == [] and r.json()["warnings"] == []


@pytest.mark.parametrize("body,status", [
    ({"vlan": 1, "net": "192.168.56"}, 422),          # VLAN 1 = normales Netz am Switch
    ({"vlan": 4095, "net": "192.168.56"}, 422),
    ({"vlan": 56, "net": "192.168.55"}, 422),         # das FestPOS-Netz selbst
    ({"vlan": 56, "net": "8.8.8"}, 422),              # nicht privat
    ({"vlan": 56, "net": "192.168.256"}, 422),
    ({"vlan": 56, "net": "192.168.56.0"}, 422),
    ({"vlan": 56, "net": "10.0.56"}, 200),
])
def test_validation(client, pn, body, status):
    r = client.put("/api/admin/printer-net", json=body, auth=AUTH)
    assert r.status_code == status, r.text


def test_refuses_without_own_address(client, pn, monkeypatch):
    """Ohne NODE_IP lässt sich die Überschneidung mit dem FestPOS-Netz nicht prüfen: nicht einschalten, und die
    bisherige Einstellung bleibt (CodeRabbit PR #68)."""
    from app import core
    assert client.put("/api/admin/printer-net", json={"vlan": 56, "net": "192.168.56"}, auth=AUTH).status_code == 200
    monkeypatch.setattr(core.Config, "node_ip", "")
    r = client.put("/api/admin/printer-net", json={"vlan": 57, "net": "192.168.57"}, auth=AUTH)
    assert r.status_code == 422 and "NODE_IP" in r.json()["detail"]
    assert pn.config() == {"vlan": 56, "net": "192.168.56"}
    assert client.put("/api/admin/printer-net", json={"vlan": 0, "net": ""}, auth=AUTH).status_code == 200   # aus geht


def test_only_admin():
    from app import auth
    assert auth.required_role("PUT", "/api/admin/printer-net") == "admin"
    assert auth.required_role("GET", "/api/admin/printer-net") == "admin"


def test_warnings_for_printer_addresses(client, pn):
    for name, host in (("Bar", "192.168.56.20"), ("Küche", "192.168.55.21"), ("Kasse", "192.168.56.12"),
                       ("Name", "drucker.local")):
        assert client.post("/api/admin/printers", json={"name": name, "host": host}, auth=AUTH).status_code == 200
    d = client.put("/api/admin/printer-net", json={"vlan": 56, "net": "192.168.56"}, auth=AUTH).json()
    assert d["warnings"] == [{"printer": "Küche", "host": "192.168.55.21", "kind": "outside"},
                             {"printer": "Kasse", "host": "192.168.56.12", "kind": "pi"}]


def test_sync_writes_only_when_changed(client, pn, tmp_path):
    from app import core
    core.rt.store.execute("INSERT INTO settings(key,value) VALUES ('printer_vlan','56'),('printer_vlan_net','192.168.56')")
    pn.sync()
    f = tmp_path / "druckernetz" / "druckernetz.json"
    assert json.loads(f.read_text()) == {"vlan": 56, "net": "192.168.56"}
    f.write_text("von Hand")
    pn.sync()                                        # unverändert in der Datenbank: nicht neu schreiben
    assert f.read_text() == "von Hand"
    assert not list(f.parent.glob(".*"))
    pn.DIR = tmp_path / "fehlt"
    pn.sync()                                        # Rechner ohne Einrichtung: nichts, kein Fehler


def test_broken_setting_counts_as_off(client, pn):
    from app import core
    core.rt.store.execute("INSERT INTO settings(key,value) VALUES ('printer_vlan','abc'),('printer_vlan_net','x')")
    assert pn.config() == {"vlan": 0, "net": ""}
    assert json.loads(pn.wanted()) == {"vlan": 0, "net": ""}


def test_local_state_from_ip_output(client, pn):
    from app import core
    core.rt.store.execute("INSERT INTO settings(key,value) VALUES ('printer_vlan','56'),('printer_vlan_net','192.168.56')")
    out = ("1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever\n"
           "2: eth0    inet 192.168.55.11/24 brd 192.168.55.255 scope global noprefixroute eth0\n"
           "4: eth0.56@eth0    inet 192.168.56.11/24 brd 192.168.56.255 scope global noprefixroute eth0.56\n")
    assert pn.local_state(addrs=out) == {"vlan": 56, "ip": "192.168.56.11", "dev": "eth0.56", "ok": True}
    assert pn.local_state(addrs=out.splitlines()[1])["ok"] is False
    core.rt.store.execute("UPDATE settings SET value='0' WHERE key='printer_vlan'")
    assert pn.local_state(addrs=out) == {"vlan": 0}


# -- festpos-druckernetz (root) ---------------------------------------------------------------------------------
def _helper():
    spec = importlib.util.spec_from_file_location("festpos_druckernetz", HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ENV = {"NODE_IP": "192.168.55.12", "FESTPOS_IFACE": "eth0", "FESTPOS_NET_CIDR": "24"}


def test_helper_reads_only_valid_files(tmp_path):
    h = _helper()
    f = tmp_path / "druckernetz.json"
    assert h.lies_soll(f) == {"vlan": 0}                                      # keine Datei: aus
    for bad in ("kaputt", '{"vlan": "56"}', '{"vlan": 56, "net": "192.168.56; rm"}', '{"vlan": 5000, "net": "1.2.3"}',
                '{"vlan": 56, "net": "192.168.999"}', '{"vlan": true}', "[]"):
        f.write_text(bad)
        assert h.lies_soll(f) is None, bad                                    # unbrauchbar: nichts ändern
    f.write_text('{"vlan": 56, "net": "192.168.56", "x": 1}')
    assert h.lies_soll(f) == {"vlan": 56, "net": "192.168.56"}
    link = tmp_path / "link.json"
    link.symlink_to(f)
    assert h.lies_soll(link) is None                                          # kein Symlink (Datei schreibt festpos)


def test_helper_env_and_target(tmp_path):
    h = _helper()
    env = tmp_path / "env"
    env.write_text('# generiert\nNODE_IP="192.168.55.12"\nFESTPOS_IFACE="eth0"\nFESTPOS_ADMIN_PASSWORD="a\\"b\\$c"\n')
    assert h.lies_env(env)["FESTPOS_ADMIN_PASSWORD"] == 'a"b$c'
    assert h.ziel({"vlan": 0}, {}) is None
    assert h.ziel({"vlan": 56, "net": "192.168.56"}, ENV) == {"parent": "eth0", "id": "56", "ifname": "eth0.56",
                                                              "addr": "192.168.56.12/24"}
    long_if = {**ENV, "FESTPOS_IFACE": "enx00e04c680001"}
    assert h.ziel({"vlan": 56, "net": "10.9.8"}, long_if)["ifname"] == "vlan56"
    with pytest.raises(ValueError):
        h.ziel({"vlan": 56, "net": "192.168.55"}, ENV)                        # gleiches Netz wie die Pi
    with pytest.raises(ValueError):
        h.ziel({"vlan": 56, "net": "192.168.56"}, {**ENV, "FESTPOS_IFACE": "eth0;x"})
    with pytest.raises(ValueError):
        h.ziel({"vlan": 56, "net": "192.168.56"}, {})                         # ohne NODE_IP


def test_helper_plan():
    h = _helper()
    want = {"parent": "eth0", "id": "56", "ifname": "eth0.56", "addr": "192.168.56.12/24"}
    assert h.plan(None, None) == []
    assert h.plan(None, {**want, "active": True}) == [["connection", "delete", "festpos-drucker"]]
    assert h.plan(want, {**want, "active": True}) == []
    assert h.plan(want, {**want, "active": False}) == [["connection", "up", "festpos-drucker"]]
    steps = h.plan(want, {**want, "addr": "192.168.56.11/24", "active": True})          # Karte von A nach B
    assert steps[0] == ["connection", "delete", "festpos-drucker"] and steps[-1] == ["connection", "up", "festpos-drucker"]
    add = steps[1]
    assert add[:3] == ["connection", "add", "type"] and "192.168.56.12/24" in add and "56" in add
    assert add[add.index("ipv6.method") + 1] == "disabled" and add[add.index("ipv4.never-default") + 1] == "yes"
    assert h.plan(want, None)[0][:2] == ["connection", "add"]


def _run_helper(tmp_path, soll: str | None, show: str | None, on_add: str = ""):
    """festpos-druckernetz mit Ersatz-nmcli: «connection show» liefert show (None = Verbindung fehlt); on_add: Shell-
    Befehle, die beim «connection add» zusätzlich laufen."""
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    (tmp_path / "show").write_text(show or "")
    (bin_ / "nmcli").write_text(f'#!/bin/sh\necho "$*" >> {tmp_path}/calls\n'
                                f'case "$*" in *"connection show"*) [ -s {tmp_path}/show ] || exit 10; '
                                f'cat {tmp_path}/show;; *"connection add"*) {on_add or ":"};; esac\nexit 0\n')
    (bin_ / "nmcli").chmod(0o755)
    d = tmp_path / "dn"
    d.mkdir(exist_ok=True)
    if soll is not None:
        (d / "druckernetz.json").write_text(soll)
    (tmp_path / "env").write_text('NODE_IP="192.168.55.11"\nFESTPOS_IFACE="eth0"\nFESTPOS_NET_CIDR="24"\n')
    env = {"PATH": f"{bin_}:/usr/bin:/bin", "FESTPOS_PRINTER_NET_DIR": str(d), "FESTPOS_ENV_FILE": str(tmp_path / "env")}
    r = subprocess.run(["python3", str(HELPER)], env=env, capture_output=True, text=True)
    calls = (tmp_path / "calls").read_text().splitlines() if (tmp_path / "calls").exists() else []
    return r, calls


def test_helper_creates_connection(tmp_path):
    r, calls = _run_helper(tmp_path, '{"vlan": 56, "net": "192.168.56"}', None)
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls[0].startswith("-g vlan.parent,vlan.id") and len(calls) == 3
    assert calls[1].startswith("connection add type vlan con-name festpos-drucker ifname eth0.56 dev eth0 id 56 ")
    assert "ipv4.addresses 192.168.56.11/24" in calls[1]
    assert calls[2] == "connection up festpos-drucker"


def test_helper_leaves_matching_connection(tmp_path):
    r, calls = _run_helper(tmp_path, '{"vlan": 56, "net": "192.168.56"}', "eth0\n56\neth0.56\n192.168.56.11/24\nactivated\n")
    assert r.returncode == 0 and len(calls) == 1                                # nur nachgeschaut


def test_helper_removes_connection_when_off(tmp_path):
    r, calls = _run_helper(tmp_path, '{"vlan": 0, "net": ""}', "eth0\n56\neth0.56\n192.168.56.11/24\nactivated\n")
    assert r.returncode == 0 and calls[-1] == "connection delete festpos-drucker"


def test_helper_without_file_does_nothing(tmp_path):
    r, calls = _run_helper(tmp_path, None, None)
    assert r.returncode == 0 and len(calls) == 1                                # nie eingerichtet: nur nachgeschaut


def test_helper_follows_change_made_while_running(tmp_path):
    """Die App schreibt neu, während der Helfer noch arbeitet: systemd löst dann nicht unbedingt nochmals aus –
    der Helfer liest am Schluss nochmals und zieht nach (CodeRabbit PR #68)."""
    active = "eth0\n56\neth0.56\n192.168.56.11/24\nactivated\n"
    on_add = (f"printf '{active}' > {tmp_path}/show; "
              f"echo '{{\"vlan\": 0, \"net\": \"\"}}' > {tmp_path}/dn/druckernetz.json")
    r, calls = _run_helper(tmp_path, '{"vlan": 56, "net": "192.168.56"}', None, on_add)
    assert r.returncode == 0, r.stdout + r.stderr
    assert [c.split()[:2] for c in calls if not c.startswith("-g")] == [
        ["connection", "add"], ["connection", "up"], ["connection", "delete"]]


def test_helper_accepts_iface_with_dot():
    h = _helper()                                    # festpos-node.sh lässt IFACE mit Punkt zu (CodeRabbit PR #68)
    assert h.ziel({"vlan": 56, "net": "192.168.56"}, {**ENV, "FESTPOS_IFACE": "eth0.10"})["ifname"] == "eth0.10.56"


def test_helper_changes_nothing_on_bad_file(tmp_path):
    r, calls = _run_helper(tmp_path, '{"vlan": 56, "net": "192.168.55"}', "eth0\n56\neth0.56\n192.168.56.11/24\nactivated\n")
    assert r.returncode == 1 and calls == [] and "FEHLER" in r.stdout          # gleiches Netz wie die Pi


def test_units_installed():
    assert "PathChanged=/var/lib/festpos/druckernetz\n" in (DEPLOY / "systemd" / "festpos-druckernetz.path").read_text()
    svc = (DEPLOY / "systemd" / "festpos-druckernetz.service").read_text()
    assert "ExecStart=/usr/local/sbin/festpos-druckernetz\n" in svc and "WantedBy=multi-user.target" in svc
    lib = (DEPLOY / "install-lib.sh").read_text()
    assert '"festpos-druckernetz.py festpos-druckernetz 0755"' in lib
    assert "enable --now festpos-druckernetz.path" in lib and "enable festpos-druckernetz.service" in lib
    assert "d /var/lib/festpos/druckernetz " in (DEPLOY / "festpos-tmpfiles.conf").read_text()
    node = (DEPLOY / "festpos-node.sh").read_text()
    assert 'festpos_env_line FESTPOS_IFACE "$IFACE"' in node and 'festpos_env_line FESTPOS_NET_CIDR "$NET_CIDR"' in node
