"""Verwaltung › Tagesabschluss › «Saison abschliessen» (T-0-N/FestPOS#12): Archiv erstellen, herunterladen, wieder
hochladen (bestätigt), Gastdaten löschen (kritisch: «Bitte bestätigen»). PC und Handy (412, 360 px) ohne Überbreite."""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import AUTH, _confirm, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402

PW = "Saison-Archiv-2026"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("saison"), target="tests.asgi_testphone:app")   # mit Produkten
    pid = c.get("/api/bootstrap").json()["products"][0]["id"]
    r = c.post("/api/orders", json={"client_uuid": str(uuid.uuid4()), "table": "5", "device": "XCover 1",
                                    "lines": [{"product_id": pid, "qty": 2}]})
    assert r.status_code == 200, r.text
    assert c.post("/api/admin/closings", auth=AUTH, json={}).status_code == 200
    yield base, c
    proc.terminate()
    proc.wait(10)


def _open(browser, base, width):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": 900}, accept_downloads=True)
    page = ctx.new_page()
    errs = _watch(page)
    page.on("dialog", lambda d: d.accept())
    page.goto(base + "/admin.html#closing")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector("#season")
    return ctx, page, errs


@pytest.mark.parametrize("width", [412, 360])
def test_card_fits_on_phone(server, browser, width):  # noqa: F811
    base, _ = server
    ctx, page, errs = _open(browser, base, width)
    assert "Saison abschliessen" in page.inner_text("#season")
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert not errs, errs
    ctx.close()


def test_archive_download_upload_and_close(server, browser, tmp_path):  # noqa: F811
    base, c = server
    ctx, page, errs = _open(browser, base, 1300)
    assert "1 Tagesabschlüsse" in page.inner_text("#season") or "Tagesabschl" in page.inner_text("#season")
    page.fill('#season-create [name="pw"]', PW)
    page.fill('#season-create [name="pw2"]', PW)
    page.click("#season-create button")
    page.wait_for_selector("#season-dl")
    assert page.is_disabled("#season-close")                             # noch nicht bestätigt
    with page.expect_download() as dl:
        page.click("#season-dl")
    path = tmp_path / dl.value.suggested_filename
    dl.value.save_as(path)
    page.set_input_files('#season-check [name="file"]', str(path))
    page.fill('#season-check [name="pw"]', PW)
    page.click("#season-check button")
    _until(page, "document.querySelector('#season-close') && !document.querySelector('#season-close').disabled")
    assert "wieder hochgeladen" in page.inner_text("#season")
    page.click("#season-close")
    _confirm(page)                                                        # kritisch: Passwort nochmals
    _until(page, "document.querySelector('#season')?.textContent.includes('abgeschlossen am')")
    assert c.get("/api/admin/season", auth=AUTH).json()["last"]["from"] == 1
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()
