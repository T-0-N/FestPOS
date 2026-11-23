"""Einfache Statusanzeige /status-einfach im Browser: status-einfach.js tauscht nur den Inhalt aus (kein Neuladen,
kein weisses Aufblitzen), zeigt nach zwei Fehlschlägen «Keine Verbindung» und lädt bei neuer Version ganz neu.
Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("playwright.sync_api")
pytest.importorskip("httpx2")

from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("status_einfach"))
    yield base, c
    proc.terminate()
    proc.wait(10)


def _fast(route):
    """Die Seite mit 1 s statt 10 s zwischen den Abrufen ausliefern – sonst dauert der Test zu lange."""
    r = route.fetch()
    route.fulfill(response=r, body=r.text().replace('data-sec="10"', 'data-sec="1"'))


def test_updates_in_place_offline_hint_and_reload_on_new_version(server, browser):  # noqa: F811
    base, c = server
    ctx = browser.new_context(viewport={"width": 1024, "height": 768})       # iPad mini quer
    page = ctx.new_page()
    errs = _watch(page)
    page.route(base + "/status-einfach", _fast)
    page.goto(base + "/status-einfach")                                      # am Pi (127.0.0.1) ohne Anmeldung
    assert page.get_attribute("#s", "data-sec") == "1"
    assert not page.is_visible(".offline")
    page.evaluate("window.festposMarke = 1")                                 # verschwindet beim Neuladen
    assert "Tisch 42" not in page.inner_text("#s")

    pid = c.get("/api/bootstrap").json()["products"][0]["id"]
    assert c.post("/api/orders", json={"client_uuid": str(uuid.uuid4()), "table": "42", "device": "XCover-9",
                                       "lines": [{"product_id": pid, "qty": 1}]}).status_code == 200
    _until(page, "document.getElementById('s').innerText.indexOf('Tisch 42') >= 0")
    assert page.evaluate("window.festposMarke") == 1                         # ausgetauscht, nicht neu geladen
    assert page.locator("#s header").count() == 1 and page.locator("#s #s").count() == 0

    # Server weg: nach zwei Fehlschlägen Hinweis und abgedunkelt, weiter versuchen
    page.route(base + "/status-einfach?teil=1", lambda route: route.abort())
    _until(page, "document.body.className === 'off'")
    assert page.is_visible(".offline") and "Keine Verbindung zum Server" in page.inner_text(".offline")
    assert page.evaluate("getComputedStyle(document.getElementById('s')).opacity") == "0.45"
    assert "Tisch 42" in page.inner_text("#s")                               # alter Inhalt bleibt stehen
    page.unroute(base + "/status-einfach?teil=1")
    _until(page, "document.body.className === ''")                           # wieder da
    assert not page.is_visible(".offline") and page.evaluate("window.festposMarke") == 1

    # Update auf eine neue Version: ganze Seite neu laden (neues CSS und Skript)
    def new_version(route):
        r = route.fetch()
        route.fulfill(response=r, headers={**r.headers, "x-festpos-version": "99.0.0"})
    page.route(base + "/status-einfach?teil=1", new_version)
    _until(page, "window.festposMarke === undefined")
    page.unroute(base + "/status-einfach?teil=1")
    page.wait_for_selector("#s header")
    assert all(e == "console: Failed to load resource: net::ERR_FAILED" for e in errs), errs   # nur die abgebrochenen
    ctx.close()
