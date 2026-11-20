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


def run(r: Path, *args) -> subprocess.CompletedProcess:
    env = {**os.environ, "FESTPOS_TEST_ROOT": str(r), "FESTPOS_BOOT": str(r / "boot/firmware")}
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
    assert st == {"ssh_keys_only": True, "user": "pi", "keys": 2, "sudo_without_password": False, "password_set": True}
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
