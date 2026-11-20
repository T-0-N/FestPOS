"""Drucker-Netz: die Drucker in einem eigenen VLAN, das nur die Pi erreichen – aus dem WLAN kommt niemand an Port 9100.

Eingestellt wird es in der Verwaltung (Geräte & Netz › Drucker, nur Admin): VLAN-Nummer und Netz der Drucker (z.B. 56 und
192.168.56). Die Einstellung steht in der Datenbank und gilt so für alle Pi. Jeder Pi schreibt sie nach
/var/lib/festpos/druckernetz/druckernetz.json (sync, im Monitor alle 10 s); festpos-druckernetz (root, über
festpos-druckernetz.path und bei jedem Start) legt danach die VLAN-Schnittstelle an (NetworkManager, z.B. eth0.56)
mit der Adresse <Netz>.<letzte Stelle der Pi-IP>, also 192.168.56.11 / .12 / .13. Zwischen den Netzen leitet der Pi
nichts weiter, und die Firewall lässt aus dem Drucker-Netz nur Antworten herein.

Am Switch: Ports der Pi im VLAN mit Tag (zusätzlich zum normalen Netz ohne Tag), Ports der Drucker nur im VLAN ohne Tag
(PVID = VLAN-Nummer). Ob ein Pi die Schnittstelle hat, meldet er mit seinen Infos (system.local_info, «pnet»).
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import ledger
from . import ledger_base as lb
from .core import Config, admin, db, rt
from .i18n import t

log = logging.getLogger("festpos.printer_net")
admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])

DIR = Path(os.environ.get("FESTPOS_PRINTER_NET_DIR", "/var/lib/festpos/druckernetz"))   # deploy/festpos-druckernetz.py
FILE = "druckernetz.json"
NET_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
VLAN_MIN, VLAN_MAX = 2, 4094            # 1 ist an fast jedem Switch das normale Netz ohne Tag


def main_net() -> ipaddress.IPv4Network | None:
    """Das FestPOS-Netz dieses Pi (NODE_IP mit NET_CIDR aus festpos.conf)."""
    try:
        return ipaddress.IPv4Interface(f"{Config.node_ip}/{os.environ.get('FESTPOS_NET_CIDR', '24')}").network
    except ValueError:
        return None


def check_net(net: str) -> str | None:
    """Fehlertext-Schlüssel, wenn das Netz der Drucker nicht taugt (sonst None). Drei Stellen, privat, nicht das
    FestPOS-Netz selbst."""
    m = NET_RE.match(net)
    if not m or any(int(x) > 255 for x in m.groups()):
        return "err.printer_net.net"
    n = ipaddress.IPv4Network(f"{net}.0/24")
    if not n.is_private or n.is_loopback or n.is_link_local:
        return "err.printer_net.net"
    main = main_net()
    if main and n.overlaps(main):
        return "err.printer_net.same_net"
    return None


def config(store=None) -> dict:
    """{"vlan": 0 (aus) oder 2..4094, "net": "192.168.56"} aus der Datenbank. Das Netz bleibt auch ausgeschaltet
    stehen (zum Wiedereinschalten)."""
    store = store or rt.store
    try:
        vlan = int(store.setting("printer_vlan", "0") or 0)
    except ValueError:
        vlan = 0
    net = store.setting("printer_vlan_net", "") or ""
    if check_net(net):
        net = ""
    if not VLAN_MIN <= vlan <= VLAN_MAX or not net:
        vlan = 0
    return {"vlan": vlan, "net": net}


def pi_address(net: str, node_ip: str) -> str | None:
    """Adresse eines Pi im Drucker-Netz: gleiche letzte Stelle wie im FestPOS-Netz (.11, .12, .13)."""
    try:
        return f"{net}.{ipaddress.IPv4Address(node_ip).packed[3]}"
    except ValueError:
        return None


def pi_addresses(net: str) -> list[str]:
    hosts = {urlsplit(p).hostname for p in Config.peers} | {Config.node_ip}
    return sorted(a for a in (pi_address(net, h or "") for h in hosts) if a)


# -- Datei für festpos-druckernetz (root) -----------------------------------------------------------------------
_written: str | None = None


def wanted(store=None) -> str:
    c = config(store)
    return json.dumps(c if c["vlan"] else {"vlan": 0, "net": ""}, sort_keys=True) + "\n"


def sync() -> None:
    """Einstellung in die Datei für festpos-druckernetz schreiben, wenn sie sich geändert hat (Monitor, alle 10 s)."""
    global _written
    if not DIR.is_dir():
        return                                                 # Rechner ohne FestPOS-Einrichtung (oder vor dem Update)
    content = wanted()
    if content == _written:
        return
    target = DIR / FILE
    try:
        if not (target.is_file() and target.read_text() == content):    # nach einem Neustart: nicht unnötig auslösen
            tmp = DIR / f".{FILE}.neu"
            tmp.write_text(content)
            os.replace(tmp, target)
            log.info("Drucker-Netz geschrieben: %s", content.strip())
    except OSError:
        log.exception("Drucker-Netz nicht geschrieben")
        return
    _written = content


# -- Zustand dieses Pi (für seine Infos) -------------------------------------------------------------------------
def _ip_addrs() -> str:
    try:
        return subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def local_state(store=None, addrs: str | None = None) -> dict | None:
    """{"vlan": 0} wenn aus; sonst {"vlan", "ip", "dev", "ok"}: hat dieser Pi seine Adresse im Drucker-Netz?
    None: Datenbank (noch) nicht lesbar."""
    try:
        c = config(store)
    except Exception:  # noqa: BLE001 – nur Anzeige; beim Start ist die Datenbank evtl. noch nicht bereit
        return None
    if not c["vlan"]:
        return {"vlan": 0}
    want = pi_address(c["net"], Config.node_ip)
    out = {"vlan": c["vlan"], "ip": want, "dev": None, "ok": False}
    for line in (addrs if addrs is not None else _ip_addrs()).splitlines():
        m = re.match(r"^\d+:\s+(\S+)\s+inet (\d+\.\d+\.\d+\.\d+)/", line)
        if m and m.group(2) == want:
            out.update(dev=m.group(1).split("@")[0], ok=True)
    return out


# -- Verwaltung ---------------------------------------------------------------------------------------------------
class PrinterNetIn(BaseModel):
    vlan: int = Field(ge=0, le=VLAN_MAX)              # 0 = aus
    net: str = Field(default="", max_length=15)


def _warnings(c: dict) -> list[dict]:
    """Drucker, die mit dem Drucker-Netz nicht zusammenpassen: Adresse ausserhalb oder gleich wie ein Pi."""
    if not c["vlan"]:
        return []
    net = ipaddress.IPv4Network(f"{c['net']}.0/24")
    pis = set(pi_addresses(c["net"]))
    out = []
    for p in rt.store.query("SELECT name, host FROM printers WHERE enabled=1 ORDER BY id"):
        try:
            ip = ipaddress.IPv4Address(p["host"])
        except ValueError:
            continue                                       # Name statt IP: nicht prüfbar
        if str(ip) in pis:
            out.append({"printer": p["name"], "host": p["host"], "kind": "pi"})
        elif ip not in net:
            out.append({"printer": p["name"], "host": p["host"], "kind": "outside"})
    return out


def overview() -> dict:
    from . import system                                   # erst hier: system braucht dieses Modul für seine Infos
    c = config()
    # dieser Pi frisch (gleich nach dem Speichern), die anderen aus ihren Infos (höchstens 30 s alt)
    nodes = [{"id": n["id"], "here": n["here"], "reachable": n["reachable"],
              "pnet": local_state() if n["here"] else (n.get("info") or {}).get("pnet")} for n in system.nodes()]
    return {**c, "pis": pi_addresses(c["net"]) if c["vlan"] else [], "nodes": nodes, "warnings": _warnings(c),
            "main_net": str(main_net() or "")}


@admin_router.get("/printer-net")
async def get_printer_net():
    return await db(overview)


@admin_router.put("/printer-net")
async def put_printer_net(p: PrinterNetIn, request: Request):
    net = p.net.strip()
    if p.vlan:
        if p.vlan < VLAN_MIN:
            raise HTTPException(422, t("err.printer_net.vlan"))
        if main_net() is None:                             # ohne NODE_IP liesse sich die Überschneidung nicht prüfen
            raise HTTPException(422, t("err.printer_net.no_node"))
        if err := check_net(net):
            raise HTTPException(422, t(err))
    actor = ledger.actor_of(request)

    def save():
        up = "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        stmts = [(up, ("printer_vlan", str(p.vlan)))]
        if p.vlan:
            stmts.append((up, ("printer_vlan_net", net)))
        stmts.append(lb.audit_stmt(actor, "printer_net", **({"vlan": p.vlan, "net": f"{net}.0/24"} if p.vlan else {"vlan": 0})))
        rt.store.batch(stmts)
        sync()                                             # dieser Pi sofort, die anderen beim nächsten Durchlauf
        return overview()
    return await db(save)
