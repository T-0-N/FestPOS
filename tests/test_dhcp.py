"""DHCP für die Handys (Version 2026.10.10-44): feste Zuteilungen für dnsmasq aus der Verwaltung, MAC aus der
ARP-Tabelle, Konfiguration aus festpos.conf (deploy/festpos-conf.sh). Ohne echten dnsmasq."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_api import AUTH

LIB = Path(__file__).resolve().parent.parent / "deploy" / "festpos-conf.sh"


def test_hosts_file_from_phone_list():
    from app import dhcp
    devices = [
        {"name": "XCover 101", "kind": "phone", "ip": "192.168.55.101", "mac": "AA-BB-CC-DD-EE-01"},
        {"name": "Ohne MAC", "kind": "phone", "ip": "192.168.55.102"},
        {"name": "Switch", "kind": "net", "ip": "192.168.55.30", "mac": "aa:bb:cc:dd:ee:02"},       # kein Handy
        {"name": "Doppelt IP", "kind": "phone", "ip": "192.168.55.101", "mac": "aa:bb:cc:dd:ee:03"},
        {"name": "Doppelt MAC", "kind": "phone", "ip": "192.168.55.104", "mac": "aa:bb:cc:dd:ee:01"},
        {"name": "Kaputt", "kind": "phone", "ip": "192.168.55.105", "mac": "aa:bb:cc:dd:ee:0"},
        {"name": "Name", "kind": "phone", "ip": "festpos.local", "mac": "aa:bb:cc:dd:ee:06"},
        {"name": "Ümlaut & Co", "kind": "phone", "ip": "192.168.55.107", "mac": "aa:bb:cc:dd:ee:07"},
    ]
    lines = dhcp.hosts(devices).splitlines()
    assert lines[0].startswith("#")
    assert lines[1:] == ["aa:bb:cc:dd:ee:01,192.168.55.101,xcover-101,1h", "aa:bb:cc:dd:ee:07,192.168.55.107,mlaut-co,1h"]
    assert dhcp.mac("00:00:00:00:00:00") is None and dhcp.mac("x") is None


def test_sync_writes_only_when_changed(tmp_path, monkeypatch, client):
    from app import core, dhcp
    monkeypatch.setattr(dhcp, "HOSTS_DIR", tmp_path)
    monkeypatch.setattr(dhcp, "_written", None)
    core.rt.store.execute("INSERT INTO settings(key,value) VALUES ('monitor_devices', ?) ON CONFLICT(key) DO UPDATE "
                          "SET value=excluded.value", (json.dumps([{"name": "A", "kind": "phone", "ip": "192.168.55.101",
                                                                     "mac": "aa:bb:cc:dd:ee:01"}]),))
    dhcp.sync()
    f = tmp_path / "festpos-handys"
    assert "aa:bb:cc:dd:ee:01,192.168.55.101,a,1h" in f.read_text()
    f.write_text("von Hand")                        # unverändert in der Datenbank: nicht neu schreiben
    dhcp.sync()
    assert f.read_text() == "von Hand"
    assert not list(tmp_path.glob(".*"))            # keine halbe Datei liegen geblieben
    monkeypatch.setattr(dhcp, "HOSTS_DIR", tmp_path / "fehlt")
    dhcp.sync()                                     # DHCP aus: nichts, kein Fehler


def test_mac_from_arp_table(tmp_path):
    from app import dhcp
    arp = tmp_path / "arp"
    arp.write_text("IP address       HW type     Flags       HW address            Mask     Device\n"
                   "192.168.55.150   0x1         0x2         6a:11:22:33:44:55     *        eth0\n"
                   "192.168.55.151   0x1         0x0         00:00:00:00:00:00     *        eth0\n")
    assert dhcp.mac_for_ip("192.168.55.150", arp) == "6a:11:22:33:44:55"
    assert dhcp.mac_for_ip("192.168.55.151", arp) is None          # unvollständiger Eintrag
    assert dhcp.mac_for_ip("192.168.55.152", arp) is None
    assert dhcp.mac_for_ip("192.168.55.150", tmp_path / "fehlt") is None


def test_monitor_list_keeps_mac_and_ignores_a_forged_one(client):
    from app import core
    core.rt.store.execute("INSERT INTO settings(key,value) VALUES ('monitor_devices', ?) ON CONFLICT(key) DO UPDATE "
                          "SET value=excluded.value", (json.dumps([{"name": "XCover 1", "kind": "phone", "ip": "192.168.55.101",
                                                                     "port": None, "mac": "aa:bb:cc:dd:ee:01"}]),))
    plain = [{"name": "XCover 1", "kind": "phone", "ip": "192.168.55.101", "port": None}]
    client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": plain})
    assert json.loads(core.rt.store.setting("monitor_devices"))[0]["mac"] == "aa:bb:cc:dd:ee:01"
    client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": [{**plain[0], "mac": "11:22:33:44:55:66"}]})
    assert json.loads(core.rt.store.setting("monitor_devices"))[0]["mac"] == "aa:bb:cc:dd:ee:01"


@pytest.mark.skipif(not shutil.which("bash"), reason="bash fehlt")
@pytest.mark.parametrize("args,ok,want", [
    (("192.168.55", "24", "eth0", "150-189", "192.168.55.10", "", "a", "1"), True,
     ["dhcp-range=192.168.55.150,192.168.55.189,255.255.255.0,2m", "dhcp-option=option:router,192.168.55.10",
      "dhcp-option=option:dns-server", "interface=eth0", "port=0", "dhcp-hostsdir=/var/lib/festpos/dhcp"]),
    (("192.168.55", "24", "eth0", "150-189", "192.168.55.1", "1.1.1.1", "a", "3"), True,
     ["dhcp-range=192.168.55.150,192.168.55.162,255.255.255.0,2m", "dhcp-option=option:dns-server,1.1.1.1"]),
    (("192.168.55", "24", "eth0", "150-189", "192.168.55.10", "", "b", "3"), True,
     ["dhcp-range=192.168.55.163,192.168.55.175,255.255.255.0,2m"]),
    (("192.168.55", "24", "eth0", "150-189", "192.168.55.10", "", "c", "3"), True,
     ["dhcp-range=192.168.55.176,192.168.55.189,255.255.255.0,2m"]),
    (("10.0", "16", "wlan0", "10-20", "10.0.0.1", "", "a", "1"), True, ["dhcp-range=10.0.10,10.0.20,255.255.0.0,2m"]),
    (("192.168.55", "24", "eth0", "150-189", "auto:192.168.55.10", "", "a", "1"), True,    # Gateway folgt dem Internet
     ["dhcp-optsdir=/var/lib/festpos/dhcp-opts", "dhcp-hostsdir=/var/lib/festpos/dhcp"]),
    (("192.168.55", "24", "eth0", "150-189", "auto:kein-gateway", "", "a", "1"), False, []),
    (("192.168.55", "24", "eth0", "189-150", "192.168.55.10", "", "a", "1"), False, []),
    (("192.168.55", "24", "eth0", "150-189", "kein-gateway", "", "a", "1"), False, []),
    (("192.168.55", "24", "eth0", "150-189", "192.168.55.10", "1.1.1", "a", "1"), False, []),
    (("192.168.55", "24", "eth0", "150-151", "192.168.55.10", "", "a", "3"), False, []),      # zu klein für 3 Pi
])
def test_dnsmasq_conf(args, ok, want):
    q = " ".join(f'"{a}"' for a in args)
    r = subprocess.run(["bash", "-c", f'source "{LIB}"; festpos_dhcp_conf {q}'], capture_output=True, text=True)
    assert (r.returncode == 0) == ok, r.stderr
    for line in want:
        assert line in r.stdout.splitlines(), (line, r.stdout)
    if args[4].startswith("auto:") and ok:
        assert "option:router" not in r.stdout                                  # kommt aus der Datei der App


def test_gateway_follows_the_way_to_the_internet(tmp_path, monkeypatch, client):
    """ohne DHCP_GATEWAY bekommen die Handhelds als Gateway den Router, wenn die Pi dauerhaft über ihn
    verbunden sind – sonst die VIP (die Pi geben weiter). Die Datei wird nur bei einer Änderung geschrieben."""
    from app import dhcp, update_net
    conf = tmp_path / "dnsmasq.conf"
    conf.write_text("dhcp-optsdir=/var/lib/festpos/dhcp-opts\n")
    monkeypatch.setattr(dhcp, "CONF", conf)
    monkeypatch.setattr(dhcp, "OPTS_DIR", tmp_path / "opts")
    monkeypatch.setattr(dhcp, "_gw_written", None)
    net = {"cfg": {}, "keep": False}
    monkeypatch.setattr(update_net, "_raw", lambda: net["cfg"])
    monkeypatch.setattr(update_net, "keep_on", lambda: net["keep"])
    dhcp.gateway_sync()
    assert not (tmp_path / "opts").exists()                                    # ohne Ordner (festpos-node.sh): nichts
    (tmp_path / "opts").mkdir()
    f = tmp_path / "opts" / dhcp.OPTS_FILE
    dhcp.gateway_sync()
    assert f.read_text() == f"option:router,{dhcp.VIP}\n" and dhcp.current_gateway() == dhcp.VIP
    net["cfg"] = {"via": "lan", "gateway": "192.168.55.1", "dns": "1.1.1.1"}
    dhcp.gateway_sync()
    assert f.read_text() == f"option:router,{dhcp.VIP}\n"                     # Schalter (c) aus: weiter die VIP
    net["keep"] = True
    dhcp.gateway_sync()
    assert f.read_text() == "option:router,192.168.55.1\n" and dhcp.current_gateway() == "192.168.55.1"
    net["cfg"] = {"via": "wlan", "ssid": "Hotspot"}
    dhcp.gateway_sync()
    assert f.read_text() == f"option:router,{dhcp.VIP}\n"                     # WLAN des Pi: über die VIP
    conf.write_text("dhcp-option=option:router,192.168.55.254\n")              # fest aus festpos.conf: gilt immer
    assert dhcp.current_gateway() == "192.168.55.254" and not dhcp.auto_gateway()


RELOAD = LIB.parent / "festpos-dhcp-reload.py"


def _reload_env(tmp_path, active=True):
    """Ersatz für systemctl (schreibt die Aufrufe mit) und die Dateien von dnsmasq."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "systemctl").write_text(f'#!/bin/sh\necho "$*" >> {tmp_path}/calls\n'
                                    f'[ "$1" = is-active ] && exit {0 if active else 3}\nexit 0\n')
    (bin_ / "systemctl").chmod(0o755)
    (tmp_path / "dhcp").mkdir()
    (tmp_path / "dhcp" / "festpos-handys").write_text(
        "# FestPOS\n6a:00:00:00:00:0b,192.168.55.104,neu,12h\naa:bb:cc:dd:ee:01,192.168.55.101,xcover-101,12h\n")
    return {"PATH": f"{bin_}:/usr/bin:/bin", "FESTPOS_DHCP_DIR": str(tmp_path / "dhcp"),
            "FESTPOS_DHCP_LEASES": str(tmp_path / "leases"), "FESTPOS_DIENST_USER": ""}


def _calls(tmp_path):
    f = tmp_path / "calls"
    return f.read_text().splitlines() if f.exists() else []


def test_reload_drops_lease_of_reassigned_address(tmp_path):
    """Handy zurückgesetzt (neue MAC) und mit derselben Adresse neu ausgerollt: die Lease der alten MAC hält
    dnsmasq sonst bis zu 12 Stunden fest («not using configured address … leased to …»). Nachgestellt mit echtem
    dnsmasq 2.91 – dort auch: gelöschte Zuteilungen vergisst dnsmasq erst mit SIGHUP («duplicate dhcp-host»)."""
    env = _reload_env(tmp_path)
    leases = ["1793038719 6a:00:00:00:00:0a 192.168.55.104 alt 01:6a:00:00:00:00:0a",     # alte MAC: weg
              "1793038720 aa:bb:cc:dd:ee:01 192.168.55.101 xcover-101 *",                  # passt: bleibt
              "1793038721 6a:00:00:00:00:0b 192.168.55.183 * *"]                           # Bereich: bleibt
    (tmp_path / "leases").write_text("\n".join(leases) + "\n")
    r = subprocess.run(["python3", str(RELOAD)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "leases").read_text().splitlines() == leases[1:]
    assert _calls(tmp_path) == ["is-active --quiet festpos-dhcp.service", "stop festpos-dhcp.service",
                                "start festpos-dhcp.service"]
    assert "6a:00:00:00:00:0a" in r.stdout


def test_reload_only_reloads_without_stale_lease(tmp_path):
    env = _reload_env(tmp_path)
    (tmp_path / "leases").write_text("1793038720 AA:BB:CC:DD:EE:01 192.168.55.101 xcover-101 *\n")
    subprocess.run(["python3", str(RELOAD)], env=env, check=True)
    assert _calls(tmp_path) == ["is-active --quiet festpos-dhcp.service", "reload festpos-dhcp.service"]
    assert "192.168.55.101" in (tmp_path / "leases").read_text()


def test_reload_never_writes_through_a_link(tmp_path):
    """Review 2026-11, S3: die Lease-Datei liegt im Ordner des Dienstbenutzers. Ist sie eine Verknüpfung auf eine
    fremde Datei (wer die App übernimmt, könnte sie so legen), darf der root-Helfer das Ziel weder kürzen noch
    umschreiben – und dnsmasq läuft danach trotzdem wieder."""
    env = _reload_env(tmp_path)
    fremd = tmp_path / "fremd"
    inhalt = "auth requisite 192.168.55.104 pam_deny.so\nzweite Zeile bleibt\n"
    fremd.write_text(inhalt)
    (tmp_path / "leases").symlink_to(fremd)
    r = subprocess.run(["python3", str(RELOAD)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr                       # der Helfer läuft durch, statt abzubrechen
    assert fremd.read_text() == inhalt and (tmp_path / "leases").is_symlink()
    assert "stop festpos-dhcp.service" not in _calls(tmp_path) or _calls(tmp_path)[-1] == "start festpos-dhcp.service"
    assert "192.168.55.104" not in r.stdout                  # auch nichts daraus ins Journal
    # ebenso die Zuteilungen: eine Verknüpfung wird nicht gelesen (dann gilt: keine festen Zuteilungen)
    (tmp_path / "leases").unlink()
    (tmp_path / "leases").write_text("1793038719 6a:00:00:00:00:0a 192.168.55.104 alt *\n")
    hosts = tmp_path / "dhcp" / "festpos-handys"
    echt = tmp_path / "echt"
    hosts.rename(echt)
    hosts.symlink_to(echt)
    subprocess.run(["python3", str(RELOAD)], env=env, check=True)
    assert "192.168.55.104" in (tmp_path / "leases").read_text()


def test_reload_does_nothing_when_dhcp_is_off(tmp_path):
    env = _reload_env(tmp_path, active=False)
    subprocess.run(["python3", str(RELOAD)], env=env, check=True)        # auch ohne Lease-Datei
    assert _calls(tmp_path) == ["is-active --quiet festpos-dhcp.service"]


def test_reload_units_installed():
    deploy = LIB.parent
    assert "PathChanged=/var/lib/festpos/dhcp\n" in (deploy / "systemd" / "festpos-dhcp-reload.path").read_text()
    assert "ExecReload=/bin/kill -HUP $MAINPID" in (deploy / "systemd" / "festpos-dhcp.service").read_text()
    lib = (deploy / "install-lib.sh").read_text()
    assert '"festpos-dhcp-reload.py festpos-dhcp-reload 0755"' in lib
    assert "enable --now festpos-dhcp-reload.path" in lib
