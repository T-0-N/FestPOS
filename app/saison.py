"""Saison abschliessen: Archiv der Belege, danach Gastdaten auf dem Pi löschen (T-0-N/FestPOS#12).

Belege müssen 10 Jahre aufbewahrt werden (OR, MWSTG). Personendaten – Name und Ort des Gastes auf Quittungen über
CHF 400 mit MWST – sollen aber nicht unbegrenzt auf dem Pi am Festplatz liegen (DSG). Darum in drei Schritten:
1. Archiv erstellen: alle Tagesabschlüsse seit der letzten Saison, alle Quittungen dieser Abschlüsse samt Kopien –
   unverändert, mit ihren Prüfsummen –, jeder Abschluss als PDF zum Lesen und archiv.json mit SHA-256 jeder Datei.
   Verschlüsselt wie eine Sicherung (backup.pack, eigenes Passwort, «kind»: saison). Liegt bis zum Abschluss nur auf
   diesem Pi in ARCHIV_DIR (Stand in PENDING).
2. Archiv bestätigen: auf den USB-Stick geschrieben und zurückgelesen (gleiche SHA-256, festpos-wiederherstellen) –
   oder heruntergeladen und wieder hochgeladen. Beim Hochladen wird es entschlüsselt und geprüft (verify).
3. Erst dann: Quittungen und Kopien der archivierten Abschlüsse löschen (kritische Aktion). Quittungen werden nie
   verändert, nur als Ganzes ins Archiv verschoben. Die Nummern laufen weiter (Untergrenze receipt_no_floor). Die
   Tagesabschlüsse bleiben (ohne Gastdaten) – die Kette der Prüfsummen läuft ohne Bruch weiter; ihr letzter Wert der
   Saison steht als Anker im Archiv und in SEASON_KEY.
Erinnerung in der Übersicht («season_due»), wenn abgeschlossene Quittungen älter als DUE_DAYS Tage auf dem Pi liegen.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import backup, i18n, ledger_base as lb, saison_inhalt
from .core import APP_VERSION, Config, admin, current_user, db, rt
from .store import now_iso

ARCHIV_DIR = Path(os.environ.get("FESTPOS_ARCHIV_DIR", "/var/lib/festpos/archiv"))
PENDING = "offen.json"
SEASON_KEY = "season_last"
DUE_DAYS = 90
FORMAT = 1
KIND = "saison"
EXT = ".festpos"
NAME_RE = re.compile(r"^festpos-saison-[0-9-]{10}_[0-9-]{10}(-[0-9]{1,3})?\.festpos$")
MAX_UPLOAD = 300 * 1024 * 1024
admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])


def t(key: str, **kw) -> str:
    return i18n.t(key, **kw)


# -- Was ins Archiv gehört ------------------------------------------------------------------------------------------
def _last() -> dict:
    try:
        d = json.loads(rt.store.setting(SEASON_KEY, "{}") or "{}")
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def scope() -> dict:
    """Abschlüsse seit der letzten Saison und ihre Quittungen (nur echte, keine Übungen)."""
    store, last = rt.store, _last()
    after = int(last.get("to") or 0)
    rng = store.query_one("SELECT MIN(id) AS a, MAX(id) AS b, MIN(created_at) AS first, MAX(created_at) AS last "
                          "FROM closings WHERE id>?", (after,)) or {}
    a, b = rng.get("a"), rng.get("b")
    if not b:
        return {"closings": 0, "receipts": 0, "recipients": 0, "from": None, "to": None, "first": None, "last": None}
    rows = store.query("SELECT data FROM receipts WHERE closing_id BETWEEN ? AND ? AND test=0", (a, b))
    recipients = sum(1 for r in rows if (json.loads(r["data"] or "{}") or {}).get("recipient"))
    n = store.scalar("SELECT COUNT(*) FROM closings WHERE id BETWEEN ? AND ?", (a, b)) or 0
    return {"closings": n, "receipts": len(rows), "recipients": recipients, "from": a, "to": b,
            "first": rng.get("first"), "last": rng.get("last")}


def due() -> bool:
    """Erinnerung: abgeschlossene Quittungen älter als DUE_DAYS liegen noch auf dem Pi."""
    limit = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - DUE_DAYS * 86400))
    return bool(rt.store.scalar("SELECT 1 FROM receipts WHERE closing_id IS NOT NULL AND test=0 AND created_at<? LIMIT 1",
                                (limit,)))


def _plain(row: dict) -> dict:
    return {k: (base64.b64encode(v).decode() if isinstance(v, bytes) else v) for k, v in row.items()}


def build(password: str) -> tuple[str, bytes, dict]:
    """Archiv erstellen -> (Dateiname, verschlüsselte Datei, Inhaltsangabe)."""
    from . import ledger
    store, sc = rt.store, scope()
    if not sc["to"]:
        raise HTTPException(409, t("err.season.nothing"))
    a, b = sc["from"], sc["to"]
    closings = store.query("SELECT * FROM closings WHERE id BETWEEN ? AND ? ORDER BY id", (a, b))
    recs = store.query("SELECT * FROM receipts WHERE closing_id BETWEEN ? AND ? AND test=0 ORDER BY id", (a, b))
    ids = [r["id"] for r in recs]
    copies = store.query(f"SELECT * FROM receipt_copies WHERE receipt_id IN ({','.join('?' * len(ids))}) ORDER BY id",
                         tuple(ids)) if ids else []
    prev = store.query_one("SELECT chain_hash FROM closings WHERE id<? ORDER BY id DESC LIMIT 1", (a,))
    files: dict[str, bytes] = {
        "abschluesse.json": json.dumps([_plain(r) for r in closings], ensure_ascii=False, indent=1).encode(),
        "quittungen.json": json.dumps([_plain(r) for r in recs], ensure_ascii=False, indent=1).encode(),
        "kopien.json": json.dumps([_plain(r) for r in copies], ensure_ascii=False, indent=1).encode(),
        "LIESMICH.txt": t("admin.season.readme").encode(),
    }
    for r in closings:
        try:
            files[f"pdf/abschluss-{r['id']:05d}.pdf"] = ledger.closing_pdf(r)
        except Exception as e:  # noqa: BLE001 – Entscheid: kein Archiv ohne PDF jedes Abschlusses
            raise HTTPException(500, t("err.season.pdf", id=r["id"])) from e
    nos = [r["no"] for r in recs if r.get("no")]
    info = {"format": FORMAT, "kind": KIND, "created_at": now_iso(), "node": Config.node, "app": APP_VERSION,
            "event": store.setting("event_name", "") or "", "closings": {"from": a, "to": b, "count": len(closings)},
            "period": {"first": sc["first"], "last": sc["last"]},
            "receipts": {"count": len(recs), "first_no": min(nos) if nos else None, "last_no": max(nos) if nos else None,
                         "copies": len(copies), "recipients": sc["recipients"]},
            "chain": {"before": prev["chain_hash"] if prev else None, "anchor": closings[-1]["chain_hash"]},
            "key_id": lb.key_id(), "files": {n: hashlib.sha256(c).hexdigest() for n, c in sorted(files.items())}}
    files["archiv.json"] = json.dumps(info, ensure_ascii=False, indent=1).encode()
    data = backup.pack(files, password, node=Config.node, app=APP_VERSION, kind=KIND)
    d1, d2 = (sc["first"] or "")[:10], (sc["last"] or "")[:10]
    return f"festpos-saison-{d1}_{d2}{EXT}", data, info


def verify(members: dict[str, bytes]) -> dict:
    """Inhalt eines entschlüsselten Archivs prüfen: SHA-256 jeder Datei, Quittungen (HMAC, wenn der Schlüssel dieser
    Installation passt) und die Kette der Tagesabschlüsse. -> {ok, files_ok, receipts: {ok, other_key, bad},
    closings: {ok, other_key, bad}, chain_ok, info}"""
    from . import ledger
    got = saison_inhalt.records(members)
    if not got:
        return {"ok": False, "files_ok": False, "format": False}
    info, closings, recs = got
    files_ok = saison_inhalt.complete(members, info, closings, recs) and not saison_inhalt.bad_digests(members, info)
    rc = {"ok": 0, "other_key": 0, "bad": 0}
    for r in recs:
        res = lb.check_digest(r.get("digest"), lb.receipt_body(r))
        rc["ok" if res in ("ok", "missing") else "other_key" if res == "other_key" else "bad"] += 1
    cc = {"ok": 0, "other_key": 0, "bad": 0}
    chain_ok, prev = True, info.get("chain", {}).get("before")
    keyed = bool(prev and prev.startswith(ledger.CHAIN_V2))
    for r in closings:
        try:
            state = ledger.check_chain_hash(r, ledger._unpack(r["orders_csv"]), keyed)
        except (ValueError, OSError, KeyError):
            state = "bad"
        cc["ok" if state in ("ok", "legacy") else "other_key" if state == "other_key" else "bad"] += 1
        chain_ok = chain_ok and (r.get("prev_hash") or None) == (prev or None)
        prev, keyed = r.get("chain_hash"), keyed or str(r.get("chain_hash") or "").startswith(ledger.CHAIN_V2)
    chain_ok = chain_ok and prev == info.get("chain", {}).get("anchor")
    ok = files_ok and chain_ok and not rc["bad"] and not cc["bad"] and info.get("kind") == KIND
    return {"ok": ok, "files_ok": files_ok, "receipts": rc, "closings": cc, "chain_ok": chain_ok,
            "info": {k: info.get(k) for k in ("created_at", "node", "app", "event", "closings", "period", "receipts")}}


# -- Stand auf diesem Pi ----------------------------------------------------------------------------------------
def _pending() -> dict | None:
    try:
        d = json.loads((ARCHIV_DIR / PENDING).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not NAME_RE.match(str(d.get("name") or "")) or not (ARCHIV_DIR / d["name"]).is_file():
        return None
    return d


def _save_pending(d: dict | None) -> None:
    ARCHIV_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(ARCHIV_DIR, 0o700)
    p = ARCHIV_DIR / PENDING
    if d is None:
        p.unlink(missing_ok=True)
        return
    tmp = p.with_name(".offen.neu")
    tmp.write_text(json.dumps(d))
    os.replace(tmp, p)


def _drop_archive() -> None:
    p = _pending()
    if p:
        (ARCHIV_DIR / p["name"]).unlink(missing_ok=True)
    _save_pending(None)


def state() -> dict:
    from . import cards
    p = _pending()
    return {"open": scope(), "last": _last() or None, "pending": p, "due": due(), "usb": cards.available()}


# -- Routen -----------------------------------------------------------------------------------------------------
class PasswordIn(BaseModel):
    password: str = Field(min_length=backup.MIN_PASSWORD, max_length=200)


class CloseIn(BaseModel):
    sha: str = Field(pattern=r"^[0-9a-f]{64}$")


@admin_router.get("/season")
async def season():
    return await db(state)


@admin_router.post("/season/archive")
async def create(p: PasswordIn, request: Request):
    """Schritt 1: Archiv erstellen (ersetzt ein noch nicht bestätigtes) und gleich zur Probe entschlüsseln."""
    def run():
        name, data, info = build(p.password)
        check = verify(backup.unpack(data, p.password))
        if not check["ok"]:
            raise HTTPException(500, t("err.season.self_check"))
        _drop_archive()
        ARCHIV_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(ARCHIV_DIR, 0o700)
        fd = os.open(ARCHIV_DIR / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        pend = {"name": name, "sha": hashlib.sha256(data).hexdigest(), "size": len(data), "at": now_iso(),
                "closings": info["closings"], "receipts": info["receipts"], "anchor": info["chain"]["anchor"],
                "verified": None, "check": check}
        _save_pending(pend)
        lb.audit(rt.store, (current_user(request) or {}).get("name"), "season_archive", name=name,
                 closings=info["closings"], receipts=info["receipts"]["count"])
        return pend
    return await db(run)


@admin_router.get("/season/archive")
async def download():
    p = await db(_pending)
    if not p:
        raise HTTPException(404, t("err.season.no_archive"))
    data = await db((ARCHIV_DIR / p["name"]).read_bytes)
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{p["name"]}"'})


@admin_router.post("/season/usb")
async def to_usb(request: Request):
    """Schritt 2 (Stick): Archiv auf den USB-Stick (Ordner festpos-archiv) und zurücklesen – gleich? dann bestätigt."""
    from . import cards
    p = await db(_pending)
    if not p:
        raise HTTPException(404, t("err.season.no_archive"))
    job = await db(cards.submit, "archiv_usb", {"name": p["name"]})
    res = await db(cards.wait, job, 180)
    if not res:
        raise HTTPException(504, t("err.season.usb_timeout"))
    if not res.get("ok"):
        code = res.get("code") or "job"
        raise HTTPException(409, t("err.season.no_stick") if code == "no_stick" else t("err.cards.failed", code=code))
    if res.get("sha") != p["sha"]:
        raise HTTPException(409, t("err.season.usb_mismatch"))
    p = {**p, "verified": {"how": "usb", "at": now_iso(), "device": res.get("device"), "path": res.get("path")}}
    await db(_save_pending, p)
    return p


@admin_router.post("/season/verify")
async def check_upload(request: Request, file: UploadFile = File(...), password: str = Form(..., max_length=200)):
    """Schritt 2 (Download): heruntergeladenes Archiv wieder hochladen – entschlüsseln, prüfen, gleich? bestätigt.
    Geht auch für ein älteres Archiv (nur prüfen und anzeigen)."""
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD or not backup.is_encrypted(data):
        raise HTTPException(422, t("err.season.not_archive"))
    try:
        members = await db(backup.unpack, data, password)
    except backup.BadPassword:
        raise HTTPException(422, t("err.season.password")) from None
    except ValueError:
        raise HTTPException(422, t("err.season.not_archive")) from None
    check = await db(verify, members)
    p = await db(_pending)
    same = bool(p) and hashlib.sha256(data).hexdigest() == p["sha"]
    if same and check["ok"]:
        p = {**p, "verified": {"how": "upload", "at": now_iso()}}
        await db(_save_pending, p)
    return {"check": check, "matches_pending": same, "pending": p}


@admin_router.post("/season/close")
async def close(c: CloseIn, request: Request):
    """Schritt 3 (kritisch): Quittungen und Kopien der archivierten Abschlüsse löschen – nur mit bestätigtem Archiv."""
    who = (current_user(request) or {}).get("name") or "?"

    def run():
        store = rt.store
        p = _pending()
        if not p or p["sha"] != c.sha or not p.get("verified"):
            raise HTTPException(409, t("err.season.not_confirmed"))
        a, b = p["closings"]["from"], p["closings"]["to"]
        if store.scalar("SELECT chain_hash FROM closings WHERE id=?", (b,)) != p["anchor"]:
            raise HTTPException(409, t("err.season.changed"))
        n = store.scalar("SELECT COUNT(*) FROM receipts WHERE closing_id BETWEEN ? AND ? AND test=0", (a, b))
        sel = "SELECT id FROM receipts WHERE closing_id BETWEEN ? AND ? AND test=0"
        copies = store.scalar(f"SELECT COUNT(*) FROM receipt_copies WHERE receipt_id IN ({sel})", (a, b))
        if n != p["receipts"]["count"] or copies != p["receipts"].get("copies"):    # Kopie seither? nicht im Archiv
            raise HTTPException(409, t("err.season.changed"))
        max_no = store.scalar("SELECT MAX(no) FROM receipts WHERE closing_id BETWEEN ? AND ? AND test=0", (a, b)) or 0
        last = {"at": now_iso(), "from": a, "to": b, "receipts": n, "sha": p["sha"], "name": p["name"],
                "anchor": p["anchor"], "by": who, "how": p["verified"].get("how")}
        floor = "INSERT INTO settings(key,value) VALUES ('receipt_no_floor', ?) ON CONFLICT(key) DO UPDATE SET " \
                "value=MAX(CAST(value AS INTEGER), CAST(excluded.value AS INTEGER))"
        store.batch([(f"DELETE FROM receipt_copies WHERE receipt_id IN ({sel})", (a, b)),
                     ("DELETE FROM receipts WHERE closing_id BETWEEN ? AND ? AND test=0", (a, b)),
                     (floor, (str(max_no),)),
                     ("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (SEASON_KEY, json.dumps(last))),
                     lb.audit_stmt(who, "season_close", closings=[a, b], receipts=n, archive=p["name"], sha=p["sha"])],
                    transaction=True)
        _drop_archive()                                    # Gastdaten auch nicht mehr im Archiv auf dem Pi
        return last
    return await db(run)


@admin_router.delete("/season/archive")
async def discard():
    """Noch nicht bestätigtes Archiv verwerfen (auf dem Pi löschen)."""
    await db(_drop_archive)
    return {"ok": True}
