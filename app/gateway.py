"""Gateway, DNS und Zeitserver für die Pi und die Handhelds (Verwaltung › Geräte & Netz › Netz & Internet).

Massgebend ist festpos.conf auf jeder Karte (GATEWAY, DNS, NTP – deploy/festpos-conf.sh, festpos_gateway): So gilt es
schon beim Start (festpos-node.sh), auch wenn die Datenbank nicht läuft, und kommt mit dem Kartenpaket auf neue Pi.
Geändert wird in der Verwaltung (kritische Aktion): der gewünschte Stand mit Nummer (rev) steht in der Datenbank
(«net_gateway»), jeder Pi lässt ihn im Monitor-Takt von festpos-netz --gateway (root) in seine festpos.conf schreiben
und ohne Neustart übernehmen und meldet seinen Stand («net_gateway.node.<x>»). Ein Pi, der aus war, holt das nach.

Arten: «auto» wie bisher (mobiler Router bei «dauerhaft verbunden», sonst gibt der Pi sein Internet weiter), «fest»
(Router oder Firewall im FestPOS-Netz für Pi und Handhelds, z.B. Firmennetz), «ohne» (geschlossenes Netz). Den
Zeitserver gibt es in jeder Art; die Handhelds holen die Zeit immer von den Pi.
"""
from __future__ import annotations

import ipaddress
import json
import os
import secrets
import socket
import struct
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import ledger_base as lb
from . import netz
from .core import Config, admin, current_user, db, log, rt
from .i18n import t

admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])

KEY = "net_gateway"
NODE_KEY = "net_gateway.node."
MODES = ("auto", "fest", "ohne")
UPSERT = "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
RETRY_S = 60                       # gescheiterter Versuch: nach so vielen Sekunden nochmals
HELPER_WAIT = 200


def _get(key: str) -> dict:
    try:
        d = json.loads(rt.store.setting(key) or "{}")
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def from_env() -> dict:
    """Was beim Start dieses Pi galt (festpos-node.sh schreibt es in /etc/festpos/env)."""
    mode = os.environ.get("FESTPOS_GW_MODE") or "auto"
    return {"mode": mode if mode in MODES else "auto", "gateway": os.environ.get("FESTPOS_GW", ""),
            "dns": [x for x in os.environ.get("FESTPOS_GW_DNS", "").split(",") if x],
            "ntp": os.environ.get("FESTPOS_NTP", "")}


def wanted() -> dict:
    """Gewünschter Stand für alle Pi ({} = nie in der Verwaltung geändert)."""
    return _get(KEY)


def node_state(node: str | None = None) -> dict:
    return _get(NODE_KEY + (node or Config.node or "a"))


def mine() -> dict:
    """Was auf diesem Pi gilt: zuletzt übernommen, sonst wie beim Start."""
    return node_state().get("applied") or from_env()


def current() -> dict:
    """Gilt für alle Pi: der gewünschte Stand, sonst der dieses Pi."""
    w = wanted()
    return {k: w[k] for k in ("mode", "gateway", "dns", "ntp")} if w.get("mode") in MODES else mine()


def mode() -> str:
    """«auto», «fest» oder «ohne» – für Internet (update_net), DHCP und die Verwaltung."""
    m = current().get("mode")
    return m if m in MODES else "auto"


def fixed() -> dict | None:
    """Fester Router: {gateway, dns} – sonst None."""
    c = current()
    if c.get("mode") != "fest" or not c.get("gateway"):
        return None
    return {"gateway": c["gateway"], "dns": (c.get("dns") or [c["gateway"]])[0]}


# -- Prüfen -------------------------------------------------------------------------------------------------------
def errors(mode_: str, gateway: str, dns: list[str], ntp: str, net: ipaddress.IPv4Network | None = None,
           avoid: list[str] | None = None, pool: tuple[str, str] | None = None) -> list[str]:
    return netz.gw_errors(mode_, gateway, dns, ntp, net or netz.current() or netz.parse(netz.DEFAULT), avoid, pool)


def _taken() -> list[str]:
    """Adressen, die nie Gateway sein dürfen: VIP und Pi."""
    from . import umzug
    pis = umzug.pis_now()
    return [x for x in (os.environ.get("FESTPOS_VIP") or netz.vip(), *pis.values()) if x]


def _host_checks() -> bool:
    return os.environ.get("FESTPOS_HOST_CHECKS", "1") != "0"


def ping(ip: str) -> bool | None:
    import shutil
    import subprocess
    if not _host_checks() or not shutil.which("ping"):
        return None
    try:
        return subprocess.run(["ping", "-c", "1", "-W", "1", "-n", "--", ip], capture_output=True, timeout=4).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return None


def dns_ok(server: str, name: str = "api.github.com") -> bool | None:
    """Eine Anfrage (Typ A) direkt an diesen DNS-Server – Antwort ohne Fehler und mit mindestens einer Adresse?"""
    if not _host_checks():
        return None
    qid = secrets.randbits(16)
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    q += b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0" + struct.pack(">HH", 1, 1)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(2)
            s.sendto(q, (server, 53))
            data, _ = s.recvfrom(1500)
    except OSError:
        return False
    if len(data) < 12:
        return False
    rid, flags, _, an = struct.unpack(">HHHH", data[:8])
    return rid == qid and flags & 0x000F == 0 and an > 0


def ntp_offset(server: str) -> float | None:
    """Abweichung der Uhr dieses Pi zum Zeitserver in Sekunden (SNTP) – None, wenn er nicht antwortet."""
    if not _host_checks():
        return None
    try:
        addr = socket.getaddrinfo(server, 123, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(2)
            sent = time.time()
            s.sendto(b"\x1b" + 47 * b"\0", addr)
            data, _ = s.recvfrom(512)
        got = time.time()
    except OSError:
        return None
    if len(data) < 48:
        return None
    secs, frac = struct.unpack(">II", data[40:48])
    server_time = secs - 2208988800 + frac / 2 ** 32
    return server_time - (sent + got) / 2


def probe(c: dict) -> dict:
    """Antworten Gateway, DNS und Zeitserver? -> {gateway, dns: {ip: ok}, ntp: {ok, offset}} (None = nicht geprüft)."""
    out: dict = {"gateway": None, "dns": {}, "ntp": None}
    if c.get("mode") == "fest" and c.get("gateway"):
        out["gateway"] = ping(c["gateway"])
        out["dns"] = {d: dns_ok(d) for d in (c.get("dns") or [c["gateway"]])}
    if c.get("ntp") and _host_checks():
        off = ntp_offset(c["ntp"])
        out["ntp"] = {"ok": off is not None and abs(off) < 5, "offset": round(off, 2) if off is not None else None}
    return out


# -- Verteilen an alle Pi -----------------------------------------------------------------------------------------
_last = [0.0]


def tick() -> None:
    """Monitor-Takt (jeder Pi): gewünschten Stand übernehmen lassen, wenn er neuer ist als der übernommene."""
    from . import cards, umzug
    w = wanted()
    if w.get("mode") not in MODES:
        return
    st = node_state()
    if st.get("rev") == w.get("rev") and st.get("ok"):
        return
    now = time.time()
    if now - _last[0] < RETRY_S:
        return
    _last[0] = now
    if umzug._running(umzug.load()):
        return                                             # während eines Netz-Umzugs nicht (festpos-netz auch nicht)
    if not cards.available():
        _report(w, False, "helper")
        return
    job = cards.submit("netz_gateway", {k: w.get(k) for k in ("mode", "gateway", "dns", "ntp", "rev")})
    res = cards.wait(job, HELPER_WAIT)
    if not res or not res.get("ok"):
        code = str((res or {}).get("code") or "no_answer")
        log.warning("Gateway, DNS, Zeitserver nicht übernommen (%s)", code)
        _report(w, False, code)
        return
    _report(w, True, None, res.get("applied"))
    log.info("Gateway, DNS, Zeitserver übernommen (%s)", w.get("mode"))


def _report(w: dict, ok: bool, code: str | None, applied: dict | None = None) -> None:
    prev = node_state()
    d = {"rev": w.get("rev"), "ok": ok, "code": code, "at": int(time.time()),
         "applied": applied if ok and isinstance(applied, dict) else prev.get("applied")}
    rt.store.execute(UPSERT, (NODE_KEY + (Config.node or "a"), json.dumps(d)))


# -- Verwaltung ---------------------------------------------------------------------------------------------------
def view() -> dict:
    from . import system
    w = wanted()
    nodes = {}
    for n in system.nodes():
        st = node_state(n["id"])
        nodes[n["id"]] = {"rev": st.get("rev"), "ok": st.get("ok"), "code": st.get("code"), "at": st.get("at")}
    from . import dhcp
    return {"current": current(), "wanted_rev": w.get("rev"), "nodes": nodes, "net": str(netz.current() or netz.DEFAULT),
            "phones_gw": dhcp.current_gateway() or "", "vip": netz.vip(),
            "pool": list(_pool()), "taken": _taken(), "keep": _keep_on()}


def _keep_on() -> bool:
    return rt.store.setting("net_keep", "0") == "1"


class GatewayIn(BaseModel):
    mode: str = Field(pattern=r"^(auto|fest|ohne)$")
    gateway: str = Field(default="", max_length=15)
    dns: list[str] = Field(default_factory=list, max_length=4)
    ntp: str = Field(default="", max_length=253)


def _clean(g: GatewayIn) -> dict:
    dns = [d.strip() for d in g.dns if d.strip()]
    gw = g.gateway.strip() if g.mode == "fest" else ""
    return {"mode": g.mode, "gateway": gw, "dns": (dns or [gw]) if g.mode == "fest" else [], "ntp": g.ntp.strip()}


def _pool() -> tuple[str, str]:
    d = netz.info()["dhcp"]                                 # aus der dnsmasq.conf dieses Pi, sonst die Vorgabe
    return d["from"], d["to"]


def _check(c: dict) -> list[str]:
    return errors(c["mode"], c["gateway"], c["dns"], c["ntp"], avoid=_taken(), pool=_pool())


ERR = {"mode": "err.gw.mode", "ntp": "err.gw.ntp", "gw_bad": "err.gw.bad", "gw_outside": "err.gw.outside",
       "gw_taken": "err.gw.taken", "gw_pool": "err.gw.pool", "dns_bad": "err.gw.dns", "dns_many": "err.gw.dns"}


@admin_router.get("/gateway")
async def get_view():
    return await db(view)


@admin_router.post("/gateway/pruefen")
async def check(g: GatewayIn):
    """Prüfen ohne zu speichern: Fehler im Formular und ob Gateway, DNS und Zeitserver antworten."""
    c = _clean(g)
    errs = await db(_check, c)
    return {"errors": errs, "probe": await db(probe, c) if not errs else None}


@admin_router.put("/gateway")
async def save(g: GatewayIn, request: Request):
    """Speichern (kritisch): neuer Stand mit nächster Nummer, die Pi übernehmen ihn im nächsten Takt."""
    c = _clean(g)
    who = (current_user(request) or {}).get("name") or "?"

    def run():
        errs = _check(c)
        if errs:
            raise HTTPException(422, t(ERR.get(errs[0], "err.gw.bad"), net=str(netz.current() or netz.DEFAULT)))
        rev = int(wanted().get("rev") or 0) + 1
        rt.store.batch([(UPSERT, (KEY, json.dumps({**c, "rev": rev, "by": who, "at": int(time.time())}))),
                        lb.audit_stmt(who, "net_gateway", mode=c["mode"], gateway=c["gateway"] or None,
                                      dns=",".join(c["dns"]) or None, ntp=c["ntp"] or None)])
        _last[0] = 0.0                                     # dieser Pi gleich im nächsten Takt
        return view()
    return await db(run)
