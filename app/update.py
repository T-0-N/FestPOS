"""Updates über die Verwaltung, Nachziehen und USB-Stick (Version 2026.10.10-26).

Ablauf
  1. Hochladen (Verwaltung › Update): festpos.zip + festpos.zip.sig. Vorprüfung hier, mit den Schlüsseln, die
     festpos-update aus der Boot-Partition meldet (keys.json): Signatur, Aufbau, Version. Das Paket liegt danach
     in UPDATE_DIR/<sha256>.zip (+ .sig), die Angaben in settings.update_upload.
  2. «Auf alle Pi verteilen» legt einen Plan an (settings.update_plan). Ab da arbeitet jeder Pi für sich:
  3. Schleife (alle 5 s, step): Bin ich an der Reihe? Zuerst die nicht druckenden Pi (nach Name), der druckende
     zuletzt; immer nur einer (settings.update_lock); und nur, wenn alle anderen erreichbar sind – sonst fehlte
     der Datenbank die Mehrheit, solange dieser Pi neu startet. Dann: Paket bei einem anderen Pi holen
     (/api/internal/update/…, mit CLUSTER_SECRET) und den Auftrag an festpos-update (root) geben. Der prüft
     selbst nochmals und spielt mit update.sh ein; die App startet dabei neu und meldet danach das Ergebnis
     (settings.update_status.<pi>).
  4. Ein Fehler hält den Plan an (update.sh hat diesen Pi schon zurückgeschaltet). Weiter geht es erst mit
     «Erneut versuchen», «Abbrechen» oder «Alle zurück».
Nachziehen: Der Plan bleibt stehen. Kommt später ein Pi mit älterer Version dazu (war ausgeschaltet, neue
SD-Karte), ist er einfach an der Reihe und holt das Paket bei den anderen.
USB-Stick: festpos-update prüft den Stick und legt ein gültiges Paket nach /run/festpos/update-status/paket;
die Schleife übernimmt es und legt den Plan selbst an.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import ssl
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import i18n, ledger_base as lb, release, timesync
from .core import APP_VERSION, Config, Throttle, admin, cluster_info, current_user, db, rt

log = logging.getLogger("festpos.update")
router = APIRouter(prefix="/api/admin/update", dependencies=[Depends(admin)])
internal = APIRouter()

UPDATE_DIR = Path(os.environ.get("FESTPOS_UPDATE_DIR", "/var/lib/festpos/update"))       # Pakete (gehört festpos)
REQ_DIR = Path(os.environ.get("FESTPOS_UPDATE_REQ", "/run/festpos/update"))              # Aufträge an festpos-update
REQ_NEU = Path(os.environ.get("FESTPOS_UPDATE_REQ_NEU", "/run/festpos/update-neu"))      # (erst fertig schreiben)
REQ_NETZ = Path(os.environ.get("FESTPOS_UPDATE_REQ_NETZ", "/run/festpos/update-netz"))    # nur Internet-Aufträge
STATUS_DIR = Path(os.environ.get("FESTPOS_UPDATE_STATUS", "/run/festpos/update-status"))  # Antworten (gehört root)
ALT_DIR = Path(os.environ.get("FESTPOS_ALT", "/opt/festpos.alt"))                        # Sicherung (update.sh)

ENABLED = True              # Schleife (Tests schalten sie ab und rufen step() selbst auf)
INTERVAL = float(os.environ.get("FESTPOS_UPDATE_INTERVAL", "5"))
SETTLE = float(os.environ.get("FESTPOS_UPDATE_SETTLE", "20"))    # nach dem Start so lange warten, bevor dieser Pi beginnt
RETRY_WAIT = 30             # Paket nicht erhältlich: so lange bis zum nächsten Versuch
NO_ANSWER = 180             # kein Ergebnis und festpos-update arbeitet nicht daran: fehlgeschlagen
LOCK_STALE = 50 * 60        # Sperre eines Pi, der sich nicht mehr meldet
RUNNING_STALE = 55 * 60
SHOW_DONE = 600             # Statusanzeige: «abgeschlossen» so lange zeigen
RECENT_ORDERS_MIN = 10
RUNNING = ("fetching", "installing")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
ACTOR = "FestPOS"

_task: asyncio.Task | None = None
_started_at = 0.0


class FetchError(Exception):
    pass


# -- Einstellungen ----------------------------------------------------------------------------------------------
def _parse(raw: str | None) -> dict:
    try:
        d = json.loads(raw or "{}")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def _get(key: str) -> dict:
    return _parse(rt.store.setting(key))


def _put(key: str, value: dict) -> None:
    rt.store.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value, ensure_ascii=False)))


def _read_json(path: Path) -> dict | None:
    try:
        d = json.loads(release.read_regular(path, 256 * 1024))
    except (OSError, ValueError, release.ReleaseError):
        return None
    return d if isinstance(d, dict) else None


def _version_of(d: Path) -> str | None:
    try:
        v = (d / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return v or None


# Gründe (vom Helfer, aus release.py und von hier) -> Text in der Sprache der Anfrage
ERR_KEYS = {
    "sig_format": "err.update.sig_format", "sig_namespace": "err.update.sig_namespace", "no_key": "err.update.no_key",
    "unknown_key": "err.update.unknown_key", "bad_signature": "err.update.bad_signature",
    "not_a_file": "err.update.not_a_file", "missing": "err.update.missing", "too_large": "err.update.too_large",
    "zip_bad": "err.update.zip_bad", "zip_unsafe": "err.update.zip_unsafe", "zip_incomplete": "err.update.zip_incomplete",
    "version_bad": "err.update.version_bad", "sha_mismatch": "err.update.sha_mismatch", "not_newer": "err.update.not_newer",
    "version_mismatch": "err.update.version_mismatch", "rollback_changed": "err.update.rollback_changed",
    "no_backup": "err.update.no_backup", "request_bad": "err.update.request_bad", "update_failed": "err.update.update_failed",
    "crlf": "err.update.crlf", "not_installed": "err.update.not_installed",
    "rollback_failed": "err.update.rollback_failed", "internal": "err.update.internal", "no_helper": "err.update.no_helper",
    "no_answer": "err.update.no_answer", "fetch": "err.update.fetch", "interrupted": "err.update.interrupted",
    # Update aus dem Internet (update_net.py und festpos-update)
    "net_bad": "err.update.net_bad", "net_none": "err.update.net_none", "net_gateway": "err.update.net_gateway", "net_resolv": "err.update.net_resolv",
    "net_no_wlan": "err.update.net_no_wlan", "net_wlan": "err.update.net_wlan", "net_no_usb": "err.update.net_no_usb",
    "net_no_link": "err.update.net_no_link", "net_dhcp": "err.update.net_dhcp", "net_same_net": "err.update.net_same_net",
    "net_share": "err.update.net_share",
    # Tunnel zur QR-Bestellung (tunnel.py und festpos-update, T22)
    "tunnel_bad": "err.update.tunnel_bad", "tunnel_no_user": "err.update.tunnel_no_user",
    "gh_offline": "err.update.gh_offline", "gh_tls": "err.update.gh_tls", "gh_auth": "err.update.gh_auth",
    "gh_rate": "err.update.gh_rate", "gh_not_found": "err.update.gh_not_found", "gh_no_release": "err.update.gh_no_release",
    "gh_no_assets": "err.update.gh_no_assets", "gh_http": "err.update.gh_http", "gh_no_repo": "err.update.gh_no_repo",
}
PLACEHOLDERS = {"namespace", "fp", "mb", "name", "missing", "version", "installed", "backup", "iface", "status"}


def err_text(code: str | None, kw: dict | None = None) -> str:
    params = {k: str(v)[:80] for k, v in (kw or {}).items() if k in PLACEHOLDERS}
    key = ERR_KEYS.get(code or "", "err.update.internal")
    for p in PLACEHOLDERS:                                     # fehlende Werte nie als {platzhalter} zeigen
        params.setdefault(p, "?")
    return i18n.t(key, **params)


def _err(e: release.ReleaseError, status: int = 422) -> HTTPException:
    return HTTPException(status, err_text(e.code, e.kw))


# -- Dieser Pi --------------------------------------------------------------------------------------------------
def helper_keys() -> dict | None:
    """Was festpos-update über die Boot-Partition meldet (None: Helfer fehlt, z.B. vor dem ersten Update von Hand)."""
    return _read_json(STATUS_DIR / "keys.json")


def local_state() -> dict:
    """Für die Pi-Infos (system.publish): Helfer da? Welche Schlüssel? Welche Version liegt in der Sicherung?"""
    k = helper_keys()
    return {"helper": k is not None, "keys": [str(x.get("fp")) for x in (k or {}).get("keys", [])][:10],
            "backup": _version_of(ALT_DIR)}


def trusted_keys() -> list[release.Key]:
    k = helper_keys()
    if k is None:
        raise release.ReleaseError("no_helper")
    keys = release.keys_from_public(k.get("keys") or [])
    if not keys:
        raise release.ReleaseError("no_key")
    return keys


# -- Zustand aller Pi -------------------------------------------------------------------------------------------
def cluster_view() -> dict:
    """Mitglieder (stimmberechtigt), erreichbar, druckender Pi, gemeldete Infos und Update-Zustand pro Pi."""
    info = cluster_info()
    raw = (info.get("nodes") or {}) if info.get("mode") == "rqlite" else {}
    if raw:
        members = sorted(n for n, v in raw.items() if v.get("voter", True))
        reach = {n: bool(raw[n].get("reachable")) for n in members}
        leader = next((n for n in members if raw[n].get("leader")), None)
    else:
        members, reach, leader = [Config.node], {Config.node: True}, Config.node
    rows = rt.store.query("SELECT key, value FROM settings WHERE key LIKE 'node_info.%' OR key LIKE 'update_status.%'")
    kv = {r["key"]: _parse(r["value"]) for r in rows}
    infos = {n: kv.get(f"node_info.{n}", {}) for n in members}
    if Config.node in infos:                                  # dieser Pi: frisch, nicht von vor einer Minute
        infos[Config.node] = {**infos[Config.node], "version": APP_VERSION, "upd": local_state()}
    return {"members": members, "reach": reach, "leader": leader, "infos": infos,
            "statuses": {n: kv.get(f"update_status.{n}", {}) for n in members}}


def goal(plan: dict) -> str:
    return plan.get("version") if plan.get("kind") == "install" else plan.get("to")


def is_due(plan: dict, info: dict) -> bool:
    v = info.get("version")
    if not v:
        return False
    if plan.get("kind") == "install":
        return release.newer(plan.get("version"), v)
    return v == plan.get("from")


def able(plan: dict, info: dict) -> str | None:
    """None = dieser Pi kann es selbst; sonst der Grund (old: Version ohne Update-Helfer, key: Schlüssel fehlt,
    backup: andere Sicherung)."""
    upd = info.get("upd") or {}
    if not upd.get("helper"):
        return "old"
    if plan.get("kind") == "install":
        return None if (plan.get("signer") or {}).get("fp") in (upd.get("keys") or []) else "key"
    return None if upd.get("backup") == plan.get("to") else "backup"


def _running(s: dict, info: dict, now: float) -> bool:
    return s.get("state") in RUNNING and now - (s.get("at") or 0) < RUNNING_STALE and info.get("version") != s.get("to")


def decide(plan: dict, me: str | None, view: dict, lock: dict, now: float) -> dict:
    """Wer ist dran? -> {"action": idle|done|blocked|wait|go, "reason", "node", "skipped": {pi: Grund}}"""
    members, infos, st = view["members"], view["infos"], view["statuses"]
    pid = plan.get("id")
    if not pid or plan.get("cancelled"):
        return {"action": "idle"}
    for n in members:
        s = st.get(n) or {}
        if s.get("plan") == pid and s.get("state") == "failed":
            return {"action": "blocked", "reason": "failed", "node": n}
    for n in members:                                         # auch einer aus einem früheren Plan: nie zwei zugleich
        if _running(st.get(n) or {}, infos.get(n) or {}, now):
            return {"action": "wait", "reason": "running", "node": n}
    due = [n for n in members if is_due(plan, infos.get(n) or {})
           and not ((st.get(n) or {}).get("plan") == pid and (st.get(n) or {}).get("state") == "ok")]
    skipped = {n: able(plan, infos.get(n) or {}) for n in due if able(plan, infos.get(n) or {})}
    todo = [n for n in due if n not in skipped]
    if not todo:
        return {"action": "done", "skipped": skipped}
    missing = [n for n in members if not view["reach"].get(n)]
    if missing and len(members) > 1:
        return {"action": "wait", "reason": "unreachable", "node": missing[0], "skipped": skipped}
    nxt = sorted(todo, key=lambda n: (n == view["leader"], n))[0]
    if nxt != me:
        return {"action": "wait", "reason": "turn", "node": nxt, "skipped": skipped}
    if lock.get("node") not in (None, me) and now - (lock.get("at") or 0) < LOCK_STALE:
        return {"action": "wait", "reason": "lock", "node": lock.get("node"), "skipped": skipped}
    return {"action": "go", "node": me, "skipped": skipped}


def _busy(view: dict, now: float) -> str | None:
    for n in view["members"]:
        if _running(view["statuses"].get(n) or {}, view["infos"].get(n) or {}, now):
            return n
    return None


# -- Sperre: immer nur ein Pi -------------------------------------------------------------------------------------
def _lock() -> dict:
    return _get("update_lock")


def _claim_lock(me: str, pid: str, now: float) -> bool:
    raw = rt.store.setting("update_lock") or ""
    cur = _parse(raw)
    if cur.get("node") not in (None, me) and now - (cur.get("at") or 0) < LOCK_STALE:
        return False
    rt.store.execute("INSERT INTO settings(key,value) VALUES ('update_lock','') ON CONFLICT(key) DO NOTHING")
    new = json.dumps({"node": me, "plan": pid, "at": int(now)})
    return rt.store.execute("UPDATE settings SET value=? WHERE key='update_lock' AND value=?", (new, raw)).rows_affected == 1


def _release_lock(me: str) -> None:
    raw = rt.store.setting("update_lock") or ""
    if _parse(raw).get("node") == me:
        rt.store.execute("UPDATE settings SET value='' WHERE key='update_lock' AND value=?", (raw,))


# -- Pakete ------------------------------------------------------------------------------------------------------
def _bundle(sha: str) -> tuple[Path, Path]:
    return UPDATE_DIR / f"{sha}.zip", UPDATE_DIR / f"{sha}.sig"


def _have(sha: str) -> bool:
    z, s = _bundle(sha)
    return z.is_file() and s.is_file() and release.sha256_file(z) == sha


def _cleanup(keep: set[str]) -> None:
    """Nur die Pakete des Plans und des letzten Hochladens behalten (je ~25 MB)."""
    try:
        for p in UPDATE_DIR.iterdir():
            sha = p.name.split(".", 1)[0]
            if sha not in keep and (p.suffix in (".zip", ".sig") or (p.suffix == ".part" and time.time() - p.stat().st_mtime > 3600)):
                p.unlink(missing_ok=True)
    except OSError:
        pass


def _keep() -> set[str]:
    return {x for x in (_get("update_plan").get("sha"), _get("update_upload").get("sha")) if x}


def _peer_order(plan: dict) -> list[str]:
    """Andere Pi, der mit dem Paket (hat es hochgeladen) zuerst."""
    src_ip = _get(f"node_info.{plan.get('source')}").get("ip")
    others = [b for b in Config.peers if urlsplit(b).hostname != Config.node_ip]
    return sorted(others, key=lambda b: urlsplit(b).hostname != src_ip)


def _download(base: str, sha: str, size: int) -> None:
    if not timesync.secret_usable(Config.cluster_secret):
        raise FetchError("CLUSTER_SECRET fehlt")
    if not Config.cafile:                              # Geheimnis nur an ein geprüftes Zertifikat
        raise FetchError("FESTPOS_CA fehlt")
    ctx = ssl.create_default_context(cafile=Config.cafile)
    hdr = {"X-Festpos-Secret": Config.cluster_secret.encode().decode("latin-1")}
    UPDATE_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
    tmp = UPDATE_DIR / f"{sha}.{secrets.token_hex(3)}.part"
    try:
        h, n = hashlib.sha256(), 0
        with urllib.request.urlopen(urllib.request.Request(f"{base.rstrip('/')}/api/internal/update/{sha}.zip", headers=hdr),
                                    timeout=20, context=ctx) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 20):
                n += len(chunk)
                if n > max(size, 1):
                    raise FetchError("grösser als angekündigt")
                h.update(chunk)
                f.write(chunk)
        if h.hexdigest() != sha:
            raise FetchError("Prüfsumme stimmt nicht")
        with urllib.request.urlopen(urllib.request.Request(f"{base.rstrip('/')}/api/internal/update/{sha}.sig", headers=hdr),
                                    timeout=20, context=ctx) as r:
            sig = r.read(release.MAX_SIG + 1)
        if len(sig) > release.MAX_SIG:
            raise FetchError("Signatur zu gross")
        z, s = _bundle(sha)
        s.write_bytes(sig)
        os.replace(tmp, z)
    finally:
        tmp.unlink(missing_ok=True)


def fetch(plan: dict) -> str:
    """Paket des Plans auf diesen Pi holen (falls es nicht schon da ist) -> woher."""
    sha = plan["sha"]
    if _have(sha):
        return "local"
    errors = []
    for base in _peer_order(plan):
        try:
            _download(base, sha, int(plan.get("size") or 0))
            return base
        except Exception as e:  # noqa: BLE001 – nächster Pi
            errors.append(f"{urlsplit(base).hostname}: {e}")
    raise FetchError("; ".join(errors) or "kein anderer Pi")


# -- Aufträge an festpos-update --------------------------------------------------------------------------------------
def place_request(req: dict, rid: str, netz: bool = False) -> None:
    """Auftrag ablegen: erst fertig schreiben, dann in einem Schritt in den Auftragsordner (festpos-update.path).
    netz: Internet-Aufträge in den eigenen Ordner (festpos-update-netz.path) – sie warten so nicht hinter einem Update;
    fehlt er (Helfer noch älter), in den gemeinsamen."""
    target = REQ_NETZ if netz and REQ_NETZ.is_dir() else REQ_DIR
    if not target.is_dir() or not REQ_NEU.is_dir():
        raise release.ReleaseError("no_helper")
    tmp = REQ_NEU / rid
    tmp.write_text(json.dumps({**req, "id": rid}), encoding="utf-8")
    os.replace(tmp, target / f"anfrage-{rid}")


def _result(rid: str) -> dict | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", rid or ""):
        return None
    r = _read_json(STATUS_DIR / f"{rid}.json")
    return r if r and r.get("id") == rid else None


def _helper_working_on(rid: str) -> bool:
    w = _read_json(STATUS_DIR / "laeuft.json") or {}
    if w.get("id") != rid:
        return False
    try:
        os.kill(int(w.get("pid") or 0), 0)
    except PermissionError:
        return True                                           # läuft (als root)
    except (OSError, ValueError):
        return False
    return True


def _set_status(node: str, st: dict) -> None:
    _put(f"update_status.{node}", st)


def _finish(base: dict, ok: bool, code: str | None = None, kw: dict | None = None, msg: str = "",
            lines: list | None = None, version: str | None = None) -> None:
    me = Config.node
    st = {**base, "state": "ok" if ok else "failed", "code": code, "kw": kw or {}, "msg": (msg or "")[:300],
          "log": [str(x)[:300] for x in (lines or [])][-40:], "version": version or APP_VERSION, "at": int(time.time())}
    _set_status(me, st)
    _release_lock(me)
    lb.audit(rt.store, ACTOR, "update_pi", pi=me, ok=ok, version=st["version"], code=code, msg=st["msg"][:160])
    log.warning("Update auf diesem Pi %s: %s %s", "fertig" if ok else "FEHLGESCHLAGEN", st["version"], code or "")


def _own_progress(own: dict, now: float) -> None:
    """Eigenen Auftrag verfolgen (auch nach dem Neustart der App durch update.sh)."""
    me = Config.node
    if own.get("state") == "fetching":                        # Übertragung unterbrochen (App neu gestartet)
        _set_status(me, {**own, "state": "wait", "code": "interrupted", "at": int(now)})
        _release_lock(me)
        return
    rid = str(own.get("req") or "")
    res = _result(rid)
    if res is not None:
        _finish(own, bool(res.get("ok")), res.get("code"), res.get("kw") if isinstance(res.get("kw"), dict) else {},
                str(res.get("msg") or ""), res.get("log") if isinstance(res.get("log"), list) else [], res.get("after"))
        return
    if _helper_working_on(rid):
        return
    pending = REQ_DIR / f"anfrage-{rid}"
    if pending.exists() and now - (own.get("at") or 0) < NO_ANSWER:
        return
    if APP_VERSION == own.get("to"):                          # z.B. Pi neu gestartet und die Antwort ist weg
        _finish(own, True, msg="")
        return
    if now - (own.get("at") or 0) > NO_ANSWER:
        pending.unlink(missing_ok=True)
        _finish(own, False, "no_answer")


def _take_turn(plan: dict, now: float) -> None:
    me = Config.node
    if not _claim_lock(me, plan["id"], now):
        return
    base = {"plan": plan["id"], "from": APP_VERSION, "to": goal(plan), "at": int(now)}
    if plan.get("kind") == "install":
        _set_status(me, {**base, "state": "fetching"})
        try:
            src = fetch(plan)
            release.verify(_bundle(plan["sha"])[0], _bundle(plan["sha"])[1].read_bytes(), trusted_keys())
        except release.ReleaseError as e:
            _finish(base, False, e.code, {k: str(v) for k, v in e.kw.items()})
            return
        except (OSError, FetchError) as e:
            _set_status(me, {**base, "state": "wait", "code": "fetch", "msg": str(e)[:300], "at": int(time.time())})
            _release_lock(me)
            log.warning("Update-Paket nicht erhältlich: %s", e)
            return
        log.info("Update-Paket %s (von %s) – spiele Version %s ein", plan["sha"][:12], src, plan["version"])
        _cleanup(_keep())                                     # ältere Pakete auf diesem Pi weg
        req = {"action": "install", "sha": plan["sha"], "version": plan["version"]}
    else:
        req = {"action": "rollback", "from": plan.get("from"), "to": plan.get("to")}
    rid = f"{int(time.time())}-{secrets.token_hex(4)}"
    try:
        _set_status(me, {**base, "state": "installing", "req": rid, "at": int(time.time())})   # vor dem Auftrag!
        place_request(req, rid)
    except (release.ReleaseError, OSError) as e:
        _finish(base, False, getattr(e, "code", "no_helper"))


def step(now: float | None = None) -> None:
    """Ein Durchgang der Schleife auf diesem Pi."""
    now = time.time() if now is None else now
    usb_pickup(now)
    me = Config.node
    view = cluster_view()
    own = view["statuses"].get(me) or {}
    if own.get("state") in RUNNING:
        _own_progress(own, now)
        return
    plan = _get("update_plan")
    if not plan.get("id") or plan.get("cancelled") or time.monotonic() - _started_at < SETTLE:
        return
    if own.get("plan") == plan["id"] and own.get("state") == "wait" and now - (own.get("at") or 0) < RETRY_WAIT:
        return
    if decide(plan, me, view, _lock(), now)["action"] == "go":
        _take_turn(plan, now)


# -- USB-Stick (und «festpos-update --ordner») -----------------------------------------------------------------------
def usb_pickup(now: float) -> dict | None:
    """Gültiges Paket von festpos-update übernehmen und den Plan anlegen (einmal pro Stick)."""
    u = _read_json(STATUS_DIR / "usb.json")
    if not u or u.get("state") != "handed" or not SHA_RE.match(str(u.get("sha") or "")):
        return None
    seen = f"update_usb.{Config.node}"
    if _get(seen).get("at") == u.get("at"):
        return None
    _put(seen, {"at": u.get("at")})                          # nur einmal – auch wenn es gleich scheitert
    sha = u["sha"]
    try:
        UPDATE_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
        z, s = _bundle(sha)
        if not _have(sha):
            tmp = UPDATE_DIR / f"{sha}.usb.part"
            tmp.unlink(missing_ok=True)
            if release.copy_regular(STATUS_DIR / "paket" / f"{sha}.zip", tmp) != sha:
                tmp.unlink(missing_ok=True)
                raise release.ReleaseError("sha_mismatch")
            s.write_bytes(release.read_regular(STATUS_DIR / "paket" / f"{sha}.sig", release.MAX_SIG))
            os.replace(tmp, z)
        info = release.check_bundle(z, s.read_bytes(), trusted_keys())
    except (OSError, release.ReleaseError) as e:
        log.warning("Update vom USB-Stick nicht übernommen: %s", e)
        return None
    record = {"sha": sha, "version": info["version"], "size": info["size"], "signer": info["signer"], "node": Config.node,
              "at": int(now), "by": "usb", "name": ""}
    _put("update_upload", record)
    lb.audit(rt.store, ACTOR, "update_usb", pi=Config.node, version=info["version"], signer=info["signer"]["comment"])
    view = cluster_view()
    plan = _get("update_plan")
    newplan = {"kind": "install", "version": info["version"], "sha": sha, "size": info["size"], "signer": info["signer"]}
    if not any(is_due(newplan, view["infos"].get(n) or {}) for n in view["members"]):
        return None
    if plan.get("id") and not plan.get("cancelled") and plan.get("kind") == "install" and plan.get("sha") == sha:
        return plan
    newplan.update(id=f"{int(now)}-{secrets.token_hex(3)}", source=Config.node, by="usb", at=int(now), cancelled=None)
    _put("update_plan", newplan)
    _cleanup(_keep())
    log.warning("Update vom USB-Stick: Version %s wird auf alle Pi verteilt", info["version"])
    return newplan


# -- Schleife ---------------------------------------------------------------------------------------------------
async def _loop() -> None:
    while True:
        await asyncio.sleep(INTERVAL)
        if not ENABLED:
            continue
        try:
            await asyncio.to_thread(step)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 – nie aufgeben
            log.exception("Update-Schritt fehlgeschlagen")


def start() -> None:
    global _task, _started_at
    _started_at = time.monotonic()
    _task = asyncio.create_task(_loop(), name="update")


async def stop() -> None:
    if _task and not _task.done():
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)


# -- Anzeige ----------------------------------------------------------------------------------------------------
def _node_rows(view: dict, plan: dict, d: dict, signer_fp: str | None, now: float) -> list[dict]:
    out = []
    pid = plan.get("id")
    for n in view["members"]:
        info, s = view["infos"].get(n) or {}, view["statuses"].get(n) or {}
        upd = info.get("upd") or {}
        if not pid or plan.get("cancelled"):
            state = None
        elif s.get("plan") == pid and s.get("state") in ("failed", "ok", "wait"):
            state = s["state"]
        elif _running(s, info, now):
            state = "running"
        elif s.get("plan") == pid and info.get("version") == s.get("to"):
            state = "ok"
        elif n in d.get("skipped", {}):
            state = "skip_" + d["skipped"][n]
        elif is_due(plan, info):
            state = "next" if d.get("node") == n and d.get("reason") == "turn" else "queued"
        else:
            state = "same"
        mine = s if s.get("plan") == pid else {}
        out.append({"id": n, "here": n == Config.node, "reachable": bool(view["reach"].get(n)), "leader": n == view["leader"],
                    "version": info.get("version"), "backup": upd.get("backup"), "helper": bool(upd.get("helper")),
                    "key_ok": bool(signer_fp and signer_fp in (upd.get("keys") or [])), "state": state,
                    "code": mine.get("code"), "text": err_text(mine["code"], mine.get("kw")) if mine.get("code") else "",
                    "msg": mine.get("msg") or "",
                    "log": mine.get("log") or [], "at": mine.get("at")})
    return out


def _warnings() -> list[dict]:
    out = []
    jobs = rt.store.scalar("SELECT COUNT(*) FROM print_jobs WHERE status IN ('pending','printing')") or 0
    if jobs:
        out.append({"code": "jobs", "count": int(jobs)})
    since = (datetime.now() - timedelta(minutes=RECENT_ORDERS_MIN)).strftime("%Y-%m-%dT%H:%M:%S")
    recent = rt.store.scalar("SELECT COUNT(*) FROM orders WHERE test=0 AND created_at >= ?", (since,)) or 0
    if recent:
        out.append({"code": "orders", "count": int(recent), "minutes": RECENT_ORDERS_MIN})
    return out


def _rollback_option(view: dict) -> dict | None:
    infos = [view["infos"].get(n) or {} for n in view["members"]]
    versions = {i.get("version") for i in infos}
    backups = {(i.get("upd") or {}).get("backup") for i in infos}
    if len(versions) == 1 and len(backups) == 1 and None not in versions | backups:
        frm, to = versions.pop(), backups.pop()
        # zurück (älter) oder wieder vor? Der Server vergleicht, er kennt beide Versionsformate (1.0.0 > 2026.10.10-103)
        return {"from": frm, "to": to, "older": not release.newer(to, frm)}
    return None


def overview() -> dict:
    now = time.time()
    view = cluster_view()
    plan, up, lock = _get("update_plan"), _get("update_upload"), _lock()
    d = decide(plan, None, view, lock, now) if plan.get("id") else {"action": "idle"}
    k = helper_keys()
    signer = ((plan.get("signer") if plan.get("id") and not plan.get("cancelled") else None) or up.get("signer") or {}).get("fp")
    nodes = _node_rows(view, plan, d, signer, now)
    if up.get("sha"):
        probe = {"kind": "install", "version": up.get("version")}
        up = {**up, "due": [n for n in view["members"] if is_due(probe, view["infos"].get(n) or {})],
              "here": all(f.is_file() for f in _bundle(up["sha"])) if up.get("node") == Config.node else None}
    usb = _read_json(STATUS_DIR / "usb.json")
    if usb and usb.get("code"):
        usb["text"] = err_text(usb["code"], usb.get("kw") if isinstance(usb.get("kw"), dict) else {})
    return {"node": Config.node,
            "helper": {"installed": k is not None, "keys": [{"fp": x.get("fp"), "comment": x.get("comment")}
                                                             for x in (k or {}).get("keys", [])],
                       "problems": (k or {}).get("problems", [])},
            "nodes": nodes, "upload": up or None,
            "plan": {**plan, "phase": d["action"], "reason": d.get("reason"), "next": d.get("node")} if plan.get("id") else None,
            "rollback": _rollback_option(view), "warnings": _warnings(),
            "usb": usb if usb and now - (usb.get("at") or 0) < 3600 else None}


def status_view() -> dict | None:
    """Für die Statusanzeige: laufendes/wartendes/fehlgeschlagenes Update und der USB-Stick der letzten Minuten."""
    now = time.time()
    out: dict = {}
    plan = _get("update_plan")
    if plan.get("id") and not plan.get("cancelled"):
        view = cluster_view()
        d = decide(plan, None, view, _lock(), now)
        done = sum(1 for n in view["members"] if not is_due(plan, view["infos"].get(n) or {}))
        last = max([(s or {}).get("at") or 0 for s in view["statuses"].values() if (s or {}).get("plan") == plan["id"]] or [0])
        if d["action"] in ("wait", "blocked") or (d["action"] == "done" and now - last < SHOW_DONE and last):
            node = d.get("node")
            out["plan"] = {"kind": plan.get("kind"), "version": goal(plan), "phase": d["action"], "reason": d.get("reason"),
                           "node": node, "done": done, "total": len(view["members"]),
                           "back": ((view["infos"].get(node) or {}).get("version")) if d["action"] == "blocked" else None}
    u = _read_json(STATUS_DIR / "usb.json")
    if u and now - (u.get("at") or 0) < 300:
        out["usb"] = {k: u.get(k) for k in ("state", "version", "installed", "at")}
        if u.get("code"):
            out["usb"]["text"] = err_text(u["code"], u.get("kw") if isinstance(u.get("kw"), dict) else {})
    return out or None


# -- Verwaltung -----------------------------------------------------------------------------------------------------
def _actor(request: Request) -> str:
    return (current_user(request) or {}).get("name") or "?"


@router.get("")
async def get_overview():
    return await db(overview)


@router.post("/upload")
async def upload(request: Request, bundle: UploadFile = File(...), sig: UploadFile = File(...)):
    try:
        keys = await db(trusted_keys)
    except release.ReleaseError as e:
        raise _err(e, 409) from e
    sig_data = await sig.read(release.MAX_SIG + 1)
    UPDATE_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
    tmp = UPDATE_DIR / f"upload-{secrets.token_hex(6)}.part"
    try:
        n = 0
        with open(tmp, "wb") as f:
            while chunk := await bundle.read(1 << 20):
                n += len(chunk)
                if n > release.MAX_ZIP:
                    raise release.ReleaseError("too_large", mb=release.MAX_ZIP // release.MB)
                f.write(chunk)
        return await db(accept_bundle, tmp, sig_data, keys, _actor(request), bundle.filename or "")
    except release.ReleaseError as e:
        raise _err(e) from e
    finally:
        tmp.unlink(missing_ok=True)


def accept_bundle(tmp: Path, sig_data: bytes, keys: list[release.Key], by: str, name: str, source: str = "") -> dict:
    """Paket prüfen und als «letztes Paket» übernehmen (Hochladen, Internet). Danach: «Auf alle Pi verteilen»."""
    info = release.check_bundle(tmp, sig_data, keys)
    sha = info["sha256"]
    z, s = _bundle(sha)
    s.write_bytes(sig_data)
    os.replace(tmp, z)
    record = {"sha": sha, "version": info["version"], "size": info["size"], "signer": info["signer"], "node": Config.node,
              "at": int(time.time()), "by": by, "name": name[:80], **({"source": source[:120]} if source else {})}
    _put("update_upload", record)
    lb.audit(rt.store, by, "update_upload", version=info["version"], signer=info["signer"]["comment"],
             fp=info["signer"]["fp"], **({"source": source[:120]} if source else {}))
    _cleanup(_keep())
    return record


class StartIn(BaseModel):
    sha: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm: bool = False


class ConfirmIn(BaseModel):
    confirm: bool = False


def _names(nodes) -> str:
    return ", ".join(str(n).upper() for n in nodes)


def _check_all_there(view: dict) -> None:
    missing = [n for n in view["members"] if not view["reach"].get(n)]
    if missing and len(view["members"]) > 1:
        raise HTTPException(409, i18n.t("err.update.unreachable", pis=_names(missing)))
    busy = _busy(view, time.time())
    if busy:
        raise HTTPException(409, i18n.t("err.update.busy", pi=busy.upper()))


def _start(sha: str, confirm: bool, actor: str) -> dict:
    up = _get("update_upload")
    if up.get("sha") != sha:
        raise HTTPException(404, i18n.t("err.update.no_upload"))
    view = cluster_view()
    _check_all_there(view)
    plan = {"kind": "install", "version": up["version"], "sha": sha, "size": up.get("size"), "signer": up.get("signer")}
    due = [n for n in view["members"] if is_due(plan, view["infos"].get(n) or {})]
    if not due:
        raise HTTPException(409, i18n.t("err.update.nothing", version=up["version"]))
    old = [n for n in due if able(plan, view["infos"].get(n) or {}) == "old"]
    if old:
        raise HTTPException(409, i18n.t("err.update.pi_old", pis=_names(old)))
    nokey = [n for n in due if able(plan, view["infos"].get(n) or {}) == "key"]
    if nokey:
        raise HTTPException(409, i18n.t("err.update.pi_key", pis=_names(nokey)))
    warnings = _warnings()
    if warnings and not confirm:
        return {"started": False, "warnings": warnings}
    plan.update(id=f"{int(time.time())}-{secrets.token_hex(3)}", source=up.get("node"), by=actor, at=int(time.time()),
                cancelled=None)
    _put("update_plan", plan)
    lb.audit(rt.store, actor, "update_start", version=plan["version"], pis=_names(due))
    return {"started": True, "plan": plan}


@router.post("/start")
async def start_plan(s: StartIn, request: Request):
    return await db(_start, s.sha, s.confirm, _actor(request))


def _rollback(confirm: bool, actor: str) -> dict:
    view = cluster_view()
    _check_all_there(view)
    opt = _rollback_option(view)
    if not opt:
        raise HTTPException(409, i18n.t("err.update.rollback_mixed"))
    plan = {"kind": "rollback", "from": opt["from"], "to": opt["to"]}
    old = [n for n in view["members"] if able(plan, view["infos"].get(n) or {}) == "old"]
    if old:
        raise HTTPException(409, i18n.t("err.update.pi_old", pis=_names(old)))
    warnings = _warnings()
    if warnings and not confirm:
        return {"started": False, "warnings": warnings}
    plan.update(id=f"{int(time.time())}-{secrets.token_hex(3)}", source=Config.node, by=actor, at=int(time.time()),
                cancelled=None)
    _put("update_plan", plan)
    lb.audit(rt.store, actor, "update_rollback", version=opt["from"], to=opt["to"])
    return {"started": True, "plan": plan}


@router.post("/rollback")
async def rollback_plan(c: ConfirmIn, request: Request):
    return await db(_rollback, c.confirm, _actor(request))


def _cancel(actor: str) -> dict:
    plan = _get("update_plan")
    if not plan.get("id") or plan.get("cancelled"):
        raise HTTPException(409, i18n.t("err.update.no_plan"))
    plan["cancelled"] = int(time.time())
    _put("update_plan", plan)
    lb.audit(rt.store, actor, "update_cancel", version=goal(plan))
    return {"ok": True}


@router.post("/cancel")
async def cancel_plan(request: Request):
    return await db(_cancel, _actor(request))


def _retry(actor: str) -> dict:
    plan = _get("update_plan")
    if not plan.get("id") or plan.get("cancelled"):
        raise HTTPException(409, i18n.t("err.update.no_plan"))
    view = cluster_view()
    failed = [n for n, s in view["statuses"].items() if s.get("plan") == plan["id"] and s.get("state") in ("failed", "wait")]
    for n in failed:
        _set_status(n, {**view["statuses"][n], "state": "retry", "at": int(time.time())})
    lb.audit(rt.store, actor, "update_retry", version=goal(plan), pis=_names(failed))
    return {"ok": True, "pis": failed}


@router.post("/retry")
async def retry_plan(request: Request):
    return await db(_retry, _actor(request))


# -- Für die anderen Pi: Paket abholen ----------------------------------------------------------------------------
fetch_throttle = Throttle(10, 300)


@internal.get("/api/internal/update/{name}")
async def internal_bundle(name: str, request: Request):
    """Ein anderer Pi holt das Paket (nur mit CLUSTER_SECRET; gegen Raten gesperrt wie bei der Uhrzeit)."""
    ip = request.client.host if request.client else "?"
    fetch_throttle.check(ip)
    try:
        given = request.headers.get("X-Festpos-Secret", "").encode("latin-1")
    except UnicodeEncodeError:
        given = b""
    if not timesync.secret_usable(Config.cluster_secret) or not secrets.compare_digest(given, Config.cluster_secret.encode()):
        fetch_throttle.fail(ip)
        raise HTTPException(403)
    fetch_throttle.ok(ip)
    if not re.fullmatch(r"[0-9a-f]{64}\.(zip|sig)", name) or not (UPDATE_DIR / name).is_file():
        raise HTTPException(404)
    return FileResponse(UPDATE_DIR / name, media_type="application/octet-stream")
