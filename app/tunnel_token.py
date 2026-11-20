"""Token des Cloudflare Tunnels zur QR-Bestellung (T22) prüfen – gemeinsam für die App und den root-Helfer
festpos-update (deshalb nur Standardbibliothek).

Cloudflare zeigt den Token beim Anlegen des Tunnels an: base64 von {"a": Konto, "t": Tunnel-ID, "s": Geheimnis}.
Geprüft wird der Aufbau, nicht ob Cloudflare ihn kennt – das zeigt erst der laufende Tunnel. Der Token kommt so nie
ungeprüft in eine Datei (keine Zeilenumbrüche, keine Sonderzeichen für die Umgebungsdatei von systemd).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re

TOKEN_RE = re.compile(r"^[A-Za-z0-9+/_-]{60,2000}={0,2}$")
ACCOUNT_RE = re.compile(r"^[0-9a-f]{32}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def parse(token: object) -> dict | None:
    """-> {"account", "tunnel"} bei einem gültig aufgebauten Token, sonst None."""
    if not isinstance(token, str):
        return None
    tok = token.strip()
    if not TOKEN_RE.match(tok):
        return None
    std = tok.rstrip("=").replace("-", "+").replace("_", "/")
    try:
        data = json.loads(base64.b64decode(std + "=" * (-len(std) % 4), validate=True))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    a, t, s = data.get("a"), data.get("t"), data.get("s")
    if not (isinstance(a, str) and ACCOUNT_RE.match(a) and isinstance(t, str) and UUID_RE.match(t)
            and isinstance(s, str) and 16 <= len(s) <= 400):
        return None
    return {"account": a, "tunnel": t}


def fingerprint(token: str) -> str:
    """Kurzer Fingerabdruck (verrät den Token nicht): Die App vergleicht damit, ob der Pi den gewünschten Token hat."""
    return hashlib.sha256(b"festpos-tunnel\0" + token.strip().encode()).hexdigest()[:16]
