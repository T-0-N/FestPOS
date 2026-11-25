"""deploy/festpos-conf.sh: Konfiguration lesen statt ausführen."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "deploy" / "festpos-conf.sh"
pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="bash fehlt")


def read(tmp_path, content: bytes, keys=("ADMIN_PASSWORD", "CLUSTER_SECRET", "VIP", "TZ", "PHONE_CERTS", "PATH")):
    f = tmp_path / "festpos.conf"
    f.write_bytes(content)
    script = f'source "{LIB}"; festpos_read_conf "{f}"; ' + "; ".join(
        f'printf "%s=[%s]\\n" {k} "${{{k}-UNSET}}"' for k in keys)
    out = subprocess.run(["bash", "-c", script], capture_output=True, env={"PATH": os.environ["PATH"], "LANG": "C.UTF-8"})
    lines = dict(l.split("=", 1) for l in out.stdout.decode("utf-8", "replace").splitlines())
    return {k: v[1:-1] for k, v in lines.items()}, out.stderr.decode("utf-8", "replace")


def test_special_characters_are_literal(tmp_path):
    v, err = read(tmp_path, b"ADMIN_PASSWORD=Fest$2026 & (Bier)\r\nCLUSTER_SECRET=\"a # b \\\"c\\\" \\\\d\"\r\n"
                            b"VIP=192.168.55.10   # Adresse\r\nTZ='Europe/Zurich'\r\n")
    assert v["ADMIN_PASSWORD"] == "Fest$2026 & (Bier)"
    assert v["CLUSTER_SECRET"] == 'a # b "c" \\d'
    assert v["VIP"] == "192.168.55.10" and v["TZ"] == "Europe/Zurich"


def test_empty_value_with_comment_and_unknown_keys(tmp_path):
    v, err = read(tmp_path, b"PHONE_CERTS=   # leer\nPATH=/boese\nFESTPOS_CONF_KEYS=PATH\nTZ=\n")
    assert v["PHONE_CERTS"] == "" and v["TZ"] == ""
    assert v["PATH"] != "/boese" and "unbekannter Eintrag PATH" in err and "FESTPOS_CONF_KEYS" in err


def test_latin1_bytes_are_kept_and_not_logged(tmp_path):
    v, err = read(tmp_path, "ADMIN_PASSWORD=Käse2026\nkaputte zeile mit geheim\n".encode("latin-1"))
    assert v["ADMIN_PASSWORD"].startswith("K") and v["ADMIN_PASSWORD"].endswith("se2026")
    assert "Zeile 2" in err and "geheim" not in err


def test_windows_bom_on_first_line(tmp_path):
    v, err = read(tmp_path, b"\xef\xbb\xbfADMIN_PASSWORD=Fest2026\r\nVIP=192.168.55.10\r\n")
    assert v["ADMIN_PASSWORD"] == "Fest2026" and v["VIP"] == "192.168.55.10" and "nicht verstanden" not in err


@pytest.mark.parametrize("script", ["setup.sh", "update.sh"])
def test_started_with_sh_switches_to_bash(tmp_path, script):
    """«sudo sh setup.sh» (ohne x-Recht nach Upload von Windows): wechselt selbst zu bash statt an Bash-Syntax
    zu scheitern. Der erste Schritt danach ist die root-Prüfung – die Meldung kommt also aus bash."""
    if not shutil.which("dash"):
        pytest.skip("braucht dash")
    cmd = ["dash"]
    if os.geteuid() == 0:                          # als root würde das Setup wirklich loslaufen: als nobody starten
        if not shutil.which("setpriv"):
            pytest.skip("braucht setpriv")
        cmd = ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", "dash"]
    base = Path(tempfile.mkdtemp(dir="/tmp"))      # für nobody lesbar (tmp_path ist es nicht)
    try:
        (base / "deploy").mkdir()
        for f in (script, "install-lib.sh", "festpos-conf.sh"):
            shutil.copy(LIB.parent / f, base / "deploy" / f)
            os.chmod(base / "deploy" / f, 0o644)   # wie nach dem Upload: kein x-Recht
        os.chmod(base, 0o755)
        os.chmod(base / "deploy", 0o755)
        out = subprocess.run(cmd + [str(base / "deploy" / script)], capture_output=True, text=True, timeout=30,
                             env={"PATH": os.environ["PATH"]})
    finally:
        shutil.rmtree(base)
    assert "Bitte mit sudo" in out.stdout + out.stderr, out.stderr
    assert "Syntax error" not in out.stderr and "Illegal option" not in out.stderr


def test_env_roundtrip_matches_systemd_quoting(tmp_path):
    env = tmp_path / "env"
    val = 'a`b$c\\d"e # f'
    script = (f'source "{LIB}"; festpos_env_line FESTPOS_ADMIN_PASSWORD \'{val.replace(chr(39), "")}\' > "{env}"; '
              f'unset FESTPOS_ADMIN_PASSWORD; festpos_read_conf "{env}"; printf "%s" "$FESTPOS_ADMIN_PASSWORD"')
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert out.stdout == val
    assert env.read_text() == 'FESTPOS_ADMIN_PASSWORD="a\\`b\\$c\\\\d\\"e # f"\n'


def test_wifi_prepare_removes_only_the_wifi_overlay(tmp_path):
    """Update aus dem Internet: dtoverlay=disable-wifi fällt weg (Funk bleibt per nmcli aus), Bluetooth bleibt aus."""
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "nmcli").write_text(f'#!/bin/sh\necho "$*" >> "{tmp_path}/nmcli.log"\n')
    (stub / "nmcli").chmod(0o755)
    cfg = tmp_path / "config.txt"
    cfg.write_text("[all]\ndtoverlay=disable-wifi\ndtoverlay=disable-bt\n")
    env = {"PATH": f"{stub}:{os.environ['PATH']}"}
    run = lambda: subprocess.run(["bash", "-c", f'source "{LIB}"; festpos_wifi_prepare "{tmp_path}"'], env=env)  # noqa: E731
    assert run().returncode == 0
    assert cfg.read_text() == "[all]\ndtoverlay=disable-bt\n"
    assert run().returncode == 1                                         # schon so: nichts geändert
    assert (tmp_path / "nmcli.log").read_text().count("radio wifi off") == 2


def test_wifi_prepare_country_and_radio_stays_on_while_connected(tmp_path):
    """WLAN-Land CH in cmdline.txt (einmal, in der einen Zeile); läuft «dauerhaft verbunden» über das WLAN
    (Verbindung festpos-internet aktiv, z.B. mitten in einem Update), bleibt der Funk an."""
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "nmcli").write_text(f'#!/bin/sh\necho "$*" >> "{tmp_path}/nmcli.log"\n'
                                '[ "$*" = "-t -f NAME connection show --active" ] && echo "${FAKE_ACTIVE:-}"\nexit 0\n')
    (stub / "nmcli").chmod(0o755)
    cmd = tmp_path / "cmdline.txt"
    cmd.write_text("console=serial0,115200 root=PARTUUID=abc rootwait\n")
    env = {"PATH": f"{stub}:{os.environ['PATH']}", "FAKE_ACTIVE": "festpos-internet"}
    run = lambda: subprocess.run(["bash", "-c", f'source "{LIB}"; festpos_wifi_prepare "{tmp_path}"'], env=env)  # noqa: E731
    assert run().returncode == 0 and run().returncode == 1
    assert cmd.read_text() == "console=serial0,115200 root=PARTUUID=abc rootwait cfg80211.ieee80211_regdom=CH\n"
    assert "radio wifi off" not in (tmp_path / "nmcli.log").read_text()
    env["FAKE_ACTIVE"] = "Wired connection 1"
    run()
    assert "radio wifi off" in (tmp_path / "nmcli.log").read_text()
