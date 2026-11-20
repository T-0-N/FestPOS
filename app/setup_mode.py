"""Ersteinrichtung im Browser (T11): kleine Web-App, die nur läuft, solange es keine festpos.conf gibt
(festpos-einrichtung.service, http Port 80; App, Datenbank und https laufen dann nicht).

Schutz: Ohne den Einrichtungscode geht nichts. Den Code erzeugt festpos-node beim Start im Ersteinrichtungs-Modus;
er steht auf dem Bildschirm am Pi und in festpos-einrichtung.txt auf der Boot-Partition (am PC lesbar), nicht im Netz.
Nach 10 falschen Versuchen ist 5 Minuten Pause. Geschrieben wird nur über den root-Helfer festpos-wiederherstellen
(Auftrag «einrichtung»), und der weist alles ab, sobald es eine festpos.conf gibt – danach ist der Modus endgültig aus.

Wege: neu einrichten (Zertifikate erzeugt der Pi), Sicherung einspielen (Ersatz-Pi; die Daten spielt die App nach dem
Neustart von selbst ein) oder Pi B/C mit dem Kartenpaket zu einem bestehenden Pi A hinzufügen."""
from __future__ import annotations

import asyncio
import hmac
import io
import json
import os
import secrets
import socket
import subprocess
import time
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import backup, i18n, release

STATIC = Path(os.environ.get("FESTPOS_STATIC", Path(__file__).resolve().parent.parent / "static"))
CODE_FILE = Path(os.environ.get("FESTPOS_EINRICHTUNG_CODE", "/var/lib/festpos/einrichtung/code"))
STASH = Path(os.environ.get("FESTPOS_EINRICHTUNG_STASH", "/var/lib/festpos/einrichtung-sicherung"))
WH_DIR = Path(os.environ.get("FESTPOS_WH_DIR", "/run/festpos/wiederherstellen"))
WH_NEU = Path(os.environ.get("FESTPOS_WH_NEU", "/run/festpos/wiederherstellen-neu"))
WH_ERGEBNIS = Path(os.environ.get("FESTPOS_WH_ERGEBNIS", "/run/festpos/wiederherstellen-ergebnis"))
MAX_FAILS, LOCK_S = 10, 300
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")
DEFAULTS = {"prefix": "192.168.55", "vip": "192.168.55.10", "a": "192.168.55.11", "b": "192.168.55.12",
            "c": "192.168.55.13", "dhcp": True, "cluster_size": 1, "node": "a", "rtc_akku": False, "tz": "Europe/Zurich"}

app = FastAPI(title="FestPOS Ersteinrichtung", docs_url=None, redoc_url=None, openapi_url=None)
S = {"fails": 0, "locked_until": 0.0, "tokens": set(), "ca_key": None, "ca_crt": None, "done": None}


# Vor dem Lesen des Inhalts (Review 2026-11, S13): FastAPI liest hochgeladene Dateien, BEVOR die Route den Code prüft.
# Ohne diese Schranke könnte jeder im Netz während der Ersteinrichtung beliebig grosse Dateien schicken. Darum: Grösse
# begrenzen, und die beiden Uploads (Sicherung, Kartenpaket) nur mit dem Sitzungs-Schlüssel aus der Code-Eingabe in
# der Kopfzeile X-Festpos-Setup.
MB = 1024 * 1024
UPLOAD_LIMITS = {"/api/einrichtung/sicherung": 260 * MB, "/api/einrichtung/kartenpaket": 5 * MB}
DEFAULT_LIMIT = 64 * 1024


class SetupGate:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            return await self.inner(scope, receive, send)
        path = scope.get("path", "")
        hdr = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        limit = UPLOAD_LIMITS.get(path, DEFAULT_LIMIT)
        if path in UPLOAD_LIMITS and hdr.get("x-festpos-setup", "") not in S["tokens"]:
            return await _reply(send, 401, t("err.setup.code_first"))
        try:
            length = int(hdr.get("content-length") or 0)
        except ValueError:
            length = 0
        if length > limit:
            return await _reply(send, 413, t("err.request.too_large", mb=max(1, limit // MB)))
        seen = 0

        async def limited():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:                      # ohne Content-Length: abbrechen wie ein Verbindungsabbruch
                    return {"type": "http.disconnect"}
            return msg
        await self.inner(scope, limited, send)


async def _reply(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                            (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})


@app.middleware("http")
async def headers(request: Request, call_next):
    r = await call_next(request)
    r.headers.setdefault("X-Content-Type-Options", "nosniff")
    r.headers.setdefault("Referrer-Policy", "no-referrer")
    r.headers.setdefault("Cache-Control", "no-store")
    if r.headers.get("content-type", "").startswith("text/html"):
        r.headers.setdefault("Content-Security-Policy", CSP)
    return r


def t(key: str, **kw) -> str:
    return i18n.t(key, **kw)


# -- Hilfen ------------------------------------------------------------------------------------------------------
def _code() -> str:
    try:
        return CODE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _norm(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def _check_token(token: str) -> None:
    if not token or token not in S["tokens"]:
        raise HTTPException(401, t("err.setup.code_first"))


def _ips() -> list[str]:
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        out = []
    return [x for x in out if ":" not in x][:6]


def _submit(kind: str, payload: dict) -> str:
    if not (WH_DIR.is_dir() and WH_NEU.is_dir()):
        raise HTTPException(503, t("err.cards.helper_missing"))
    job = secrets.token_hex(8)
    tmp = WH_NEU / job
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as f:
        json.dump({"id": job, "kind": kind, **payload}, f)
    os.replace(tmp, WH_DIR / f"auftrag-{job}.json")
    return job


def _wait(job: str, timeout: float = 60.0) -> dict | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            return json.loads((WH_ERGEBNIS / f"{job}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.25)
    return None


async def _job(kind: str, payload: dict, timeout: float = 90.0) -> dict:
    job = await asyncio.to_thread(_submit, kind, payload)
    res = await asyncio.to_thread(_wait, job, timeout)
    if not res or not res.get("ok"):
        code = (res or {}).get("code") or "timeout"
        raise HTTPException(409 if code == "configured" else 502, t("err.setup.failed", code=code))
    return res


def configured() -> bool:
    """Schon eingerichtet (festpos.conf geschrieben, Neustart steht aus)?"""
    return S["done"] is not None


# -- Seiten ------------------------------------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
@app.get("/admin.html", include_in_schema=False)
@app.get("/status.html", include_in_schema=False)
@app.get("/einrichtung.html", include_in_schema=False)
async def page():
    """Bestell-App, Verwaltung und Statusanzeige zeigen hier «Pi ist noch nicht eingerichtet» mit der Einrichtung."""
    return FileResponse(STATIC / "einrichtung.html", media_type="text/html")


@app.get("/i18n/{name}.json", include_in_schema=False)
async def lang(name: str):
    if name != "auto" and not i18n.CODE_RE.match(name):
        raise HTTPException(404)
    return JSONResponse(i18n.catalog(None if name == "auto" else name))


@app.get("/ca.crt", include_in_schema=False)
async def ca_crt():
    if not S["ca_crt"]:
        raise HTTPException(404)
    return Response(S["ca_crt"], media_type="application/x-x509-ca-cert",
                    headers={"Content-Disposition": 'attachment; filename="festpos-ca.crt"'})


@app.get("/api/health")
async def health():
    return {"setup": True}


@app.get("/api/einrichtung")
async def info():
    langs = [{"code": c, "name": (i18n.catalog(c).get("_meta") or {}).get("name") or c} for c in sorted(i18n._files())]
    return {"setup": True, "ips": await asyncio.to_thread(_ips), "host": socket.gethostname(), "defaults": DEFAULTS,
            "langs": langs, "server_time": time.time(), "done": S["done"], "locked": S["locked_until"] > time.time()}


class CodeIn(BaseModel):
    code: str = Field(max_length=40)


@app.post("/api/einrichtung/code")
async def code(c: CodeIn):
    now = time.time()
    if S["locked_until"] > now:
        raise HTTPException(429, t("err.setup.locked", minutes=max(1, round((S["locked_until"] - now) / 60))))
    want = _norm(await asyncio.to_thread(_code))
    if not want or not hmac.compare_digest(_norm(c.code), want):
        S["fails"] += 1
        if S["fails"] >= MAX_FAILS:
            S["fails"], S["locked_until"] = 0, now + LOCK_S
        await asyncio.sleep(1)                                   # Raten bremsen
        raise HTTPException(403, t("err.setup.code_wrong"))
    S["fails"] = 0
    token = secrets.token_urlsafe(24)
    S["tokens"].add(token)
    return {"token": token}


class Values(BaseModel):
    prefix: str = Field(max_length=15)
    vip: str = Field(max_length=15)
    a: str = Field(max_length=15)
    b: str = Field(max_length=15)
    c: str = Field(max_length=15)
    dhcp: bool = True
    cluster_size: int = Field(1, ge=1, le=3)
    node: str = Field("a", pattern="^[abc]$")
    rtc_akku: bool = False
    tz: str = Field("Europe/Zurich", max_length=40)


class NewIn(BaseModel):
    token: str = Field(max_length=80)
    lang: str = Field("", max_length=12)
    time_ms: int | None = None
    admin_password: str = Field(min_length=10, max_length=128)
    values: Values
    # eigener Update-Schlüssel (Inhalt von festpos-update.pub), zusätzlich zum Projekt-Schlüssel aus dem Image
    update_key: str | None = Field(None, max_length=4096)


@app.post("/api/einrichtung/neu")
async def new(n: NewIn):
    _check_token(n.token)
    if configured():
        raise HTTPException(409, t("err.setup.failed", code="configured"))
    vals = {**n.values.model_dump(), "admin_password": n.admin_password}
    keys = {}
    if n.update_key and n.update_key.strip():
        try:
            release.parse_key_line(n.update_key)
        except ValueError:
            raise HTTPException(422, t("err.setup.update_key")) from None
        typ, blob, *rest = n.update_key.split(None, 2)              # Kommentar nur ASCII (die Datei liest root als latin-1)
        comment = "".join(ch for ch in (rest[0] if rest else "") if 32 <= ord(ch) < 127).strip()[:80]
        keys["festpos-update.pub"] = " ".join(x for x in (typ, blob, comment) if x) + "\n"
    res = await _job("einrichtung", {"mode": "neu", "values": vals, "time_ms": n.time_ms, "lang": n.lang,
                                     **({"update_keys": keys} if keys else {})})
    S["ca_key"], S["ca_crt"] = res.get("ca_key"), res.get("ca_crt")
    S["done"] = {"mode": "neu", "node": res.get("node"), "vip": n.values.vip, "at": time.time()}
    return {"ok": True, "node": res.get("node"), "ca_key": bool(S["ca_key"])}


@app.get("/api/einrichtung/ca.key")
async def ca_key(token: str):
    """Schlüssel der Server-CA – nur bis zum Neustart, er kommt nicht auf die Karte."""
    _check_token(token)
    if not S["ca_key"]:
        raise HTTPException(404)
    return Response(S["ca_key"], media_type="application/x-pem-file",
                    headers={"Content-Disposition": 'attachment; filename="festpos-ca.key"'})


def _open_backup(data: bytes, password: str) -> dict[str, bytes]:
    if not backup.is_encrypted(data):
        raise HTTPException(422, t("err.restore.not_backup"))
    try:
        res = backup.try_unpack(data, backup.password_variants(password))
    except (ValueError, OSError) as e:
        raise HTTPException(422, t("err.restore.damaged", error=e)) from None
    if res is None:
        raise HTTPException(422, t("err.restore.wrong_password"))
    return res[0]


@app.post("/api/einrichtung/sicherung")
async def from_backup(token: str = Form(...), password: str = Form(...), values: str = Form(...),
                      lang: str = Form(""), time_ms: int = Form(0), file: UploadFile = File(...)):
    """Ersatz-Pi: Schlüssel und Notfall-Passwort aus der Sicherung; die Daten spielt die App nach dem Neustart ein."""
    _check_token(token)
    if configured():
        raise HTTPException(409, t("err.setup.failed", code="configured"))
    data = await file.read(256 * 1024 * 1024)
    content = await asyncio.to_thread(_open_backup, data, password)
    try:
        conf = json.loads(content.get("conf.json") or b"{}")
        vals = Values(**json.loads(values)).model_dump()
    except (ValueError, TypeError):
        raise HTTPException(422, t("err.setup.values")) from None
    tls = {k[4:]: v.decode("latin-1") for k, v in content.items() if k.startswith("tls/")}
    keys = {k[12:]: v.decode("latin-1") for k, v in content.items() if k.startswith("update-keys/")}
    if not {"ca.crt", "server.crt", "server.key"} <= set(tls):
        raise HTTPException(422, t("err.restore.no_keys"))
    await asyncio.to_thread(_stash, data, password)
    S["ca_crt"] = tls["ca.crt"].encode("latin-1")
    res = await _job("einrichtung", {"mode": "sicherung", "values": vals, "tls": tls, "update_keys": keys,
                                     "admin_password": conf.get("admin_password") or "",
                                     "cluster_secret": conf.get("cluster_secret") or "", "time_ms": time_ms or None,
                                     "lang": lang})
    S["done"] = {"mode": "sicherung", "node": res.get("node"), "vip": vals["vip"], "at": time.time()}
    return {"ok": True, "node": res.get("node")}


def _stash(data: bytes, password: str) -> None:
    """Sicherung und Passwort für die App ablegen (nur Benutzer festpos): sie spielt sie nach dem Neustart ein."""
    STASH.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, content in (("sicherung.festpos", data), ("passwort", password.encode())):
        tmp = STASH / f".{name}.neu"
        tmp.unlink(missing_ok=True)
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
            f.write(content)
        os.replace(tmp, STASH / name)


def cards_in_zip(data: bytes) -> dict[str, dict]:
    """Kartenpaket (T8): Ordner karte-a/-b/-c mit festpos.conf, node.conf, festpos-tls/ und Update-Schlüsseln."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(422, t("err.setup.no_cards")) from None
    out: dict[str, dict] = {}
    for info in z.infolist():
        parts = info.filename.split("/")
        if len(parts) < 2 or not parts[0].startswith("karte-") or info.is_dir() or info.file_size > 64 * 1024:
            continue
        letter = parts[0][6:]
        if letter not in ("a", "b", "c"):
            continue
        c = out.setdefault(letter, {"tls": {}, "update_keys": {}, "conf_text": ""})
        rest = "/".join(parts[1:])
        raw = z.read(info)
        if rest == "festpos.conf":
            c["conf_text"] = raw.decode("utf-8", errors="replace")
        elif rest.startswith("festpos-tls/") and rest[12:] in backup.TLS_FILES:
            c["tls"][rest[12:]] = raw.decode("latin-1")
        elif backup.UPDATE_KEY_RE.match(rest):
            c["update_keys"][rest] = raw.decode("latin-1")
    return {k: v for k, v in out.items() if v["conf_text"] and v["tls"]}


@app.post("/api/einrichtung/kartenpaket")
async def from_cards(token: str = Form(...), letter: str = Form(""), lang: str = Form(""), time_ms: int = Form(0),
                     file: UploadFile = File(...)):
    """Pi B/C zu einem bestehenden Pi A: Ordner aus dem Kartenpaket. Ohne Buchstabe: welche gibt es?"""
    _check_token(token)
    if configured():
        raise HTTPException(409, t("err.setup.failed", code="configured"))
    found = await asyncio.to_thread(cards_in_zip, await file.read(4 * 1024 * 1024))
    if not found:
        raise HTTPException(422, t("err.setup.no_cards"))
    if letter not in found:
        return {"letters": sorted(found)}
    c = found[letter]
    S["ca_crt"] = (c["tls"].get("ca.crt") or "").encode("latin-1") or None
    res = await _job("einrichtung", {"mode": "karte", "letter": letter, **c, "time_ms": time_ms or None, "lang": lang})
    S["done"] = {"mode": "karte", "node": res.get("node"), "vip": backup.conf_value(c["conf_text"], "VIP"),
                 "at": time.time()}
    return {"ok": True, "node": res.get("node")}


class TokenIn(BaseModel):
    token: str = Field(max_length=80)


@app.post("/api/einrichtung/neustart")
async def restart(b: TokenIn):
    _check_token(b.token)
    await _job("einrichtung_neustart", {}, 30.0)
    return {"restart": True}


app.add_middleware(SetupGate)                                  # ganz aussen: vor dem Lesen des Inhalts

if STATIC.is_dir():                                            # admin.css, i18n.js, Schriften, Symbole
    app.mount("/", StaticFiles(directory=STATIC), name="static")
