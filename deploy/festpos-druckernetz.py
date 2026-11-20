#!/usr/bin/env python3
"""FestPOS – Drucker-Netz einrichten (als root: bei jedem Start und über festpos-druckernetz.path, wenn die App die
Einstellung neu schreibt, app/printer_net.py).

Die Drucker hängen in einem eigenen VLAN, das nur die Pi erreichen. Dieser Pi bekommt dafür eine VLAN-Schnittstelle
(NetworkManager-Verbindung «festpos-drucker», z.B. eth0.56) mit der Adresse <Netz>.<letzte Stelle von NODE_IP>/24 –
ohne Gateway, ohne IPv6. NetworkManager merkt sich die Verbindung und bringt sie bei jedem Start selbst hoch.
Ausgeschaltet (VLAN 0): die Verbindung wird gelöscht. Die Firewall muss nichts wissen: aus dem Drucker-Netz kommen
nur Antworten herein (festpos-node.sh: policy drop), weitergeleitet wird nichts.

Die Datei schreibt die App (Benutzer festpos): darum hier alles nochmals prüfen, bevor es an nmcli geht.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SOLL = Path(os.environ.get("FESTPOS_PRINTER_NET_DIR", "/var/lib/festpos/druckernetz")) / "druckernetz.json"
ENV = Path(os.environ.get("FESTPOS_ENV_FILE", "/etc/festpos/env"))        # festpos-node.sh
CON = "festpos-drucker"
NET_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
IFACE_RE = re.compile(r"^[A-Za-z0-9._-]{1,15}$")              # wie IFACE in festpos-node.sh
MAX = 4096


def log(msg: str) -> None:
    print(f"festpos-druckernetz: {msg}", flush=True)


def lies_soll(path: Path = SOLL) -> dict | None:
    """{"vlan": 0} = aus, {"vlan": 56, "net": "192.168.56"} = ein; None = Datei unbrauchbar (dann nichts ändern).
    Fehlt die Datei, ist das Drucker-Netz aus."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {"vlan": 0}
    except OSError as e:
        log(f"{path} nicht lesbar: {e}")
        return None
    with os.fdopen(fd, "rb") as fh:
        raw = fh.read(MAX + 1)
    try:
        d = json.loads(raw.decode()) if len(raw) <= MAX else None
    except ValueError:
        d = None
    if not isinstance(d, dict) or type(d.get("vlan")) is not int:
        log(f"{path} ungültig – nichts geändert")
        return None
    if d["vlan"] == 0:
        return {"vlan": 0}
    m = NET_RE.match(str(d.get("net") or ""))
    if not 2 <= d["vlan"] <= 4094 or not m or any(int(x) > 255 for x in m.groups()):
        log(f"{path}: VLAN oder Netz ungültig – nichts geändert")
        return None
    return {"vlan": d["vlan"], "net": d["net"]}


def lies_env(path: Path = ENV) -> dict[str, str]:
    """KEY="Wert"-Zeilen von festpos_env_line (festpos-conf.sh)."""
    out = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        m = re.match(r'^([A-Z_][A-Z0-9_]*)="(.*)"$', line)
        if m:
            out[m.group(1)] = re.sub(r"\\(.)", r"\1", m.group(2))
    return out


def ziel(soll: dict, env: dict[str, str]) -> dict | None:
    """Wie die Verbindung sein soll -> {"parent", "id", "ifname", "addr"}; None = keine Verbindung (aus).
    ValueError, wenn es mit den Werten dieses Pi nicht geht."""
    if not soll["vlan"]:
        return None
    iface = env.get("FESTPOS_IFACE") or "eth0"
    if not IFACE_RE.match(iface):
        raise ValueError(f"Netzwerkgerät ungültig: {iface!r}")
    node = ipaddress.IPv4Address(env.get("NODE_IP", ""))
    main = ipaddress.IPv4Interface(f"{node}/{env.get('FESTPOS_NET_CIDR') or '24'}").network
    net = ipaddress.IPv4Network(f"{soll['net']}.0/24")
    if net.overlaps(main):
        raise ValueError(f"Drucker-Netz {net} überschneidet sich mit dem FestPOS-Netz {main}")
    ifname = f"{iface}.{soll['vlan']}"
    if len(ifname) > 15:                                   # Linux: höchstens 15 Zeichen
        ifname = f"vlan{soll['vlan']}"
    return {"parent": iface, "id": str(soll["vlan"]), "ifname": ifname, "addr": f"{soll['net']}.{node.packed[3]}/24"}


def nmcli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=30)


def vorhanden() -> dict | None:
    """Die Verbindung, wie sie jetzt ist -> {"parent", "id", "ifname", "addr", "active"}; None = gibt es nicht."""
    r = nmcli("-g", "vlan.parent,vlan.id,connection.interface-name,ipv4.addresses,GENERAL.STATE", "connection", "show", CON)
    if r.returncode != 0:
        return None
    f = (r.stdout.splitlines() + [""] * 5)[:5]
    return {"parent": f[0], "id": f[1], "ifname": f[2], "addr": f[3].replace(" ", ""), "active": f[4] == "activated"}


def plan(want: dict | None, have: dict | None) -> list[list[str]]:
    """nmcli-Aufrufe von «have» nach «want»."""
    if want is None:
        return [["connection", "delete", CON]] if have else []
    if have and all(have[k] == want[k] for k in ("parent", "id", "ifname", "addr")):
        return [] if have["active"] else [["connection", "up", CON]]
    out = [["connection", "delete", CON]] if have else []
    return out + [["connection", "add", "type", "vlan", "con-name", CON, "ifname", want["ifname"], "dev", want["parent"],
                   "id", want["id"], "ipv4.method", "manual", "ipv4.addresses", want["addr"], "ipv4.never-default", "yes",
                   "ipv6.method", "disabled", "connection.autoconnect", "yes"],
                  ["connection", "up", CON]]


def einrichten(soll: dict) -> int:
    try:
        want = ziel(soll, lies_env())
    except ValueError as e:
        log(f"FEHLER: {e} – nichts geändert")
        return 1
    steps = plan(want, vorhanden())
    if not steps:
        return 0
    log(f"Drucker-Netz: VLAN {want['id']} auf {want['ifname']}, Adresse {want['addr']}" if want else "Drucker-Netz aus")
    for args in steps:
        r = nmcli(*args)
        if r.returncode != 0:
            msg = (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
            if args[1] == "up":                            # z.B. Kabel draussen: NetworkManager holt es später nach
                log(f"WARNUNG: Verbindung noch nicht aktiv ({msg[0]})")
            else:
                log(f"FEHLER: nmcli {' '.join(args[:2])}: {msg[0]}")
                return 1
    return 0


def main() -> int:
    # Ändert die App die Datei, während dieser Lauf noch arbeitet, löst systemd nicht unbedingt nochmals aus: darum
    # am Schluss nochmals lesen und bei einer Änderung gleich nachziehen (höchstens ein paar Runden).
    rc = 1
    for _ in range(5):
        soll = lies_soll()
        if soll is None:
            return 1
        rc = einrichten(soll)
        if lies_soll() == soll:
            return rc
    return rc


if __name__ == "__main__":
    sys.exit(main())
