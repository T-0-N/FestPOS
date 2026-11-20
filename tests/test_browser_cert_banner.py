"""T3: Hinweis «Gerätezertifikat läuft ab» in der Bestell-App – nur, wenn der Pi das Zertifikat nicht selbst erneuert.
Ohne Fully mit «Jetzt erneuern», mit Fully (Automatik aus oder gescheitert) mit dem Weg über die Festleitung.
Die Antwort von /api/bootstrap wird im Browser abgeändert (Zertifikat wie vom Server). Handy 412 und 360 px.
Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("banner"))
    yield base, c
    proc.terminate()
    proc.wait(10)


def _app(browser, base, cert, width=412):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": 892})
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('Anna'))")
    page = ctx.new_page()
    errs = _watch(page)

    def boot(route):
        r = route.fetch()
        data = r.json()
        data["cert"] = cert
        route.fulfill(response=r, body=json.dumps(data))
    page.route("**/api/bootstrap", boot)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    _until(page, "typeof S !== 'undefined' && !!S.boot?.cert")      # Antwort mit dem Zertifikat ist da
    return ctx, page, errs


def _close(ctx, page):
    page.unroute_all(behavior="ignoreErrors")                         # die App fragt regelmässig nach
    ctx.close()


CERT = {"name": "XCover 7", "serial": "abc", "expires_at": "2026-12-01", "days_left": 30, "warn_days": 730,
        "renew_due": True, "revoked": False}


@pytest.mark.parametrize("width", [412, 360])
def test_banner_only_without_automatic_renewal(server, browser, width):  # noqa: F811
    base, _ = server
    ctx, page, errs = _app(browser, base, {**CERT, "auto": True, "fully": True}, width)
    assert page.locator(".banner").count() == 0                         # der Pi erneuert selbst: nichts zu tun
    _close(ctx, page)
    ctx, page, errs2 = _app(browser, base, {**CERT, "auto": False, "fully": True}, width)
    text = page.inner_text(".banner")
    assert "Festleitung" in text and page.locator('.banner [data-act="renew"]').count() == 0   # Fully: kein Download
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    _close(ctx, page)
    ctx, page, errs3 = _app(browser, base, {**CERT, "auto": False, "fully": False}, width)
    assert page.locator('.banner [data-act="renew"]').count() == 1      # Browser ohne Fully: «Jetzt erneuern»
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    _close(ctx, page)
    assert not (errs + errs2 + errs3), errs + errs2 + errs3
