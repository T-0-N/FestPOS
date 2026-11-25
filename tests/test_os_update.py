"""Sicherheitsupdates des Betriebssystems (T-0-N/FestPOS#5): holen in der CI (tools/os_updates.py), einspielen auf dem
Pi ohne Internet (deploy/festpos-os-update), Stand in der Verwaltung. apt, dpkg und dpkg-deb sind nachgebildet."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from tests.test_api import AUTH

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "deploy" / "festpos-os-update"

FAKE_APT_CI = r'''#!/usr/bin/env python3
import os, re, sys
from pathlib import Path
conf = Path(os.environ["APT_CONFIG"]).read_text()
root = Path(re.search(r'^Dir "([^"]+)/";', conf, re.M).group(1))
args = sys.argv[1:]
log = Path(os.environ["FAKE_LOG"]); log.write_text(log.read_text() + " ".join(args) + "\n" if log.exists() else " ".join(args) + "\n")
if args == ["update", "--error-on=any"]:
    sys.exit(int(os.environ.get("FAKE_UPDATE_RC", "0")))
if args[:2] == ["-s", "dist-upgrade"]:
    print("Reading package lists...")
    print("Inst openssl [3.0.11-1~deb12u2] (3.0.15-1~deb12u1 Debian-Security:12/stable-security [arm64]) []")
    print("Inst linux-image-6.6.62+rpt-rpi-2712 (1:6.6.62-1+rpt1 Raspberry Pi:stable [arm64])")
    print("Conf openssl (3.0.15-1~deb12u1 Debian-Security:12/stable-security [arm64])")
    sys.exit(0)
if args[:2] == ["-s", "install"]:
    print("Inst cryptsetup-bin (2:2.6.1-4~deb12u2 Debian:12/stable [arm64])")
    print("Inst openssl [3.0.11-1~deb12u2] (3.0.15-1~deb12u1 Debian-Security:12/stable-security [arm64]) []")
    sys.exit(0)
if "--download-only" in args and "install" in args:
    (root / "var/cache/apt/archives/cryptsetup-bin_2%3a2.6.1-4~deb12u2_arm64.deb").write_bytes(b"deb cryptsetup")
    sys.exit(0)
if "--download-only" in args:
    a = root / "var/cache/apt/archives"
    (a / "openssl_3.0.15-1~deb12u1_arm64.deb").write_bytes(b"deb openssl")
    if not os.environ.get("FAKE_MISSING"):
        (a / "linux-image-6.6.62+rpt-rpi-2712_1%3a6.6.62-1+rpt1_arm64.deb").write_bytes(b"deb kernel")
    (a / "fremd_1.0_arm64.deb").write_bytes(b"nicht im Plan")
    sys.exit(0)
sys.exit(3)
'''
FAKE_DPKG_DEB = r'''#!/usr/bin/env python3
import sys
from urllib.parse import unquote
name, ver, arch = unquote(sys.argv[2].rsplit("/", 1)[-1][:-4]).split("_")
print(f"Package: {name}\nVersion: {ver}\nArchitecture: {arch}")
'''


def _exe(path: Path, text: str) -> str:
    path.write_text(text)
    path.chmod(0o755)
    return str(path)


# ------------------------------------------------------------------ CI: holen
def test_ci_collects_exactly_what_apt_plans(tmp_path):
    pk = tmp_path / "pakete.txt"
    pk.write_text("openssl\t3.0.11-1~deb12u2\tarm64\nlibc6\t2.36-9+deb12u7\tarm64\ntzdata\t2024a-0+deb12u1\tall\n")
    env = {**os.environ, "FESTPOS_APT_GET": _exe(tmp_path / "apt-get", FAKE_APT_CI),
           "FESTPOS_DPKG_DEB": _exe(tmp_path / "dpkg-deb", FAKE_DPKG_DEB), "FAKE_LOG": str(tmp_path / "log")}
    ziel = tmp_path / "os"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "os_updates.py"), "--pakete", str(pk), "--ziel", str(ziel),
                        "--debian-keyring", "/k/debian.gpg", "--rpi-keyring", "/k/rpi.gpg"], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    items = {i["name"]: i for i in json.loads((ziel / "liste.json").read_text())}
    assert set(items) == {"openssl", "linux-image-6.6.62+rpt-rpi-2712", "cryptsetup-bin"}     # «fremd» nicht
    assert items["cryptsetup-bin"]["new"] and items["cryptsetup-bin"]["version"] == "2:2.6.1-4~deb12u2"  # neu nötig (#3)
    o, k = items["openssl"], items["linux-image-6.6.62+rpt-rpi-2712"]
    assert o["before"] == "3.0.11-1~deb12u2" and not o["new"] and not o["kernel"]
    assert k["new"] and k["kernel"] and k["version"] == "1:6.6.62-1+rpt1"
    assert hashlib.sha256((ziel / o["file"]).read_bytes()).hexdigest() == o["sha256"]
    stand = json.loads((ziel / "stand.json").read_text())
    assert stand["count"] == 3 and stand["kernel"] and stand["suite"] == "bookworm"
    assert ("update --error-on=any\n-s dist-upgrade\n--download-only -y dist-upgrade\n-s install --no-install-recommends cryptsetup-bin\n"
            "--download-only -y install --no-install-recommends cryptsetup-bin") in (tmp_path / "log").read_text()


def test_ci_sandbox_sources_and_status(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import os_updates
    conf = os_updates.sandbox(tmp_path, [("openssl", "3.0", "arm64"), ("tzdata", "1", "all")], "/k/d.gpg", "/k/r.gpg")
    src = (tmp_path / "etc/apt/sources.list").read_text()
    assert "signed-by=/k/d.gpg] http://security.debian.org/debian-security bookworm-security" in src
    assert "signed-by=/k/r.gpg] http://archive.raspberrypi.com/debian bookworm main" in src
    assert "[arch=arm64 " in src and "bookworm-updates" in src
    status = (tmp_path / "var/lib/dpkg/status").read_text()
    assert "Package: openssl\nStatus: install ok installed" in status and "Architecture: all" in status
    assert 'APT::Architecture "arm64"' in conf.read_text() and 'AllowInsecureRepositories "false"' in conf.read_text()


def test_ci_fails_when_a_planned_package_is_missing(tmp_path):
    pk = tmp_path / "pakete.txt"
    pk.write_text("openssl\t3.0.11-1~deb12u2\tarm64\n")
    env = {**os.environ, "FESTPOS_APT_GET": _exe(tmp_path / "apt-get", FAKE_APT_CI), "FAKE_MISSING": "1",
           "FESTPOS_DPKG_DEB": _exe(tmp_path / "dpkg-deb", FAKE_DPKG_DEB), "FAKE_LOG": str(tmp_path / "log")}
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "os_updates.py"), "--pakete", str(pk), "--ziel",
                        str(tmp_path / "os"), "--rpi-keyring", "/k/r.gpg"], env=env, capture_output=True, text=True,
                       timeout=60)
    assert r.returncode and "nicht alle Pakete" in r.stderr + r.stdout


def test_build_zip_carries_os_updates(tmp_path, monkeypatch):
    import tools.build_zip as bz
    src = tmp_path / "projekt"
    (src / "deploy").mkdir(parents=True)
    (src / "deploy" / "update.sh").write_bytes(b"#!/bin/bash\n")
    (src / "VERSION").write_text("99.0.5\n")
    osdir = tmp_path / "os"
    (osdir / "debs").mkdir(parents=True)
    (osdir / "debs" / "a_1_arm64.deb").write_bytes(b"x")
    (osdir / "liste.json").write_text("[]")
    monkeypatch.setattr(bz, "ROOT", src)
    monkeypatch.setattr(sys, "argv", ["build_zip.py", str(tmp_path / "out.zip"), "--os-updates", str(osdir)])
    bz.main()
    names = zipfile.ZipFile(tmp_path / "out.zip").namelist()
    assert "festpos/os-updates/debs/a_1_arm64.deb" in names and "festpos/os-updates/liste.json" in names


# ------------------------------------------------------------------ Pi: einspielen
FAKE_DPKG = r'''#!/usr/bin/env python3
import os, sys
from pathlib import Path
d = Path(os.environ["FAKE_DIR"]); a = sys.argv[1:]
(d / "log").open("a").write("dpkg " + " ".join(a) + "\n")
if a[:1] == ["--compare-versions"]:
    key = lambda v: [int(x) if x.isdigit() else x for x in v.replace("~", ".").replace("-", ".").split(".")]
    sys.exit(0 if key(a[1]) > key(a[3]) else 1)
if a == ["--audit"]:
    f = d / "audit"
    print(f.read_text() if f.exists() else "")
    sys.exit(0)
if a == ["--configure", "-a"]:
    (d / "audit").unlink(missing_ok=True)
sys.exit(0)
'''
FAKE_DPKG_QUERY = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
inst = json.loads((Path(os.environ["FAKE_DIR"]) / "installed.json").read_text())
v = inst.get(sys.argv[-1].split(":")[0])
if v is None:
    sys.exit(1)
print(f"install ok installed\t{v}", end="")
'''
FAKE_APT_PI = r'''#!/usr/bin/env python3
import os, sys
from pathlib import Path
d = Path(os.environ["FAKE_DIR"])
(d / "log").open("a").write("apt-get " + " ".join(sys.argv[1:]) + "\n")
if "install" in sys.argv and "-f" not in sys.argv and (d / "apt-fail").exists():
    (d / "audit").write_text("halb konfiguriert")
    print("E: Sub-process /usr/bin/dpkg returned an error code (1)", file=sys.stderr)
    sys.exit(100)
sys.exit(0)
'''


@pytest.fixture
def pi(tmp_path):
    d = tmp_path / "fake"
    d.mkdir()
    (d / "installed.json").write_text(json.dumps({"openssl": "3.0.11", "curl": "7.88.1-10+deb12u8", "zsh": "5.9"}))
    pkg = tmp_path / "os-updates"
    (pkg / "debs").mkdir(parents=True)
    items = []
    for name, ver, new in (("openssl", "3.0.15", False), ("curl", "7.88.1-10+deb12u8", False),
                           ("vim", "9.0.1", False), ("linux-image-6.6.62", "1.0", True)):
        f = pkg / "debs" / f"{name}_{ver}_arm64.deb"
        f.write_bytes(f"deb {name}".encode())
        items.append({"name": name, "version": ver, "arch": "arm64", "file": f"debs/{f.name}",
                      "sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "new": new})
    (pkg / "liste.json").write_text(json.dumps(items))
    (pkg / "stand.json").write_text(json.dumps({"date": "2026-11-20", "count": 4}))
    r = tmp_path / "root"
    r.mkdir()
    env = {**os.environ, "FESTPOS_TEST_ROOT": str(r), "FAKE_DIR": str(d), "FESTPOS_DPKG": _exe(d / "dpkg", FAKE_DPKG),
           "FESTPOS_DPKG_QUERY": _exe(d / "dpkg-query", FAKE_DPKG_QUERY), "FESTPOS_APT_GET": _exe(d / "apt-get", FAKE_APT_PI)}

    def run(*args):
        p = subprocess.run([sys.executable, str(HELPER), *args], env=env, capture_output=True, text=True, timeout=60)
        stand = json.loads((r / "var/lib/festpos-os/stand.json").read_text()) if (r / "var/lib/festpos-os/stand.json").exists() else {}
        log = (d / "log").read_text() if (d / "log").exists() else ""
        return p, stand, log
    return {"run": run, "pkg": pkg, "fake": d, "root": r}


def test_pi_installs_only_older_installed_and_new(pi):
    p, stand, log = pi["run"]("--paket", str(pi["pkg"]))
    assert p.returncode == 0, p.stdout + p.stderr
    inst = [ln for ln in log.splitlines() if ln.startswith("apt-get install")]
    assert len(inst) == 1 and "--no-download" in inst[0] and "--force-confold" in inst[0]
    assert "openssl_3.0.15" in inst[0] and "linux-image" in inst[0]                   # älter installiert / neu nötig
    assert "curl_" not in inst[0] and "vim_" not in inst[0]                            # gleich / nicht installiert
    assert stand["result"] == "ok" and stand["date"] == "2026-11-20" and stand["installed"] == 2 and stand["kernel_new"]


def test_pi_refuses_bad_checksum_and_repairs_after_apt_error(pi):
    deb = next((pi["pkg"] / "debs").glob("openssl_*"))
    deb.write_bytes(b"veraendert")
    p, stand, log = pi["run"]("--paket", str(pi["pkg"]))
    assert p.returncode == 1 and stand["result"] == "failed" and stand["error"] == "sha256" and "apt-get install" not in log
    deb.write_bytes(b"deb openssl")
    (pi["fake"] / "apt-fail").write_text("1")
    p, stand, log = pi["run"]("--paket", str(pi["pkg"]))
    assert p.returncode == 1 and stand["result"] == "repaired" and "dpkg --configure -a" in log and "apt-get -f install" in log


def test_pi_repair_at_boot_and_stand(pi):
    (pi["fake"] / "audit").write_text("Paket openssl ist nur halb konfiguriert")
    p, stand, log = pi["run"]("--reparieren")
    assert p.returncode == 0 and stand["result"] == "repaired" and "dpkg --configure -a" in log
    (pi["root"] / "run").mkdir(parents=True, exist_ok=True)
    (pi["root"] / "run" / "reboot-required").write_text("")
    p, stand, _ = pi["run"]("--stand")
    assert stand["reboot"] is True and stand["kernel"]
    assert pi["run"]("--unsinn")[0].returncode == 2


def test_pi_nothing_to_do(pi):
    (pi["fake"] / "installed.json").write_text(json.dumps({"openssl": "3.0.15"}))
    lst = json.loads((pi["pkg"] / "liste.json").read_text())
    (pi["pkg"] / "liste.json").write_text(json.dumps([i for i in lst if not i["new"]]))
    p, stand, log = pi["run"]("--paket", str(pi["pkg"]))
    assert p.returncode == 0 and "apt-get" not in log and stand["result"] == "ok" and stand["open"] == 0


# ------------------------------------------------------------------ Verwaltung
def test_os_status_in_system_info_and_online_job(client, monkeypatch, tmp_path):
    from app import cards, system
    stand = tmp_path / "stand.json"
    monkeypatch.setattr(system, "OS_STAND", str(stand))
    assert system.os_status() is None                                    # ohne dpkg (Testrechner) und ohne Stand
    stand.write_text(json.dumps({"date": "2026-01-02", "result": "repaired", "error": "x" * 500, "open": 3}))
    s = system.os_status()
    assert s["date"] == "2026-01-02" and s["result"] == "repaired" and len(s["error"]) == 300 and s["open"] == 3
    monkeypatch.setattr(system, "OS_STAND", str(tmp_path / "fehlt.json"))
    dpkg = tmp_path / "status"
    dpkg.write_text("x")
    monkeypatch.setattr(system, "DPKG_STATUS", str(dpkg))
    assert system.os_status()["date"] == time.strftime("%Y-%m-%d")                  # ohne Stand: Datum der Paketdatenbank
    jobs = []
    monkeypatch.setattr(cards, "submit", lambda kind, payload=None: jobs.append(kind) or "j")
    assert client.post("/api/admin/os/online", auth=AUTH).json()["started"] and jobs == ["os_online"]
    tok = client.post("/api/admin/login", json={"name": "admin", "password": "geheim"}).json()["token"]
    assert client.post("/api/admin/os/online", headers={"Authorization": f"Bearer {tok}"}).status_code == 428


def test_release_keeps_the_package_list_of_the_image():
    """Der Release-Entwurf behält festpos-pakete.txt: release.cmd übernimmt sie als deploy/os-pakete.txt."""
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "rm out/festpos-pakete.txt" not in wf and "--os-updates dist/os-updates" in wf
