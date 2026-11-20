"""Signierte Update-Pakete prüfen (Version 2026.10.10-26).

Ein Update besteht aus zwei Dateien: dem ZIP mit dem Projektordner (festpos.zip) und der Signatur dazu
(festpos.zip.sig). Signiert wird am PC mit dem eigenen Update-Schlüssel – mit dem ssh-keygen, das in Windows,
macOS und Linux schon dabei ist:

    ssh-keygen -Y sign -f E:\\festpos-update -n festpos-update festpos.zip      -> festpos.zip.sig

Die Pi kennen nur den öffentlichen Teil (festpos-update.pub auf der Boot-Partition, nur für root lesbar). Ohne
gültige Signatur dieses Schlüssels wird nichts eingespielt – egal, wie das Paket auf den Pi kam (Verwaltung,
anderer Pi, USB-Stick). Ein gestohlenes Admin-Passwort genügt also nicht, um eigenen Code auf die Pi zu bringen.

Benutzt von der App (Vorprüfung beim Hochladen, für eine schnelle Rückmeldung) und vom root-Helfer festpos-update
(die eigentliche Prüfung vor dem Einspielen). Darum nur Standardbibliothek und cryptography.

Format der Signatur: OpenSSH «SSHSIG» (PROTOCOL.sshsig), nur Ed25519, Hash sha512 oder sha256.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
import posixpath
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path

NAMESPACE = "festpos-update"               # ssh-keygen -n …: eine Signatur für etwas anderes gilt hier nicht
KEY_GLOB = "festpos-update*.pub"           # auf der Boot-Partition (mehrere Schlüssel möglich: Wechsel)
MB = 1024 * 1024
MAX_ZIP = 200 * MB                         # heute ~45 MB (mit den Python-Paketen und rqlite)
MAX_UNPACKED = 400 * MB
MAX_FILES = 5000
MAX_SIG = 8 * 1024
MAX_KEYFILE = 64 * 1024
# Versionen: ab 1.0 MAJOR.MINOR.PATCH, Testversionen mit -rc.N (1.1.0-rc.1 ist älter als 1.1.0). Das frühere Format
# JJJJ.M.T-N (bis 2026.10.10-103) wird weiter gelesen und ist älter als jede Version ab 1.0 – so nehmen bestehende
# Pi das Update auf 1.0 an.
_N = r"(?:0|[1-9]\d{0,2})"                    # Zahl ohne führende Null
VERSION_RE = re.compile(r"^(?:(?P<y>\d{4})\.(?P<mo>\d{1,2})\.(?P<d>\d{1,2})-(?P<n>\d{1,4})"
                        rf"|(?P<major>{_N})\.(?P<minor>{_N})\.(?P<patch>{_N})(?:-rc\.(?P<rc>[1-9]\d{{0,2}}))?)$")
NEEDED = ("VERSION", "deploy/update.sh", "deploy/install-lib.sh", "app/main.py")
_ARMOR_BEGIN, _ARMOR_END = "-----BEGIN SSH SIGNATURE-----", "-----END SSH SIGNATURE-----"
_HASHES = {"sha256": hashlib.sha256, "sha512": hashlib.sha512}


class ReleaseError(Exception):
    """Paket abgelehnt. code -> Text «err.update.<code>» in der Sprachdatei (Werte in kw)."""

    def __init__(self, code: str, **kw):
        super().__init__(code if not kw else f"{code} {kw}")
        self.code, self.kw = code, kw


@dataclass(frozen=True)
class Key:
    blob: bytes                # öffentlicher Schlüssel im SSH-Format (Typ + 32 Byte)
    raw: bytes                 # die 32 Byte Ed25519
    comment: str
    fp: str                    # «SHA256:…» wie ssh-keygen -l
    file: str = ""

    @property
    def line(self) -> str:
        return f"ssh-ed25519 {base64.b64encode(self.blob).decode()} {self.comment}".strip()

    def public(self) -> dict:
        return {"fp": self.fp, "comment": self.comment, "file": self.file, "line": self.line}


# -- SSH-Datenformat: «string» = uint32 Länge + Bytes -------------------------------------------------------
def _string(buf: bytes, off: int) -> tuple[bytes, int]:
    if off + 4 > len(buf):
        raise ValueError("kurz")
    n = int.from_bytes(buf[off:off + 4], "big")
    if n > len(buf) - off - 4:
        raise ValueError("Länge")
    return buf[off + 4:off + 4 + n], off + 4 + n


def _put(b: bytes) -> bytes:
    return len(b).to_bytes(4, "big") + b


def fingerprint(blob: bytes) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def _ed25519_raw(blob: bytes) -> bytes:
    kind, off = _string(blob, 0)
    raw, off = _string(blob, off)
    if kind != b"ssh-ed25519" or len(raw) != 32 or off != len(blob):
        raise ValueError("kein Ed25519-Schlüssel")
    return raw


# -- Schlüssel ----------------------------------------------------------------------------------------------
def parse_key_line(line: str, file: str = "") -> Key:
    """«ssh-ed25519 AAAA… Kommentar» (Inhalt von festpos-update.pub). ValueError, wenn es keiner ist."""
    parts = line.strip().split(None, 2)
    if len(parts) < 2:
        raise ValueError("unvollständig")
    if parts[0] != "ssh-ed25519":
        raise ValueError(f"Typ {parts[0][:20]} – nur ssh-ed25519 (ssh-keygen -t ed25519)")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError("kein gültiger Schlüssel") from e
    raw = _ed25519_raw(blob)
    comment = re.sub(r"[\x00-\x1f\x7f]", "", parts[2] if len(parts) > 2 else "").strip()[:80]
    return Key(blob, raw, comment, fingerprint(blob), file)


def parse_keys(text: str, file: str = "") -> tuple[list[Key], list[str]]:
    keys, problems = [], []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            keys.append(parse_key_line(line, file))
        except ValueError as e:
            problems.append(f"{file or '?'} Zeile {n}: {e}")
    return keys, problems


def load_keys(directory: str | Path) -> tuple[list[Key], list[str]]:
    """Alle festpos-update*.pub eines Ordners (die Boot-Partition). Doppelte zählen einmal."""
    keys: list[Key] = []
    problems: list[str] = []
    for p in sorted(Path(directory).glob(KEY_GLOB)):
        try:
            data = read_regular(p, MAX_KEYFILE)
        except (OSError, ReleaseError) as e:
            problems.append(f"{p.name}: nicht lesbar ({e})")
            continue
        found, bad = parse_keys(data.decode("utf-8", "replace"), p.name)
        problems += bad
        keys += [k for k in found if k.blob not in {x.blob for x in keys}]
    return keys, problems


def keys_from_public(items: list[dict]) -> list[Key]:
    """Schlüssel aus der Liste, die festpos-update für die App veröffentlicht (keys.json)."""
    out = []
    for it in items or []:
        try:
            out.append(parse_key_line(str(it.get("line") or ""), str(it.get("file") or "")))
        except (ValueError, AttributeError):
            continue
    return out


# -- Signatur -------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Signature:
    key_blob: bytes
    namespace: str
    reserved: bytes
    hash_alg: str
    sig: bytes


def parse_signature(data: bytes) -> Signature:
    """Inhalt einer .sig-Datei von «ssh-keygen -Y sign». ReleaseError("sig_format"), wenn sie das nicht ist."""
    if len(data) > MAX_SIG:
        raise ReleaseError("sig_format")
    try:
        text = data.decode("ascii").strip().replace("\r\n", "\n")
    except UnicodeDecodeError as e:
        raise ReleaseError("sig_format") from e
    lines = text.split("\n")
    if len(lines) < 3 or lines[0].strip() != _ARMOR_BEGIN or lines[-1].strip() != _ARMOR_END:
        raise ReleaseError("sig_format")
    try:
        blob = base64.b64decode("".join(x.strip() for x in lines[1:-1]), validate=True)
        if blob[:6] != b"SSHSIG" or int.from_bytes(blob[6:10], "big") != 1:
            raise ValueError("Kopf")
        off = 10
        key_blob, off = _string(blob, off)
        ns, off = _string(blob, off)
        reserved, off = _string(blob, off)
        alg, off = _string(blob, off)
        sigblob, off = _string(blob, off)
        if off != len(blob):
            raise ValueError("Rest")
        sig_kind, o2 = _string(sigblob, 0)
        sig, o2 = _string(sigblob, o2)
        if sig_kind != b"ssh-ed25519" or len(sig) != 64 or o2 != len(sigblob):
            raise ValueError("kein Ed25519")
        _ed25519_raw(key_blob)
        return Signature(key_blob, ns.decode("utf-8"), reserved, alg.decode("ascii"), sig)
    except (ValueError, binascii.Error, UnicodeDecodeError) as e:
        raise ReleaseError("sig_format") from e


def digest_file(path: str | Path, alg: str) -> bytes:
    h = _HASHES[alg]()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.digest()


def signed_data(namespace: str, reserved: bytes, alg: str, digest: bytes) -> bytes:
    return b"SSHSIG" + _put(namespace.encode()) + _put(reserved) + _put(alg.encode()) + _put(digest)


def verify(path: str | Path, sig_data: bytes, keys: list[Key], namespace: str = NAMESPACE) -> Key:
    """Prüft die Signatur der Datei. -> der Schlüssel, der unterschrieben hat. Sonst ReleaseError."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    s = parse_signature(sig_data)
    if s.namespace != namespace:
        raise ReleaseError("sig_namespace", namespace=s.namespace[:40])
    if s.hash_alg not in _HASHES:
        raise ReleaseError("sig_format")
    if not keys:
        raise ReleaseError("no_key")
    key = next((k for k in keys if k.blob == s.key_blob), None)
    if key is None:
        raise ReleaseError("unknown_key", fp=fingerprint(s.key_blob))
    try:
        Ed25519PublicKey.from_public_bytes(key.raw).verify(
            s.sig, signed_data(s.namespace, s.reserved, s.hash_alg, digest_file(path, s.hash_alg)))
    except InvalidSignature as e:
        raise ReleaseError("bad_signature") from e
    return key


def sign(path: str | Path, private_key, namespace: str = NAMESPACE, hash_alg: str = "sha512") -> bytes:
    """Wie «ssh-keygen -Y sign» (für Tests und Werkzeuge). private_key: cryptography Ed25519PrivateKey."""
    from cryptography.hazmat.primitives import serialization
    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key_blob = _put(b"ssh-ed25519") + _put(raw)
    sig = private_key.sign(signed_data(namespace, b"", hash_alg, digest_file(path, hash_alg)))
    blob = (b"SSHSIG" + (1).to_bytes(4, "big") + _put(key_blob) + _put(namespace.encode()) + _put(b"")
            + _put(hash_alg.encode()) + _put(_put(b"ssh-ed25519") + _put(sig)))
    b64 = base64.b64encode(blob).decode()
    return ("\n".join([_ARMOR_BEGIN, *[b64[i:i + 70] for i in range(0, len(b64), 70)], _ARMOR_END]) + "\n").encode()


def public_line(private_key, comment: str = "FestPOS Update") -> str:
    from cryptography.hazmat.primitives import serialization
    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return f"ssh-ed25519 {base64.b64encode(_put(b'ssh-ed25519') + _put(raw)).decode()} {comment}"


# -- Versionen ----------------------------------------------------------------------------------------------
def version_key(v: str | None) -> tuple[int, ...] | None:
    """Sortierschlüssel: (0, J, M, T, N, 0) für das frühere Format, (1, MAJOR, MINOR, PATCH, fertig, rc) ab 1.0 –
    fertig ist 0 für eine Testversion (-rc.N) und 1 für die fertige Version. None: kein gültiges Format."""
    m = VERSION_RE.match((v or "").strip())
    if not m:
        return None
    if m["y"]:
        return (0, int(m["y"]), int(m["mo"]), int(m["d"]), int(m["n"]), 0)
    rc = m["rc"]
    return (1, int(m["major"]), int(m["minor"]), int(m["patch"]), 0 if rc else 1, int(rc or 0))


def is_prerelease(v: str | None) -> bool:
    """Testversion (1.1.0-rc.1)?"""
    m = VERSION_RE.match((v or "").strip())
    return bool(m and m["rc"])


def newer(a: str | None, b: str | None) -> bool:
    """Ist Version a neuer als b? Eine unlesbare Version a ist nie neuer; eine unlesbare b ist älter als alles."""
    ka, kb = version_key(a), version_key(b)
    if ka is None:
        return False
    return kb is None or ka > kb


# -- Dateien sicher lesen (auch als root in Ordnern, die einem anderen Benutzer gehören) ---------------------------
def read_regular(path: str | Path, limit: int) -> bytes:
    """Nur eine normale Datei (kein Symlink, keine Pipe, kein Gerät), höchstens limit Bytes."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ReleaseError("not_a_file")
        if st.st_size > limit:
            raise ReleaseError("too_large", mb=limit // MB or 1)
        out = bytearray()
        while len(out) <= limit:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            out += chunk
        if len(out) > limit:
            raise ReleaseError("too_large", mb=limit // MB or 1)
        return bytes(out)
    finally:
        os.close(fd)


def copy_regular(src: str | Path, dst: str | Path, limit: int = MAX_ZIP) -> str:
    """Normale Datei nach dst kopieren (dst wird neu angelegt) -> SHA-256 (hex) der Kopie."""
    fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    h = hashlib.sha256()
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ReleaseError("not_a_file")
        if st.st_size > limit:
            raise ReleaseError("too_large", mb=limit // MB)
        out = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        n = 0
        try:
            while chunk := os.read(fd, 1 << 20):
                n += len(chunk)
                if n > limit:
                    raise ReleaseError("too_large", mb=limit // MB)
                h.update(chunk)
                os.write(out, chunk)
        finally:
            os.close(out)
    finally:
        os.close(fd)
    return h.hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# -- ZIP ------------------------------------------------------------------------------------------------------
def _safe_name(name: str) -> bool:
    if not name or name.startswith("/") or "\\" in name or "\x00" in name or ":" in name.split("/", 1)[0]:
        return False
    parts = name.rstrip("/").split("/")
    return all(p not in ("", ".", "..") for p in parts) and posixpath.normpath(name.rstrip("/")) == name.rstrip("/")


def _is_link(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def windows_line_endings(name: str, data: bytes) -> bool:
    """Textdatei mit Windows-Zeilenenden (CRLF)? Damit laufen auf dem Pi Shell-Skripte und systemd-Dateien nicht
    («set: pipefail\\r: invalid option name»). Passiert beim Bauen unter Windows mit core.autocrlf=true.
    Ausgenommen: CSV-Vorlagen (für Excel absichtlich CRLF) und Binärdateien."""
    return not name.endswith(".csv") and b"\0" not in data[:8192] and b"\r\n" in data


def inspect_zip(path: str | Path) -> dict:
    """Aufbau prüfen: ein Projektordner (festpos/…) mit VERSION, deploy/update.sh, app/main.py; keine Pfade
    nach aussen, keine Verknüpfungen, Grösse begrenzt. -> {"version", "root", "files", "size"}"""
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise ReleaseError("zip_bad") from e
    with z:
        infos = z.infolist()
        if not infos or len(infos) > MAX_FILES:
            raise ReleaseError("zip_bad")
        total = 0
        names = set()
        for i in infos:
            if not _safe_name(i.filename) or _is_link(i):
                raise ReleaseError("zip_unsafe", name=i.filename[:80])
            total += i.file_size
            names.add(i.filename.rstrip("/"))
        if total > MAX_UNPACKED:
            raise ReleaseError("too_large", mb=MAX_UNPACKED // MB)
        for i in infos:                                     # Windows-Zeilenenden: lieber gar nicht verteilen
            if i.is_dir():
                continue
            with z.open(i) as f:
                head = f.read(8192)
                if b"\0" in head:
                    continue                                # Binärdatei (Python-Pakete, Bilder …): nicht ganz lesen
                if windows_line_endings(i.filename, head + f.read()):
                    raise ReleaseError("crlf", name=i.filename[:80])
        tops = {n.split("/", 1)[0] for n in names}
        root = ""
        if "VERSION" not in names and len(tops) == 1:
            root = next(iter(tops)) + "/"
        missing = [n for n in NEEDED if root + n not in names]
        if missing:
            raise ReleaseError("zip_incomplete", missing=", ".join(missing))
        vinfo = z.getinfo(root + "VERSION")
        if vinfo.file_size > 100:
            raise ReleaseError("zip_incomplete", missing="VERSION")
        version = z.read(vinfo).decode("utf-8", "replace").strip()
        if not version_key(version):
            raise ReleaseError("version_bad", version=version[:30])
        return {"version": version, "root": root.rstrip("/"), "files": len(infos), "size": total}


def extract_zip(path: str | Path, dest: str | Path) -> Path:
    """Geprüftes ZIP in einen LEEREN Ordner auspacken -> Projektordner. Jede Datei wird neu angelegt (nie
    überschrieben, nie über eine Verknüpfung); Ausführungsrechte bleiben, sonst rw-r--r--."""
    info = inspect_zip(path)
    dest = Path(dest)
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(dest.iterdir()):
        raise ReleaseError("zip_bad")
    written = 0
    with zipfile.ZipFile(path) as z:
        for i in z.infolist():
            target = dest / i.filename.rstrip("/")
            if i.is_dir():
                target.mkdir(mode=0o755, parents=True, exist_ok=True)
                continue
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            mode = 0o755 if (i.external_attr >> 16) & 0o111 else 0o644
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
            try:
                with z.open(i) as src:
                    while chunk := src.read(1 << 20):
                        written += len(chunk)
                        if written > MAX_UNPACKED:            # Angabe im ZIP gelogen (ZIP-Bombe)
                            raise ReleaseError("too_large", mb=MAX_UNPACKED // MB)
                        os.write(fd, chunk)
            finally:
                os.close(fd)
            os.chmod(target, mode)
    return dest / info["root"] if info["root"] else dest


def check_bundle(zip_path: str | Path, sig_data: bytes, keys: list[Key]) -> dict:
    """Signatur und Aufbau. -> {"version", "signer": {"fp", "comment"}, "sha256", "size"}"""
    key = verify(zip_path, sig_data, keys)
    info = inspect_zip(zip_path)
    return {"version": info["version"], "signer": {"fp": key.fp, "comment": key.comment},
            "sha256": sha256_file(zip_path), "size": os.path.getsize(zip_path)}
