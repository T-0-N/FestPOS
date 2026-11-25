"""Payrexx-REST-API für die QR-Bestellung am Tisch (T22): Zahlungsseite (Gateway), Stand, Rückerstattung.

PAYREXX-UNGETESTET: Noch nie gegen ein echtes Payrexx-Konto gelaufen (Stand Version 1.0). Vor dem Einsatz mit dem
Testmodus von Payrexx prüfen: docs/testplan-payrexx.md. Danach PAYREXX_GEPRUEFT in app/payrexx_api.py auf True.

Übernommen aus der ersten Fassung von T13 (Zahlungslink/QR, Commit a44f44f), ergänzt um die Rückerstattung.
API-Schlüssel: verschlüsselt in der Datenbank (AES-GCM, Schlüssel aus CLUSTER_SECRET – auf allen Pi gleich), nie
zurückgegeben, nicht im Protokoll. Dokumentation: https://developers.payrexx.com (Gateway, Transaction, SignatureCheck).
Signatur: HMAC-SHA256 über die URL-codierten Parameter, Base64.

Mit einem Payrexx-Testkonto zu prüfen: Feldnamen der Rückerstattung und wo die Transaktion im Gateway steht.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import secrets
import socket
import urllib.error
import urllib.parse
import urllib.request

from .core import Config, log

# Stand der Prüfung mit einem echten Payrexx-Konto (Tap to Pay, QR-Bestellung, Rückerstattung). Solange False, zeigt
# die Verwaltung bei Payrexx einen Hinweis und fragt beim Einschalten nach (PAYREXX-UNGETESTET).
PAYREXX_GEPRUEFT = False

BASE = os.environ.get("FESTPOS_PAYREXX_BASE", "https://api.payrexx.com/v1.0")
TIMEOUT = 10.0
PAID = ("confirmed",)                       # Payrexx: nur das gilt als bezahlt
FAILED = ("declined", "cancelled", "expired", "error")


class PayrexxError(Exception):
    """code: offline (kein Internet – Anfrage sicher nicht angekommen), unclear (gesendet, Antwort verloren oder
    unlesbar – kann ausgeführt sein, T37), auth (Schlüssel/Instanz falsch), http (Payrexx lehnt ab), refused"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# -- Schlüssel verschlüsselt ablegen ------------------------------------------------------------------------------
def _box() -> bytes:
    return hashlib.sha256(b"festpos-payrexx\0" + (Config.cluster_secret or "").encode()).digest()


def seal(key: str, purpose: str = "gast_key_enc") -> str:
    """Payrexx-Schlüssel (bzw. Tunnel-Token, purpose gast_tunnel_enc) verschlüsselt – gemeinsames Verfahren
    (secretbox, T-0-N/FestPOS#8). Solange nicht alle Pi diese Version haben, im bisherigen Verfahren (das können
    auch ältere Pi lesen); migrate() schreibt es danach um."""
    from . import secretbox
    if not secretbox.may_seal():
        return seal_legacy(key)
    return secretbox.seal(purpose, key)


def seal_legacy(key: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    return base64.b64encode(nonce + AESGCM(_box()).encrypt(nonce, key.encode(), b"payrexx")).decode()


def unseal(value: str, purpose: str = "gast_key_enc") -> str | None:
    """Neues Verfahren («enc1:…») oder das bisherige (bis die Werte umgeschrieben sind)."""
    from . import secretbox
    return secretbox.unseal(purpose, value) if secretbox.sealed(value) else unseal_legacy(value)


def unseal_legacy(value: str) -> str | None:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        raw = base64.b64decode(value)
        return AESGCM(_box()).decrypt(raw[:12], raw[12:], b"payrexx").decode()
    except (ValueError, InvalidTag):
        return None


# -- Aufrufe ----------------------------------------------------------------------------------------------------
def _sign(key: str, params: list[tuple[str, str]]) -> str:
    q = urllib.parse.urlencode(params)
    return base64.b64encode(hmac.new(key.encode(), q.encode(), hashlib.sha256).digest()).decode()


def _call(method: str, path: str, instance: str, key: str, params: list[tuple[str, str]] | None = None) -> dict:
    params = list(params or [])
    sig = _sign(key, params)
    url = f"{BASE}/{path}?instance={urllib.parse.quote(instance)}"
    if method == "POST":
        body = urllib.parse.urlencode(params + [("ApiSignature", sig)]).encode()
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    else:
        req = urllib.request.Request(f"{url}&ApiSignature={urllib.parse.quote(sig)}", method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:        # noqa: S310 – feste https-Adresse
            data = json.loads(r.read(1024 * 1024) or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read(4096) or b"{}").get("message") or ""
        except ValueError:
            msg = ""
        log.warning("Payrexx %s %s: HTTP %s %s", method, path, e.code, str(msg)[:120])
        if e.code >= 500:                     # Server/Proxy-Fehler: die Anfrage kann ausgeführt sein (T37)
            raise PayrexxError("unclear") from None
        raise PayrexxError("auth" if e.code in (401, 403) else "http") from None
    except urllib.error.URLError as e:
        raise PayrexxError(_lost(e.reason)) from None
    except (OSError, http.client.HTTPException, ValueError) as e:
        raise PayrexxError(_lost(e)) from None
    if data.get("status") != "success":
        raise PayrexxError("refused")
    return data


def _lost(e) -> str:
    """offline: sicher nicht angekommen (Name nicht aufgelöst, Verbindung abgewiesen, kein Netz). Sonst unclear:
    die Anfrage kann bei Payrexx angekommen und ausgeführt sein (Timeout, Verbindung abgerissen, Antwort unlesbar)."""
    if isinstance(e, (socket.gaierror, ConnectionRefusedError)):
        return "offline"
    if isinstance(e, OSError) and not isinstance(e, (TimeoutError, ConnectionResetError, BrokenPipeError)) \
            and getattr(e, "errno", None) in (101, 113):          # ENETUNREACH, EHOSTUNREACH
        return "offline"
    if isinstance(e, str):                                       # URLError mit Text (z.B. Proxy): vor dem Senden
        return "offline"
    return "unclear"


def _first(d):
    return d[0] if isinstance(d, list) and d else d


def check_connection(instance: str, key: str) -> None:
    _call("GET", "SignatureCheck/", instance, key)


def create_gateway(instance: str, key: str, *, amount: int, reference: str, purpose: str, success_url: str,
                   failed_url: str, cancel_url: str, lang: str = "de") -> dict:
    """Zahlungsseite anlegen -> {id, link}. amount in Rappen."""
    params = [("amount", str(amount)), ("currency", "CHF"), ("referenceId", reference), ("purpose", purpose),
              ("successRedirectUrl", success_url), ("failedRedirectUrl", failed_url),
              ("cancelRedirectUrl", cancel_url), ("language", lang if lang in ("de", "fr", "it", "en") else "de"),
              ("skipResultPage", "1"), ("validity", "15")]
    g = _first(_call("POST", "Gateway/", instance, key, params)["data"])
    return {"id": int(g["id"]), "link": str(g["link"])}


def gateway_status(instance: str, key: str, gid: int) -> dict:
    """-> {status, transaction (id), method}. Bezahlt nur bei status «confirmed»."""
    g = _first(_call("GET", f"Gateway/{int(gid)}/", instance, key)["data"]) or {}
    tx = None
    method = None
    for inv in g.get("invoices") or []:
        for t in inv.get("transactions") or []:
            if str(t.get("status")) in PAID:
                tx = t.get("id") or t.get("uuid")
                method = (t.get("payment") or {}).get("brand") or t.get("pspId")
    return {"status": str(g.get("status") or ""), "transaction": str(tx) if tx is not None else None,
            "method": str(method) if method else None}


def delete_gateway(instance: str, key: str, gid: int) -> None:
    _call("DELETE", f"Gateway/{int(gid)}/", instance, key)


def refund(instance: str, key: str, transaction: str, amount: int) -> dict:
    """Ganz oder teilweise zurückerstatten (amount in Rappen) -> Antwort von Payrexx."""
    d = _call("POST", f"Transaction/{urllib.parse.quote(str(transaction))}/refund", instance, key,
              [("amount", str(int(amount)))])
    return _first(d.get("data")) or {}
