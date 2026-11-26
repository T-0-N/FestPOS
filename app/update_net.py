"""Update aus dem Internet: festpos.zip + festpos.zip.sig aus dem neusten GitHub-Release (öffentliches oder privates
Repo) holen – Verwaltung › Wartung › Update › «Aus dem Internet».

Ablauf (immer auf dem Pi, der diese Verwaltung zeigt)
  1. Internet ein: Auftrag net_up an den root-Helfer festpos-update. Der setzt je nach Einstellung eine Route über
     einen Router im FestPOS-Netz (mit DNS), verbindet das WLAN des Pi oder einen USB-Netzwerkadapter (DHCP).
  2. «Nach neuer Version suchen»: GET /repos/<repo>/releases/latest; «Herunterladen und prüfen»: dazu die beiden
     Dateien des Release (über die API, damit es auch für private Repos geht – Token nur an api.github.com).
  3. Internet aus (net_down), immer – auch bei Fehlern. Bleibt das aus, schaltet ein Zeitgeber im Helfer ab.
  4. Das Paket geht durch dieselbe Prüfung wie beim Hochladen (update.accept_bundle: Signatur mit den Schlüsseln der
     Boot-Partition, Aufbau, Version). Verteilt wird wie gewohnt mit «Auf alle Pi verteilen».
Das Repo selbst ist nicht vertrauenswürdig: Was nicht mit einem Update-Schlüssel signiert ist, wird abgelehnt.

Gespeichert (Datenbank, für alle Pi gleich; update_* steht nie in /api/admin/settings):
  update_net        – JSON {repo, via (lan|wlan|usb), gateway, dns, ssid}
  update_net_token  – Zugriffs-Token für ein privates Repo; nur setzen, gekürzt zeigen
  update_net_psk    – WLAN-Passwort; nur setzen, nie zurückgeben
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import secrets
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import i18n, ledger_base as lb, release, secretbox, update
from .core import APP_VERSION, Config, admin, current_user, db, rt

log = logging.getLogger("festpos.update_net")
router = APIRouter(prefix="/api/admin/update/net", dependencies=[Depends(admin)])

API = os.environ.get("FESTPOS_GITHUB_API", "https://api.github.com").rstrip("/")
SYS_NET = Path(os.environ.get("FESTPOS_SYS_NET", "/sys/class/net"))
NET_WAIT = float(os.environ.get("FESTPOS_NET_WAIT", "150"))       # auf festpos-update warten (WLAN braucht etwas)
TIMEOUT = 30
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
# Ohne eigenes Repo: die Releases des Projekts (signiert mit dem Projekt-Schlüssel, der im Image liegt)
DEFAULT_REPO = os.environ.get("FESTPOS_UPDATE_REPO", "T-0-N/FestPOS")


def repo_of(cfg: dict) -> str:
    return str(cfg.get("repo") or "") or DEFAULT_REPO
TOKEN_RE = re.compile(r"^[A-Za-z0-9_]{20,255}$")
SSID_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,32}$")
PSK_RE = re.compile(r"^(?:[\x20-\x7e]{8,63}|[0-9a-fA-F]{64})$")
VIA = ("lan", "wlan", "usb")
ZIP_NAME, SIG_NAME = "festpos.zip", "festpos.zip.sig"

_job: dict = {}                     # laufender oder letzter Vorgang auf diesem Pi
_task: asyncio.Task | None = None


class GhError(Exception):
    def __init__(self, code: str, **kw):
        super().__init__(code)
        self.code, self.kw = code, kw


# -- Einstellungen ----------------------------------------------------------------------------------------------
def _raw() -> dict:
    try:
        c = json.loads(rt.store.setting("update_net") or "{}")
    except ValueError:
        c = {}
    return c if isinstance(c, dict) else {}


def _cfg() -> dict:
    """Verbindung, wie sie gilt: mit festem Router (Gateway, DNS & Zeitserver, app/gateway.py) immer über ihn."""
    from . import gateway
    c, f = _raw(), gateway.fixed()
    return {**c, "via": "lan", "gateway": f["gateway"], "dns": f["dns"]} if f else c


def _want(cfg: dict | None = None) -> list:
    """Weg der Verbindung, wie er gilt (Weg, Router, WLAN) – ändert er sich, wird neu verbunden."""
    c = cfg or _cfg()
    via = c.get("via") if c.get("via") in VIA else "lan"
    return [via, (c.get("gateway") or "") if via == "lan" else "", (c.get("ssid") or "") if via == "wlan" else ""]


def _closed() -> bool:
    """Geschlossenes Netz (Gateway «ohne»): kein Internet, auch nicht kurz."""
    from . import gateway
    return gateway.mode() == "ohne"


def short_token(tok: str) -> str:
    """Nie ganz zeigen: Anfang und die letzten drei Zeichen."""
    prefix = next((p for p in ("github_pat_", "ghp_") if tok.startswith(p)), "")
    head = tok[:len(prefix) + 4]
    return f"{head}…{tok[-3:]}" if len(tok) > 12 else "…"


def _public() -> dict:
    c = _raw()
    tok = rt.store.setting("update_net_token") or ""
    return {"repo": repo_of(c), "default_repo": DEFAULT_REPO, "via": c.get("via") if c.get("via") in VIA else "lan",
            "gateway": c.get("gateway") or "", "dns": c.get("dns") or "", "ssid": c.get("ssid") or "",
            "token": short_token(tok) if tok else "", "psk_set": bool(rt.store.setting("update_net_psk"))}


TETHER_DRIVERS = {"rndis_host", "ipheth"}                  # wie festpos-update: Handys per USB-Tethering nie
PHONE_VENDORS = {"04e8", "18d1", "05ac", "2717", "2a70", "22b8", "0fce", "1004"}


def _is_tether(d: Path) -> bool:
    try:
        if os.path.basename(os.path.realpath(d / "device" / "driver")) in TETHER_DRIVERS:
            return True
        return (Path(os.path.realpath(d / "device")).parent / "idVendor").read_text().strip().lower() in PHONE_VENDORS
    except OSError:
        return False


def devices() -> dict:
    """WLAN und USB-Netzwerkadapter dieses Pi (wie festpos-update sie erkennt; Handy-Tethering zählt nicht)."""
    out: dict = {"wlan": [], "usb": []}
    try:
        names = sorted(e.name for e in os.scandir(SYS_NET))
    except OSError:
        return out
    for n in names:
        d = SYS_NET / n
        if (d / "wireless").exists() or (d / "phy80211").exists():
            out["wlan"].append(n)
        elif (d / "device").exists() and "/usb" in os.path.realpath(d / "device") and not _is_tether(d):
            try:
                link = (d / "carrier").read_text().strip() == "1"
            except OSError:
                link = False
            out["usb"].append({"name": n, "link": link})
    return out


def _job_view() -> dict | None:
    if not _job:
        return None
    j = {k: v for k, v in _job.items() if k not in ("kw",)}
    if _job.get("code"):
        j["text"] = update.err_text(_job["code"], _job.get("kw"))
    return j


def allowed() -> bool:
    """Seite «Internet», Schalter (a): Internet für Updates erlaubt (Standard ja – wie vor T5)."""
    return not _closed() and rt.store.setting("net_updates", "1") != "0"


def view() -> dict:
    from . import gateway
    return {"config": _public(), "gw_mode": gateway.mode(), "node": Config.node, "devices": devices(), "job": _job_view(),
            "running": APP_VERSION, "busy": bool(_task and not _task.done()), "allowed": allowed(),
            "keep": keep_view()}


# -- Dauerhaft verbunden (Seite «Internet», Schalter c): Kartenzahlung über Payrexx und QR-Bestellung ------------
# Jeder Pi prüft jede Minute (Monitor-Takt): Ist (c) an, hält er die Verbindung offen – ohne Zeitgeber, nach einem
# Neustart wieder. Router im Netz: jeder Pi selbst, die Handhelds direkt (DHCP-Gateway = Router, app/dhcp.py).
# WLAN/USB-Adapter: der Pi mit dem Gerät verbindet sich und gibt das Internet weiter; die anderen Pi gehen über ihn
# (Route). Die Handhelds schicken alles an die VIP: deren Pi gibt weiter (selbst oder über den Pi mit dem Gerät).
# Weitergegeben wird nur an die eingetragenen Handhelds und die Pi – beim Ausrollen neuer Handhelds
# (Schalter b) kurz ans ganze Netz. Handys per USB-Tethering nie.
#
# Stabil halten (T-0-N/FestPOS#1):
#   - Nach einem Neustart der App (auch beim Update) übernimmt sie eine bestehende Verbindung, statt sie abzubauen:
#     der Helfer meldet ihren Stand in update-status/internet.json.
#   - «Online» heisst: Name auflösen (DNS) UND eine HTTPS-Verbindung mit gültigem Zertifikat. Scheitert das, steht der
#     Grund dabei (kein DNS, Uhrzeit falsch, keine Antwort). Neu verbunden wird erst nach KEEP_FAILS Fehlversuchen
#     hintereinander, danach mit wachsender Pause (BACKOFF) – ein schwaches Netz flattert so nicht.
#   - WLAN mit 3 Pi: nur der Pi mit der VIP verbindet sich (ein Handy-Hotspot nimmt oft nur wenige Geräte); die
#     anderen gehen über ihn.
KEEP_FAILS = 3                                              # so viele Prüfungen (je 1 Minute) offline: neu verbinden
BACKOFF = (60, 120, 240, 480, 900)                          # Pause nach dem 1., 2., … Verbinden ohne Erfolg (Sekunden)
PROBE_HOSTS = ("api.payrexx.com", "api.github.com")         # Prüfung «online»: DNS und HTTPS (Zertifikat geprüft)
# Grund, wenn das Verbinden scheitert (Fehlercode des Helfers -> Grund für Verwaltung und Statusanzeige)
REASONS = {"net_wlan_not_found": "wlan_not_found", "net_wlan": "wlan", "net_no_wlan": "no_wlan", "net_dhcp": "dhcp",
           "net_no_link": "no_link", "net_no_usb": "no_usb", "net_gateway": "gateway", "no_answer": "helper",
           "no_helper": "helper", "net_same_net": "same_net"}
_keep: dict = {}


def keep_on() -> bool:
    return not _closed() and rt.store.setting("net_keep", "0") == "1"


def keep_report() -> dict:
    """Für die anderen Pi (/api/internal/node): bin ich verbunden und gebe ich das Internet weiter?"""
    return {"up": _keep.get("state") == "up", "share": bool(_keep.get("share")), "ip": Config.node_ip or "",
            "via": _keep.get("via"), "device": bool(_keep.get("device"))}


def _probe(timeout: float = 5.0) -> str | None:
    """Ist das Internet da? None = ja; sonst der Grund: dns (Namen lassen sich nicht auflösen), time (Zertifikat
    «noch nicht gültig» bzw. «abgelaufen»: die Uhr des Pi stimmt nicht) oder https (keine Antwort)."""
    if os.environ.get("FESTPOS_HOST_CHECKS", "1") == "0":
        return None if _keep.get("state") == "up" else "https"
    import socket
    dns_ok, time_bad = False, False
    ctx = ssl.create_default_context()
    for host in PROBE_HOSTS:
        try:
            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            continue
        dns_ok = True
        try:
            with socket.create_connection((host, 443), timeout) as raw, ctx.wrap_socket(raw, server_hostname=host):
                return None
        except ssl.SSLCertVerificationError as e:
            msg = str(e).lower()
            time_bad = time_bad or "not yet valid" in msg or "expired" in msg
        except (OSError, ssl.SSLError):
            continue
    return "time" if time_bad else "https" if dns_ok else "dns"


def _online(timeout: float = 5.0) -> bool:
    return _probe(timeout) is None


TUNNEL_ENV = Path(os.environ.get("FESTPOS_TUNNEL_ENV", "/etc/festpos/tunnel.env"))   # nur mit TUNNEL_TOKEN (festpos-node)


def _tunnel() -> bool | None:
    """Läuft der Tunnel zur QR-Bestellung? None: auf diesem Pi nicht eingerichtet oder nicht prüfbar."""
    if os.environ.get("FESTPOS_HOST_CHECKS", "1") == "0" or not TUNNEL_ENV.exists():
        return None
    import subprocess
    try:
        r = subprocess.run(["systemctl", "is-active", "festpos-tunnel.service"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() == "active"


def _peer_sharing() -> dict | None:
    """Ein anderer Pi, der selbst verbunden ist (eigenes WLAN/USB-Gerät) und das Internet weitergibt (Stand der letzten
    Abfrage, höchstens 60 s alt). Nie einer, der es nur von einem dritten Pi weiterreicht – der könnte über diesen Pi
    gehen (Kreis, beide ohne Internet). Nur wer «device» ausdrücklich meldet (ältere Versionen nicht: dort hilft
    während eines Updates das eigene Gerät oder der nächste Versuch)."""
    peers = getattr(getattr(rt, "monitor", None), "peers", None) or {}
    for node, p in sorted(peers.items()):
        net = (p.get("data") or {}).get("internet") or {}
        if net.get("up") and net.get("share") and net.get("device") is True and net.get("ip") \
                and time.time() - (p.get("at") or 0) < 60:
            return {"node": node, "ip": net["ip"]}
    return None


def keep_view() -> dict:
    v = {k: _keep.get(k) for k in ("state", "via", "iface", "since", "share", "peer", "checked", "reason", "fails",
                                   "tries", "last", "next")}
    v["fails_max"] = KEEP_FAILS
    v["ssid"] = (_raw().get("ssid") or "") if _keep.get("via") == "wlan" else ""
    if _keep.get("code"):
        v["text"] = update.err_text(_keep["code"], _keep.get("kw"))
    return {"on": keep_on(), **v, "online": _keep.get("online"), "tunnel": _tunnel() if _keep.get("state") == "up" else None}


def share_allow() -> list[str]:
    """Wer über die Pi ins Internet darf: die eingetragenen Handhelds, alle Pi und die VIP."""
    from . import dhcp
    pis = {urlsplit(p).hostname or "" for p in Config.peers} | {Config.node_ip, dhcp.VIP}
    out = set(dhcp.handheld_ips())
    for ip in pis:
        try:
            out.add(str(ipaddress.IPv4Address(ip)))
        except ValueError:
            continue
    return sorted(out, key=ipaddress.IPv4Address)


def _holds_vip() -> bool:
    from . import dhcp, fully
    return fully.is_local(dhcp.VIP)


def _rollout() -> bool:
    from . import fully
    return bool(fully._share.get("on"))


def _share_set(want: bool, allow: list[str] | None) -> None:
    """Weitergabe der bestehenden Verbindung nachführen (festpos-update net_share)."""
    req = {"action": "net_share", **({"allow": allow} if allow else {}), **({} if want else {"off": True})}
    r = _helper(req, 60)
    if not r.get("ok"):
        raise release.ReleaseError(r.get("code") or "internal", **(r.get("kw") or {}))
    _keep.update(share=want, allow=allow if want else None)


def _share_follow() -> None:
    """Verbunden: weitergeben, wenn nötig – an die richtigen Adressen (neue Handhelds, VIP gewechselt, Ausrollen)."""
    via = _keep.get("via")
    want = bool(_keep.get("device")) or (via in ("wlan", "usb") and _holds_vip())
    allow = None if _rollout() else share_allow()
    if want == bool(_keep.get("share")) and (not want or allow == _keep.get("allow")):
        return
    try:
        _share_set(want, allow)
    except release.ReleaseError as e:
        log.warning("Internet weitergeben: %s", e.code)
        return
    log.info("Internet weitergeben: %s", ("an alle (Ausrollen)" if allow is None else f"an {len(allow)} Geräte")
             if want else "aus")


def keep_status() -> dict | None:
    """Für die Statusanzeige (in den Infos jedes Pi, system.local_info): Stand der dauerhaften Verbindung."""
    if not keep_on():
        return None
    st = _keep.get("state")
    return {"state": st, "via": _keep.get("via"), "online": _keep.get("online"), "peer": _keep.get("peer"),
            "share": bool(_keep.get("share")), "device": bool(_keep.get("device")), "since": _keep.get("since"),
            "reason": _keep.get("reason"), "ssid": (_raw().get("ssid") or "") if _keep.get("reason") == "wlan_not_found" else "",
            "tunnel": _tunnel() if st == "up" else None}


def phone_issue() -> bool:
    """Bestell-App: Internet über WLAN/USB des Pi weg, obwohl Karte/TWINT oder QR-Bestellung es brauchen (der Weg über
    den Router meldet sich als Mobilfunk-Router, status.uplink_issue). Erst nach der ersten Prüfung."""
    from . import status
    if not keep_on() or (_cfg().get("via") or "lan") == "lan" or not status.internet_needed():
        return False
    st = _keep.get("state")
    return st in ("failed", "no_way") or (st == "up" and _keep.get("online") is False)


def _helper_state() -> dict:
    """Stand der Verbindung laut Helfer (update-status/internet.json) – bleibt über einen Neustart der App."""
    try:
        d = json.loads((update.STATUS_DIR / "internet.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _adopt(cfg: dict, now: float) -> bool:
    """Erster Takt nach dem Start der App: steht die dauerhafte Verbindung noch (gleicher Weg), sie übernehmen statt
    abbauen und neu aufbauen – sonst bräche sie bei jedem Neustart und jedem Update kurz ab."""
    h = _helper_state()
    via = cfg.get("via") if cfg.get("via") in VIA else "lan"
    if not h.get("keep") or h.get("via") not in VIA:
        return False
    peer = None
    if h["via"] == "lan" and via in ("wlan", "usb"):          # über einen anderen Pi mit dem Gerät
        peer = next((n for n, p in sorted((getattr(getattr(rt, "monitor", None), "peers", None) or {}).items())
                     if ((p.get("data") or {}).get("internet") or {}).get("ip") == h.get("gateway")), None)
        if not peer:
            return False
    elif h["via"] != via or (via == "lan" and h.get("gateway") != cfg.get("gateway")) or \
            (via == "wlan" and (h.get("ssid") or "") != (cfg.get("ssid") or "")):
        return False                                         # anderer Weg, Router oder ein anderes WLAN eingestellt
    _keep.clear()
    _keep.update(state="up", via=via, iface=h.get("dev") or "", since=int(h.get("at") or now), share=bool(h.get("share")),
                 peer=peer, device=h["via"] in ("wlan", "usb"), allow=None, fails=0, tries=0, adopted=True,
                 want=_want(cfg))
    log.info("Internet dauerhaft: bestehende Verbindung übernommen (%s)", via)
    return True


def _cluster() -> bool:
    return bool(Config.peers)


def _wlan_not_mine() -> bool:
    """WLAN mit 3 Pi: nur der Pi mit der VIP verbindet sich mit dem Hotspot (viele nehmen nur wenige Geräte)."""
    return _cluster() and not _holds_vip()


def _other_wlan_holder() -> bool:
    """Ein anderer Pi ist selbst mit dem WLAN verbunden und gibt weiter (Stand der letzten Abfrage)?"""
    peers = getattr(getattr(rt, "monitor", None), "peers", None) or {}
    return any((n := (p.get("data") or {}).get("internet") or {}).get("up") and n.get("share") and n.get("device")
               and time.time() - (p.get("at") or 0) < 60 for p in peers.values())


def keep_tick() -> None:
    """Monitor-Takt (jeder Pi, jede Minute): Verbindung nach Schalter (c) halten oder abbauen."""
    if not keep_on():
        if _keep.get("state") in ("up", "failed") or _helper_state().get("keep"):
            _keep.clear()
            _net_down(force=True)
            log.info("Internet dauerhaft: aus")
        _keep.clear()
        return
    if _task and not _task.done():
        return                                               # Update aus dem Internet läuft gerade
    now = time.time()
    cfg = _cfg()
    if not _keep:
        _adopt(cfg, now)
    if _keep.get("state") == "up" and _keep.get("want") not in (None, _want(cfg)):
        log.info("Internet dauerhaft: anderer Weg eingestellt (z.B. fester Router) – neu verbinden")
        _keep.clear()
        _net_down(force=True)
    if _keep.get("state") == "up":
        reason = _probe()
        _keep.update(online=reason is None, reason=reason, checked=int(now))
        if reason is None and _keep.get("device") and _keep.get("via") == "wlan" and _wlan_not_mine() \
                and _other_wlan_holder():
            # zwei Pi am Hotspot (z.B. beide gleichzeitig gestartet): der ohne VIP geht über den anderen
            log.info("Internet dauerhaft: WLAN dem Pi mit der VIP überlassen")
            _keep.clear()
        elif reason is None:
            _keep.update(fails=0, tries=0, next=None)
            _share_follow()
            return
        else:
            _keep["fails"] = (_keep.get("fails") or 0) + 1
            log.warning("Internet dauerhaft: nicht erreichbar (%s, %d. Mal)", reason, _keep["fails"])
            if _keep["fails"] < KEEP_FAILS or now < (_keep.get("next") or 0):
                return                                       # kurz weg (schwaches Netz): nicht gleich neu verbinden
    elif now < (_keep.get("next") or 0):
        return
    tries = (_keep.get("tries") or 0) + 1                     # Pause bis zum nächsten Verbinden: 1, 2, 4 … 15 Minuten
    wait = BACKOFF[min(tries, len(BACKOFF)) - 1]
    psk = rt.store.setting("update_net_psk") or ""
    via = cfg.get("via") if cfg.get("via") in VIA else "lan"
    found = devices()
    share, peer, device = False, None, False
    has_dev = via == "wlan" and found["wlan"] or via == "usb" and any(d["link"] for d in found["usb"])
    if has_dev and via == "wlan" and _wlan_not_mine():
        has_dev = False                                      # WLAN: nur der Pi mit der VIP am Hotspot, die anderen über ihn
    if has_dev:
        share = device = True                                # dieser Pi hat das Gerät: verbinden und weitergeben
    elif via in ("wlan", "usb"):
        peer = _peer_sharing()
        if not peer:
            _keep.update(state="no_way", via=via, next=now + 60, code=None, reason=None, last=int(now))
            return
        cfg = {"via": "lan", "gateway": peer["ip"], "dns": cfg.get("dns") or "1.1.1.1"}
        share = _holds_vip()                                 # die Handhelds kommen hier an: an den Pi weiterreichen
    allow = None if _rollout() else share_allow()
    try:
        iface = _net_up(cfg, psk, share=share, keep=True, allow=allow if share else None)
    except release.ReleaseError as e:
        _keep.clear()                                        # Pause ab dem Ende des Versuchs (WLAN-Suche dauert)
        _keep.update(state="failed", via=via, code=e.code, kw={k: str(v) for k, v in (e.kw or {}).items()},
                     reason=REASONS.get(e.code, "connect"), tries=tries, last=int(now), next=time.time() + wait)
        log.warning("Internet dauerhaft: %s (%d. Versuch, nächster in %d s)", e.code, tries, wait)
        return
    _keep.clear()
    _keep.update(state="up", via=via, iface=iface, since=int(now), share=share, peer=peer and peer["node"],
                 checked=int(now), device=device, allow=allow if share else None, fails=0, last=int(now),
                 want=_want(_cfg()))
    reason = _probe()
    _keep.update(online=reason is None, reason=reason, tries=0 if reason is None else tries,
                 next=None if reason is None else time.time() + wait)
    log.info("Internet dauerhaft: ein (%s%s)", via, f" über Pi {peer['node']}" if peer else "")


# -- GitHub -----------------------------------------------------------------------------------------------------
class _Redirect(urllib.request.HTTPRedirectHandler):
    """Dateien liegen hinter einer Weiterleitung (anderer Server): dorthin ohne Token, und nur über HTTPS."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            if urlsplit(newurl).scheme != urlsplit(API).scheme:
                raise GhError("gh_http", status=code)
            if urlsplit(newurl).hostname != urlsplit(req.full_url).hostname:
                new.remove_header("Authorization")
        return new


def _opener() -> urllib.request.OpenerDirector:
    ctx = ssl.create_default_context()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _Redirect(),
                                       urllib.request.HTTPSHandler(context=ctx))


def _open(url: str, token: str, accept: str = "application/vnd.github+json"):
    hdr = {"Accept": accept, "User-Agent": f"FestPOS/{APP_VERSION}", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    try:
        return _opener().open(urllib.request.Request(url, headers=hdr), timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise GhError("gh_auth") from None
        if e.code in (403, 429) and e.headers.get("X-RateLimit-Remaining") == "0":
            raise GhError("gh_rate") from None
        if e.code == 403:
            raise GhError("gh_auth") from None
        if e.code == 404:
            raise GhError("gh_not_found") from None
        raise GhError("gh_http", status=e.code) from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLError):
            raise GhError("gh_tls") from None
        raise GhError("gh_offline") from None
    except (TimeoutError, OSError):
        raise GhError("gh_offline") from None


def _json(url: str, token: str) -> dict:
    with _open(url, token) as r:
        try:
            d = json.loads(r.read(2 * release.MB))
        except ValueError:
            raise GhError("gh_http", status=r.status) from None
    return d if isinstance(d, dict) else {}


def latest(repo: str, token: str) -> dict:
    """Neuster Release -> {tag, version, name, published, size, zip, sig, newer}."""
    try:
        d = _json(f"{API}/repos/{repo}/releases/latest", token)
    except GhError as e:
        if e.code != "gh_not_found":
            raise
        _json(f"{API}/repos/{repo}", token)                    # Repo da? Dann fehlt nur der Release
        raise GhError("gh_no_release") from None
    tag = str(d.get("tag_name") or "")[:60]
    version = tag[1:] if tag[:1] in "vV" else tag
    assets = {str(a.get("name") or "").lower(): a for a in d.get("assets") or [] if isinstance(a, dict)}
    z, s = assets.get(ZIP_NAME), assets.get(SIG_NAME)
    if not z or not s:
        raise GhError("gh_no_assets", version=tag)
    for a in (z, s):                                          # nur über die API derselben Adresse (mit Token)
        if not str(a.get("url") or "").startswith(f"{API}/repos/"):
            raise GhError("gh_http", status="url")
    version = version if release.VERSION_RE.match(version) else None
    return {"tag": tag, "version": version, "name": str(d.get("name") or "")[:80],
            "published": str(d.get("published_at") or "")[:20], "size": int(z.get("size") or 0),
            "zip": z["url"], "sig": s["url"], "newer": bool(version and release.newer(version, APP_VERSION))}


def _download(url: str, token: str, dest: Path, limit: int, progress=None) -> int:
    n = 0
    with _open(url, token, "application/octet-stream") as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            n += len(chunk)
            if n > limit:
                raise release.ReleaseError("too_large", mb=release.MAX_ZIP // release.MB)
            f.write(chunk)
            if progress:
                progress(n)
    return n


# -- Internet ein/aus über festpos-update -----------------------------------------------------------------------
def _helper(req: dict, wait: float) -> dict:
    rid = f"net-{int(time.time())}-{secrets.token_hex(3)}"
    update.place_request(req, rid, netz=True)                  # eigener Dienst: wartet nicht hinter einem Update
    end = time.monotonic() + wait
    while time.monotonic() < end:
        r = update._result(rid)
        if r:
            return r
        time.sleep(0.3)
    raise release.ReleaseError("no_answer")


def _net_up(cfg: dict, psk: str, share: bool = False, keep: bool = False, allow: list[str] | None = None) -> str:
    """Internet ein (über festpos-update). share: an die Geräte im FestPOS-Netz weitergeben – an alle (Ausrollen,
    fully.py) oder nur an allow. keep: dauerhaft (Schalter c). Steht die dauerhafte Verbindung schon, bleibt sie;
    fürs Ausrollen gibt sie dann ans ganze Netz weiter (bis keep_tick wieder einschränkt)."""
    if _closed():
        raise release.ReleaseError("net_none")
    if not keep and _keep.get("state") == "up" and _keep.get("want") not in (None, _want()):
        _keep.clear()                                          # anderer Weg eingestellt: die alte Verbindung nicht nehmen
        _net_down(force=True)
    if not keep and _keep.get("state") == "up":
        if share and (not _keep.get("share") or _keep.get("allow")):
            _share_set(True, None)
        return str(_keep.get("iface") or "")
    req = {"action": "net_up", "via": cfg.get("via"), **({"share": True} if share else {}),
           **({"keep": True} if keep else {}), **({"allow": allow} if share and allow else {})}
    if cfg.get("via") == "lan":
        req.update(gateway=cfg.get("gateway"), dns=cfg.get("dns"))
    elif cfg.get("via") == "wlan":
        req.update(ssid=cfg.get("ssid"), psk=psk)
    r = _helper(req, NET_WAIT)
    if not r.get("ok"):
        raise release.ReleaseError(r.get("code") or "internal", **(r.get("kw") or {}))
    return str(r.get("iface") or "")


def _net_down(force: bool = False) -> None:
    if not force and keep_on() and _keep.get("state") == "up":
        return                                                  # dauerhaft verbunden: nicht abbauen
    try:
        _helper({"action": "net_down"}, 60)
    except Exception as e:  # noqa: BLE001 – der Zeitgeber im Helfer schaltet sonst ab
        log.warning("Internet aus: %s", e)


# -- Vorgang (im Hintergrund) -----------------------------------------------------------------------------------
def _set(**kw) -> None:
    _job.update(kw, at=int(time.time()))


def _run(op: str, actor: str) -> None:
    cfg, token, psk = _cfg(), rt.store.setting("update_net_token") or "", rt.store.setting("update_net_psk") or ""
    repo = repo_of(cfg)
    up = False
    try:
        keys = update.trusted_keys() if op == "download" else None
        _set(state="connecting")
        _net_up(cfg, psk)
        up = True
        _set(state="searching")
        found = latest(repo, token)
        _set(found={k: v for k, v in found.items() if k not in ("zip", "sig")})
        if op == "check":
            _set(state="done")
            return
        if not found["newer"]:
            raise release.ReleaseError("not_newer", version=found["version"] or found["tag"], installed=APP_VERSION)
        if found["size"] > release.MAX_ZIP:
            raise release.ReleaseError("too_large", mb=release.MAX_ZIP // release.MB)
        update.UPDATE_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
        name = f"net-{secrets.token_hex(6)}"
        tmp = update.UPDATE_DIR / f"{name}.part"
        try:
            _set(state="downloading", done=0, total=found["size"])
            _download(found["zip"], token, tmp, release.MAX_ZIP, lambda n: _job.update(done=n))
            sig = update.UPDATE_DIR / f"{name}.sig.part"        # .part: das Aufräumen lässt es stehen
            try:
                _download(found["sig"], token, sig, release.MAX_SIG)
                sig_data = sig.read_bytes()
            finally:
                sig.unlink(missing_ok=True)
            _net_down()
            up = False
            _set(state="verifying")
            rec = update.accept_bundle(tmp, sig_data, keys, actor, ZIP_NAME, f"{repo} {found['tag']}")
        finally:
            tmp.unlink(missing_ok=True)
        _set(state="done", version=rec["version"])
    except (GhError, release.ReleaseError) as e:
        _set(state="failed", code=e.code, kw={k: str(v) for k, v in (e.kw or {}).items()})
    except Exception as e:  # noqa: BLE001 – Ergebnis muss in der Verwaltung ankommen
        log.exception("Update aus dem Internet")
        _set(state="failed", code="internal", kw={"name": type(e).__name__})
    finally:
        if up:
            _net_down()
        try:
            lb.audit(rt.store, actor, "update_net", op=op, result=_job.get("state"), code=_job.get("code") or "",
                     version=(_job.get("found") or {}).get("tag") or "")
        except Exception:  # noqa: BLE001
            log.exception("Protokoll")


def _actor(request: Request) -> str:
    return (current_user(request) or {}).get("name") or "?"


def _start(op: str, actor: str) -> dict:
    global _task
    if _task and not _task.done():
        raise HTTPException(409, i18n.t("err.update.net_busy"))
    if not allowed():
        raise HTTPException(409, i18n.t("err.update.net_none" if _closed() else "err.update.net_off"))
    cfg = _cfg()
    if not REPO_RE.match(repo_of(cfg)):
        raise HTTPException(422, update.err_text("gh_no_repo"))
    if op == "download":
        try:
            update.trusted_keys()
        except release.ReleaseError as e:
            raise update._err(e, 409) from e
    _job.clear()
    _set(op=op, state="connecting", by=actor, via=cfg.get("via"), started=int(time.time()))
    _task = asyncio.get_running_loop().create_task(asyncio.to_thread(_run, op, actor))
    return view()


# -- Verwaltung -------------------------------------------------------------------------------------------------
@router.get("")
async def get_view():
    return await db(view)


class NetIn(BaseModel):
    repo: str = Field(default="", max_length=140)
    token: str | None = Field(default=None, max_length=255)            # None/leer = unverändert
    clear_token: bool = False
    via: str = Field(default="lan", pattern=r"^(lan|wlan|usb)$")
    gateway: str = Field(default="", max_length=15)
    dns: str = Field(default="", max_length=15)
    ssid: str = Field(default="", max_length=32)
    psk: str | None = Field(default=None, max_length=64)               # None/leer = unverändert


def _ip(v: str, key: str) -> str:
    try:
        return str(ipaddress.IPv4Address(v.strip()))
    except ValueError:
        raise HTTPException(422, i18n.t(key)) from None


@router.put("")
async def save(n: NetIn, request: Request):
    repo = n.repo.strip().removeprefix("https://github.com/").removesuffix(".git").strip("/")
    if repo and not REPO_RE.match(repo):
        raise HTTPException(422, update.err_text("gh_no_repo"))
    token = (n.token or "").strip()
    if token and not TOKEN_RE.match(token):
        raise HTTPException(422, i18n.t("err.update.net_token"))
    new = {"repo": repo, "via": n.via, "gateway": "", "dns": "", "ssid": " ".join(n.ssid.split())}
    if n.via == "lan":
        new.update(gateway=_ip(n.gateway, "err.update.net_gateway_bad"), dns=_ip(n.dns, "err.update.net_dns_bad"))
    if n.via == "wlan" and not SSID_RE.match(new["ssid"]):
        raise HTTPException(422, i18n.t("err.update.net_ssid"))
    psk = n.psk or ""
    if psk and not PSK_RE.match(psk):
        raise HTTPException(422, i18n.t("err.update.net_psk"))
    up = "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    stmts = [(up, ("update_net", json.dumps(new, ensure_ascii=False)))]
    if token:
        stmts.append(secretbox.put_stmt("update_net_token", token))
    elif n.clear_token:
        stmts.append(("DELETE FROM settings WHERE key='update_net_token'", ()))
    if psk:
        stmts.append(secretbox.put_stmt("update_net_psk", psk))
    shown = {**new, **({"token": short_token(token)} if token else {"token": "-"} if n.clear_token else {}),
             **({"psk": "***"} if psk else {})}
    stmts.append(lb.audit_stmt(_actor(request), "update_net_config", **shown))
    await db(lambda: rt.store.batch(stmts))
    return await db(view)


@router.post("/check")
async def check(request: Request):
    return _start("check", _actor(request))


@router.post("/download")
async def download(request: Request):
    return _start("download", _actor(request))
