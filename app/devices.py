"""Gerätezertifikate der Bestell-Handys: prüfen, sperren, erneuern, ausstellen (Handy- und Verwaltungs-Routen)."""
from __future__ import annotations

import datetime as dt
import json
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import auth, certs, device_ids, i18n, ledger_base as lb, netz, qr, timesync
from .core import APP_VERSION, Config, Throttle, admin, current_user, db, log, rt

router = APIRouter()
admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])


def via_phone(request: Request) -> bool:
    """True, wenn die Anfrage über den Handy-Port kam – dort hat TLS das Gerätezertifikat bereits geprüft."""
    return not Config.phone_port or request.scope.get("server", (None, 0))[1] == Config.phone_port


async def phone_only(request: Request) -> None:
    if not via_phone(request):
        raise HTTPException(403, i18n.t("err.device.phone_only"))
    info = client_cert(request)
    if info and info["serial"] in await db(revoked_serials):
        raise HTTPException(403, await db(revoked_message, info["serial"]))
    if info:
        await db(retire_replaced, info["serial"])


def revoked_message(serial: str) -> str:
    if rt.store.scalar("SELECT 1 FROM device_certs WHERE replaces=?", (serial,)):
        return i18n.t("err.device.replaced")
    return i18n.t("err.device.revoked")


_retired: set[str] = set()          # Zertifikate, deren Vorgänger schon gesperrt ist (oder die keinen haben)


def retire_replaced(serial: str) -> None:
    """Das erste Mal mit einem ERNEUERTEN Zertifikat: das alte wird gesperrt. Sonst hätte ein verlorenes
    Handy zwei gültige Zertifikate (REV-028). Erst jetzt, weil das neue sicher installiert ist."""
    if serial in _retired:
        return
    # die ganze Kette davor (T3): wurde ein Zertifikat ersetzt, bevor es je benutzt wurde (z. B. eine Erneuerung, die
    # Fully nicht laden konnte), gälte sonst das ursprüngliche weiter
    chain, cur = [], serial
    while len(chain) < 50:
        cur = rt.store.scalar("SELECT replaces FROM device_certs WHERE serial=?", (cur,))
        if not cur or cur in chain:
            break
        chain.append(cur)
    if chain:
        n = rt.store.execute(f"UPDATE device_certs SET revoked_at=? WHERE serial IN ({','.join('?' * len(chain))}) "
                             "AND revoked_at IS NULL", (time.strftime("%Y-%m-%dT%H:%M:%S"), *chain)).rows_affected
        if n:
            log.info("Gerätezertifikat %s durch Erneuerung ersetzt und gesperrt", chain[0][:8])
            notify_revoked()
    if len(_retired) > 10000:
        _retired.clear()
    _retired.add(serial)


_revoked_cache: tuple[float, set[str]] = (0.0, set())


def revoked_serials() -> set[str]:
    """Gesperrte Zertifikate (Seriennummern), 15 s zwischengespeichert. Nach einer Sperre auf einem anderen Pi meldet
    der sich (notify_revoked) – die 15 s sind nur der Rückfall, wenn das nicht ankommt."""
    global _revoked_cache
    if time.monotonic() - _revoked_cache[0] > 15:
        rows = rt.store.query("SELECT serial FROM device_certs WHERE revoked_at IS NOT NULL")
        _revoked_cache = (time.monotonic(), {r["serial"] for r in rows})
    return _revoked_cache[1]


def forget_retired() -> None:
    """Nach dem Einspielen einer Sicherung: Erneuerungsketten neu prüfen (sonst wirkte eine wiederhergestellte
    Sperre erst nach einem Neustart)."""
    _retired.clear()


def forget_revoked() -> None:
    """Sperrliste beim nächsten Gebrauch neu lesen."""
    global _revoked_cache
    _revoked_cache = (0.0, set())


def notify_revoked() -> None:
    """Sperrliste geändert: hier sofort neu lesen, den anderen Pi im Hintergrund Bescheid geben (T3). Geschützt mit
    CLUSTER_SECRET und nur an ein geprüftes Zertifikat (wie die Uhrzeit, timesync.broadcast)."""
    import threading
    forget_revoked()
    peers = [b for b in Config.peers if not Config.node_ip or urlsplit(b).hostname != Config.node_ip]
    if not peers or not timesync.secret_usable(Config.cluster_secret) or not Config.cafile:
        return

    def send():
        import ssl
        import urllib.request
        ctx = ssl.create_default_context(cafile=Config.cafile)
        for base in peers:
            req = urllib.request.Request(f"{base.rstrip('/')}/api/internal/revoked", data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "X-Festpos-Secret": Config.cluster_secret.encode().decode("latin-1")})
            try:
                with urllib.request.urlopen(req, timeout=3, context=ctx):
                    pass
            except Exception as e:  # noqa: BLE001 – Pi aus: er liest die Sperrliste spätestens nach 15 s selbst
                log.info("Sperrliste: %s nicht erreicht (%s)", base, e)
    threading.Thread(target=send, name="revoked-notify", daemon=True).start()


revoked_throttle = Throttle()


@router.post("/api/internal/revoked")
async def internal_revoked(request: Request):
    """Ein anderer Pi hat gesperrt oder entsperrt: Sperrliste sofort neu lesen (notify_revoked)."""
    ip = request.client.host if request.client else "?"
    revoked_throttle.check(ip)
    given = request.headers.get("X-Festpos-Secret", "").encode("latin-1")
    if not timesync.secret_usable(Config.cluster_secret) or \
            not secrets.compare_digest(given, Config.cluster_secret.encode()):
        revoked_throttle.fail(ip)
        raise HTTPException(403)
    revoked_throttle.ok(ip)
    forget_revoked()
    return {"ok": True}


# Mit gesperrtem Zertifikat bleibt am Handy-Port nur das offen: der Zustand des Zertifikats (die App zeigt damit «Gerät
# gesperrt») und was eigenen Schutz hat – Verwaltung und Statusanzeige mit Anmeldung, die Pi untereinander, Zustand.
REVOKED_OPEN = ("/api/device/cert", "/api/admin/", "/api/internal/", "/api/health", "/api/device-cert/", "/api/status")


def revoke_checked(request: Request) -> bool:
    """Prüft diese Anfrage die Sperrliste? Alle Routen der App am Handy-Port (T3, main.revoked_gate) – auch neue."""
    path = request.url.path
    return path.startswith("/api/") and not path.startswith(REVOKED_OPEN) and via_phone(request)


downloads = certs.OneTimeDownloads()


def client_cert(request: Request) -> dict | None:
    """Das Gerätezertifikat dieser Verbindung (vom Handy-Port, siehe app/serve.py)."""
    der = (request.scope.get("extensions") or {}).get("festpos.client_cert")
    return certs.describe(der) if der else None


def cert_status(request: Request) -> dict | None:
    """auto (T3): erneuert der Pi dieses Zertifikat selbst? Dann zeigt die App keinen Hinweis – erst, wenn die
    Automatik nicht geht. fully: Handy mit Fully – erneuern geht dann nur über die Verwaltung (Fully liest das
    Zertifikat aus einer Datei, nicht aus dem Android-Speicher)."""
    info = client_cert(request)
    if not info:
        return None
    warn = int(rt.store.setting("cert_warn_days", "730") or 730)
    did = device_ids.device_id_of(rt.store, info["serial"]) or ""
    fully = any(d.get("cert") == did and d.get("fully_pin") for d in device_ids.phones_raw(rt.store)) if did else False
    return {**info, "warn_days": warn, "renew_due": info["days_left"] <= warn,
            "revoked": info["serial"] in revoked_serials(),
            "auto": bool(did) and renew_modes().get(did, {}).get("mode") == "auto", "fully": fully}


# -- Automatisch erneuern (T3) ---------------------------------------------------------------------------------
# Handys mit Fully erneuert der Pi selbst (fully.auto_renew), alle anderen von Hand. Der Zustand pro Gerät steht in der
# Einstellung cert_renew_state (über alle Pi gleich, ohne Geheimnisse):
#   {device_id: {"state": running|ok|retry|failed, "from": altes Zertifikat, "serial": neues, "at": Zeit, "day": Tag,
#                "tries": Versuche an diesem Tag, "step"/"detail": woran es scheiterte}}
RENEW_STATE = "cert_renew_state"
RENEW_TRIES = 2                     # Versuche pro Gerät und Tag


def renew_days() -> int:
    """Automatisch erneuern so viele Tage vor Ablauf; 0 = aus."""
    try:
        return max(0, int(rt.store.setting("cert_renew_days", "730") or 0))
    except ValueError:
        return 0


def renew_state() -> dict[str, dict]:
    try:
        v = json.loads(rt.store.setting(RENEW_STATE, "{}") or "{}")
    except ValueError:
        return {}
    return {k: x for k, x in v.items() if isinstance(x, dict)} if isinstance(v, dict) else {}


def save_renew_state(device_id: str, entry: dict | None) -> None:
    """Zustand eines Geräts setzen (None = vergessen); Einträge gelöschter Geräte fallen dabei weg."""
    state = renew_state()
    if entry is None:
        state.pop(device_id, None)
    else:
        state[device_id] = entry
    lins = device_ids.lineages(rt.store)
    state = {k: x for k, x in state.items() if k in lins and not lins[k]["removed"]}
    rt.store.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (RENEW_STATE, json.dumps(state)))


def auto_phones(items: list[dict] | None = None) -> dict[str, dict]:
    """Geräte-ID -> Handy-Eintrag, wenn der Pi das Zertifikat selbst erneuern kann: Fully eingerichtet (Fernverwaltung
    mit eigenem Zertifikat), Zertifikat verknüpft, im Einsatz."""
    items = device_ids.phones_raw(rt.store) if items is None else items
    return {d["cert"]: d for d in items if d.get("kind") == "phone" and d.get("cert") and d.get("fully_pin")
            and d.get("active") is not False}


def newest_valid(lin: dict, day: str | None = None) -> dict | None:
    """Das Zertifikat, das ein Gerät gerade benutzt (neuestes gültiges)."""
    return next((c for c in lin["certs"] if device_ids.cert_valid(c, day)), None)


def renew_threshold(cert: dict) -> int:
    """Ab so vielen Tagen vor Ablauf ist dieses Zertifikat fällig: Einstellung, aber höchstens die halbe Laufzeit –
    sonst wäre ein kurz gültiges gleich nach dem Ausstellen wieder fällig."""
    try:
        life = (dt.date.fromisoformat(cert["expires_at"]) - dt.date.fromisoformat(str(cert["created_at"])[:10])).days
    except (TypeError, ValueError):
        return 0
    return min(renew_days(), life // 2)


def days_left(cert: dict) -> int | None:
    try:
        return (dt.date.fromisoformat(cert["expires_at"]) - certs.utc_today()).days
    except (TypeError, ValueError):
        return None


def renew_modes() -> dict[str, dict]:
    """Pro gültigem Gerät: wie es erneuert wird -> {mode: auto|manual|failed, state…}. failed: die Automatik gibt für
    das aktuelle Zertifikat auf (heute alle Versuche gescheitert) – dann wieder von Hand, wie ohne Fully."""
    lins = device_ids.lineages(rt.store)
    auto = auto_phones() if renew_days() else {}
    state = renew_state()
    out: dict[str, dict] = {}
    for k, x in lins.items():
        cur = None if x["removed"] else newest_valid(x)
        if not cur:
            continue
        st = state.get(k) or {}
        if st.get("from") != cur["serial"] and st.get("state") != "running":
            st = {}                                     # gehört zu einem früheren Zertifikat
        mode = "failed" if st.get("state") == "failed" else "auto" if k in auto else "manual"
        out[k] = {"mode": mode, **{f: st[f] for f in ("state", "at", "step", "detail", "tries") if f in st}}
    return out


def expiring_manual() -> int:
    """Geräte, deren Zertifikat bald abläuft und die jemand von Hand erneuern muss (Übersicht, T3): gesperrte zählen nicht,
    ebenso Fully-Handys, solange der Pi sie selbst erneuert."""
    warn = int(rt.store.setting("cert_warn_days", "730") or 730)
    modes = renew_modes()
    n = 0
    for k, x in device_ids.lineages(rt.store).items():
        live = [c for c in x["certs"] if not c["revoked_at"] and not c["removed_at"]]   # abgelaufene zählen mit
        left = days_left(live[0]) if live else None
        if left is not None and left <= warn and modes.get(k, {}).get("mode") != "auto":
            n += 1
    return n


def ascii_name(name: str) -> str:
    """Dateiname nur aus a–z, 0–9 und «-»: Zeichen ausserhalb Latin-1 im Namen führten sonst beim Download
    zu Fehler 500 – und der Einmal-Link war verbraucht (REV-039)."""
    import re
    import unicodedata
    t = name.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:40] or "geraet"


def issue_p12(name: str, replaces: str | None = None, password: str | None = None, device_id: str | None = None) -> dict:
    """Neues Gerätezertifikat ausstellen und eintragen -> {p12, password, filename, serial, expires_at, device_id}.
    Gerät (T1): device_id angegeben oder vom Vorgänger (replaces) geerbt, sonst ein neues Gerät (eigene Seriennummer)."""
    if not (Config.device_ca and Config.device_ca_key and Path(Config.device_ca_key).exists()):
        raise HTTPException(503, i18n.t("err.device.no_ca"))
    days = int(rt.store.setting("cert_days", str(certs.DEFAULT_DAYS)) or certs.DEFAULT_DAYS)
    ca_cert, ca_key = certs.load_ca(Config.device_ca, Config.device_ca_key)
    ca_left = (ca_cert.not_valid_after_utc.date() - certs.utc_today()).days - 1
    days = max(1, min(days, ca_left))                  # nie länger als die Geräte-CA
    p12, password, serial, until = certs.issue(ca_cert, ca_key, name, days, password)
    did = device_id or (device_ids.device_id_of(rt.store, replaces) if replaces else None) or serial
    rt.store.execute("INSERT INTO device_certs(name, serial, created_at, expires_at, replaces, device_id) VALUES (?,?,?,?,?,?)",
                  (name, serial, time.strftime("%Y-%m-%dT%H:%M:%S"), until.strftime("%Y-%m-%d"), replaces, did))
    return {"p12": p12, "password": password, "filename": f"festpos-{ascii_name(name)}-{until:%Y%m}.p12",
            "serial": serial, "expires_at": until.strftime("%Y-%m-%d"), "device_id": did}


def issue_cert(name: str, replaces: str | None = None, device_id: str | None = None) -> dict:
    r = issue_p12(name, replaces, device_id=device_id)
    token = downloads.put(r.pop("p12"), r["filename"])
    return {**r, "token": token}


def current_serial(device_id: str | None) -> str | None:
    """Neuestes, nicht gesperrtes Zertifikat dieses Geräts – das ersetzt ein neues (Erneuern)."""
    lin = device_ids.lineages(rt.store).get(device_id or "")
    return next((c["serial"] for c in lin["certs"] if not c["revoked_at"] and not c["removed_at"]), None) if lin else None


def device_by_name(name: str) -> str | None:
    """Alte Form (nur Name, ohne Geräte-ID): das gültige Gerät mit diesem Namen, sonst das neueste nicht gelöschte."""
    n, lins = device_ids.norm(name), device_ids.lineages(rt.store)
    hits = [k for k, x in lins.items() if n in x["valid_names"]] or \
        [k for k, x in sorted(lins.items(), key=lambda kv: kv[1]["certs"][0]["id"], reverse=True)
         if not x["removed"] and device_ids.norm(x["name"]) == n][:1]
    return hits[0] if len(hits) == 1 else None


def check_name(name: str, own_cert: str | None = None, own_phone: str | None = None) -> None:
    """409, wenn ein anderes gültiges Gerät schon so heisst (T1)."""
    other = device_ids.name_conflict(rt.store, name, own_cert, own_phone)
    if other:
        raise HTTPException(409, i18n.t("err.device.name_taken", name=other))


_ids_cache: tuple[float, dict[str, tuple[str, str]]] = (0.0, {})


def serial_map() -> dict[str, tuple[str, str]]:
    """Seriennummer -> (device_id, Gerätename) für die Statusanzeige, 15 s zwischengespeichert."""
    global _ids_cache
    if time.monotonic() - _ids_cache[0] > 15:
        m = {c["serial"]: (k, x["name"]) for k, x in device_ids.lineages(rt.store).items() for c in x["certs"]}
        _ids_cache = (time.monotonic(), m)
    return _ids_cache[1]


def set_role(device_id: str, role: str) -> list[tuple[str, tuple]]:
    """Rolle eines Geräts mit Zertifikat; device_roles (nach Namen) für ältere Versionen mitschreiben."""
    lin = device_ids.lineages(rt.store).get(device_id)
    out = [("INSERT INTO device_role_ids(device_id, role) VALUES (?,?) ON CONFLICT(device_id) DO UPDATE SET role=excluded.role",
            (device_id, role))]
    if lin:
        out.append(("INSERT INTO device_roles(name, role) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET role=excluded.role",
                    (lin["name"], role)))
    return out


def link_phone(phone_id: str, device_id: str) -> None:
    """Handy (monitor_devices) gehört zum Gerät mit diesem Zertifikat; seine Rolle geht ans Gerät über."""
    items = device_ids.phones_raw(rt.store)
    hit = next((d for d in items if d.get("kind") == "phone" and d.get("id") == phone_id), None)
    if not hit:
        return
    for d in items:                                     # ein Zertifikat gehört genau einem Handy
        if d is not hit and d.get("cert") == device_id:
            d.pop("cert")
    hit["cert"] = device_id
    stmts = []
    role = hit.pop("role", None)
    if role in lb.DEVICE_ROLES and not rt.store.scalar("SELECT 1 FROM device_role_ids WHERE device_id=?", (device_id,)):
        stmts += set_role(device_id, role)
    rt.store.batch([device_ids.save_phones(items), *stmts])


def drop_undelivered(serial: str, reached_phone: bool) -> None:
    """Ein über Fully ausgestelltes Zertifikat kam nicht an. Ging nichts hinaus, fällt es weg; kann die Datei
    (teilweise) auf dem Handy liegen, wird es gesperrt – ein Zertifikat ohne Eintrag liesse sich nie sperren."""
    if reached_phone:
        rt.store.execute("UPDATE device_certs SET revoked_at=? WHERE serial=? AND revoked_at IS NULL",
                         (time.strftime("%Y-%m-%dT%H:%M:%S"), serial))
        notify_revoked()
    else:
        rt.store.execute("DELETE FROM device_certs WHERE serial=?", (serial,))


@router.get("/api/device/cert")
async def device_cert_status(request: Request):
    """Zertifikat dieses Handys: Name, Ablauf, ob erneuert werden sollte."""
    st = await db(cert_status, request)
    if not st:
        raise HTTPException(404, i18n.t("err.device.no_cert"))
    return st


@router.post("/api/device/renew")
async def device_renew(request: Request):
    """Erneuern mit dem noch gültigen Zertifikat: gleicher Name, neue Gültigkeit."""
    info = client_cert(request)
    if not info:
        raise HTTPException(403, i18n.t("err.device.renew_needs_cert"))
    if info["serial"] in await db(revoked_serials):
        raise HTTPException(403, i18n.t("err.device.revoked"))
    r = await db(issue_cert, info["name"], info["serial"])
    log.info("Gerätezertifikat erneuert: %s (alt %s, neu %s bis %s)", info["name"], info["serial"][:8],
             r["serial"][:8], r["expires_at"])
    return {"password": r["password"], "url": f"/api/device/renew/{r['token']}", "filename": r["filename"],
            "expires_at": r["expires_at"]}


@router.get("/api/device/renew/{token}")
async def device_renew_download(token: str, request: Request):
    info = client_cert(request)
    if not info or info["serial"] in await db(revoked_serials):
        raise HTTPException(403)
    got = downloads.take(token)
    if not got:
        raise HTTPException(404, i18n.t("err.device.link_used"))
    data, fname = got
    return Response(data, media_type="application/x-pkcs12",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"', "Cache-Control": "no-store"})


@router.get("/api/device-cert/{token}")
async def device_cert_download(token: str):
    """Einmal-Download des Gerätezertifikats (Link aus dem QR-Code, 10 Minuten gültig)."""
    got = downloads.take(token)
    if not got:
        raise HTTPException(404, i18n.t("err.device.link_used_admin"))
    data, fname = got
    return Response(data, media_type="application/x-pkcs12",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"', "Cache-Control": "no-store"})


# -- Geräte (Zertifikate) ----------------------------------------------------
# Seite «Geräte» (T5): ab Festleitung ansehen, hinzufügen, sperren, Rolle und «Im Einsatz»; entsperren und löschen
# nur Admin (Regeln in auth.py, Entsperren hier). Jede Änderung steht im Protokoll.


def _actor(request: Request) -> str:
    return (current_user(request) or {}).get("name") or "?"


ID_PATTERN = r"^[0-9A-Za-z]{1,64}$"


class CertIn(BaseModel):
    name: str = Field(min_length=1, max_length=40, pattern=r"^[\w .\-äöüÄÖÜ]+$")
    renew: bool = False         # neues Zertifikat für ein bestehendes Gerät: das alte sperrt der Server beim ersten Gebrauch
    device: str | None = Field(default=None, pattern=ID_PATTERN)      # dieses Gerät erneuern/umbenennen (T1)
    phone: str | None = Field(default=None, pattern=r"^p[0-9a-f]{12}$")  # Handy ohne Zertifikat: gehört dazu
    role: str | None = Field(default=None, pattern="^(" + "|".join(lb.DEVICE_ROLES) + ")$")


def _list() -> dict:
    from . import test_mode
    lins = device_ids.lineages(rt.store)
    ids = {c["id"]: k for k, x in lins.items() for c in x["certs"]}
    certs_ = rt.store.query("SELECT id, name, serial, created_at, expires_at, revoked_at, "
                            "CAST(julianday(expires_at) - julianday(date('now')) AS INTEGER) AS days_left "
                            "FROM device_certs WHERE removed_at IS NULL ORDER BY expires_at DESC, id DESC")   # pro Gerät neuestes zuerst
    for c in certs_:
        c["device_id"] = ids.get(c["id"])
    by_serial = {c["serial"]: k for k, x in lins.items() for c in x["certs"]}
    seen: dict[str, int] = {}
    seen_ip: dict[str, int] = {}
    for d in rt.monitor.device_list():                   # letzte Meldung der Bestell-App – pro Gerät, nie nach Namen
        k = by_serial.get(d.get("serial") or "")
        if k:
            seen[k] = min(seen.get(k, d["seconds"]), d["seconds"])
        if d.get("ip"):
            seen_ip[d["ip"]] = min(seen_ip.get(d["ip"], d["seconds"]), d["seconds"])
    return {"enabled": bool(Config.phone_port), "last_seen": seen, "last_seen_ip": seen_ip,
            "ip_now": {k: ip for k, (_, ip) in _now_ips().items()},          # Adresse, die das Gerät gerade hat
            "warn_days": int(rt.store.setting("cert_warn_days", "730") or 730),
            "renew_days": renew_days(), "renew": renew_modes(),
            "mac": mac_view(), "mac_minutes": _mac().minutes(),                # MAC-Wechsel (T4)
            "roles": {r["device_id"]: r["role"] for r in rt.store.query("SELECT device_id, role FROM device_role_ids")},
            "test": test_mode.active(),                                        # gerade im Testmodus (T7)
            "pins": sorted(own_pins()),                                        # Geräte mit eigener PIN (T14)
            "pin_common": rt.store.setting("pin_enabled", "0") == "1" and bool(rt.store.setting("pin_hash")),
            "certs": certs_}


@admin_router.get("/devices")
async def list_devices():
    return await db(_list)


@admin_router.post("/devices/cert")
async def issue_device_cert(c: CertIn, request: Request):
    name = c.name.strip()
    dev = c.device or (await db(device_by_name, name) if c.renew else None)
    if c.device and (await db(device_ids.lineages, rt.store)).get(c.device, {"removed": True})["removed"]:
        raise HTTPException(404, i18n.t("err.device.not_found"))
    await db(check_name, name, dev, c.phone)
    r = await db(issue_cert, name, await db(current_serial, dev) if dev else None, dev)
    if c.phone:
        await db(link_phone, c.phone, r["device_id"])
    else:
        await db(device_ids.relink, rt.store)
    if c.role and c.role != "service":
        await db(rt.store.batch, set_role(r["device_id"], c.role))
    await db(lb.audit, rt.store, _actor(request), "device_cert", name=name, expires_at=r["expires_at"], renew=bool(dev))
    host = (request.headers.get("host") or netz.vip()).split(":")[0]
    port = f":{Config.admin_port}" if Config.admin_port and Config.admin_port != 443 else ""
    url = f"https://{host}{port}/api/device-cert/{r['token']}"
    return {"password": r["password"], "url": url, "svg": qr.qr_svg(url), "filename": r["filename"],
            "serial": r["serial"], "expires_at": r["expires_at"], "device_id": r["device_id"]}


@admin_router.post("/devices/{did}/revoke")
async def revoke_device(request: Request, did: int, undo: bool = False):
    """Gerät sperren – ALLE Zertifikate mit diesem Namen, auch ein erneuertes (REV-028). undo=true gibt die
    mit derselben Sperre gesperrten wieder frei (nur Admin). Wirkt spätestens nach 15 Sekunden."""
    if undo and not auth.has(current_user(request), auth.ADMIN_PERM):
        raise HTTPException(403, i18n.t("err.login.forbidden"))
    row = await db(rt.store.query_one, "SELECT name, revoked_at, removed_at FROM device_certs WHERE id=?", (did,))
    if not row or (undo and row["removed_at"]):          # endgültig gelöscht: Sperre lässt sich nicht mehr aufheben
        raise HTTPException(404, i18n.t("err.device.not_found"))
    lin = await db(_lineage_of_row, did)
    ids = ",".join(str(c["id"]) for c in lin["certs"])  # nur Zahlen aus der Datenbank
    if undo:
        # Name inzwischen von einem anderen Gerät belegt (gesperrte zählen nicht, T1): nicht entsperren
        other = await db(device_ids.name_conflict, rt.store, lin["name"], lin["device_id"])
        if other:
            raise HTTPException(409, i18n.t("err.device.unrevoke_name_taken", name=other))
        res = await db(rt.store.execute, f"UPDATE device_certs SET revoked_at=NULL WHERE id IN ({ids}) AND revoked_at=? "
                                         "AND removed_at IS NULL", (row["revoked_at"],))
    else:
        res = await db(rt.store.execute, f"UPDATE device_certs SET revoked_at=? WHERE id IN ({ids}) AND revoked_at IS NULL",
                       (time.strftime("%Y-%m-%dT%H:%M:%S"),))
    notify_revoked()                                     # hier sofort, die anderen Pi auf Zuruf (T3)
    await db(device_ids.relink, rt.store)
    await db(lb.audit, rt.store, _actor(request), "device_unrevoke" if undo else "device_revoke", name=row["name"])
    return {"ok": True, "changed": res.rows_affected}


def _lineage_of_row(row_id: int) -> dict:
    """Alle Zertifikate des Geräts, zu dem diese Zeile gehört (T1: über die Geräte-ID, nicht über den Namen)."""
    lins = device_ids.lineages(rt.store)
    return next(x for x in lins.values() if any(c["id"] == row_id for c in x["certs"]))


@admin_router.post("/devices/{did}/remove")
async def remove_device(did: int, request: Request):
    """Gerät endgültig löschen (nur Admin): nur wenn kein Zertifikat dieses Namens mehr gilt (alle gesperrt oder
    abgelaufen). Die Zeilen bleiben mit removed_at – so bleibt die Sperre wirksam (revoked_serials), das Gerät
    erscheint aber nirgends mehr. Die Rolle fällt weg, ebenso ein Handy-Eintrag (feste IP, MAC, Fully-Zugang), der
    beim Sperren fürs Entsperren stehen blieb (T3)."""
    row = await db(rt.store.query_one, "SELECT name FROM device_certs WHERE id=? AND removed_at IS NULL", (did,))
    if not row:
        raise HTTPException(404, i18n.t("err.device.not_found"))
    lin = await db(_lineage_of_row, did)
    if lin["valid"]:
        raise HTTPException(409, i18n.t("err.device.remove_valid", name=row["name"]))
    ids = ",".join(str(c["id"]) for c in lin["certs"])
    stmts = [(f"UPDATE device_certs SET removed_at=? WHERE id IN ({ids}) AND removed_at IS NULL", (time.strftime("%Y-%m-%dT%H:%M:%S"),)),
             ("DELETE FROM device_role_ids WHERE device_id=?", (lin["device_id"],)),
             ("DELETE FROM settings WHERE key=?", (PIN_KEY + lin["device_id"],)),      # eigene PIN (T14)
             lb.audit_stmt(_actor(request), "device_remove", name=row["name"])]
    items = await db(device_ids.phones_raw, rt.store)
    gone = [d for d in items if d.get("kind") == "phone" and d.get("cert") == lin["device_id"]]
    if gone:
        stmts.append(device_ids.save_phones([d for d in items if d not in gone]))
        for d in gone:
            rt.monitor.fully_forget(str(d.get("ip") or ""))
    others = await db(device_ids.lineages, rt.store)        # alte Rolle nach Namen nur, wenn kein anderes Gerät so heisst
    if not any(k != lin["device_id"] and not x["removed"] and device_ids.norm(x["name"]) == device_ids.norm(lin["name"])
               for k, x in others.items()):
        stmts.insert(2, ("DELETE FROM device_roles WHERE name=?", (lin["name"],)))
    await db(rt.store.batch, stmts)
    await db(device_ids.relink, rt.store)
    return {"ok": True}


class PhoneIn(BaseModel):
    id: str | None = Field(default=None, pattern=r"^p[0-9a-f]{12}$")   # dieses Handy ändern (leer = neu eintragen)
    old: str | None = Field(default=None, max_length=40)       # alte Form: bisheriger Name statt id
    name: str = Field(min_length=1, max_length=30, pattern=r"^[\w .\-äöüÄÖÜ]+$")
    ip: str = Field(default="", max_length=63)                 # leer = von der Statusanzeige nehmen
    active: bool = True
    cert: str | None = Field(default=None, pattern=ID_PATTERN)  # neu für ein Gerät mit Zertifikat («Nur Zugang» + IP)
    role: str | None = Field(default=None, pattern="^(" + "|".join(lb.DEVICE_ROLES) + ")$")   # neu: Rolle
    here: bool = False          # ip ist die Adresse, die das Handy GERADE hat («Fully auf vorhandenem Handy»): MAC von dort


def save_phone(p: PhoneIn) -> dict:
    """Ein Handy in «Handys auf der Statusanzeige» (monitor_devices) eintragen oder ändern – nur diesen Eintrag;
    Fully-Zertifikat und MAC (feste DHCP-Zuteilung) bleiben. IP unter den Handys eindeutig, der Name unter allen
    gültigen Geräten (T1)."""
    from . import dhcp as dhcp_mod, monitor as monitor_mod
    items = device_ids.phones_raw(rt.store)
    lins = device_ids.lineages(rt.store)
    device_ids.normalize(items, lins)
    old = (p.old or "").strip().lower()
    phones = [d for d in items if d.get("kind") == "phone"]
    hit = next((d for d in phones if d.get("id") == p.id), None) if p.id else \
        next((d for d in phones if str(d.get("name") or "").lower() == old), None) if old else None
    if (p.id or old) and not hit:
        raise HTTPException(404, i18n.t("err.fully.not_listed"))
    cert = hit.get("cert") if hit else (p.cert if p.cert in lins and not lins[p.cert]["removed"] else None)
    if not p.ip.strip() and hit:                           # IP leer: von der Statusanzeige nehmen (mit Fully-Zugang und MAC)
        items.remove(hit)
        _save_items(items)
        return {"name": hit.get("name"), "ip": "", "active": False}
    if not monitor_mod.HOST_RE.match(p.ip):
        raise HTTPException(422, i18n.t("err.device.bad_ip", name=p.name))
    other = device_ids.name_conflict(rt.store, p.name, cert, hit.get("id") if hit else None, items, lins)
    if other:
        raise HTTPException(409, i18n.t("err.device.name_taken", name=other))
    for d in items:
        if d is not hit and d.get("ip") == p.ip:
            raise HTTPException(409, i18n.t("err.device.ip_taken", ip=p.ip, name=d.get("name") or ""))
    if hit:
        if hit.get("ip") != p.ip:                              # neue IP: das Fully-Zertifikat gilt für die alte
            hit.pop("fully_pin", None)
            hit.pop("fully_until", None)
        hit.update(name=p.name.strip(), ip=p.ip, active=p.active)
    else:
        new = {"id": device_ids.new_phone_id(), "name": p.name.strip(), "kind": "phone", "ip": p.ip, "port": None,
               "active": p.active}
        if cert:
            for x in items:                                 # ein Zertifikat gehört genau einem Handy
                if x.get("cert") == cert:
                    x.pop("cert")
            new["cert"] = cert
        elif p.role and p.role != "service":            # noch ohne Zertifikat: Rolle wartet beim Handy (link_phone)
            new["role"] = p.role
        items.insert(0, new)
    d = hit or items[0]
    # Handy ist schon im WLAN und hat genau diese Adresse: seine MAC -> feste Zuteilung (DHCP, dhcp.py). Sonst nie über
    # die feste IP – die kann gerade ein anderes Gerät aus dem Pool haben; die MAC lernt dann learn_macs.
    if not d.get("mac") and p.here:
        m = dhcp_mod.mac_for_ip(p.ip)
        if m and not any(x.get("mac") == m for x in items if x is not d):
            d["mac"] = m
    _save_items(items)
    return d


def _save_items(items: list[dict]) -> None:
    rt.store.execute(*device_ids.save_phones(items))


def _now_ips() -> dict[str, tuple[int, str]]:
    """Gerät (device_id) -> (Sekunden, IP) der letzten Meldung der Bestell-App mit Zertifikat (dieser Pi)."""
    ids = serial_map()
    out: dict[str, tuple[int, str]] = {}
    for e in rt.monitor.device_list():
        did = ids.get(e.get("serial") or "", (None, None))[0]
        if did and e.get("ip") and (did not in out or e["seconds"] < out[did][0]):
            out[did] = (e["seconds"], e["ip"])
    return out


def learn_macs() -> None:
    """Feste IP ohne MAC = noch keine Reservierung im DHCP. Meldet sich das Handy mit seinem Gerätezertifikat, kommt
    die MAC aus der ARP-Tabelle – von der Adresse, die es GERADE hat, nicht von der festen (Monitor, alle 10 s).
    Gehört die MAC schon einem anderen Eintrag, bleibt es. Hat der Eintrag schon eine MAC: mac_change (T4)."""
    from . import dhcp as dhcp_mod, monitor as monitor_mod
    items = device_ids.phones_raw(rt.store)
    want = {d["cert"]: d for d in items if d.get("kind") == "phone" and d.get("cert") and not d.get("mac")}
    if not want:
        return
    changed = False
    for did, (sec, ip) in _now_ips().items():
        if did not in want or sec >= monitor_mod.ONLINE_SECONDS:
            continue
        m = dhcp_mod.mac_for_ip(ip)
        if m and not any(x.get("mac") == m for x in items):
            want[did]["mac"] = m
            changed = True
            log.info("MAC %s für %s übernommen (feste IP %s, jetzt %s)", m, want[did].get("name"), want[did].get("ip"), ip)
    if changed:
        _save_items(items)


def _mac():
    from . import mac_change                            # erst hier: mac_change braucht dieses Modul
    return mac_change


def mac_view() -> dict[str, dict]:
    """MAC-Wechsel pro Gerät (T4) mit Zustand und «zuletzt gesehen»; dazu, ob die reservierte MAC zufällig ist."""
    from . import dhcp as dhcp_mod
    out = _mac().view()
    for d in device_ids.phones_raw(rt.store):
        if d.get("kind") == "phone" and d.get("cert") and dhcp_mod.is_random(d.get("mac")):
            out.setdefault(d["cert"], {})["random"] = True
    return out


def mac_alarm_names() -> list[dict]:
    """Übersicht: Geräte mit Alarm «zwei Geräte mit demselben Zertifikat» (T4) -> [{device, name}]."""
    lins = device_ids.lineages(rt.store)
    return sorted(({"device": k, "name": lins[k]["name"]} for k in _mac().alarms() if k in lins),
                  key=lambda x: (x["name"].casefold(), x["device"]))


class MacIn(BaseModel):
    device: str = Field(pattern=ID_PATTERN)
    action: str = Field(pattern="^(take|keep)$")      # take: neue MAC übernehmen; keep: reservierte behalten
    mac: str = Field(max_length=17)                   # die neue MAC, über die entschieden wird (so wie angezeigt)


@admin_router.post("/devices/mac")
async def decide_mac(m: MacIn, request: Request):
    """MAC-Wechsel von Hand entscheiden (T4, ab Festleitung wie «Sperren»): übernehmen oder alte behalten."""
    mc = _mac()
    try:
        if m.action == "take":
            await db(mc.take, m.device, m.mac, "manual", _actor(request))
        else:
            await db(mc.keep, m.device, m.mac, _actor(request))
    except mc.Refused as e:
        if e.code == "taken":
            raise HTTPException(409, i18n.t("err.device.mac_taken", name=e.name)) from None
        raise HTTPException(409, i18n.t("err.device.mac_gone")) from None
    return {"ok": True}


@admin_router.put("/devices/phone")
async def put_phone(p: PhoneIn, request: Request):
    d = await db(save_phone, p)
    if p.role and p.role != "service" and not p.id and d.get("cert"):
        await db(rt.store.batch, set_role(d["cert"], p.role))
    await db(lb.audit, rt.store, _actor(request), "device_phone", old=p.old, name=d["name"], ip=d["ip"], active=d["active"])
    return {"ok": True, "id": d.get("id"), "name": d["name"], "ip": d["ip"], "active": d["active"]}


@admin_router.get("/devices/wifi-qr")
async def wifi_qr(response: Response):
    """QR zum Beitritt ins FestPOS-WLAN (WLAN beim Einrichten, Einstellungen › Geräte) für «Fully auf vorhandenem
    Handy» und «Nur Zugang». Das Passwort steckt nur im Bild, nie als Text in der Antwort."""
    def read():
        s = rt.store.setting
        return (s("fully_prov_ssid", "") or "", s("fully_prov_security", "WPA") or "WPA", s("fully_prov_wifi_password", "") or "")
    ssid, sec, pw = await db(read)
    response.headers["Cache-Control"] = "no-store"
    if not ssid:
        return {"ssid": "", "svg": None}
    sec = sec if sec in ("WPA", "WEP") and pw else "nopass"

    def q(v: str) -> str:                                 # Sonderzeichen im WLAN-QR maskieren
        return "".join("\\" + c if c in '\\;,":' else c for c in v)
    text = f"WIFI:T:{sec};S:{q(ssid)};" + (f"P:{q(pw)};" if sec != "nopass" else "") + ";"
    return {"ssid": ssid, "security": sec, "svg": qr.qr_svg(text)}


@admin_router.get("/system/certs")
async def system_certs():
    """Ablaufdaten der Server-Zertifikate – bei Nutzung einmal im Jahr wichtig."""
    from cryptography import x509
    warn = int(await db(rt.store.setting, "cert_warn_days", "730") or 730)
    out = []
    for label, path in ((i18n.t("admin.certs.server"), Config.server_cert), (i18n.t("admin.certs.server_ca"), Config.cafile),
                        (i18n.t("admin.certs.device_ca"), Config.device_ca)):
        if not path or not Path(path).exists():
            continue
        try:
            c = x509.load_pem_x509_certificate(Path(path).read_bytes())
        except (OSError, ValueError):
            continue
        until = c.not_valid_after_utc.date()
        left = (until - certs.utc_today()).days
        item = {"label": label, "expires_at": until.isoformat(), "days_left": left, "warn": left <= warn}
        if path == Config.cafile:                      # Server-CA: Fingerabdruck + Namensbeschränkung (REV-026)
            from cryptography.hazmat.primitives import hashes
            item["fingerprint"] = ":".join(f"{b:02X}" for b in c.fingerprint(hashes.SHA256()))
            try:
                c.extensions.get_extension_for_class(x509.NameConstraints)
                item["constrained"] = True
            except x509.ExtensionNotFound:
                item["constrained"] = False
        out.append(item)
    return {"warn_days": warn, "certs": out, "version": APP_VERSION}


@admin_router.delete("/devices/{did}")
async def forget_device(did: int):
    """Aus der Liste entfernen – nur abgelaufene Zertifikate (die kommen ohnehin nicht mehr durch). Ein gültiges
    liesse sich ohne Eintrag nicht mehr sperren, und ein gesperrtes stünde nicht mehr auf der Sperrliste."""
    row = await db(rt.store.query_one, "SELECT id, revoked_at, expires_at FROM device_certs WHERE id=?", (did,))
    if not row:
        raise HTTPException(404, i18n.t("err.device.not_found"))
    if not row["expires_at"] or row["expires_at"] >= certs.utc_today().isoformat():
        raise HTTPException(409, i18n.t("err.device.keep_revoked" if row["revoked_at"] else "err.device.forget_valid"))
    await db(rt.store.execute, "DELETE FROM device_certs WHERE id=?", (did,))
    return {"ok": True}


# -- PIN pro Gerät (T14) -------------------------------------------------------------------------------------------
# Eigene PIN eines Geräts (Handy) statt der gemeinsamen (Einstellungen › Bestell-App › PIN-Sperre). Nur gesalzener
# Hash, in settings unter «device_pin.<Geräte-ID>» (wie die gemeinsame PIN; GET /settings gibt sie nie zurück).
# Ein Handy bekommt nur seinen eigenen Hash (main.pin_config) und prüft ihn auch ohne Verbindung.
PIN_KEY = "device_pin."


def own_pins() -> set[str]:
    return {r["key"][len(PIN_KEY):] for r in rt.store.query("SELECT key FROM settings WHERE substr(key, 1, ?) = ?",
                                                             (len(PIN_KEY), PIN_KEY))}


def own_pin(device_id: str | None) -> dict | None:
    if not device_id:
        return None
    try:
        d = json.loads(rt.store.setting(PIN_KEY + device_id) or "null")
        return {"salt": str(d["salt"]), "hash": str(d["hash"]), "len": int(d["len"])} if d else None
    except (ValueError, KeyError, TypeError):
        return None


class DevicePinIn(BaseModel):
    device: str = Field(min_length=1, max_length=80)
    pin: str | None = Field(default=None, pattern=r"^\d{4,8}$")


def _pin_device(did: str) -> dict:
    lin = device_ids.lineages(rt.store).get(did)
    if not lin or lin.get("removed"):
        raise HTTPException(404, i18n.t("err.device.not_found"))
    return lin


@admin_router.put("/devices/pin")
async def set_device_pin(b: DevicePinIn, request: Request):
    """Eigene PIN setzen bzw. ändern (nur Admin), ohne «pin»: entfernen – dann gilt wieder die gemeinsame PIN.
    Wirkt am Handy nach der nächsten Abfrage (≤ 20 s)."""
    lin = await db(_pin_device, b.device)
    if not b.pin:
        await db(rt.store.batch, [("DELETE FROM settings WHERE key=?", (PIN_KEY + b.device,)),
                                  lb.audit_stmt(_actor(request), "device_pin", name=lin["name"], how="removed")])
        return {"own": False}
    from .settings_admin import _pin_hash
    salt, h = _pin_hash(b.pin)
    value = json.dumps({"salt": salt, "hash": h, "len": len(b.pin)})
    await db(rt.store.batch, [("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET "
                               "value=excluded.value", (PIN_KEY + b.device, value)),
                              lb.audit_stmt(_actor(request), "device_pin", name=lin["name"], how="set")])
    return {"own": True}
