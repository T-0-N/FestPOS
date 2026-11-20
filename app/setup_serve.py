"""Ersteinrichtung (T11) starten: https auf Port 443 mit einem vorläufigen, selbst signierten Zertifikat (erzeugt
festpos-node beim Start im Ersteinrichtungs-Modus; Fingerabdruck auf dem Bildschirm am Pi und in
festpos-einrichtung.txt) und http auf Port 80, das nur auf https umleitet. Fehlt das Zertifikat, läuft die
Einrichtung als Rückfall über http.

  python -m app.setup_serve                (festpos-einrichtung.service)
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import uvicorn

CERT = Path(os.environ.get("FESTPOS_SETUP_CERT", "/var/lib/festpos/einrichtung/tls/cert.pem"))
KEY = Path(os.environ.get("FESTPOS_SETUP_KEY", "/var/lib/festpos/einrichtung/tls/key.pem"))
HTTP_PORT = int(os.environ.get("FESTPOS_SETUP_HTTP_PORT", "80"))
HTTPS_PORT = int(os.environ.get("FESTPOS_SETUP_HTTPS_PORT", "443"))


async def redirect(scope, receive, send):
    """Port 80: alles auf https umleiten (gleicher Host, Port 443). /api/health bleibt für Prüfungen erreichbar."""
    if scope["type"] != "http":
        return
    path = scope.get("path") or "/"
    if path == "/api/health":
        body = b'{"setup":true,"https":true}'
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
        return
    host = dict(scope.get("headers") or []).get(b"host", b"festpos.local").decode("latin-1").split(":")[0]
    port = "" if HTTPS_PORT == 443 else f":{HTTPS_PORT}"
    loc = f"https://{host}{port}{path}"
    await send({"type": "http.response.start", "status": 308,
                "headers": [(b"location", loc.encode("latin-1")), (b"content-length", b"0")]})
    await send({"type": "http.response.body", "body": b""})


def servers() -> list[uvicorn.Server]:
    from .setup_mode import app
    if CERT.is_file() and KEY.is_file():
        return [uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=HTTPS_PORT, ssl_certfile=str(CERT),
                                              ssl_keyfile=str(KEY), access_log=False)),
                uvicorn.Server(uvicorn.Config(redirect, host="0.0.0.0", port=HTTP_PORT, access_log=False))]
    return [uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=HTTP_PORT, access_log=False))]   # Rückfall


async def main() -> None:
    await asyncio.gather(*(s.serve() for s in servers()))


if __name__ == "__main__":
    asyncio.run(main())
