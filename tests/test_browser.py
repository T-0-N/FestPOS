"""Rauchtest im echten Browser (Chromium über Playwright): Bestell-App, Verwaltung, Statusanzeige.

Prüft, was die anderen Tests nicht sehen: JavaScript-Fehler, Content-Security-Policy, Anmeldung mit
Sitzungs-Token, Doppeltipp-Schutz an der Kasse, schneller Seitenwechsel in der Verwaltung.
Wird übersprungen, wenn Playwright oder Chromium fehlt (z.B. auf dem Pi).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
httpx = pytest.importorskip("httpx2")

from tests.conftest import free_port  # noqa: E402
from tests.test_api import AUTH, setup_shop  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _outward_ip() -> str | None:
    """Eine Adresse, die NICHT 127.0.0.1 ist (die Statusanzeige ist von dort ohne Anmeldung offen)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("browser")
    port = free_port()
    env = {**os.environ, "FESTPOS_DB": f"sqlite://{tmp}/b.db", "FESTPOS_ADMIN_PASSWORD": "geheim",
           "FESTPOS_STATIC": str(ROOT / "static"), "FESTPOS_NODE": "a"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(base + "/api/health", timeout=1).json().get("ready"):
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("Server startet nicht")
    with httpx.Client(base_url=base, timeout=10) as c:
        setup_shop(c)
    yield base, port
    proc.terminate()
    proc.wait(10)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Chromium nicht startbar: {e}")
        yield b
        b.close()


def _favicon(m) -> bool:
    """Favicon-Anfrage des Browsers (sichtbarer Chromium): der Name steht in der URL, nicht im Text der Meldung."""
    return "favicon" in m.text or "favicon" in ((m.location or {}).get("url") or "")


def _watch(page) -> list[str]:
    """Sammelt JavaScript-Fehler und CSP-Verstösse."""
    errs: list[str] = []
    page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    page.on("console", lambda m: errs.append(f"console: {m.text}")
            if m.type == "error" and not _favicon(m) else None)
    return errs


def test_app_order_double_tap_and_csp(server, browser):
    base, _ = server
    ctx = browser.new_context(viewport={"width": 400, "height": 820})
    page = ctx.new_page()
    errs = _watch(page)
    resp = page.goto(base + "/index.html")
    assert "script-src 'self'" in resp.headers["content-security-policy"]
    page.wait_for_selector('[data-act="digit"]')
    page.click('[data-act="digit"][data-d="3"]')
    page.click('[data-act="table-ok"]')
    page.wait_for_selector('[data-act="add"]')
    page.click('[data-act="add"] >> nth=0')
    page.click('[data-act="add"] >> nth=0')
    page.click("#send")
    page.click('[data-act="done"]')                       # «Doppeltipp»: zweiter Tipp landet auf «Nächste Bestellung»
    assert page.locator(".pay-view").count() == 1, "Doppeltipp darf die Kasse nicht verlassen"
    page.wait_for_selector('#paystate[data-s="sent"]', timeout=10000)
    page.wait_for_timeout(700)
    page.click('[data-act="done"]')                       # danach normal weiter
    page.wait_for_selector(".table-view")
    assert not errs, errs
    ctx.close()


def test_admin_token_navigation_and_status(server, browser):
    base, _ = server
    ctx = browser.new_context(viewport={"width": 1200, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector(".side")
    stored = page.evaluate("JSON.stringify(sessionStorage)")
    assert "festpos.token" in stored and "geheim" not in stored       # nur das Token, nie das Passwort
    # schneller Seitenwechsel: am Ende steht die zuletzt gewählte Seite da
    for p in ("products", "devices", "jobs"):
        page.click(f'.side button[data-page="{p}"]')
    page.wait_for_timeout(1500)
    assert page.inner_text("#main h2").startswith("Druckaufträge")
    # Statusanzeige aus der Verwaltung öffnen: übernimmt die Anmeldung
    with page.expect_popup() as pop:
        page.click('a[href="/status.html"]')
    st = pop.value
    st_errs = _watch(st)
    st.wait_for_timeout(2500)
    assert "Nicht angemeldet" not in st.inner_text("body")
    assert not errs and not st_errs, errs + st_errs
    ctx.close()


def test_status_stops_polling_after_401(server, browser):
    base, port = server
    ip = _outward_ip()
    if not ip:
        pytest.skip("keine Adresse ausser 127.0.0.1")
    ctx = browser.new_context()
    page = ctx.new_page()
    calls: list[str] = []
    page.on("request", lambda r: calls.append(r.url) if "/api/status" in r.url else None)
    page.add_init_script("sessionStorage.setItem('festpos.token', '1.abc.falsch')")
    page.goto(f"http://{ip}:{port}/status.html")
    page.wait_for_timeout(7500)                           # sonst 3 Abfragen
    assert len(calls) == 1, calls
    assert "Nicht angemeldet" in page.inner_text("body")
    # und es zählte nicht als Rateversuch: Anmeldung geht weiterhin
    assert httpx.get(f"{base}/api/admin/settings", auth=AUTH).status_code == 200
    ctx.close()
