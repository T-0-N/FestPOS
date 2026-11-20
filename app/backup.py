"""Sicherung: verschlüsselt, mit allem drin, und automatisch auf einen USB-Stick (T8).

Jede Sicherung ist eine Datei «.festpos»: ein lesbarer Kopf (Pi, Zeitpunkt, Version – nichts Geheimes) und ein mit
AES-256-GCM verschlüsselter Inhalt (Schlüssel aus dem Passwort mit scrypt). Darin ein ZIP mit der ganzen Datenbank
samt Zugangsdaten, den Zertifikaten und Schlüsseln (festpos-tls), CLUSTER_SECRET und Notfall-Passwort. Damit läuft
auf einem neuen Pi alles wieder, ohne dass die Handys neu eingerichtet werden. Ein falsches Passwort oder eine
veränderte Datei fällt sicher auf; es wird nie etwas Halbes eingespielt. Ältere Sicherungen (unverschlüsselte
SQLite-Datei ohne Geheimnisse) lassen sich weiterhin einspielen.

Passwort: die automatische Sicherung benutzt das Sicherungs-Passwort, das der Pi bei der Einrichtung erzeugt
(Einstellung backup_password, ein Geheimnis wie die anderen in SECRET_KEYS); heruntergeladene Sicherungen ein Passwort,
das der Admin selbst wählt.

Automatisch: Der root-Helfer festpos-sicherung (Zeitgeber, alle 15 Minuten) sucht einen USB-Stick mit dem Ordner
«festpos-sicherung», schreibt eine Sicherung hinein und behält dort die letzten 20 dieses Pi. Was dabei herauskam,
steht in STATUS_FILE; die App meldet es mit den anderen Infos dieses Pi (system.publish) und warnt in Übersicht und
Statusanzeige.

Standardbibliothek und cryptography (im venv): auch die root-Helfer benutzen dieses Modul.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import time
import zipfile
from pathlib import Path

SECRET_KEYS = ("update_net_token", "update_net_psk", "fully_password", "uplink_password", "fully_prov_wifi_password",
               "fully_rollouts", "fully_volume_key", "fully_kiosk_pin", "backup_password", "backup_password_old")
# Zustand der einzelnen Pi (Infos, Druckerstand, Bildschirm, Update): gehört nicht in eine Sicherung, beim Einspielen
# gelten die Werte des laufenden Systems
STATE_KEYS = ("printer_state", "update_lock")
STATE_PREFIXES = ("node_info.", "update_status.", "display_status.")
STICK_DIR = "festpos-sicherung"                 # Ordner auf dem Stick: nur dorthin wird geschrieben
KEEP = 20                                       # so viele Sicherungen pro Pi bleiben auf dem Stick
INTERVAL_S = 15 * 60
OLD_S = 3 * INTERVAL_S + 300                    # älter: Warnung (dreimal hintereinander nichts geschrieben)
FILE_RE = re.compile(r"^festpos-([a-z0-9]{1,8})-(\d{8}-\d{6})\.(?:festpos|sqlite)$")
EXT = ".festpos"
SQLITE_MAGIC = b"SQLite format 3\x00"
MAGIC = b"FESTPOS-SICHERUNG\n"                  # Anfang jeder verschlüsselten Sicherung, danach eine Zeile JSON (Kopf)
FORMAT = 1
KDF = {"n": 2 ** 15, "r": 8, "p": 1}            # scrypt: ~32 MB, auf dem Pi 5 deutlich unter einer Sekunde
MIN_PASSWORD = 12                               # selbst gewählte Passwörter
PW_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # ohne Verwechsler (0/O, 1/I): 5 Bit pro Zeichen
PW_GROUPS, PW_GROUP_LEN = 6, 4                  # K7QM-3XRA-9TCV-HW4P-ZE6N-2LYD: 120 Bit
PW_RE = re.compile(rf"^[{PW_ALPHABET}]{{{PW_GROUPS * PW_GROUP_LEN}}}$")
TLS_FILES = ("ca.crt", "server.crt", "server.key", "device-ca.crt", "device-ca.key")
UPDATE_KEY_RE = re.compile(r"^festpos-update[A-Za-z0-9._-]*\.pub$")
STATUS_FILE = Path(os.environ.get("FESTPOS_SICHERUNG_STATUS", "/var/lib/festpos-sicherung/status.json"))
HELPER = Path(os.environ.get("FESTPOS_SICHERUNG_BIN", "/usr/local/sbin/festpos-sicherung"))


def installed() -> bool:
    """Ist der root-Helfer da (Pi ab dieser Version)? Ohne ihn (Entwicklung, Tests) keine Warnung «fehlt»."""
    return HELPER.exists()


def no_wal(data: bytes) -> bytes:
    """SQLite-Kopf auf Rollback-Journal setzen (Bytes 18/19 = 1): eine Datei aus einer WAL-Datenbank lässt sich
    sonst weder im Speicher öffnen noch überall einspielen."""
    if len(data) >= 20 and data[18:20] == b"\x02\x02":
        data = data[:18] + b"\x01\x01" + data[20:]
    return data


def strip_secrets(data: bytes) -> bytes:
    """Sicherungsdatei ohne Geheimnisse (SECRET_KEYS). secure_delete: die Werte bleiben auch nicht in freien
    Seiten der Datei stehen."""
    con = sqlite3.connect(":memory:")
    try:
        con.deserialize(no_wal(data))
        con.execute("PRAGMA secure_delete=ON")
        try:
            con.execute(f"DELETE FROM settings WHERE key IN ({','.join('?' * len(SECRET_KEYS))})", SECRET_KEYS)
        except sqlite3.OperationalError as e:
            if "no such table" not in str(e):        # sehr alte Sicherung ohne Einstellungen
                raise
        con.commit()
        return con.serialize()
    finally:
        con.close()


def file_name(node: str, now: float | None = None) -> str:
    return f"festpos-{node}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}{EXT}"


def manual_name(node: str, now: float | None = None) -> str:
    """Heruntergeladene Sicherung (eigenes Passwort): anders als die automatischen «festpos-a-…» (T17)."""
    return f"festpos-manuell-{node}-{time.strftime('%Y%m%d-%H%M', time.localtime(now))}{EXT}"


# -- Verschlüsselte Sicherung --------------------------------------------------------------------------------------
class BadPassword(ValueError):
    """Passwort falsch – oder die Datei wurde verändert (GCM kann das nicht unterscheiden)."""


def new_password() -> str:
    """Sicherungs-Passwort für die automatische Sicherung: 6 Gruppen à 4 Zeichen ohne Verwechsler."""
    raw = "".join(secrets.choice(PW_ALPHABET) for _ in range(PW_GROUPS * PW_GROUP_LEN))
    return "-".join(raw[i:i + PW_GROUP_LEN] for i in range(0, len(raw), PW_GROUP_LEN))


def password_variants(pw: str) -> list[str]:
    """Das Passwort wie eingegeben – und, wenn es wie ein erzeugtes aussieht, in dessen genauer Schreibweise
    (gross/klein egal, Bindestriche und Leerzeichen dürfen fehlen oder anders stehen)."""
    out = [pw]
    raw = re.sub(r"[\s-]+", "", pw).upper()
    if PW_RE.match(raw):
        canon = "-".join(raw[i:i + PW_GROUP_LEN] for i in range(0, len(raw), PW_GROUP_LEN))
        if canon != pw:
            out.append(canon)
    return out


def _key(password: str, salt: bytes, kdf: dict) -> bytes:
    n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
    if not (2 ** 10 <= n <= 2 ** 20 and 1 <= r <= 16 and 1 <= p <= 4):
        raise ValueError("kdf")                                  # Datei verlangt Unsinniges (oder Absicht)
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=256 * 1024 * 1024, dklen=32)


def is_encrypted(data: bytes) -> bool:
    return data.startswith(MAGIC)


def read_header(data: bytes) -> dict | None:
    """Lesbarer Kopf einer verschlüsselten Sicherung (Pi, Zeitpunkt, Version) – None, wenn es keine ist."""
    if not is_encrypted(data):
        return None
    end = data.find(b"\n", len(MAGIC))
    if end < 0 or end - len(MAGIC) > 4096:
        return None
    try:
        head = json.loads(data[len(MAGIC):end])
    except ValueError:
        return None
    return head if isinstance(head, dict) and head.get("format") == FORMAT else None


def pack(members: dict[str, bytes], password: str, *, node: str = "", app: str = "", now: float | None = None,
         kdf: dict | None = None, kind: str = "") -> bytes:
    """Sicherung verschlüsseln: ZIP der Dateien, AES-256-GCM, der Kopf ist mit geschützt (AAD)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, content in members.items():
            z.writestr(name, content)
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    kdf = dict(kdf or KDF)
    head = {"format": FORMAT, "node": node, "at": int(time.time() if now is None else now), "app": app,
            "kdf": kdf, "salt": base64.b64encode(salt).decode(), "nonce": base64.b64encode(nonce).decode()}
    if kind:                       # T17: «auto» (USB-Stick, Sicherungs-Passwort) oder «manual» (heruntergeladen)
        head["kind"] = kind
    line = json.dumps(head, sort_keys=True, separators=(",", ":")).encode()
    aad = MAGIC + line + b"\n"
    return aad + AESGCM(_key(password, salt, kdf)).encrypt(nonce, buf.getvalue(), aad)


def unpack(data: bytes, password: str) -> dict[str, bytes]:
    """Sicherung entschlüsseln -> {Name: Inhalt}. BadPassword bei falschem Passwort oder veränderter Datei."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    head = read_header(data)
    if head is None:
        raise ValueError("format")
    end = data.find(b"\n", len(MAGIC)) + 1
    try:
        salt, nonce = base64.b64decode(head["salt"]), base64.b64decode(head["nonce"])
        kdf = head["kdf"]
    except (KeyError, ValueError, TypeError):
        raise ValueError("format") from None
    last = None
    for pw in password_variants(password):
        try:
            plain = AESGCM(_key(pw, salt, kdf)).decrypt(nonce, data[end:], data[:end])
            break
        except InvalidTag as e:
            last = e
    else:
        raise BadPassword("password") from last
    out = {}
    with zipfile.ZipFile(io.BytesIO(plain)) as z:
        for info in z.infolist():
            if info.is_dir() or info.file_size > 300 * 1024 * 1024:
                continue
            out[info.filename] = z.read(info)
    return out


def try_unpack(data: bytes, passwords: list[str]) -> tuple[dict[str, bytes], int] | None:
    """Mit den gegebenen Passwörtern versuchen -> (Inhalt, Index des passenden) oder None."""
    for i, pw in enumerate(passwords):
        if not pw:
            continue
        try:
            return unpack(data, pw), i
        except BadPassword:
            continue
    return None


def members(db: bytes, tls_dir: Path | None, conf: dict, keys_dir: Path | None = None) -> dict[str, bytes]:
    """Inhalt einer Sicherung: Datenbank, Zertifikate und Schlüssel (soweit lesbar), CLUSTER_SECRET und
    Notfall-Passwort, Update-Schlüssel."""
    out = {"festpos.sqlite": no_wal(db), "conf.json": json.dumps(conf, sort_keys=True).encode()}
    for name in TLS_FILES:
        try:
            out[f"tls/{name}"] = (tls_dir / name).read_bytes() if tls_dir else b""
        except OSError:
            pass
        if out.get(f"tls/{name}") == b"":
            out.pop(f"tls/{name}")
    if keys_dir:
        try:
            for e in sorted(os.scandir(keys_dir), key=lambda e: e.name):
                if UPDATE_KEY_RE.match(e.name) and e.is_file(follow_symlinks=False):
                    out[f"update-keys/{e.name}"] = Path(e.path).read_bytes()
        except OSError:
            pass
    return out


def fingerprint(pem: bytes | None) -> str | None:
    """SHA-256 eines Zertifikats (über die DER-Bytes) – öffentlich, zum Vergleichen."""
    if not pem:
        return None
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        der = x509.load_pem_x509_certificate(pem).public_bytes(serialization.Encoding.DER)
    except ValueError:
        return None
    return hashlib.sha256(der).hexdigest()


# -- festpos.conf und /etc/festpos/env (wie deploy/festpos-conf.sh) -----------------------------------------------
PLACEHOLDER_SECRETS = {"", "admin", "bitte-aendern", "bitte-aendern-lang-und-zufaellig"}


def derived_secret(server_key: bytes) -> str:
    """CLUSTER_SECRET, wenn in festpos.conf nur der Platzhalter steht (gleiche Rechnung wie festpos_secret_fix)."""
    return "auto-" + hashlib.sha256(b"festpos-cluster-secret\0" + server_key).hexdigest()


def conf_value(text: str, key: str) -> str | None:
    """Wert aus festpos.conf/env lesen (Regeln von festpos_read_conf: «KEY=Wert # Kommentar», "…", '…')."""
    val = None
    for line in text.replace("\r", "").lstrip("\ufeff").split("\n"):
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if not m or m.group(1) != key:
            continue
        v = m.group(2)
        if re.match(r"^\s*$", v) or re.match(r"^\s+#", v):
            val = ""
            continue
        v = v.lstrip()
        if (q := re.match(r'^"(.*)"\s*(#.*)?$', v)):
            val = re.sub(r"\\(.)", r"\1", q.group(1))
        elif (q := re.match(r"^'([^']*)'\s*(#.*)?$", v)):
            val = q.group(1)
        else:
            val = re.split(r"\s#", v, maxsplit=1)[0].rstrip()
    return val


def conf_line(key: str, value: str) -> str:
    """KEY="Wert" – wörtlich lesbar für festpos_read_conf und systemd (wie festpos_env_line).
    ValueError bei Steuerzeichen: ein Zeilenumbruch im Wert (z.B. im Notfall-Passwort einer fremden Sicherung) schriebe
    sonst weitere Zeilen in festpos.conf (Review 2026-11, S2)."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("conf_value")
    v = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return f'{key}="{v}"'


def conf_set(text: str, key: str, value: str) -> str:
    """Erste Zeile «KEY=…» ersetzen (Kommentare und Rest bleiben) oder am Ende anhängen."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if re.match(rf"^\s*{key}\s*=", line):
            lines[i] = conf_line(key, value)
            return "\n".join(lines)
    return text.rstrip("\n") + "\n" + conf_line(key, value) + "\n"


def to_delete(names: list[str], node: str, keep: int = KEEP) -> list[str]:
    """Ältere Sicherungen dieses Pi, die über die letzten «keep» hinausgehen (andere Dateien bleiben unberührt)."""
    mine = sorted((n for n in names if (m := FILE_RE.match(n)) and m.group(1) == node), reverse=True)
    return mine[keep:]


def read_status(path: Path | None = None) -> dict | None:
    """Letztes Ergebnis von festpos-sicherung auf diesem Pi: {state: ok|no_stick|failed, at, last_ok, file, size,
    code} oder None (noch nie gelaufen)."""
    try:
        d = json.loads((path or STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    keep = ("state", "at", "last_ok", "file", "size", "code")
    return {k: d.get(k) for k in keep if d.get(k) is not None}


def warnings(states: dict[str, dict | None], single: bool, now: float | None = None) -> tuple[list[str], dict | None]:
    """(Hinweis-Codes, neuste Sicherung {node, at, file}) aus dem Ergebnis aller Pi.
    backup_failed: ein Pi fand den Stick, konnte aber nicht schreiben (immer). backup_none / backup_old: keine bzw.
    keine frische Sicherung – nur bei einem einzelnen Pi (im Cluster liegt jede Bestellung schon auf zwei Pi)."""
    now = time.time() if now is None else now
    out: list[str] = []
    newest = None
    for node, st in sorted(states.items()):
        if not st:
            continue
        if st.get("state") == "failed" and now - (st.get("at") or 0) < OLD_S:
            out.append("backup_failed")
        if st.get("last_ok") and (newest is None or st["last_ok"] > newest["at"]):
            newest = {"node": node, "at": st["last_ok"], "file": st.get("file")}
    if single and "backup_failed" not in out:
        if newest is None:
            out.append("backup_none")
        elif now - newest["at"] > OLD_S:
            out.append("backup_old")
    return sorted(set(out)), newest
