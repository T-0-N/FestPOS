"""T12: fertiges Image mit pi-gen – dieselben Bausteine wie setup.sh, Aufräumen nach karte-ohne.txt (wie T10),
Imager-Liste. Das eigentliche Bauen läuft in der CI (.github/workflows/image.yml) bzw. mit deploy/pi-gen/build.sh."""
from __future__ import annotations

import hashlib
import json
import lzma
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PG = ROOT / "deploy" / "pi-gen"


def _tree(tmp: Path) -> tuple[Path, Path]:
    root, boot = tmp / "rootfs", tmp / "rootfs" / "boot" / "firmware"
    files = {"etc/machine-id": "abc\n", "etc/ssh/ssh_host_ed25519_key": "k", "etc/ssh/sshd_config": "x",
             "var/lib/festpos/rqlite/db.sqlite": "db", "var/log/apt/history.log": "log", "opt/festpos/VERSION": "1",
             "var/lib/festpos/einrichtung/code": "ABCD-EFGH", "root/.bash_history": "ls"}
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    (boot / "festpos-tls").mkdir(parents=True)
    (boot / "festpos-tls" / "server.key").write_text("k")
    (boot / "festpos.conf").write_text("ADMIN_PASSWORD=x\n")
    (boot / "config.txt").write_text("arm_64bit=1\n")
    return root, boot


def test_clean_image_removes_everything_device_specific(tmp_path):
    root, boot = _tree(tmp_path)
    r = subprocess.run(["bash", str(PG / "clean-image.sh"), str(root), str(boot), str(ROOT / "deploy" / "karte-ohne.txt")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Image sauber" in r.stdout
    assert (root / "etc" / "machine-id").read_text() == ""
    assert not (root / "etc" / "ssh" / "ssh_host_ed25519_key").exists() and (root / "etc" / "ssh" / "sshd_config").exists()
    assert not list((root / "var" / "lib" / "festpos" / "rqlite").iterdir())
    assert (root / "var" / "log" / "apt").is_dir() and not (root / "var" / "log" / "apt" / "history.log").exists()
    assert not (boot / "festpos.conf").exists() and not (boot / "festpos-tls").exists() and (boot / "config.txt").exists()
    assert (root / "opt" / "festpos" / "VERSION").exists()


def test_clean_image_fails_when_something_stays(tmp_path):
    root, boot = _tree(tmp_path)
    liste = tmp_path / "leer.txt"
    liste.write_text("/root/.bash_history\n")
    r = subprocess.run(["bash", str(PG / "clean-image.sh"), str(root), str(boot), str(liste)], capture_output=True, text=True)
    assert r.returncode == 1 and "festpos.conf im Image" in r.stdout and "ssh_host_ed25519_key" in r.stdout


def test_os_list_entry(tmp_path):
    raw = b"FESTPOS" * 100_000
    img = tmp_path / "festpos-1.0.0.img.xz"
    img.write_bytes(lzma.compress(raw))
    r = subprocess.run([sys.executable, str(PG / "os_list.py"), str(img), "1.0.0"], capture_output=True, text=True)
    e = json.loads(r.stdout)["os_list"][0]
    assert e["extract_size"] == len(raw) and e["extract_sha256"] == hashlib.sha256(raw).hexdigest()
    assert e["image_download_sha256"] == hashlib.sha256(img.read_bytes()).hexdigest()
    # Download-Adresse über den Tag (v1.0.0), nicht die Version
    assert e["url"] == "https://github.com/T-0-N/FestPOS/releases/download/v1.0.0/festpos-1.0.0.img.xz"
    assert e["name"] == "FestPOS 1.0.0" and "pi5-64bit" in e["devices"]
    r = subprocess.run([sys.executable, str(PG / "os_list.py"), str(img), "1.0.0", "", "v1.0.0-rc.1"],
                       capture_output=True, text=True)
    assert json.loads(r.stdout)["os_list"][0]["url"].endswith("/v1.0.0-rc.1/festpos-1.0.0.img.xz")


def test_same_building_blocks_as_setup():
    run = (PG / "stage-festpos" / "00-festpos" / "01-run.sh").read_text()
    assert "FESTPOS_CHROOT=1 bash /opt/festpos-src/deploy/setup.sh" in run and "clean-image.sh" in run
    assert 'rm -f "${ROOTFS_DIR}/opt/festpos-src/deploy/festpos.conf"' in run
    setup = (ROOT / "deploy" / "setup.sh").read_text()
    assert '[ -n "$CHROOT" ] || systemctl daemon-reload' in setup
    cfg = (PG / "config").read_text()
    assert "ENABLE_SSH=0" in cfg and "stage-festpos" in cfg and "FIRST_USER_PASS" not in cfg
    wf = (ROOT / ".github" / "workflows" / "image.yml").read_text()
    assert "ubuntu-24.04-arm" in wf and "qemu-user-static" in wf and "deploy/pi-gen/build.sh" in wf
    assert "runner.arch != 'ARM64'" in wf                                  # QEMU nur auf einem x86-Runner
    rel = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "uses: ./.github/workflows/image.yml" in rel and "gh release upload" in rel and "--draft" in rel
    assert 'tools/pakete.py' in (PG / "build.sh").read_text()              # Python-Pakete und rqlite ins Image


def test_project_key_goes_into_the_image_after_cleaning():
    """Der Projekt-Schlüssel kommt NACH dem Aufräumen auf die Boot-Partition (karte-ohne.txt entfernt festpos-update*.pub);
    liegt er im Repo, muss er gültig sein (sonst nähmen Pi aus dem Image keine Updates an)."""
    run = (PG / "stage-festpos" / "00-festpos" / "01-run.sh").read_text()
    assert run.index("clean-image.sh") < run.index("festpos-update-projekt.pub")
    key = ROOT / "deploy" / "festpos-update-projekt.pub"
    if key.exists():
        sys.path.insert(0, str(ROOT))
        from app.release import parse_key_line
        assert parse_key_line(key.read_text()).fp.startswith("SHA256:")
    assert "festpos-update-projekt.pub" in (ROOT / ".github" / "workflows" / "release.yml").read_text()
