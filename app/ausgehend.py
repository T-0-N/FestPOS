"""Firewall für Verbindungen der Pi ins Internet (T-0-N/FestPOS#10) – Liste und Regeln an einer Stelle.

Drei Modi (Einstellung in der Verwaltung, für alle Pi gleich, app/firewall.py):
- «strict» Nur FestPOS-Dienste (Standard): ausgehend nur zu HOSTS (Payrexx, Cloudflare, GitHub, Paketquellen), dazu
  Zeitserver (NTP) und DNS über die eingestellten Server. Alles andere wird verworfen und gezählt.
- «watch» Beobachten: alles erlaubt, Verbindungen zu anderen Zielen werden gezählt – zum Herausfinden, was fehlt.
- «open» Offen: wie vor 1.2.0, nichts gezählt.
«watch» und «open» gelten höchstens MAX_HOURS, danach wieder «strict» (der Pi stellt auch selbst zurück).

Das lokale Netz (Handhelds, Drucker, Router, die anderen Pi) ist nie betroffen, ebenso wenig die Weitergabe des
Internets an die Handhelds (eigene Kette «forward» in festpos-update) – Fully, Payrexx-App und Android brauchen mehr.
Namen werden regelmässig neu aufgelöst (deploy/festpos-firewall, root, Timer); die Adressen der letzten Auflösung
bleiben eine Weile gültig, damit ein Wechsel des DNS-Eintrags keine laufende Verbindung kappt.
"""
from __future__ import annotations

import ipaddress

MODES = ("strict", "watch", "open")
DEFAULT = "strict"
MAX_HOURS = 24
TABLE = "festpos_out"

# Wohin die Pi selbst dürfen (Name: Sprachschlüssel «wofür»). Nur hier ändern.
HOSTS = {
    "api.payrexx.com": "admin.fw.h_payrexx",
    "api.github.com": "admin.fw.h_github",
    "github.com": "admin.fw.h_github",
    "objects.githubusercontent.com": "admin.fw.h_github_files",
    "release-assets.githubusercontent.com": "admin.fw.h_github_files",
    "codeload.github.com": "admin.fw.h_github_files",
    "pkg.cloudflare.com": "admin.fw.h_cloudflared",
    "region1.v2.argotunnel.com": "admin.fw.h_tunnel",
    "region2.v2.argotunnel.com": "admin.fw.h_tunnel",
    "api.cloudflare.com": "admin.fw.h_tunnel",
    "update.argotunnel.com": "admin.fw.h_tunnel",
    "deb.debian.org": "admin.fw.h_debian",
    "security.debian.org": "admin.fw.h_debian",
    "archive.raspberrypi.com": "admin.fw.h_raspberrypi",
}
# Cloudflare Tunnel spricht seine Server auch direkt an (Adressen aus dem Tunnel, nicht aus DNS): veröffentlichte
# Bereiche von Cloudflare (cloudflare.com/ips) – ändern sich selten.
NETS = ("173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18", "108.162.192.0/18",
        "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15",
        "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
        "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32", "2a06:98c0::/29",
        "2c0f:f248::/32")
LOCAL4 = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10", "127.0.0.0/8",
          "224.0.0.0/4", "255.255.255.255/32")
LOCAL6 = ("::1/128", "fc00::/7", "fe80::/10", "ff00::/8")


def split(addrs) -> tuple[list[str], list[str]]:
    """Adressen und Netze -> (IPv4, IPv6), ungültige weg, sortiert und ohne Doppelte."""
    v4, v6 = set(), set()
    for a in addrs:
        try:
            n = ipaddress.ip_network(str(a).strip(), strict=False)
        except ValueError:
            continue
        (v4 if n.version == 4 else v6).add(str(n))
    return ([str(n) for n in ipaddress.collapse_addresses(map(ipaddress.ip_network, v4))],
            [str(n) for n in ipaddress.collapse_addresses(map(ipaddress.ip_network, v6))])


def _elements(items: list[str]) -> str:
    return f"elements = {{ {', '.join(items)} }}" if items else ""


def rules(mode: str, addrs, dns: list[str]) -> str:
    """nft-Skript für den Modus. addrs: erlaubte Adressen (aufgelöst) und Netze; dns: eingestellte DNS-Server.
    «open»: nur die Tabelle entfernen."""
    head = f"table inet {TABLE}\ndelete table inet {TABLE}\n"          # alte Regeln weg (auch wenn es keine gab)
    if mode not in MODES or mode == "open":
        return head
    v4, v6 = split([*addrs, *NETS])
    d4, d6 = split(dns)
    verdict = "drop" if mode == "strict" else "accept"
    return head + f"""table inet {TABLE} {{
    counter unknown {{ }}
    set allow4 {{ type ipv4_addr; flags interval; {_elements(v4)} }}
    set allow6 {{ type ipv6_addr; flags interval; {_elements(v6)} }}
    set dns4 {{ type ipv4_addr; flags interval; {_elements(d4)} }}
    set dns6 {{ type ipv6_addr; flags interval; {_elements(d6)} }}
    set seen4 {{ type ipv4_addr . inet_proto . inet_service; flags dynamic, timeout; timeout 1d; size 512; }}
    set seen6 {{ type ipv6_addr . inet_proto . inet_service; flags dynamic, timeout; timeout 1d; size 512; }}
    chain output {{
        type filter hook output priority 10; policy accept;
        oifname "lo" accept
        ct state established,related accept
        ip daddr {{ {', '.join(LOCAL4)} }} accept
        ip6 daddr {{ {', '.join(LOCAL6)} }} accept
        meta l4proto {{ tcp, udp }} th dport 53 ip daddr @dns4 accept
        meta l4proto {{ tcp, udp }} th dport 53 ip6 daddr @dns6 accept
        udp dport 123 accept
        ip daddr @allow4 accept
        ip6 daddr @allow6 accept
        meta l4proto {{ tcp, udp }} update @seen4 {{ ip daddr . meta l4proto . th dport }}
        meta l4proto {{ tcp, udp }} update @seen6 {{ ip6 daddr . meta l4proto . th dport }}
        counter name unknown
        {verdict}
    }}
}}
"""
