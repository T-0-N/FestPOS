"""Gateway, DNS und Zeitserver für Pi und Handhelds: festpos.conf (GATEWAY, DNS, NTP), Start (festpos-conf.sh),
root-Helfer (festpos-netz --gateway), Verteilen an alle Pi (app/gateway.py) und die Internet-Logik je nach Art."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app import backup
from tests.test_api import AUTH

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "deploy" / "festpos-conf.sh"
HELPER = ROOT / "deploy" / "festpos-netz"
CONF = """# FestPOS
CLUSTER_SIZE=1
NET=192.168.55.0/24
VIP=192.168.55.10
NODE_A_IP=192.168.55.11
INTERNET_GATEWAY=192.168.55.1
IFACE=eth0
"""


def _gw(conf: str) -> dict:
    """festpos_gateway wie beim Start -> Variablen."""
    script = ('source "$1"; festpos_read_conf "$2" >/dev/null 2>&1; festpos_net_from_conf; festpos_gateway; rc=$?; '
              'for k in GW_ART PI_GW PI_DNS DHCP_GW DHCP_DNS_LIST NTP_SERVER; do printf "%s=%s\\n" "$k" "${!k}"; done; '
              'echo "rc=$rc"')
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
        f.write(conf)
    r = subprocess.run(["bash", "-c", script, "t", str(LIB), f.name], capture_output=True, text=True, timeout=30)
    Path(f.name).unlink()
    return dict(line.split("=", 1) for line in r.stdout.splitlines())


@pytest.mark.parametrize("extra, want", [
    ("", {"GW_ART": "auto", "PI_GW": "192.168.55.1", "PI_DNS": "192.168.55.1", "DHCP_GW": "auto:192.168.55.10",
          "DHCP_DNS_LIST": "1.1.1.1", "NTP_SERVER": "", "rc": "0"}),                      # wie bisher
    ("GATEWAY=192.168.55.254\nDNS=192.168.55.253, 9.9.9.9\nNTP=ntp.firma.local\n",
     {"GW_ART": "fest", "PI_GW": "192.168.55.254", "PI_DNS": "192.168.55.253,9.9.9.9", "DHCP_GW": "192.168.55.254",
      "DHCP_DNS_LIST": "192.168.55.253,9.9.9.9", "NTP_SERVER": "ntp.firma.local", "rc": "0"}),
    ("GATEWAY=192.168.55.254\n", {"GW_ART": "fest", "PI_DNS": "192.168.55.254", "DHCP_DNS_LIST": "192.168.55.254"}),
    ("GATEWAY=ohne\nNTP=10.1.1.1\n", {"GW_ART": "ohne", "PI_GW": "", "PI_DNS": "", "DHCP_GW": "ohne",
                                      "DHCP_DNS_LIST": "", "NTP_SERVER": "10.1.1.1", "rc": "0"}),
    ("GATEWAY=10.9.9.9\n", {"GW_ART": "auto", "PI_GW": "192.168.55.1", "rc": "1"}),         # nicht im Netz: automatisch
    ("GATEWAY=192.168.55.254\nDNS=1.1.1.1,2.2.2.2,3.3.3.3\n", {"GW_ART": "auto", "rc": "1"}),   # zu viele DNS
    ("NTP=ntp;rm -rf /\n", {"NTP_SERVER": "", "rc": "1"}),
])
def test_gateway_from_conf(extra, want):
    got = _gw(CONF + extra)
    assert {k: got.get(k) for k in want} == want


def test_dnsmasq_without_router_and_with_two_dns():
    def conf(gw, dns):
        r = subprocess.run(["bash", "-c", f'source "{LIB}"; festpos_dhcp_conf 192.168.55.0/24 eth0 150-189 "{gw}" "{dns}" a 1 '
                            '192.168.55.10'], capture_output=True, text=True)
        return r.returncode, r.stdout
    rc, out = conf("ohne", "")
    assert rc == 0 and "dhcp-option=option:router\n" in out and "dhcp-option=option:dns-server\n" in out
    rc, out = conf("192.168.55.254", "192.168.55.253,9.9.9.9")
    assert rc == 0 and "dhcp-option=option:router,192.168.55.254\n" in out
    assert "dhcp-option=option:dns-server,192.168.55.253,9.9.9.9\n" in out
    assert conf("192.168.55.254", "1.1.1.1,2.2.2.2,3.3.3.3")[0] != 0


# ------------------------------------------------------------------ root-Helfer
LOGGER = r'''#!/usr/bin/env python3
import os, sys
with open(os.environ["FAKE_LOG"], "a") as f: f.write(os.path.basename(sys.argv[0]) + " " + " ".join(sys.argv[1:]) + "\n")
if sys.argv[1:4] == ["-g", "GENERAL.CONNECTION", "device"]: print("festpos-eth0")
'''


@pytest.fixture
def pi(tmp_path):
    r, boot, tools = tmp_path / "root", tmp_path / "boot", tmp_path / "bin"
    for d in (boot, tools, r / "etc/festpos", r / "etc/chrony"):
        d.mkdir(parents=True)
    (boot / "festpos.conf").write_text(CONF)
    dnsmasq = subprocess.run(["bash", "-c", f'source "{LIB}"; festpos_dhcp_conf 192.168.55.0/24 eth0 150-189 '
                              'auto:192.168.55.10 1.1.1.1 a 1 192.168.55.10'], capture_output=True, text=True).stdout
    (r / "etc/festpos/dnsmasq.conf").write_text(dnsmasq)
    (r / "etc/chrony/chrony.conf").write_text("# generiert\npool ch.pool.ntp.org iburst maxsources 2\nlocal stratum 10 orphan\n")
    for name in ("systemctl", "nmcli", "firewall"):
        (tools / name).write_text(LOGGER)
        (tools / name).chmod(0o755)
    log = tmp_path / "log"
    env = {"PATH": "/usr/bin:/bin", "FESTPOS_RELEASE_LIB": str(ROOT), "FESTPOS_TEST_ROOT": str(r),
           "FESTPOS_BOOT": str(boot), "FESTPOS_SYSTEMCTL": str(tools / "systemctl"), "FESTPOS_NMCLI": str(tools / "nmcli"),
           "FESTPOS_FIREWALL_BIN": str(tools / "firewall"), "FESTPOS_CONF_LIB": str(LIB), "FAKE_LOG": str(log)}

    def run(job):
        p = subprocess.run([sys.executable, str(HELPER), "--gateway"], env=env, input=json.dumps(job), capture_output=True,
                           text=True, timeout=60)
        return p, json.loads((p.stdout.strip().splitlines() or ["{}"])[-1])
    return {"r": r, "boot": boot, "run": run, "log": log}


def test_helper_writes_conf_and_applies_without_restart(pi):
    p, res = pi["run"]({"mode": "fest", "gateway": "192.168.55.254", "dns": ["192.168.55.253"], "ntp": "ntp.firma.local",
                        "rev": 3})
    assert res["ok"] and res["rev"] == 3, p.stderr
    assert res["applied"] == {"mode": "fest", "gateway": "192.168.55.254", "dns": ["192.168.55.253"], "ntp": "ntp.firma.local"}
    conf = (pi["boot"] / "festpos.conf").read_text()
    assert backup.conf_value(conf, "GATEWAY") == "192.168.55.254" and backup.conf_value(conf, "DNS") == "192.168.55.253"
    assert backup.conf_value(conf, "NTP") == "ntp.firma.local" and backup.conf_value(conf, "INTERNET_GATEWAY") == "192.168.55.1"
    dns = (pi["r"] / "etc/festpos/dnsmasq.conf").read_text()
    assert "dhcp-option=option:router,192.168.55.254\n" in dns and "dhcp-optsdir" not in dns
    assert "dhcp-option=option:dns-server,192.168.55.253\n" in dns and dns.count("dhcp-option=option:router") == 1
    chrony = (pi["r"] / "etc/chrony/chrony.conf").read_text()
    assert "server ntp.firma.local iburst\n" in chrony and "pool " not in chrony and "local stratum" in chrony
    log = pi["log"].read_text()
    assert "nmcli connection modify festpos-eth0 ipv4.gateway 192.168.55.254 ipv4.dns 192.168.55.253" in log
    assert "nmcli device reapply eth0" in log and "reboot" not in log
    assert "systemctl try-restart festpos-dhcp.service" in log and "systemctl try-restart chrony.service" in log
    # geschlossenes Netz, dann wieder automatisch (wie beim Start: INTERNET_GATEWAY, Gateway der Handhelds nach App)
    p, res = pi["run"]({"mode": "ohne", "rev": 4})
    assert res["ok"] and res["applied"]["mode"] == "ohne", p.stderr
    dns = (pi["r"] / "etc/festpos/dnsmasq.conf").read_text()
    assert "dhcp-option=option:router\n" in dns and "dhcp-option=option:dns-server\n" in dns
    assert "pool ch.pool.ntp.org" in (pi["r"] / "etc/chrony/chrony.conf").read_text()
    p, res = pi["run"]({"mode": "auto", "rev": 5})
    conf = (pi["boot"] / "festpos.conf").read_text()
    assert res["applied"] == {"mode": "auto", "gateway": "192.168.55.1", "dns": ["192.168.55.1"], "ntp": ""}
    assert backup.conf_value(conf, "GATEWAY") is None and backup.conf_value(conf, "DNS") is None
    assert "dhcp-optsdir=" in (pi["r"] / "etc/festpos/dnsmasq.conf").read_text()


@pytest.mark.parametrize("job, code", [
    ({"mode": "fest", "gateway": "10.9.9.9"}, "gw_outside"),
    ({"mode": "fest", "gateway": "x"}, "gw_bad"),
    ({"mode": "fest", "gateway": "192.168.55.254", "dns": ["1.1.1.1", "2.2.2.2", "3.3.3.3"]}, "gw_bad"),
    ({"mode": "auto", "ntp": "a b"}, "gw_ntp"),
    ({"mode": "alles"}, "job"),
])
def test_helper_refuses_bad_jobs(pi, job, code):
    p, res = pi["run"](job)
    assert res == {"ok": False, "code": code}, p.stderr
    assert (pi["boot"] / "festpos.conf").read_text() == CONF


def test_helper_waits_during_net_move(pi):
    st = pi["r"] / "var/lib/festpos-netz/umzug.json"
    st.parent.mkdir(parents=True)
    st.write_text(json.dumps({"phase": "umgestellt"}))
    p, res = pi["run"]({"mode": "ohne", "rev": 1})
    assert res == {"ok": False, "code": "running"}
    assert (pi["boot"] / "festpos.conf").read_text() == CONF


# ------------------------------------------------------------------ App
def _confirmed_admin(client):
    """Admin mit frischer Bestätigung (kritische Aktion)."""
    return AUTH


def test_save_needs_confirmation_and_checks_values(client):
    tok = client.post("/api/admin/login", json={"name": "admin", "password": "geheim"}).json()["token"]
    body = {"mode": "fest", "gateway": "192.168.55.254", "dns": ["192.168.55.253"], "ntp": "ntp.firma.local"}
    assert client.put("/api/admin/gateway", headers={"Authorization": f"Bearer {tok}"}, json=body).status_code == 428
    for bad, text in (({**body, "gateway": "10.1.1.1"}, "nicht im FestPOS-Netz"),
                      ({**body, "gateway": "192.168.55.10"}, "Adresse für die Handhelds oder ein Pi"),
                      ({**body, "gateway": "192.168.55.160"}, "DHCP-Bereich"),
                      ({**body, "ntp": "a b"}, "Zeitserver")):
        r = client.put("/api/admin/gateway", auth=AUTH, json=bad)
        assert r.status_code == 422 and text in r.json()["detail"], (bad, r.text)
    r = client.put("/api/admin/gateway", auth=AUTH, json=body)
    assert r.status_code == 200 and r.json()["current"] == {"mode": "fest", "gateway": "192.168.55.254",
                                                             "dns": ["192.168.55.253"], "ntp": "ntp.firma.local"}
    assert r.json()["wanted_rev"] == 1
    assert client.put("/api/admin/gateway", auth=AUTH, json={"mode": "ohne"}).json()["wanted_rev"] == 2
    audit = json.dumps(client.get("/api/admin/audit", auth=AUTH).json())
    assert "net_gateway" in audit
    chk = client.post("/api/admin/gateway/pruefen", auth=AUTH, json={**body, "gateway": "10.1.1.1"}).json()
    assert chk["errors"] == ["gw_outside"] and chk["probe"] is None


def test_each_pi_applies_the_wanted_state_once(client, monkeypatch):
    from app import cards, core, gateway
    monkeypatch.setattr(core.Config, "node", "a")
    jobs = []
    monkeypatch.setattr(cards, "available", lambda: True)
    monkeypatch.setattr(cards, "submit", lambda kind, payload=None: jobs.append((kind, payload)) or str(len(jobs)))
    answer = [{"ok": False, "code": "conf"}]
    monkeypatch.setattr(cards, "wait", lambda job, timeout=30: answer[0])
    gateway.tick()
    assert jobs == []                                                       # nichts gewünscht: nichts zu tun
    client.put("/api/admin/gateway", auth=AUTH, json={"mode": "fest", "gateway": "192.168.55.254"})
    gateway.tick()
    assert jobs[-1] == ("netz_gateway", {"mode": "fest", "gateway": "192.168.55.254", "dns": ["192.168.55.254"],
                                         "ntp": "", "rev": 1})
    assert gateway.node_state()["ok"] is False and gateway.node_state()["code"] == "conf"
    gateway.tick()
    assert len(jobs) == 1                                                   # nochmals erst nach einer Minute
    gateway._last[0] = 0.0
    answer[0] = {"ok": True, "rev": 1, "applied": {"mode": "fest", "gateway": "192.168.55.254",
                                                   "dns": ["192.168.55.254"], "ntp": ""}}
    gateway.tick()
    st = gateway.node_state()
    assert len(jobs) == 2 and st["ok"] and st["rev"] == 1 and gateway.mine()["gateway"] == "192.168.55.254"
    gateway._last[0] = 0.0
    gateway.tick()
    assert len(jobs) == 2                                                   # übernommen: nicht nochmals
    v = client.get("/api/admin/gateway", auth=AUTH).json()
    assert v["nodes"]["a"]["ok"] and v["nodes"]["a"]["rev"] == 1


def test_internet_follows_the_mode(client, monkeypatch):
    from app import core, release, update_net
    core.rt.store.execute(update_net.UPSERT if hasattr(update_net, "UPSERT") else
                          "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          ("update_net", json.dumps({"via": "wlan", "ssid": "Fest", "gateway": "", "dns": ""})))
    assert update_net._cfg()["via"] == "wlan"
    client.put("/api/admin/gateway", auth=AUTH, json={"mode": "fest", "gateway": "192.168.55.254", "dns": ["9.9.9.9"]})
    assert {k: update_net._cfg()[k] for k in ("via", "gateway", "dns")} == {"via": "lan", "gateway": "192.168.55.254",
                                                                            "dns": "9.9.9.9"}
    assert client.get("/api/admin/update/net", auth=AUTH).json()["gw_mode"] == "fest"
    client.put("/api/admin/gateway", auth=AUTH, json={"mode": "ohne"})
    core.rt.store.execute("INSERT INTO settings(key,value) VALUES ('net_keep','1') ON CONFLICT(key) DO UPDATE SET value='1'")
    assert not update_net.allowed() and not update_net.keep_on()
    with pytest.raises(release.ReleaseError) as e:
        update_net._net_up({"via": "lan"}, "")
    assert e.value.code == "net_none"
    r = client.post("/api/admin/update/net/check", auth=AUTH)
    assert r.status_code == 409 and "geschlossen" in r.json()["detail"]


def test_setup_checks_and_writes_gateway():
    from app import netz, setup_mode
    from fastapi import HTTPException
    base = {"net": "10.20.0.0/22", "vip": "10.20.0.10", "a": "10.20.0.11", "b": "10.20.0.12", "c": "10.20.0.13",
            "dhcp": True, "cluster_size": 1}
    assert setup_mode.net_values({**base, "gw_mode": "fest", "gw": "10.20.3.254"})["gw"] == "10.20.3.254"
    for bad, text in (({"gw_mode": "fest", "gw": "10.20.0.150"}, "DHCP-Bereich"), ({"gw_mode": "fest", "gw": "x"}, "IPv4"),
                      ({"gw_ntp": "a b"}, "Zeitserver")):
        with pytest.raises(HTTPException) as e:
            setup_mode.net_values({**base, **bad})
        assert e.value.status_code == 422 and text in e.value.detail
    assert netz.gw_conf("fest", "10.20.3.254", ["10.20.3.253", ""], "ntp") == {
        "GATEWAY": "10.20.3.254", "DNS": "10.20.3.253", "NTP": "ntp"}
    assert netz.gw_conf("ohne", "", [], "") == {"GATEWAY": "ohne", "DNS": "", "NTP": ""}


def test_helper_reports_failures_and_retries_pending_restart(pi, tmp_path):
    """Scheitert NetworkManager oder ein Neustart: kein «ok» (die App versucht es nochmals); ein vorgemerkter Neustart
    wird beim nächsten Mal nachgeholt, auch wenn die Datei schon stimmt."""
    tools = tmp_path / "bin"
    nm = tools / "nmcli"
    nm.write_text(LOGGER + 'if sys.argv[1:3] == ["connection", "modify"]: sys.exit(1)\n')
    p, res = pi["run"]({"mode": "fest", "gateway": "192.168.55.254", "rev": 1})
    assert res == {"ok": False, "code": "gw_nm"}, p.stderr
    nm.write_text(LOGGER)
    sc = tools / "systemctl"
    sc.write_text(LOGGER + 'if "try-restart" in sys.argv: sys.exit(1)\n')
    p, res = pi["run"]({"mode": "fest", "gateway": "192.168.55.254", "rev": 1})
    assert res == {"ok": False, "code": "gw_restart"}, p.stderr
    assert "option:router,192.168.55.254" in (pi["r"] / "etc/festpos/dnsmasq.conf").read_text()   # Datei schon neu
    sc.write_text(LOGGER)
    pi["log"].write_text("")
    p, res = pi["run"]({"mode": "fest", "gateway": "192.168.55.254", "rev": 1})
    assert res["ok"], p.stderr
    assert "systemctl try-restart festpos-dhcp.service" in pi["log"].read_text()       # nachgeholt
    pi["log"].write_text("")
    pi["run"]({"mode": "fest", "gateway": "192.168.55.254", "rev": 1})
    assert "try-restart" not in pi["log"].read_text()                                # nichts mehr offen


def test_restore_from_other_net_moves_fixed_router(client):
    """Sicherung aus einem anderen Netz (net_guard -> translate ohne Plan): fester Router und DNS umgerechnet."""
    from app import gateway, umzug
    client.put("/api/admin/gateway", auth=AUTH, json={"mode": "fest", "gateway": "192.168.55.254",
                                                       "dns": ["192.168.55.253", "9.9.9.9"]})
    undo = umzug.translate({"old": {"net": "192.168.55.0/24"}, "new": {"net": "10.20.0.0/22"}})
    w = gateway.wanted()
    assert w["gateway"] == "10.20.0.254" and w["dns"] == ["10.20.0.253", "9.9.9.9"] and w["rev"] == 2
    umzug.untranslate(undo)
    assert gateway.wanted()["gateway"] == "192.168.55.254"


def test_keep_reconnects_when_the_way_changes(client, monkeypatch):
    """Dauerhaft über WLAN verbunden, dann fester Router: die alte Verbindung wird abgebaut und neu verbunden."""
    from app import core, main, update_net
    monkeypatch.setattr(main, "_keep_tick", lambda: None)               # nur dieser Test verbindet
    core.rt.store.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          ("update_net", json.dumps({"via": "wlan", "ssid": "Fest"})))
    core.rt.store.execute("INSERT INTO settings(key,value) VALUES ('net_keep','1') ON CONFLICT(key) DO UPDATE SET value='1'")
    calls = []
    monkeypatch.setattr(update_net, "_net_down", lambda force=False: calls.append("down"))
    monkeypatch.setattr(update_net, "_net_up", lambda cfg, psk, **kw: calls.append(("up", cfg.get("via"), cfg.get("gateway"))) or "eth0")
    monkeypatch.setattr(update_net, "_probe", lambda timeout=5.0: None)
    monkeypatch.setattr(update_net, "_keep", {"state": "up", "via": "wlan", "want": update_net._want(), "since": 1})
    update_net.keep_tick()
    assert calls == []                                                       # gleicher Weg: bleibt
    client.put("/api/admin/gateway", auth=AUTH, json={"mode": "fest", "gateway": "192.168.55.254"})
    update_net.keep_tick()
    assert calls == ["down", ("up", "lan", "192.168.55.254")]
