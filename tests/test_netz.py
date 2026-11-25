"""Netz bei der Ersteinrichtung frei wählen (T-0-N/FestPOS#2, Teil 1): Prüfung, Vorschläge, festpos.conf,
Zertifizierungsstelle genau für das Netz, festpos-node und make-certs mit NET – auch 10.20.0.0/22 und 172.16.5.0/26."""
from __future__ import annotations

import ipaddress
import subprocess
from pathlib import Path

import pytest
from cryptography import x509

from app import backup, netz, zertifikate
from tests.test_einrichtung import _token, setup  # noqa: F401  (setup = Fixture)

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "deploy" / "festpos-conf.sh"
V22 = {"net": "10.20.0.0/22", "vip": "10.20.0.10", "a": "10.20.0.11", "b": "10.20.0.12", "c": "10.20.0.13",
       "dhcp_from": "10.20.1.1", "dhcp_to": "10.20.3.200", "dhcp": True, "cluster_size": 3, "node": "b",
       "rtc_akku": False, "tz": "Europe/Zurich"}


def test_defaults_for_several_sizes():
    assert netz.defaults("10.20.0.0/22") == {"net": "10.20.0.0/22", "vip": "10.20.0.10", "a": "10.20.0.11",
                                             "b": "10.20.0.12", "c": "10.20.0.13", "dhcp_from": "10.20.0.150",
                                             "dhcp_to": "10.20.0.189"}
    d = netz.defaults("172.16.5.0/26")
    assert (d["vip"], d["dhcp_from"], d["dhcp_to"]) == ("172.16.5.10", "172.16.5.32", "172.16.5.62")
    assert netz.defaults("unsinn")["net"] == netz.DEFAULT
    assert netz.parse("10.20.0.5/22") is None                          # nur die Netzadresse


@pytest.mark.parametrize("change, err", [
    ({}, []),
    ({"net": "10.20.0.0/15"}, "size"), ({"net": "10.20.0.0/28"}, "size"), ({"net": "8.8.0.0/22"}, "size"),
    ({"net": "127.0.0.0/24", "vip": "127.0.0.10", "a": "127.0.0.11", "b": "127.0.0.12", "c": "127.0.0.13",
      "dhcp_from": "127.0.0.150", "dhcp_to": "127.0.0.189"}, "size"),                 # Loopback ist nicht RFC 1918
    ({"net": "10.20.0.1/22"}, "net"),
    ({"vip": "10.20.4.10"}, "ip_outside"), ({"a": "10.20.0.0"}, "ip_outside"), ({"b": "10.20.0.11"}, "ip_twice"),
    ({"vip": "zehn"}, "ip_bad"),
    ({"dhcp_from": "10.20.3.200", "dhcp_to": "10.20.1.1"}, "dhcp_outside"),
    ({"dhcp_from": "10.20.1.1", "dhcp_to": "10.20.1.5"}, "dhcp_small"),
    ({"dhcp_from": "10.20.0.1", "dhcp_to": "10.20.0.100"}, "dhcp_overlap"),
    ({"net": "192.168.0.0/16", "vip": "192.168.1.10", "a": "192.168.1.11", "b": "192.168.1.12", "c": "192.168.1.13",
      "dhcp_from": "192.168.2.1", "dhcp_to": "192.168.2.99"}, "overlap_printer"),
])
def test_plan_errors(change, err):
    v = {**V22, **change}
    got = netz.plan_errors(v["net"], v["vip"], v["a"], v["b"], v["c"], v["dhcp_from"], v["dhcp_to"], 3)
    assert (got == []) if err == [] else (got and got[0] == err), got


def test_other_net_and_without_dhcp():
    ips = {k: V22[k] for k in ("net", "vip", "a", "b", "c")}
    assert netz.plan_errors(**ips, dhcp=False) == []                       # ohne DHCP: kein Bereich nötig
    assert netz.plan_errors(**ips, dhcp=False, avoid=["10.20.2.0/24"]) == ["overlap_other"]     # z.B. Router


def test_setup_address_and_printer_net():
    d = netz.defaults("192.168.55.0/24")
    assert netz.plan_errors(**{k: d[k] for k in ("net", "vip", "a", "b", "c", "dhcp_from", "dhcp_to")}) == []
    d = netz.defaults("192.168.48.0/20")                              # enthält 192.168.55.10 und das Druckernetz
    errs = netz.plan_errors(**{k: d[k] for k in ("net", "vip", "a", "b", "c", "dhcp_from", "dhcp_to")})
    assert "overlap_printer" in errs and "overlap_setup" in errs


def test_conf_lines():
    c = netz.conf_lines(**{k: V22[k] for k in ("net", "vip", "a", "b", "c", "dhcp_from", "dhcp_to")})
    assert c == {"NET": "10.20.0.0/22", "NET_PREFIX": "10.20.0", "NET_CIDR": "22", "VIP": "10.20.0.10",
                 "NODE_A_IP": "10.20.0.11", "NODE_B_IP": "10.20.0.12", "NODE_C_IP": "10.20.0.13",
                 "DHCP_POOL": "10.20.1.1-10.20.3.200", "SSH_FROM": "10.20.0.200-10.20.0.254"}
    assert netz.conf_lines("172.16.5.0/26", "172.16.5.10", "172.16.5.11", "172.16.5.12", "172.16.5.13",
                           "172.16.5.32", "172.16.5.62")["SSH_FROM"] == ""        # klein: SSH aus dem ganzen Netz


def test_ca_only_for_the_chosen_net():
    made = zertifikate.make_all("10.20.0.0/22", ["10.20.0.10", "10.20.3.13"])
    ca = x509.load_pem_x509_certificate(made["ca.crt"])
    nc = ca.extensions.get_extension_for_class(x509.NameConstraints).value
    nets = {str(x.value) for x in nc.permitted_subtrees if isinstance(x, x509.IPAddress)}
    assert "10.20.0.0/22" in nets and "192.168.55.0/24" not in nets
    with pytest.raises(ValueError):
        zertifikate.make_all("10.20.0.0/22", ["10.20.4.1"])


def test_setup_writes_net_and_refuses_bad_one(setup):  # noqa: F811
    c, boot = setup["c"], setup["boot"]
    tok = _token(c)
    body = {"token": tok, "admin_password": "Theater-Fest-2026"}
    r = c.post("/api/einrichtung/neu", json={**body, "values": {**V22, "cluster_size": 1, "node": "a", "vip": "10.20.9.1"}})
    assert r.status_code == 422 and "10.20.0.0/22" in r.json()["detail"]
    r = c.post("/api/einrichtung/neu", json={**body, "values": {**V22, "cluster_size": 1, "node": "a"}})
    assert r.status_code == 200, r.text
    conf = (boot / "festpos.conf").read_text()
    get = lambda k: backup.conf_value(conf, k)  # noqa: E731
    assert get("NET") == "10.20.0.0/22" and get("NET_CIDR") == "22" and get("NET_PREFIX") == "10.20.0"
    assert get("DHCP_POOL") == "10.20.1.1-10.20.3.200" and get("VIP") == "10.20.0.10"
    srv = x509.load_pem_x509_certificate((boot / "festpos-tls" / "server.crt").read_bytes())
    sans = {str(i) for i in srv.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            .get_values_for_type(x509.IPAddress)}
    assert {"10.20.0.10", "10.20.0.11"} <= sans


def test_old_prefix_form_still_works(setup):  # noqa: F811
    c, boot = setup["c"], setup["boot"]
    v = {"prefix": "192.168.55", "vip": "192.168.55.10", "a": "192.168.55.11", "b": "192.168.55.12",
         "c": "192.168.55.13", "dhcp": True, "cluster_size": 1, "node": "a", "rtc_akku": False, "tz": "Europe/Zurich"}
    r = c.post("/api/einrichtung/neu", json={"token": _token(c), "admin_password": "Theater-Fest-2026", "values": v})
    assert r.status_code == 200, r.text
    conf = (boot / "festpos.conf").read_text()
    assert backup.conf_value(conf, "NET") == "192.168.55.0/24"
    assert backup.conf_value(conf, "DHCP_POOL") == "192.168.55.150-192.168.55.189"


@pytest.mark.parametrize("conf, want", [
    ("NET=10.20.0.0/22", "10.20.0.0/22 10.20.0 22"),
    ("NET_PREFIX=192.168.55\nNET_CIDR=24", "192.168.55.0/24 192.168.55 24"),         # ältere festpos.conf
    ("NET=172.16.5.0/26", "172.16.5.0/26 172.16.5 26"),
    ("NET=10.20.0.5/22", "ungültig"), ("NET=10.20.0.0/31", "ungültig"), ("NET_PREFIX=10.0\nNET_CIDR=24", "ungültig"),
])
def test_shell_net_from_conf(conf, want):
    sets = "; ".join(f"{line}" for line in conf.split("\n"))
    r = subprocess.run(["bash", "-c", f'source "{LIB}"; unset NET NET_PREFIX NET_CIDR; {sets}; '
                                      'festpos_net_from_conf && echo "$NET $NET_PREFIX $NET_CIDR" || echo ungültig'],
                       capture_output=True, text=True)
    assert r.stdout.strip() == want


def test_shell_in_net():
    def ok(ip, net):
        return subprocess.run(["bash", "-c", f'source "{LIB}"; festpos_in_net {ip} {net}']).returncode == 0
    assert ok("10.20.3.254", "10.20.0.0/22") and not ok("10.20.3.255", "10.20.0.0/22")
    assert not ok("10.20.0.0", "10.20.0.0/22") and not ok("10.20.4.1", "10.20.0.0/22") and ok("172.16.5.62", "172.16.5.0/26")


def test_node_script_has_no_fixed_prefix_math():
    text = (ROOT / "deploy" / "festpos-node.sh").read_text()
    assert "$NET_PREFIX.0/$NET_CIDR" not in text and "${INTERNET_GATEWAY%.*}" not in text
    assert 'festpos_env_line FESTPOS_NET "$NET"' in text
    assert "$NET_PREFIX.0" not in (ROOT / "deploy" / "make-certs.sh").read_text()


def test_info_names_dhcp_pool(monkeypatch, tmp_path):
    monkeypatch.setenv("FESTPOS_NET", "10.20.0.0/22")
    conf = tmp_path / "dnsmasq.conf"
    monkeypatch.setattr(netz, "DHCP_CONF", conf)
    assert netz.info()["dhcp"] == {"from": "10.20.0.150", "to": "10.20.0.189"}          # Vorgabe ohne dnsmasq.conf
    conf.write_text("port=0\ndhcp-range=10.20.1.0,10.20.1.200,255.255.252.0,2m\n")
    assert netz.info()["dhcp"] == {"from": "10.20.1.0", "to": "10.20.1.200"}


def test_info_from_environment(monkeypatch):
    monkeypatch.setenv("FESTPOS_NET", "10.20.0.0/22")
    monkeypatch.setenv("FESTPOS_VIP", "10.20.0.10")
    i = netz.info()
    assert i["net"] == "10.20.0.0/22" and i["vip"] == "10.20.0.10" and i["examples"]["phone"] == "10.20.0.101"
    monkeypatch.delenv("FESTPOS_NET")
    monkeypatch.setenv("NODE_IP", "172.16.5.11")
    monkeypatch.setenv("FESTPOS_NET_CIDR", "26")
    assert str(netz.current()) == "172.16.5.0/26" and ipaddress.ip_address(netz.info()["examples"]["pc"]) in netz.current()
    monkeypatch.setenv("FESTPOS_NET", "10.20.0.0/27")
    ex = netz.info()["examples"]
    assert len(set(ex.values())) == len(ex) and ex["pc"] != netz.info()["vip"]              # alle verschieden


def test_netinfo_for_the_admin(client, monkeypatch):
    from tests.test_api import AUTH
    monkeypatch.setenv("FESTPOS_NET", "10.20.0.0/22")
    monkeypatch.setenv("FESTPOS_VIP", "10.20.0.10")
    r = client.get("/api/admin/netinfo", auth=AUTH).json()
    assert r["net"] == "10.20.0.0/22" and r["examples"]["router"] == "10.20.0.1" and r["examples"]["switch"] == "10.20.0.30"
