"""Cloudflare Tunnel zur QR-Bestellung (T22) aus der Verwaltung ein- und ausschalten.

Die Verwaltung speichert den Token verschlüsselt in der Datenbank (gast_tunnel_enc, wie der Payrexx-Schlüssel). So
gilt er für alle Pi, auch für später dazukommende. Jeder Pi vergleicht jede Minute (Monitor-Takt) den Fingerabdruck
des gewünschten Tokens mit dem, den festpos-update bei ihm abgelegt hat (/etc/festpos/tunnel-app.fp). Weichen sie ab,
gibt er dem root-Helfer den Auftrag «tunnel» (Token setzen oder aus). FestPOS selbst bekommt dafür keine root-Rechte.
Benutzer, Firewall und Sandbox des Gästedienstes kommen nie aus der Datenbank, sondern aus festpos-node und den
systemd-Diensten. TUNNEL_TOKEN in festpos.conf (Notweg) hat Vorrang (/etc/festpos/tunnel-conf).

cloudflared kommt nicht mit FestPOS: Ist der Tunnel an und fehlt es, gibt jeder Pi dem Helfer alle 10 Minuten den
Auftrag «cloudflared» (installieren aus der Paketquelle von Cloudflare, braucht Internet); ist es da, einmal pro Woche
mit «refresh» (aktualisieren).
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

from . import payrexx_api as px
from . import release, tunnel_token, update
from .core import log, rt

SETTING = "gast_tunnel_enc"
ETC = Path(os.environ.get("FESTPOS_TUNNEL_DIR", "/etc/festpos"))
CLOUDFLARED = Path(os.environ.get("FESTPOS_CLOUDFLARED", "/usr/bin/cloudflared"))
RETRY = 120                                       # nach einem Fehler so lange warten
WAIT = 60                                         # so lange auf festpos-update warten
CF_WAIT = 900                                     # Installieren/Aktualisieren darf so lange dauern (apt)
CF_RETRY = 600                                    # fehlt cloudflared: so oft nochmals versuchen
CF_REFRESH = 7 * 86400                            # so oft aktualisieren
_state: dict = {}                                 # letzter Fehler dieses Pi: {"fp", "next", "code", "kw"}
_cf: dict = {"next": 0.0}                         # nächster Versuch für cloudflared


def wanted() -> str | None:
    """Token aus der Verwaltung (None: keiner oder nicht mehr lesbar, z.B. CLUSTER_SECRET geändert)."""
    enc = rt.store.setting(SETTING, "") or ""
    tok = px.unseal(enc) if enc else None
    return tok if tok and tunnel_token.parse(tok) else None


def configured() -> dict:
    """Für die Verwaltung: Token gesetzt, lesbar und die Tunnel-ID (gekürzt) – nie der Token selbst."""
    enc = rt.store.setting(SETTING, "") or ""
    tok = px.unseal(enc) if enc else None
    info = tunnel_token.parse(tok) if tok else None
    return {"set": bool(enc), "ok": info is not None, "id": info["tunnel"][:8] if info else None}


def seal(token: str) -> str:
    """Neuer Token aus der Verwaltung -> verschlüsselt (ValueError, wenn er nicht wie ein Token von Cloudflare aussieht)."""
    if not tunnel_token.parse(token):
        raise ValueError("tunnel_bad")
    return px.seal(token.strip())


def _applied() -> str:
    try:
        return (ETC / "tunnel-app.fp").read_text().strip()
    except OSError:
        return ""


def _active() -> bool | None:
    if os.environ.get("FESTPOS_HOST_CHECKS", "1") == "0" or not (ETC / "tunnel.env").exists():
        return None
    try:
        r = subprocess.run(["systemctl", "is-active", "festpos-tunnel.service"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() == "active"


def local_status() -> dict:
    """Für die Infos jedes Pi (system.local_info): Stand des Tunnels hier. source: conf (festpos.conf), app
    (Verwaltung) oder None (aus); synced: der Token der Verwaltung ist hier eingerichtet (bzw. aus)."""
    want = wanted()
    conf = (ETC / "tunnel-conf").exists()
    on = (ETC / "tunnel.env").exists()
    out = {"cloudflared": CLOUDFLARED.exists(), "source": "conf" if conf else "app" if on else None,
           "active": _active(), "synced": _applied() == (tunnel_token.fingerprint(want) if want else "")}
    if _state.get("code"):
        out["error"] = update.err_text(_state["code"], _state.get("kw"))
    return out


def _refreshed() -> float:
    try:
        return (ETC / "cloudflared-refresh").stat().st_mtime
    except OSError:
        return 0.0


def cf_tick(now: float | None = None, wait: bool = False) -> None:
    """Tunnel an (aus der Verwaltung oder festpos.conf): cloudflared installieren, solange es fehlt; sonst einmal pro
    Woche aktualisieren. Ohne Internet meldet der Helfer «fehlt noch» – dann in CF_RETRY Sekunden wieder. Läuft im
    Hintergrund (apt braucht Minuten), der Monitor-Takt wartet nicht; wait=True nur für die Tests."""
    now = time.time() if now is None else now
    running = _cf.get("th")
    if not (ETC / "tunnel.env").exists() or now < _cf["next"] or (running and running.is_alive()):
        return
    have = CLOUDFLARED.exists()
    if have and now - _refreshed() < CF_REFRESH:
        return
    _cf["next"] = now + CF_RETRY
    th = _cf["th"] = threading.Thread(target=_cf_job, args=(have,), name="cloudflared", daemon=True)
    th.start()
    if wait:
        th.join()


def _cf_job(have: bool) -> None:
    from . import update_net
    try:
        r = update_net._helper({"action": "cloudflared", **({"refresh": True} if have else {})}, CF_WAIT)
    except release.ReleaseError as e:
        r = {"ok": False, "code": e.code}
    if r.get("cloudflared") and not have:
        log.info("QR-Bestellung: cloudflared installiert")
    elif not r.get("cloudflared"):
        log.warning("QR-Bestellung: cloudflared fehlt noch (Internet?) – nächster Versuch in %d Minuten", CF_RETRY // 60)


def tick() -> None:
    """Monitor-Takt (jeder Pi, jede Minute): Token der Verwaltung hier einrichten oder den Tunnel ausschalten."""
    if not update.REQ_DIR.is_dir():
        return                                    # kein root-Helfer (Entwicklung, Tests ohne Helfer)
    want = wanted()
    fp = tunnel_token.fingerprint(want) if want else ""
    if _applied() == fp:
        _state.clear()
        cf_tick()
        return
    now = time.time()
    if _state.get("fp") == fp and now < (_state.get("next") or 0):
        return
    from . import update_net
    try:
        r = update_net._helper({"action": "tunnel", **({"token": want} if want else {"off": True})}, WAIT)
    except release.ReleaseError as e:
        r = {"ok": False, "code": e.code, "kw": e.kw}
    if r.get("ok"):
        _state.clear()
        log.info("QR-Bestellung: Tunnel %s", "eingerichtet" if want else "aus")
        return
    _state.update(fp=fp, next=now + RETRY, code=r.get("code") or "internal",
                  kw={k: str(v) for k, v in (r.get("kw") or {}).items()})
    log.warning("QR-Bestellung: Tunnel einrichten gescheitert: %s", _state["code"])


def pis_view() -> list[dict]:
    """Für die Verwaltung: Stand des Tunnels auf jedem Pi (dieser Pi jetzt, die anderen zuletzt gemeldet)."""
    from . import system
    return [{"id": p["id"], "here": p["here"], "reachable": p["reachable"],
             **((local_status() if p["here"] else (p.get("info") or {}).get("tunnel")) or {})} for p in system.nodes()]
