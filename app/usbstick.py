"""USB-Stick finden und kurz einbinden (für die root-Helfer, z.B. ca.key bei der Ersteinrichtung auf den Stick).

Nur Partitionen (oder ganze Sticks ohne Partitionstabelle) an USB, die nicht eingebunden sind und von denen der Pi
nicht startet. Eingebunden wird nur für die Dauer des Schreibens, mit rw,nosuid,nodev,noexec; danach wird alles auf
den Stick geschrieben (sync) und er wieder ausgehängt – er kann gleich abgezogen werden.
Wie festpos-sicherung (dort mit eigenem Ordner «festpos-sicherung» als Bedingung)."""
from __future__ import annotations

import contextlib
import os
import re
import subprocess
from pathlib import Path

SYS_BLOCK = Path(os.environ.get("FESTPOS_SYS_BLOCK", "/sys/block"))
PROC_MOUNTS = Path(os.environ.get("FESTPOS_PROC_MOUNTS", "/proc/self/mounts"))
MNT = Path(os.environ.get("FESTPOS_STICK_MOUNT", "/run/festpos-stick"))
DEV_RE = re.compile(r"^sd[a-z]{1,3}[0-9]{0,3}$")          # USB-Speicher (sda, sdb1 …)
FS_TYPES = {"vfat": "vfat", "exfat": "exfat", "ntfs": "ntfs3", "ext4": "ext4", "ext3": "ext3", "ext2": "ext2"}


def mounts() -> list[tuple[str, str]]:
    try:
        with open(PROC_MOUNTS, encoding="utf-8") as f:
            return [(ln.split()[0], ln.split()[1]) for ln in f if len(ln.split()) > 1]
    except OSError:
        return []


def candidates() -> list[str]:
    """Partitionen an USB, die nicht vom System benutzt werden (Reihenfolge: sda1, sda2, sdb …)."""
    try:
        disks = sorted(e.name for e in os.scandir(SYS_BLOCK) if DEV_RE.match(e.name))
    except OSError:
        return []
    mounted = mounts()
    out = []
    for disk in disks:
        if "/usb" not in os.path.realpath(SYS_BLOCK / disk):
            continue
        if any(src.startswith(f"/dev/{disk}") and (target == "/" or target.startswith("/boot")) for src, target in mounted):
            continue                                       # Pi startet von dieser USB-SSD
        parts = sorted(e.name for e in os.scandir(SYS_BLOCK / disk) if e.name.startswith(disk) and DEV_RE.match(e.name))
        out += [p for p in (parts or [disk]) if not any(src == f"/dev/{p}" for src, _ in mounted)]
    return out


@contextlib.contextmanager
def first_stick():
    """Ersten beschreibbaren Stick einbinden -> (Gerät, Ordner) oder (None, None), wenn keiner da ist."""
    for dev in candidates():
        try:
            fs = subprocess.run(["blkid", "-o", "value", "-s", "TYPE", f"/dev/{dev}"], capture_output=True, text=True,
                                timeout=20).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            continue
        if fs not in FS_TYPES:
            continue
        mnt = MNT / dev
        mnt.mkdir(mode=0o700, parents=True, exist_ok=True)
        r = subprocess.run(["mount", "-t", FS_TYPES[fs], "-o", "rw,nosuid,nodev,noexec,noatime", f"/dev/{dev}", str(mnt)],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            mnt.rmdir()
            continue
        try:
            yield dev, mnt
        finally:
            try:
                subprocess.run(["sync"], capture_output=True, timeout=60)
            finally:                                      # aushängen auch, wenn sync hängt (dessen Fehler bleibt)
                _release(mnt)
        return
    yield None, None


def _release(mnt: Path) -> None:
    """Stick aushängen – scheitert umount (auch mit einer Ausnahme), dann «umount -l»; nie eine eigene Ausnahme."""
    try:
        subprocess.run(["umount", str(mnt)], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        mnt.rmdir()
        return
    except OSError:
        pass
    try:
        subprocess.run(["umount", "-l", str(mnt)], capture_output=True, timeout=20)
        mnt.rmdir()
    except (OSError, subprocess.SubprocessError):
        pass
