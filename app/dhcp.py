"""DHCP für die Handys: feste Zuteilungen MAC -> IP für dnsmasq (deploy/systemd/festpos-dhcp.service).

festpos-node.sh schreibt bei DHCP=1 die dnsmasq-Konfiguration; dnsmasq liest die festen Zuteilungen aus einem
Verzeichnis und bemerkt Änderungen selbst. Jeder Pi schreibt sie dort aus der Datenbank (Handys mit MAC unter
«Handys auf der Statusanzeige») – so vergeben alle Pi dieselbe Adresse an dasselbe Handy. Die MAC trägt
«Neues Handy ausrollen» ein (app/fully.py): sie stammt aus der ARP-Tabelle, wenn das Handy beim Einrichten seine
Einstellungen abholt – also die MAC, die das Handy im WLAN wirklich zeigt (auch eine zufällige).
Wechselt die MAC eines Handys mit bekanntem Zertifikat, schreibt mac_change.py die Zuteilung um (T4).
Geänderte oder gelöschte Zuteilungen vergisst dnsmasq von selbst nicht: festpos-dhcp-reload (root, über
festpos-dhcp-reload.path) lädt es neu und löscht Leases von Adressen, die jetzt einer anderen MAC gehören.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
from pathlib import Path

from . import device_ids
from .core import rt

log = logging.getLogger("festpos.dhcp")

HOSTS_DIR = Path(os.environ.get("FESTPOS_DHCP_DIR", "/var/lib/festpos/dhcp"))          # deploy/festpos-conf.sh
HOSTS_FILE = "festpos-handys"
CONF = Path(os.environ.get("FESTPOS_DHCP_CONF", "/etc/festpos/dnsmasq.conf"))
OPTS_DIR = Path(os.environ.get("FESTPOS_DHCP_OPTS", "/var/lib/festpos/dhcp-opts"))     # Gateway bei «auto»
OPTS_FILE = "festpos-gateway"
VIP = os.environ.get("FESTPOS_VIP") or "192.168.55.10"
ARP = Path("/proc/net/arp")
LEASE = "1h"                                   # kurz: ein neues Gateway kommt nach höchstens 30 Min an
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def enabled() -> bool:
    """DHCP=1 auf diesem Pi (festpos-node hat die Konfiguration geschrieben)."""
    return CONF.is_file()


def mac(v) -> str | None:
    """MAC in einheitlicher Schreibweise (klein, mit «:»), sonst None."""
    m = str(v or "").strip().lower().replace("-", ":")
    return m if MAC_RE.match(m) and m != "00:00:00:00:00:00" else None


def _hostname(name: str) -> str:
    h = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")[:30]
    return h or "handy"


def hosts(devices: list[dict]) -> str:
    """Inhalt der Zuteilungsdatei (Format wie dhcp-host): «mac,ip,name,dauer» pro Handy."""
    lines = ["# FestPOS – feste Zuteilungen der Handys (aus der Verwaltung, nicht von Hand ändern)"]
    seen_ip, seen_mac, seen_host = set(), set(), set()
    for d in devices:
        m = mac(d.get("mac"))
        if d.get("kind") != "phone" or not m:
            continue
        try:
            ip = str(ipaddress.IPv4Address(str(d.get("ip") or "")))
        except ValueError:
            continue
        if ip in seen_ip or m in seen_mac:                    # doppelt: nur die erste Zuteilung gilt
            continue
        seen_ip.add(ip)
        seen_mac.add(m)
        host = base = _hostname(str(d.get("name") or ""))
        n = 1
        while host in seen_host:                              # gleichnamige Altgeräte (vor T1): eigener Hostname
            n += 1
            host = f"{base[:27]}-{n}"
        seen_host.add(host)
        lines.append(f"{m},{ip},{host},{LEASE}")
    return "\n".join(lines) + "\n"


_written: str | None = None


def wanted() -> str | None:
    """Inhalt der Zuteilungsdatei, wie er aus der Datenbank sein soll (None: Einstellung unlesbar)."""
    try:
        items = json.loads(rt.store.setting("monitor_devices") or "[]")
    except ValueError:
        return None
    items = [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
    if any(x.get("cert") for x in items):                     # gesperrte Geräte: keine Reservierung, solange gesperrt (T3)
        off = device_ids.blocked(device_ids.lineages(rt.store))
        items = [x for x in items if x.get("cert") not in off]
    return hosts(items)


def _count(content: str) -> int:
    return sum(1 for ln in content.splitlines() if ln and not ln.startswith("#"))


def reservations_expected() -> int:
    return _count(wanted() or "")


def reservations_written() -> int | None:
    """Zuteilungen in der Datei für dnsmasq (None: noch keine Datei) – Prüfliste nach dem Einspielen (T8)."""
    try:
        return _count((HOSTS_DIR / HOSTS_FILE).read_text())
    except OSError:
        return None


def sync() -> None:
    """Zuteilungen in die Datei für dnsmasq schreiben, wenn sie sich geändert haben (Monitor, alle paar Sekunden)."""
    global _written
    if not HOSTS_DIR.is_dir():
        return                                                # DHCP aus oder Rechner ohne FestPOS-Einrichtung
    content = wanted()
    if content is None or content == _written:
        return
    tmp = HOSTS_DIR / f".{HOSTS_FILE}.neu"                     # mit «.»: liest dnsmasq nicht
    try:
        tmp.write_text(content)
        os.replace(tmp, HOSTS_DIR / HOSTS_FILE)
    except OSError:
        log.exception("DHCP-Zuteilungen nicht geschrieben")
        return
    _written = content
    log.info("DHCP-Zuteilungen geschrieben: %d Handys", content.count("\n") - 1)


# -- Gateway der Handhelds ------------------------------------------------------------------------
# Ohne DHCP_GATEWAY in festpos.conf folgt das Gateway dem Weg ins Internet (Verwaltung › Internet): Router im
# FestPOS-Netz bei «Dauerhaft verbunden» -> dessen Adresse (die Handhelds gehen direkt über den Router); sonst die Pi
# (VIP) – sie geben das Internet bei WLAN/USB an die eingetragenen Handhelds weiter (update_net.keep_tick).
def auto_gateway() -> bool:
    """DHCP-Gateway automatisch (festpos-node.sh hat dhcp-optsdir gesetzt)."""
    try:
        return "dhcp-optsdir=" in CONF.read_text()
    except OSError:
        return False


def wanted_gateway() -> str:
    from . import update_net
    cfg = update_net._raw()
    if update_net.keep_on() and (cfg.get("via") or "lan") == "lan" and cfg.get("gateway"):
        try:
            return str(ipaddress.IPv4Address(str(cfg["gateway"])))
        except ValueError:
            pass
    return VIP


def current_gateway() -> str | None:
    """Gateway, das die Pi per DHCP vergeben – fest aus dnsmasq.conf oder (automatisch) aus der Datei der App."""
    try:
        m = re.search(r"^dhcp-option=option:router,([0-9.]+)\s*$", CONF.read_text(), re.M)
    except OSError:
        return None
    if m:
        return m.group(1)
    try:
        m = re.search(r"option:router,([0-9.]+)", (OPTS_DIR / OPTS_FILE).read_text())
    except OSError:
        return None
    return m.group(1) if m else None


_gw_written: str | None = None


def gateway_sync() -> None:
    """Monitor-Takt: Gateway-Datei nachführen, wenn sich der Weg ins Internet geändert hat. festpos-dhcp-reload
    (root, über festpos-dhcp-reload.path) lädt dnsmasq dann neu; die Handhelds übernehmen es beim nächsten
    Erneuern ihrer Adresse (Mietdauer 1 Stunde: nach höchstens 30 Minuten)."""
    global _gw_written
    if not OPTS_DIR.is_dir() or not auto_gateway():
        return
    want = f"option:router,{wanted_gateway()}\n"
    if want == _gw_written:
        return
    path = OPTS_DIR / OPTS_FILE
    try:
        if path.read_text() == want:
            _gw_written = want
            return
    except OSError:
        pass
    tmp = OPTS_DIR / f".{OPTS_FILE}.neu"
    try:
        tmp.write_text(want)
        os.replace(tmp, path)
    except OSError:
        log.exception("Gateway der Handhelds nicht geschrieben")
        return
    _gw_written = want
    log.info("Gateway der Handhelds: %s", want.strip().split(",")[1])


def handheld_ips() -> list[str]:
    """Feste IPs der eingetragenen Handhelds im Einsatz (nur sie bekommen über die Pi Internet)."""
    try:
        items = json.loads(rt.store.setting("monitor_devices") or "[]")
    except ValueError:
        return []
    out = []
    for d in items if isinstance(items, list) else []:
        if isinstance(d, dict) and d.get("kind") == "phone" and d.get("active", True):
            try:
                out.append(str(ipaddress.IPv4Address(str(d.get("ip") or ""))))
            except ValueError:
                continue
    return sorted(set(out))


def mac_for_ip(ip: str, arp: Path = ARP) -> str | None:
    """MAC zu einer IP aus der ARP-Tabelle dieses Pi (gleiches Netz, gerade eine Verbindung von dort)."""
    try:
        rows = arp.read_text().splitlines()[1:]
    except OSError:
        return None
    for row in rows:
        f = row.split()
        if len(f) >= 4 and f[0] == ip:
            return mac(f[3])
    return None


def arp_table(arp: Path = ARP) -> dict[str, str]:
    """Ganze ARP-Tabelle dieses Pi: IP -> MAC, nur vollständige Einträge (Flag 0x2) – für den MAC-Wechsel (T4)."""
    try:
        rows = arp.read_text().splitlines()[1:]
    except OSError:
        return {}
    out = {}
    for row in rows:
        f = row.split()
        try:
            complete = len(f) >= 4 and int(f[2], 16) & 0x2
        except ValueError:
            continue
        m = mac(f[3]) if complete else None
        if m:
            out[f[0]] = m
    return out


def is_random(m: str | None) -> bool:
    """Zufällige MAC (Android «Zufällige MAC»): Bit «lokal verwaltet» gesetzt, 2. Zeichen 2, 6, A oder E."""
    m = mac(m)
    return bool(m) and int(m[1], 16) & 0x2 == 0x2

