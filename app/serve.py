"""FestPOS-Server: zwei HTTPS-Ports in EINEM Prozess (der Druck-Spooler läuft so nur einmal).

  Port 443  – Handys. Mit FESTPOS_PHONE_CERTS=1 nur mit gültigem Gerätezertifikat (mTLS):
              Ohne Zertifikat kommt gar keine Verbindung zustande.
  Port 8443 – Verwaltung und Systemzugriffe (keepalived, Zeitabgleich). Fragt nach einem Gerätezertifikat, verlangt
              es auf TLS-Ebene aber nicht (die Pi untereinander haben keines). Mit «Verwaltung nur mit
              Gerätezertifikat» weist die App die Verwaltung ohne gültiges Zertifikat ab
              (main.admin_cert_gate); die Verwaltung ist zusätzlich per Passwort geschützt; Bestellen ist hier gesperrt.

Übergang nach einem Netz-Umzug (T-0-N/FestPOS#2, FESTPOS_LISTEN_ALT): der Server lauscht dann pro Adresse – die neuen
(FESTPOS_LISTEN, mit dem neuen Zertifikat) und die alten (mit dem alten Zertifikat FESTPOS_TLS_ALT_CERT, gleicher
Schlüssel). So passt das Zertifikat zur Adresse, über die ein Handheld kommt, egal welche CA es schon hat. Adressen, die
(noch) nicht auf diesem Pi liegen (VIP), werden mit IP_FREEBIND gebunden.

Start:  python -m app.serve        (Einstellungen über Umgebungsvariablen, siehe deploy/festpos.service)
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import ssl

import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol

PHONE_PORT = int(os.environ.get("FESTPOS_PHONE_LISTEN", "443"))
ADMIN_PORT = int(os.environ.get("FESTPOS_ADMIN_LISTEN", "8443"))
CERT = os.environ.get("FESTPOS_TLS_CERT", "/etc/festpos/tls/server.crt")
KEY = os.environ.get("FESTPOS_TLS_KEY", "/etc/festpos/tls/server.key")
DEVICE_CA = os.environ.get("FESTPOS_DEVICE_CA", "/etc/festpos/tls/device-ca.crt")
PHONE_CERTS = os.environ.get("FESTPOS_PHONE_CERTS", "1") == "1"
LISTEN = [x.strip() for x in os.environ.get("FESTPOS_LISTEN", "").split(",") if x.strip()]
LISTEN_ALT = [x.strip() for x in os.environ.get("FESTPOS_LISTEN_ALT", "").split(",") if x.strip()]
ALT_CERT = os.environ.get("FESTPOS_TLS_ALT_CERT", "")
IP_FREEBIND = getattr(socket, "IP_FREEBIND", 15)

# Der App mitteilen, welcher Port der Handy-Port ist (nur dort ist Bestellen erlaubt)
os.environ["FESTPOS_PHONE_PORT"] = str(PHONE_PORT) if PHONE_CERTS else "0"
os.environ["FESTPOS_ADMIN_PORT"] = str(ADMIN_PORT)

from app.main import app  # noqa: E402  (nach dem Setzen der Umgebung importieren)


class CertAwareH11(H11Protocol):
    """Reicht das Client-Zertifikat (DER) der TLS-Verbindung an die App weiter:
    scope["extensions"]["festpos.client_cert"]. uvicorn selbst stellt es nicht bereit."""

    def connection_made(self, transport):
        super().connection_made(transport)
        ssl_obj = transport.get_extra_info("ssl_object")
        der = ssl_obj.getpeercert(binary_form=True) if ssl_obj else None
        inner = self.app

        async def app(scope, receive, send):
            if der:
                scope.setdefault("extensions", {})["festpos.client_cert"] = der
            await inner(scope, receive, send)
        self.app = app


class QuietServer(uvicorn.Server):
    """Zweiter Server: keine eigenen Signal-Handler (der erste steuert das Beenden)."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


def bind(addrs: list[str], port: int) -> list[socket.socket]:
    """Je Adresse ein Socket (auch für eine VIP, die gerade ein anderer Pi hat)."""
    out = []
    for a in addrs:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with contextlib.suppress(OSError):
            s.setsockopt(socket.IPPROTO_IP, IP_FREEBIND, 1)
        s.bind((a, port))
        s.listen(2048)
        s.set_inheritable(True)
        out.append(s)
    return out


async def main_split() -> None:
    """Übergang nach dem Netz-Umzug: neue Adressen mit dem neuen, alte mit dem alten Zertifikat."""
    servers, tasks = [], []
    for addrs, cert in ((LISTEN, CERT), (LISTEN_ALT, ALT_CERT)):
        common = dict(ssl_certfile=cert, ssl_keyfile=KEY, log_level=os.environ.get("FESTPOS_LOGLEVEL", "info").lower(),
                      access_log=False, timeout_keep_alive=30)
        admin_cfg, phone_cfg = dict(common), dict(common)
        if PHONE_CERTS and os.path.exists(DEVICE_CA):
            admin_cfg.update(ssl_ca_certs=DEVICE_CA, ssl_cert_reqs=ssl.CERT_OPTIONAL, http=CertAwareH11)
        if PHONE_CERTS:
            phone_cfg.update(ssl_ca_certs=DEVICE_CA, ssl_cert_reqs=ssl.CERT_REQUIRED, http=CertAwareH11)
        first = not servers
        admin = (uvicorn.Server if first else QuietServer)(uvicorn.Config(app, lifespan="on" if first else "off",
                                                                         **admin_cfg))
        phone = QuietServer(uvicorn.Config(app, lifespan="off", **phone_cfg))
        servers += [admin, phone]
        tasks.append(asyncio.create_task(admin.serve(sockets=bind(addrs, ADMIN_PORT))))
        if first:
            while not admin.started:                 # Verwaltungs-Port zuerst (Lebenszyklus der App)
                if tasks[0].done():
                    await tasks[0]
                    return
                await asyncio.sleep(0.05)
        tasks.append(asyncio.create_task(phone.serve(sockets=bind(addrs, PHONE_PORT))))
    done, _ = await asyncio.wait(set(tasks), return_when=asyncio.FIRST_COMPLETED)
    for s in servers:
        s.should_exit = True
    await asyncio.gather(*tasks, return_exceptions=True)
    for t in done:
        if t.exception():
            raise t.exception()


async def main() -> None:
    if LISTEN and LISTEN_ALT and ALT_CERT and os.path.exists(ALT_CERT):
        await main_split()
        return
    common = dict(ssl_certfile=CERT, ssl_keyfile=KEY, log_level=os.environ.get("FESTPOS_LOGLEVEL", "info").lower(),
                  access_log=False, timeout_keep_alive=30)
    admin_cfg = dict(common)
    if PHONE_CERTS and os.path.exists(DEVICE_CA):
        admin_cfg.update(ssl_ca_certs=DEVICE_CA, ssl_cert_reqs=ssl.CERT_OPTIONAL, http=CertAwareH11)
    admin = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=ADMIN_PORT, lifespan="on", **admin_cfg))
    phone_cfg = dict(common)
    if PHONE_CERTS:
        phone_cfg.update(ssl_ca_certs=DEVICE_CA, ssl_cert_reqs=ssl.CERT_REQUIRED, http=CertAwareH11)
    phone = QuietServer(uvicorn.Config(app, host="0.0.0.0", port=PHONE_PORT, lifespan="off", **phone_cfg))

    admin_task = asyncio.create_task(admin.serve())
    while not admin.started:                      # Verwaltungs-Port zuerst (er beantwortet /api/health…)
        if admin_task.done():
            await admin_task                      # Startfehler weiterreichen
            return
        await asyncio.sleep(0.05)
    phone_task = asyncio.create_task(phone.serve())
    done, _ = await asyncio.wait({admin_task, phone_task}, return_when=asyncio.FIRST_COMPLETED)
    admin.should_exit = phone.should_exit = True
    await asyncio.gather(admin_task, phone_task, return_exceptions=True)
    for t in done:
        if t.exception():
            raise t.exception()


if __name__ == "__main__":
    asyncio.run(main())
