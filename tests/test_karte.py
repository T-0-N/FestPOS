"""SD-Karte im USB-Kartenleser schreiben (T10): festpos-karte mit gefälschtem /sys/block, gefälschten Programmen
(sfdisk, mkfs, mount …, die nur mitschreiben) und Ordnern statt Partitionen – ohne root, ohne echte Karte.
Echtes Partitionieren und Starten der Karte: Abhakliste in docs/testplan-1.0.md (Block C)."""
from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app import backup
from tests.test_api import AUTH
from tests.test_wiederherstellen import CONF, make_tls

ROOT = Path(__file__).resolve().parent.parent
KARTE = ROOT / "deploy" / "festpos-karte"
WH = ROOT / "deploy" / "festpos-wiederherstellen"
OLD_ID = "1a2b3c4d"
GB = 10 ** 9

FAKES = {
    # udevadm info --query=property --name=DEV/sdX | udevadm settle
    "udevadm": 'echo "udevadm $*" >> "$FAKE/log"; [ "$1" = info ] || exit 0\n'
               'n=${3#--name=}; n=${n##*/}; [ -f "$FAKE/serial-$n" ] && echo "ID_SERIAL_SHORT=$(cat "$FAKE/serial-$n")"\n'
               'echo ID_BUS=usb',
    "blkid": 'n=${3##*/}; [ -f "$FAKE/blkid-$n" ] && cat "$FAKE/blkid-$n"; exit 0',
    # mount -t FS -o OPT DEV ZIEL: Inhalt aus FAKE/media-<dev> hineinkopieren (Prüfung «Sicherungsstick»)
    "mount": 'echo "mount $*" >> "$FAKE/log"; for a; do :; done; ziel=$a; dev=${@: -2:1}; dev=${dev##*/}\n'
             '[ -d "$FAKE/media-$dev" ] && cp -a "$FAKE/media-$dev/." "$ziel/"; exit 0',
    "umount": 'echo "umount $*" >> "$FAKE/log"; for a; do :; done; case ${a##*/} in pruef-*) rm -rf "${a:?}"/* ;; esac; exit 0',
    "sfdisk": 'echo "sfdisk $*" >> "$FAKE/log"; cat > "$FAKE/sfdisk.txt"; for a; do :; done\n'
              'touch "$a"1 "$a"2; exit 0',
}
LOGGERS = ("wipefs", "mkfs.vfat", "mkfs.ext4", "fsck.vfat", "e2fsck", "blockdev", "systemctl")


def _fake_bin(d: Path) -> None:
    d.mkdir()
    for name in LOGGERS:
        FAKES.setdefault(name, f'echo "{name} $*" >> "$FAKE/log"; [ -f "$FAKE/slow-{name}" ] && sleep 3\n'
                               f'[ -f "$FAKE/fail-once-{name}" ] && rm "$FAKE/fail-once-{name}" && exit 8\n'
                               f'[ -f "$FAKE/fail-{name}" ] && exit 1; exit 0')
    for name, body in FAKES.items():
        p = d / name
        p.write_text(f"#!/bin/bash\n{body}\n")
        p.chmod(0o755)


def _disk(sys_block: Path, devices: Path, name: str, size: int, *, usb=True, removable=True, parts=(),
          vendor="Generic", model="SD Reader"):
    real = devices / ("usb1/1-1/1-1:1.0/host0" if usb else "ata1/host0") / "block" / name
    (real / "device").mkdir(parents=True)
    (real / "size").write_text(str(size // 512))
    (real / "removable").write_text("1" if removable else "0")
    (real / "device" / "vendor").write_text(vendor)
    (real / "device" / "model").write_text(model)
    for p in parts:
        (real / p).mkdir()
    (sys_block / name).symlink_to(real)


@pytest.fixture
def pi(tmp_path):
    """Pi A im Einzelbetrieb: Boot-Partition, Systempartition (Quelle), Kartenleser in /sys/block, gefälschte Programme."""
    boot, src, etc = tmp_path / "boot", tmp_path / "quelle", tmp_path / "etc"
    (boot / "festpos-tls").mkdir(parents=True)
    etc.mkdir()
    for n, v in make_tls("A").items():
        (boot / "festpos-tls" / n).write_bytes(v)
    (boot / "festpos.conf").write_text(CONF)
    (boot / "festpos-update.pub").write_text("ssh-ed25519 AAAA schluessel")
    (boot / "cmdline.txt").write_text(f"console=tty1 root=PARTUUID={OLD_ID}-02 rootfstype=ext4 rootwait\n")
    (boot / "config.txt").write_text("arm_64bit=1\n")
    (boot / "kernel_2712.img").write_bytes(b"K" * 5000)
    (boot / "overlays").mkdir()
    (boot / "overlays" / "x.dtbo").write_bytes(b"o")
    (boot / "festpos-tls.alt-20261001").mkdir()
    (boot / "festpos-tls.alt-20261001" / "server.key").write_text("ALT")
    files = {
        "etc/fstab": f"proc /proc proc defaults 0 0\nPARTUUID={OLD_ID}-01 /boot/firmware vfat defaults 0 2\n"
                     f"PARTUUID={OLD_ID}-02 / ext4 defaults,noatime 0 1\n",
        "etc/machine-id": "0123456789abcdef0123456789abcdef\n",
        "etc/ssh/ssh_host_ed25519_key": "GEHEIM", "etc/ssh/sshd_config": "Port 22\n", "etc/ssh/.festpos-node": "a\n",
        "var/lib/festpos/rqlite/db.sqlite": "DATENBANK", "var/lib/festpos/rqlite/.festpos-member": "",
        "var/lib/festpos/dhcp.leases": "lease", "var/lib/festpos/fully/fully.apk": "APK",
        "var/lib/festpos/adb/adbkey": "ADB", "var/log/apt/history.log": "log", "var/log/syslog": "log",
        "var/lib/festpos-sicherung/status.json": "{}", "opt/festpos/VERSION": "2026.11.01-1\n",
        "usr/bin/festpos-x": "#!/bin/sh\n", "root/.bash_history": "ls", "proc/1/status": "x",
    }
    for rel, text in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(text)
    (src / "tmp").mkdir()
    sys_block, devices, dev, fake, kdir = (tmp_path / x for x in ("sys/block", "sys/devices", "dev", "fake", "run/karte"))
    for d in (sys_block, devices, dev, fake):
        d.mkdir(parents=True)
    _fake_bin(tmp_path / "bin")
    _disk(sys_block, devices, "sda", 32 * GB, parts=("sda1", "sda2"))           # Karte im Leser: erlaubt
    (fake / "serial-sda").write_text("READER123")
    (fake / "blkid-sda1").write_text("LABEL=bootfs\nTYPE=vfat\n")
    _disk(sys_block, devices, "sdb", 0)                                          # Leser ohne Karte
    _disk(sys_block, devices, "sdc", 500 * GB, removable=False)                  # USB-SSD
    _disk(sys_block, devices, "sdd", 64 * GB, parts=("sdd1",), model="Stick")    # Sicherungsstick
    (fake / "blkid-sdd1").write_text("LABEL=FESTPOS\nTYPE=exfat\n")
    (fake / "media-sdd1" / backup.STICK_DIR).mkdir(parents=True)
    _disk(sys_block, devices, "sde", 64 * GB, parts=("sde1", "sde2"))            # Pi startet davon (USB-Boot)
    _disk(sys_block, devices, "sdf", 4 * GB)                                     # zu klein
    (tmp_path / "mounts").write_text(f"{dev}/sde2 / ext4 rw 0 0\nproc /proc proc rw 0 0\n")
    env = {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "FAKE": str(fake), "FESTPOS_RELEASE_LIB": str(ROOT),
           "FESTPOS_SYS_BLOCK": str(sys_block), "FESTPOS_DEV": str(dev), "FESTPOS_PROC_MOUNTS": str(tmp_path / "mounts"),
           "FESTPOS_KARTE_DIR": str(kdir), "FESTPOS_KARTE_QUELLE": str(src), "FESTPOS_BOOT": str(boot),
           "FESTPOS_ETC": str(etc), "FESTPOS_KARTE_OHNE": str(ROOT / "deploy" / "karte-ohne.txt"),
           "FESTPOS_USB_LOCK": str(tmp_path / "usb.lock"), "FESTPOS_KARTE_OHNE_ATTR": "1",
           "FESTPOS_KARTE_BELEGT": str(3 * GB), "FESTPOS_KARTE_BOOT_BYTES": str(512 * 1024 ** 2),
           "FESTPOS_KARTE_FSCK_WAIT": "0"}
    return {"tmp": tmp_path, "boot": boot, "src": src, "dev": dev, "fake": fake, "kdir": kdir, "env": env,
            "sys": sys_block}


def karte(pi, *args, stdin: dict | None = None, wait=True):
    cmd = [sys.executable, "-I", str(KARTE), *args]
    if not wait:
        return subprocess.Popen(cmd, env=pi["env"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    r = subprocess.run(cmd, env=pi["env"], input=json.dumps(stdin or {}), capture_output=True, text=True, timeout=60)
    return r


def liste(pi) -> dict:
    r = karte(pi, "--liste")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def log(pi) -> str:
    try:
        return (pi["fake"] / "log").read_text()
    except OSError:
        return ""


def test_liste_erlaubt_nur_wechselmedien(pi):
    res = liste(pi)
    by = {d["device"]: d for d in res["devices"]}
    assert by["sda"]["allowed"] and by["sda"]["serial"] == "READER123" and by["sda"]["size"] == 32 * GB
    assert by["sda"]["parts"][0] == {"name": "sda1", "label": "bootfs", "fs": "vfat"}
    assert {k: v["reason"] for k, v in by.items() if k != "sda"} == {
        "sdb": "no_card", "sdc": "not_removable", "sdd": "stick", "sde": "system", "sdf": "too_small"}
    assert res["need"] == 512 * 1024 ** 2 + int(3 * GB * 1.2) + 1024 ** 3
    assert not list((pi["kdir"]).glob("pruef-*"))                       # Prüf-Einhängepunkte aufgeräumt
    assert "mount -t exfat -o ro" in log(pi)                            # Stick nur lesend angesehen


def _job(pi, letter="b", **kw):
    return {"device": "sda", "serial": "READER123", "size": 32 * GB, "letter": letter, "confirm": letter, **kw}


def test_auftrag_prueft_alles(pi):
    def code(**kw):
        r = karte(pi, "--auftrag", stdin=_job(pi, **kw))
        return json.loads(r.stdout).get("code")
    assert code(confirm="c") == "confirm"
    assert code(letter="x", confirm="x") == "letters"
    assert code(device="../sda") == "device"
    assert code(device="sdd") == "stick"
    assert code(device="sde") == "system"
    assert code(serial="ANDERS") == "changed"
    assert code(size=16 * GB) == "changed"
    assert "systemctl" not in log(pi)
    r = karte(pi, "--auftrag", stdin=_job(pi))
    assert r.returncode == 0 and json.loads(r.stdout)["ok"], r.stdout
    assert "systemctl start --no-block festpos-karte.service" in log(pi)
    job = json.loads((pi["kdir"] / "auftrag.json").read_text())
    assert job["device"] == "sda" and job["letter"] == "b"
    assert stat.S_IMODE((pi["kdir"] / "auftrag.json").stat().st_mode) == 0o600
    assert json.loads((pi["kdir"] / "status.json").read_text())["state"] == "running"
    assert json.loads(karte(pi, "--auftrag", stdin=_job(pi)).stdout)["code"] == "busy"   # läuft schon


def schreiben(pi, letter="b") -> dict:
    assert karte(pi, "--auftrag", stdin=_job(pi, letter)).returncode == 0
    r = karte(pi, "--schreiben")
    st = json.loads((pi["kdir"] / "status.json").read_text())
    return {"rc": r.returncode, "err": r.stderr, **st}


def test_karte_fuer_pi_b(pi):
    st = schreiben(pi, "b")
    assert st["rc"] == 0 and st["state"] == "done" and st["step"] == "auswerfen", st["err"]
    assert st["done"] > 0 and st["total"] > 0
    root, boot = pi["kdir"] / "ziel-root", pi["kdir"] / "ziel-boot"
    # neue Disk-ID, beide Partitionen
    script = (pi["fake"] / "sfdisk.txt").read_text()
    new_id = script.split("label-id: 0x")[1][:8]
    assert new_id != OLD_ID and "type=c" in script and "type=83" in script
    assert f"root=PARTUUID={new_id}-02" in (boot / "cmdline.txt").read_text()
    fstab = (root / "etc" / "fstab").read_text()
    assert f"PARTUUID={new_id}-01 /boot/firmware" in fstab and f"PARTUUID={new_id}-02 /" in fstab and OLD_ID not in fstab
    # System kopiert, aber ohne Datenbank, Leases, Logs, Schlüssel dieses Pi
    assert (root / "opt" / "festpos" / "VERSION").read_text() == "2026.11.01-1\n"
    assert (root / "var" / "lib" / "festpos" / "fully" / "fully.apk").exists()
    assert (root / "etc" / "ssh" / "sshd_config").exists()
    assert (root / "var" / "lib" / "festpos" / "rqlite").is_dir()
    assert not list((root / "var" / "lib" / "festpos" / "rqlite").iterdir())
    for gone in ("var/lib/festpos/dhcp.leases", "var/lib/festpos/adb/adbkey", "var/log/syslog", "var/log/apt/history.log",
                 "etc/ssh/ssh_host_ed25519_key", "etc/ssh/.festpos-node", "var/lib/festpos-sicherung/status.json",
                 "root/.bash_history", "proc/1"):
        assert not (root / gone).exists(), gone
    assert (root / "var" / "log" / "apt").is_dir()                     # Ordner der Logs bleiben
    assert (root / "etc" / "machine-id").read_text() == ""
    assert stat.S_IMODE((root / "tmp").stat().st_mode) == 0o1777 and (root / "boot" / "firmware").is_dir()
    # Boot-Partition: System dieses Pi + Dateien für Pi B
    assert (boot / "kernel_2712.img").read_bytes() == b"K" * 5000 and (boot / "overlays" / "x.dtbo").exists()
    assert not (boot / "festpos-tls.alt-20261001").exists()
    assert (boot / "node.conf").read_text() == "NODE=b\n"
    conf = (boot / "festpos.conf").read_text()
    assert backup.conf_value(conf, "CLUSTER_SIZE") == "3"
    assert backup.conf_value(conf, "CLUSTER_SECRET").startswith("auto-")
    assert (boot / "festpos-tls" / "server.key").read_bytes() == (pi["boot"] / "festpos-tls" / "server.key").read_bytes()
    assert (boot / "festpos-update.pub").exists()
    # geprüft und ausgeworfen
    lg = log(pi)
    assert "wipefs -a" in lg and "mkfs.vfat -F 32 -n bootfs" in lg and "mkfs.ext4 -F -q -L rootfs" in lg
    assert "fsck.vfat -n" in lg and "e2fsck -fn" in lg and "blockdev --flushbufs" in lg
    assert (pi["sys"] / "sda" / "device" / "delete").read_text() == "1"
    assert not (pi["kdir"] / "auftrag.json").exists()


def test_ersatzkarte_einzel_pi(pi):
    st = schreiben(pi, "a")
    assert st["state"] == "done", st
    conf = (pi["kdir"] / "ziel-boot" / "festpos.conf").read_text()
    assert backup.conf_value(conf, "CLUSTER_SIZE") == "1" and conf == CONF
    assert (pi["kdir"] / "ziel-boot" / "node.conf").read_text() == "NODE=a\n"


def test_fehler_loescht_die_tabelle(pi):
    (pi["fake"] / "fail-mkfs.ext4").touch()
    st = schreiben(pi)
    assert st["rc"] == 2 and st["state"] == "failed" and st["code"] == "mkfs"
    assert log(pi).count("wipefs -a") == 2                         # beim Partitionieren und beim Aufräumen


def test_fsck_nach_dem_aushaengen_wartet_und_versucht_nochmals(pi):
    """Direkt nach dem Aushängen liest udev die Karte kurz an; e2fsck bricht dann ab («aborted»). Der Pi wartet
    (udevadm settle) und prüft nochmals – die Karte gilt als geschrieben. Ein bleibender Fehler lässt es scheitern."""
    (pi["fake"] / "fail-once-e2fsck").touch()
    st = schreiben(pi, "a")
    assert st["state"] == "done", st
    lg = log(pi)
    assert lg.count("e2fsck -fn") == 2 and "udevadm settle" in lg[lg.index("umount"):]
    (pi["fake"] / "fail-e2fsck").touch()
    st = schreiben(pi, "a")
    assert st["state"] == "failed" and st["code"] == "fsck"


def test_karte_gewechselt_nichts_geschrieben(pi):
    assert karte(pi, "--auftrag", stdin=_job(pi)).returncode == 0
    (pi["sys"] / "sda").resolve().joinpath("size").write_text(str(64 * GB // 512))   # andere Karte im Leser
    r = karte(pi, "--schreiben")
    st = json.loads((pi["kdir"] / "status.json").read_text())
    assert r.returncode == 2 and st["code"] == "changed"
    assert "sfdisk" not in log(pi) and "wipefs" not in log(pi)


def test_abbrechen(pi):
    (pi["fake"] / "slow-mkfs.ext4").touch()
    assert karte(pi, "--auftrag", stdin=_job(pi)).returncode == 0
    p = karte(pi, "--schreiben", wait=False)
    end = time.time() + 20
    while time.time() < end and "mkfs.ext4" not in log(pi):
        time.sleep(0.1)
    p.send_signal(signal.SIGTERM)
    assert p.wait(30) == 3
    st = json.loads((pi["kdir"] / "status.json").read_text())
    assert st["state"] == "cancelled"
    assert log(pi).count("wipefs -a") == 2


def test_status_ohne_lebenszeichen(pi):
    pi["kdir"].mkdir(parents=True)
    (pi["kdir"] / "status.json").write_text(json.dumps({"id": "x", "state": "running", "at": time.time() - 600}))
    assert json.loads(karte(pi, "--status").stdout)["code"] == "stale"


def test_ausschlussliste():
    """Pflichteinträge: keine Datenbank, keine Leases, keine Gerätekennung, keine SSH-Schlüssel (auch für T12)."""
    lines = [ln.split("#", 1)[0].strip() for ln in (ROOT / "deploy" / "karte-ohne.txt").read_text().splitlines()]
    lines = [ln for ln in lines if ln]
    for must in ("/var/lib/festpos/rqlite/*", "/var/lib/festpos/dhcp.leases", "/etc/machine-id", "/etc/ssh/ssh_host_*",
                 "/var/lib/festpos/adb/*", "/var/log/* ordner", "boot:festpos.conf", "boot:festpos-tls"):
        assert must in lines, must
    assert all(ln.startswith(("/", "boot:")) for ln in lines)


# -- API (über festpos-wiederherstellen) ------------------------------------------------------------------------
@pytest.fixture
def api_pi(pi, monkeypatch):
    from app import cards
    for k in ("jobs", "neu", "out"):
        (pi["tmp"] / k).mkdir()
    env = {**pi["env"], "FESTPOS_WH_DIR": str(pi["tmp"] / "jobs"), "FESTPOS_WH_ERGEBNIS": str(pi["tmp"] / "out"),
           "FESTPOS_WH_TROCKEN": "1", "FESTPOS_KARTE_BIN": str(KARTE), "FESTPOS_KARTE_PYTHON": "1"}
    monkeypatch.setattr(cards, "WH_DIR", pi["tmp"] / "jobs")
    monkeypatch.setattr(cards, "WH_NEU", pi["tmp"] / "neu")
    monkeypatch.setattr(cards, "WH_ERGEBNIS", pi["tmp"] / "out")
    monkeypatch.setattr(cards, "KARTE_DIR", pi["kdir"])
    real_wait = cards.wait

    def wait(job, timeout=30.0):
        subprocess.run([sys.executable, "-I", str(WH), "--anfrage"], env=env, capture_output=True, timeout=60)
        return real_wait(job, 2.0)
    monkeypatch.setattr(cards, "wait", wait)
    return pi


def test_api_karte_schreiben(client, api_pi):
    r = client.get("/api/admin/cluster/karten", auth=AUTH)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["helper"] and d["letters"] == ["a", "b", "c"] and d["status"] is None and d["written"] == {}
    sda = next(x for x in d["devices"] if x["device"] == "sda")
    body = {"device": "sda", "serial": sda["serial"], "size": sda["size"], "letter": "b", "confirm": "c"}
    assert client.post("/api/admin/cluster/karte", auth=AUTH, json=body).status_code == 400
    assert client.post("/api/admin/cluster/karte", auth=AUTH, json={**body, "device": "/dev/mmcblk0"}).status_code == 422
    r = client.post("/api/admin/cluster/karte", auth=AUTH, json={**body, "confirm": "B", "serial": "X"})
    assert r.status_code == 409
    r = client.post("/api/admin/cluster/karte", auth=AUTH, json={**body, "confirm": "B"})
    assert r.status_code == 200, r.text
    # der Dienst (hier direkt) schreibt die Karte
    subprocess.run([sys.executable, "-I", str(KARTE), "--schreiben"], env=api_pi["env"], capture_output=True, timeout=60)
    st = client.get("/api/admin/cluster/karte", auth=AUTH).json()
    assert st["status"]["state"] == "done" and st["written"]["b"]
    client.get("/api/admin/cluster/karte", auth=AUTH)                       # Protokoll nur einmal
    acts = [e["action"] for e in client.get("/api/admin/audit", auth=AUTH).json()["entries"]]
    assert acts.count("card_written") == 1 and "card_write" in acts


def test_api_nur_admin_und_ohne_helfer(client):
    from app import cards
    assert client.get("/api/admin/cluster/karten").status_code == 401
    d = client.get("/api/admin/cluster/karten", auth=AUTH).json()
    assert not d["helper"] and d["devices"] == [] and not cards.available()
    assert client.post("/api/admin/cluster/karte", auth=AUTH, json={"device": "sda", "serial": "", "size": 1,
                                                                    "letter": "b", "confirm": "b"}).status_code == 503


def test_ssh_schluessel_auch_ohne_markierung():
    text = (ROOT / "deploy" / "festpos-node.sh").read_text()
    assert 'compgen -G "/etc/ssh/ssh_host_*_key"' in text


def test_installiert():
    lib = (ROOT / "deploy" / "install-lib.sh").read_text()
    assert '"festpos-karte festpos-karte 0755"' in lib and "karte-ohne.txt" in lib
    assert "/run/festpos/karte" in (ROOT / "deploy" / "festpos-tmpfiles.conf").read_text()
    unit = (ROOT / "deploy" / "systemd" / "festpos-karte.service").read_text()
    assert "IOSchedulingClass=idle" in unit and "Nice=19" in unit and "[Install]" not in unit
    assert os.access(KARTE, os.X_OK)
