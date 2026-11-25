"""Zertifikate für einen neuen Pi erzeugen – dieselbe Logik wie deploy/make-certs.sh, aber in Python (T11: der Pi
richtet sich im Browser selbst ein, ohne PC-Werkzeuge).

  - Server-CA mit Namensbeschränkung: darf nur das Festnetz (NET, z.B. 10.20.0.0/22), 127.0.0.1, festpos.local und
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


# -- Privaten Schlüssel mit Passwort verschlüsseln (ca.key bei der Ersteinrichtung) -------------------------------
# PKCS#8 «ENCRYPTED PRIVATE KEY» mit PBES2 (PBKDF2-HMAC-SHA256, AES-256-CBC) – liest OpenSSL (make-certs.sh fragt nach
# dem Passwort). cryptography selbst nimmt dafür nur 2048 Runden; für ein Passwort, das ein Mensch wählt, zu wenig.
PBKDF2_ROUNDS = 600_000
_OID = {"pbes2": "1.2.840.113549.1.5.13", "pbkdf2": "1.2.840.113549.1.5.12", "hmac_sha256": "1.2.840.113549.2.9",
        "aes256_cbc": "2.16.840.1.101.3.4.1.42"}


def _der(tag: int, body: bytes) -> bytes:
    n = len(body)
    size = bytes([n]) if n < 128 else bytes([0x80 | ((n.bit_length() + 7) // 8)]) + n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([tag]) + size + body


def _oid(dotted: str) -> bytes:
    a, b, *rest = (int(x) for x in dotted.split("."))
    out = bytearray([40 * a + b])
    for v in rest:
        chunk = [v & 0x7F]
        while v > 0x7F:
            v >>= 7
            chunk.insert(0, 0x80 | (v & 0x7F))
        out += bytes(chunk)
    return _der(0x06, bytes(out))


def _int(v: int) -> bytes:
    return _der(0x02, v.to_bytes(v.bit_length() // 8 + 1, "big"))


def encrypt_key_pem(pem: bytes, password: str, rounds: int = PBKDF2_ROUNDS) -> bytes:
    """Privater Schlüssel (PEM, unverschlüsselt) -> «ENCRYPTED PRIVATE KEY» (PKCS#8, PBES2)."""
    import base64
    import os

    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    key = serialization.load_pem_private_key(pem, password=None)
    plain = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    salt, iv = os.urandom(16), os.urandom(16)
    aes_key = PBKDF2HMAC(hashes.SHA256(), 32, salt, rounds).derive(password.encode())
    padder = padding.PKCS7(128).padder()
    enc = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).encryptor()
    data = enc.update(padder.update(plain) + padder.finalize()) + enc.finalize()
    prf = _der(0x30, _oid(_OID["hmac_sha256"]) + b"\x05\x00")
    kdf = _der(0x30, _oid(_OID["pbkdf2"]) + _der(0x30, _der(0x04, salt) + _int(rounds) + prf))
    scheme = _der(0x30, _oid(_OID["aes256_cbc"]) + _der(0x04, iv))
    alg = _der(0x30, _oid(_OID["pbes2"]) + _der(0x30, kdf + scheme))
    der = _der(0x30, alg + _der(0x04, data))
    b64 = base64.b64encode(der).decode()
    lines = "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64))
    return f"-----BEGIN ENCRYPTED PRIVATE KEY-----\n{lines}\n-----END ENCRYPTED PRIVATE KEY-----\n".encode()


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


def _server_ca(net: str, ips: list[str], now: dt.datetime, year: int | None):
    """Server-CA genau für dieses Netz (Name Constraint) -> (Schlüssel, Zertifikat, Adressen)."""
    n = ipaddress.ip_network(net, strict=False)
    addrs = [ipaddress.ip_address(x) for x in ips]
    if any(a not in n for a in addrs):
        raise ValueError("ip_outside")
    constraints = x509.NameConstraints(
        permitted_subtrees=[x509.IPAddress(n), x509.IPAddress(ipaddress.ip_network("127.0.0.1/32")),
                            x509.DNSName("festpos.local"), x509.DNSName("localhost")], excluded_subtrees=None)
    ca_key, ca = _ca(f"FestPOS Lokale CA {year or now.year}", constraints, now)
    return ca_key, ca, addrs


def make_all(net: str, ips: list[str], year: int | None = None) -> dict[str, bytes]:
    """{ca.crt, ca.key, server.crt, server.key, device-ca.crt, device-ca.key} als PEM. net: «10.20.0.0/22» – die
    Server-CA gilt genau für dieses Netz (T-0-N/FestPOS#2). ca.key gehört NICHT auf die Karte."""
    now = dt.datetime.now(dt.timezone.utc)
    ca_key, ca, addrs = _server_ca(net, ips, now, year)
    dca_key, dca = _ca("FestPOS Geraete-CA", None, now)
    srv_key = ec.generate_private_key(ec.SECP256R1())
    srv = _server_cert(ca_key, ca, srv_key.public_key(), addrs, now)
    return {"ca.crt": _pem(ca), "ca.key": _pem_key(ca_key), "server.crt": _pem(srv), "server.key": _pem_key(srv_key),
            "device-ca.crt": _pem(dca), "device-ca.key": _pem_key(dca_key)}


def for_move(net: str, ips: list[str], server_crt: bytes, year: int | None = None) -> dict[str, bytes]:
    """Netz umziehen (T-0-N/FestPOS#2): neue Server-CA für das neue Netz und ein Serverzertifikat für den BISHERIGEN
    Server-Schlüssel (aus server.crt) -> {ca.crt, ca.key, server.crt}. So bleibt server.key (und ein daraus
    abgeleitetes CLUSTER_SECRET) gleich; die Geräte-CA und die Gerätezertifikate der Handhelds auch."""
    now = dt.datetime.now(dt.timezone.utc)
    ca_key, ca, addrs = _server_ca(net, ips, now, year)
    pub = x509.load_pem_x509_certificate(server_crt).public_key()
    srv = _server_cert(ca_key, ca, pub, addrs, now)
    return {"ca.crt": _pem(ca), "ca.key": _pem_key(ca_key), "server.crt": _pem(srv)}


def _server_cert(ca_key, ca, public_key, addrs, now: dt.datetime):
    sans = [x509.IPAddress(a) for a in dict.fromkeys(addrs)] + [x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                                                                 x509.DNSName("festpos.local"), x509.DNSName("localhost")]
    return (x509.CertificateBuilder().subject_name(_name("festpos.local")).issuer_name(ca.subject)
           .public_key(public_key).serial_number(x509.random_serial_number())
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


def ca_nets(ca_pem: bytes) -> set[str] | None:
    """Netze, für die eine Server-CA gilt (Name Constraint) – None: ohne Beschränkung (älter) oder nicht lesbar."""
    try:
        ca = x509.load_pem_x509_certificate(ca_pem)
        nc = ca.extensions.get_extension_for_class(x509.NameConstraints).value
    except (ValueError, x509.ExtensionNotFound):
        return None
    nets = {str(x.value) for x in nc.permitted_subtrees or [] if isinstance(x, x509.IPAddress)}
    return {n for n in nets if n != "127.0.0.1/32"} or None


def other_net(ca_pem: bytes, net: str | None) -> bool:
    """Gilt die CA (einer Sicherung, einer Karte) für ein anderes Netz als net? (Netz umgezogen, T-0-N/FestPOS#2)"""
    nets = ca_nets(ca_pem)
    return bool(net and nets is not None and str(net) not in nets)


def fingerprint(pem: bytes) -> str:
    c = x509.load_pem_x509_certificate(pem)
    return c.fingerprint(hashes.SHA256()).hex(":").upper()
