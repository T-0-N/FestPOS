"""T46 im Browser: Verwaltung › Tagesabschluss – Kartenzahlung nach dem Abschluss bestätigt, Bestellung bar: Warnkasten
mit «Gast hat bar bezahlt – zurückerstattet» und «Gast hat nicht bar bezahlt – nachbuchen»; 1300 und 360 px.
Übersprungen ohne Playwright/Chromium."""
from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("closingt46")
    proc, base, c = _start(tmp)
    db = sqlite3.connect(tmp / "b.db")
    for n, table in ((1, "7"), (2, "9")):
        db.execute("INSERT INTO orders(client_uuid, table_label, total_cents, created_at, payment, closing_id) "
                   "VALUES (?, ?, 1300, '2026-11-10T19:00:00', 'cash', 1)", (f"o-{n}", table))
        db.execute("INSERT INTO payrexx_payments(key, order_id, part, amount_cents, status, device, created_at, "
                   "paid_at, late) VALUES (?, ?, 'all', 1300, 'confirmed', 'XCover 1', '2026-11-10T19:01:00', "
                   "'2026-11-10T23:10:00', 'open')", (f"l-{n}", n))
    db.commit()
    db.close()
    yield base
    proc.terminate()
    proc.wait(10)


@pytest.mark.parametrize("width", [1300, 360])
def test_late_payment_decision_on_closing_page(server, browser, width):  # noqa: F811
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
    page.wait_for_selector("#prxlate [data-late]")
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0
    table, action = ("7", "refund") if width == 1300 else ("9", "book")   # jeder Durchgang seine eigene Zahlung
    row = page.locator("#prxlate li", has_text=f"Tisch {table} ")
    assert row.count() == 1 and "Abschluss Nr. 1" in row.inner_text()
    row.locator(f"[data-late={action}]").click()
    _until(page, "![...document.querySelectorAll('#prxlate li')].some((li) => li.textContent.includes('Tisch %s '))"
           % table, 8000)
    if action == "book":                                          # nachgebucht: Zeile «nachträglich bestätigt»
        _until(page, "!!document.querySelector('#lateline')", 8000)
        assert "13.00" in page.inner_text("#lateline")
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0
    assert [e for e in errs if "Failed to load resource" not in e] == []
    ctx.close()
