"""T45 im Browser: Verwaltung › Tagesabschluss – offene Kartenzahlung sperrt (Warnkasten, «Abbrechen»), Zeile
«Rückerstattungen früherer Tage»; 1300 und 360 px. Übersprungen ohne Playwright/Chromium."""
from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("closingt45")
    proc, base, c = _start(tmp)
    db = sqlite3.connect(tmp / "b.db")
    db.execute("INSERT INTO orders(client_uuid, table_label, total_cents, created_at, payment) "
               "VALUES ('o-1', '7', 1300, '2026-11-10T19:00:00', 'payrexx')")
    db.execute("INSERT INTO payrexx_payments(key, order_id, part, amount_cents, status, device, created_at) "
               "VALUES ('w-1', 1, 'all', 1300, 'waiting', 'XCover 1', '2026-11-10T19:01:00')")
    db.execute("INSERT INTO corrections(key, kind, order_id, amount_cents, goods_cents, tip_cents, vat, why, created_at) "
               "VALUES ('k-1', 'refund', 99, 1500, 1300, 200, '[]', 'admin:test', '2026-11-10T19:02:00')")
    db.commit()
    db.close()
    yield base
    proc.terminate()
    proc.wait(10)


@pytest.mark.parametrize("width", [1300, 360])
def test_waiting_payment_and_correction_on_closing_page(server, browser, width):  # noqa: F811
    base = server
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.on("dialog", lambda d: d.accept())
    page.goto(base + "/admin.html#closing")
    page.fill("#user", "admin")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector("#main h2")
    _until(page, "!!document.querySelector('#corrline')", 8000)
    assert "früherer Tage" in page.inner_text("#corrline") and "15.00" in page.inner_text("#corrline")
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0
    if width == 1300:
        page.wait_for_selector("#prxwaiting [data-prxcancel]")
        assert "XCover 1" in page.inner_text("#prxwaiting")
    else:                                                         # zweiter Durchgang: abbrechen
        page.wait_for_selector("#prxwaiting [data-prxcancel]")
        page.click("#prxwaiting [data-prxcancel]")
        _until(page, "!document.querySelector('#prxwaiting')", 8000)
    assert [e for e in errs if "Failed to load resource" not in e] == []
    ctx.close()
