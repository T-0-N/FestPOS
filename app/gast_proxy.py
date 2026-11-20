"""Gästedienst für die QR-Bestellung am Tisch (T22) – das Einzige, was über den Cloudflare Tunnel erreichbar ist.

Läuft als eigener Benutzer «festpos-gast» in einer Sandbox (deploy/systemd/festpos-gast.service) nur auf 127.0.0.1:8090.
Er kennt weder Datenbank noch Payrexx-Schlüssel: Er liefert die Gästeseite aus und reicht genau diese Anfragen an
FestPOS weiter (Verwaltungs-Port auf 127.0.0.1, mit dem Schlüssel aus /etc/festpos-gast/token):

    GET  /api/gast/menu, /api/gast/order/<token>     POST /api/gast/order, /api/gast/order/<token>/cancel, /webhook
    GET  /i18n/<sprache>.json, /media/<bild>, /api/logo   (Texte, Produktbilder, Logo)
    GET  / (ohne QR-Code), /impressum, /agb, /datenschutz, /rueckerstattung, /custom.css   (Webseite, app/web.py)

Alles andere: 404. Dazu: Bremse pro Gast (Adresse von Cloudflare; IPv6 pro /64, weil ein Anschluss dort beliebig viele
Adressen hat) und für neue Bestellungen eine Bremse über alle Gäste zusammen, Grössenlimit, feste Sicherheits-Header;
eingehende Header werden nicht weitergereicht (nur Inhaltstyp), Cloudflare-Header nie.

    uvicorn app.gast_proxy:app --host 127.0.0.1 --port 8090
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import ssl
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

STATIC = Path(os.environ.get("FESTPOS_STATIC", Path(__file__).resolve().parent.parent / "static"))
UPSTREAM_HOST = os.environ.get("FESTPOS_GAST_UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.environ.get("FESTPOS_GAST_UPSTREAM_PORT", "8443"))
UPSTREAM_TLS = os.environ.get("FESTPOS_GAST_UPSTREAM_TLS", "1") == "1"
TOKEN_FILE = Path(os.environ.get("FESTPOS_GAST_TOKEN_FILE", "/etc/festpos-gast/token"))
BODY_MAX = 32 * 1024
REPLY_MAX = 2 * 1024 * 1024
RATE = {"get": (120, 60.0), "post": (12, 60.0),           # Anfragen pro Zeitfenster und Gast
        # neue Bestellungen (je eine Zahlungsseite bei Payrexx und Schreibzugriffe auf allen Pi) von ALLEN Gästen
        # zusammen – begrenzt, was jemand mit vielen Adressen anrichten kann (Review 2026-11, S7)
        "order": (60, 60.0)}
HITS_MAX = 20000                                          # so viele Zähler höchstens im Speicher
FILES = {"/": "gast/index.html", "/index.html": "gast/index.html", "/gast.js": "gast/gast.js",
         "/app.css": "app.css", "/i18n.js": "i18n.js", "/web.css": "gast/web.css"}
# Webseite zur QR-Bestellung (app/web.py): öffentliche Adresse -> Seite bei FestPOS; «/» nur ohne QR-Code (t, b)
WEB = {"/impressum": "imprint", "/agb": "terms", "/datenschutz": "privacy", "/rueckerstattung": "refund",
       "/custom.css": "custom.css"}
FORWARD_GET = re.compile(r"^/(api/gast/menu|api/gast/order/[A-Za-z0-9_-]{8,40}|i18n/[a-z-]{2,12}\.json|"
                         r"media/[A-Za-z0-9_-]{6,80}|api/logo)$")
FORWARD_POST = re.compile(r"^/api/gast/(order|order/[A-Za-z0-9_-]{8,40}/cancel|webhook)$")
FONT = re.compile(r"^/fonts/[a-z0-9-]+\.woff2$")
# Webseite und Rechtsseiten (app/web.py) brauchen keine Skripte: dieselbe strengere Regel wie web.CSP (der Gästedienst
# lädt app.web nicht – test_gast_proxy.py prüft, dass beide gleich bleiben; Review 2026-11, S18)
WEB_CSP = ("default-src 'self'; script-src 'none'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
           "connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
                               "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cache-Control": "no-store",
}
_hits: dict[tuple[str, str], list[float]] = {}


def _token() -> str:
    try:
        return TOKEN_FILE.read_text().strip()
    except OSError:
        return ""


def client_ip(request: Request) -> str:
    """Adresse des Gastes: von Cloudflare (CF-Connecting-IP), nur wenn die Anfrage von cloudflared auf diesem Pi kommt."""
    peer = request.client.host if request.client else ""
    if peer in ("127.0.0.1", "::1"):
        cf = request.headers.get("cf-connecting-ip", "")
        if re.fullmatch(r"[0-9a-fA-F:.]{2,45}", cf):
            return cf
    return peer


def rate_key(ip: str) -> str:
    """Wofür die Bremse zählt: IPv4 die Adresse, IPv6 das /64 (ein Anschluss hat dort 2^64 Adressen)."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if a.version == 6:
        if a.ipv4_mapped:
            return str(a.ipv4_mapped)
        return f"{ipaddress.ip_network(f'{a}/64', strict=False).network_address}/64"
    return str(a)


def _prune(now: float) -> None:
    """Speicher begrenzen, ohne die Bremse aufzuheben: zuerst abgelaufene Zähler weg, dann die mit den wenigsten
    Treffern – wer an der Grenze ist, bleibt gesperrt (früher wurde alles gelöscht: damit setzte, wer viele Adressen
    hat, die Bremse für alle zurück)."""
    for k in [k for k, v in _hits.items() if not v or now - v[-1] >= RATE[k[1]][1]]:
        del _hits[k]
    if len(_hits) > HITS_MAX:
        for k in sorted(_hits, key=lambda k: len(_hits[k]))[:len(_hits) // 2]:
            del _hits[k]


def limited(ip: str, kind: str) -> bool:
    n, win = RATE[kind]
    now = time.monotonic()
    key = (rate_key(ip), kind)
    lst = [x for x in _hits.get(key, []) if now - x < win]
    if len(lst) >= n:
        _hits[key] = lst
        return True
    lst.append(now)
    _hits[key] = lst
    if len(_hits) > HITS_MAX:
        _prune(now)
    return False


def _secure(resp: Response, cache: bool = False, web: bool = False) -> Response:
    for k, v in HEADERS.items():
        if k == "Cache-Control" and cache:
            v = "public, max-age=600"
        if k == "Content-Security-Policy" and web:
            v = WEB_CSP
        resp.headers[k] = v
    return resp


async def upstream(method: str, path_qs: str, body: bytes, ctype: str | None, ip: str) -> tuple[int, str, bytes]:
    """Eine Anfrage an FestPOS (Verbindung zu, Antwort begrenzt). Ohne Zertifikatsprüfung: nur 127.0.0.1."""
    ctx = None
    if UPSTREAM_TLS:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    reader, writer = await asyncio.wait_for(asyncio.open_connection(UPSTREAM_HOST, UPSTREAM_PORT, ssl=ctx), 5)
    try:
        head = [f"{method} {path_qs} HTTP/1.1", f"Host: {UPSTREAM_HOST}", "Connection: close",
                f"X-Festpos-Gast: {_token()}", f"X-Festpos-Gast-Ip: {ip}", f"Content-Length: {len(body)}"]
        if ctype:
            head.append(f"Content-Type: {ctype[:100]}")
        writer.write(("\r\n".join(head) + "\r\n\r\n").encode() + body)
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(REPLY_MAX + 65536), 20)
        while len(raw) < REPLY_MAX + 65536:
            more = await asyncio.wait_for(reader.read(65536), 20)
            if not more:
                break
            raw += more
    finally:
        writer.close()
    head, _, rest = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    hdrs = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
    if hdrs.get("transfer-encoding", "").lower() == "chunked":
        rest = _unchunk(rest)
    return status, hdrs.get("content-type", "application/octet-stream"), rest[:REPLY_MAX]


def _unchunk(data: bytes) -> bytes:
    out, i = b"", 0
    while i < len(data):
        j = data.find(b"\r\n", i)
        if j < 0:
            break
        size = int(data[i:j].split(b";")[0] or b"0", 16)
        if size == 0:
            break
        out += data[j + 2:j + 2 + size]
        i = j + 2 + size + 2
    return out


async def handle(request: Request) -> Response:
    # der rohe, dekodierte Pfad (wie das Routing): request.url.path entfernt Zeilenumbrüche und schneidet bei «?» ab
    path = request.scope.get("path") or ""
    ip = client_ip(request)
    kind = "post" if request.method == "POST" else "get"
    if request.method not in ("GET", "HEAD", "POST"):
        return _secure(Response(status_code=405))
    web = None
    if request.method in ("GET", "HEAD"):
        q = request.query_params
        if path in WEB or (path in ("/", "/index.html") and not q.get("t") and not q.get("b")):
            web = WEB.get(path, "start")
        elif path in FILES:
            return _secure(FileResponse(STATIC / FILES[path]), cache=path not in ("/", "/index.html"))
        elif FONT.fullmatch(path) and (STATIC / path.lstrip("/")).is_file():
            return _secure(FileResponse(STATIC / path.lstrip("/")), cache=True)
        elif not FORWARD_GET.fullmatch(path):          # fullmatch: «$» liesse einen Zeilenumbruch am Ende durch
            return _secure(JSONResponse({"detail": "Not Found"}, status_code=404))
    elif not FORWARD_POST.fullmatch(path):
        return _secure(JSONResponse({"detail": "Not Found"}, status_code=404))
    if limited(ip, kind) or (request.method == "POST" and path == "/api/gast/order" and limited("*", "order")):
        return _secure(JSONResponse({"detail": "busy"}, status_code=429))
    body = b""
    if request.method == "POST":
        if int(request.headers.get("content-length") or 0) > BODY_MAX:
            return _secure(Response(status_code=413))
        async for chunk in request.stream():
            body += chunk
            if len(body) > BODY_MAX:
                return _secure(Response(status_code=413))
    qs = request.url.query
    if len(qs) > 300:
        return _secure(Response(status_code=414))
    if web:                                                   # nur die Sprache geht weiter
        lang = request.query_params.get("l", "")
        path, qs = f"/api/gast/web/{web}", (f"l={lang}" if re.fullmatch(r"[a-z-]{2,12}", lang) else "")
    try:
        status, ctype, data = await upstream(request.method if request.method != "HEAD" else "GET",
                                             path + (f"?{qs}" if qs else ""), body,
                                             request.headers.get("content-type"), ip)
    except (OSError, asyncio.TimeoutError, ValueError, IndexError):
        return _secure(JSONResponse({"detail": "offline"}, status_code=503))
    cache = path.startswith(("/media/", "/i18n/"))
    return _secure(Response(data, status_code=status, media_type=ctype.split(";")[0] if ctype else None,
                            headers={"Content-Type": ctype}), cache=cache, web=bool(web))


app = Starlette(routes=[Route("/{path:path}", handle, methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH"])])
