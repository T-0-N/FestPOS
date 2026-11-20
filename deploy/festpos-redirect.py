#!/usr/bin/env python3
"""FestPOS: leitet http://… auf https://… um (wer nur die IP eintippt, landet in der App).
Ausnahmen: http://…/ca.crt liefert die Server-CA zum Installieren auf Handys und PCs (öffentlich, kein Geheimnis);
http://…/fully/<Einmal-Code>.zip reicht eine ZIP für Fully Kiosk weiter, die die App lokal bereithält (app/fully.py –
Fully holt sie mit loadZipFile; der Inhalt ist öffentlich oder mit einem Kennwort verschlüsselt);
http://…/fully/<Code>.json die Einstellungen eines neuen Handys beim Einrichten (mit seiner Adresse, für DHCP);
http://…/fully/fully.apk liefert die Fully-APK, die in der Verwaltung hochgeladen wurde (Einrichten neuer Handys –
Android prüft sie mit der Prüfsumme aus dem Einrichtungs-QR); http://…/fully/festpos-tap.apk die Begleit-App für die
NFC-Zahlung über Payrexx (T13), die Fully damit auf die Handys installiert."""
import os
import re
import shutil
import ssl
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


CA_FILE = "/etc/festpos/tls/ca.crt"
APP = "https://127.0.0.1:8443"
ZIP_RE = re.compile(r"^/fully/([A-Za-z0-9_-]{20,64})\.zip$")
JSON_RE = re.compile(r"^/fully/([A-Za-z0-9_-]{20,64})\.json$")    # Einstellungen beim Einrichten eines Handys
ZIP_MAX = 1024 * 1024
APK = os.path.join(os.environ.get("FESTPOS_FULLY_DIR", "/var/lib/festpos/fully"), "fully.apk")   # app/fully.py
TAP_APK = os.path.join(os.environ.get("FESTPOS_FULLY_DIR", "/var/lib/festpos/fully"), "festpos-tap.apk")   # T13


def fetch(path: str, client: str = "") -> bytes | None:
    """Bei der App auf diesem Pi abholen (localhost: Zertifikat nicht prüfen). client: Adresse des Handys."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(f"{APP}{path}", headers={"X-Festpos-Client": client} if client else {})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            data = r.read(ZIP_MAX + 1)
    except (urllib.error.URLError, OSError):
        return None
    return data if len(data) <= ZIP_MAX else None


def fetch_zip(token: str) -> bytes | None:
    return fetch(f"/api/device-cert/fully/{token}")


def fetch_settings(token: str, client: str) -> bytes | None:
    return fetch(f"/api/device-cert/fully/settings/{token}", client)


class Redirect(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.split("?")[0] == "/fully/fully.apk":
            self.send_apk()
            return
        if self.path.split("?")[0] == "/fully/festpos-tap.apk":     # Begleit-App für NFC-Zahlung (T13)
            self.send_apk(TAP_APK)                                  # erst hier gelesen (Tests ändern TAP_APK)
            return
        m = JSON_RE.match(self.path.split("?")[0])
        if m and self.command == "GET":
            self.send_data(fetch_settings(m.group(1), self.client_address[0]), "application/json")
            return
        m = ZIP_RE.match(self.path.split("?")[0])
        if m and self.command == "GET":                    # HEAD würde die Einmal-ZIP verbrauchen
            self.send_data(fetch_zip(m.group(1)), "application/zip")
            return
        if self.path.split("?")[0] == "/ca.crt":           # Server-CA für Handys/PCs: http://192.168.55.10/ca.crt
            try:
                with open(CA_FILE, "rb") as f:
                    data = f.read()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-x509-ca-cert")
            self.send_header("Content-Disposition", 'attachment; filename="festpos-ca.crt"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
            return
        host = (self.headers.get("Host") or "").split(":")[0] or "192.168.55.10"
        # Verwaltung läuft auf 8443 (ohne Gerätezertifikat), alles andere auf 443 (Handys)
        target = f"https://{host}:8443/admin.html" if self.path.startswith("/admin") else f"https://{host}{self.path}"
        self.send_response(301)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_data(self, data: bytes | None, ctype: str):
        if data is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_apk(self, path: str | None = None):
        try:
            f = open(path or APK, "rb")
        except OSError:
            self.send_error(404)
            return
        with f:
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.android.package-archive")
            self.send_header("Content-Length", str(os.fstat(f.fileno()).st_size))
            self.end_headers()
            if self.command != "HEAD":
                shutil.copyfileobj(f, self.wfile, 1 << 20)

    do_HEAD = do_GET

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 80), Redirect).serve_forever()
