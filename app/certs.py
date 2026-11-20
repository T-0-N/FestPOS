"""Gerätezertifikate für die Handys (Client-Zertifikate, mTLS).

Eine eigene Geräte-CA (device-ca.crt/.key, erzeugt von deploy/make-certs.sh) signiert pro Handy ein
Zertifikat mit begrenzter Gültigkeit (Einstellung in der Verwaltung). Vor Ablauf zeigt die App eine
Warnung; das Handy kann sich mit seinem noch gültigen Zertifikat selbst ein neues holen.
Der Server verlangt auf Port 443 ein Zertifikat dieser CA – ohne kommt keine Verbindung zustande.

Ausgabe als PKCS#12 (.p12) im "Legacy"-Format (3DES/SHA1): das versteht der Android-Zertifikatsimport
zuverlässig, das moderne AES-Format nicht auf allen Geräten.
"""
from __future__ import annotations

import datetime as dt
import secrets
import threading
import time
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DEFAULT_DAYS = 3650


def utc_today() -> dt.date:
    """Heutiges Datum in UTC – wie die Ablaufdaten der Zertifikate (sonst um Mitternacht einen Tag daneben)."""
    return dt.datetime.now(dt.timezone.utc).date()


def load_ca(cert_path: str | Path, key_path: str | Path):
    cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    key = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    return cert, key


def issue(ca_cert, ca_key, name: str, days: int = DEFAULT_DAYS,
          password: str | None = None) -> tuple[bytes, str, str, dt.datetime]:
    """-> (p12_bytes, import_kennwort, seriennummer_hex, gültig_bis). Ohne password ein 6-stelliges Kennwort zum
    Abtippen; über Fully (app/fully.py) ein langes, das niemand eingeben muss."""
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)       # Toleranz für falsche Uhren
    serial = x509.random_serial_number()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "FestPOS Gerät")])
    cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(ca_cert.subject)
            .public_key(key.public_key()).serial_number(serial)
            .not_valid_before(now).not_valid_after(now + dt.timedelta(days=days + 1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, content_commitment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    password = password or f"{secrets.randbelow(1_000_000):06d}"
    enc = (serialization.PrivateFormat.PKCS12.encryption_builder()
           .kdf_rounds(50000)
           .key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC)
           .hmac_hash(hashes.SHA1())
           .build(password.encode()))
    until = cert.not_valid_after_utc
    # Anzeigename in Android: mit Ablaufmonat, damit altes und neues Zertifikat unterscheidbar sind
    friendly = f"{name} (bis {until:%m.%Y})"
    p12 = pkcs12.serialize_key_and_certificates(friendly.encode(), key, cert, [ca_cert], enc)
    return p12, password, format(serial, "x"), until


def describe(der: bytes) -> dict:
    """Name, Seriennummer und Ablauf aus einem Client-Zertifikat (DER)."""
    cert = x509.load_der_x509_certificate(der)
    cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    until = cert.not_valid_after_utc
    days_left = (until.date() - utc_today()).days            # ganze Kalendertage, wie in der Verwaltung
    return {"name": cn[0].value if cn else "", "serial": format(cert.serial_number, "x"),
            "expires_at": until.strftime("%Y-%m-%d"), "days_left": days_left}


class OneTimeDownloads:
    """Ausgestellte .p12 liegen nur kurz im Speicher und sind genau einmal abrufbar (Link im QR-Code)."""

    TTL = 600.0

    def __init__(self):
        self._d: dict[str, tuple[float, bytes, str]] = {}
        self._lock = threading.Lock()

    def put(self, data: bytes, filename: str) -> str:
        token = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            self._d = {k: v for k, v in self._d.items() if now - v[0] < self.TTL}
            self._d[token] = (now, data, filename)
        return token

    def pending(self, token: str) -> bool:
        """Noch nicht abgeholt (und nicht abgelaufen)?"""
        with self._lock:
            v = self._d.get(token)
        return v is not None and time.monotonic() - v[0] <= self.TTL

    def take(self, token: str) -> tuple[bytes, str] | None:
        with self._lock:
            v = self._d.pop(token, None)
        if v is None or time.monotonic() - v[0] > self.TTL:
            return None
        return v[1], v[2]
