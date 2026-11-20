"""Eigene CA und Server-Zertifikat für Tests mit mehreren «Pi» über HTTPS (127.0.0.1–3), wie make-certs.sh."""
from __future__ import annotations

import datetime as dt
import ipaddress
from pathlib import Path


def make_certs(d: Path) -> tuple[Path, Path, Path]:
    """Eigene CA und ein Server-Zertifikat für 127.0.0.1–3 (wie make-certs.sh für die Pi-Adressen)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = dt.datetime.now(dt.timezone.utc)

    def name(cn):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca = (x509.CertificateBuilder().subject_name(name("Messung CA")).issuer_name(name("Messung CA"))
          .public_key(ca_key.public_key()).serial_number(1).not_valid_before(now - dt.timedelta(days=1))
          .not_valid_after(now + dt.timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                       key_encipherment=False, data_encipherment=False, key_agreement=False,
                                       encipher_only=False, decipher_only=False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    san = x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(f"127.0.0.{i}")) for i in (1, 2, 3)])
    srv = (x509.CertificateBuilder().subject_name(name("festpos")).issuer_name(ca.subject).public_key(key.public_key())
           .serial_number(2).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=2))
           .add_extension(san, critical=False)
           .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
           .sign(ca_key, hashes.SHA256()))
    paths = d / "ca.crt", d / "server.crt", d / "server.key"
    paths[0].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths[1].write_bytes(srv.public_bytes(serialization.Encoding.PEM))
    paths[2].write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return paths
