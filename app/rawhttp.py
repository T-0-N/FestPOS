"""Kleiner HTTP-Baustein für Abfragen an Geräte im Festnetz (Fully Kiosk, Mobilfunk-Router).

Absichtlich ohne zusätzliche Bibliothek: eine Anfrage pro Verbindung («Connection: close»), Antwort begrenzt.
Das Zertifikat der Geräte ist selbst signiert – geprüft wird stattdessen sein Fingerabdruck (SHA-256), und zwar
von den Aufrufern, bevor ein Passwort hinausgeht.
"""
from __future__ import annotations

import asyncio
import hashlib
import ssl


def tls_client() -> ssl.SSLContext:
    """TLS ohne Prüfung der Zertifizierungsstelle – der Aufrufer vergleicht danach den Fingerabdruck."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def peer_pin(writer: asyncio.StreamWriter) -> str | None:
    """SHA-256-Fingerabdruck des Zertifikats, das die Gegenstelle gezeigt hat (hex, klein)."""
    ssl_obj = writer.get_extra_info("ssl_object")
    der = ssl_obj.getpeercert(binary_form=True) if ssl_obj else None
    return hashlib.sha256(der).hexdigest() if der else None


async def read_all(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, limit: int) -> bytes | None:
    """Alles bis zum Ende der Verbindung lesen. None = grösser als limit (das ist keine sinnvolle Antwort)."""
    await writer.drain()
    raw = bytearray()
    while len(raw) <= limit:
        chunk = await reader.read(65536)
        if not chunk:
            return bytes(raw)
        raw += chunk
    return None


def parse(raw: bytes) -> tuple[int, dict[str, str], bytes]:
    """HTTP-Antwort -> (Status, Kopfzeilen klein geschrieben, Inhalt). Versteht «chunked» und Content-Length."""
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    try:
        status = int(lines[0].split()[1])
    except (IndexError, ValueError):
        return 0, {}, b""
    headers = {}
    for line in lines[1:]:
        k, _, v = line.partition(":")
        headers[k.strip().lower()] = v.strip()
    if "chunked" in headers.get("transfer-encoding", "").lower():
        out, rest = bytearray(), body
        while rest:
            size_line, _, rest = rest.partition(b"\r\n")
            try:
                size = int(size_line.split(b";")[0], 16)
            except ValueError:
                break
            if size == 0:
                break
            out += rest[:size]
            rest = rest[size + 2:]
        body = bytes(out)
    elif headers.get("content-length", "").isdigit():
        body = body[:int(headers["content-length"])]
    return status, headers, body
