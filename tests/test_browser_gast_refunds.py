"""T37: Verwaltung › Kasse & App › Kasse › QR-Bestellung › Online-Bestellungen: unklare Rückerstattung mit
«Nochmals senden» und «Ist erledigt», auch bei 360 px. Im Browser; übersprungen ohne Playwright/Chromium."""
from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gastrf")
    proc, base, c = _start(tmp)
    db = sqlite3.connect(tmp / "b.db")
    db.execute("INSERT INTO gast_orders(token, key, table_label, lines, amount_cents, status, transaction_id, "
               "refund_open, created_at, paid_at) VALUES ('tok-1','key-1','7','[]',1250,'ordered','7001',1250,"
               "'2026-11-10T19:00:00','2026-11-10T19:01:00')")
    db.execute("INSERT INTO gast_refunds(key, gast_id, amount_cents, why, status, error, created_at) "
               "VALUES ('storno:x', 1, 1250, 'storno:admin', 'unclear', 'unclear', '2026-11-10T19:05:00')")
    db.commit()
    yield base, tmp
    db.close()
    proc.terminate()
    proc.wait(10)


@pytest.mark.parametrize("width", [1300, 360])
def test_unclear_refund_buttons(server, browser, width):  # noqa: F811
    base, tmp = server
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.on("dialog", lambda d: d.accept())
    page.goto(base + "/admin.html#pos")
    page.fill("#user", "admin")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector(".side")
    page.click('#main .tabs [data-tab="kasse"]')
    page.wait_for_selector('details[data-card="gast"]')
    page.eval_on_selector('details[data-card="gast"]', "d => d.open = true")
    page.click("#gast-list")
    page.wait_for_selector("#gast-orders [data-rdone]")
    txt = page.inner_text("#gast-orders")
    assert "unklar" in txt and "Nochmals senden" in txt and "Ist erledigt" in txt
    assert not page.locator("#gast-orders [data-refund]").count()          # alles reserviert, nichts gescheitert
    over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert over <= 0, over
    if width == 360:
        page.click("#gast-orders [data-rdone]")
        _until(page, "!document.querySelector('#gast-orders [data-rdone]')")
        db = sqlite3.connect(tmp / "b.db")
        assert db.execute("SELECT status FROM gast_refunds").fetchone()[0] == "done"
        assert db.execute("SELECT refunded_cents, refund_open, status FROM gast_orders").fetchone() == (1250, 0, "refunded")
        db.close()
    assert [e for e in errs if "Failed to load resource" not in e] == []
    ctx.close()
