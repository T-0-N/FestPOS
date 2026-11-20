"""Version 2026.10.10-26: der root-Helfer festpos-update – mit dem echten update.sh aus dem Paket in einem
Test-Wurzelverzeichnis (systemctl und curl sind Attrappen, wie in test_update.py).

Geprüft wird: Auftrag der App -> Signatur, Version, Einspielen, Antwort; abgelehnt bei fremdem Schlüssel,
veränderter Datei, älterer Version, ohne Schlüssel; Zurück zur Sicherung; Ordner/USB-Stick (an die App übergeben
oder – wenn sie nicht läuft – nur hier einspielen)."""
from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app import release
from tests.release_util import build_bundle, keypair, sign
from tests.test_update import OLD, ROOT, fake  # noqa: F401  (fake = Fixture)

HELPER = ROOT / "deploy" / "festpos-update"
NEW = "99.0.0"


@pytest.fixture
def helper(fake, tmp_path):  # noqa: F811
    r, dest, env = fake
    d = {k: tmp_path / k for k in ("boot", "pakete", "req", "status", "work")}
    for p in d.values():
        p.mkdir()
    pk, line = keypair("Vereins-Update")
    (d["boot"] / "festpos-update.pub").write_text(line + "\n")
    # Konfiguration auf der «Boot-Partition» – update.sh prüft sie mit festpos-node --pruefen (FESTPOS_BOOT)
    (d["boot"] / "festpos.conf").write_text((ROOT / "deploy" / "festpos.conf.example").read_text())
    (d["boot"] / "node.conf").write_text("NODE=a\n")
    env = {**env, "FESTPOS_DEST": str(dest), "FESTPOS_ALT": str(r / "opt" / "festpos.alt"),
           "FESTPOS_RELEASE_LIB": str(ROOT), "FESTPOS_BOOT": str(d["boot"]), "FESTPOS_UPDATE_DIR": str(d["pakete"]),
           "FESTPOS_UPDATE_REQ": str(d["req"]), "FESTPOS_UPDATE_STATUS": str(d["status"]),
           "FESTPOS_UPDATE_WORK": str(d["work"]), "FESTPOS_UPDATE_HEALTH": "http://127.0.0.1:9/api/health",
           "FESTPOS_UPDATE_APP_WAIT": "0"}
    return {"r": r, "dest": dest, "env": env, "pk": pk, "line": line, **d}


def run_helper(h, *args):
    return subprocess.run([sys.executable, "-I", str(HELPER), *args], env=h["env"], capture_output=True, text=True,
                          timeout=240)


def offer(h, version=NEW, pk=None, tamper=False, replace: dict[str, bytes] | None = None) -> str:
    """Paket wie von der App abgelegt: <sha>.zip + <sha>.sig im Paketordner -> sha.
    replace: Dateien im Paket ersetzen oder ergänzen (Name ohne «festpos/»), vor dem Signieren."""
    tmp = h["work"].parent / f"b-{version}.zip"
    build_bundle(tmp, version)
    if replace:
        import zipfile
        old = tmp.with_suffix(".alt")
        tmp.rename(old)
        with zipfile.ZipFile(old) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                if info.filename.removeprefix("festpos/") not in replace:
                    dst.writestr(info, src.read(info))
            for name, data in replace.items():
                info = zipfile.ZipInfo("festpos/" + name)
                info.external_attr = (0o100755 & 0xFFFF) << 16
                dst.writestr(info, data)
        old.unlink()
    sig = release.sign(tmp, pk or h["pk"])
    if tamper:
        with open(tmp, "ab") as f:
            f.write(b"\0")
    sha = release.sha256_file(tmp)
    tmp.replace(h["pakete"] / f"{sha}.zip")
    (h["pakete"] / f"{sha}.sig").write_bytes(sig)
    return sha


def request(h, rid, **req):
    (h["req"] / f"anfrage-{rid}").write_text(json.dumps(req))
    res = run_helper(h, "--anfrage")
    out = json.loads((h["status"] / f"{rid}.json").read_text())
    return res, out


@pytest.fixture(autouse=True)
def _strict_umask():
    """T43: Rechte der Ausgabe gelten auch mit strenger umask (0644 ausdrücklich, nicht dank umask 022)."""
    old = os.umask(0o077)
    yield
    os.umask(old)


def test_install_request_end_to_end(helper):
    h = helper
    sha = offer(h)
    res, out = request(h, "r1", action="install", sha=sha, version=NEW)
    assert out["ok"] is True and out["before"] == OLD and out["after"] == NEW, res.stdout + res.stderr
    assert (h["dest"] / "VERSION").read_text().strip() == NEW
    assert (h["dest"] / "deploy" / "update.sh").exists()
    assert out["msg"].startswith("OK: Server läuft mit Version " + NEW)
    log = (h["status"] / "r1.log").read_text()
    assert "Signatur gültig (Vereins-Update)" in log and "OK: Server läuft" in log
    assert not list(h["req"].iterdir())                              # Auftrag weg (sonst startet systemd neu)
    assert not (h["status"] / "laeuft.json").exists()
    assert not list(h["work"].glob("neu*")) and not (h["work"] / "paket.zip").exists()
    keys = json.loads((h["status"] / "keys.json").read_text())
    assert keys["keys"][0]["comment"] == "Vereins-Update" and keys["version"] == NEW and keys["backup"] == OLD
    assert oct((h["status"] / "keys.json").stat().st_mode & 0o777) == "0o644"
    # gleiche Version nochmals: abgelehnt (nur neuere)
    _, out = request(h, "r2", action="install", sha=sha, version=NEW)
    assert out["ok"] is False and out["code"] == "not_newer"
    # Zurück zur Sicherung – aber nur, wenn noch derselbe Stand läuft
    _, out = request(h, "r3", action="rollback", **{"from": "2026.10.10-1", "to": OLD})
    assert out["code"] == "rollback_changed" and (h["dest"] / "VERSION").read_text().strip() == NEW
    _, out = request(h, "r4", action="rollback", **{"from": NEW, "to": OLD})
    assert out["ok"] is True and out["after"] == OLD, out
    assert not (h["dest"] / "VERSION").exists() and not (h["dest"] / "deploy").exists()


@pytest.mark.parametrize("case, code", [("foreign_key", "unknown_key"), ("tampered", "bad_signature"),
                                        ("older", "not_newer"), ("no_key", "no_key"), ("wrong_sha", "sha_mismatch"),
                                        ("symlink", "not_a_file"), ("missing", "missing"), ("bad_version", "version_mismatch"),
                                        ("crlf", "crlf")])
def test_refused_requests_change_nothing(helper, case, code):
    h = helper
    version = "2026.10.10-1" if case == "older" else NEW
    other, _ = keypair("Fremd")
    crlf = {"deploy/update.sh": (ROOT / "deploy" / "update.sh").read_bytes().replace(b"\n", b"\r\n")}
    sha = offer(h, version, pk=other if case == "foreign_key" else None, tamper=case == "tampered",
                replace=crlf if case == "crlf" else None)                # unter Windows gebaut (core.autocrlf)
    req = {"action": "install", "sha": sha, "version": version}
    if case == "no_key":
        (h["boot"] / "festpos-update.pub").unlink()
    elif case == "wrong_sha":
        req["sha"] = "0" * 64
        (h["pakete"] / f"{sha}.zip").rename(h["pakete"] / f"{'0' * 64}.zip")
        (h["pakete"] / f"{sha}.sig").rename(h["pakete"] / f"{'0' * 64}.sig")
    elif case == "symlink":
        real = h["pakete"] / "echt.zip"
        (h["pakete"] / f"{sha}.zip").rename(real)
        os.symlink(real, h["pakete"] / f"{sha}.zip")
    elif case == "missing":
        (h["pakete"] / f"{sha}.zip").unlink()
    elif case == "bad_version":
        req["version"] = "99.0.1"
    res, out = request(h, "x", **req)
    assert out["ok"] is False and out["code"] == code, res.stdout + res.stderr
    assert (h["dest"] / "app" / "main.py").read_text() == f'APP_VERSION = "{OLD}"\n'
    assert not (h["dest"] / "VERSION").exists()
    assert "ABGELEHNT" in (h["status"] / "x.log").read_text()
    assert not list(h["work"].glob("*.zip"))                        # keine Reste (je ~25 MB)


def test_update_that_installs_nothing_is_not_done(helper):
    """update.sh endet mit 2 («läuft, wartet auf die Datenbank»), hat aber nichts eingespielt – so endete ein
    Paket mit Windows-Zeilenenden («set: pipefail\\r: invalid option name»). Früher hiess das «fertig»."""
    h = helper
    sha = offer(h, replace={"deploy/update.sh": b"#!/bin/bash\necho 'tut nichts'\nexit 2\n"})
    res, out = request(h, "n1", action="install", sha=sha, version=NEW)
    assert out["ok"] is False and out["code"] == "not_installed", res.stdout + res.stderr
    assert out["before"] == out["after"] == OLD and out["kw"] == {"installed": OLD}
    assert (h["dest"] / "app" / "main.py").read_text() == f'APP_VERSION = "{OLD}"\n'


def test_unknown_and_broken_requests_are_answered(helper):
    h = helper
    (h["req"] / "anfrage-kaputt").write_text("{nicht json")
    (h["req"] / "fremd").write_text("x")
    os.mkdir(h["req"] / "ordner")
    res = run_helper(h, "--anfrage")
    assert json.loads((h["status"] / "kaputt.json").read_text())["code"] == "request_bad", res.stderr
    assert not list(h["req"].iterdir())


def test_private_key_on_boot_partition_is_reported(helper):
    h = helper
    (h["boot"] / "festpos-update").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nxyz\n")
    res = run_helper(h, "--schluessel")
    assert "PRIVATER Schlüssel" in res.stdout and "Vereins-Update" in res.stdout
    assert any("PRIVATER" in p for p in json.loads((h["status"] / "keys.json").read_text())["problems"])
    (h["boot"] / "festpos-update.pub").unlink()
    assert "Kein Update-Schlüssel" in run_helper(h, "--schluessel").stdout


class _Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b'{"ready": true, "version": "x"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _stick(h, *versions, sub="", signer=None) -> Path:
    d = h["work"].parent / "stick"
    (d / sub).mkdir(parents=True, exist_ok=True)
    for i, v in enumerate(versions):
        z = build_bundle(d / sub / (f"festpos-{i}.zip" if i else "FESTPOS.ZIP"), v)
        sign(z, signer or h["pk"], sig_path=z.with_name(z.name + (".SIG" if not i else ".sig")))
    return d


def test_folder_hands_newest_bundle_to_running_app(helper):
    h = helper
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Health)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    h["env"]["FESTPOS_UPDATE_HEALTH"] = f"http://127.0.0.1:{srv.server_port}/api/health"
    try:
        d = _stick(h, "2026.10.10-80", NEW, sub="festpos-update")
        (d / "unsigniert.zip").write_bytes(b"egal")                       # ohne .sig: nicht beachtet
        res = run_helper(h, "--ordner", str(d))
    finally:
        srv.shutdown()
    assert res.returncode == 0, res.stdout + res.stderr
    usb = json.loads((h["status"] / "usb.json").read_text())
    assert usb["state"] == "handed" and usb["version"] == NEW and usb["source"] == "folder"
    assert (h["status"] / "paket" / f"{usb['sha']}.zip").exists() and (h["status"] / "paket" / f"{usb['sha']}.sig").exists()
    assert release.sha256_file(h["status"] / "paket" / f"{usb['sha']}.zip") == usb["sha"]
    assert not (h["dest"] / "VERSION").exists()                           # nichts selbst eingespielt
    assert not list(h["work"].glob("*.zip"))


def test_folder_installs_here_when_app_is_down_and_reports_bad_sticks(helper):
    h = helper
    d = _stick(h, NEW)
    res = run_helper(h, "--ordner", str(d))
    assert res.returncode == 0, res.stdout + res.stderr
    usb = json.loads((h["status"] / "usb.json").read_text())
    assert usb["state"] == "installed" and usb["after"] == NEW
    assert (h["dest"] / "VERSION").read_text().strip() == NEW
    # nochmals derselbe Stick (z.B. beim nächsten Start noch eingesteckt): nichts
    run_helper(h, "--ordner", str(d))
    assert json.loads((h["status"] / "usb.json").read_text())["state"] == "old"
    # fremd signiert
    other, _ = keypair("Fremd")
    bad = _stick(h, "99.0.5", sub="b", signer=other) / "b"
    assert run_helper(h, "--ordner", str(bad)).returncode == 2
    usb = json.loads((h["status"] / "usb.json").read_text())
    assert usb["state"] == "invalid" and usb["code"] == "unknown_key"
    assert (h["dest"] / "VERSION").read_text().strip() == NEW
    # leerer Stick
    (h["work"].parent / "leer").mkdir()
    run_helper(h, "--ordner", str(h["work"].parent / "leer"))
    assert json.loads((h["status"] / "usb.json").read_text())["state"] == "none"


def test_usb_refuses_odd_device_names(helper):
    assert run_helper(helper, "--usb", "../sda1").returncode == 1
    assert run_helper(helper, "--usb", "mmcblk0p1").returncode == 1        # die SD-Karte nie


# -- Internet für «Update aus dem Internet» (net_up / net_down / --netz-aus) -------------------------------------
NMCLI = r"""#!/bin/bash
echo "nmcli $*" >> "$NETLOG"
cons=$NETDIR/cons; touch "$cons"
case "$*" in
  "-t -f NAME connection show") cat "$cons" ;;
  "connection add"*) n=""; prev=""; for a in "$@"; do [ "$prev" = con-name ] && n=$a; prev=$a; done; echo "$n" >> "$cons" ;;
  "connection delete"*) grep -vx "$3" "$cons" > "$cons.neu"; mv "$cons.neu" "$cons" ;;
  *"connection up"*)
    prev=""; for a in "$@"; do [ "$prev" = passwd-file ] && cat "$a" >> "$NETDIR/pw"; prev=$a; done
    exit "${FAKE_UP_RC:-0}" ;;
esac
exit 0
"""
IP = r"""#!/bin/bash
echo "ip $*" >> "$NETLOG"
if [ "$1 $2 $3 $4" = "-4 -o addr show" ]; then
  if [ "$5" = dev ]; then
    [ -n "$FAKE_DEV_ADDR" ] && echo "3: $6    inet $FAKE_DEV_ADDR brd 1.2.3.255 scope global dynamic $6"
  else
    echo "2: eth0    inet 192.168.55.11/24 brd 192.168.55.255 scope global eth0"
  fi
fi
exit 0
"""
LOGONLY = '#!/bin/bash\necho "$(basename "$0") $*" >> "$NETLOG"\nexit 0\n'
NFT = '#!/bin/bash\necho "nft $*" >> "$NETLOG"\ncat >> "$NETDIR/nft"\nexit "${FAKE_NFT_RC:-0}"\n'


@pytest.fixture
def netz(helper, tmp_path):
    h = helper
    stub, nd = tmp_path / "netstub", tmp_path / "netz"
    stub.mkdir()
    nd.mkdir()
    for name, body in (("nmcli", NMCLI), ("ip", IP), ("systemd-run", LOGONLY), ("rfkill", LOGONLY),
                       ("systemctl", LOGONLY), ("nft", NFT)):
        (stub / name).write_text(body)
        (stub / name).chmod(0o755)
    sysnet = tmp_path / "sysnet"
    (sysnet / "eth0").mkdir(parents=True)
    (sysnet / "wlan0" / "wireless").mkdir(parents=True)
    usbdev = tmp_path / "devices" / "usb2" / "2-1"                     # wie /sys/devices/…/usb2/2-1
    usbdev.mkdir(parents=True)
    (sysnet / "enx00e04c").mkdir()
    (sysnet / "enx00e04c" / "device").symlink_to(usbdev)
    (sysnet / "enx00e04c" / "carrier").write_text("1\n")
    (tmp_path / "node.env").write_text("FESTPOS_NODE=a\nNODE_IP=192.168.55.11\n")
    resolv = tmp_path / "resolv.conf"
    resolv.write_text("# Generated by NetworkManager\n")
    h["env"] = {**h["env"], "PATH": f"{stub}:{h['env']['PATH']}", "NETLOG": str(nd / "log"), "NETDIR": str(nd),
                "FESTPOS_SYS_NET": str(sysnet), "FESTPOS_RESOLV": str(resolv),
                "FESTPOS_NODE_ENV": str(tmp_path / "node.env"), "FESTPOS_NET_SCAN_WAIT": "0",
                "FESTPOS_IP_FORWARD": str(nd / "ip_forward")}
    return {**h, "nd": nd, "resolv": resolv, "sysnet": sysnet}


def netlog(n) -> str:
    p = n["nd"] / "log"
    return p.read_text() if p.exists() else ""


def test_net_lan_sets_route_and_dns_and_takes_them_back(netz):
    n = netz
    _, out = request(n, "n1", action="net_up", via="lan", gateway="192.168.55.1", dns="1.1.1.1")
    assert out["ok"], out
    log = netlog(n)
    assert "ip route replace default via 192.168.55.1 metric 900" in log
    assert "nameserver 1.1.1.1" in n["resolv"].read_text()
    assert "systemd-run --unit=festpos-internet-aus --on-active=1800" in log and "--netz-aus" in log
    _, out = request(n, "n2", action="net_down")
    assert out["ok"]
    assert "ip route del default via 192.168.55.1" in netlog(n)
    assert n["resolv"].read_text() == "# Generated by NetworkManager\n"
    assert "nmcli radio wifi off" in netlog(n)
    _, out = request(n, "n3", action="net_up", via="lan", gateway="10.0.0.1", dns="1.1.1.1")   # nicht im FestPOS-Netz
    assert not out["ok"] and out["code"] == "net_gateway"
    assert "nameserver" not in n["resolv"].read_text()


def test_net_wlan_password_only_through_a_file(netz):
    n = netz
    n["env"]["FAKE_DEV_ADDR"] = "172.20.10.2/28"                          # Handy-Hotspot
    _, out = request(n, "w1", action="net_up", via="wlan", ssid="Fest-Hotspot", psk="geheim-wlan-123")
    assert out["ok"] and out["iface"] == "wlan0", out
    log = netlog(n)
    assert "rfkill unblock wifi" in log and "nmcli radio wifi on" in log
    assert "connection add save no type wifi ifname wlan0 con-name festpos-internet ssid Fest-Hotspot" in log
    assert "geheim-wlan-123" not in log and "geheim-wlan-123" not in json.dumps(out)
    assert (n["nd"] / "pw").read_text() == "802-11-wireless-security.psk:geheim-wlan-123\n"
    assert not list(n["work"].glob("internet.pw"))                       # Datei mit dem Passwort ist weg
    run_helper(n, "--netz-aus")                                           # wie der Zeitgeber
    log = netlog(n)
    assert "nmcli connection delete festpos-internet" in log and log.rstrip().endswith("nmcli radio wifi off")


def test_net_wlan_failures_switch_everything_off(netz):
    n = netz
    n["env"]["FAKE_UP_RC"] = "4"
    _, out = request(n, "w2", action="net_up", via="wlan", ssid="Falsch", psk="geheim-wlan-123")
    assert not out["ok"] and out["code"] == "net_wlan"
    assert "nmcli connection delete festpos-internet" in netlog(n) and netlog(n).rstrip().endswith("nmcli radio wifi off")
    n["env"]["FAKE_UP_RC"] = "0"
    n["env"]["FAKE_DEV_ADDR"] = "192.168.55.77/24"                         # WLAN im gleichen Netz wie FestPOS
    _, out = request(n, "w3", action="net_up", via="wlan", ssid="Gleich", psk="geheim-wlan-123")
    assert not out["ok"] and out["code"] == "net_same_net"
    _, out = request(n, "w4", action="net_up", via="wlan", ssid="x", psk="kurz")
    assert not out["ok"] and out["code"] == "net_bad"
    import shutil
    shutil.rmtree(n["sysnet"] / "wlan0")
    _, out = request(n, "w5", action="net_up", via="wlan", ssid="x", psk="geheim-wlan-123")
    assert not out["ok"] and out["code"] == "net_no_wlan"


def test_net_usb_adapter_needs_a_cable(netz):
    n = netz
    n["env"]["FAKE_DEV_ADDR"] = "192.168.1.20/24"
    _, out = request(n, "u1", action="net_up", via="usb")
    assert out["ok"] and out["iface"] == "enx00e04c", out
    assert "connection add save no type ethernet ifname enx00e04c con-name festpos-internet" in netlog(n)
    (n["sysnet"] / "enx00e04c" / "carrier").write_text("0\n")
    _, out = request(n, "u2", action="net_up", via="usb")
    assert not out["ok"] and out["code"] == "net_no_link" and out["kw"]["iface"] == "enx00e04c"
    (n["sysnet"] / "enx00e04c" / "device").unlink()
    _, out = request(n, "u3", action="net_up", via="usb")
    assert not out["ok"] and out["code"] == "net_no_usb"


def test_net_share_forwards_only_the_festpos_network_and_switches_back(netz):
    """Neues Handy ausrollen: dieser Pi gibt die Verbindung ans FestPOS-Netz weiter (Weiterleitung + NAT) – und
    nimmt beides mit net_down (oder dem Zeitgeber) wieder weg."""
    n = netz
    n["env"]["FAKE_DEV_ADDR"] = "192.168.1.20/24"
    _, out = request(n, "s1", action="net_up", via="usb", share=True)
    assert out["ok"] and out["iface"] == "enx00e04c", out
    rules = (n["nd"] / "nft").read_text()
    assert "table ip festpos_share" in rules and "policy drop;" in rules
    assert 'iifname "eth0" oifname "enx00e04c" ip saddr 192.168.55.0/24 accept' in rules
    assert 'oifname "enx00e04c" ip saddr 192.168.55.0/24 masquerade' in rules
    assert (n["nd"] / "ip_forward").read_text() == "1\n"
    run_helper(n, "--netz-aus")                                           # wie der Zeitgeber
    assert (n["nd"] / "ip_forward").read_text() == "0\n"
    assert (n["nd"] / "nft").read_text().rstrip().endswith("delete table ip festpos_share")
    # ohne «share»: keine Weiterleitung
    (n["nd"] / "nft").unlink()
    _, out = request(n, "s2", action="net_up", via="usb")
    assert out["ok"] and "masquerade" not in (n["nd"] / "nft").read_text()
    assert (n["nd"] / "ip_forward").read_text() == "0\n"
    # Firewall lehnt ab: alles wieder aus
    n["env"]["FAKE_NFT_RC"] = "1"
    _, out = request(n, "s3", action="net_up", via="usb", share=True)
    assert not out["ok"] and out["code"] == "net_share"
    assert "nmcli connection delete festpos-internet" in netlog(n) and (n["nd"] / "ip_forward").read_text() == "0\n"


def test_net_share_only_to_listed_addresses_and_follow_up(netz):
    """Dauerhaft verbunden geben die Pi nur an die eingetragenen Handhelds und die Pi weiter. net_share
    führt die Liste nach (neues Handheld, Ausrollen), ohne die Verbindung neu aufzubauen."""
    n = netz
    n["env"]["FAKE_DEV_ADDR"] = "192.168.1.20/24"
    nft = n["nd"] / "nft"
    _, out = request(n, "a1", action="net_up", via="usb", keep=True, share=True,
                     allow=["192.168.55.101", "192.168.55.11", "192.168.55.10", "192.168.55.101"])
    assert out["ok"], out
    src = "{ 192.168.55.10, 192.168.55.11, 192.168.55.101 }"
    assert f'iifname "eth0" oifname "enx00e04c" ip saddr {src} accept' in nft.read_text()
    assert f'iifname "eth0" oifname "enx00e04c" ip saddr {src} masquerade' in nft.read_text()   # nur Weitergeleitetes
    connects = netlog(n).count("connection up")
    nft.unlink()
    _, out = request(n, "a2", action="net_share", allow=["192.168.55.101", "192.168.55.102"])
    assert out["ok"] and "ip saddr { 192.168.55.101, 192.168.55.102 } accept" in nft.read_text()
    assert netlog(n).count("connection up") == connects                     # Verbindung bleibt stehen
    nft.unlink()
    _, out = request(n, "a3", action="net_share")                           # Ausrollen: kurz ans ganze Netz
    assert out["ok"] and "ip saddr 192.168.55.0/24 accept" in nft.read_text()
    _, out = request(n, "a4", action="net_share", allow=["10.0.0.5"])       # nicht im FestPOS-Netz
    assert not out["ok"] and out["code"] == "net_bad"
    _, out = request(n, "a5", action="net_share", off=True)
    assert out["ok"] and (n["nd"] / "ip_forward").read_text() == "0\n"
    _, out = request(n, "a6", action="net_up", via="usb", share=True, allow=["8.8.8.8"])
    assert not out["ok"] and out["code"] == "net_bad" and "nmcli connection delete festpos-internet" in netlog(n)
    _, out = request(n, "a7", action="net_share", allow=["192.168.55.101"])  # nicht verbunden: nichts weiterzugeben
    assert not out["ok"] and out["code"] == "net_bad"


def test_net_keep_has_no_timer_and_phones_never_count(netz):
    """Seite «Internet», Schalter (c): dauerhaft ohne Zeitgeber. Handys per USB-Tethering (Treiber rndis_host oder
    Hersteller eines Handys) zählen nie als USB-Netzwerkadapter."""
    n = netz
    n["env"]["FAKE_DEV_ADDR"] = "192.168.1.20/24"
    _, out = request(n, "k1", action="net_up", via="usb", keep=True, share=True)
    assert out["ok"] and out["iface"] == "enx00e04c", out
    assert "systemd-run" not in netlog(n)
    _, out = request(n, "k2", action="net_up", via="usb")                   # kurz (Update): wieder mit Zeitgeber
    assert out["ok"] and "systemd-run --unit=festpos-internet-aus" in netlog(n)
    # Handy per USB-Tethering: Treiber rndis_host -> kein Adapter
    phone = n["sysnet"].parent / "devices" / "usb1" / "1-1" / "1-1:1.0"
    drivers = n["sysnet"].parent / "bus" / "usb" / "drivers" / "rndis_host"      # wie /sys/bus/usb/drivers/rndis_host
    drivers.mkdir(parents=True)
    phone.mkdir(parents=True)
    (n["sysnet"] / "enx00e04c" / "device").unlink()
    (n["sysnet"] / "usb0").mkdir()
    (n["sysnet"] / "usb0" / "device").symlink_to(phone)
    (phone / "driver").symlink_to(drivers)
    (n["sysnet"] / "usb0" / "carrier").write_text("1\n")
    _, out = request(n, "k3", action="net_up", via="usb")
    assert not out["ok"] and out["code"] == "net_no_usb"
    # Samsung-Handy (Hersteller 04e8) mit anderem Treiber: ebenfalls nicht
    (phone / "driver").unlink()
    (phone.parent / "idVendor").write_text("04e8\n")
    _, out = request(n, "k4", action="net_up", via="usb")
    assert not out["ok"] and out["code"] == "net_no_usb"


def test_status_parents_readable_with_strict_umask(helper):
    """T43: eigener Ausgabeordner mit fehlenden Oberordnern – auch bei umask 077 für die App lesbar (0755)."""
    h = helper
    deep = h["work"].parent / "neu" / "tief" / "status"
    h["env"] = {**h["env"], "FESTPOS_UPDATE_STATUS": str(deep)}
    h["status"] = deep
    sha = offer(h)
    request(h, "r9", action="install", sha=sha, version=NEW)
    for p in (deep, deep.parent, deep.parent.parent):
        assert oct(p.stat().st_mode & 0o777) == "0o755", p
    assert oct((deep / "keys.json").stat().st_mode & 0o777) == "0o644"


def test_status_dir_never_changes_foreign_dirs(helper, tmp_path):
    """Rechte nur an selbst angelegten Ordnern ändern, nie über eine Verknüpfung (CodeRabbit PR #65)."""
    h = helper
    private = tmp_path / "privat"
    private.mkdir(mode=0o700)
    link = tmp_path / "status-link"
    link.symlink_to(private, target_is_directory=True)
    h["env"] = {**h["env"], "FESTPOS_UPDATE_STATUS": str(link)}
    h["status"] = link
    sha = offer(h)
    request(h, "r8", action="install", sha=sha, version=NEW)
    assert oct(private.stat().st_mode & 0o777) == "0o700"          # bestehender Ordner hinter dem Link: unverändert
