#!/usr/bin/env python3
"""FestPOS – feste DHCP-Zuteilungen übernehmen (festpos-dhcp-reload.path, als root, wenn die App sie neu schreibt).

dnsmasq liest neue Zeilen aus dhcp-hostsdir zwar selbst, vergisst aber gelöschte oder geänderte nie: Bekommt eine
Adresse eine andere MAC (Handy zurückgesetzt und neu ausgerollt, altes Handy gelöscht), meldet es «duplicate
dhcp-host IP address» und bleibt bei der alten Zuteilung. Darum hier jedes Mal neu laden (SIGHUP).

Hat die alte MAC die Adresse noch gemietet (Lease bis 1 Stunde), gibt dnsmasq sie trotzdem nicht her («not using
configured address … because it is leased to …»). Solche Leases – Adresse fest einer ANDEREN MAC zugeteilt – werden
gelöscht; dafür dnsmasq kurz anhalten, die Lease-Datei schreibt es sonst selbst.

Beide Dateien liegen in Ordnern des Dienstbenutzers festpos. Darum fasst dieses Programm sie nur mit DESSEN Rechten an
(als_dienst) und folgt keiner Verknüpfung (Review 2026-11, S3): sonst könnte, wer die App übernimmt, über eine
Verknüpfung «dhcp.leases -> /etc/…» root eine fremde Datei umschreiben lassen. root braucht es nur für systemctl.
"""
from __future__ import annotations

import contextlib
import os
import pwd
import stat
import subprocess
import sys
from pathlib import Path

HOSTS = Path(os.environ.get("FESTPOS_DHCP_DIR", "/var/lib/festpos/dhcp")) / "festpos-handys"   # app/dhcp.py
LEASES = Path(os.environ.get("FESTPOS_DHCP_LEASES", "/var/lib/festpos/dhcp.leases"))        # festpos-conf.sh
UNIT = "festpos-dhcp.service"
DIENST = os.environ.get("FESTPOS_DIENST_USER", "festpos")      # leer: nur Tests (kein Benutzer festpos)
MAX = 1024 * 1024


@contextlib.contextmanager
def als_dienst():
    """Als root: vorübergehend mit den Rechten des Dienstbenutzers (er besitzt die Dateien). Sonst unverändert
    (Tests laufen als normaler Benutzer). Gibt es den Benutzer nicht, wird nichts angefasst."""
    if not DIENST or os.geteuid() != 0:
        yield
        return
    pw = pwd.getpwnam(DIENST)                           # KeyError: kein Dienstbenutzer -> Abbruch statt als root
    groups = os.getgroups()
    os.setgroups([pw.pw_gid])
    os.setegid(pw.pw_gid)
    os.seteuid(pw.pw_uid)
    try:
        yield
    finally:
        os.seteuid(0)
        os.setegid(0)
        os.setgroups(groups)


def lies(path: Path) -> str:
    """Kleine, normale Datei lesen – keine Verknüpfung, kein Gerät, nicht riesig. OSError sonst."""
    with als_dienst():
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX:
                raise OSError(f"{path}: keine normale Datei")
            return os.read(fd, MAX).decode("utf-8", errors="replace")
        finally:
            os.close(fd)


def schreibe(path: Path, text: str) -> None:
    """Bestehende normale Datei an Ort und Stelle ersetzen (Besitzer bleibt festpos) – nie über eine Verknüpfung."""
    with als_dienst():
        fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError(f"{path}: keine normale Datei")
            os.ftruncate(fd, 0)
            data = memoryview(text.encode("utf-8"))
            while data:                              # os.write darf weniger schreiben als verlangt (Platte voll)
                data = data[os.write(fd, data):]
        finally:
            os.close(fd)


def reserved(text: str) -> dict[str, str]:
    """Feste Zuteilungen «mac,ip,name,dauer» -> {ip: mac}."""
    out = {}
    for line in text.splitlines():
        f = line.strip().split(",")
        if len(f) >= 2 and not line.startswith("#"):
            out.setdefault(f[1], f[0].lower())
    return out


def stale(lines: list[str], res: dict[str, str]) -> list[str]:
    """Leases («ablauf mac ip name client-id»), deren Adresse fest einer anderen MAC gehört."""
    out = []
    for line in lines:
        f = line.split()
        if len(f) >= 3 and f[2] in res and f[1].lower() != res[f[2]]:
            out.append(line)
    return out


def systemctl(*args: str) -> int:
    return subprocess.run(["systemctl", *args, UNIT], check=False).returncode


def main() -> int:
    if systemctl("is-active", "--quiet") != 0:
        return 0                                       # DHCP aus: nichts zu tun
    try:
        res = reserved(lies(HOSTS))
    except (OSError, KeyError):
        res = {}
    try:
        lines = lies(LEASES).splitlines()
    except (OSError, KeyError):
        lines = []
    old = stale(lines, res)
    if not old:
        return systemctl("reload")
    for line in old:
        print(f"Lease gelöscht (Adresse jetzt fest einer anderen MAC zugeteilt): {line}")
    systemctl("stop")
    try:
        lines = lies(LEASES).splitlines()              # nochmals: dnsmasq kann bis eben geschrieben haben
        old = stale(lines, res)
        schreibe(LEASES, "".join(f"{x}\n" for x in lines if x not in old))
    except (OSError, KeyError) as e:
        print(f"Leases nicht bereinigt: {e}")
    finally:
        rc = systemctl("start")
    return rc


if __name__ == "__main__":
    sys.exit(main())
