"""Verwaltung › Wartung: Sicherung herunterladen und einspielen (verschlüsselt, mit allem drin – T8),
Sicherungs-Passwort, Bestellungen nach dem Fest zurücksetzen, Übungsbestellungen (Testmodus) löschen.

Einspielen unterscheidet zwei Fälle (docs/backup-restore.md):
- gleiches System (die laufende Datenbank hat Bestellungen, Zertifikate oder Abschlüsse): Schlüssel, Zugangsdaten
  und Sicherungs-Passwort des laufenden Systems bleiben; Sperren und Zertifikate, die seit der Sicherung dazukamen,
  werden übernommen; Quittungsnummern laufen ohne Lücke weiter.
- neuer Pi (leere Datenbank, z. B. Ersatzkarte): alles aus der Sicherung gilt; weichen Zertifikate, Schlüssel,
  CLUSTER_SECRET oder Notfall-Passwort ab, schreibt sie der root-Helfer festpos-wiederherstellen zurück und startet
  den Pi neu. Die Quittungsnummer springt (die Nummern nach der Sicherung kennt niemand).
Was beim Einspielen festgestellt wurde, steht in der Einstellung restore_check (Prüfliste, app/restore_check.py)."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import auth, backup as backup_mod, cards, devices, i18n, ledger
from . import ledger_base as lb
from .core import APP_VERSION, Config, admin, db, log, rt
from .i18n import t
from .store import StoreError

admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])

PASSWORD, PASSWORD_OLD = "backup_password", "backup_password_old"          # Geheimnisse (backup.SECRET_KEYS)
PASSWORD_AT, PASSWORD_PRINTED = "backup_password_at", "backup_password_printed"
SYSTEM_IDS = "system_ids"            # Fingerabdrücke der CA und Kennung des Prüfsummen-Schlüssels (nichts Geheimes)
RESTORE_CHECK = "restore_check"      # was beim letzten Einspielen festgestellt wurde (Prüfliste)
RECEIPT_FLOOR = "receipt_no_floor"   # Quittungsnummern: nie unter diese (receipts.py)
PASSWORD_META = (PASSWORD_AT, PASSWORD_PRINTED)
UPSERT = "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"


# -- Sicherungs-Passwort und Kennungen (beim Start) ----------------------------------------------------------------
def ensure_password() -> None:
    """Neueinrichtung (und erster Start nach dem Update): Sicherungs-Passwort erzeugen. Im Cluster gewinnt der erste
    Pi (ON CONFLICT DO NOTHING), alle verschlüsseln danach mit demselben."""
    if rt.store.setting(PASSWORD):
        return
    rt.store.batch([("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO NOTHING",
                     (PASSWORD, backup_mod.new_password())),
                    ("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO NOTHING",
                     (PASSWORD_AT, str(int(time.time()))))])
    log.info("Sicherungs-Passwort erzeugt – in der Verwaltung ausdrucken (Wartung › Sicherung)")


def tls_dir() -> Path | None:
    return Path(Config.server_cert).parent if Config.server_cert else None


def _read(path: str | Path | None) -> bytes | None:
    try:
        return Path(path).read_bytes() if path else None
    except OSError:
        return None


def system_ids() -> dict:
    """Woran man erkennt, ob eine Sicherung zu diesem Pi passt: Geräte-CA, Server-CA, Prüfsummen-Schlüssel."""
    return {"device_ca": backup_mod.fingerprint(_read(Config.device_ca)),
            "server_ca": backup_mod.fingerprint(_read(Config.cafile)), "ledger_key": lb.key_id()}


def publish_system_ids() -> None:
    """Beim Start, nur wenn sich etwas geändert hat (SD-Karte schonen): so steht in jeder Sicherung, zu welcher CA
    und welchem Schlüssel sie gehört."""
    value = json.dumps(system_ids(), sort_keys=True)
    if rt.store.setting(SYSTEM_IDS) != value:
        rt.store.execute(UPSERT, (SYSTEM_IDS, value))


def current_conf() -> dict:
    return {"cluster_secret": Config.cluster_secret, "admin_password": Config.admin_password}


# -- Herunterladen ------------------------------------------------------------------------------------------------
class BackupIn(BaseModel):
    password: str = Field(max_length=200)


def make_backup(password: str, kind: str = "manual") -> bytes:
    """Ganze Datenbank (mit Zugangsdaten), Zertifikate und Schlüssel, CLUSTER_SECRET und Notfall-Passwort –
    verschlüsselt."""
    data = backup_mod.members(rt.store.backup(), tls_dir(), current_conf())
    return backup_mod.pack(data, password, node=Config.node, app=APP_VERSION, kind=kind)


@admin_router.post("/backup")
async def backup(b: BackupIn, request: Request):
    """Sicherung herunterladen – immer verschlüsselt, mit einem Passwort, das der Admin wählt (mind. 12 Zeichen)."""
    if len(b.password) < backup_mod.MIN_PASSWORD:
        raise HTTPException(422, t("err.backup.password_short", count=backup_mod.MIN_PASSWORD))
    data = await db(make_backup, b.password)
    await db(lb.audit, rt.store, ledger.actor_of(request), "backup_download")
    name = backup_mod.manual_name(Config.node)
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


# -- Sicherungs-Passwort (automatische Sicherung) ------------------------------------------------------------------
def password_info(show: bool = False) -> dict:
    pw = rt.store.setting(PASSWORD)
    printed = rt.store.setting(PASSWORD_PRINTED)
    return {"set": bool(pw), "prefix": pw[:4] if pw else "", "password": pw if show else None,
            "at": int(rt.store.setting(PASSWORD_AT) or 0) or None, "printed": int(printed) if printed else None,
            "has_old": bool(rt.store.setting(PASSWORD_OLD))}


@admin_router.get("/backup/password")
async def get_password(request: Request, show: bool = False):
    info = await db(password_info, show)
    if show:
        from . import qr
        await db(lb.audit, rt.store, ledger.actor_of(request), "backup_password_show")
        info.update(svg=qr.qr_svg(info["password"]) if info["password"] else None, node=Config.node,
                    event=await db(rt.store.setting, "event_name"))
    return info


@admin_router.post("/backup/password/printed")
async def password_printed(request: Request):
    await db(rt.store.execute, UPSERT, (PASSWORD_PRINTED, str(int(time.time()))))
    await db(lb.audit, rt.store, ledger.actor_of(request), "backup_password_printed")
    return await db(password_info)


@admin_router.post("/backup/password/new")
async def password_new(request: Request, confirm: str = Query(...)):
    """Neues Sicherungs-Passwort: gilt ab der nächsten Sicherung; das bisherige bleibt als «früheres» (der Pi probiert
    es beim Einspielen selbst), ältere Sicherungen auf dem Stick brauchen es weiter."""
    if confirm != "JA":
        raise HTTPException(400, t("err.common.confirm_ja"))

    def run():
        old = rt.store.setting(PASSWORD)
        rt.store.batch([(UPSERT, (PASSWORD_OLD, old or "")), (UPSERT, (PASSWORD, backup_mod.new_password())),
                        (UPSERT, (PASSWORD_AT, str(int(time.time())))),
                        ("DELETE FROM settings WHERE key=?", (PASSWORD_PRINTED,))])
    await db(run)
    await db(lb.audit, rt.store, ledger.actor_of(request), "backup_password_new")
    return await db(password_info)


class BonIn(BaseModel):
    printer_id: int


@admin_router.post("/backup/password/bon")
async def password_bon(b: BonIn, request: Request):
    """Passwort auf einen Bondrucker – direkt, nicht über die Warteschlange (sonst stünde es in print_jobs)."""
    from . import print_service as ps
    from .escpos import PrinterAddr, PrinterError, send_raw
    p = (await db(ps.load_printers, rt.store)).get(b.printer_id)
    if not p:
        raise HTTPException(404, t("err.printer.not_found"))
    info = await db(password_info, True)
    if not info["password"]:
        raise HTTPException(409, t("err.backup.no_password"))
    payload = ps.render_password_bon(p, password=info["password"], node=Config.node, when=time.strftime("%d.%m.%Y %H:%M"))
    try:
        await send_raw(PrinterAddr(p.host, p.port), payload)
    except PrinterError as e:
        raise HTTPException(502, t("err.backup.bon_failed", printer=p.name, error=e)) from None
    await db(lb.audit, rt.store, ledger.actor_of(request), "backup_password_bon", printer=p.name)
    return {"printed": True}


# -- Einspielen ---------------------------------------------------------------------------------------------------
def _open_backup(data: bytes, password: str) -> tuple[dict[str, bytes], dict | None, str]:
    """Datei öffnen -> (Inhalt, Kopf, welches Passwort passte: given | own | old | none). Ältere Sicherungen sind eine
    reine SQLite-Datei."""
    if backup_mod.is_encrypted(data):
        head = backup_mod.read_header(data)
        if head is None:
            raise HTTPException(422, t("err.restore.damaged", error="format"))
        tries = [password] if password else [rt.store.setting(PASSWORD), rt.store.setting(PASSWORD_OLD)]
        try:
            res = backup_mod.try_unpack(data, tries)
        except (ValueError, OSError) as e:                     # ZIP kaputt, Kopf unsinnig
            raise HTTPException(422, t("err.restore.damaged", error=e)) from None
        if res is None:
            if password:
                raise HTTPException(422, t("err.restore.wrong_password"))
            raise HTTPException(428, t("err.restore.password_needed"))
        content, i = res
        if not content.get("festpos.sqlite", b"").startswith(backup_mod.SQLITE_MAGIC):
            raise HTTPException(422, t("err.restore.damaged", error="festpos.sqlite"))
        return content, head, "given" if password else ("own", "old")[i]
    if data.startswith(backup_mod.SQLITE_MAGIC):
        return {"festpos.sqlite": data}, None, "none"
    raise HTTPException(422, t("err.restore.not_backup"))


def _running() -> dict:
    """Was vom laufenden System beim Einspielen gebraucht wird (vor dem Ersetzen gelesen)."""
    st = rt.store
    keys = backup_mod.SECRET_KEYS + PASSWORD_META
    marks = ",".join("?" * len(keys))
    state_like = " OR ".join("key LIKE ?" for _ in backup_mod.STATE_PREFIXES)
    state_marks = ",".join("?" * len(backup_mod.STATE_KEYS))
    new = not st.scalar("SELECT (SELECT COUNT(*) FROM orders) + (SELECT COUNT(*) FROM device_certs) + "
                        "(SELECT COUNT(*) FROM closings) + (SELECT COUNT(*) FROM receipts WHERE no IS NOT NULL)")
    return {
        "new": bool(new),
        "secrets": st.query(f"SELECT key, value FROM settings WHERE key IN ({marks})", keys),
        "state": st.query(f"SELECT key, value FROM settings WHERE key IN ({state_marks}) OR {state_like}",
                          (*backup_mod.STATE_KEYS, *(p + "%" for p in backup_mod.STATE_PREFIXES))),
        "certs": st.query("SELECT * FROM device_certs"),
        "role_ids": st.query("SELECT device_id, role FROM device_role_ids"),
        "receipt_max": int(st.scalar("SELECT COALESCE(MAX(no), 0) FROM receipts") or 0),
        "receipt_floor": int(st.setting(RECEIPT_FLOOR) or 0),
        "gen": int(st.scalar("SELECT COALESCE(MAX(gen), 0) FROM users") or 0),
    }


def _ledger_key_of(con: sqlite3.Connection) -> str | None:
    """Kennung des Prüfsummen-Schlüssels einer älteren Sicherung (ohne system_ids): aus der neusten Prüfsumme."""
    for sql in ("SELECT digest FROM orders WHERE digest IS NOT NULL ORDER BY id DESC LIMIT 1",
                "SELECT chain_hash FROM closings ORDER BY id DESC LIMIT 1"):
        try:
            row = con.execute(sql).fetchone()
        except sqlite3.OperationalError:
            continue
        m = re.search(r"([0-9a-f]{8}):[0-9a-f]{64}$", (row or [""])[0] or "")
        if m:
            return m.group(1)
    return None


def _ceil_hundred(n: int) -> int:
    return -(-n // 100) * 100


def _prepare_backup(data: bytes, gen_add: int, now: dict | None = None, info: dict | None = None) -> tuple[bytes, int]:
    """Sicherung vor dem Einspielen anpassen -> (Datei, stornierte Aufträge). Alles in der Datei selbst, damit es vom
    ersten Moment an gilt, auch wenn der Pi gleich danach ausfällt (offene Aufträge: REV-023):
    - offene Druckaufträge stornieren – sonst könnte der Druckdienst sie greifen und alte Bons drucken
    - Anmeldungen enden (gen + gen_add): sonst passte ein Token (Nummer + Stand) womöglich auf eine andere Person
    - Geheimnisse: gleiches System -> die des laufenden; neuer Pi -> die der Sicherung (fehlende vom laufenden)
    - Zustand der Pi (Infos, Druckerstand, Bildschirm) vom laufenden System, nie aus der Sicherung
    - Sperren und Zertifikate seit der Sicherung bleiben (nur dazu, nie eine Sperre aufheben)
    - Quittungsnummern laufen weiter (Untergrenze)
    info (falls gegeben) bekommt, was festgestellt wurde (für die Prüfliste)."""
    now = now if now is not None else {"new": False, "secrets": [], "state": [], "certs": [], "role_ids": [],
                                       "receipt_max": 0, "receipt_floor": 0}
    info = info if info is not None else {}

    def run(sql: str, params: tuple = ()) -> int:
        try:
            return max(con.execute(sql, params).rowcount, 0)
        except sqlite3.OperationalError as e:
            if "no such table" in str(e):                 # sehr alte Sicherung: init_schema legt sie nachher an
                return 0
            raise

    def one(sql: str, params: tuple = ()):
        try:
            row = con.execute(sql, params).fetchone()
        except sqlite3.OperationalError as e:
            if "no such table" in str(e) or "no such column" in str(e):
                return None
            raise
        return row[0] if row else None

    con = sqlite3.connect(":memory:")
    try:
        try:
            con.deserialize(backup_mod.no_wal(data))
            n = run("UPDATE print_jobs SET status='cancelled', last_error=? WHERE status IN ('pending','printing')",
                    (t("srv.job.restored"),))
            run("UPDATE users SET gen = gen + ?", (gen_add,))
            # Geheimnisse und Sicherungs-Passwort
            upsert = UPSERT if not now["new"] else "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO NOTHING"
            for r in now["secrets"]:
                run(upsert, (r["key"], r["value"]))
            if not now["new"] and PASSWORD_PRINTED not in {r["key"] for r in now["secrets"]}:
                run("DELETE FROM settings WHERE key=?", (PASSWORD_PRINTED,))   # gilt für das Passwort des laufenden
            # Zustand der Pi
            like = " OR ".join("key LIKE ?" for _ in backup_mod.STATE_PREFIXES)
            run(f"DELETE FROM settings WHERE key IN ({','.join('?' * len(backup_mod.STATE_KEYS))}) OR {like}",
                (*backup_mod.STATE_KEYS, *(p + "%" for p in backup_mod.STATE_PREFIXES)))
            for r in now["state"]:
                run(UPSERT, (r["key"], r["value"]))
            # Zeitpunkt der Sicherung: neuste Bestellung bzw. neuster Protokolleintrag
            from .timesync import local_epoch
            backup_at = max(local_epoch(one("SELECT MAX(created_at) FROM orders")) or 0,
                            local_epoch(one("SELECT MAX(at) FROM audit_log")) or 0) or None
            info["backup_at"] = backup_at
            # T4: Kandidaten und Alarme beziehen sich auf MAC von damals – neu bilden lassen
            from . import mac_change
            if not backup_at or time.time() - backup_at > mac_change.DEFAULT_MINUTES * 60:
                run("DELETE FROM settings WHERE key=?", (mac_change.STATE,))
            # Kennungen der Sicherung (Prüfliste)
            try:
                ids = json.loads(one("SELECT value FROM settings WHERE key=?", (SYSTEM_IDS,)) or "{}")
            except ValueError:
                ids = {}
            info["ids_backup"] = {"device_ca": ids.get("device_ca"), "server_ca": ids.get("server_ca"),
                                  "ledger_key": ids.get("ledger_key") or _ledger_key_of(con)}
            # Zertifikate und Sperren seit der Sicherung
            cols = [r[1] for r in con.execute("PRAGMA table_info(device_certs)")]
            have = {r[0]: r[1] for r in con.execute("SELECT serial, revoked_at FROM device_certs")} if cols else {}
            added = revoked = 0
            for c in now["certs"]:
                if c["serial"] in have:
                    if c.get("revoked_at") and not have[c["serial"]]:
                        sets = ["revoked_at=?"] + (["removed_at=COALESCE(removed_at, ?)"] if "removed_at" in cols else [])
                        vals = [c["revoked_at"]] + ([c.get("removed_at")] if "removed_at" in cols else [])
                        revoked += run(f"UPDATE device_certs SET {', '.join(sets)} WHERE serial=?", (*vals, c["serial"]))
                elif cols:
                    use = [k for k in c if k in cols and k != "id"]
                    added += run(f"INSERT INTO device_certs({','.join(use)}) VALUES ({','.join('?' * len(use))})",
                                 tuple(c[k] for k in use))
            for r in now["role_ids"]:
                run("INSERT INTO device_role_ids(device_id, role) VALUES (?,?) ON CONFLICT(device_id) DO NOTHING",
                    (r["device_id"], r["role"]))
            info.update(certs_added=added, revocations_added=revoked)
            # Quittungsnummern
            file_max = int(one("SELECT COALESCE(MAX(no), 0) FROM receipts") or 0)
            file_floor = int(one("SELECT value FROM settings WHERE key=?", (RECEIPT_FLOOR,)) or 0)
            if now["new"]:
                real = file_max or one("SELECT COUNT(*) FROM orders WHERE test=0")
                floor = max(file_floor, _ceil_hundred(file_max + 100) - 1) if real else file_floor
            else:
                floor = max(file_floor, now["receipt_max"], now["receipt_floor"])
            if floor > file_floor:
                run(UPSERT, (RECEIPT_FLOOR, str(floor)))
            info.update(receipt_last_backup=file_max, receipt_last_now=now["receipt_max"],
                        receipt_next=max(file_max, floor) + 1)
            # Prüfliste
            run("DELETE FROM settings WHERE key=?", (RESTORE_CHECK,))
            run(UPSERT, (RESTORE_CHECK, json.dumps(info, sort_keys=True)))
            con.commit()
        except sqlite3.DatabaseError as e:
            raise HTTPException(422, t("err.restore.damaged", error=e)) from None
        return con.serialize(), n
    finally:
        con.close()


def _keys_of(content: dict[str, bytes]) -> tuple[dict[str, bytes], dict] | None:
    """Zertifikate/Schlüssel und Werte aus festpos.conf einer verschlüsselten Sicherung – None bei älteren."""
    tls = {k[4:]: v for k, v in content.items() if k.startswith("tls/") and k[4:] in backup_mod.TLS_FILES}
    if not tls:
        return None
    try:
        conf = json.loads(content.get("conf.json") or b"{}")
    except ValueError:
        conf = {}
    return tls, {k: v for k, v in conf.items() if k in ("cluster_secret", "admin_password") and isinstance(v, str)}


def keys_differ(tls: dict[str, bytes], conf: dict) -> bool:
    here = tls_dir()
    if any(_read(here / name if here else None) != content for name, content in tls.items()):
        return True
    now = current_conf()
    return any(conf.get(k) and conf[k] != now[k] for k in ("cluster_secret", "admin_password"))


def apply_keys(content: dict[str, bytes]) -> bool:
    """Schlüssel und Werte der Sicherung an den root-Helfer geben (nur Einzel-Pi) – er schreibt sie auf die
    Boot-Partition und startet den Pi neu."""
    k = _keys_of(content)
    if not k:
        return False
    tls, conf = k
    if any(ord(ch) < 32 or ord(ch) == 127 for v in conf.values() for ch in v):   # gehörte nie in festpos.conf (S2)
        raise HTTPException(422, t("err.restore.damaged", error="conf.json"))
    # Update-Schlüssel der Sicherung: nur die Namen – der Helfer schreibt sie nie (Review 2026-11, S2: sonst genügte
    # ein Admin-Passwort, um einen eigenen Update-Schlüssel und damit eigenen Code auf den Pi zu bringen)
    names = sorted(n[12:] for n in content if n.startswith("update-keys/"))
    cards.submit("keys", {"tls": {n: v.decode("latin-1") for n, v in tls.items()}, "conf": conf,
                          "update_keys": {n: "" for n in names}})
    return True


def _keys_state(content: dict[str, bytes], new: bool) -> str:
    """same | differ | differ_cluster | restoring | unknown (ältere Sicherung ohne Schlüssel)."""
    k = _keys_of(content)
    if not k:
        return "unknown"
    if not keys_differ(*k):
        return "same"
    if len(Config.peers) > 1:
        return "differ_cluster"
    return "restoring" if new else "differ"


@admin_router.post("/restore")
async def restore(request: Request, file: UploadFile = File(...), confirm: str = Query(...),
                  password: str = Form("")):
    """Sicherung einspielen. Ersetzt ALLE Daten. Offene Druckaufträge aus der Sicherung werden nicht gedruckt (sonst
    kämen alte Bons erneut). Verschlüsselte Sicherung: zuerst mit dem eigenen (und dem früheren) Sicherungs-Passwort
    versucht; passt keines, 428 – dann mit dem Passwort vom Ausdruck."""
    if confirm != "JA":
        raise HTTPException(400, t("err.common.confirm_ja"))
    data = await file.read(256 * 1024 * 1024)
    return await restore_data(data, password, ledger.actor_of(request), file.filename or "")


async def restore_data(data: bytes, password: str, actor: str, filename: str) -> dict:
    """Einspielen (Verwaltung, oder nach der Ersteinrichtung mit Sicherung von selbst, T11)."""
    content, head, used = await db(_open_backup, data, password)
    now = await db(_running)
    keys = await db(_keys_state, content, now["new"])
    if keys == "restoring" and not cards.available():
        keys = "differ"                                         # Helfer fehlt (Entwicklung, alte Installation)
    info = {"at": int(time.time()), "by": actor, "case": "new" if now["new"] else "same",
            "file": filename[:120], "backup_node": (head or {}).get("node"),
            "backup_app": (head or {}).get("app"), "encrypted": head is not None, "password": used,
            "keys": keys, "ids_before": await db(system_ids), "done": None}
    fixed, pre_cancelled = await db(_prepare_backup, content["festpos.sqlite"], now["gen"] + 1, now, info)
    await db(rt.store.restore, fixed)
    await db(rt.store.init_schema)                           # ältere Sicherung -> fehlende Spalten ergänzen
    auth.forget()
    devices.forget_retired()
    devices.notify_revoked()                                 # Sperrliste sofort, auch auf den anderen Pi
    res = await db(rt.store.execute, "UPDATE print_jobs SET status='cancelled', last_error=?, error_code='restored' "
                                  "WHERE status IN ('pending','printing')", (t("srv.job.restored"),))
    for r in await db(rt.store.query, "SELECT id FROM printers"):
        rt.spooler.notify(r["id"])
    await db(publish_system_ids)
    stats = await db(rt.store.query_one, "SELECT (SELECT COUNT(*) FROM products) AS products, "
                                      "(SELECT COUNT(*) FROM orders) AS orders")
    await db(lb.audit, rt.store, actor, "restore", case=info["case"], keys=keys, **stats)
    nxt, last = info.get("receipt_next") or 0, info.get("receipt_last_backup") or 0
    if nxt > last + 1:
        await db(lb.audit, rt.store, actor, "receipt_gap", first=last + 1, last=nxt - 1, case=info["case"])
    restart = False
    if keys == "restoring":
        restart = await db(apply_keys, content)
        if restart:
            await db(lb.audit, rt.store, actor, "restore_keys")
    return {**stats, "jobs_cancelled": pre_cancelled + res.rows_affected, "case": info["case"], "keys": keys,
            "restart": restart, "check": True}


@admin_router.post("/restore/keys")
async def restore_keys(request: Request, file: UploadFile = File(...), password: str = Form("")):
    """Gleiches System, aber Schlüssel weichen ab: auf Wunsch die Schlüssel der Sicherung übernehmen (Einzel-Pi)."""
    if len(Config.peers) > 1:
        raise HTTPException(409, t("err.restore.keys_cluster"))
    if not cards.available():
        raise HTTPException(503, t("err.cards.helper_missing"))
    content, _, _ = await db(_open_backup, await file.read(256 * 1024 * 1024), password)
    if not await db(apply_keys, content):
        raise HTTPException(422, t("err.restore.no_keys"))
    await db(lb.audit, rt.store, ledger.actor_of(request), "restore_keys")
    return {"restart": True}


@admin_router.post("/reset-orders")
async def reset_orders(request: Request, confirm: str = Query(..., description="Muss 'JA' sein")):
    """Nach dem Fest: alle Bestellungen + Druckjobs löschen (Produkte/Drucker/Tische bleiben). Erst wenn alle
    echten Bestellungen in einem Tagesabschluss sind – die Abschlüsse (mit allen Bestellungen als CSV) bleiben."""
    if confirm != "JA":
        raise HTTPException(400, t("err.common.confirm_ja"))
    open_n = await db(ledger.open_orders, rt.store)
    if open_n:
        raise HTTPException(409, t("err.ledger.close_first", count=open_n))
    # Kommt zwischen Prüfung und Löschen noch eine Bestellung, bleibt der Zeitpunkt im Protokoll leer: dann
    # scheitert der ganze Batch und nichts ist gelöscht
    guard = ("INSERT INTO audit_log(at, actor, action, details) VALUES (CASE WHEN EXISTS(SELECT 1 FROM orders "
             "WHERE closing_id IS NULL AND test=0) THEN NULL ELSE ? END,?,?,?)",
             lb.audit_stmt(ledger.actor_of(request), "reset_orders")[1])
    try:
        # Nummern beginnen danach wieder bei 1: was an einer Bestellnummer hängt (Trinkgeld, Payrexx, Online-Bestellung)
        # darf nicht an einer neuen Bestellung gleicher Nummer landen – steht alles im Tagesabschluss
        res = await db(rt.store.batch, [guard, ("DELETE FROM print_jobs", ()), ("DELETE FROM order_items", ()),
                                        ("DELETE FROM orders", ()), ("DELETE FROM tips", ()),
                                        ("DELETE FROM payrexx_payments", ()),
                                        ("DELETE FROM tab_payments", ()), ("DELETE FROM tabs", ()),
                                        ("UPDATE gast_orders SET order_id=NULL", ()),
                                        ("DELETE FROM order_payments", ())])
    except StoreError as e:
        if "NOT NULL constraint failed: audit_log.at" not in str(e):
            raise
        raise HTTPException(409, t("err.ledger.close_first", count=await db(ledger.open_orders, rt.store))) from None
    return {"deleted_orders": res[3].rows_affected}


@admin_router.post("/test-orders/delete")
async def delete_test_orders(request: Request, confirm: str = Query(..., description="Muss 'JA' sein")):
    """Übungsbestellungen aus dem Testmodus löschen (echte Bestellungen bleiben). Bons, die gerade gedruckt
    werden, bleiben samt ihrer Bestellung – nochmals versuchen, wenn sie fertig sind."""
    if confirm != "JA":
        raise HTTPException(400, t("err.common.confirm_ja"))
    test_ids = "SELECT id FROM orders WHERE test=1"
    busy = f"SELECT order_id FROM print_jobs WHERE status='printing' AND order_id IN ({test_ids})"
    res = await db(rt.store.batch, [
        (f"DELETE FROM print_jobs WHERE order_id IN ({test_ids}) AND order_id NOT IN ({busy})", ()),
        (f"DELETE FROM receipt_copies WHERE receipt_id IN (SELECT id FROM receipts WHERE test=1 AND order_uuid NOT IN "
         f"(SELECT client_uuid FROM orders WHERE id IN ({busy})))", ()),
        (f"DELETE FROM receipts WHERE test=1 AND order_uuid NOT IN (SELECT client_uuid FROM orders WHERE id IN ({busy}))", ()),
        (f"DELETE FROM order_items WHERE order_id IN ({test_ids}) AND order_id NOT IN ({busy})", ()),
        (f"DELETE FROM tips WHERE order_id IN ({test_ids}) AND order_id NOT IN ({busy})", ()),
        (f"DELETE FROM orders WHERE test=1 AND id NOT IN ({busy})", ()),
        ("DELETE FROM tab_payments WHERE tab_id IN (SELECT id FROM tabs WHERE test=1 AND id NOT IN "
         "(SELECT tab_id FROM orders WHERE tab_id IS NOT NULL))", ()),
        ("DELETE FROM tabs WHERE test=1 AND id NOT IN (SELECT tab_id FROM orders WHERE tab_id IS NOT NULL)", ()),
        ("DELETE FROM order_payments WHERE order_id NOT IN (SELECT id FROM orders)", ()),
    ], transaction=True)
    if res[5].rows_affected:
        await db(lb.audit, rt.store, ledger.actor_of(request), "test_orders_delete", count=res[5].rows_affected)
    return {"deleted_orders": res[5].rows_affected}


# -- Nach der Ersteinrichtung (T11) ------------------------------------------------------------------------------
STASH = Path(os.environ.get("FESTPOS_EINRICHTUNG_STASH", "/var/lib/festpos/einrichtung-sicherung"))
START_FILE = Path(os.environ.get("FESTPOS_EINRICHTUNG_START", "/var/lib/festpos/einrichtung/start.json"))


async def after_setup() -> None:
    """Einmal nach dem Start: Sprache aus der Ersteinrichtung übernehmen (wenn noch keine eingestellt ist) und eine
    Sicherung, die bei der Ersteinrichtung gewählt wurde, einspielen – dann ist der Ersatz-Pi ohne weiteren Schritt
    so wie vorher (Prüfliste in der Verwaltung)."""
    try:
        start = json.loads(START_FILE.read_text(encoding="utf-8"))
        code = i18n.normalize(start.get("lang"))
        if code and not await db(rt.store.setting, "language"):
            await db(rt.store.execute, "INSERT INTO settings(key,value) VALUES ('language',?) "
                                       "ON CONFLICT(key) DO NOTHING", (code,))
    except (OSError, ValueError, AttributeError):
        pass
    data_p, pw_p = STASH / "sicherung.festpos", STASH / "passwort"
    if not data_p.is_file():
        return
    try:
        data, pw = data_p.read_bytes(), pw_p.read_text(encoding="utf-8")
        res = await restore_data(data, pw, "einrichtung", "einrichtung")
        log.warning("Sicherung aus der Ersteinrichtung eingespielt: %s", {k: res.get(k) for k in ("case", "keys", "orders")})
    except HTTPException as e:
        log.error("Sicherung aus der Ersteinrichtung nicht eingespielt: %s", e.detail)
    except Exception:                                          # noqa: BLE001 – Start der App nie verhindern
        log.exception("Sicherung aus der Ersteinrichtung nicht eingespielt")
        return                                                 # (Datenbank noch nicht bereit?) beim nächsten Start
    for p_ in (data_p, pw_p):
        p_.unlink(missing_ok=True)
