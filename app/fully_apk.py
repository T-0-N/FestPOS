"""Fully-APK auf allen Pi und auf die Handys verteilen (T6).

Die APK (Fully-Variante für Geräteinhaber, Paket com.fullykiosk.emm) wird auf einem Pi hochgeladen (fully.py,
POST /api/admin/fully/apk). Massgeblich ist danach die Einstellung fully_apk {checksum, size, name, at, package,
version_name, version_code, from}. Jeder Pi meldet seine eigene APK (Prüfsumme) mit seinen Infos (system.local_info,
über /api/internal/node an die anderen, T9); fehlt sie oder ist sie alt, holt er sie bei einem Pi, der die richtige
hat (GET /api/internal/fully/apk, nur mit CLUSTER_SECRET) – im Hintergrund, eine Übertragung auf einmal, Prüfsumme
nachgerechnet. In der Sicherung steht nur fully_apk; die APK legt festpos-sicherung daneben auf den Stick (T8).

Handys: loadApkFile (fully.setup_command) mit der Adresse eines Pi, der die richtige APK hat. Ohne Rückfrage installiert
Android nur, wenn Fully Geräteinhaber ist; Fully aus dem Play Store (Version «…-play», Paket de.ozerov.fully) ist eine
andere App und bekommt die APK nie. Erfolg gilt erst, wenn Fully die neue Version meldet. Stand der Handys (Version,
Art) in fully_versions, Stand der Verteilung in fully_apk_push – beides nur bei Änderung geschrieben (T9).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import ssl
import struct
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import device_ids, fully, i18n, ledger_base as lb, monitor as monitor_mod, timesync
from .core import Config, Throttle, admin, current_user, db, require_cluster_secret, rt

log = logging.getLogger("festpos.fully")

PACKAGE = "com.fullykiosk.emm"                # Fully für Geräteinhaber (Einrichtungs-QR: PROV_COMPONENT)
INFO_KEYS = ("checksum", "size", "name", "at", "package", "version_name", "version_code")
SYNC_RETRY = (60, 300, 900)                   # Sekunden bis zum nächsten Versuch nach 1, 2, ≥3 Fehlschlägen
SYNC_RESERVE = 50 * 1024 * 1024               # so viel muss nach dem Kopieren noch frei sein
PUSH_WAIT = 600.0                             # so lange darf Fully für Herunterladen und Installieren brauchen
PUSH_TRIES = 3                                # Versuche pro Handy und Tag, danach «von Hand»
MANIFEST_MAX = 2 * 1024 * 1024                # AndroidManifest.xml ist ein paar KB gross


# -- Version aus der APK (AndroidManifest.xml, Binärformat AXML) ---------------------------------------------
_ATTR_IDS = {0x0101021B: "versionCode", 0x0101021C: "versionName"}    # android:versionCode/-Name (Ressourcen-ID)


def _strings(data: bytes, off: int) -> list[str]:
    """String-Pool eines AXML-Chunks (UTF-8 oder UTF-16)."""
    _t, hsize, size, count, _styles, flags, start, _ = struct.unpack_from("<HHIIIIII", data, off)
    if count > 100_000 or off + size > len(data):
        raise ValueError("String-Pool")
    utf8 = bool(flags & 0x100)
    out = []
    for i in range(count):
        p = off + start + struct.unpack_from("<I", data, off + hsize + 4 * i)[0]
        if utf8:
            n = data[p]
            p += 2 if n & 0x80 else 1                      # Länge in UTF-16 (überspringen)
            n = data[p]
            if n & 0x80:
                n = ((n & 0x7F) << 8) | data[p + 1]
                p += 1
            out.append(data[p + 1:p + 1 + n].decode("utf-8", "replace"))
        else:
            n = struct.unpack_from("<H", data, p)[0]
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, p + 2)[0]
                p += 2
            out.append(data[p + 2:p + 2 + 2 * n].decode("utf-16-le", "replace"))
    return out


def parse_axml(data: bytes) -> dict:
    """package, versionName, versionCode aus dem ersten Element (<manifest>) -> {package, version_name, version_code}."""
    if len(data) < 8 or struct.unpack_from("<H", data, 0)[0] != 0x0003:
        raise ValueError("kein AXML")
    strings: list[str] = []
    res_ids: list[int] = []
    off = struct.unpack_from("<H", data, 2)[0]
    while off + 8 <= len(data):
        typ, hsize, size = struct.unpack_from("<HHI", data, off)
        if size < 8:
            raise ValueError("Chunk")
        if typ == 0x0001:
            strings = _strings(data, off)
        elif typ == 0x0180:
            res_ids = list(struct.unpack_from(f"<{(size - hsize) // 4}I", data, off + hsize))
        elif typ == 0x0102:                                # erstes Start-Element = <manifest>
            name_i = struct.unpack_from("<I", data, off + 20)[0]
            if name_i >= len(strings) or strings[name_i] != "manifest":
                raise ValueError("kein <manifest>")
            a_start, a_size, a_count = struct.unpack_from("<HHH", data, off + 24)
            out: dict = {}
            for k in range(min(a_count, 200)):
                a = off + 16 + a_start + k * a_size
                _ns, an, raw, _vs, _r, dtype, val = struct.unpack_from("<IIIHBBI", data, a)
                key = _ATTR_IDS.get(res_ids[an]) if an < len(res_ids) else None
                key = key or (strings[an] if an < len(strings) else "")
                value: str | int | None = None
                if dtype == 0x03 and val < len(strings):
                    value = strings[val]
                elif raw != 0xFFFFFFFF and raw < len(strings):
                    value = strings[raw]
                elif dtype in (0x10, 0x11):
                    value = val
                if key == "package" and isinstance(value, str):
                    out["package"] = value[:120]
                elif key == "versionName" and isinstance(value, str):
                    out["version_name"] = value[:40]
                elif key == "versionCode" and isinstance(value, int):
                    out["version_code"] = value
            return out
        off += size
    raise ValueError("kein Element")


NAME_VERSION_RE = re.compile(r"v?(\d+(?:\.\d+){1,3})(?:-([a-z]+))?", re.I)


def apk_manifest(path: Path, filename: str = "") -> dict:
    """Paket und Version der APK; geht das nicht (kaputt, ungewohnt): Version aus dem Dateinamen
    («Fully-Kiosk-Browser-v1.61.2-emm.apk»), sonst {}."""
    try:
        with zipfile.ZipFile(path) as z:
            info = z.getinfo("AndroidManifest.xml")
            if info.file_size > MANIFEST_MAX:
                raise ValueError("zu gross")
            out = parse_axml(z.read(info))
        if out.get("version_name") or out.get("version_code"):
            return out
    except (OSError, ValueError, KeyError, IndexError, struct.error, zipfile.BadZipFile, UnicodeDecodeError) as e:
        log.info("Fully-APK: Version nicht aus dem Manifest lesbar (%s)", type(e).__name__)
        out = {}
    m = NAME_VERSION_RE.search(filename or "")
    if m:
        out["version_name"] = m.group(0).lstrip("vV")
    return out


def version_key(name) -> tuple[int, ...] | None:
    """«1.61.2-emm» -> (1, 61, 2)"""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", str(name or ""))
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def version_cmp(have: dict, want: dict) -> int | None:
    """Version auf dem Handy (have) gegen die APK (want): -1 älter, 0 gleich, 1 neuer, None unbekannt. Mit versionCode,
    wenn beide einen haben, sonst die Zahlen von versionName."""
    a, b = have.get("version_code"), want.get("version_code")
    if isinstance(a, int) and isinstance(b, int):
        return (a > b) - (a < b)
    ka, kb = version_key(have.get("version_name")), version_key(want.get("version_name"))
    if not ka or not kb:
        return None
    n = max(len(ka), len(kb))
    ka, kb = ka + (0,) * (n - len(ka)), kb + (0,) * (n - len(kb))
    return (ka > kb) - (ka < kb)


def variant(data: dict) -> str | None:
    """Art von Fully auf dem Handy: «play» (Play Store, anderes Paket – bekommt die APK nie), «owner» (Geräteinhaber),
    «app» (Variante für Geräteinhaber, aber nicht Geräteinhaber: Android fragt am Handy) oder None (unbekannt)."""
    name = str(data.get("fully_version") or "").lower()
    if name.endswith("-play") or "-play" in name:
        return "play"
    if data.get("device_owner") is True:
        return "owner"
    if data.get("device_owner") is False:
        return "app"
    return None


# -- Massgebliche APK und die Datei auf diesem Pi ------------------------------------------------------------
def _setting(key: str) -> dict:
    try:
        v = json.loads(rt.store.setting(key) or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def _put(key: str, value: dict) -> None:
    rt.store.execute("INSERT INTO settings(key,value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value, ensure_ascii=False)))


def meta() -> dict | None:
    """Die zuletzt hochgeladene APK (für alle Pi) oder None."""
    m = _setting("fully_apk")
    return m if m.get("checksum") else None


def save_meta(info: dict, source: str) -> dict:
    m = {**{k: info.get(k) for k in INFO_KEYS}, "from": source}
    _put("fully_apk", m)
    return m


def local() -> dict | None:
    """APK auf diesem Pi (fully.json neben fully.apk) oder None."""
    return fully.apk_info()


def local_checksum() -> str | None:
    """Für die Infos dieses Pi (system.local_info): welche APK er hat."""
    info = local()
    return str(info.get("checksum")) if info and info.get("checksum") else None


def current_here() -> bool:
    m, here = meta(), local()
    return bool(m and here and here.get("checksum") == m["checksum"])


def write_local(src: Path, info: dict) -> None:
    """Datei nach fully.apk (ersetzt in einem Schritt), dazu fully.json."""
    os.chmod(src, 0o644)
    os.replace(src, fully.APK_DIR / fully.APK_FILE)
    write_local_json(info)


def adopt_local(force: bool = False) -> dict | None:
    """Übergang und «Vom Stick übernehmen»: Hat dieser Pi eine APK, die nicht die massgebliche ist, wird sie es, wenn
    es noch keine gibt oder seine neuer ist (force: immer). -> neue massgebliche oder None."""
    here, m = local(), meta()
    if not here or (m and m.get("checksum") == here.get("checksum")):
        return None
    if m and not force and str(here.get("at") or "") <= str(m.get("at") or ""):
        return None                                     # ältere als die zuletzt hochgeladene: wird ersetzt
    if not (here.get("version_name") or here.get("version_code")):
        here = {**apk_manifest(fully.APK_DIR / fully.APK_FILE, str(here.get("name") or "")), **here}
        write_local_json(here)
    log.info("Fully-APK %s von Pi %s gilt jetzt für alle Pi", here.get("name"), Config.node)
    return save_meta(here, Config.node)


def write_local_json(info: dict) -> None:
    tmp = fully.APK_DIR / f".fully.json.{secrets.token_hex(3)}"
    tmp.write_text(json.dumps({k: info.get(k) for k in INFO_KEYS if info.get(k) is not None}))
    os.replace(tmp, fully.APK_DIR / "fully.json")


# -- Kopie auf die anderen Pi -----------------------------------------------------------------------------
_sync: dict = {"thread": None, "fails": 0, "next": 0.0, "error": None, "want": None}
_sync_lock = threading.Lock()


def _pis() -> list[dict]:
    from . import system
    return system.nodes()


def holders(checksum: str) -> list[dict]:
    """Andere Pi, die laut ihren Infos genau diese APK haben -> [{id, ip}], der mit der Quelle zuerst."""
    m = meta() or {}
    out = [{"id": n["id"], "ip": n["info"].get("ip")} for n in _pis()
           if not n["here"] and n.get("reachable") and (n.get("info") or {}).get("apk") == checksum
           and (n.get("info_age") is None or n["info_age"] < 60) and n["info"].get("ip")]
    return sorted(out, key=lambda x: x["id"] != m.get("from"))


def _base_of(ip: str) -> str | None:
    return next((b for b in Config.peers if urlsplit(b).hostname == ip), None)


def sync_tick() -> None:
    """Monitor-Takt (main._tick, jeder Pi): fehlt die massgebliche APK hier, sie im Hintergrund holen."""
    m = meta()
    if not m:
        return
    with _sync_lock:
        if current_here():
            _sync.update(fails=0, error=None, want=None)
            return
        t = _sync["thread"]
        if (t and t.is_alive()) or time.time() < _sync["next"] and _sync["want"] == m["checksum"]:
            return
        if _sync["want"] != m["checksum"]:
            _sync.update(fails=0, error=None, want=m["checksum"])
        src = next((h for h in holders(m["checksum"]) if _base_of(h["ip"])), None)
        if not src:
            _sync["error"] = "no_source"
            return
        _sync["thread"] = threading.Thread(target=_fetch_safe, args=(_base_of(src["ip"]), src["id"], m),
                                           name="fully-apk", daemon=True)
        _sync["thread"].start()


def _fetch_safe(base: str, node: str, m: dict) -> None:
    try:
        fetch(base, m)
    except Exception as e:  # noqa: BLE001 – nächster Versuch später
        with _sync_lock:
            _sync["fails"] += 1
            _sync["error"] = str(e)[:120] or type(e).__name__
            _sync["next"] = time.time() + SYNC_RETRY[min(_sync["fails"], len(SYNC_RETRY)) - 1]
        log.warning("Fully-APK nicht von Pi %s geholt (%s) – nächster Versuch später", node, _sync["error"])
    else:
        with _sync_lock:
            _sync.update(fails=0, error=None)
        log.info("Fully-APK %s von Pi %s geholt", m.get("name"), node)


class FetchError(Exception):
    pass


def fetch(base: str, m: dict) -> None:
    """APK bei einem anderen Pi holen, Prüfsumme nachrechnen, ersetzen."""
    if not timesync.secret_usable(Config.cluster_secret) or not Config.cafile:
        raise FetchError("CLUSTER_SECRET oder FESTPOS_CA fehlt")    # Geheimnis nur an ein geprüftes Zertifikat
    size = int(m.get("size") or 0)
    if not 0 < size <= fully.APK_MAX:
        raise FetchError("Grösse unbekannt")
    fully.APK_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
    if shutil.disk_usage(fully.APK_DIR).free < size + SYNC_RESERVE:
        raise FetchError("zu wenig Platz")
    ctx = ssl.create_default_context(cafile=Config.cafile)
    req = urllib.request.Request(f"{base.rstrip('/')}/api/internal/fully/apk",
                                 headers={"X-Festpos-Secret": Config.cluster_secret.encode().decode("latin-1")})
    tmp = fully.APK_DIR / f"upload-{secrets.token_hex(6)}.part"
    try:
        n = 0
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 20):
                n += len(chunk)
                if n > size:
                    raise FetchError("grösser als angekündigt")
                f.write(chunk)
        if fully.apk_checksum(tmp) != m["checksum"]:
            raise FetchError("Prüfsumme stimmt nicht")
        write_local(tmp, m)
    finally:
        tmp.unlink(missing_ok=True)


internal = APIRouter()
fetch_throttle = Throttle(10, 300)


@internal.get("/api/internal/fully/apk")
async def internal_apk(request: Request):
    """Ein anderer Pi holt die APK (nur mit CLUSTER_SECRET, gegen Raten gesperrt)."""
    require_cluster_secret(request, fetch_throttle)
    info = await asyncio.to_thread(local)
    if not info:
        raise HTTPException(404)
    return FileResponse(fully.APK_DIR / fully.APK_FILE, media_type="application/vnd.android.package-archive",
                        headers={"X-Festpos-Checksum": str(info.get("checksum"))})


def pis_view() -> list[dict]:
    """Stand der APK pro Pi: ok | copying | failed | missing | off (für die Karte Fully-APK)."""
    m = meta() or {}
    out = []
    for n in _pis():
        have = local_checksum() if n["here"] else (n.get("info") or {}).get("apk")    # hier: nicht die Infos von vor 30 s
        if have and have == m.get("checksum"):
            state = "ok"
        elif not n["here"] and (not n.get("reachable") or n.get("info_age") is None or n["info_age"] > 60):
            state = "off"
        elif n["here"] and _sync.get("error") == "no_source":
            state = "missing"                           # kein anderer Pi hat sie gerade
        elif n["here"] and _sync.get("error") and not (_sync["thread"] and _sync["thread"].is_alive()):
            state = "failed"
        else:
            state = "copying"
        out.append({"id": n["id"], "here": n["here"], "state": state, **({"error": _sync["error"]} if state == "failed" else {})})
    return out


def apk_host(prefer: str) -> str | None:
    """Adresse, von der ein Handy die massgebliche APK laden soll: dieser Pi (prefer), wenn er sie hat, sonst ein
    anderer Pi, der sie hat, sonst None (wird noch kopiert)."""
    m = meta()
    if not m:
        return prefer if local() else None             # noch nichts massgeblich (vor dem ersten Übergang)
    if current_here():
        return prefer
    return next((h["ip"] for h in holders(m["checksum"])), None)


def apk_url(prefer: str) -> str:
    host = apk_host(prefer)
    if not (meta() or local()):
        raise HTTPException(409, i18n.t("err.fully.no_apk"))
    if not host:
        raise HTTPException(409, i18n.t("err.fully.apk_copying"))
    return f"http://{host}/fully/{fully.APK_FILE}"


# -- Versionen der Handys und Stand der Verteilung ----------------------------------------------------------
def versions() -> dict:
    return _setting("fully_versions")


def note_versions(results: dict[str, dict]) -> None:
    """Nach der Fully-Abfrage (Pi mit der VIP): Version und Art pro Handy merken – geschrieben nur bei Änderung.
    results: rt.monitor.fully_view()"""
    phones = {p.get("ip"): p for p in fully._devices_raw() if p.get("kind") == "phone" and p.get("id")}
    old = versions()
    new = dict(old)
    for ip, r in results.items():
        d, p = r.get("data") or {}, phones.get(ip)
        if r.get("state") != "ok" or not p or not d.get("fully_version"):
            continue
        entry = {"name": d["fully_version"], "code": d.get("fully_code"), "variant": variant(d)}
        if {k: (old.get(p["id"]) or {}).get(k) for k in entry} != entry:
            new[p["id"]] = {**entry, "at": int(time.time())}
    new = {k: v for k, v in new.items() if k in {p["id"] for p in phones.values()}}    # gelöschte Handys vergessen
    if new != old:
        _put("fully_versions", new)


def push_state() -> dict:
    return _setting("fully_apk_push")


def _today(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now))


def phones_view() -> list[dict]:
    """Handys mit Fully: Version, Art, Vergleich mit der APK und Stand der Verteilung.
    -> [{id, name, ip, version, variant, cmp (-1/0/1/None), push {state, …}|None, outdated}]"""
    m, vers, push = meta() or {}, versions(), push_state()
    jobs = push.get("phones", {}) if push.get("checksum") == m.get("checksum") else {}
    out = []
    for p in fully._devices_raw():
        if p.get("kind") != "phone" or not p.get("fully_pin") or not p.get("id"):
            continue
        v = vers.get(p["id"]) or {}
        cmp = version_cmp({"version_name": v.get("name"), "version_code": v.get("code")}, m) if v and m else None
        job = jobs.get(p["id"])
        if job and cmp == 0:
            job = {**job, "state": "ok"}
        out.append({"id": p["id"], "name": p.get("name"), "ip": p.get("ip"), "active": p.get("active", True),
                    "version": v.get("name"), "variant": v.get("variant"), "cmp": cmp, "push": job,
                    "outdated": bool(m) and p.get("active", True) and v.get("variant") != "play"
                    and (cmp == -1 or (cmp is None and bool(job) and job.get("state") != "ok"))})
    return out


def outdated() -> list[dict]:
    """Handys, die die APK auf den Pi noch nicht haben (Warnung in Übersicht und Geräte)."""
    return [p for p in phones_view() if p["outdated"]]


def _busy(phone: dict) -> bool:
    """Wartet auf diesem Handy noch eine Bestellung (Bestell-App meldet eine Outbox)?"""
    lin = device_ids.lineages(rt.store).get(phone.get("cert") or "") or {}
    serials = {c["serial"] for c in lin.get("certs", [])}
    return any(d.get("serial") in serials and d.get("seconds", 1e9) < monitor_mod.ONLINE_SECONDS and d.get("outbox")
               for d in rt.monitor.device_list())


async def _send(ip: str, cfg: dict, target: dict, url: str) -> dict:
    # T30: Installationssperren kurz aus (Fully startet beim Aktualisieren neu – die Sperren kommen danach wieder)
    return await fully.install_apk(ip, cfg["port"], target["pin"], cfg["password"], url, wait=False)


def _job(old: dict | None, state: str, now: float, text: str = "", sent: bool = False) -> dict:
    day = _today(now)
    tries = int((old or {}).get("tries") or 0) if (old or {}).get("day") == day else 0
    return {"state": state, "tries": tries + (1 if sent else 0), "day": day, "at": int(now), **({"text": text[:160]} if text else {})}


class DistributeIn(BaseModel):
    phones: list[str] | None = Field(default=None, max_length=60)


async def distribute(request: Request, ids: list[str] | None, actor: str) -> dict:
    """Auf Knopfdruck: an alle abweichenden (oder die genannten) Handys loadApkFile, sofort an die erreichbaren und
    nicht beschäftigten, die anderen bleiben «offen» für push_pending. -> {version, results: [{name, state, text?}]}"""
    m = await db(meta)
    if not m:
        raise HTTPException(409, i18n.t("err.fully.no_apk"))
    url = await db(apk_url, fully._pi_host(request))
    cfg = await db(fully.config)
    if not cfg["password"]:
        raise HTTPException(422, i18n.t("err.fully.no_password"))
    view = await db(phones_view)
    phones = [p for p in view if (p["id"] in ids if ids else p["outdated"]) and p["variant"] != "play" and p["active"]]
    raw = {p.get("id"): p for p in await db(fully._devices_raw)}
    targets = {t["ip"]: t for t in cfg["targets"]}
    now = time.time()
    old = await db(push_state)
    jobs = dict(old.get("phones") or {}) if old.get("checksum") == m["checksum"] else {}

    async def one(p: dict) -> dict:
        t = targets.get(p["ip"])
        if not t:
            return {"state": "cert"}
        if await db(_busy, raw.get(p["id"]) or {}):
            return {"state": "busy"}
        return await _send(p["ip"], cfg, t, url)

    res = await asyncio.gather(*(one(p) for p in phones))
    out = []
    for p, r in zip(phones, res):
        st = r.get("state")
        sent = st in ("ok", "sent")
        jobs[p["id"]] = _job(jobs.get(p["id"]), "sent" if sent else "open", now, "" if sent else (r.get("text") or st), sent)
        out.append({"id": p["id"], "name": p["name"], "state": "sent" if sent else st,
                    **({"text": r["text"]} if r.get("text") and not sent else {})})
    await db(_put, "fully_apk_push", {"checksum": m["checksum"], "version": m.get("version_name"), "by": actor,
                                      "at": int(now), "phones": jobs})
    await db(lb.audit, rt.store, actor, "fully_apk_push", version=m.get("version_name") or m.get("name"),
             sent=sum(1 for x in out if x["state"] == "sent"), open=sum(1 for x in out if x["state"] != "sent"))
    log.info("Fully %s verteilen: %s", m.get("version_name"), ", ".join(f"{x['name']} {x['state']}" for x in out) or "keine Handys")
    return {"version": m.get("version_name"), "results": out}


def note_sent(phone_id: str) -> None:
    """«Fully aktualisieren» an einem Handy: auch hier prüfen, ob die neue Version kommt."""
    m = meta()
    if not m:
        return
    st = push_state()
    if st.get("checksum") != m["checksum"]:
        st = {"checksum": m["checksum"], "version": m.get("version_name"), "at": int(time.time()), "phones": {}}
    st["phones"][phone_id] = _job(st["phones"].get(phone_id), "sent", time.time(), sent=True)
    _put("fully_apk_push", st)


def _push_round(now: float) -> tuple[dict | None, list[dict]]:
    """Stand der Verteilung nachführen (angekommen, Zeit abgelaufen) -> (Stand oder None, Kandidaten «offen»)."""
    m, st = meta(), push_state()
    if not m or st.get("checksum") != m["checksum"] or not st.get("phones"):
        return None, []
    view = {p["id"]: p for p in phones_view()}
    jobs, changed = dict(st["phones"]), False
    for pid, job in list(jobs.items()):
        p = view.get(pid)
        if not p or job.get("state") in ("ok", "failed") and job.get("day") == _today(now):
            continue
        if p["cmp"] is not None and p["cmp"] >= 0:
            if job.get("state") != "ok":
                jobs[pid], changed = {**job, "state": "ok", "at": int(now)}, True
                lb.audit(rt.store, fully.AUTO_ACTOR, "fully_apk_ok", name=p["name"], version=p["version"])
            continue
        if job.get("state") == "sent" and now - float(job.get("at") or 0) > PUSH_WAIT:
            tries = job.get("tries", 0) if job.get("day") == _today(now) else 0
            jobs[pid] = {**job, "state": "failed" if tries >= PUSH_TRIES else "open", "at": int(now), "text": "not_installed"}
            changed = True
        elif job.get("state") in ("ok", "failed") and job.get("day") != _today(now):
            jobs[pid], changed = {**job, "state": "open", "tries": 0, "day": _today(now)}, True   # neuer Tag
    if changed:
        st = {**st, "phones": jobs}
        _put("fully_apk_push", st)
    raw = {p.get("id"): p for p in fully._devices_raw()}
    cands = [{**view[pid], "cert": (raw.get(pid) or {}).get("cert")} for pid, job in jobs.items()
             if job.get("state") == "open" and pid in view and view[pid]["active"] and view[pid]["variant"] != "play"]
    return st, cands


def _idle(phone: dict, now: float, screen_on) -> bool:
    """Wie beim Erneuern der Zertifikate (fully._idle): niemand braucht das Handy gerade."""
    lin = device_ids.lineages(rt.store).get(phone.get("cert") or "") or {}
    c = {"serials": [x["serial"] for x in lin.get("certs", [])], "names": sorted({x["name"] for x in lin.get("certs", [])})
         or [phone.get("name") or "?"]}
    return fully._idle(c, now, screen_on)


def _save_one(checksum: str, p: dict, res: dict, now: float) -> None:
    sent = res.get("state") in ("ok", "sent")
    cur = push_state()
    if cur.get("checksum") != checksum:
        return                                          # inzwischen neue APK hochgeladen
    cur["phones"][p["id"]] = _job(cur["phones"].get(p["id"]), "sent" if sent else "open", now,
                                  "" if sent else (res.get("text") or res.get("state") or ""), True)
    _put("fully_apk_push", cur)
    lb.audit(rt.store, fully.AUTO_ACTOR, "fully_apk_push_one", name=p["name"], state=res.get("state"))


async def push_pending(cfg: dict) -> str | None:
    """Pi mit der VIP, nach poll_fully und auto_renew: Stand nachführen und einem «offenen» Handy, das wieder antwortet
    und gerade unbenutzt ist, die APK schicken (eines pro Runde). -> dessen ID oder None"""
    if not cfg.get("password"):
        return None
    now = time.time()
    st, cands = await db(_push_round, now)
    if not st or not cands:
        return None
    try:
        url = await db(apk_url, fully.renew_host())
    except HTTPException:
        return None                                     # APK noch nirgends ganz: später
    targets = {t["ip"]: t for t in cfg["targets"]}
    for p in cands:
        t, seen = targets.get(p["ip"]), rt.monitor.fully.get(p["ip"]) or {}
        job = st["phones"].get(p["id"]) or {}
        tries = job.get("tries", 0) if job.get("day") == _today(now) else 0
        if not t or seen.get("state") != "ok" or now - seen.get("at", 0) > 2 * fully.INTERVAL or \
                p["ip"] in fully._installing or tries >= PUSH_TRIES:
            continue
        if await db(_busy, p) or not await db(_idle, p, now, (seen.get("data") or {}).get("screen_on")):
            continue
        res = await _send(p["ip"], cfg, t, url)
        await db(_save_one, st["checksum"], p, res, now)
        log.info("Fully-APK nochmals an %s (%s): %s", p["name"], p["ip"], res.get("state"))
        return p["id"]
    return None


# -- Verwaltung ------------------------------------------------------------------------------------------
admin_router = APIRouter(prefix="/api/admin/fully/apk", dependencies=[Depends(admin)])


def overview() -> dict:
    m = meta()
    phones = phones_view()
    return {"meta": m, "pis": pis_view(), "phones": phones,
            "outdated": [{"name": p["name"], "version": p["version"], "push": p["push"]} for p in phones if p["outdated"]],
            "current": sum(1 for p in phones if p["cmp"] == 0)}


@admin_router.post("/distribute")
async def fully_apk_distribute(d: DistributeIn, request: Request):
    """APK vom Pi auf die Handys mit Fully (nur Admin)."""
    return await distribute(request, d.phones, (current_user(request) or {}).get("name") or "?")
