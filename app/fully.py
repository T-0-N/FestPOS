"""Fully Kiosk Browser: Zusatzinfos der Handys direkt bei Fully abholen – Akku und Akku-Temperatur, Bildschirm,
Kiosk-Modus, WLAN-Signal, freier Speicher, Fully-Version. Über die Fernverwaltung von Fully (Remote Admin,
PLUS-Lizenz), ausschliesslich per HTTPS. Ohne Fully meldet die Bestell-App selbst den Akkustand (monitor.seen).

Sicherheit:
  - Für jedes Handy erstellt die Verwaltung ein eigenes Zertifikat. Fully erwartet es als Datei
    «fully-remote-admin-ca.p12» (Kennwort «fully») im obersten Ordner des internen Speichers.
  - Gespeichert wird nur der SHA-256-Fingerabdruck dieses Zertifikats (öffentlich). Der private Schlüssel steht
    nur in der heruntergeladenen Datei und danach auf dem Handy – nie in der Datenbank oder einer Sicherung.
  - Erst wenn das Handy über TLS genau dieses Zertifikat vorzeigt, geht die Anfrage mit dem Remote-Admin-Passwort
    hinaus. Antwortet Fully nur unverschlüsselt (HTTP), zeigt ein anderes Zertifikat (z.B. ein anderes Gerät mit
    derselben IP oder jemand gibt sich im WLAN als Handy aus) oder ist kein Zertifikat eingerichtet: das Passwort
    wird nie gesendet.
  - Im Hintergrund nur lesen (cmd=deviceInfo; alle 15 Minuten listSettings für die Whitelist, web_due). Befehle
    (COMMANDS: App/Handy neu starten, Handy ausschalten, Bildschirm aus/an, Kiosk-Modus sperren/entsperren) nur auf
    Knopfdruck ab der Rolle Festleitung, auf demselben Weg – nie andere Befehle.
  - Einrichten (nur Admin, auf Knopfdruck): CA des Servers und Gerätezertifikat holt Fully als ZIP von einer
    Einmal-Adresse (loadZipFile), dann installiert es die CA per file:// (installUserCa); gesetzt werden nur
    clientCaUrl/clientCaPassword (SETTINGS). Das Kennwort des Gerätezertifikats ist lang und zufällig und geht nur
    über diese HTTPS-Verbindung hinaus.
Die Abfrage läuft im Hintergrund (alle 30 s, nur auf dem Pi, der druckt – er hat die VIP, mit ihm reden die Handys),
parallel und mit kurzer Zeitgrenze. Bestellen und Drucken hängen nicht davon ab.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import io
import ipaddress
import json
import logging
import os
import re
import secrets
import ssl
import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import certs, device_ids, devices, dhcp as dhcp_mod, i18n, ledger_base as lb, monitor as monitor_mod, rawhttp, timesync
from .core import LOCAL_HOSTS, Config, admin, current_user, db, rt

log = logging.getLogger("festpos.fully")

P12_NAME = "fully-remote-admin-ca.p12"       # so heisst die Datei, die Fully beim Start lädt
P12_PASSWORD = b"fully"                       # von Fully vorgegeben
CERT_DAYS = 3650
DEFAULT_PORT = 2323
INTERVAL = 30.0                               # Sekunden zwischen zwei Abfragen
TIMEOUT = 4.0                                 # pro Handy und Abfrage, alles zusammen
COMMAND_TIMEOUT = 12.0                        # Befehle: Fully versucht z.B. erst Root, bevor es ablehnt – Antwort abwarten
MAX_ANSWER = 512 * 1024
STATES = ("ok", "no_https", "cert", "auth", "unreachable", "timeout", "bad")
PIN_RE = re.compile(r"^[0-9a-f]{64}$")


# -- Zertifikat -------------------------------------------------------------------------------------------
def make_cert(name: str, ip: str) -> tuple[bytes, str, str]:
    """Selbst signiertes Server-Zertifikat für Fully auf diesem Handy.
    -> (p12-Datei, SHA-256-Fingerabdruck des Zertifikats, gültig bis JJJJ-MM-TT)"""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)   # RSA: versteht jedes Android
    now = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)            # Toleranz für falsche Uhren
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, (name or ip)[:64]),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "FestPOS Fully")])
    try:
        alt: x509.GeneralName = x509.IPAddress(ipaddress.ip_address(ip))
    except ValueError:
        alt = x509.DNSName(ip)
    cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now).not_valid_after(now + dt.timedelta(days=CERT_DAYS + 1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=True, content_commitment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectAlternativeName([alt]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    # wie die Gerätezertifikate: «Legacy»-Verschlüsselung (3DES/SHA1) – die lädt Android zuverlässig
    enc = (serialization.PrivateFormat.PKCS12.encryption_builder()
           .kdf_rounds(50000)
           .key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC)
           .hmac_hash(hashes.SHA1())
           .build(P12_PASSWORD))
    p12 = pkcs12.serialize_key_and_certificates(b"fully", key, cert, None, enc)
    pin = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    return p12, pin, cert.not_valid_after_utc.strftime("%Y-%m-%d")


# -- Abfrage ----------------------------------------------------------------------------------------------
def _tls() -> ssl.SSLContext:
    return rawhttp.tls_client()               # geprüft wird stattdessen genau DIESES Zertifikat (Fingerabdruck)


COMMANDS = ("restartApp", "rebootDevice", "shutdownDevice", "screenOff", "screenOn",   # was FestPOS auslösen darf –
            "lockKiosk", "unlockKiosk",                                                   # sonst nichts
            "loadStartURL", "clearCache", "toForeground")         # Seite neu laden, Cache, Vordergrund
KIOSK_COMMANDS = ("lockKiosk", "unlockKiosk")


PLAIN = "plain"                               # statt Fingerabdruck: einmal unverschlüsselt (nur fully_bootstrap)


async def _exchange(ip: str, port: int, pin: str, password: str, params: dict,
                    timeout: float) -> tuple[str, bytes | None]:
    """Eine Anfrage an Fully, nur über HTTPS mit genau diesem Zertifikat (sonst geht das Passwort nicht hinaus).
    -> ("answer", Antwort) oder (Zustand, None): cert|auth|timeout|unreachable|no_https|bad – oder, wenn die Anfrage
    schon draussen war: «noanswer» (keine Antwort in der Zeit), «lost» (Verbindung weg), «empty» (leere Antwort).
    Höchstens etwa `timeout` Sekunden: Verbindung, TLS und Antwort haben je einen Teil davon.
    Einzige Ausnahme: pin == PLAIN – unverschlüsselt, nur beim Ausrollen eines frisch eingerichteten Handys, das
    noch kein Zertifikat hat (fully_bootstrap), auf ausdrückliche Bestätigung in der Verwaltung."""
    if pin != PLAIN and not PIN_RE.match(pin or ""):
        return "cert", None
    if not password:
        return "auth", None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout * 0.4)
    except asyncio.TimeoutError:
        return "timeout", None                     # Handy schläft oder ist nicht im WLAN
    except OSError:
        return "unreachable", None                 # Handy aus, Fully zu oder Fernverwaltung aus
    sent = False
    try:
        if pin != PLAIN:
            try:
                await asyncio.wait_for(writer.start_tls(_tls(), server_hostname=None), timeout * 0.3)
            except (ssl.SSLError, OSError, EOFError, asyncio.TimeoutError):
                return "no_https", None            # spricht nur HTTP (kein Zertifikat in Fully) – Passwort bleibt hier
            ssl_obj = writer.get_extra_info("ssl_object")
            der = ssl_obj.getpeercert(binary_form=True) if ssl_obj else None
            if not der or hashlib.sha256(der).hexdigest() != pin:
                log.warning("Fully %s:%s zeigt ein anderes Zertifikat – Passwort nicht gesendet", ip, port)
                return "cert", None
        q = urlencode({**params, "type": "json", "password": password})
        writer.write(f"GET /?{q} HTTP/1.1\r\nHost: {ip}:{port}\r\nUser-Agent: FestPOS\r\n"
                     f"Accept: application/json\r\nConnection: close\r\n\r\n".encode())
        sent = True
        try:
            raw = await asyncio.wait_for(_read(reader, writer), timeout * 0.5)
        except asyncio.TimeoutError:
            return "noanswer", None
        if raw is None:
            return "bad", None
        return ("answer", raw) if raw else ("empty", None)
    except (ssl.SSLError, OSError, EOFError):
        return ("lost" if sent else "unreachable"), None
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 1)
        except (OSError, ssl.SSLError, asyncio.TimeoutError):
            pass


def _command_result(state: str, raw: bytes | None) -> dict:
    """Ergebnis eines Befehls: ohne Antwort gilt er als «sent» – bei Neustart/Ausschalten ist Fully oft weg,
    bevor es antwortet; angekommen ist der Auftrag trotzdem."""
    if state == "answer":
        return interpret_command(raw or b"")
    return {"state": "sent" if state in ("noanswer", "lost", "empty") else state}


async def query(ip: str, port: int, pin: str, password: str, timeout: float = TIMEOUT, cmd: str = "deviceInfo") -> dict:
    """Geräteinfo von Fully. -> {"state": ok|no_https|cert|auth|unreachable|timeout|bad, "data": {…} bei ok}
    Mit cmd (COMMANDS) stattdessen einen Befehl: -> {"state": ok|refused|sent|…, "text": Antwort von Fully}."""
    if cmd != "deviceInfo" and cmd not in COMMANDS:
        raise ValueError(cmd)
    if pin == PLAIN:                               # nie im Hintergrund oder per Knopf unverschlüsselt
        return {"state": "cert"}
    state, raw = await _exchange(ip, port, pin, password, {"cmd": cmd}, timeout)
    if cmd != "deviceInfo":
        return _command_result(state, raw)
    if state == "answer":
        return interpret(raw or b"")
    return {"state": {"noanswer": "timeout", "lost": "unreachable", "empty": "bad"}.get(state, state)}


# -- Einrichten über Fully: Dateien, Einstellungen, CA (nur die Verwaltung, nur Admin) ------------------------
STORAGE_ROOT = "/storage/emulated/0"          # oberster Ordner des internen Speichers (dort auch fully-remote-admin-ca.p12)
FILES_FOLDER = "FestPOS"
FILES_DIR = f"{STORAGE_ROOT}/{FILES_FOLDER}"   # dort legt FestPOS seine Dateien auf dem Handy ab
CA_FILE = "festpos-ca.crt"
SETTINGS = ("clientCaUrl", "clientCaPassword", "volumeLicenseKey", "kioskPin",   # ausserhalb der Vorlage gesetzt
            "urlWhitelist")                                                   # (Whitelist: fully_whitelist)
SETUP_COMMANDS = ("setStringSetting", "setBooleanSetting", "installUserCa", "restartApp", "loadZipFile", "loadApkFile",
                  "rebootDevice")
FETCH_WAIT = 30.0                             # so lange darf Fully für das Abholen der ZIP brauchen
UNZIP_WAIT = 2.0                              # Fully entpackt im Hintergrund – kurz Zeit lassen

# Dateien für Fully: als ZIP unter einer Einmal-Adresse, abgeholt mit loadZipFile über http://<Pi>/fully/<code>.zip
# (deploy/festpos-redirect.py auf Port 80 holt sie lokal hier ab). Klartext im WLAN ist dabei kein Problem: die CA ist
# öffentlich (geprüft wird sie nicht hier, sondern beim Installieren nur per file://), das Gerätezertifikat ist mit
# einem langen Kennwort verschlüsselt, und das geht nur über die geprüfte HTTPS-Verbindung an Fully.
zips = certs.OneTimeDownloads()
zips.TTL = FETCH_WAIT + 30


def make_zip(files: dict[str, bytes], folder: str = "") -> bytes:
    """ZIP mit den Dateien – mit folder in diesem Unterordner (samt Eintrag für den Ordner selbst)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if folder:
            z.writestr(f"{folder}/", b"")
        for name, data in files.items():
            z.writestr(f"{folder}/{name}" if folder else name, data)
    return buf.getvalue()


async def deliver(args: tuple, host: str, files: dict[str, bytes], folder: str = FILES_FOLDER) -> dict:
    """Dateien nach STORAGE_ROOT/folder aufs Handy: Fully holt die ZIP selbst ab (loadZipFile läuft im Hintergrund).
    Entpackt wird immer in den obersten Ordner, der Unterordner steckt in der ZIP: Fully lehnt einen Zielordner ab,
    den es noch nicht gibt («Output folder is not writable» – auf einem frisch eingerichteten Handy fehlt FestPOS/).
    Fertig ist es erst, wenn Fully die Einmal-Adresse abgerufen hat. -> {"state": ok|not_fetched|…}"""
    token = zips.put(make_zip(files, folder), "festpos.zip")
    res = await setup_command(*args, "loadZipFile", url=f"http://{host}/fully/{token}.zip", dir=STORAGE_ROOT)
    if res["state"] not in ("ok", "sent"):
        zips.take(token)
        return res
    end = time.monotonic() + FETCH_WAIT
    while zips.pending(token):
        if time.monotonic() > end:
            zips.take(token)                       # später nicht mehr abholbar
            return {"state": "not_fetched"}
        await asyncio.sleep(0.3)
    await asyncio.sleep(UNZIP_WAIT)
    return {"state": "ok"}


async def setup_command(ip: str, port: int, pin: str, password: str, cmd: str, **params: str) -> dict:
    """Befehl beim Einrichten: Einstellung setzen (Zertifikat oder aus der Vorlage), CA installieren, ZIP laden,
    App neu starten."""
    if cmd not in SETUP_COMMANDS or (cmd in ("setStringSetting", "setBooleanSetting")
                                     and params.get("key") not in SETTINGS and not template_key(params.get("key"))):
        raise ValueError(cmd)
    state, raw = await _exchange(ip, port, pin, password, {"cmd": cmd, **params}, COMMAND_TIMEOUT)
    return _command_result(state, raw)


# -- T30: APK installieren trotz Installationssperren ---------------------------------------------
# Fully-Doku (fully-kiosk.com/docs/en/configuration/device-owner und …/knox): «Disable APK Installs» (Geräteinhaber)
# und «Disable non-Google-Play Apps» (KNOX) verhindern das Installieren einer APK. Die Doku nennt nur Anzeigenamen –
# die Schlüssel sucht der Pi in listSettings (wie adb_setting): Ja/Nein-Einstellung mit «disable» + «apk» + «install»
# bzw. mit «knox» + «google»/«market»/«unknown». Steht eine auf «ein», schaltet der Pi sie fürs Installieren aus und
# danach wieder ein. Ob es geklappt hat, meldet Fully mit getInstallApkState (eine Liste der installierten Apps gibt
# die REST-API nicht).
INSTALL_WAIT = 150.0                          # so lange auf «installiert» warten (Download + Installation)
INSTALL_POLL = 5.0
RELOCK_AFTER = 120.0                          # Fully selbst aktualisieren: Fully startet neu – Sperren danach wieder ein
_relocks: set = set()                         # laufende Aufgaben (sonst räumt Python sie weg)


def _is_true(v) -> bool:
    return v is True or str(v).lower() == "true"


def install_locks(settings: dict) -> list[str]:
    """Schlüssel der Einstellungen, die das Installieren einer APK sperren (unabhängig vom Wert)."""
    out = []
    for k, v in sorted(settings.items()):
        low = k.lower()
        if not (isinstance(v, bool) or str(v).lower() in ("true", "false")):
            continue
        if ("disable" in low and "apk" in low and "install" in low) or \
                ("knox" in low and any(w in low for w in ("google", "market", "unknown"))):
            out.append(k)
    return out


def install_result(data) -> str | None:
    """getInstallApkState -> installed | failed | None (läuft noch / unbekannt). Das Format beschreibt die Doku nicht:
    gesucht wird nach Wörtern im JSON."""
    text = json.dumps(data).lower() if data is not None else ""
    if any(w in text for w in ("fail", "error", "abort", "denied", "blocked", "cancel")):
        return "failed"
    if any(w in text for w in ("success", "installed", "complete", "finished", "done")):
        return "installed"
    return None


async def install_state(args: tuple) -> dict | None:
    state, raw = await _exchange(*args, {"cmd": "getInstallApkState"}, COMMAND_TIMEOUT)
    if state != "answer":
        return None
    status, _, body = _http(raw or b"")
    if status != 200:
        return None
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return {"text": body.decode("utf-8", "replace")[:200]}
    return data if isinstance(data, dict) else {"value": data}


async def _set_locks(args: tuple, keys: list[str], value: bool, tries: int = 1) -> list[str]:
    done = []
    for k in keys:
        for i in range(tries):
            r = await setup_command(*args, "setBooleanSetting", key=k, value="true" if value else "false")
            if r.get("state") in ("ok", "sent"):
                done.append(k)
                break
            if i + 1 < tries:
                await asyncio.sleep(15)
    return done


async def _relock_later(args: tuple, keys: list[str]) -> None:
    try:
        await asyncio.sleep(RELOCK_AFTER)
        back = await _set_locks(args, keys, True, tries=6)
        log.info("Installationssperren auf %s wieder ein: %s", args[0], ", ".join(back) or "–")
    except asyncio.CancelledError:
        pass


async def install_apk(ip: str, port: int, pin: str, password: str, url: str, wait: bool = True) -> dict:
    """APK über Fully installieren (loadApkFile): Sperren kurz aus, danach wieder ein. wait: auf getInstallApkState
    warten (FestPOS Tap); ohne (Fully selbst – startet dabei neu) kommen die Sperren nach RELOCK_AFTER wieder.
    -> {"state": installed|failed|sent|<Fehler von Fully>, "locks": [ausgeschaltet], "text": …}; ohne wait die Antwort
    von Fully auf loadApkFile (ok | sent | …)."""
    args = (ip, port, pin, password)
    st, settings = await read_settings(args)
    locked = [k for k in install_locks(settings) if _is_true(settings[k])] if st == "ok" else []
    opened = await _set_locks(args, locked, False)
    relock_now = True
    try:
        r = await setup_command(*args, "loadApkFile", url=url, forceInstall="false")
        if r.get("state") not in ("ok", "sent"):
            return {**r, "locks": opened}
        if not wait:
            relock_now = False
            if opened:
                task = asyncio.create_task(_relock_later(args, opened))
                _relocks.add(task)
                task.add_done_callback(_relocks.discard)
            return {**r, "locks": opened}                       # Antwort von Fully wie bisher (ok | sent)
        end = time.monotonic() + INSTALL_WAIT
        last = None
        while time.monotonic() < end:
            await asyncio.sleep(INSTALL_POLL)
            last = await install_state(args)
            res = install_result(last)
            if res:
                return {"state": res, "locks": opened, "text": json.dumps(last)[:200]}
        return {"state": "sent", "locks": opened, "text": json.dumps(last)[:200] if last else ""}
    finally:
        if relock_now and opened:
            await _set_locks(args, opened, True, tries=3)


# -- Vorlage: alle Einstellungen von Fully gleich auf allen Handys ---------------------------------------------
# Übernommen von einem Handy (listSettings) oder als JSON hochgeladen. Nie darin: was pro Handy anders ist und alles
# Geheime (Kennwörter, verschlüsselte Werte, Lizenz, WLAN) – das bleibt auf jedem Handy, wie es ist.
TEMPLATE_SKIP = frozenset({
    "deviceName", "actionBarTitle",                       # pro Handy (z.B. «VEREIN MUSTER | 101»)
    "clientCaUrl", "clientCaUrls",                       # Gerätezertifikat: pro Handy, setzt «Gerätezertifikat»
    "volumeLicenseKey", "wifiConfiguration", "kioskWifiPin", "authUsername", "mqttBrokerUsername",
    "sebExamKey", "sebConfigKey",                         # Lizenz, WLAN, Zugangsdaten
    "kioskPin",                                           # Kiosk-PIN: eigenes Feld der Vorlage (Geheimnis, kiosk_pin)
    "urlWhitelist",                                       # aus der Start-URL jedes Handys (fully_whitelist)
    "lastVersionInfo", "isRunning",                       # Zustand, keine Einstellung
})
TEMPLATE_FORCE = {"remoteAdmin": True, "remoteAdminLan": True}   # sonst sperrte sich FestPOS selbst aus
TEMPLATE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
TEMPLATE_MAX = 1000
TEMPLATE_VALUE_MAX = 4000


def template_key(key) -> bool:
    """Darf diese Einstellung in die Vorlage (und damit auf alle Handys)?"""
    if not isinstance(key, str) or not TEMPLATE_KEY_RE.match(key) or key in TEMPLATE_SKIP:
        return False
    low = key.lower()
    return not (key.endswith("Enc") or "password" in low or "secret" in low or "token" in low)


def clean_template(raw) -> dict:
    """Nur erlaubte Schlüssel mit einfachen Werten (Ja/Nein, Zahl, Text); Fernverwaltung bleibt immer an."""
    if not isinstance(raw, dict):
        raise ValueError("keine Einstellungen")
    out: dict = {}
    for k, v in raw.items():
        if not template_key(k) or v is None or isinstance(v, (dict, list)):
            continue
        if isinstance(v, str) and (len(v) > TEMPLATE_VALUE_MAX or "\x00" in v):
            continue
        if isinstance(v, float) and v != v:
            continue
        out[k] = v
        if len(out) >= TEMPLATE_MAX:
            break
    out.update(TEMPLATE_FORCE)
    return dict(sorted(out.items()))


def load_template() -> dict:
    try:
        t = json.loads(rt.store.setting("fully_template") or "{}")
    except ValueError:
        return {}
    return t if isinstance(t, dict) and isinstance(t.get("settings"), dict) else {}


def save_template(settings: dict, source: str) -> dict:
    t = {"settings": settings, "source": source[:60], "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    rt.store.execute("INSERT INTO settings(key,value) VALUES ('fully_template', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(t, ensure_ascii=False),))
    return t


def same_value(want, have) -> bool:
    """Fully liefert Zahlen manchmal als Text («500») – gleich ist gleich, egal wie geschrieben."""
    if isinstance(want, bool) or isinstance(have, bool):
        return want == have
    return str(want) == str(have)


def differences(template: dict, current: dict) -> dict:
    """Was auf dem Handy anders ist als in der Vorlage (fehlt eine Einstellung dort, kennt diese Fully-Version sie
    nicht – dann nicht setzen)."""
    return {k: v for k, v in template.items() if k in current and not same_value(v, current[k])}


async def read_settings(args: tuple) -> tuple[str, dict]:
    """Alle Einstellungen von Fully (listSettings). -> (Zustand, Einstellungen). Enthält auch Geheimes: nur gefiltert
    weitergeben, nie speichern oder protokollieren."""
    state, raw = await _exchange(*args, {"cmd": "listSettings"}, COMMAND_TIMEOUT)
    if state != "answer":
        return {"noanswer": "timeout", "lost": "unreachable", "empty": "bad"}.get(state, state), {}
    status, _, body = _http(raw or b"")
    if status in (401, 403):
        return "auth", {}
    try:
        data = json.loads(body.decode("utf-8", "replace")) if status == 200 else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or str(data.get("status", "")).lower() == "error":
        return ("auth" if isinstance(data, dict) and "login" in str(data.get("statustext", "")).lower() else "bad"), {}
    return "ok", data


async def apply_settings(args: tuple, changes: dict) -> tuple[list[str], dict[str, str]]:
    """Einstellungen setzen -> (gesetzt, {fehlgeschlagen: Antwort von Fully})."""
    done, failed = [], {}
    for k, v in changes.items():
        if isinstance(v, bool):
            res = await setup_command(*args, "setBooleanSetting", key=k, value="true" if v else "false")
        else:
            res = await setup_command(*args, "setStringSetting", key=k, value=str(v))
        if res["state"] in ("ok", "sent"):
            done.append(k)
        else:
            failed[k] = res.get("text") or res["state"]
            log.warning("Fully-Einstellung %s auf %s nicht gesetzt: %s", k, args[0], failed[k])   # nie den Wert
    return done, failed


# -- JavaScript-Schnittstelle nur mit URL Whitelist ----------------------------------------------------
# Fully gibt seine JavaScript-Schnittstelle (window.fully: Kiosk entsperren, Apps starten, Einstellungen ändern, Handy
# neu starten …) JEDER Seite, die es lädt. Die Fully-Doku dazu: «Enable this option only if you trust all websites
# loaded (use URL Whitelist!)». Der Pi liest darum ab und zu die Einstellungen (listSettings – nur hier ausgewertet,
# nie gespeichert) und meldet in der Verwaltung, wenn die Schnittstelle an ist, die Whitelist aber fehlt oder mehr
# erlaubt als die Bestell-App. «Whitelist setzen» trägt genau deren Adresse ein (aus der Start-URL des Handys).
# Die Doku nennt nur Anzeigenamen; die Schlüssel sind die aus listSettings bzw. dem Einstellungs-Export von Fully.
# «Enable JavaScript Interface» heisst intern websiteIntegration (früherer Name im Menü: XCover 7 mit
# Fully 1.61.4 kannte «jsInterface» nicht). Eine leere Whitelist lässt Fully in listSettings evtl. weg: dann «fehlt».
# Ohne Schlüssel für die Schnittstelle heisst es «nicht prüfbar» – mit den Namen, die nach Schnittstelle oder
# Whitelist klingen (nur Namen, nie Werte), damit sich eine andere Fully-Version schnell nachtragen lässt.
JS_KEYS = ("websiteIntegration", "jsInterface")   # Advanced Web Settings › Enable JavaScript Interface
WHITELIST_KEY = "urlWhitelist"                # Web Content Settings › URL Whitelist (eine Adresse pro Zeile, * = alles)
_WEB_KEY_HINT = re.compile(r"javascript|jsinterface|integration|whitelist", re.I)
WEB_EVERY = 900.0                             # so oft pro Handy prüfen (listSettings ist gross: nicht bei jeder Abfrage)
WEB_RETRY = 120.0                             # nach einer gescheiterten Prüfung frühestens wieder
WEB_PER_ROUND = 2                             # höchstens so viele Handys pro Runde (die Abfrage soll kurz bleiben)
_web_tried: dict[str, float] = {}


def web_origins(start_url) -> list[str]:
    """Was die Whitelist erlauben muss: die Adresse der Start-URL – beginnt sie mit http://, dazu die HTTPS-Adresse,
    auf die festpos-redirect.py weiterleitet. Ohne brauchbare Start-URL: die feste Adresse der Pi (VIP)."""
    try:
        u = urlsplit(str(start_url or "").strip())
        host, port = u.hostname, u.port
    except ValueError:
        host, port = None, None
    if not host or u.scheme not in ("http", "https") or not _HOST_RE.match(host):
        return [f"https://{dhcp_mod.VIP}"]
    where = f"{host}:{port}" if port else host
    return [f"https://{where}"] if u.scheme == "https" else [f"http://{where}", f"https://{host}"]


def whitelist_lines(value) -> list[str]:
    return [x.strip() for x in str(value or "").splitlines() if x.strip()]


def whitelist_value(start_url) -> str:
    """Die Whitelist, die «Whitelist setzen» einträgt: Startseite und alles darunter, sonst nichts."""
    return "\n".join(f"{o}{tail}" for o in web_origins(start_url) for tail in ("", "/*"))


def _line_ok(line: str, origins: list[str]) -> bool:
    """Erlaubt der Eintrag nur Seiten der Bestell-App? (Ein * vor dem ersten / erlaubte auch fremde Adressen.)"""
    return not re.search(r"[\s,;]", line) and any(line == o or line.startswith(o + "/") for o in origins)


def web_check(settings: dict) -> dict:
    """-> {"state": ok|off|missing|wide|unknown, "foreign": [Einträge, die mehr erlauben] bei wide}"""
    js = next((k for k in JS_KEYS if k in settings), None)
    if js is None:
        return {"state": "unknown", "keys": sorted(k[:40] for k in settings if _WEB_KEY_HINT.search(str(k)))[:8]}
    if not _is_true(settings[js]):
        return {"state": "off"}                       # Schnittstelle aus: nichts offen (Akku, Helligkeit … gehen nicht)
    lines = whitelist_lines(settings.get(WHITELIST_KEY))
    if not lines:
        return {"state": "missing"}
    origins = web_origins(settings.get("startURL"))
    foreign = [x for x in lines if not _line_ok(x, origins)]
    return {"state": "wide", "foreign": [x[:80] for x in foreign[:5]]} if foreign else {"state": "ok"}


async def web_read(args: tuple) -> tuple[str, dict, dict]:
    """Einstellungen lesen und prüfen -> (Zustand der Abfrage, Ergebnis von web_check, Einstellungen)."""
    state, settings = await read_settings(args)
    return state, (web_check(settings) if state == "ok" else {}), settings


async def web_due(cfg: dict) -> None:
    """Nach jeder Fully-Runde (monitor._fully_loop): fällige Handys prüfen, die gerade antworten – die am längsten
    nicht geprüften zuerst, höchstens WEB_PER_ROUND."""
    if not cfg.get("password"):
        return
    now = time.time()
    due = []
    for t in cfg["targets"]:
        seen, last = rt.monitor.fully.get(t["ip"]) or {}, rt.monitor.fully_web.get(t["ip"]) or {}
        if not t.get("active", True) or t["ip"] in _installing or seen.get("state") != "ok" \
                or now - seen.get("at", 0) > 2 * INTERVAL or now - _web_tried.get(t["ip"], 0) < WEB_RETRY:
            continue
        if not last or now - last["at"] >= WEB_EVERY:
            due.append((last.get("at", 0), t))
    for _, t in sorted(due, key=lambda x: x[0])[:WEB_PER_ROUND]:
        _web_tried[t["ip"]] = now
        state, res, _ = await web_read((t["ip"], cfg["port"], t["pin"], cfg["password"]))
        if state == "ok":
            rt.monitor.fully_web_store(t["ip"], res)


def interpret_command(raw: bytes) -> dict:
    """Antwort von Fully auf einen Befehl: {"status": "OK"|"Error", "statustext": …}."""
    status, _, body = _http(raw)
    if status in (401, 403):
        return {"state": "auth"}
    if status != 200:
        return {"state": "bad"}
    try:
        info = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        low = body.lower()
        return {"state": "auth" if b"password" in low or b"login" in low else "bad"}
    if not isinstance(info, dict):
        return {"state": "bad"}
    text = _str(info.get("statustext"), 160) or ""
    st = str(info.get("status", "")).lower()
    if st in ("ok", "success"):
        return {"state": "ok", "text": text}
    if st == "error":
        low = text.lower()
        if "password" in low or "login" in low:
            return {"state": "auth"}
        # z.B. «Missing device owner, system or root rights to reboot the device» – bei Samsung fehlt meist nur
        # «Enable KNOX Features» in Fully
        if "device owner" in low or "root" in low or "provision" in low or "knox" in low:
            return {"state": "no_rights", "text": text}
        return {"state": "refused", "text": text}
    return {"state": "bad"}


async def command_all(targets: list[dict], port: int, password: str, cmd: str) -> dict[str, dict]:
    """Befehl an mehrere Handys gleichzeitig -> {ip: {"name", "state", "text"?}}."""
    res = await asyncio.gather(*(query(t["ip"], port, t["pin"], password, COMMAND_TIMEOUT, cmd=cmd) for t in targets))
    return {t["ip"]: {"name": t["name"], **r} for t, r in zip(targets, res)}


async def _read(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> bytes | None:
    return await rawhttp.read_all(reader, writer, MAX_ANSWER)   # None: viel zu gross, das ist keine Geräteinfo


_http = rawhttp.parse


def interpret(raw: bytes) -> dict:
    """Antwort von Fully deuten (eigene Funktion: ohne Netzwerk testbar)."""
    status, _, body = _http(raw)
    if status in (401, 403):
        return {"state": "auth"}
    if status != 200:
        return {"state": "bad"}
    try:
        info = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        low = body.lower()                         # Anmeldeseite statt Daten: Passwort falsch
        return {"state": "auth" if b"password" in low or b"login" in low else "bad"}
    if not isinstance(info, dict):
        return {"state": "bad"}
    if str(info.get("status", "")).lower() == "error":
        text = str(info.get("statustext", "")).lower()
        return {"state": "auth" if "password" in text or "login" in text or not text else "bad"}
    if "batteryLevel" not in info and "deviceID" not in info:
        return {"state": "bad"}
    return {"state": "ok", "data": pick(info)}


_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _int(v) -> int | None:
    if isinstance(v, bool):
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError, OverflowError):
        return None


def _bool(v) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.lower() in ("true", "false"):
        return v.lower() == "true"
    return None


def _str(v, n: int) -> str | None:
    if not isinstance(v, str):
        return None
    return _CTRL.sub("", v).strip().strip('"')[:n] or None


def _ssid(v) -> str | None:
    """Ab Android 10 ohne Standort-Recht meldet Android nur «<unknown ssid>» – das ist kein Netzname."""
    s = _str(v, 40)
    return None if not s or s.lower() in ("<unknown ssid>", "unknown ssid", "0x") else s


def pick(info: dict) -> dict:
    """Nur die bekannten Werte übernehmen, geprüft und gekürzt – alles andere aus der Antwort fällt weg."""
    battery = _int(info.get("batteryLevel"))
    charging = _bool(info.get("isPlugged"))
    out = {
        "battery": battery if battery is not None and 0 <= battery <= 100 else None,
        "charging": charging if charging is not None else _bool(info.get("plugged")),
        "temp": _int(info.get("batteryTemperature")),
        "screen_on": _bool(info.get("screenOn")),
        "wifi": _int(info.get("wifiSignalLevel")),
        "ssid": _ssid(info.get("SSID")),
        "kiosk": _bool(info.get("kioskMode")),
        "kiosk_locked": _bool(info.get("kioskLocked")),
        "maintenance": _bool(info.get("maintenanceMode")),
        "foreground": _str(info.get("foregroundApp"), 80),
        "page": _str(info.get("currentPage"), 200),
        "storage_free": _int(info.get("internalStorageFreeSpace")),
        "storage_total": _int(info.get("internalStorageTotalSpace")),
        "ram_free": _int(info.get("ramFreeMemory")),
        "fully_version": _str(info.get("appVersionName"), 20),
        "fully_code": _int(info.get("appVersionCode")),         # T6: Vergleich mit der APK auf den Pi
        "device_owner": _bool(info.get("isDeviceOwner")),        # T6: nur dann installiert Android ohne Rückfrage
        "android": _str(info.get("androidVersion"), 20),
        "model": _str(" ".join(x for x in (info.get("deviceManufacturer"), info.get("deviceModel"))
                               if isinstance(x, str)), 60),
        "licensed": _bool(info.get("isLicensed")),
        "device_id": _str(info.get("deviceID"), 64),           # an ihr hängt eine Einzellizenz von Fully
    }
    return {k: v for k, v in out.items() if v is not None}


# -- Einstellungen ----------------------------------------------------------------------------------------
def config() -> dict:
    """Was abgefragt wird: Handys aus «Überwachte Geräte» mit eingerichtetem Zertifikat, Passwort, Port."""
    store = rt.store
    targets = monitor_mod.parse_targets(store.setting("monitor_devices"),
                                        store.setting("monitor_hosts", monitor_mod.DEFAULT_HOSTS))
    try:
        port = int(store.setting("fully_port", str(DEFAULT_PORT)))
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return {"password": store.setting("fully_password", "") or "", "port": port,
            "targets": [{"name": t["name"], "ip": t["ip"], "pin": t["fully_pin"], "active": t["active"]}
                        for t in targets if t["kind"] == "phone" and t.get("fully_pin")]}


def _devices_raw() -> list[dict]:
    try:
        items = json.loads(rt.store.setting("monitor_devices") or "[]")
    except ValueError:
        return []
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


def keep_pins(new: list[dict], old: list[dict]) -> list[dict]:
    """Beim Speichern der Geräteliste: eingerichtete Fully-Zertifikate, die MAC (feste DHCP-Zuteilung, dhcp.py) und die
    Geräte-ID mit Zertifikat und Rolle (T1) behalten – die Liste in der Verwaltung schickt sie nicht mit. Zuordnung
    über die ID, sonst gleiche IP, sonst gleicher Name."""
    olds = [o for o in old if o.get("kind") == "phone"]
    out = []
    for d in new:
        d = {k: v for k, v in d.items() if k not in ("fully_pin", "fully_until", "mac", "cert", "role")}
        if d.get("kind") == "phone":
            hit = next((o for o in olds if d.get("id") and o.get("id") == d.get("id")), None) or \
                next((o for o in olds if o.get("ip") == d.get("ip")), None) or \
                next((o for o in olds if o.get("name") == d.get("name")), None)
            if not d.get("id"):
                d.pop("id", None)
            if hit:
                olds.remove(hit)
                if PIN_RE.match(str(hit.get("fully_pin") or "")):
                    d["fully_pin"], d["fully_until"] = hit["fully_pin"], hit.get("fully_until")
                for k in ("mac", "id", "cert", "role"):
                    if hit.get(k):
                        d[k] = hit[k]
        else:
            d.pop("id", None)
        out.append(d)
    return out


def _set_pin(ip: str, pin: str | None, until: str | None) -> dict:
    items = _devices_raw()
    hit = next((d for d in items if d.get("kind") == "phone" and d.get("ip") == ip), None)
    if not hit:
        raise HTTPException(404, i18n.t("err.fully.not_listed"))
    if pin:
        hit["fully_pin"], hit["fully_until"] = pin, until
    else:
        hit.pop("fully_pin", None)
        hit.pop("fully_until", None)
    rt.store.execute(*device_ids.save_phones(items))
    return hit


# -- Verwaltung -------------------------------------------------------------------------------------------
admin_router = APIRouter(prefix="/api/admin/fully", dependencies=[Depends(admin)])


class FullyDeviceIn(BaseModel):
    ip: str = Field(pattern=monitor_mod.HOST_RE.pattern)


@admin_router.get("")
async def fully_overview():
    """Letzte Abfrage pro Handy (aus dem Speicher dieses Pi)."""
    cfg = await db(config)
    return {"password_set": bool(cfg["password"]), "port": cfg["port"], "results": rt.monitor.fully_view()}


@admin_router.post("/cert")
async def fully_cert(d: FullyDeviceIn):
    """Neues Zertifikat für Fully auf diesem Handy. Das bisherige gilt danach nicht mehr."""
    item = next((x for x in await db(_devices_raw) if x.get("kind") == "phone" and x.get("ip") == d.ip), None)
    if not item:
        raise HTTPException(404, i18n.t("err.fully.not_listed"))
    p12, pin, until = await asyncio.to_thread(make_cert, str(item.get("name") or d.ip), d.ip)
    await db(_set_pin, d.ip, pin, until)
    rt.monitor.fully_forget(d.ip)
    return Response(p12, media_type="application/x-pkcs12", headers={
        "Content-Disposition": f'attachment; filename="{P12_NAME}"', "Cache-Control": "no-store"})


@admin_router.post("/forget")
async def fully_forget(d: FullyDeviceIn):
    """Fully bei diesem Handy nicht mehr abfragen (Zertifikat vergessen)."""
    await db(_set_pin, d.ip, None, None)
    rt.monitor.fully_forget(d.ip)
    return {"ok": True}


@admin_router.post("/check")
async def fully_check(d: FullyDeviceIn):
    """Sofort abfragen (Knopf «Prüfen»)."""
    cfg = await db(config)
    target = next((x for x in cfg["targets"] if x["ip"] == d.ip), None)
    if not target:
        raise HTTPException(422, i18n.t("err.fully.no_cert"))
    if not cfg["password"]:
        raise HTTPException(422, i18n.t("err.fully.no_password"))
    args = (d.ip, cfg["port"], target["pin"], cfg["password"])
    res = await query(*args)
    rt.monitor.fully_store(target["name"], d.ip, res)
    if res.get("state") == "ok":                          # gleich auch die JavaScript-Schnittstelle (Whitelist)
        _web_tried[d.ip] = time.time()
        state, web, _ = await web_read(args)
        if state == "ok":
            rt.monitor.fully_web_store(d.ip, web)
    return rt.monitor.fully_view().get(d.ip, {})


@admin_router.post("/whitelist")
async def fully_whitelist(d: FullyDeviceIn):
    """URL Whitelist auf die Adresse der Bestell-App setzen (aus der Start-URL dieses Handys) – ab Festleitung.
    Ersetzt die bisherige Whitelist ganz; die JavaScript-Schnittstelle selbst bleibt, wie sie ist."""
    cfg, target = await _ready(d.ip)
    args = (d.ip, cfg["port"], target["pin"], cfg["password"])
    state, before, settings = await web_read(args)
    if state != "ok":
        return {"state": state}
    if before["state"] == "unknown":
        raise HTTPException(409, i18n.t("err.fully.whitelist_unknown"))
    value = whitelist_value(settings.get("startURL"))
    res = await setup_command(*args, "setStringSetting", key=WHITELIST_KEY, value=value)
    if res["state"] not in ("ok", "sent"):
        return {"state": "failed", "text": res.get("text") or res["state"]}
    state, after, _ = await web_read(args)                # nachlesen: hat Fully sie übernommen?
    if state == "ok":
        rt.monitor.fully_web_store(d.ip, after)
    log.info("Fully-Whitelist auf %s (%s) gesetzt: %s -> %s", target["name"], d.ip, before["state"],
             after.get("state", state))
    return {"state": "ok", "web": after if state == "ok" else None, "lines": whitelist_lines(value)}


KIOSK_SETTLE = 0.8   # Sekunden, bis Fully nach Sperren/Entsperren den neuen Zustand meldet


BRIGHTNESS = "brightness"   # Helligkeit 0–255 (setStringSetting screenBrightness) – der einzige Befehl mit Wert


class FullyCommandIn(BaseModel):
    ip: str = Field(pattern=monitor_mod.HOST_RE.pattern)
    cmd: str = Field(pattern="^(" + "|".join((*COMMANDS, BRIGHTNESS)) + ")$")
    value: int | None = Field(default=None, ge=0, le=255)


async def _setting(ip: str, port: int, pin: str, password: str, cmd: str, key: str, value: str) -> dict:
    """Eine einzelne Fully-Einstellung (nur die festen Fälle von fully_command), Ergebnis wie ein Befehl."""
    if pin == PLAIN:
        return {"state": "cert"}
    state, raw = await _exchange(ip, port, pin, password, {"cmd": cmd, "key": key, "value": value}, COMMAND_TIMEOUT)
    return _command_result(state, raw)


@admin_router.post("/command")
async def fully_command(c: FullyCommandIn):
    """Befehl an Fully auf einem Handy (App/Handy neu starten, ausschalten, Bildschirm, Helligkeit, Kiosk, Seite neu
    laden, Cache leeren, in den Vordergrund) – ab Festleitung (Recht «Geräte»)."""
    cfg = await db(config)
    target = next((x for x in cfg["targets"] if x["ip"] == c.ip), None)
    if not target:
        raise HTTPException(422, i18n.t("err.fully.no_cert"))
    if not cfg["password"]:
        raise HTTPException(422, i18n.t("err.fully.no_password"))
    args = (c.ip, cfg["port"], target["pin"], cfg["password"])
    if c.cmd == BRIGHTNESS:
        if c.value is None:
            raise HTTPException(422, i18n.t("err.fully.no_value"))
        res = await _setting(*args, "setStringSetting", "screenBrightness", str(c.value))
        log.info("Fully-Helligkeit %s an %s (%s): %s", c.value, target["name"], c.ip, res.get("state"))
        return {"name": target["name"], **res}
    if c.cmd == "lockKiosk":                          # Kiosk-Modus ganz aus? Frisch fragen, nicht dem letzten Stand trauen
        info = await query(*args)
        if info.get("state") == "ok":
            rt.monitor.fully_store(target["name"], c.ip, info)
        if info.get("state") != "ok" or info["data"].get("kiosk") is not True:
            await _setting(*args, "setBooleanSetting", "kioskMode", "true")    # aus oder unbekannt: zuerst wieder ein
    res = await query(*args, COMMAND_TIMEOUT, cmd=c.cmd)
    if c.cmd == "clearCache" and res.get("state") in ("ok", "sent"):
        again = await query(*args, COMMAND_TIMEOUT, cmd="loadStartURL")    # danach die App frisch laden
        res["reload"] = again.get("state")                                 # nicht geklappt: die Verwaltung sagt es
    log.info("Fully-Befehl %s an %s (%s): %s", c.cmd, target["name"], c.ip, res.get("state"))
    if c.cmd in KIOSK_COMMANDS and res.get("state") in ("ok", "sent"):
        await asyncio.sleep(KIOSK_SETTLE)                 # gleich nachfragen: die Anzeige zeigt den neuen Zustand
        info = await query(c.ip, cfg["port"], target["pin"], cfg["password"])
        rt.monitor.fully_store(target["name"], c.ip, info)
        if info.get("state") == "ok":
            res["kiosk_locked"] = info["data"].get("kiosk_locked")
    return {"name": target["name"], **res}


async def _ready(ip: str) -> tuple[dict, dict]:
    """Handy mit eingerichtetem Fully und Passwort -> (Einstellungen, Handy). Vorher einmal abfragen: so steht
    fest, dass Passwort und Zertifikat stimmen, bevor eine Datei hinausgeht."""
    cfg = await db(config)
    target = next((x for x in cfg["targets"] if x["ip"] == ip), None)
    if not target:
        raise HTTPException(422, i18n.t("err.fully.no_cert"))
    if not cfg["password"]:
        raise HTTPException(422, i18n.t("err.fully.no_password"))
    info = await query(ip, cfg["port"], target["pin"], cfg["password"])
    rt.monitor.fully_store(target["name"], ip, info)
    if info["state"] != "ok":
        raise HTTPException(409, i18n.t("err.fully.not_ready", state=i18n.t(f"admin.fully.st_{info['state']}")))
    return cfg, target


files_router = APIRouter()


@files_router.get("/api/device-cert/fully/{token}")
async def fully_zip(token: str, request: Request):
    """ZIP für Fully, genau einmal – nur für den Pi selbst: festpos-redirect.py reicht sie auf Port 80 weiter."""
    if (request.client.host if request.client else None) not in LOCAL_HOSTS:
        raise HTTPException(403)
    got = zips.take(token)
    if not got:
        raise HTTPException(404)
    return Response(got[0], media_type="application/zip", headers={"Cache-Control": "no-store"})


_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,63}$")


def _pi_host(request: Request) -> str:
    """Adresse dieses Pi, wie die Verwaltung ihn erreicht – dort holt Fully die ZIP ab (im Cluster liegt die
    Einmal-Adresse nur im Speicher des Pi, der die Verwaltung gerade zeigt)."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0]
    return host if _HOST_RE.match(host) else "192.168.55.10"


@admin_router.post("/ca")
async def fully_install_ca(d: FullyDeviceIn, request: Request):
    """CA des Servers über Fully auf dem Handy installieren: als ZIP abholen lassen, dann installUserCa mit
    file:// (http:// direkt hat am Handy nicht sicher geklappt)."""
    if not Config.cafile or not Path(Config.cafile).is_file():
        raise HTTPException(503, i18n.t("err.fully.no_ca"))
    data = await asyncio.to_thread(Path(Config.cafile).read_bytes)
    cfg, target = await _ready(d.ip)
    args = (d.ip, cfg["port"], target["pin"], cfg["password"])
    res = await deliver(args, _pi_host(request), {CA_FILE: data})
    if res["state"] != "ok":
        return {"step": "download", **res}
    res = await setup_command(*args, "installUserCa", url=f"file://{FILES_DIR}/{CA_FILE}")
    log.info("CA über Fully auf %s (%s): %s", target["name"], d.ip, res["state"])
    return {"step": "install", **res}


class FullyDeviceCertIn(BaseModel):
    ip: str = Field(pattern=monitor_mod.HOST_RE.pattern)
    name: str = Field(min_length=1, max_length=40, pattern=r"^[\w .\-äöüÄÖÜ]+$")


@admin_router.post("/device-cert")
async def fully_device_cert(d: FullyDeviceCertIn, request: Request):
    """Gerätezertifikat ausstellen und über Fully einrichten (Knopf in der Geräteansicht)."""
    cfg, target = await _ready(d.ip)
    return await install_device_cert(d.ip, d.name.strip(), cfg, target, _pi_host(request),
                                     (current_user(request) or {}).get("name") or "?")


_installing: set[str] = set()       # Handys (IP), auf die gerade ein Gerätezertifikat geht – Knopf und Automatik (T3)


async def install_device_cert(ip: str, name: str, cfg: dict, target: dict, host: str, actor: str, via: str = "fully") -> dict:
    """Gerätezertifikat ausstellen und über Fully einrichten: als ZIP abholen lassen, clientCaUrl/clientCaPassword
    setzen, App neu starten. Gleicher Name wie ein gültiges Zertifikat = erneuern; das alte sperrt der Server, sobald
    das Handy das neue benutzt (devices.retire_replaced). Fully muss bereit sein (_ready).
    -> {"step": download|settings|restart, "state", …, "serial" (nur wenn es auf dem Handy eingerichtet ist)}"""
    if ip in _installing:
        raise HTTPException(409, i18n.t("err.fully.cert_busy"))
    _installing.add(ip)
    try:
        args = (ip, cfg["port"], target["pin"], cfg["password"])
        phone = next((x for x in await db(_devices_raw) if x.get("kind") == "phone" and x.get("ip") == ip), {})
        dev = phone.get("cert")                              # Gerät dieses Handys (T1) – sonst ein neues
        await db(devices.check_name, name, dev, phone.get("id"))
        replaces = await db(devices.current_serial, dev) if dev else None
        cert = await db(devices.issue_p12, name, replaces, secrets.token_urlsafe(24), dev)
        res = await deliver(args, host, {cert["filename"]: cert["p12"]})
        if res["state"] != "ok":
            await db(devices.drop_undelivered, cert["serial"], False)    # nicht abgeholt: nichts auf dem Handy
            return {"step": "download", **res}
        for key, value in (("clientCaUrl", f"file://{FILES_DIR}/{cert['filename']}"),
                           ("clientCaPassword", cert["password"])):
            res = await setup_command(*args, "setStringSetting", key=key, value=value)
            if res["state"] not in ("ok", "sent"):
                await db(devices.drop_undelivered, cert["serial"], True)
                return {"step": "settings", **res}
        if phone.get("id"):                                # Handy und Zertifikat gehören zusammen (Rolle geht mit)
            await db(devices.link_phone, phone["id"], cert["device_id"])
        res = await setup_command(*args, "restartApp")    # Fully lädt das Zertifikat neu, die App meldet sich damit
        await db(lb.audit, rt.store, actor, "device_cert", name=name, expires_at=cert["expires_at"], via=via)
        log.info("Gerätezertifikat %s (%s) über Fully auf %s (%s) eingerichtet%s", name, cert["serial"][:8],
                 target["name"], ip, f", ersetzt {replaces[:8]}" if replaces else "")
        return {"step": "restart", **res, "name": name, "expires_at": cert["expires_at"], "renewed": bool(replaces),
                "serial": cert["serial"], "device_id": cert["device_id"]}
    finally:
        _installing.discard(ip)


# -- Automatisch erneuern (T3) -----------------------------------------------------------------------------------
# Nach jeder Fully-Runde (monitor._fully_loop, nur der Pi, mit dem die Handys reden). Ein Gerät nach dem anderen und nur,
# wenn es gerade niemand braucht. Das alte Zertifikat gilt, bis das Handy das neue benutzt – lädt Fully das neue nicht,
# spielt der Pi gleich noch eines ein (die Fernverwaltung hat ihr eigenes Zertifikat); nach RENEW_TRIES Fehlschlägen
# an einem Tag bleibt es für heute und die Verwaltung meldet «von Hand» (devices.renew_modes).
RENEW_WAIT = 600.0                  # so lange darf das Handy brauchen, bis es sich mit dem neuen Zertifikat meldet
RENEW_IDLE = 600.0                  # nicht stören: so lange keine Bestellung (dieses Handy; alle, wenn der Bildschirm an ist)
AUTO_ACTOR = "FestPOS"              # im Protokoll


def _renew_check(now: float) -> tuple[bool, list[dict]]:
    """Laufende Erneuerung prüfen (fertig, gescheitert, noch warten) und fällige Geräte suchen.
    -> (eine läuft noch: dann nichts Neues, Kandidaten nach Dringlichkeit)"""
    store = rt.store
    state = devices.renew_state()
    lins = device_ids.lineages(store)
    serials = {d.get("serial") for d in rt.monitor.device_list() if d.get("seconds", 1e9) < monitor_mod.ONLINE_SECONDS}
    for did, st in list(state.items()):
        if st.get("state") != "running":
            continue
        new = next((c for c in lins.get(did, {}).get("certs", []) if c["serial"] == st.get("serial")), None)
        old = next((c for c in lins.get(did, {}).get("certs", []) if c["serial"] == st.get("from")), None)
        if new and not new["revoked_at"] and (st.get("serial") in serials or (old and old["revoked_at"])):
            devices.save_renew_state(did, {"state": "ok", "from": st.get("from"), "serial": st.get("serial"), "at": now,
                                           "day": st.get("day"), "tries": st.get("tries", 1)})
            log.info("Gerätezertifikat %s automatisch erneuert (%s)", lins[did]["name"], str(st.get("serial"))[:8])
            continue
        if now - float(st.get("at") or 0) < RENEW_WAIT and new and not new["revoked_at"]:
            return True, []                                  # Handy startet noch neu: warten
        if new:                                               # Fully hat es nicht geladen: weg damit, das alte gilt noch
            devices.drop_undelivered(new["serial"], True)
        _renew_failed(did, st, "restart", "not_seen", now)
        state = devices.renew_state()
    days = devices.renew_days()
    if not days or not (Config.device_ca_key and Path(Config.device_ca_key).exists()) or now < timesync.time_floor(store):
        return False, []                                     # aus, keine Geräte-CA, Uhr sicher falsch
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    lins = device_ids.lineages(store)                        # frisch: oben evtl. ein Zertifikat gesperrt
    blocked = device_ids.blocked(lins)
    out = []
    for did, phone in devices.auto_phones().items():
        x = lins.get(did)
        cur = devices.newest_valid(x) if x and not x["removed"] and did not in blocked else None
        left = devices.days_left(cur) if cur else None
        if left is None or left > devices.renew_threshold(cur):
            continue
        made = timesync.local_epoch(cur["created_at"])
        if made is None or now - made < 86400:              # frisch ausgestellt: nie gleich wieder (Uhr, Einstellung)
            continue
        st = state.get(did) or {}
        if st.get("from") == cur["serial"] and st.get("day") == today and \
                (st.get("state") == "failed" or st.get("tries", 0) >= devices.RENEW_TRIES):
            continue
        out.append({"device_id": did, "ip": phone["ip"], "name": x["name"], "serial": cur["serial"], "days_left": left,
                    "serials": [c["serial"] for c in x["certs"]], "names": sorted({c["name"] for c in x["certs"]}),
                    "tries": st.get("tries", 0) if st.get("from") == cur["serial"] and st.get("day") == today else 0})
    return False, sorted(out, key=lambda c: c["days_left"])


def _renew_failed(did: str, st: dict, step: str, detail: str, now: float) -> None:
    tries = int(st.get("tries") or 0) + (0 if st.get("state") == "running" else 1)
    devices.save_renew_state(did, {"state": "failed" if tries >= devices.RENEW_TRIES else "retry", "from": st.get("from"),
                                   "at": now, "day": st.get("day"), "tries": tries, "step": step, "detail": detail})
    lb.audit(rt.store, AUTO_ACTOR, "device_cert_auto_failed", device=did, step=step, state=detail, tries=tries)
    log.warning("Gerätezertifikat automatisch erneuern: %s gescheitert bei %s (%s), Versuch %d", did, step, detail, tries)


def _idle(c: dict, now: float, screen_on) -> bool:
    """Braucht das Handy gerade jemand? Nein, wenn keine Bestellung wartet, es seit RENEW_IDLE nichts bestellt hat und
    (Bildschirm aus oder seit RENEW_IDLE niemand im ganzen Fest bestellt hat)."""
    for d in rt.monitor.device_list():
        if d.get("serial") in c["serials"] and d.get("seconds", 1e9) < monitor_mod.ONLINE_SECONDS and d.get("outbox"):
            return False
    ph = ",".join("?" * len(c["names"]))
    last = timesync.local_epoch(rt.store.scalar(f"SELECT MAX(created_at) FROM orders WHERE device_cert IN ({ph})", tuple(c["names"])))
    if last and now - last < RENEW_IDLE:
        return False
    if screen_on is not False:
        last = timesync.local_epoch(rt.store.scalar("SELECT MAX(created_at) FROM orders"))
        if last and now - last < RENEW_IDLE:
            return False
    return True


def renew_host() -> str:
    """Adresse dieses Pi für Fully (holt die ZIP hier ab – die Einmal-Adresse liegt nur in seinem Speicher)."""
    return Config.node_ip if _HOST_RE.match(Config.node_ip or "") else "192.168.55.10"


async def auto_renew(cfg: dict) -> str | None:
    """Ein fälliges Gerät erneuern, wenn es geht (monitor._fully_loop, nach poll_fully). -> dessen Geräte-ID oder None."""
    if not cfg.get("password"):
        return None
    now = time.time()
    busy, cands = await db(_renew_check, now)
    if busy:
        return None
    pins = {t["ip"]: t for t in cfg["targets"]}
    for c in cands:
        target, seen = pins.get(c["ip"]), rt.monitor.fully.get(c["ip"]) or {}
        if not target or seen.get("state") != "ok" or now - seen.get("at", 0) > 2 * INTERVAL or c["ip"] in _installing:
            continue                                          # Fully gerade nicht erreichbar: nächste Runde
        if not await db(_idle, c, now, (seen.get("data") or {}).get("screen_on")):
            continue
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        st = {"from": c["serial"], "day": day, "tries": c["tries"]}
        try:
            res = await install_device_cert(c["ip"], c["name"], cfg, target, renew_host(), AUTO_ACTOR, via="auto")
        except HTTPException as e:                            # z. B. Name inzwischen vergeben, keine CA
            await db(_renew_failed, c["device_id"], st, "issue", str(e.detail)[:120], now)
            return c["device_id"]
        if res.get("serial"):                                 # liegt auf dem Handy: warten, bis es sich damit meldet
            await db(devices.save_renew_state, c["device_id"],
                     {"state": "running", "from": c["serial"], "serial": res["serial"], "at": now, "day": day,
                      "tries": c["tries"] + 1})
        else:
            await db(_renew_failed, c["device_id"], st, res.get("step", "?"), res.get("state", "?"), now)
        return c["device_id"]
    return None


# -- Vorlage in der Verwaltung (ändern nur Admin, anwenden ab Festleitung) -------------------------------------------------------------------
# Kiosk-PIN: gehört zur Vorlage, ist aber ein Geheimnis – eigenes Feld, nie zurückgegeben, nicht in der heruntergeladenen
# Vorlage und nicht in der Sicherung. Fully zeigt ihn nur verschlüsselt (kioskPinEnc): FestPOS merkt sich nach dem
# Setzen diesen Wert (fully_kiosk_pin_enc) und erkennt so Abweichungen. Verschlüsselt Fully pro Gerät anders, wird der
# PIN beim Abgleichen eben jedes Mal neu gesetzt – das schadet nicht.
KIOSK_PIN_RE = re.compile(r"^[0-9A-Za-z]{1,32}$")


def kiosk_pin() -> str:
    return rt.store.setting("fully_kiosk_pin", "") or ""


@admin_router.get("/template")
async def fully_template():
    """Vorlage der Fully-Einstellungen (ohne Geheimes – siehe template_key)."""
    t = await db(load_template)
    return {"settings": t.get("settings", {}), "source": t.get("source"), "at": t.get("at"),
            "skipped": sorted(TEMPLATE_SKIP), "forced": TEMPLATE_FORCE, "pin_set": bool(await db(kiosk_pin))}


class PinIn(BaseModel):
    pin: str = Field(default="", max_length=32)
    clear: bool = False


@admin_router.put("/template/pin")
async def fully_template_pin(p: PinIn):
    """Kiosk-PIN für alle Handys setzen oder entfernen (leer und ohne clear = unverändert)."""
    if p.pin and not KIOSK_PIN_RE.match(p.pin):
        raise HTTPException(422, i18n.t("err.fully.pin_bad"))

    def save():
        if p.pin or p.clear:
            _put_setting("fully_kiosk_pin", "" if p.clear else p.pin)
            _put_setting("fully_kiosk_pin_enc", "")           # neuer PIN: verschlüsselten Wert neu lernen
    await db(save)
    return {"pin_set": bool(await db(kiosk_pin))}


@admin_router.post("/template/from")
async def fully_template_from(d: FullyDeviceIn):
    """Vorlage von diesem Handy übernehmen: alle Einstellungen, ohne Geheimes und ohne was pro Handy anders ist."""
    cfg, target = await _ready(d.ip)
    state, current = await read_settings((d.ip, cfg["port"], target["pin"], cfg["password"]))
    if state != "ok":
        return {"state": state}
    t = await db(save_template, clean_template(current), target["name"])
    log.info("Fully-Vorlage von %s (%s) übernommen: %d Einstellungen", target["name"], d.ip, len(t["settings"]))
    return {"state": "ok", "count": len(t["settings"]), "source": t["source"], "at": t["at"]}


class TemplateIn(BaseModel):
    settings: dict


@admin_router.put("/template")
async def fully_template_put(t: TemplateIn):
    """Vorlage als JSON (heruntergeladen, bearbeitet, wieder hochgeladen). Was nicht hineingehört, fällt weg."""
    settings = clean_template(t.settings)
    if len(settings) <= len(TEMPLATE_FORCE):
        raise HTTPException(422, i18n.t("err.fully.template_bad"))
    saved = await db(save_template, settings, i18n.t("admin.fully.template_file"))
    return {"state": "ok", "count": len(saved["settings"]), "source": saved["source"], "at": saved["at"],
            "dropped": sorted(k for k in t.settings if k not in settings)[:50]}


class FullySettingsIn(BaseModel):
    ip: str = Field(pattern=monitor_mod.HOST_RE.pattern)
    apply: bool = False


@admin_router.post("/settings")
async def fully_settings(d: FullySettingsIn):
    """Handy mit der Vorlage vergleichen; mit apply=true die abweichenden Einstellungen setzen."""
    template = {k: v for k, v in ((await db(load_template)).get("settings") or {}).items() if template_key(k)}
    pin = await db(kiosk_pin)
    if not template and not pin:
        raise HTTPException(422, i18n.t("err.fully.no_template"))
    cfg, target = await _ready(d.ip)
    args = (d.ip, cfg["port"], target["pin"], cfg["password"])
    state, current = await read_settings(args)
    if state != "ok":
        return {"state": state}
    diff = differences(template, current)
    known_enc = await db(rt.store.setting, "fully_kiosk_pin_enc", "")
    enc = str(current.get("kioskPinEnc") or "")
    if pin and (not known_enc or enc != known_enc):
        if enc and await db(rollout_pin_given, d.ip, pin):
            await db(_put_setting, "fully_kiosk_pin_enc", enc)   # beim Ausrollen schon gesetzt: nur merken
        else:
            diff["kioskPin"] = pin                        # in der Antwort steht nur der Name, nie der PIN
    if not d.apply:
        return {"state": "ok", "different": sorted(diff), "changed": [], "failed": []}
    changed, failed = await apply_settings(args, diff)
    if "kioskPin" in diff:                                # verschlüsselten Wert merken (zum Vergleichen)
        state, after = await read_settings(args)
        new_enc = str(after.get("kioskPinEnc") or "") if state == "ok" else ""
        if "kioskPin" in failed and new_enc and new_enc != enc:
            del failed["kioskPin"]                        # Fully meldet einen Fehler, hat den PIN aber übernommen
            changed.append("kioskPin")
        if "kioskPin" in changed and new_enc:
            await db(_put_setting, "fully_kiosk_pin_enc", new_enc)
    log.info("Fully-Vorlage auf %s (%s): %d gesetzt, %d fehlgeschlagen", target["name"], d.ip, len(changed), len(failed))
    return {"state": "ok", "different": sorted(diff), "changed": changed, "failed": sorted(failed),
            "errors": failed}


# -- Neues Handy ausrollen (nur Admin) ------------------------------------------------------------------------
# 1. Einrichtungs-QR: Android macht Fully beim Einrichten eines frisch zurückgesetzten Handys zum Geräteinhaber
#    (6× auf den Willkommensbildschirm tippen, scannen). Inhalt wie der QR-Code aus Fully selbst.
# 2. fully_bootstrap: das neue Handy bekommt sein Zertifikat für die Fernverwaltung – danach geht alles über HTTPS
#    (CA, Gerätezertifikat, Vorlage: dieselben Routen wie oben; die Verwaltung ruft sie der Reihe nach auf).
PROV_COMPONENT = "com.fullykiosk.emm/de.ozerov.fully.DeviceOwnerReceiver"
PROV_DEFAULTS = {"apk_url": "https://www.fully-kiosk.com/files/2026/08/Fully-Kiosk-Browser-v1.61.2-emm.apk",
                 "checksum": "h7lwsUsSv8aWEju9W6YPFR3ZgE5AH8VkXY9z407d-n0", "code": "", "ssid": "", "security": "WPA",
                 "apk_local": "1"}
PROV_KEYS = tuple(PROV_DEFAULTS)
NO_CLOUD_CODE = "FFF"                         # kein Fully-Cloud-Profil (siehe provision_json)
RESTART_WAIT = 25.0                           # nach dem Neustart der App: so lange auf HTTPS warten
BOOT_WAIT = 180.0                             # nach dem Neustart des Handys: so lange auf HTTPS warten


def _put_setting(key: str, value: str) -> None:
    rt.store.execute("INSERT INTO settings(key,value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, value))


def provision_config() -> dict:
    s = rt.store.setting
    c = {k: s(f"fully_prov_{k}", v) if s(f"fully_prov_{k}", None) is not None else v for k, v in PROV_DEFAULTS.items()}
    return {**c, "wifi_password_set": bool(s("fully_prov_wifi_password", "")),
            "volume_key_set": bool(s("fully_volume_key", ""))}


# -- Fully-APK auf dem Pi: Android lädt sie beim Einrichten von hier (http://<Pi>/fully/fully.apk, ausgeliefert von
# festpos-redirect.py), «Fully aktualisieren» ebenso. Liegt nur auf dem Pi, auf dem sie hochgeladen wurde.
APK_DIR = Path(os.environ.get("FESTPOS_FULLY_DIR", "/var/lib/festpos/fully"))
APK_FILE = "fully.apk"
APK_MAX = 200 * 1024 * 1024


def apk_info() -> dict | None:
    """Die APK auf diesem Pi -> {checksum, size, name, at} oder None."""
    try:
        info = json.loads((APK_DIR / "fully.json").read_text())
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) and (APK_DIR / APK_FILE).is_file() else None


def apk_checksum(path: Path) -> str:
    """Prüfsumme, wie Android sie im Einrichtungs-QR erwartet: SHA-256, Base64 (URL-sicher, ohne «=»)."""
    import base64
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return base64.urlsafe_b64encode(h.digest()).decode().rstrip("=")


def provision_json(host: str, settings_url: str = "") -> str:
    """Inhalt des Einrichtungs-QR (Android Enterprise, wie der QR-Code aus Fully). settings_url: Fully lädt beim
    Einrichten seine Einstellungen von dort (FULLY_SETTINGS_DOWNLOAD_LOCATION)."""
    c = provision_config()
    p = "android.app.extra.PROVISIONING_"
    if c["apk_local"] == "1":                  # massgebliche APK, von diesem oder einem anderen Pi, der sie hat (T6)
        from . import fully_apk
        url = fully_apk.apk_url(host)
        checksum = (fully_apk.meta() or apk_info() or {})["checksum"]
    else:
        url, checksum = c["apk_url"], c["checksum"]
    data = {f"{p}DEVICE_ADMIN_COMPONENT_NAME": PROV_COMPONENT,
            f"{p}DEVICE_ADMIN_PACKAGE_DOWNLOAD_LOCATION": url,
            f"{p}DEVICE_ADMIN_PACKAGE_CHECKSUM": checksum,
            f"{p}LOCALE": "de_CH", f"{p}TIME_ZONE": "Europe/Zurich",
            f"{p}SKIP_ENCRYPTION": True, f"{p}KEEP_SCREEN_ON": True}
    if c["ssid"]:
        data[f"{p}WIFI_SSID"] = c["ssid"]
        data[f"{p}WIFI_SECURITY_TYPE"] = c["security"]
        pw = rt.store.setting("fully_prov_wifi_password", "") or ""
        if pw and c["security"] != "NONE":
            data[f"{p}WIFI_PASSWORD"] = pw
    # Ohne Code fragt die Fully-Variante für Geräteinhaber beim Einrichten nach einem «Provisioning Code» (Fully Cloud).
    # Mit «FFF» (wie im ersten QR-Code aus Fully) kommt die Frage nicht – am XCover so ausprobiert.
    extras = {"FULLY_PROVISIONING_CODE": c["code"] or NO_CLOUD_CODE}
    if settings_url:
        extras["FULLY_SETTINGS_DOWNLOAD_LOCATION"] = settings_url
    if extras:
        data[f"{p}ADMIN_EXTRAS_BUNDLE"] = extras
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


@admin_router.get("/provision")
async def fully_provision():
    """Einstellungen für den Einrichtungs-QR (WLAN-Passwort nie zurück); dazu, ob DHCP auf diesem Pi läuft."""
    return {**await db(provision_config), "dhcp": dhcp_mod.enabled(), "share": await db(share_view)}


class ProvisionIn(BaseModel):
    # Adresse und Prüfsumme nur bei «APK aus dem Internet» Pflicht; bei «von diesem Pi» leer = bleibt (T6)
    apk_url: str = Field(default="", max_length=300, pattern=r"^(https?://[^\s\"\\]+)?$")
    checksum: str = Field(default="", max_length=100, pattern=r"^([A-Za-z0-9_\-+/=]{20,100})?$")
    code: str = Field(default="", max_length=40, pattern=r"^[A-Za-z0-9_\-]*$")
    ssid: str = Field(default="", max_length=32, pattern=r"^[^\x00-\x1f\x7f\"\\]*$")
    security: str = Field(default="WPA", pattern="^(WPA|WEP|NONE)$")
    apk_local: str = Field(default="1", pattern="^[01]$")
    wifi_password: str | None = Field(default=None, max_length=63, pattern=r"^[^\x00-\x1f\x7f\"\\]*$")  # leer = unverändert
    volume_key: str | None = Field(default=None, max_length=200, pattern=r"^[^\s\"\\]*$")           # leer = unverändert
    volume_key_clear: bool = False


@admin_router.put("/provision")
async def fully_provision_put(p: ProvisionIn):
    if p.apk_local == "0" and not (p.apk_url.strip() and p.checksum.strip()):
        raise HTTPException(422, i18n.t("err.fully.apk_url_needed"))

    def save():
        for k in PROV_KEYS:
            if k in ("apk_url", "checksum") and not getattr(p, k).strip():
                continue                                   # APK vom Pi: gespeicherte Adresse bleibt
            _put_setting(f"fully_prov_{k}", getattr(p, k).strip())
        if p.wifi_password:
            _put_setting("fully_prov_wifi_password", p.wifi_password)
        if p.volume_key:
            _put_setting("fully_volume_key", p.volume_key)
        elif p.volume_key_clear:
            _put_setting("fully_volume_key", "")
    await db(save)
    return await db(provision_config)


@admin_router.get("/provision/qr")
async def fully_provision_qr(request: Request):
    """Einrichtungs-QR als SVG. Enthält das WLAN-Passwort – nur anzeigen, nicht ausdrucken oder speichern."""
    from . import qr
    return {"svg": qr.qr_svg(await db(provision_json, _pi_host(request)))}


@admin_router.get("/apk")
async def fully_apk():
    """APK auf diesem Pi, die massgebliche für alle Pi, Stand pro Pi und pro Handy (T6)."""
    from . import fully_apk as fa
    return {"apk": await asyncio.to_thread(apk_info), "node": Config.node, **await db(fa.overview)}


@admin_router.post("/apk")
async def fully_apk_upload(request: Request, apk: UploadFile = File(...)):
    """Fully-APK auf diesen Pi laden (von fully-kiosk.com, Variante für Geräteinhaber: …-emm.apk)."""
    APK_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
    tmp = APK_DIR / f"upload-{secrets.token_hex(6)}.part"
    try:
        n, head = 0, b""
        with open(tmp, "wb") as f:
            while chunk := await apk.read(1 << 20):
                head = head or chunk[:4]
                n += len(chunk)
                if n > APK_MAX:
                    raise HTTPException(413, i18n.t("err.fully.apk_too_large", mb=APK_MAX // (1024 * 1024)))
                f.write(chunk)
        if head != b"PK\x03\x04":                     # eine APK ist ein ZIP
            raise HTTPException(422, i18n.t("err.fully.apk_bad"))
        from . import fully_apk as fa
        name = (apk.filename or "")[:120]
        man = await asyncio.to_thread(fa.apk_manifest, tmp, name)
        if man.get("package") and man["package"] != fa.PACKAGE:       # z.B. die Variante aus dem Play Store (T6)
            raise HTTPException(422, i18n.t("err.fully.apk_variant", package=man["package"]))
        checksum = await asyncio.to_thread(apk_checksum, tmp)
        info = {"checksum": checksum, "size": n, "name": name, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), **man}
        await asyncio.to_thread(fa.write_local, tmp, info)
        await db(fa.save_meta, info, Config.node)               # gilt für alle Pi: die anderen holen sie (fully_apk.py)
        await db(lb.audit, rt.store, (current_user(request) or {}).get("name") or "?", "fully_apk_upload",
                 name=name, version=man.get("version_name") or "?")
        log.info("Fully-APK hochgeladen: %s (%s, %d Bytes, %s)", name, man.get("version_name") or "Version unbekannt",
                 n, checksum[:10])
        return {"apk": info, "node": Config.node, **await db(fa.overview)}
    finally:
        tmp.unlink(missing_ok=True)


@admin_router.post("/update-app")
async def fully_update_app(d: FullyDeviceIn, request: Request):
    """Fully auf diesem Handy mit der APK vom Pi aktualisieren (loadApkFile; als Geräteinhaber ohne Rückfrage). Die
    Adresse ist ein Pi mit der massgeblichen APK; ob die neue Version ankommt, prüft fully_apk.push_pending (T6)."""
    from . import fully_apk as fa
    url = await db(fa.apk_url, _pi_host(request))
    cfg, target = await _ready(d.ip)
    res = await install_apk(d.ip, cfg["port"], target["pin"], cfg["password"], url, wait=False)   # T30: Sperren
    log.info("Fully aktualisieren auf %s (%s): %s%s", target["name"], d.ip, res["state"],
             f" (Sperren kurz aus: {', '.join(res['locks'])})" if res.get("locks") else "")
    phone = next((x for x in await db(_devices_raw) if x.get("kind") == "phone" and x.get("ip") == d.ip), {})
    if res["state"] in ("ok", "sent") and phone.get("id"):
        await db(fa.note_sent, phone["id"])
    return res


class BootstrapIn(BaseModel):
    ip: str = Field(pattern=monitor_mod.HOST_RE.pattern)
    name: str = Field(min_length=1, max_length=30, pattern=r"^[\w .\-äöüÄÖÜ]+$")
    plain_ok: bool = False                     # Admin hat bestätigt: das ECHTE Passwort geht einmal unverschlüsselt hinaus
    token: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{20,64}$")   # «Fully auf vorhandenem Handy» (prepare)


def _ensure_phone(ip: str, name: str) -> None:
    """Handy in «Handys auf der Statusanzeige» eintragen, falls es dort noch fehlt (Name eindeutig, T1)."""
    items = _devices_raw()
    if not any(d.get("kind") == "phone" and d.get("ip") == ip for d in items):
        devices.check_name(name)
        new = {"id": device_ids.new_phone_id(), "name": name, "kind": "phone", "ip": ip, "port": None, "active": True}
        role = max((e for e in _rollouts().values() if e.get("ip") == ip), key=lambda e: e.get("at", 0), default={}).get("role")
        if role in lb.DEVICE_ROLES and role != "service":   # Rolle aus «Gerät hinzufügen» (auch ohne QR-Code)
            new["role"] = role
        items.insert(0, new)
        _put_setting("monitor_devices", json.dumps(items, ensure_ascii=False))


@admin_router.post("/bootstrap")
async def fully_bootstrap(d: BootstrapIn, request: Request):
    """Frisch eingerichtetes Handy: Zertifikat für die Fernverwaltung von Fully einspielen, damit danach alles über
    HTTPS geht. Nur wenn das Handy noch KEIN HTTPS spricht (sonst wäre es nicht frisch – oder jemand anderes).
    Mit QR-Code ausgerollt: Fully hat ein Einmal-Passwort (rollout_pw); damit laufen die unverschlüsselten Befehle,
    danach setzt FestPOS über HTTPS das echte Passwort – das echte geht nie unverschlüsselt hinaus. Ohne QR-Code
    (Handy schon im WLAN, echtes Passwort) nur mit Bestätigung (plain_ok).
    -> {"state": ok|already|plain_needed|pw_not_switched|cert|auth|unreachable|not_fetched|no_https|…}"""
    cfg = await db(config)
    if not cfg["password"]:
        raise HTTPException(422, i18n.t("err.fully.no_password"))
    real = cfg["password"]
    if d.token:                                    # Einmal-Passwort von «prepare»: gehört ab jetzt zu dieser IP
        await db(_bind_prepared, d.token, d.ip)
    temp = await db(rollout_pw, d.ip)
    await db(_ensure_phone, d.ip, d.name.strip())
    cfg = await db(config)
    target = next((x for x in cfg["targets"] if x["ip"] == d.ip), None)
    name = str(next((x.get("name") for x in await db(_devices_raw) if x.get("ip") == d.ip), d.name))
    if target:                                     # schon über HTTPS erreichbar: fertig – oder noch umstellen
        res = await query(d.ip, cfg["port"], target["pin"], real)
        if res["state"] == "ok":
            rt.monitor.fully_store(target["name"], d.ip, res)
            return {"state": "already"}
        if temp and (await query(d.ip, cfg["port"], target["pin"], temp))["state"] == "ok":
            return await _switch_password(d.ip, cfg["port"], target["pin"], temp, real, name)
    probe = await query(d.ip, cfg["port"], "0" * 64, real)      # spricht das Handy schon HTTPS?
    if probe["state"] != "no_https":
        return {"state": probe["state"] if probe["state"] != "cert" else "cert"}
    if not temp and not d.plain_ok:
        return {"state": "plain_needed"}
    first = temp or real
    plain = (d.ip, cfg["port"], PLAIN, first)
    state, raw = await _exchange(*plain, {"cmd": "deviceInfo"}, TIMEOUT)
    info = interpret(raw or b"") if state == "answer" else {"state": state}
    if info["state"] != "ok":
        return {"state": info["state"]}
    p12, pin, until = await asyncio.to_thread(make_cert, name, d.ip)
    if temp:
        log.info("Ausrollen %s (%s): unverschlüsselt nur mit dem Einmal-Passwort", name, d.ip)
    else:
        log.warning("Ausrollen %s (%s): Fully-Passwort einmal unverschlüsselt gesendet (bestätigt)", name, d.ip)
    res = await deliver(plain, _pi_host(request), {P12_NAME: p12}, folder="")    # Fully sucht sie ganz oben
    if res["state"] != "ok":
        return res
    await db(_set_pin, d.ip, pin, until)
    rt.monitor.fully_forget(d.ip)

    # Fully lädt das Zertifikat beim Start – ein Neustart der App reicht nicht immer (am XCover nicht): dann das Handy
    async def https_up(wait: float) -> bool:
        end = time.monotonic() + wait
        while time.monotonic() < end:
            await asyncio.sleep(3)
            res = await query(d.ip, cfg["port"], pin, first)
            if res["state"] == "ok":
                rt.monitor.fully_store(name, d.ip, res)
                return True
        return False
    rebooted = False
    await setup_command(*plain, "restartApp")
    if not await https_up(RESTART_WAIT):
        log.info("Ausrollen %s: Fully lädt das Zertifikat nicht nach einem Neustart der App – Handy neu starten", name)
        await setup_command(*plain, "rebootDevice")
        rebooted = True
        if not await https_up(BOOT_WAIT):
            return {"state": "no_https"}
    out = await _switch_password(d.ip, cfg["port"], pin, temp, real, name) if temp else {"state": "ok"}
    return {**out, "rebooted": True} if rebooted else out


async def _switch_password(ip: str, port: int, pin: str, temp: str, real: str, name: str) -> dict:
    """Über HTTPS vom Einmal-Passwort auf das echte umstellen und prüfen, dass es gilt."""
    # nur hier, nur über HTTPS mit dem gepinnten Zertifikat (darum nicht in SETTINGS / setup_command)
    state, raw = await _exchange(ip, port, pin, temp, {"cmd": "setStringSetting", "key": "remoteAdminPassword",
                                                       "value": real}, COMMAND_TIMEOUT)
    res = _command_result(state, raw)
    if res["state"] in ("ok", "sent"):
        for _ in range(5):                         # Fully übernimmt es evtl. erst nach einem Moment
            info = await query(ip, port, pin, real)
            if info["state"] == "ok":
                rt.monitor.fully_store(name, ip, info)
                await db(rollout_pw_done, ip)
                log.info("Ausrollen %s (%s): Fully-Passwort über HTTPS umgestellt", name, ip)
                return {"state": "ok"}
            await asyncio.sleep(2)
    log.warning("Ausrollen %s (%s): Fully hat das Passwort nicht übernommen – es gilt noch das Einmal-Passwort", name, ip)
    return {"state": "pw_not_switched"}


# -- Ein QR-Code pro Handy: alles von selbst ---------------------------------------------------------------------
# Der Einrichtungs-QR enthält die Adresse einer Einstellungsdatei für genau dieses Handy (30 Minuten gültig). Holt
# Fully sie beim Einrichten ab, kennt der Pi die MAC des Handys (ARP) und trägt die feste Adresse ein (DHCP, dhcp.py);
# nach spätestens etwa 2 Minuten hat das Handy sie. Die Verwaltung rollt dann wie oben aus (bootstrap, CA, …).
# In der Datei steht ein Einmal-Passwort für die Fernverwaltung (nicht das echte); sie gibt es nur mit dem Code aus dem
# QR und nur in dieser Zeit.
ROLLOUT_TTL = 30 * 60                         # so lange gilt der QR-Code (Einstellungsdatei)
ROLLOUT_KEEP = 24 * 3600                      # so lange merkt sich FestPOS das Einmal-Passwort (Umstellen nachholen)
ROLLOUT_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")


def _rollouts() -> dict:
    try:
        r = json.loads(rt.store.setting("fully_rollouts") or "{}")
    except ValueError:
        return {}
    now = time.time()
    return {k: v for k, v in r.items() if isinstance(v, dict) and now - v.get("at", 0) < ROLLOUT_KEEP} \
        if isinstance(r, dict) else {}


def _fresh(entry: dict | None) -> bool:
    """QR-Code (Einstellungsdatei) noch gültig?"""
    return bool(entry) and time.time() - entry.get("at", 0) < ROLLOUT_TTL


def rollout_pw(ip: str) -> str | None:
    """Einmal-Passwort des neusten Ausrollens an diese IP, solange es noch nicht durch das echte ersetzt ist."""
    hits = [v for v in _rollouts().values() if v.get("ip") == ip and v.get("temp_pw") and not v.get("pw_done")]
    return max(hits, key=lambda v: v.get("at", 0))["temp_pw"] if hits else None


def _pin_sha(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()


def rollout_pin_given(ip: str, pin: str) -> bool:
    """Hat dieses Handy beim Ausrollen genau diesen Kiosk-PIN schon mit der Einstellungsdatei bekommen?"""
    sha = _pin_sha(pin)
    return any(v.get("ip") == ip and v.get("fetched_at") and v.get("pin_sha") == sha for v in _rollouts().values())


# Lesen – ändern – schreiben von fully_rollouts (und der Geräteliste dazu) immer unter dieser Sperre: sonst schriebe
# ein Abruf der Einstellungsdatei, der kurz vor dem Umstellen begann, das Einmal-Passwort wieder hinein
_rollout_lock = threading.Lock()


def rollout_pw_done(ip: str) -> None:
    with _rollout_lock:
        r = _rollouts()
        for v in r.values():
            if v.get("ip") == ip and v.get("temp_pw"):
                v["pw_done"] = True
                v.pop("temp_pw", None)             # nicht länger aufbewahren als nötig
        _save_rollouts(r)


def _save_rollouts(r: dict) -> None:
    _put_setting("fully_rollouts", json.dumps(r, ensure_ascii=False))


class PrepareIn(BaseModel):
    name: str = Field(min_length=1, max_length=30, pattern=r"^[\w .\-äöüÄÖÜ]+$")


@admin_router.post("/prepare")
async def fully_prepare(d: PrepareIn, request: Request):
    """«Fully auf vorhandenem Handy» (T5): Einmal-Passwort, das man in Fully als Remote-Admin-Passwort einträgt. Damit
    laufen die ersten Befehle unverschlüsselt (wie beim QR-Code), danach setzt FestPOS über HTTPS das echte – so muss
    niemand das echte Fully-Passwort kennen. Die IP kommt mit bootstrap (token) dazu. -> {id, temp_pw}"""
    cfg = await db(config)
    if not cfg["password"]:
        raise HTTPException(422, i18n.t("err.fully.no_password"))
    await db(devices.check_name, d.name)
    token, temp = secrets.token_urlsafe(24), secrets.token_hex(4)    # kurz: wird von Hand eingetippt

    def save():
        with _rollout_lock:
            r = _rollouts()
            r[token] = {"name": d.name.strip(), "ip": "", "cert": d.name.strip(), "at": time.time(), "manual": True,
                        "host": _pi_host(request), "temp_pw": temp}
            _save_rollouts(r)
    await db(save)
    return {"id": token, "temp_pw": temp}


def _bind_prepared(token: str, ip: str) -> None:
    with _rollout_lock:
        r = _rollouts()
        e = r.get(token)
        if e and e.get("manual") and _fresh(e) and not e.get("pw_done"):
            e["ip"] = ip
            _save_rollouts(r)


def _unlink_blocked(ip: str) -> dict:
    """Neues Handy auf die IP eines gesperrten/abgelaufenen Geräts (Ersatz nach Verlust): der Eintrag dort gehört nicht
    mehr zu dessen Zertifikat – sonst käme das neue in die alte Kette und erbte die Rolle (T1). -> Eintrag oder {}"""
    items = _devices_raw()
    hit = next((x for x in items if x.get("kind") == "phone" and x.get("ip") == ip), None)
    if not hit:
        return {}
    lin = device_ids.lineages(rt.store).get(hit.get("cert") or "")
    if lin and not lin["valid"]:
        hit.pop("cert")
        hit.pop("role", None)
        rt.store.execute(*device_ids.save_phones(items))
    return hit


class RolloutIn(BaseModel):
    name: str = Field(min_length=1, max_length=30, pattern=r"^[\w .\-äöüÄÖÜ]+$")
    ip: str = Field(pattern=r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
    cert: str = Field(default="", max_length=40, pattern=r"^[\w .\-äöüÄÖÜ]*$")
    role: str | None = Field(default=None, pattern="^(" + "|".join(lb.DEVICE_ROLES) + ")$")


@admin_router.post("/rollout")
async def fully_rollout(d: RolloutIn, request: Request):
    """Einrichtungs-QR für genau dieses Handy (Name, feste IP) -> {id, svg}."""
    from . import qr
    try:
        ipaddress.IPv4Address(d.ip)
    except ValueError:
        raise HTTPException(422, i18n.t("err.fully.bad_ip")) from None
    cfg = await db(config)
    if not cfg["password"]:
        raise HTTPException(422, i18n.t("err.fully.no_password"))
    own = await db(_unlink_blocked, d.ip)
    for n in {d.name.strip(), d.cert.strip() or d.name.strip()}:     # das Handy mit dieser IP ist dasselbe Gerät
        await db(devices.check_name, n, own.get("cert"), own.get("id"))
    host = _pi_host(request)
    token = secrets.token_urlsafe(24)
    data = await db(provision_json, host, f"http://{host}/fully/{token}.json")

    def save():
        with _rollout_lock:
            r = _rollouts()
            r[token] = {"name": d.name.strip(), "ip": d.ip, "cert": d.cert.strip() or d.name.strip(), "at": time.time(),
                        "host": host, "temp_pw": secrets.token_urlsafe(18), "role": d.role}
            _save_rollouts(r)
    await db(save)
    await db(lb.audit, rt.store, (current_user(request) or {}).get("name") or "?", "device_rollout", name=d.name.strip(), ip=d.ip)
    log.info("Ausrollen vorbereitet: %s (%s), QR gültig %d Minuten", d.name, d.ip, ROLLOUT_TTL // 60)
    return {"id": token, "svg": qr.qr_svg(data)}


@admin_router.get("/rollout/{token}")
async def fully_rollout_state(token: str):
    """Stand: «wait» (QR noch nicht gescannt), «fetched» (Einstellungen abgeholt, mit Adresse und MAC), «gone»."""
    r = (await db(_rollouts)).get(token)
    if not r or (not r.get("fetched_at") and not _fresh(r)):
        return {"state": "gone"}
    return {"state": "fetched" if r.get("fetched_at") else "wait", "seen_ip": r.get("seen_ip"), "mac": r.get("mac"),
            "name": r["name"], "ip": r["ip"], "cert": r["cert"]}


def rollout_settings(token: str, client_ip: str) -> dict | None:
    """Einstellungsdatei für Fully beim Einrichten (Vorlage + was dieses Handy braucht); MAC merken, feste Adresse
    eintragen. None: Code unbekannt oder abgelaufen."""
    with _rollout_lock:
        return _rollout_settings(token, client_ip)


def _rollout_settings(token: str, client_ip: str) -> dict | None:
    r = _rollouts()
    entry = r.get(token)
    if not _fresh(entry):
        return None
    # Review 2026-11 (N1): die Datei gibt es nur, solange das Einmal-Passwort gilt. Danach (FestPOS hat über HTTPS auf
    # das echte umgestellt) und für «Fully auf vorhandenem Handy» (ohne Einstellungsdatei) nichts mehr – sonst bekäme,
    # wer den Code im WLAN mitgelesen hat, das echte Fully-Passwort und könnte dem Handy Zertifikat und Adresse nehmen.
    if not entry.get("temp_pw") or entry.get("pw_done") or entry.get("manual"):
        return None
    m = dhcp_mod.mac_for_ip(client_ip)
    entry.update(fetched_at=time.time(), seen_ip=client_ip, mac=m)
    if kiosk_pin():                                    # Vorlage-Schritt: nicht nochmals setzen (rollout_pin_given)
        entry["pin_sha"] = _pin_sha(kiosk_pin())
    _save_rollouts(r)
    items = _devices_raw()
    hit = next((x for x in items if x.get("kind") == "phone" and x.get("ip") == entry["ip"]), None)
    if not hit:
        hit = {"id": device_ids.new_phone_id(), "name": entry["name"], "kind": "phone", "ip": entry["ip"], "port": None,
               "active": True}
        items.insert(0, hit)
    hit["name"] = entry["name"]
    if entry.get("role") in lb.DEVICE_ROLES and entry["role"] != "service" and not hit.get("cert"):
        hit["role"] = entry["role"]                    # wartet beim Handy, bis das Zertifikat dazukommt (T1)
    hit.pop("fully_pin", None)                         # neu eingerichtet: das alte Fully-Zertifikat gilt nicht mehr
    hit.pop("fully_until", None)
    if m:
        for x in items:                                # eine MAC gehört genau einem Handy
            if x is not hit and x.get("mac") == m:
                x.pop("mac", None)
        hit["mac"] = m
    _put_setting("monitor_devices", json.dumps(items, ensure_ascii=False))
    dhcp_mod.sync()                                    # hier sofort, die anderen Pi beim nächsten Durchlauf
    settings = dict(load_template().get("settings") or {})
    settings.setdefault("startURL", f"https://{entry.get('host') or '192.168.55.10'}")
    settings.update(TEMPLATE_FORCE)
    settings[WHITELIST_KEY] = whitelist_value(settings["startURL"])   # von Anfang an nur die Bestell-App (web_check)
    # Einmal-Passwort: das echte setzt FestPOS erst über HTTPS (fully_bootstrap) – es geht nie unverschlüsselt hinaus
    settings.update({"remoteAdminPassword": entry["temp_pw"], "deviceName": entry["name"],
                     "forceManageExternalStoragePermission": True})
    key = rt.store.setting("fully_volume_key", "") or ""
    if key:
        settings["volumeLicenseKey"] = key
    if kiosk_pin():
        settings["kioskPin"] = kiosk_pin()
    log.info("Ausrollen %s: Einstellungen abgeholt von %s (MAC %s)", entry["name"], client_ip, m or "unbekannt")
    return settings


@files_router.get("/api/device-cert/fully/settings/{token}")
async def fully_rollout_file(token: str, request: Request):
    """Einstellungsdatei für Fully – nur für den Pi selbst: festpos-redirect.py reicht sie auf Port 80 weiter und
    nennt die Adresse des Handys (X-Festpos-Client)."""
    if (request.client.host if request.client else None) not in LOCAL_HOSTS or not ROLLOUT_RE.match(token):
        raise HTTPException(403)
    client = request.headers.get("x-festpos-client", "")
    try:
        ipaddress.IPv4Address(client)
    except ValueError:
        raise HTTPException(400) from None
    settings = await db(rollout_settings, token, client)
    if settings is None:
        raise HTTPException(404)
    return Response(json.dumps(settings, ensure_ascii=False), media_type="application/json",
                    headers={"Cache-Control": "no-store"})


# -- Internet beim Ausrollen über diesen Pi ---------------------------------------------------------------------
# Android will beim Einrichten als Arbeitsgerät ins Internet (Google, Knox), Fully für die Lizenz. Ohne Router im Netz
# gibt dieser Pi seine Verbindung weiter – dieselbe wie «Update aus dem Internet» (WLAN des Pi, USB-Adapter oder
# Router), über den root-Helfer festpos-update (Weiterleitung + NAT, Zeitgeber 30 Minuten). Die Handys schicken
# alles an ihr DHCP-Gateway: das muss dieser Pi sein (normalerweise die VIP .10) – oder, dauerhaft über den Router
# verbunden (Schalter c), der Router selbst (Gateway automatisch, app/dhcp.py).
_share: dict = {}


def dhcp_gateway() -> str | None:
    """Gateway, das die Pi per DHCP vergeben (fest aus /etc/festpos/dnsmasq.conf oder automatisch, app/dhcp.py)."""
    return dhcp_mod.current_gateway()


def is_local(ip: str) -> bool:
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind((ip, 0))
        return True
    except OSError:
        return False


def share_view() -> dict:
    from . import update_net
    c = update_net._public()
    ready = (c["via"] == "wlan" and bool(c["ssid"])) or c["via"] == "usb" or (c["via"] == "lan" and bool(c["gateway"]))
    return {"on": bool(_share.get("on")), "since": _share.get("since"), "via": c["via"], "ssid": c["ssid"],
            "ready": ready, "gateway": dhcp_gateway(), "allowed": share_allowed()}


def share_allowed() -> bool:
    """Seite «Internet», Schalter (b): der Pi darf neuen Handys beim Einrichten Internet weitergeben (Standard ja)."""
    return rt.store.setting("net_phones", "1") != "0"


class ShareIn(BaseModel):
    on: bool


@admin_router.post("/internet")
async def fully_internet(d: ShareIn):
    """Internet über diesen Pi an die Handys weitergeben (ein/aus)."""
    from . import release, update, update_net
    if not d.on:
        await asyncio.to_thread(update_net._net_down)
        _share.clear()
        log.info("Internet für die Handys aus")
        return {"state": "off", **share_view()}
    if not await db(share_allowed):
        raise HTTPException(409, i18n.t("err.fully.share_not_allowed"))
    gw = await asyncio.to_thread(dhcp_gateway)
    if not gw:
        raise HTTPException(409, i18n.t("err.fully.share_no_dhcp"))
    if not is_local(gw):
        if gw == (await db(update_net._raw)).get("gateway") and update_net._keep.get("state") == "up":
            _share.update(on=True, since=int(time.time()), dev="")      # dauerhaft über den Router: schon im Internet
            return {"state": "on", **share_view()}
        raise HTTPException(409, i18n.t("err.fully.share_not_gateway", gateway=gw))
    if update_net._task and not update_net._task.done():
        raise HTTPException(409, i18n.t("err.update.net_busy"))
    cfg, psk = await db(update_net._raw), await db(rt.store.setting, "update_net_psk")
    if not (await db(share_view))["ready"]:
        raise HTTPException(409, i18n.t("err.fully.share_no_net"))
    try:
        dev = await asyncio.to_thread(update_net._net_up, cfg, psk or "", True)
    except release.ReleaseError as e:
        return {"state": "failed", "text": update.err_text(e.code, e.kw), **share_view()}
    _share.update(on=True, since=int(time.time()), dev=dev)
    log.info("Internet für die Handys ein (%s %s)", cfg.get("via"), dev)
    return {"state": "on", **share_view()}



# -- Fully-Lizenz (letzter Schritt beim Ausrollen) ---------------------------------------------------------------
# Volumenlizenz: ein Schlüssel für alle Handys, FestPOS setzt ihn (volumeLicenseKey). Einzellizenz: hängt an der
# Geräte-ID von Fully (nach dem Zurücksetzen eine neue) – im Lizenzportal von Fully zuweisen oder verschieben; FestPOS
# zeigt die Geräte-ID und prüft, bis Fully «lizenziert» meldet. Fully prüft die Lizenz online: Internet nötig.
def adb_setting(settings: dict) -> tuple[str, bool] | None:
    """Fully-Einstellung, die ADB (USB-Debugging) schaltet, und der Wert für «ein» (T2: Zeitserver per USB beim
    Werksreset). Welche Fully-Version wie heisst, steht nicht fest: genommen wird eine Ja/Nein-Einstellung mit «adb»
    im Namen; heisst sie «disable…», bedeutet «false» ein. Keine gefunden: None (dann von Hand, Entwickleroptionen)."""
    for k, v in sorted(settings.items()):
        if "adb" in k.lower() and template_key(k) and (isinstance(v, bool) or str(v).lower() in ("true", "false")):
            return k, not k.lower().startswith("disable")
    return None


class AdbIn(BaseModel):
    ip: str = Field(pattern=monitor_mod.HOST_RE.pattern)
    on: bool


@admin_router.post("/adb")
async def fully_adb(d: AdbIn):
    """ADB über Fully ein- bzw. wieder ausschalten (Geräteinhaber). -> {state: ok|none|…, key}"""
    cfg, target = await _ready(d.ip)
    args = (d.ip, cfg["port"], target["pin"], cfg["password"])
    state, current = await read_settings(args)
    if state != "ok":
        return {"state": state}
    found = adb_setting(current)
    if not found:
        log.info("Fully auf %s: keine Einstellung für ADB gefunden", target["name"])
        return {"state": "none"}
    key, on_value = found
    res = await setup_command(*args, "setBooleanSetting", key=key, value="true" if d.on == on_value else "false")
    log.info("Fully auf %s: ADB %s über %s -> %s", target["name"], "ein" if d.on else "aus", key, res["state"])
    return {"state": "ok" if res["state"] in ("ok", "sent") else res["state"], "key": key, "text": res.get("text")}


class LicenseIn(BaseModel):
    ip: str = Field(pattern=monitor_mod.HOST_RE.pattern)
    volume: bool = False                       # Volumenlizenz aus der Verwaltung setzen


@admin_router.post("/license")
async def fully_license(d: LicenseIn):
    """Lizenz prüfen (oder zuerst die Volumenlizenz setzen) -> {licensed, device_id, volume_key_set}."""
    cfg, target = await _ready(d.ip)
    key = await db(rt.store.setting, "fully_volume_key", "")
    if d.volume:
        if not key:
            raise HTTPException(422, i18n.t("err.fully.no_volume_key"))
        res = await setup_command(d.ip, cfg["port"], target["pin"], cfg["password"], "setStringSetting",
                                  key="volumeLicenseKey", value=key)
        if res["state"] not in ("ok", "sent"):
            return {"state": res["state"], "text": res.get("text")}
        log.info("Volumenlizenz an %s (%s) gesendet", target["name"], d.ip)
    info = await query(d.ip, cfg["port"], target["pin"], cfg["password"])
    rt.monitor.fully_store(target["name"], d.ip, info)
    data = info.get("data") or {}
    return {"state": info["state"], "licensed": bool(data.get("licensed")), "device_id": data.get("device_id"),
            "volume_key_set": bool(key)}
