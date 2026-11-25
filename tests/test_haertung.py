"""T23: Härtung von SSH und sudo (deploy/festpos-haertung) – im Testverzeichnis, ohne root und ohne den Rechner zu
berühren: SSH-Schlüssel von der Boot-Partition, nur Schlüssel, sudo nur mit Passwort, nichts aussperren."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "festpos-haertung"
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl admin@pc"
KEY2 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBr3cQKNBcRw0YDMoLNAa3Qvh2rDzdvDJAD0n7pHn5xZ"


def _real_key() -> tuple[str, str]:
    """Zwei echte Schlüssel, wenn ssh-keygen da ist (das Skript prüft sie damit), sonst feste Beispiele."""
    if not shutil.which("ssh-keygen"):
        return KEY, KEY2
    out = []
    for i in range(2):
        d = Path(os.environ.get("TMPDIR", "/tmp"))
        f = d / f"festpos-test-key-{os.getpid()}-{i}"
        f.unlink(missing_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"test{i}", "-f", str(f)], check=True)
        out.append(Path(f"{f}.pub").read_text().strip())
        f.unlink(); Path(f"{f}.pub").unlink()
    return out[0], out[1]


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "root"
    (r / "etc/sudoers.d").mkdir(parents=True)
    (r / "boot/firmware").mkdir(parents=True)
    (r / "home/pi").mkdir(parents=True)
    (r / "etc/passwd").write_text("root:x:0:0:root:/root:/bin/bash\npi:x:1000:1000:,,,:/home/pi:/bin/bash\n"
                                  "festpos:x:999:999::/var/lib/festpos:/usr/sbin/nologin\n")
    (r / "etc/shadow").write_text("root:*:19000:0:99999:7:::\npi:$y$j9T$abc$def:19000:0:99999:7:::\n")
    (r / "etc/sudoers.d/010_pi-nopasswd").write_text("pi ALL=(ALL) NOPASSWD: ALL\n")
    return r


def run(r: Path, *args, path: Path | None = None) -> subprocess.CompletedProcess:
    """path: Ordner mit Attrappen (rpi-eeprom-config) vorne im PATH; ohne: das Werkzeug fehlt (wie auf dem Testrechner)."""
    env = {**os.environ, "FESTPOS_TEST_ROOT": str(r), "FESTPOS_BOOT": str(r / "boot/firmware"),
           "FESTPOS_HAERTUNG_STATE": str(r / "run/festpos-haertung.json"),
           "PATH": f"{path}:/usr/bin:/bin" if path else "/usr/bin:/bin"}
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=30)


def test_keys_from_boot_partition_ssh_keys_only_sudo_with_password(root):
    k1, k2 = _real_key()
    (root / "boot/firmware/festpos-ssh.pub").write_text(f"{k1}\r\n# Kommentar\nkein schluessel\n{k2}\r\n{k1}\n")
    p = run(root)
    assert p.returncode == 0, p.stderr
    ak = (root / "home/pi/.ssh/authorized_keys").read_text().splitlines()
    assert ak == [k1, k2]                                              # ohne CRLF, ohne Kommentar, nicht doppelt
    assert oct((root / "home/pi/.ssh").stat().st_mode & 0o777) == "0o700"
    assert oct((root / "home/pi/.ssh/authorized_keys").stat().st_mode & 0o777) == "0o600"
    assert not (root / "boot/firmware/festpos-ssh.pub").exists()
    assert (root / "boot/firmware/festpos-ssh.pub.uebernommen").exists()   # am PC sichtbar: übernommen
    sshd = (root / "etc/ssh/sshd_config.d/10-festpos.conf").read_text()
    for line in ("PasswordAuthentication no", "KbdInteractiveAuthentication no", "PermitRootLogin no",
                 "AuthenticationMethods publickey", "MaxAuthTries 3", "AllowTcpForwarding no"):
        assert f"\n{line}\n" in sshd
    assert not (root / "etc/sudoers.d/010_pi-nopasswd").exists()       # Passwort gesetzt: NOPASSWD weg
    sudo = (root / "etc/sudoers.d/festpos-haertung").read_text()
    assert "Defaults use_pty" in sudo and "timestamp_timeout=5" in sudo
    assert oct((root / "etc/sudoers.d/festpos-haertung").stat().st_mode & 0o777) == "0o440"
    st = json.loads(run(root, "--stand").stdout)
    assert st == {"ssh_keys_only": True, "user": "pi", "keys": 2, "sudo_without_password": False, "password_set": True,
                  "boot": "no_tool", "bt_off": False, "console_off": True}
    # nochmals (jeder Start): nichts doppelt, nichts kaputt
    (root / "boot/firmware/festpos-ssh.pub").write_text(k2 + "\n")
    assert run(root).returncode == 0
    assert (root / "home/pi/.ssh/authorized_keys").read_text().splitlines() == [k1, k2]


def test_user_without_password_keeps_sudo_and_nothing_breaks(root):
    (root / "etc/shadow").write_text("pi:!:19000:0:99999:7:::\n")       # Image ohne Imager-Passwort: gesperrt
    p = run(root)
    assert p.returncode == 0
    assert (root / "etc/sudoers.d/010_pi-nopasswd").exists()           # sonst gäbe es gar kein sudo mehr
    assert "kein Passwort" in p.stderr
    st = json.loads(run(root, "--stand").stdout)
    assert st["sudo_without_password"] is True and st["password_set"] is False and st["keys"] == 0
    assert (root / "etc/ssh/sshd_config.d/10-festpos.conf").exists()   # SSH trotzdem nur mit Schlüssel


def test_other_users_sudo_rules_stay(root):
    (root / "etc/sudoers.d/020_backup").write_text("backup ALL=(root) NOPASSWD: /usr/bin/rsync\n")
    assert run(root).returncode == 0
    assert (root / "etc/sudoers.d/020_backup").exists()


def test_wired_into_boot_install_and_firewall():
    node = (ROOT / "deploy/festpos-node.sh").read_text()
    lib = (ROOT / "deploy/install-lib.sh").read_text()
    assert "/usr/local/sbin/festpos-haertung" in node and node.index("festpos-haertung") < node.index('if [ ! -f "$CONF" ]')
    assert node.count("tcp dport 22 ct state new limit rate 6/minute") == 3   # Einrichtung, Betrieb, Notfall-Regel
    assert "tcp dport { 22," not in node
    assert '"festpos-haertung festpos-haertung 0755"' in lib and '"$SBIN/festpos-haertung"' in lib
    assert "deploy/festpos-haertung" in (ROOT / "tools/check.sh").read_text()
    # Einrichtungscode: tty1 zeigt ihn nur beim Start von getty – nach dem Schreiben neu zeigen (wenn tty1 frei ist)
    issue = node.index("/etc/issue.d/festpos.issue")
    assert node.index("systemctl try-restart getty@tty1.service", issue) - issue < 400


# -- Bootloader nur von der SD-Karte, Bluetooth aus, keine Anmeldung an der Konsole -------------------------------
EEPROM = r"""#!/bin/bash
# Attrappe von rpi-eeprom-config: ohne Argumente die aktuelle Einstellung, --apply DATEI merkt sie sich und legt das
# Update auf die Boot-Partition (wie rpi-eeprom-update)
if [ "${1:-}" = --apply ]; then
  [ "${FAKE_APPLY_RC:-0}" = 0 ] || exit 1
  cp "$2" "$FAKE_DIR/applied"; cp "$2" "$FAKE_DIR/staged"; echo "$2" >> "$FAKE_DIR/calls"
  touch "$FESTPOS_BOOT/pieeprom.upd" "$FESTPOS_BOOT/recovery.bin"
  exit 0
fi
if [ -n "${1:-}" ]; then cat "$FAKE_DIR/staged" 2>/dev/null; exit 0; fi   # Einstellungen eines Updates (pieeprom.upd)
cat "$FAKE_DIR/current"
"""


@pytest.fixture
def eeprom(tmp_path):
    d = tmp_path / "fake"
    d.mkdir()
    (d / "rpi-eeprom-config").write_text(EEPROM)
    (d / "rpi-eeprom-config").chmod(0o755)
    (d / "current").write_text("[all]\nBOOT_UART=1\nPOWER_OFF_ON_HALT=0\nBOOT_ORDER=0xf461\n")
    return d


def _run_fake(root: Path, eeprom: Path, *args, rc: str = "0") -> subprocess.CompletedProcess:
    os.environ["FAKE_DIR"], os.environ["FAKE_APPLY_RC"] = str(eeprom), rc
    try:
        return run(root, *args, path=eeprom)
    finally:
        del os.environ["FAKE_DIR"], os.environ["FAKE_APPLY_RC"]


def _state(root: Path) -> dict:
    return json.loads((root / "run/festpos-haertung.json").read_text())


def test_bootloader_only_sd_card_after_next_restart(root, eeprom):
    p = _run_fake(root, eeprom)
    assert p.returncode == 0, p.stderr
    applied = (eeprom / "applied").read_text()
    assert "BOOT_UART=1" in applied and "POWER_OFF_ON_HALT=0" in applied           # Rest bleibt
    assert "0xf461" not in applied
    assert applied.endswith("[all]\nBOOT_ORDER=0xf1\nNET_INSTALL_ENABLED=0\nNET_INSTALL_AT_POWER_ON=0\n")
    assert _state(root)["boot"] == "pending" and "nächsten Neustart" in p.stderr
    assert _run_fake(root, eeprom).returncode == 0                                # nochmals: nicht doppelt
    assert len((eeprom / "calls").read_text().splitlines()) == 1 and _state(root)["boot"] == "pending"
    (eeprom / "current").write_text(applied)                                       # nach dem Neustart
    for f in ("pieeprom.upd", "recovery.bin"):
        (root / "boot/firmware" / f).unlink()
    assert _run_fake(root, eeprom).returncode == 0
    st = _state(root)
    assert st["boot"] == "ok" and st["boot_order"] == "0xf1"
    assert len((eeprom / "calls").read_text().splitlines()) == 1


def test_foreign_staged_update_and_power_on_install_are_not_ok(root, eeprom):
    """Ein anderes, wartendes Bootloader-Update (ohne unsere Einstellungen) zählt nicht als «wirkt nach Neustart»;
    NET_INSTALL_AT_POWER_ON=1 ginge vor NET_INSTALL_ENABLED=0."""
    for f in ("pieeprom.upd", "recovery.bin"):
        (root / "boot/firmware" / f).touch()
    (eeprom / "staged").write_text("[all]\nBOOT_ORDER=0xf41\n")
    assert _run_fake(root, eeprom).returncode == 0
    assert len((eeprom / "calls").read_text().splitlines()) == 1 and _state(root)["boot"] == "pending"
    (eeprom / "current").write_text("[all]\nBOOT_ORDER=0xf1\nNET_INSTALL_ENABLED=0\nNET_INSTALL_AT_POWER_ON=1\n")
    for f in ("pieeprom.upd", "recovery.bin"):
        (root / "boot/firmware" / f).unlink()
    assert _run_fake(root, eeprom).returncode == 0
    assert "NET_INSTALL_AT_POWER_ON=0" in (eeprom / "applied").read_text()
    assert len((eeprom / "calls").read_text().splitlines()) == 2


def test_unsafe_staged_update_is_replaced_even_if_current_is_safe(root, eeprom):
    """Aktuell sicher, aber ein fremdes, unsicheres Update wartet auf den Neustart: es würde die Härtung aufheben – also
    neu schreiben (wirkt nach dem Neustart)."""
    (eeprom / "current").write_text("[all]\nBOOT_ORDER=0xf1\nNET_INSTALL_ENABLED=0\nNET_INSTALL_AT_POWER_ON=0\n")
    for f in ("pieeprom.upd", "recovery.bin"):
        (root / "boot/firmware" / f).touch()
    (eeprom / "staged").write_text("[all]\nBOOT_ORDER=0xf41\n")
    assert _run_fake(root, eeprom).returncode == 0
    assert len((eeprom / "calls").read_text().splitlines()) == 1 and _state(root)["boot"] == "pending"
    assert (eeprom / "applied").read_text().endswith("NET_INSTALL_AT_POWER_ON=0\n")


def test_values_in_other_sections_do_not_count(root, eeprom):
    """Ein [pi4]-Abschnitt mit den richtigen Werten macht einen Pi 5 nicht sicher – es wird neu geschrieben."""
    (root / "proc/device-tree").mkdir(parents=True, exist_ok=True)
    (root / "proc/device-tree/model").write_bytes(b"Raspberry Pi 5 Model B Rev 1.0\0")
    (eeprom / "current").write_text("[all]\nBOOT_ORDER=0xf461\n[pi4]\nBOOT_ORDER=0xf1\nNET_INSTALL_ENABLED=0\n"
                                    "NET_INSTALL_AT_POWER_ON=0\n")
    assert _run_fake(root, eeprom).returncode == 0
    applied = (eeprom / "applied").read_text()
    assert applied.endswith("[all]\nBOOT_ORDER=0xf1\nNET_INSTALL_ENABLED=0\nNET_INSTALL_AT_POWER_ON=0\n")
    assert "0xf461" not in applied and applied.count("BOOT_ORDER=") == 1
    (eeprom / "current").write_text("[pi5]\nBOOT_ORDER=0xf1\nNET_INSTALL_ENABLED=0\nNET_INSTALL_AT_POWER_ON=0\n")
    for f in ("pieeprom.upd", "recovery.bin"):
        (root / "boot/firmware" / f).unlink(missing_ok=True)
    assert _run_fake(root, eeprom).returncode == 0 and _state(root)["boot"] == "ok"     # [pi5] gilt auf dem Pi 5


def test_bootloader_failure_is_reported_not_fatal(root, eeprom):
    p = _run_fake(root, eeprom, rc="1")
    assert p.returncode == 0 and "WARNUNG" in p.stderr
    assert _state(root)["boot"] == "failed"
    assert run(root).returncode == 0 and _state(root)["boot"] == "no_tool"          # Werkzeug fehlt: nur melden


def test_bluetooth_off_once_and_in_all_section(root):
    cfg = root / "boot/firmware/config.txt"
    cfg.write_text("dtparam=audio=on\n[cm5]\ndtoverlay=dwc2,dr_mode=host\n")
    assert run(root).returncode == 0 and run(root).returncode == 0
    text = cfg.read_text()
    assert text.count("dtoverlay=disable-bt") == 1 and text.endswith("[all]\ndtoverlay=disable-bt\n")
    assert _state(root)["bt_off"] is True
    cfg.write_text("dtoverlay=disable-bt\n")                                      # schon da (Image): nichts dazu
    assert run(root).returncode == 0 and cfg.read_text() == "dtoverlay=disable-bt\n"
    cfg.write_text("[cm5]\ndtoverlay=disable-bt\n")                              # gilt nicht für einen Pi 5
    assert run(root).returncode == 0 and cfg.read_text().endswith("[cm5]\ndtoverlay=disable-bt\n\n[all]\ndtoverlay=disable-bt\n")
    assert _state(root)["bt_off"] is True


def test_console_shows_hint_but_takes_no_login(root):
    assert run(root).returncode == 0
    drop = (root / "etc/systemd/system/getty@tty1.service.d/zz-festpos-kein-login.conf").read_text()
    assert "ExecStart=\n" in drop and "--skip-login" in drop and "--kein-login" in drop and "--noclear" in drop
    assert "NAutoVTs=0" in (root / "etc/systemd/logind.conf.d/festpos-konsole.conf").read_text()
    for u in ("serial-getty@ttyAMA10.service", "serial-getty@ttyAMA0.service"):
        assert os.readlink(root / "etc/systemd/system" / u) == "/dev/null"
    assert _state(root)["console_off"] is True
    p = subprocess.run(["timeout", "1", "bash", str(SCRIPT), "--kein-login"], capture_output=True, text=True)
    assert "Anmelden am Bildschirm ist abgeschaltet" in p.stdout
