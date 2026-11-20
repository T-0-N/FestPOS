"""Karten für weitere Pi (T8): Kartenpaket herunterladen, Pi A auf 3 Pi umstellen – und der Weg zum root-Helfer
festpos-wiederherstellen, der auch Schlüssel aus einer Sicherung zurückschreibt und die Fully-APK vom Stick holt.

Die App (Benutzer festpos) darf weder die Boot-Partition lesen noch schreiben. Sie legt pro Auftrag eine Datei
«auftrag-<id>.json» in WH_DIR ab (erst in WH_NEU fertig schreiben, dann verschieben); systemd startet den Helfer,
solange dort etwas liegt (festpos-wiederherstellen.path). Die Antwort steht in WH_ERGEBNIS/<id>.json (gehört root),
das Kartenpaket in WH_ERGEBNIS/<id>.zip (Gruppe festpos). /run liegt im Arbeitsspeicher: nach einem Neustart weg.

Kartenpaket: ein Ordner pro Karte (karte-b, karte-c …) mit festpos.conf (CLUSTER_SIZE=3, CLUSTER_SECRET
ausgeschrieben), node.conf, festpos-tls/ und den Update-Schlüsseln – Inhalt 1:1 auf die Boot-Partition. Nicht
verschlüsselt (die Karte liest es beim Start ohne Passwort), darum nur auf Knopfdruck und mit Warnung."""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import ledger
from . import ledger_base as lb
from .core import Config, admin, db, rt
from .i18n import t

admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])

WH_DIR = Path(os.environ.get("FESTPOS_WH_DIR", "/run/festpos/wiederherstellen"))               # Aufträge (festpos)
WH_NEU = Path(os.environ.get("FESTPOS_WH_NEU", "/run/festpos/wiederherstellen-neu"))           # zum Schreiben
WH_ERGEBNIS = Path(os.environ.get("FESTPOS_WH_ERGEBNIS", "/run/festpos/wiederherstellen-ergebnis"))  # Antworten (root)
LETTERS = ("a", "b", "c")
FRESH_BACKUP_S = 60 * 60            # «frische Sicherung» vor dem Umstellen auf 3 Pi


def available() -> bool:
    """Ist der root-Helfer da (Pi ab dieser Version)? In der Entwicklung und in Tests ohne Ordner: nein."""
    return WH_DIR.is_dir() and WH_NEU.is_dir()


def submit(kind: str, payload: dict | None = None) -> str:
    """Auftrag an festpos-wiederherstellen ablegen -> Auftragsnummer."""
    if not available():
        raise HTTPException(503, t("err.cards.helper_missing"))
    job = secrets.token_hex(8)
    tmp = WH_NEU / job
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as f:
        json.dump({"id": job, "kind": kind, **(payload or {})}, f)
    os.replace(tmp, WH_DIR / f"auftrag-{job}.json")
    return job


def wait(job: str, timeout: float = 30.0) -> dict | None:
    """Antwort des Helfers abwarten -> {ok, code, …} oder None (antwortet nicht)."""
    end = time.monotonic() + timeout
    path = WH_ERGEBNIS / f"{job}.json"
    while time.monotonic() < end:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.25)
    return None


# -- Weitere Pi vorbereiten ------------------------------------------------------------------------------------
def expand_state() -> dict:
    """Was «Auf 3 Pi erweitern» braucht: Einzelbetrieb und führend, frische Sicherung, kein Anlass im Gang."""
    from . import status as status_mod, system as system_mod
    single = len(Config.peers) <= 1
    _, newest = status_mod.backup_state()
    last_dl = rt.store.scalar("SELECT MAX(at) FROM audit_log WHERE action='backup_download'")
    from .timesync import local_epoch
    times = [x for x in ((newest or {}).get("at"), local_epoch(last_dl)) if x]
    backup_at = max(times) if times else None
    open_jobs = int(rt.store.scalar("SELECT COUNT(*) FROM print_jobs WHERE status IN ('pending','printing')") or 0)
    last_order = local_epoch(rt.store.scalar("SELECT MAX(created_at) FROM orders WHERE test=0"))
    busy = open_jobs > 0 or bool(last_order and time.time() - last_order < 30 * 60)
    checks = {"single": single and bool(rt.spooler and rt.spooler.is_leader),
              "backup": bool(backup_at and time.time() - backup_at < FRESH_BACKUP_S),
              "idle": not busy}
    return {"single": single, "node": Config.node, "helper": available(), "checks": checks,
            "backup_at": backup_at, "open_jobs": open_jobs, "last_order_at": last_order,
            "nodes": system_mod.nodes(), "letters": [x for x in LETTERS if x != Config.node]}


@admin_router.get("/cluster/expand")
async def get_expand():
    return await db(expand_state)


class CardsIn(BaseModel):
    letters: list[str] = Field(min_length=1, max_length=3)


def _readme(letters: list[str]) -> str:
    return "\n\n".join([t("srv.cards.readme.title"), t("srv.cards.readme.warning"),
                        *(t("srv.cards.readme.card", letter=x.upper(), folder=f"karte-{x}") for x in letters),
                        t("srv.cards.readme.after")]) + "\n"


@admin_router.post("/cluster/kartenpaket")
async def kartenpaket(c: CardsIn, request: Request):
    """Kartenpaket herunterladen (unverschlüsselt, mit allen Schlüsseln – nach dem Kopieren löschen)."""
    letters = sorted({x.lower() for x in c.letters})
    if not set(letters) <= set(LETTERS) or Config.node in letters:
        raise HTTPException(422, t("err.cards.letters"))
    job = await db(submit, "kartenpaket", {"letters": letters, "readme": _readme(letters)})
    res = await db(wait, job, 30.0)
    if not res or not res.get("ok"):
        raise HTTPException(502, t("err.cards.failed", code=(res or {}).get("code") or "timeout"))
    path = WH_ERGEBNIS / f"{job}.zip"
    try:
        data = await db(path.read_bytes)
    except OSError:
        raise HTTPException(502, t("err.cards.failed", code="zip")) from None
    await db(lb.audit, rt.store, ledger.actor_of(request), "kartenpaket", letters=",".join(letters))
    name = f"festpos-kartenpaket-{time.strftime('%Y%m%d')}.zip"
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@admin_router.post("/cluster/expand")
async def expand(request: Request, confirm: str = Query(...)):
    """Pi A auf 3 Pi umstellen: CLUSTER_SIZE=3 in festpos.conf, dann startet der Pi neu. Danach arbeitet er allein
    weiter, bis B und C beigetreten sind (festpos-rqlited)."""
    if confirm != "JA":
        raise HTTPException(400, t("err.common.confirm_ja"))
    st = await db(expand_state)
    if not st["single"]:
        raise HTTPException(409, t("err.cards.not_single"))
    if not all(st["checks"].values()):
        raise HTTPException(409, t("err.cards.checks"))
    job = await db(submit, "cluster3")
    res = await db(wait, job, 20.0)
    if not res or not res.get("ok"):
        raise HTTPException(502, t("err.cards.failed", code=(res or {}).get("code") or "timeout"))
    await db(lb.audit, rt.store, ledger.actor_of(request), "cluster_expand")
    return {"restart": True}


def apk_from_stick() -> dict:
    """Fully-APK vom USB-Stick (Ordner festpos-sicherung) auf diesen Pi holen."""
    job = submit("apk")
    res = wait(job, 90.0)
    if not res or not res.get("ok"):
        raise HTTPException(502, t("err.cards.apk", code=(res or {}).get("code") or "timeout"))
    return res


# -- SD-Karte im USB-Kartenleser schreiben (T10) -----------------------------------------------------------------
# Das Schreiben macht root (deploy/festpos-karte, Dienst festpos-karte.service); die App liefert nur Gerät,
# Seriennummer, Grösse und Buchstabe aus der Liste und liest den Fortschritt aus KARTE_DIR/status.json.
KARTE_DIR = Path(os.environ.get("FESTPOS_KARTE_DIR", "/run/festpos/karte"))
CARD_AUDIT = {"done": "card_written", "failed": "card_failed", "cancelled": "card_cancelled"}
WRITTEN_S = 24 * 3600                   # «Karten B und C sind vorbereitet»: an diesem Pi in den letzten 24 h


def card_status() -> dict | None:
    """Fortschritt des Schreibens (ohne Datenbank); ein Status ohne Lebenszeichen gilt als gescheitert."""
    try:
        s = json.loads((KARTE_DIR / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(s, dict):
        return None
    if s.get("state") == "running" and time.time() - float(s.get("at") or 0) > 60:
        s.update(state="failed", code="stale")
    return s


def _written() -> dict:
    """Buchstabe -> Zeit der zuletzt an diesem Pi geschriebenen Karte (aus dem Protokoll)."""
    from .timesync import local_epoch
    out = {}
    for r in rt.store.query("SELECT at, details FROM audit_log WHERE action='card_written' ORDER BY id DESC LIMIT 20"):
        at = local_epoch(r["at"])
        try:
            d = json.loads(r["details"] or "{}")
        except ValueError:
            continue
        if at and time.time() - at < WRITTEN_S and d.get("node") == Config.node and d.get("letter") not in out:
            out[d.get("letter")] = at
    return out


def _audit_card_end(s: dict | None) -> None:
    """Am Ende einmal ins Protokoll (Auftragsnummer), geschrieben vom Pi, der den Status liest."""
    if not s or s.get("state") not in CARD_AUDIT or not s.get("id"):
        return
    job = str(s["id"])
    if rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action IN ('card_written','card_failed','card_cancelled') "
                       "AND details LIKE ?", (f'%"job": "{job}"%',)):
        return
    lb.audit(rt.store, "system", CARD_AUDIT[s["state"]], job=job, letter=s.get("letter"), node=Config.node,
             model=s.get("model"), size=s.get("size"), code=s.get("code"))


def cards_state() -> dict:
    st = expand_state()
    s = card_status()
    _audit_card_end(s)
    reachable = {n["id"] for n in st["nodes"] if n.get("reachable") and not n.get("here")}
    return {"helper": st["helper"], "node": Config.node, "single": st["single"], "busy": not st["checks"]["idle"],
            "letters": list(LETTERS), "running": sorted(reachable), "status": s, "written": _written()}


@admin_router.get("/cluster/karten")
async def get_cards():
    """Kartenleser und Karten (vom root-Helfer) und der Stand des Schreibens."""
    out = await db(cards_state)
    if not out["helper"]:
        return {**out, "devices": [], "missing": [], "need": None}
    job = await db(submit, "karten")
    res = await db(wait, job, 15.0)
    if not res or not res.get("ok"):
        raise HTTPException(502, t("err.cards.failed", code=(res or {}).get("code") or "timeout"))
    liste = res.get("liste") or {}
    return {**out, "devices": liste.get("devices") or [], "missing": liste.get("missing") or [],
            "need": liste.get("need"), "small_hint": bool(liste.get("small_hint"))}


@admin_router.get("/cluster/karte")
async def get_card_status():
    return await db(cards_state)


class CardWriteIn(BaseModel):
    device: str = Field(pattern=r"^sd[a-z]{1,2}$")
    serial: str = Field(default="", max_length=200)
    size: int = Field(gt=0)
    letter: str = Field(pattern="^[abcABC]$")
    confirm: str = Field(max_length=5)


@admin_router.post("/cluster/karte")
async def write_card(c: CardWriteIn, request: Request):
    letter = c.letter.lower()
    if c.confirm.strip().lower() != letter:
        raise HTTPException(400, t("err.cards.confirm_letter", letter=letter.upper()))
    job = await db(submit, "karte_schreiben", {"device": c.device, "serial": c.serial, "size": c.size,
                                               "letter": letter, "confirm": c.confirm.strip().lower()})
    res = await db(wait, job, 60.0)
    if not res or not res.get("ok"):
        code = (res or {}).get("code") or "timeout"
        raise HTTPException(409 if code in CARD_REFUSE else 502, t(CARD_REFUSE.get(code, "err.cards.failed"), code=code))
    await db(lb.audit, rt.store, ledger.actor_of(request), "card_write", job=res.get("job"), letter=letter,
             node=Config.node, device=c.device, size=c.size)
    return {"job": res.get("job")}


CARD_REFUSE = {"busy": "err.cards.card_busy", "changed": "err.cards.card_changed", "too_small": "err.cards.card_small",
               "system": "err.cards.card_refused", "stick": "err.cards.card_refused", "mounted": "err.cards.card_refused",
               "not_usb": "err.cards.card_refused", "not_removable": "err.cards.card_refused",
               "no_card": "err.cards.card_none", "tools": "err.cards.card_tools"}


@admin_router.post("/cluster/karte/abbrechen")
async def cancel_card():
    job = await db(submit, "karte_abbrechen")
    res = await db(wait, job, 20.0)
    if not res or not res.get("ok"):
        raise HTTPException(502, t("err.cards.failed", code=(res or {}).get("code") or "timeout"))
    return {"running": bool(res.get("running"))}
