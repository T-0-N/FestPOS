"""Zweiter Faktor für die Verwaltung: Code aus einer Authenticator-App (TOTP, RFC 6238) – T-0-N/FestPOS#9.

SHA-1, 6 Ziffern, 30 Sekunden (so können es alle verbreiteten Apps). Ein Code gilt einen Schritt vorher und nachher
(±30 s, Uhr des Pi oder des Handys etwas daneben) und nur einmal (gleicher Schritt nicht nochmals). Passt er erst mit
einer grösseren Abweichung, sagt check() das – dann stimmt die Uhr des Pi nicht (klare Meldung statt «falsch»).

Wiederherstellungs-Codes: 10 Stück «XXXX-XXXX», je einmal gültig, gespeichert nur als HMAC (mit eigenem Salz).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP, DIGITS, WINDOW = 30, 6, 1
FAR = 20                                            # bis ±10 Minuten nachsehen, nur um «Uhr falsch» zu erkennen
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
RECOVERY_COUNT = 10
_now = time.time                                    # Tests: Zeit verschieben


def new_secret() -> str:
    """Zufälliges Geheimnis (160 Bit), Base32 ohne «=» – so tippt man es auch von Hand in die App."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    s = secret.replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def code_at(secret: str, step: int) -> str:
    mac = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    n = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 10 ** DIGITS
    return str(n).zfill(DIGITS)


def check(secret: str, code: str, now: float | None = None, last_step: int = -1) -> tuple[str, int]:
    """-> ("ok", Schritt) | ("used", Schritt: schon benutzt) | ("clock", Abweichung in Schritten) | ("wrong", 0)."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return "wrong", 0
    cur = int((_now() if now is None else now) // STEP)
    for d in sorted(range(-WINDOW, WINDOW + 1), key=abs):
        if cur + d >= 0 and hmac.compare_digest(code_at(secret, cur + d), code):
            return ("used", cur + d) if cur + d <= last_step else ("ok", cur + d)
    for d in sorted(range(-FAR, FAR + 1), key=abs):
        if abs(d) > WINDOW and cur + d >= 0 and hmac.compare_digest(code_at(secret, cur + d), code):
            return "clock", d
    return "wrong", 0


def uri(secret: str, account: str, issuer: str = "FestPOS") -> str:
    """otpauth://-Adresse für den QR-Code der App."""
    label = quote(f"{issuer}:{account}", safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer, safe='')}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


# -- Wiederherstellungs-Codes ------------------------------------------------------------------------------------
def new_codes(n: int = RECOVERY_COUNT) -> list[str]:
    out = []
    for _ in range(n):
        c = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        out.append(f"{c[:4]}-{c[4:]}")
    return out


def _norm(code: str) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def code_hash(code: str, salt: str) -> str:
    return hmac.new(salt.encode(), _norm(code).encode(), hashlib.sha256).hexdigest()


def use_code(code: str, salt: str, hashes: list[str]) -> list[str] | None:
    """Wiederherstellungs-Code prüfen -> übrige Hashes (der benutzte fällt weg); None: falsch."""
    h = code_hash(code, salt)
    for i, x in enumerate(hashes):
        if hmac.compare_digest(h, x):
            return hashes[:i] + hashes[i + 1:]
    return None
