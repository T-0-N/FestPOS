"""Das FestPOS-Netz: frei wählbar bei der Ersteinrichtung (T-0-N/FestPOS#2).

Netz als Adresse mit Grösse (z.B. 10.20.0.0/22, erlaubt /16 bis /27), darin die Adresse für die Handhelds (VIP),
Pi A/B/C und der DHCP-Bereich. plan_errors() prüft alles, bevor festpos.conf geschrieben wird: Adressen im Netz und
verschieden, DHCP-Bereich im Netz ohne VIP und Pi, kein Überschneiden mit dem Druckernetz und dem Einrichtungsnetz
(192.168.55.10 während der Ersteinrichtung); beim Umzug (umzug.py) auch nicht mit dem bisherigen Netz (avoid).
Entscheid: kein Abgleich mit den Netzen, in denen der Pi gerade hängt – bei der Ersteinrichtung ist das Internet noch
nicht eingerichtet, und ein Pi, der einem Cluster beitritt, steht schon im FestPOS-Netz. Die Zertifizierungsstelle gilt genau
für dieses Netz (zertifikate.make_all). Im Betrieb: current() aus festpos-node (FESTPOS_NET); Beispiele und Vorschläge
in der Verwaltung rechnen damit statt mit festen 192.168.55.x.
"""
from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path

DEFAULT = "192.168.55.0/24"
MIN_CIDR, MAX_CIDR = 16, 27
SETUP_IP = "192.168.55.10"            # Adresse des Pi während der Ersteinrichtung (festpos-node, ohne festpos.conf)
PRINTER_NET = "192.168.56.0/24"       # Vorgabe des Druckernetzes (printer_net.py)
OFFSETS = {"vip": 10, "a": 11, "b": 12, "c": 13}
DHCP_CONF = Path(os.environ.get("FESTPOS_DHCP_CONF", "/etc/festpos/dnsmasq.conf"))   # dieselbe Datei wie dhcp.CONF
RFC1918 = tuple(ipaddress.IPv4Network(x) for x in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def parse(net: str) -> ipaddress.IPv4Network | None:
    """«10.20.0.0/22» -> Netz; None, wenn ungültig oder nicht genau die Netzadresse (10.20.0.5/22)."""
    try:
        n = ipaddress.IPv4Network(str(net).strip(), strict=True)
    except ValueError:
        return None
    return n


def at(net: ipaddress.IPv4Network, offset: int) -> str:
    """Adresse mit diesem Abstand zur Netzadresse (im /24 die letzte Stelle); im kleinen Netz begrenzt."""
    offset = max(1, min(offset, net.num_addresses - 2))
    return str(net.network_address + offset)


def defaults(net: str = DEFAULT) -> dict:
    """Vorschläge für ein Netz: VIP .10, Pi .11/.12/.13, DHCP .150–.189 (kleine Netze: zweite Hälfte)."""
    n = parse(net) or parse(DEFAULT)
    out = {k: at(n, o) for k, o in OFFSETS.items()}
    if n.prefixlen <= 24:
        out["dhcp_from"], out["dhcp_to"] = at(n, 150), at(n, 189)
    else:
        out["dhcp_from"], out["dhcp_to"] = at(n, n.num_addresses // 2), at(n, n.num_addresses - 2)
    return {"net": str(n), **out}


def plan_errors(net: str, vip: str, a: str, b: str, c: str, dhcp_from: str = "", dhcp_to: str = "",
                cluster_size: int = 1, dhcp: bool = True, avoid: list[str] | None = None) -> list[str]:
    """Fehlercodes (leer = in Ordnung): net, size, ip_bad, ip_outside, ip_twice, dhcp_bad, dhcp_outside,
    dhcp_small, dhcp_overlap, overlap_printer, overlap_setup, overlap_other. avoid: weitere Netze (Router …)."""
    n = parse(net)
    if not n:
        return ["net"]
    errs = []
    if not MIN_CIDR <= n.prefixlen <= MAX_CIDR or not any(n.subnet_of(x) for x in RFC1918):   # nicht 127.x, 169.254.x
        errs.append("size")
    try:
        ips = [ipaddress.IPv4Address(x.strip()) for x in (vip, a, b, c)]
    except ValueError:
        return [*errs, "ip_bad"]
    usable = lambda x: x in n and x not in (n.network_address, n.broadcast_address)  # noqa: E731
    if not all(usable(x) for x in ips):
        errs.append("ip_outside")
    if len(set(ips)) != 4:
        errs.append("ip_twice")
    if dhcp:
        try:
            lo, hi = ipaddress.IPv4Address(dhcp_from.strip()), ipaddress.IPv4Address(dhcp_to.strip())
        except ValueError:
            return [*errs, "dhcp_bad"]
        if not (usable(lo) and usable(hi)) or lo > hi:
            errs.append("dhcp_outside")
        elif int(hi) - int(lo) + 1 < (3 if cluster_size == 3 else 1) * 3:
            errs.append("dhcp_small")
        elif any(lo <= x <= hi for x in ips):
            errs.append("dhcp_overlap")
    if n.overlaps(ipaddress.IPv4Network(PRINTER_NET)):
        errs.append("overlap_printer")
    if ipaddress.IPv4Address(SETUP_IP) in n and n != ipaddress.IPv4Network(DEFAULT):
        errs.append("overlap_setup")
    for other in avoid or []:
        o = parse(other) if "/" in str(other) else None
        if o and n.overlaps(o):
            errs.append("overlap_other")
            break
    return errs


def conf_lines(net: str, vip: str, a: str, b: str, c: str, dhcp_from: str, dhcp_to: str) -> dict[str, str]:
    """Einträge für festpos.conf: NET und – für ältere Skripte – NET_PREFIX/NET_CIDR, DHCP_POOL, SSH_FROM."""
    n = parse(net)
    assert n is not None
    first3 = str(n.network_address).rsplit(".", 1)[0]
    ssh = f"{at(n, 200)}-{at(n, 254)}" if n.prefixlen <= 24 else ""
    return {"NET": str(n), "NET_PREFIX": first3, "NET_CIDR": str(n.prefixlen), "VIP": vip, "NODE_A_IP": a,
            "NODE_B_IP": b, "NODE_C_IP": c, "DHCP_POOL": f"{dhcp_from}-{dhcp_to}", "SSH_FROM": ssh}


def from_conf(text: str) -> ipaddress.IPv4Network | None:
    """Netz aus einer festpos.conf: NET, sonst (älter) NET_PREFIX und NET_CIDR."""
    from . import backup
    n = parse(backup.conf_value(text, "NET") or "")
    if n:
        return n
    prefix, cidr = backup.conf_value(text, "NET_PREFIX") or "", backup.conf_value(text, "NET_CIDR") or "24"
    return parse(f"{prefix}.0/{cidr}") if prefix else None


def current() -> ipaddress.IPv4Network | None:
    """Das Netz dieses Pi im Betrieb (festpos-node: FESTPOS_NET; älter: NODE_IP mit FESTPOS_NET_CIDR)."""
    n = parse(os.environ.get("FESTPOS_NET", ""))
    if n:
        return n
    try:
        return ipaddress.IPv4Interface(f"{os.environ.get('NODE_IP', '')}/{os.environ.get('FESTPOS_NET_CIDR', '24')}").network
    except ValueError:
        return None


def pool() -> tuple[str, str] | None:
    """DHCP-Bereich dieses Pi aus der von festpos-node geschriebenen dnsmasq.conf (wie dhcp.CONF), sonst None."""
    try:
        m = re.search(r"^dhcp-range=([0-9.]+),([0-9.]+),", DHCP_CONF.read_text(), re.M)
    except OSError:
        return None
    return (m.group(1), m.group(2)) if m else None


def info() -> dict:
    """Für die Verwaltung: Netz, VIP und Beispieladressen (statt fest eingetragener 192.168.55.x)."""
    n = current() or parse(DEFAULT)
    vip = os.environ.get("FESTPOS_VIP") or at(n, 10)
    if n.prefixlen <= 24:
        ex = {"router": at(n, 1), "printer": at(n, 21), "switch": at(n, 30), "ap": at(n, 40), "phone": at(n, 101),
              "pc": at(n, 200)}
    else:                                   # kleines Netz: verschiedene Adressen vor der VIP, Handy im DHCP-Bereich
        ex = {"router": at(n, 1), "printer": at(n, 2), "switch": at(n, 3), "ap": at(n, 4), "pc": at(n, 5),
              "phone": defaults(str(n))["dhcp_from"]}
    d = defaults(str(n))
    lo, hi = pool() or (d["dhcp_from"], d["dhcp_to"])            # Vorschläge für feste Adressen meiden ihn
    return {"net": str(n), "vip": vip, "examples": ex, "dhcp": {"from": lo, "to": hi}}


def vip() -> str:
    return info()["vip"]


# -- Netz umziehen (T-0-N/FestPOS#2, Teil 2) ------------------------------------------------------------------------
IP_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")


def move_ip(ip: str, old: ipaddress.IPv4Network, new: ipaddress.IPv4Network) -> str | None:
    """Adresse im alten Netz -> gleicher Abstand zur Netzadresse im neuen (192.168.55.21 -> 10.20.0.21). None: nicht im
    alten Netz oder passt nicht ins neue (z.B. .200 in ein /26)."""
    try:
        a = ipaddress.IPv4Address(str(ip).strip())
    except ValueError:
        return None
    if a not in old:
        return None
    off = int(a) - int(old.network_address)
    if off <= 0 or off >= new.num_addresses - 1:
        return None
    return str(new.network_address + off)


def move_text(text: str, old: ipaddress.IPv4Network, new: ipaddress.IPv4Network) -> tuple[str, list[str]]:
    """Alle Adressen des alten Netzes in einem Text umrechnen («Switch=192.168.55.30; …», «192.168.55.200-…») ->
    (neuer Text, Adressen, die nicht ins neue Netz passen)."""
    missing: list[str] = []

    def one(m: re.Match) -> str:
        ip = m.group(1)
        try:
            if ipaddress.IPv4Address(ip) not in old:
                return ip
        except ValueError:
            return ip
        new_ip = move_ip(ip, old, new)
        if new_ip is None:
            missing.append(ip)
            return ip
        return new_ip
    return IP_RE.sub(one, text or ""), missing
