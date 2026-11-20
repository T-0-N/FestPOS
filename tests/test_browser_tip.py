"""Trinkgeld an der Kasse (Entscheid: , Mockup tip-kasse): «+ Trinkgeld» unter dem Ziffernblock in Rückgeld,
Teilen und Einzeln; Blatt mit «Aufrunden», «Stimmt so», +1/+2/+5 und eigener Eingabe, danach «inkl. Trinkgeld … ✓» und
das Rückgeld mit Trinkgeld. Der Pi bekommt die Summe pro Bestellung (nachgereicht). Auch 360 px breit.
Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import time

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_api import AUTH  # noqa: E402
from tests.test_browser_kasse import _out, _pay_view  # noqa: E402
from tests.test_browser_p4 import _start, browser  # noqa: E402,F401  (browser = Fixture)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("tip"), target="tests.asgi_testphone:app")
    assert c.put("/api/admin/settings", auth=AUTH, json={"tips_enabled": True}).status_code == 200
    yield base, c
    proc.terminate()
    proc.wait(10)


def _tips(c) -> int:
    return c.get("/api/admin/closings/open", auth=AUTH).json()["summary"]["tips"]["total"]


def _wait_tips(c, want: int) -> None:
    end = time.time() + 10
    while time.time() < end:
        if _tips(c) == want:
            return
        time.sleep(0.2)
    assert _tips(c) == want


def _txt(page, sel) -> str:
    return " ".join(page.inner_text(sel).split())


def test_tip_in_change(server, browser):  # noqa: F811
    base, c = server
    before = _tips(c)
    ctx, page, errs = _pay_view(browser, base)                         # 33.00
    page.click('#paypanel [data-act="key"][data-k="5"]')
    page.click('#paypanel [data-act="key"][data-k="0"]')
    assert _out(page) == ("good", "Rückgeld 17.00")
    pad = page.evaluate("document.querySelector('#paypanel .pad').getBoundingClientRect().bottom")
    btn = page.locator('#paypanel .tip-btn')
    assert _txt(page, "#paypanel .tip-btn") == "+ Trinkgeld"
    assert btn.bounding_box()["y"] >= pad                              # unter dem Ziffernblock
    btn.click()
    page.wait_for_selector("#tipsheet")
    chips = [" ".join(x.split()) for x in page.locator("#tipsheet .tipgrid .chip").all_inner_texts()]
    assert chips == ["Aufrunden auf 34.00 · 1.00", "Stimmt so (50.00) · 17.00", "+1", "+2", "+5"]
    assert page.locator('#tipsheet [data-act="tip-ok"]').is_disabled()
    page.click('#tipsheet [data-act="tip-set"] >> nth=0')               # aufrunden: 1.00
    assert _txt(page, "#tipsheet .c-out") == "Rückgeld danach 16.00"
    page.click('#tipsheet [data-act="tip-plus"][data-v="200"]')         # +2
    assert page.inner_text("#tipsheet .calc output") == "3"
    assert _txt(page, '#tipsheet [data-act="tip-ok"]') == "3.00 Trinkgeld übernehmen"
    assert page.locator("#tipsheet .go").count() == 1                   # genau ein grüner Knopf
    page.click('#tipsheet [data-act="tip-ok"]')
    page.wait_for_selector("#tipsheet", state="detached")
    assert _out(page) == ("good", "Rückgeld 14.00")
    assert _txt(page, "#paypanel .tipline") == "inkl. Trinkgeld 3.00 ✓ · ändern"
    assert page.locator("#paypanel .tip-btn").count() == 0
    assert "Trinkgeld 3.00" in _txt(page, "#bon")
    _wait_tips(c, before + 300)
    # ändern: eigene Eingabe, dann ganz weg
    page.click("#paypanel .tipline")
    page.wait_for_selector("#tipsheet")
    assert page.inner_text("#tipsheet .calc output") == "3"
    page.click('#tipsheet [data-act="key"][data-k="del"]')
    page.click('#tipsheet [data-act="key"][data-k="2"]')
    page.click('#tipsheet [data-act="key"][data-k="."]')
    page.click('#tipsheet [data-act="key"][data-k="5"]')
    assert _txt(page, "#tipsheet .c-out") == "Rückgeld danach 14.50"
    page.click('#tipsheet [data-act="tip-none"]')
    assert _out(page) == ("good", "Rückgeld 17.00") and page.locator("#paypanel .tipline").count() == 0
    _wait_tips(c, before)
    page.set_viewport_size({"width": 360, "height": 892})
    page.click("#paypanel .tip-btn")
    page.wait_for_selector("#tipsheet")
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    assert page.evaluate("document.querySelector('.sheet').scrollWidth <= document.querySelector('.sheet').clientWidth")
    page.click('#tipsheet [data-act="tip-none"]')
    assert not errs, errs
    ctx.close()


def test_tip_in_split_and_per_person(server, browser):  # noqa: F811
    """Trinkgeld beim Teilen pro Person («s<Person>»), bei Einzeln pro Person; fällt mit «rückgängig» weg."""
    base, c = server
    before = _tips(c)
    ctx, page, errs = _pay_view(browser, base)                         # 33.00 -> 2 × 16.50
    page.click('[data-act="mode"][data-m="split"]')
    page.click('#payfoot [data-act="split-go"]')                       # Person 1
    page.click('#paypanel [data-act="split-given"] >> nth=1')           # gibt 20
    assert _out(page) == ("good", "Rückgeld 3.50")
    page.click("#paypanel .tip-btn")
    page.wait_for_selector("#tipsheet")
    assert _txt(page, "#tipsheet .muted") == "Total 16.50 · gegeben 20.00"
    page.click('#tipsheet [data-act="tip-set"] >> nth=0')               # aufrunden auf 17.00
    page.click('#tipsheet [data-act="tip-ok"]')
    assert _out(page) == ("good", "Rückgeld 3.00") and _txt(page, "#paypanel .tipline") == "inkl. Trinkgeld 0.50 ✓ · ändern"
    page.click('#payfoot [data-act="split-paid"]')
    page.click('#payfoot [data-act="split-go"]')                       # Person 2: ohne Trinkgeld
    assert page.locator("#paypanel .tipline").count() == 0 and page.inner_text("#paypanel .calc output") == ""
    page.click("#paypanel .tip-btn")
    page.click('#tipsheet [data-act="tip-plus"][data-v="100"]')
    page.click('#tipsheet [data-act="tip-ok"]')
    page.click('#paypanel [data-act="split-given"] >> nth=1')
    assert _out(page) == ("good", "Rückgeld 2.50") and "1.00" in _txt(page, "#paypanel .tipline")
    page.click('#payfoot [data-act="split-paid"]')
    _wait_tips(c, before + 150)
    page.click(".pc.done >> nth=0")                                    # Person 1 rückgängig: ihr Trinkgeld fällt weg
    page.click('.sheet [data-act="part-undo"]')
    _wait_tips(c, before + 100)
    page.click(".pc.done >> nth=0")                                    # Person 2 auch: wieder wie vorher
    page.click('.sheet [data-act="part-undo"]')
    _wait_tips(c, before)
    # Einzeln: Trinkgeld pro Person, fällt mit «rückgängig» weg
    page.click('[data-act="mode"][data-m="part"]')
    page.click("#pickpanel .pi >> nth=0")
    page.click('#payfoot [data-act="ez-next"]')
    page.click("#partpay .tip-btn")
    page.click('#tipsheet [data-act="tip-plus"][data-v="500"]')
    page.click('#tipsheet [data-act="tip-ok"]')
    assert _txt(page, "#partpay .tipline") == "inkl. Trinkgeld 5.00 ✓ · ändern"
    page.click('#partpay [data-act="part-given"] >> nth=0')            # Passend = Betrag + Trinkgeld
    assert _out(page) == ("good", "Rückgeld 0.00")
    page.click('#payfoot [data-act="part-pay"]')
    _wait_tips(c, before + 500)
    page.click(".pc.done >> nth=0")
    page.click('.sheet [data-act="part-undo"]')
    _wait_tips(c, before)
    assert not errs, errs
    ctx.close()



def test_off_shows_nothing(server, browser):  # noqa: F811
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"tips_enabled": False})
    try:
        ctx, page, errs = _pay_view(browser, base)
        assert page.locator("#paypanel .tip-btn").count() == 0
        page.click('[data-act="mode"][data-m="split"]')
        assert page.locator("#paypanel .tip-btn").count() == 0
        assert not errs, errs
        ctx.close()
    finally:
        c.put("/api/admin/settings", auth=AUTH, json={"tips_enabled": True})
