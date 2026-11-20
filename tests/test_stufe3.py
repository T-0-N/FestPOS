"""Stufe 3 des Reviews: Zertifikate (REV-026), eine Versionsquelle (REV-052), feste Pakete (REV-031)."""
from __future__ import annotations

import os
import shutil
import ssl
import subprocess
import threading
from pathlib import Path

import pytest

from tests.test_api import AUTH

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
CONF = """NET_PREFIX=192.168.55
NET_CIDR=24
VIP=192.168.55.10
NODE_A_IP=192.168.55.11
NODE_B_IP=192.168.55.12
NODE_C_IP=192.168.55.13
ADMIN_PASSWORD=x
"""
pytestmark = pytest.mark.skipif(not shutil.which("openssl"), reason="openssl fehlt")


def make_certs(work: Path, ca_dir: Path, *args: str) -> subprocess.CompletedProcess:
    (work / "festpos.conf").write_text(CONF)
    return subprocess.run(["bash", str(DEPLOY / "make-certs.sh"), "festpos.conf", *args], cwd=work,
                          env={**os.environ, "FESTPOS_CA_DIR": str(ca_dir)}, capture_output=True, text=True)


def ossl(*args: str, inp: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["openssl", *args], capture_output=True, text=True, input=inp)


def sign_foreign(ca_dir: Path, tmp: Path, san: str) -> Path:
    """Mit dem (gestohlenen) ca.key ein Zertifikat für eine fremde Adresse ausstellen."""
    ossl("req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-keyout", str(tmp / "f.key"),
         "-out", str(tmp / "f.csr"), "-subj", "/CN=fremd")
    (tmp / "f.ext").write_text(f"subjectAltName={san}\n")
    r = ossl("x509", "-req", "-in", str(tmp / "f.csr"), "-CA", str(ca_dir / "ca.crt"), "-CAkey", str(ca_dir / "ca.key"),
             "-CAcreateserial", "-CAserial", str(tmp / "f.srl"), "-days", "10", "-extfile", str(tmp / "f.ext"),
             "-out", str(tmp / "f.crt"))
    assert r.returncode == 0, r.stderr
    return tmp / "f.crt"


def test_new_ca_is_name_constrained_and_key_outside_project(tmp_path):
    work, ca = tmp_path / "deploy", tmp_path / "geheim"
    work.mkdir()
    r = make_certs(work, ca)
    assert r.returncode == 0, r.stdout + r.stderr
    tls = work / "festpos-tls"
    assert (ca / "ca.key").exists() and not (tls / "ca.key").exists()
    assert {p.name for p in tls.iterdir()} == {"ca.crt", "server.crt", "server.key", "device-ca.crt", "device-ca.key"}
    nc = ossl("x509", "-in", str(tls / "ca.crt"), "-noout", "-ext", "nameConstraints").stdout
    assert "IP:192.168.55.0/255.255.255.0" in nc and "DNS:festpos.local" in nc
    assert ossl("verify", "-CAfile", str(tls / "ca.crt"), str(tls / "server.crt")).returncode == 0
    # gestohlener ca.key: fremde Webseiten lassen sich damit nicht fälschen
    for san in ("DNS:www.meine-bank.ch", "IP:8.8.8.8"):
        bad = ossl("verify", "-CAfile", str(tls / "ca.crt"), str(sign_foreign(ca, tmp_path, san)))
        assert bad.returncode != 0 and "permitted subtree" in (bad.stdout + bad.stderr), san
    assert "Fingerabdruck" in r.stdout and "HINWEIS" not in r.stdout


def test_old_layout_is_migrated_and_new_ca_replaces_old(tmp_path):
    work, ca = tmp_path / "deploy", tmp_path / "geheim"
    tls = work / "festpos-tls"
    tls.mkdir(parents=True)
    # altes Layout: CA ohne Namensbeschränkung, ca.key im Ordner, der auf die Karten kommt
    ossl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "100",
         "-keyout", str(tls / "ca.key"), "-out", str(tls / "ca.crt"), "-subj", "/CN=FestPOS Lokale CA/O=FestPOS",
         "-addext", "basicConstraints=critical,CA:TRUE")
    r = make_certs(work, ca)
    assert r.returncode == 0, r.stderr
    assert not (tls / "ca.key").exists() and (ca / "ca.key").exists() and "verschoben" in r.stdout
    assert "ohne Namensbeschränkung" in r.stdout
    device_before = (tls / "device-ca.crt").read_bytes()
    r = make_certs(work, ca, "--neue-ca")
    assert r.returncode == 0, r.stderr
    assert list(ca.glob("alt-*/ca.key")), "alte CA gesichert"
    assert "Lokale CA 20" in ossl("x509", "-in", str(tls / "ca.crt"), "-noout", "-subject").stdout
    assert "Permitted" in ossl("x509", "-in", str(tls / "ca.crt"), "-noout", "-ext", "nameConstraints").stdout
    assert (tls / "device-ca.crt").read_bytes() == device_before    # Gerätezertifikate der Handys bleiben gültig
    assert "NEUE Server-CA" in r.stdout


def test_ca_dir_inside_project_refused(tmp_path):
    work = tmp_path / "deploy"
    work.mkdir()
    r = make_certs(work, ROOT / "geheim-im-projekt")
    assert r.returncode != 0 and "Projektordner" in r.stdout
    assert not (ROOT / "geheim-im-projekt").exists() or not any((ROOT / "geheim-im-projekt").iterdir())
    shutil.rmtree(ROOT / "geheim-im-projekt", ignore_errors=True)


def test_system_certs_show_fingerprint_and_constraint(client, tmp_path, monkeypatch):
    from app import main as m
    work, ca = tmp_path / "deploy", tmp_path / "geheim"
    work.mkdir()
    assert make_certs(work, ca).returncode == 0
    monkeypatch.setattr(m.Config, "cafile", str(work / "festpos-tls" / "ca.crt"))
    certs = client.get("/api/admin/system/certs", auth=AUTH).json()["certs"]
    server_ca = next(c for c in certs if c.get("fingerprint"))
    assert server_ca["constrained"] is True and len(server_ca["fingerprint"].split(":")) == 32


def test_redirect_serves_ca_cert(tmp_path, monkeypatch):
    import importlib.util
    import urllib.request
    from http.server import ThreadingHTTPServer
    spec = importlib.util.spec_from_file_location("redirect", DEPLOY / "festpos-redirect.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "ca.crt").write_text("-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n")
    monkeypatch.setattr(mod, "CA_FILE", str(tmp_path / "ca.crt"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.Redirect)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        with urllib.request.urlopen(base + "/ca.crt") as r:
            assert r.headers["Content-Type"] == "application/x-x509-ca-cert" and b"BEGIN CERTIFICATE" in r.read()

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        with pytest.raises(urllib.error.HTTPError) as e:
            opener.open(base + "/")
        assert e.value.code == 301 and e.value.headers["Location"].startswith("https://")
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("line,expected", [
    ("PARTUUID=ab-01  /boot/firmware  vfat    defaults          0       2",
     "PARTUUID=ab-01  /boot/firmware  vfat  defaults,fmask=0077,dmask=0077  0  2"),
    ("PARTUUID=ab-01 /boot/firmware vfat defaults,umask=0022,noatime 0 2",
     "PARTUUID=ab-01  /boot/firmware  vfat  defaults,noatime,fmask=0077,dmask=0077  0  2"),
    ("PARTUUID=ab-01 /boot/firmware vfat defaults,fmask=0077,dmask=0077 0 2", None),        # schon so
    ("PARTUUID=ab-02 / ext4 defaults,noatime 0 1", None),                                    # andere Partition
])
def test_fstab_boot_private(tmp_path, line, expected):
    fstab = tmp_path / "fstab"
    fstab.write_text(f"# Kommentar\nproc /proc proc defaults 0 0\n{line}\n")
    r = subprocess.run(["bash", "-c", f'source "{DEPLOY}/festpos-conf.sh"; festpos_fstab_private "$1" /boot/firmware',
                        "_", str(fstab)], capture_output=True, text=True)
    lines = fstab.read_text().splitlines()
    assert lines[:2] == ["# Kommentar", "proc /proc proc defaults 0 0"]
    if expected is None:
        assert r.returncode == 1 and lines[2] == line and not (tmp_path / "fstab.festpos-alt").exists()
    else:
        assert r.returncode == 0 and lines[2] == expected and (tmp_path / "fstab.festpos-alt").exists()


# ---------------------------------------------------------------------------
# REV-052: eine Versionsquelle
# ---------------------------------------------------------------------------

def test_version_comes_from_version_file(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tests.conftest import load_app
    monkeypatch.setenv("FESTPOS_DB", f"sqlite://{tmp_path}/v.db")
    monkeypatch.setenv("FESTPOS_STATIC", str(ROOT / "static"))
    main = load_app()
    version = (ROOT / "VERSION").read_text().strip()
    assert main.APP_VERSION == version
    with TestClient(main.app) as c:
        assert c.get("/api/health").json()["version"] == version
        js = c.get("/app.js")
        assert f'const APP_VERSION = "{version}"' in js.text and "__FESTPOS_VERSION__" not in js.text
        assert js.headers["content-type"].startswith("text/javascript")
        sw = c.get("/sw.js")
        assert f'const VERSION = "festpos-{version}"' in sw.text
        again = c.get("/sw.js", headers={"If-None-Match": sw.headers["etag"]})
        assert again.status_code == 304
    for f in ("static/app.js", "static/sw.js", "app/main.py"):
        assert version not in (ROOT / f).read_text(), f"Versionsnummer von Hand in {f}"


# ---------------------------------------------------------------------------
# REV-031: Zertifikat des Handys kommt mit der festen uvicorn-Version an (serve.py erbt von uvicorn intern)
# ---------------------------------------------------------------------------

def test_client_certificate_reaches_the_app(tmp_path):
    import asyncio
    import datetime as dt
    import socket

    import uvicorn
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    from app import certs
    from tests.test_api import make_device_ca

    saved = dict(os.environ)
    try:
        from app.serve import CertAwareH11                       # setzt beim Import Umgebungsvariablen
    finally:
        os.environ.clear()
        os.environ.update(saved)

    # Server-Zertifikat für 127.0.0.1
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = dt.datetime.now(dt.timezone.utc)
    crt = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
           .serial_number(1).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=5))
           .add_extension(x509.SubjectAlternativeName([x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]),
                          critical=False).sign(key, hashes.SHA256()))
    (tmp_path / "s.crt").write_bytes(crt.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "s.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))
    dca_crt, dca_key, _ = make_device_ca(tmp_path)
    ca_cert, ca_key = certs.load_ca(dca_crt, dca_key)
    p12, password, serial, _ = certs.issue(ca_cert, ca_key, "XCover 1", 30)
    from cryptography.hazmat.primitives.serialization import pkcs12
    k, c, _ = pkcs12.load_key_and_certificates(p12, password.encode())
    (tmp_path / "c.pem").write_bytes(c.public_bytes(serialization.Encoding.PEM) + k.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))

    async def asgi(scope, receive, send):
        der = (scope.get("extensions") or {}).get("festpos.client_cert")
        body = certs.describe(der)["serial"].encode() if der else b"-"
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": body})

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(asgi, host="127.0.0.1", port=port, lifespan="off", log_level="warning",
                                           ssl_certfile=str(tmp_path / "s.crt"), ssl_keyfile=str(tmp_path / "s.key"),
                                           ssl_ca_certs=str(dca_crt), ssl_cert_reqs=ssl.CERT_REQUIRED, http=CertAwareH11))

    async def run():
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.05)
        ctx = ssl.create_default_context(cafile=str(tmp_path / "s.crt"))
        ctx.load_cert_chain(str(tmp_path / "c.pem"))

        def fetch():
            import urllib.request
            with urllib.request.urlopen(f"https://127.0.0.1:{port}/", context=ctx, timeout=5) as r:
                return r.read().decode()
        try:
            return await asyncio.to_thread(fetch)
        finally:
            server.should_exit = True
            await task
    assert asyncio.run(run()) == serial


def test_missing_ca_key_never_silently_replaces_ca(tmp_path):
    """ca.key liegt auf dem USB-Stick (nicht am Standardort): KEINE stille neue CA, sonst kämen alle Handys
    nach dem Einspielen nicht mehr auf die App."""
    work, ca, stick = tmp_path / "deploy", tmp_path / "geheim", tmp_path / "usb"
    work.mkdir()
    assert make_certs(work, ca).returncode == 0
    before = (work / "festpos-tls" / "ca.crt").read_bytes()
    shutil.move(str(ca), str(stick))
    r = make_certs(work, ca)
    assert r.returncode != 0 and "ca.key nicht gefunden" in r.stdout
    assert (work / "festpos-tls" / "ca.crt").read_bytes() == before
    r = make_certs(work, stick)                                     # mit dem Stick: neues Server-Zertifikat, gleiche CA
    assert r.returncode == 0 and (work / "festpos-tls" / "ca.crt").read_bytes() == before


def test_relative_ca_dir_inside_project_refused(tmp_path):
    work = ROOT / "deploy"
    (tmp_path / "festpos.conf").write_text(CONF)
    r = subprocess.run(["bash", str(DEPLOY / "make-certs.sh"), str(tmp_path / "festpos.conf")], cwd=tmp_path,
                       env={**os.environ, "FESTPOS_CA_DIR": os.path.relpath(work / "geheim-rel", tmp_path)},
                       capture_output=True, text=True)
    assert r.returncode != 0 and "Projektordner" in r.stdout
    assert not (work / "geheim-rel").exists()
