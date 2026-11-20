"""Nur für Browser-Tests: die App so, als käme die Anfrage über den Handy-Port mit Gerätezertifikat oder von einem
anderen Rechner im Netz (nicht vom Pi selbst). Gestartet mit «uvicorn tests.asgi_testphone:app».

    X-Test-Cert: <Name>   -> selbst signiertes Gerätezertifikat mit diesem Namen (sonst setzt das app/serve.py beim TLS)
    X-Test-Client: <IP>   -> Absender-Adresse (sonst 127.0.0.1 = Bildschirm am Pi)
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.main import app as festpos


@lru_cache(maxsize=16)
def _der(name: str) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    subj = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subj).issuer_name(subj).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=900)).sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.DER)


async def app(scope, receive, send):
    if scope["type"] == "http":
        headers = dict(scope.get("headers") or [])
        name = headers.get(b"x-test-cert", b"").decode()
        if name:
            scope = {**scope, "extensions": {**(scope.get("extensions") or {}), "festpos.client_cert": _der(name)}}
        ip = headers.get(b"x-test-client", b"").decode()
        if ip:
            scope = {**scope, "client": (ip, 50000)}
    await festpos(scope, receive, send)
