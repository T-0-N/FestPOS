"""T42: abgelehnte Bestellungen gehen am Handy nicht verloren – auch über 30 nicht – und stehen im Protokoll; der
Verlauf behält Bestellungen, die noch nicht gesendet oder als abgelehnt offen sind. Übersprungen ohne Playwright."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_api import AUTH  # noqa: E402
from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402
from tests.test_browser_stock import PHONE, _fill_cart, _ids, _phone_unaware, _reset  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("rejected"), target="tests.asgi_testphone:app")
    yield base, c
    proc.terminate()
    proc.wait(10)


def test_rejected_never_cut_and_logged(server, browser):  # noqa: F811
    base, c = server
    ids = _ids(c)
    _reset(c, ids, "off")
    old = [{"id": f"alt-{n}", "table": str(n), "lines": [], "error": "x", "ts": n, "test": False} for n in range(30)]
    ctx = browser.new_context(viewport={"width": 360, "height": 892}, has_touch=True, extra_http_headers=PHONE)
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('XCover 1'));"
                        f"localStorage.setItem('festpos.rejected', {json.dumps(json.dumps(old))})")
    page = ctx.new_page()
    errs = _watch(page)
    _phone_unaware(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    assert page.evaluate("rejected.length") == 30
    _fill_cart(page)
    c.put(f"/api/admin/products/{ids['Bratwurst']}/stock", auth=AUTH, json={"soldout": True})
    page.click('.sheet [data-act="send"]')
    _until(page, "rejected.length === 31")                              # die älteste bleibt (bisher: weggeschnitten)
    assert page.evaluate("rejected.some((r) => r.id === 'alt-0')")
    assert "31" in page.inner_text(".prob")
    # Verlauf: die neuesten 40, ältere nur, solange nicht gesendet oder abgelehnt-offen
    kept = page.evaluate("""() => {
      const list = Array.from({ length: 45 }, (_, i) => ({ id: 'h' + i, status: 'sent' }));
      list[42] = { id: 'h42' };                                        // noch nicht gesendet
      list[43] = { id: 'alt-5', status: 'rejected' };                  // abgelehnt, noch offen
      list[44] = { id: 'weg', status: 'rejected' };                    // abgelehnt, schon erledigt
      return trimHist(list).map((h) => h.id).slice(40); }""")
    assert kept == ["h42", "alt-5"]
    audit = json.dumps(c.get("/api/admin/audit", auth=AUTH).json())
    assert "order_rejected" in audit and "Bratwurst" in audit
    assert not [e for e in errs if "422" not in e], errs
    ctx.close()
