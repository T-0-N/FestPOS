"""Bestell-App startet ohne Server aus dem Service Worker (z.B. während eines Failovers). Seit app.js auf mehrere
Skripte verteilt ist (static/app/), muss jedes in sw.js (SHELL) stehen – sonst fehlt offline ein Teil der App.
Der Service Worker läuft nur über HTTPS: Server mit selbst signiertem Zertifikat. Im Browser; wird übersprungen,
wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import datetime as dt
import ipaddress
import os
import subprocess
import sys
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
httpx = pytest.importorskip("httpx2")

from tests.conftest import free_port  # noqa: E402
from tests.test_api import setup_shop  # noqa: E402
from tests.test_browser_p4 import ROOT, _watch  # noqa: E402
from tests.test_browser_receipt import _until  # noqa: E402


def _self_signed(tmp):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .sign(key, hashes.SHA256()))
    (tmp / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption()))
    return tmp / "c.pem", tmp / "k.pem"


@pytest.fixture(scope="module")
def browser():
    """Eigener Chromium, der das selbst signierte Zertifikat annimmt: mit einem Zertifikatsfehler registriert Chromium
    keinen Service Worker (ignore_https_errors allein reicht dafür nicht)."""
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(args=["--ignore-certificate-errors"])
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Chromium nicht startbar: {e}")
        yield b
        b.close()


def _stop(proc) -> None:
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:                                     # offene Verbindungen des Browsers
        proc.kill()
        proc.wait(5)


def test_app_starts_from_service_worker_without_server(tmp_path, browser):
    crt, key = _self_signed(tmp_path)
    port = free_port()
    env = {**os.environ, "FESTPOS_DB": f"sqlite://{tmp_path}/o.db", "FESTPOS_ADMIN_PASSWORD": "geheim",
           "FESTPOS_STATIC": str(ROOT / "static"), "FESTPOS_NODE": "a"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
                             "--ssl-certfile", str(crt), "--ssl-keyfile", str(key)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"https://127.0.0.1:{port}"
    try:
        c = httpx.Client(base_url=base, timeout=10, verify=False)
        for _ in range(150):
            try:
                if c.get("/api/health").json().get("ready"):
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        else:
            pytest.fail("Server startet nicht")
        setup_shop(c)
        ctx = browser.new_context(viewport={"width": 412, "height": 900}, ignore_https_errors=True)
        ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('XCover 1'))")
        page = ctx.new_page()
        errs = _watch(page)
        page.goto(base + "/index.html")
        page.wait_for_selector('[data-act="digit"]')
        _until(page, "!!navigator.serviceWorker.controller", 15000)        # Service Worker installiert und aktiv
        n_cached = page.evaluate("""caches.keys().then((ks) => Promise.all(ks.map((k) => caches.open(k).then((c) => c.keys()))))
            .then((all) => all.flat().filter((r) => new URL(r.url).pathname.startsWith('/app/')).length)""")
        assert n_cached == len(list((ROOT / "static" / "app").glob("*.js")))
    finally:
        _stop(proc)
    # Server weg: neu laden kommt ganz aus dem Cache
    page.reload()
    page.wait_for_selector('[data-act="digit"]')
    for d in "3":
        page.click(f'[data-act="digit"][data-d="{d}"]')
    page.click('[data-act="table-ok"]')
    page.wait_for_selector(".grid .tile")                                # Preisliste aus dem Gerät
    real = [e for e in errs if "Failed to load resource" not in e and "net::ERR" not in e]
    assert real == [], real
    ctx.close()
