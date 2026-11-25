"""Gemeinsame Grundlage aller Module: Konfiguration, Laufzeit-Objekte, Admin-Anmeldung, Sperre gegen Raten."""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path

from fastapi import HTTPException, Request

from . import auth, emergency, i18n, monitor as monitor_mod, secretbox, timesync
from .i18n import t
from .version import APP_VERSION  # noqa: F401 – von hier importiert

log = logging.getLogger("festpos")
logging.basicConfig(level=os.environ.get("FESTPOS_LOGLEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class Config:
    db = os.environ.get("FESTPOS_DB", "sqlite:///./festpos.db")
    node = os.environ.get("FESTPOS_NODE", "local")
    admin_password = os.environ.get("FESTPOS_ADMIN_PASSWORD", "admin")   # nur noch ältere Konfiguration (Klartext)
    # Notfall-Passwort als Prüfwert (T-0-N/FestPOS#15): ADMIN_PASSWORD_HASH/-LAENGE aus festpos.conf (app/emergency.py)
    admin_password_hash = os.environ.get("FESTPOS_ADMIN_PASSWORD_HASH", "")
    admin_password_len = int(os.environ.get("FESTPOS_ADMIN_PASSWORD_LEN", "0") or 0)
    # nur für die Pi untereinander (Zeit stellen); KEIN Rückfall aufs Admin-Passwort (REV-013)
    cluster_secret = ""                 # siehe _cluster_secret() unten
    cluster_secret_derived = False      # True: aus dem Server-Schlüssel abgeleitet (Platzhalter in festpos.conf)
    node_ip = os.environ.get("NODE_IP", "")
    peers = [p for p in os.environ.get("FESTPOS_PEERS", "").split(",") if p.strip()]
    cafile = os.environ.get("FESTPOS_CA") or None
    static = Path(os.environ.get("FESTPOS_STATIC", Path(__file__).parent.parent / "static"))
    # Gerätezertifikate: Port, auf dem nur Handys mit Zertifikat ankommen (0 = keine Einschränkung, z.B. Entwicklung)
    phone_port = int(os.environ.get("FESTPOS_PHONE_PORT", "0") or 0)
    admin_port = int(os.environ.get("FESTPOS_ADMIN_PORT", "0") or 0)
    device_ca = os.environ.get("FESTPOS_DEVICE_CA", "")
    device_ca_key = os.environ.get("FESTPOS_DEVICE_CA_KEY", "")
    server_cert = os.environ.get("FESTPOS_TLS_CERT", "")
    phone_certs_problem = os.environ.get("FESTPOS_PHONE_CERTS_PROBLEM", "")   # von festpos-node gesetzt
    rq_data = os.environ.get("FESTPOS_RQ_DATA", "/var/lib/festpos/rqlite")     # Datenverzeichnis von rqlited
    admin_from = os.environ.get("FESTPOS_ADMIN_FROM", "")                     # Verwaltung nur von hier (leer = alle)


def ip_ranges(text: str) -> list[tuple[int, int]]:
    """«192.168.55.200-192.168.55.254, 10.0.0.0/24, 1.2.3.4» (wie SSH_FROM) -> [(von, bis)] als Zahlen.
    Ungültige Teile werden übergangen (festpos-node prüft die Schreibweise schon)."""
    import ipaddress
    out = []
    for part in text.replace(",", " ").split():
        try:
            if "-" in part:
                a, b = (int(ipaddress.IPv4Address(x)) for x in part.split("-", 1))
            else:
                net = ipaddress.IPv4Network(part, strict=False)
                a, b = int(net.network_address), int(net.broadcast_address)
        except ValueError:
            continue
        out.append((min(a, b), max(a, b)))
    return out


def admin_allowed_from(ip: str | None) -> bool:
    """Darf diese Adresse die Verwaltung auf dem Verwaltungs-Port benutzen (ADMIN_FROM)? Der Pi selbst und die
    anderen Pi immer."""
    ranges = ip_ranges(Config.admin_from)
    if not ranges:
        return True
    if not ip:
        return False
    if ip in LOCAL_HOSTS or ip == Config.node_ip:
        return True
    from urllib.parse import urlsplit
    if ip in {urlsplit(p).hostname for p in Config.peers}:
        return True
    import ipaddress
    try:
        n = int(ipaddress.IPv4Address(ip))
    except ValueError:
        return False
    return any(a <= n <= b for a, b in ranges)


def _cluster_secret() -> tuple[str, bool]:
    """(CLUSTER_SECRET aus festpos.conf, False) – oder, wenn dort nur der Platzhalter steht, (abgeleitet aus dem
    Server-Schlüssel, True): liegt auf allen Pi gleich und ist geheim. So stellen sich die Pi gegenseitig die Uhr,
    ohne dass jemand ein Geheimnis eintragen muss. Abgeleitet hängen aber die Prüfsummen am Zertifikat – darum
    schreiben make-certs.sh, festpos-zertifikate und festpos-zuruecksetzen es vor einem neuen Schlüssel fest
    (deploy/festpos-conf.sh: festpos_secret_fix, gleiche Rechnung wie hier)."""
    s = os.environ.get("FESTPOS_CLUSTER_SECRET", "")
    if timesync.secret_usable(s):
        return s, False
    try:
        key = Path(os.environ.get("FESTPOS_TLS_KEY", "")).read_bytes()
    except OSError:
        return s, False
    from .backup import derived_secret                       # gleiche Rechnung wie festpos_secret_fix (Shell)
    return derived_secret(key), True


Config.cluster_secret, Config.cluster_secret_derived = _cluster_secret()
secretbox.use(lambda: Config.cluster_secret)            # Hauptschlüssel der verschlüsselten Geheimnisse (#8)


class Runtime:
    """Was beim Start entsteht (Datenbank, Druckdienst, Überwachung) – für alle Module an einer Stelle."""

    def __init__(self) -> None:
        self.store = None                      # app.store.Store, gesetzt im lifespan (app.main)
        self.spooler = None                    # app.print_service.PrintSpooler
        self.monitor = monitor_mod.Monitor()
        self.ready = False                     # Datenbank bereit, Druckdienst und Überwachung laufen


rt = Runtime()
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def cluster_info() -> dict:
    """Mitglieder und Erreichbarkeit der Pi für Statusanzeige und Verwaltung: der Zwischenstand des Monitors
    (alle paar Sekunden im Hintergrund erneuert). Ein ausgesteckter Pi bremst so keine Anzeige aus. Nur wenn es
    noch keinen frischen Stand gibt (gleich nach dem Start), direkt fragen – mit kurzer Zeitgrenze."""
    cached = rt.monitor.cluster_cached()
    return cached if cached is not None else rt.store.cluster_info()


def db(fn, *args, **kw):
    return asyncio.to_thread(fn, *args, **kw)


class Throttle:
    """Schutz gegen Passwort-Raten: nach MAX falschen Versuchen in WINDOW Sekunden sperrt diese Adresse.
    Jeder Fehlversuch merkt sich den Namen, mit dem er kam. Eine erfolgreiche Anmeldung löscht nur die Fehlversuche
    mit DEMSELBEN Namen (eigene Tippfehler) – sonst könnte, wer ein eigenes Konto hat, zwischen Rateversuchen für
    «admin» die Zählung immer wieder auf null setzen."""

    def __init__(self, max_fails: int = 10, window_s: float = 300, name_max: int = 0, trust_s: float = 12 * 3600,
                 on_lock=None):
        self.max_fails, self.window_s = max_fails, window_s
        self.on_lock = on_lock                      # on_lock(Adresse, Name, "ip"|"name"): gerade gesperrt (#11)
        self._fails: dict[str, list[tuple[float, str]]] = {}   # Adresse -> [(Zeitpunkt, Name)]
        self._lock = threading.Lock()               # admin() läuft in mehreren Threads gleichzeitig
        # Zusätzlich pro Name über ALLE Adressen (Review 2026-11, S6): wer im WLAN nach jedem zehnten Versuch die
        # Adresse wechselt, umginge sonst die Sperre. Nach name_max Fehlversuchen in window_s kommt für diesen Namen
        # nur noch durch, wer sich von seiner Adresse aus kürzlich (trust_s) richtig angemeldet hat – so sperrt ein
        # Angreifer die Festleitung an ihrem PC nicht aus. 0 = aus.
        self.name_max, self.trust_s = name_max, trust_s
        self._name_fails: dict[str, list[float]] = {}          # Name -> [Zeitpunkt]
        self._trusted: dict[str, dict[str, float]] = {}        # Name -> {Adresse: letzte richtige Anmeldung}

    def check(self, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            fails = [f for f in self._fails.get(ip, ()) if now - f[0] < self.window_s]
            if fails:
                self._fails[ip] = fails
            else:
                self._fails.pop(ip, None)
        if len(fails) >= self.max_fails:
            wait = int(self.window_s - (now - fails[0][0])) + 1
            raise HTTPException(429, t("err.login.throttled", minutes=max(1, wait // 60)),
                                headers={"Retry-After": str(wait)})

    def check_name(self, ip: str, name: str) -> None:
        """429, wenn für diesen Namen (von allen Adressen zusammen) zu viele Fehlversuche laufen und diese Adresse
        sich damit nicht kürzlich richtig angemeldet hat."""
        if not self.name_max or not name:
            return
        key, now = name.casefold(), time.monotonic()
        with self._lock:
            fails = [f for f in self._name_fails.get(key, ()) if now - f < self.window_s]
            if fails:
                self._name_fails[key] = fails
            else:
                self._name_fails.pop(key, None)
            trusted = now - self._trusted.get(key, {}).get(ip, -1e12) < self.trust_s
        if len(fails) >= self.name_max and not trusted:
            wait = int(self.window_s - (now - fails[0])) + 1
            raise HTTPException(429, t("err.login.throttled", minutes=max(1, wait // 60)),
                                headers={"Retry-After": str(wait)})

    def fail(self, ip: str, name: str = "") -> None:
        locked = None
        with self._lock:
            now = time.monotonic()
            fails = [f for f in self._fails.get(ip, ()) if now - f[0] < self.window_s] + [(now, name.casefold())]
            self._fails[ip] = fails
            if len(fails) == self.max_fails:        # genau jetzt gesperrt (nicht bei jedem weiteren Versuch)
                locked = "ip"
            if len(self._fails) > 1000:             # Speicher begrenzen
                self._fails.pop(next(iter(self._fails)))
            if self.name_max and name:
                nf = [f for f in self._name_fails.get(name.casefold(), ()) if now - f < self.window_s] + [now]
                self._name_fails[name.casefold()] = nf
                if len(nf) == self.name_max:
                    locked = locked or "name"
                if len(self._name_fails) > 1000:
                    self._name_fails.pop(next(iter(self._name_fails)))
        if locked and self.on_lock:
            try:
                self.on_lock(ip, name, locked)
            except Exception:  # noqa: BLE001 – Meldung darf die Anmeldung nicht stören
                log.exception("Anmeldesperre melden")

    def ok(self, ip: str, name: str = "") -> None:
        with self._lock:
            keep = [f for f in self._fails.get(ip, ()) if f[1] != name.casefold()]
            if keep:
                self._fails[ip] = keep
            else:
                self._fails.pop(ip, None)
            if self.name_max and name:
                seen = self._trusted.setdefault(name.casefold(), {})
                seen[ip] = time.monotonic()
                if len(seen) > 50:                  # die ältesten Adressen vergessen
                    seen.pop(min(seen, key=seen.get))
                if len(self._trusted) > 1000:
                    self._trusted.pop(next(iter(self._trusted)))

    def clear(self) -> None:
        with self._lock:
            self._fails.clear()
            self._name_fails.clear()
            self._trusted.clear()


def require_cluster_secret(request: Request, throttle: Throttle) -> None:
    """Nur die anderen Pi (Header X-Festpos-Secret = CLUSTER_SECRET), gegen Raten gesperrt. Sonst 403."""
    ip = request.client.host if request.client else "?"
    throttle.check(ip)
    given = request.headers.get("X-Festpos-Secret", "").encode("latin-1")   # rohe Bytes (UTF-8 vom Absender)
    if not timesync.secret_usable(Config.cluster_secret) or \
            not secrets.compare_digest(given, Config.cluster_secret.encode()):
        throttle.fail(ip)
        raise HTTPException(403)
    throttle.ok(ip)


# Schutz gegen Passwort-Raten: nach 10 falschen Passwörtern in 5 Minuten sperrt diese Adresse 5 Minuten. Dazu pro
# Name: nach 30 Fehlversuchen in 5 Minuten (von allen Adressen zusammen) nur noch von Adressen, die sich mit diesem
# Namen in den letzten 12 Stunden richtig angemeldet haben.
LOGIN_MAX_FAILS, LOGIN_WINDOW_S, LOGIN_NAME_MAX_FAILS = 10, 300, 30
login_throttle = Throttle(LOGIN_MAX_FAILS, LOGIN_WINDOW_S, name_max=LOGIN_NAME_MAX_FAILS)

# ---------------------------------------------------------------------------
# Anmeldung der Verwaltung: Sitzungs-Token statt Passwort im Browser
# ---------------------------------------------------------------------------
# Die Verwaltung schickt Name und Passwort EINMAL (POST /api/admin/login) und bekommt ein Token für 12 Stunden.
# Das Token nennt den Benutzer (Nummer + Stand) und ist mit einem Schlüssel aus CLUSTER_SECRET und dem
# Notfall-Passwort signiert: es gilt auf allen Pi (auch nach einem Wechsel der virtuellen IP). Ungültig wird es,
# wenn das Notfall-Passwort in festpos.conf geändert wird oder der Benutzer gesperrt/gelöscht wird oder ein neues
# Passwort bekommt (Stand «gen» in der Datenbank). Welche Rolle was darf: auth.py.
# Basic Auth (z.B. für curl): admin + Notfall-Passwort oder Name + Passwort eines Benutzers.
#
# Abmelden ohne Bedienung: Das Token trägt zusätzlich eine Frist («~<Zeit>» am Ende des Zufallsteils) – danach gilt es
# nicht mehr. Die Verwaltung holt sich bei Bedienung (Klick, Tippen, Eingabe) ein neues Token mit neuer Frist
# (POST /api/admin/me/refresh); das automatische Aktualisieren der Seiten verlängert nicht. Die Höchstdauer (12 Stunden
# ab Anmeldung) bleibt. Rolle Anzeige (Statusanzeige) ohne Frist. Ältere Pi lesen die Frist nicht mit (Signatur über
# denselben Text) – während eines Updates gilt das Token dort weiter wie bisher.
SESSION_HOURS = 12
IDLE_DEFAULT, IDLE_MIN, IDLE_MAX = 30, 10, 120          # Minuten ohne Bedienung (Einstellung admin_idle_minutes)
IDLE_KEY = "admin_idle_minutes"


_RANDOM_SESSION_KEY = secrets.token_bytes(32)


def _session_key() -> bytes:
    # Ohne brauchbares CLUSTER_SECRET (Entwicklung ohne festpos.conf und ohne TLS-Schlüssel) wäre der Schlüssel aus
    # bekannten Vorgaben berechenbar – dann ein zufälliger pro Start (Review 2026-11, S16): Sitzungen enden beim
    # Neustart und gelten nicht auf anderen Pi; auf einem eingerichteten Pi kommt das nicht vor.
    secret = Config.cluster_secret.encode() if timesync.secret_usable(Config.cluster_secret) else _RANDOM_SESSION_KEY
    # Prüfwert des Notfall-Passworts (auf allen Pi gleich, emergency.salt) – nie der Klartext; neues Passwort:
    # alle Sitzungen enden. Ältere Konfiguration ohne Prüfwert: wie bisher der Klartext.
    ref = Config.admin_password_hash or Config.admin_password
    return hashlib.sha256(b"festpos-session\0" + secret + b"\0" + ref.encode()).digest()


def idle_minutes(store=None) -> int:
    """Minuten ohne Bedienung bis zum Abmelden (Einstellung, 10–120, Standard 30)."""
    try:
        v = int((store or rt.store).setting(IDLE_KEY, str(IDLE_DEFAULT)))
    except Exception:  # noqa: BLE001 – ungültig oder Datenbank gerade nicht da: Standard
        return IDLE_DEFAULT
    return min(IDLE_MAX, max(IDLE_MIN, v))


def idle_seconds(user: dict) -> int:
    """Frist ohne Bedienung für diesen Benutzer in Sekunden; 0 = keine (Rolle Anzeige, z.B. für die Statusanzeige)."""
    return 0 if user.get("role") == "view" else idle_minutes() * 60


def issue_session(uid: int = 0, gen: int = 0, idle_s: int = 0, exp: int | None = None, confirmed: int = 0) -> dict:
    """Neues Token. idle_s: Frist ohne Bedienung (0 = keine); exp: Ablauf behalten (Verlängern nach Bedienung);
    confirmed: kritische Aktionen bestätigt bis (Zeit, 0 = nicht; twofa.py) – Zufallsteil «~Frist~bestätigt»."""
    now = int(time.time())
    exp = exp or now + SESSION_HOURS * 3600
    idle = min(exp, now + idle_s) if idle_s > 0 else 0
    conf = min(exp, confirmed) if confirmed > now else 0
    nonce = secrets.token_urlsafe(12) + (f"~{idle}" if idle or conf else "") + (f"~{conf}" if conf else "")
    msg = f"{exp}.{int(uid)}.{int(gen)}.{nonce}"
    sig = hmac.new(_session_key(), msg.encode(), hashlib.sha256).hexdigest()
    return {"token": f"{msg}.{sig}", "expires": exp, "hours": SESSION_HOURS,
            "idle_until": idle or None, "idle_in": idle - now if idle else None, "idle_minutes": idle_s // 60 or None,
            "confirmed_in": conf - now if conf else None}


def _parse_session(token: str) -> tuple[str, int, int, int, int, int] | None:
    """('ok', Ablauf, Benutzer, Stand, Frist ohne Bedienung oder 0, bestätigt bis oder 0) – nur echte Tokens;
    None = gefälscht/kaputt."""
    try:
        exp_s, uid_s, gen_s, nonce, sig = token.split(".")
        exp, uid, gen = int(exp_s), int(uid_s), int(gen_s)
        parts = nonce.split("~")
        idle = int(parts[1]) if len(parts) > 1 else 0
        conf = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return None
    good = hmac.new(_session_key(), f"{exp_s}.{uid_s}.{gen_s}.{nonce}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig.encode(), good.encode()):
        return None
    return "ok", exp, uid, gen, idle, conf


def session_user(token: str) -> tuple[str, dict | None]:
    """('ok', Benutzer) | ('expired', None) – echt, aber abgelaufen/gesperrt | ('idle', None) – echt, aber zu lange
    ohne Bedienung | ('invalid', None) – gefälscht."""
    p = _parse_session(token)
    if not p:
        return "invalid", None
    _, exp, uid, gen, idle, _conf = p
    now = time.time()
    # Ablauf in der Zukunft: bis 1 Tag Toleranz (anderer Pi mit noch nicht gestellter Uhr), mehr ist verdächtig
    if exp < now or exp > now + SESSION_HOURS * 3600 + 86400:
        return "expired", None
    if idle and idle < now:
        return "idle", None
    user = auth.lookup(uid)
    if not user or int(user.get("gen", 0)) != gen:
        return "expired", None                      # gesperrt, gelöscht oder neues Passwort: neu anmelden
    return "ok", user


def refresh_session(token: str, user: dict) -> dict | None:
    """Nach Bedienung: neues Token mit neuer Frist, gleichem Ablauf (Höchstdauer bleibt). None: Token nicht gültig."""
    p = _parse_session(token)
    if not p or session_user(token)[0] != "ok":
        return None
    _, exp, uid, gen, _, conf = p
    return issue_session(uid, gen, idle_seconds(user), exp, conf)


def confirm_session(token: str, user: dict, seconds: int) -> dict | None:
    """Kritische Aktionen bestätigt (twofa.py): neues Token, bestätigt für «seconds», Frist ohne Bedienung neu."""
    p = _parse_session(token)
    if not p or session_user(token)[0] != "ok":
        return None
    _, exp, uid, gen, _, _ = p
    return issue_session(uid, gen, idle_seconds(user), exp, int(time.time()) + seconds)


def session_confirmed(request: Request, user: dict) -> bool:
    """Darf diese Anfrage eine kritische Aktion ausführen? Token: bestätigt und noch nicht abgelaufen. Basic Auth:
    schickt das Passwort mit – gilt als bestätigt, ausser der Benutzer hat einen zweiten Faktor."""
    scheme, _, cred = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "basic":
        return not user.get("totp_on")
    p = _parse_session(cred.strip()) if scheme.lower() == "bearer" else None
    return bool(p) and p[5] > time.time()


def security_warnings() -> list[str]:
    """Hinweise für Übersicht und Statusanzeige (Codes, übersetzt wird bei der Anzeige)."""
    out = []
    p = emergency_problem()
    if p:
        out.append(f"admin_pw_{p}")                 # admin_pw_placeholder | admin_pw_short | admin_pw_short_locked
    if Config.cluster_secret_derived:
        out.append("secret_derived")                # Prüfsummen hängen am Server-Zertifikat
    return out


def session_state(token: str) -> str:
    return session_user(token)[0]


# Notfall-Passwort aus festpos.conf (Prüfwert, app/emergency.py): Platzhalter sperren den Notfall-Login; kürzer als
# 16 Zeichen sperrt ihn, sobald es einen eigenen Admin-Benutzer gibt (sonst nur Warnung – niemand ausgesperrt)
PLACEHOLDER_PASSWORDS = emergency.PLACEHOLDERS
PW_WARN_LEN = emergency.MIN_LEN


def _other_admin() -> bool:
    """Gibt es einen eigenen, aktiven Benutzer mit der Rolle Admin (nicht den Notfall-Login)?"""
    try:
        return any(uid and u.get("role") == "admin" and u.get("active") for uid, u in auth._users().items())
    except Exception:  # noqa: BLE001 – Datenbank gerade nicht da: als «keiner» (nicht aussperren)
        return False


def emergency_problem() -> str | None:
    """placeholder (gesperrt) | short_locked (zu kurz, gesperrt: es gibt einen eigenen Admin) | short (Warnung) | None"""
    if Config.admin_password_hash:
        n = Config.admin_password_len or PW_WARN_LEN
    else:
        if Config.admin_password.strip() in PLACEHOLDER_PASSWORDS:
            return "placeholder"
        n = len(Config.admin_password)
    if n >= PW_WARN_LEN:
        return None
    return "short_locked" if _other_admin() else "short"


def _password_ok(given: str) -> bool:
    if emergency_problem() in ("placeholder", "short_locked"):
        auth._dummy()
        return False
    if Config.admin_password_hash:
        return emergency.check(given, Config.admin_password_hash)
    return secrets.compare_digest(given.encode(), Config.admin_password.encode())


def login_name(name: str | None) -> str:
    """Name, unter dem Fehlversuche gezählt werden («admin» und leer sind derselbe Notfall-Login)."""
    name = (name or "").strip()
    return auth.EMERGENCY_NAME if not name or name.lower() == auth.EMERGENCY_NAME else name


def check_admin(request: Request) -> tuple[dict | None, bool, str]:
    """(Benutzer oder None, zählt als Fehlversuch?, Name) – liest nur Header, nie den Inhalt der Anfrage."""
    scheme, _, cred = request.headers.get("authorization", "").partition(" ")
    scheme, cred = scheme.lower(), cred.strip()
    if scheme == "bearer":
        st, user = session_user(cred)
        # abgelaufen ist kein Rateversuch (z.B. offener Status-Tab)
        return user, st == "invalid", login_name(user["name"]) if user else ""
    if scheme == "basic":
        try:
            raw = base64.b64decode(cred, validate=True)
        except (binascii.Error, ValueError):
            return None, True, ""
        try:
            text = raw.decode("utf-8")              # Passwörter mit Umlauten (Browser schicken UTF-8)
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        name, _, pw = text.partition(":")
        login_throttle.check_name(request.client.host if request.client else "?", login_name(name))
        user = login_user(name, pw, request.client.host if request.client else None)
        return user, user is None, login_name(name)
    return None, False, ""


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def cross_site_basic(request: Request) -> bool:
    """Schreibende Anfrage mit Basic Auth, die der Browser von einer FREMDEN Seite aus schickt (Review 2026-11, S5)?
    Hat ein Browser Name und Passwort für Basic Auth gespeichert (z.B. von /status-einfach), schickt er sie bei jeder
    Anfrage an den Pi mit – auch wenn eine fremde Webseite ein Formular dorthin abschickt (CSRF). Die Verwaltung
    selbst arbeitet mit dem Sitzungs-Token (das schickt nur ihr eigenes Skript); curl und Skripte schicken weder
    «Sec-Fetch-Site» noch «Origin» und sind nicht betroffen."""
    if request.method not in UNSAFE_METHODS:
        return False
    if not request.headers.get("authorization", "").lower().startswith("basic "):
        return False
    site = request.headers.get("sec-fetch-site", "").lower()
    if site:
        return site not in ("same-origin", "none")
    origin = request.headers.get("origin", "")
    if not origin:
        return False
    from urllib.parse import urlsplit
    return origin == "null" or urlsplit(origin).netloc.lower() != request.headers.get("host", "").lower()


def login_user(name: str, password: str, ip: str | None = None) -> dict | None:
    """Name + Passwort prüfen. «admin» (oder leer) = Notfall-Login mit dem Passwort aus festpos.conf – nur aus dem
    FestPOS-Netz oder am Pi selbst (ip None: Aufruf ohne Anfrage, z.B. Werkzeuge)."""
    name = (name or "").strip()
    if login_name(name) == auth.EMERGENCY_NAME:
        if ip is not None and not emergency.local_ok(ip):
            auth._dummy()
            return None
        return auth.emergency() if _password_ok(password) else None
    row = None
    if rt.store is not None:
        try:
            row = rt.store.query_one("SELECT id, name, role, gen, active, pw_hash, totp FROM users "
                                     "WHERE name=? COLLATE NOCASE", (name,))
        except Exception:  # noqa: BLE001 – Datenbank gerade nicht da: nur der Notfall-Login geht
            row = None
    if not row or not row["active"]:
        auth._dummy()
        return None
    if not auth.verify_password(password, row["pw_hash"]):
        return None
    from .users import _with_totp
    t2 = _with_totp({"totp": row["totp"]})
    return {"id": row["id"], "name": row["name"], "role": row["role"], "gen": row["gen"], "active": True,
            "emergency": False, **t2}


def _unauthorized(request: Request) -> HTTPException:
    # Die Verwaltung hat ein eigenes Login – dort keinen Browser-Dialog auslösen
    hdr = {} if request.headers.get("X-Festpos-UI") else {"WWW-Authenticate": 'Basic realm="FestPOS Admin"'}
    return HTTPException(401, t("err.login.required"), headers=hdr)


def admin(request: Request) -> dict:
    """Abhängigkeit aller Verwaltungs-Routen: angemeldet und die Rolle hat ein Recht für diesen Pfad (auth.RULES).
    Liest evtl. die Datenbank (Benutzer) und rechnet scrypt (Basic Auth): nie direkt im Event-Loop aufrufen."""
    user = getattr(request.state, "user", None)     # schon von RequestGate geprüft (gleiche Anfrage)
    if user is None:
        if cross_site_basic(request):
            raise HTTPException(403, t("err.login.cross_site"))
        ip = request.client.host if request.client else "?"
        login_throttle.check(ip)
        user, bad, name = check_admin(request)
        if not user:
            if bad:
                login_throttle.fail(ip, name)
            raise _unauthorized(request)
        login_throttle.ok(ip, name)
    # scope["path"] wie das Routing – request.url.path schneidet bei «?»/«#» im Pfad ab (Review 2026-11, S8)
    path = request.scope.get("path") or request.url.path
    if not auth.allowed(user["role"], auth.required(request.method, path)):
        raise HTTPException(403, t("err.login.forbidden"))
    from . import twofa                              # zweiter Faktor, kritische Aktionen (T-0-N/FestPOS#9)
    basic = request.headers.get("authorization", "").lower().startswith("basic ")
    if twofa.setup_blocked(user) and not twofa.setup_allowed(path):
        raise HTTPException(403, {"code": "totp_setup", "text": t("err.totp.setup_first")})
    if basic and user.get("totp_on") and request.method in UNSAFE_METHODS:
        raise HTTPException(403, t("err.totp.basic"))          # mit zweitem Faktor: Basic Auth nur zum Lesen
    if auth.critical(request.method, path) and getattr(request.state, "critical_ok", None) is None:
        if not session_confirmed(request, user):
            raise HTTPException(428, {"code": "confirm_needed", "text": t("err.login.confirm_needed")})
        request.state.critical_ok = True
        try:                                         # Protokoll: welche Aktion, wer, bestätigt
            from . import ledger_base
            ledger_base.audit(rt.store, user.get("name"), "critical", method=request.method, path=path,
                              how="basic" if basic else "confirmed")
        except Exception:  # noqa: BLE001 – Protokoll darf die Aktion nicht verhindern
            log.exception("Protokoll (kritische Aktion)")
    request.state.user = user
    return user


# -- Verwaltung nur mit Gerätezertifikat ------------------------------------------------------------
# Einstellung «admin_cert_only»: die Verwaltung (/api/admin/…) nimmt auf dem Verwaltungs-Port nur noch Anfragen mit einem
# gültigen, nicht gesperrten Gerätezertifikat an (TLS fragt danach, app/serve.py). Der Handy-Port verlangt es ohnehin.
# Ausnahme: der Pi selbst (127.0.0.1, z.B. per SSH-Tunnel) – der Notweg, wenn kein Gerät mit Zertifikat mehr geht.
_cert_only: dict = {"until": 0.0, "on": False}


def admin_cert_only() -> bool:
    """Ist «Verwaltung nur mit Gerätezertifikat» eingeschaltet? (5 s zwischengespeichert, im Cluster gleich)"""
    now = time.monotonic()
    if now >= _cert_only["until"] and rt.store is not None:
        try:
            _cert_only["on"] = rt.store.setting("admin_cert_only", "0") == "1"
        except Exception:  # noqa: BLE001 – Datenbank gerade nicht da: letzter Stand gilt kurz weiter
            pass
        _cert_only["until"] = now + 5
    return _cert_only["on"]


def forget_cert_only() -> None:
    _cert_only["until"] = 0.0


def admin_cert_ok(request: Request) -> bool:
    """Kommt diese Anfrage mit einem gültigen, nicht gesperrten Gerätezertifikat? Auf jedem Port gegen die Sperrliste
    geprüft – am Handy-Port verlangt TLS zwar ein Zertifikat, sperrt aber selbst keines (dort ist /api/admin/ von
    main.revoked_gate ausgenommen). Liest die Sperrliste: nicht im Event-Loop aufrufen."""
    from . import devices
    info = devices.client_cert(request)
    if not info or info.get("days_left", 0) < 0:
        return False
    return info["serial"] not in devices.revoked_serials()


def admin_cert_needed(request: Request) -> bool:
    """Muss diese Verwaltungs-Anfrage ein Zertifikat zeigen und tut es nicht? (nur mit Gerätezertifikaten im Einsatz)"""
    if not Config.phone_port or not admin_cert_only():
        return False
    if (request.client.host if request.client else None) in LOCAL_HOSTS:
        return False                                   # am Pi selbst / SSH-Tunnel: Notweg
    return not admin_cert_ok(request)


def current_user(request: Request) -> dict | None:
    """Wer diese Anfrage stellt (nach admin()), sonst None – z.B. Statusanzeige am Pi selbst."""
    return getattr(request.state, "user", None)


# ---------------------------------------------------------------------------
# Vor dem Lesen des Inhalts: Anmeldung prüfen und Grösse begrenzen (REV-037)
# ---------------------------------------------------------------------------
# FastAPI liest hochgeladene Dateien, BEVOR die Anmeldung geprüft wird. Ohne diese Schranke könnte jeder im
# Netz grosse Uploads schicken, die RAM und SD-Karte belasten.
MB = 1024 * 1024
BODY_LIMITS = (                                   # (Pfadanfang, Höchstgrösse) – erster Treffer gilt
    ("/api/admin/restore", 260 * MB),
    ("/api/admin/update/upload", 610 * MB),          # Update-Paket (release.MAX_ZIP, mit Updates des Systems)
    ("/api/admin/fully/apk", 210 * MB),              # Fully-APK (heute ~25 MB, höchstens 200 MB: fully.APK_MAX)
    ("/api/admin/payrexx/apk", 31 * MB),             # Begleit-App «FestPOS Tap» (T13, wenige MB)
    ("/api/admin/import/images", 70 * MB),
    ("/api/admin/import/products", 9 * MB),
    ("/api/admin/products/", 9 * MB),             # Produktbild
    ("/api/admin/plan/areas/", 16 * MB),             # Grundriss für den Tischplan (Foto oder Scan, höchstens 15 MB)
    ("/api/admin/bon-design/logo", 9 * MB),          # Logo für den Bondrucker
    ("/api/admin/bon-design/image", 9 * MB),         # Bild im Bon-Baukasten (Baustein «Bild»)
)
DEFAULT_BODY_LIMIT = 1 * MB


def body_limit(path: str) -> int:
    return next((n for p, n in BODY_LIMITS if path.startswith(p)), DEFAULT_BODY_LIMIT)


class RequestGate:
    """ASGI-Middleware: Verwaltungs-Anfragen ohne gültige Anmeldung sofort abweisen, zu grosse Anfragen ablehnen."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        request = Request(scope)
        # Sprache dieser Anfrage (Wahl auf dem Handy bzw. in der Verwaltung) – gilt für alle Meldungen dahinter
        token = i18n.current.set(i18n.request_lang(request.headers.get("x-festpos-lang")))
        try:
            await self._handle(scope, receive, send, path, request)
        finally:
            i18n.current.reset(token)

    async def _handle(self, scope, receive, send, path: str, request: Request) -> None:
        if path.startswith("/api/admin/") and path not in ("/api/admin/login", "/api/admin/login/totp"):
            try:
                await asyncio.to_thread(admin, request)      # Datenbank/scrypt: nicht im Event-Loop (blockiert sonst alles)
            except HTTPException as e:
                return await _send_json(send, e.status_code, {"detail": e.detail}, e.headers)
        limit = body_limit(path)
        try:
            length = int(request.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        if length > limit:
            return await _send_json(send, 413, {"detail": t("err.request.too_large", mb=limit // MB)})
        seen = 0

        async def limited_receive():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:                  # ohne Content-Length (chunked): abbrechen wie ein Verbindungsabbruch
                    return {"type": "http.disconnect"}
            return msg

        await self.app(scope, limited_receive, send)


async def _send_json(send, status: int, data: dict, headers: dict | None = None) -> None:
    body = json.dumps(data, ensure_ascii=False).encode()
    hdrs = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    hdrs += [(k.lower().encode(), str(v).encode()) for k, v in (headers or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": hdrs})
    await send({"type": "http.response.body", "body": body})


def logo_info(st) -> dict:
    """Für Bestell-App, Statusanzeige und Verwaltung: Adresse des Logos (unveränderlich, /media/…) und ob es auf
    weissem Grund stehen soll (dunkle Logos auf dunklen Seiten)."""
    iid = st.setting("logo_image")
    return {"logo": f"/media/{iid}" if iid else None, "logo_plate": st.setting("logo_plate", "white") != "none"}
