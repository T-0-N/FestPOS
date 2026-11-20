"""Zertifikate für einen neuen Pi erzeugen – dieselbe Logik wie deploy/make-certs.sh, aber in Python (T11: der Pi
richtet sich im Browser selbst ein, ohne PC-Werkzeuge).

  - Server-CA mit Namensbeschränkung: darf nur das Festnetz (NET_PREFIX/NET_CIDR), 127.0.0.1, festpos.local und
    localhost unterschreiben – selbst mit dem Schlüssel lassen sich keine fremden Webseiten fälschen.
  - Geräte-CA: stellt die Gerätezertifikate der Handys aus (liegt auf dem Pi).
  - Server-Zertifikat für VIP, die Adressen der drei Pi, 127.0.0.1, festpos.local, localhost; serverAuth und
    clientAuth (RAFT_TLS zwischen den Pi).
Läuft als root (festpos-wiederherstellen) – braucht nur «cryptography»."""
from __future__ import annotations

import datetime as dt
import ipaddress

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_DAYS = 10950                       # 30 Jahre wie make-certs.sh


def _pem_key(k) -> bytes:
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def _pem(cert) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "FestPOS")])


def _ca(cn: str, constraints: x509.NameConstraints | None, now: dt.datetime):
    key = ec.generate_private_key(ec.SECP256R1())
    b = (x509.CertificateBuilder().subject_name(_name(cn)).issuer_name(_name(cn)).public_key(key.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
         .not_valid_after(now + dt.timedelta(days=CA_DAYS))
         .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
         .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                      data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                                      encipher_only=False, decipher_only=False), critical=True)
         .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False))
    if constraints:
        b = b.add_extension(constraints, critical=True)
    return key, b.sign(key, hashes.SHA256())


def make_all(net_prefix: str, net_cidr: int, ips: list[str], year: int | None = None) -> dict[str, bytes]:
    """{ca.crt, ca.key, server.crt, server.key, device-ca.crt, device-ca.key} als PEM.
    ca.key gehört NICHT auf die Karte (nur einmal herunterladen und aufbewahren)."""
    now = dt.datetime.now(dt.timezone.utc)
    net = ipaddress.ip_network(f"{net_prefix}.0/{net_cidr}", strict=False)
    addrs = [ipaddress.ip_address(x) for x in ips]
    if any(a not in net for a in addrs):
        raise ValueError("ip_outside")
    constraints = x509.NameConstraints(
        permitted_subtrees=[x509.IPAddress(net), x509.IPAddress(ipaddress.ip_network("127.0.0.1/32")),
                            x509.DNSName("festpos.local"), x509.DNSName("localhost")], excluded_subtrees=None)
    ca_key, ca = _ca(f"FestPOS Lokale CA {year or now.year}", constraints, now)
    dca_key, dca = _ca("FestPOS Geraete-CA", None, now)
    srv_key = ec.generate_private_key(ec.SECP256R1())
    sans = [x509.IPAddress(a) for a in dict.fromkeys(addrs)] + [x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                                                                 x509.DNSName("festpos.local"), x509.DNSName("localhost")]
    srv = (x509.CertificateBuilder().subject_name(_name("festpos.local")).issuer_name(ca.subject)
           .public_key(srv_key.public_key()).serial_number(x509.random_serial_number())
           .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=CA_DAYS - 1))
           .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
           .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                        data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                        crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
           .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]),
                          critical=False)
           .add_extension(x509.SubjectAlternativeName(sans), critical=False)
           .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
           .sign(ca_key, hashes.SHA256()))
    return {"ca.crt": _pem(ca), "ca.key": _pem_key(ca_key), "server.crt": _pem(srv), "server.key": _pem_key(srv_key),
            "device-ca.crt": _pem(dca), "device-ca.key": _pem_key(dca_key)}


def fingerprint(pem: bytes) -> str:
    c = x509.load_pem_x509_certificate(pem)
    return c.fingerprint(hashes.SHA256()).hex(":").upper()
