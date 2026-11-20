"""T44 im Browser: eine unklare NFC-Zahlung (Erfolg ohne Betrag) erneut öffnen – «FestPOS Tap» wird nicht nochmals
gestartet (fully.startIntent gezählt), kein «Nochmals an das Handheld halten», «Payrexx zeigte bezahlt» bleibt."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_kasse import _pay_view  # noqa: E402
from tests.test_browser_payrexx import FULLY, _launched, server  # noqa: E402,F401  (server = Fixture)
from tests.test_browser_p4 import browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402
from tests.test_payrexx import _result  # noqa: E402


@pytest.mark.parametrize("width", [412, 360])
def test_reopen_unclear_never_starts_again(server, browser, width):  # noqa: F811
    base, c = server
    ctx, page, errs = _pay_view(browser, base)
    page.evaluate(FULLY)
    page.set_viewport_size({"width": width, "height": 892})
    page.wait_for_selector('#payrow [data-act="payrexx-all"]')
    page.click('#payrow [data-act="payrexx-all"]')
    x = _launched(page)
    assert page.evaluate("window.__taps.length") == 1
    r = c.post("/api/tap/result", json={"id": int(x["id"]), "token": x["token"], "ok": True,
                                        "result": _result("unknown", x["ref"])})             # Erfolg ohne Betrag
    assert r.json()["error"] == "unclear"
    _until(page, "document.querySelector('#tapsheet')?.textContent.includes('ohne Betrag')", 8000)
    assert page.locator('#tapsheet [data-act="payrexx-again"]').count() == 0
    page.evaluate("stopTap(); TAP = null; closeSheet()")                                     # Bedienung schliesst …
    page.click('#payrow [data-act="payrexx-all"]')                                          # … und öffnet nochmals
    page.wait_for_selector("#tapsheet")
    _until(page, "document.querySelector('#tapsheet')?.textContent.includes('ohne Betrag')", 8000)
    page.wait_for_timeout(500)
    assert page.evaluate("window.__taps.length") == 1                                       # nicht nochmals gestartet
    assert page.locator('#tapsheet [data-act="payrexx-again"]').count() == 0
    assert page.locator('#tapsheet [data-act="payrexx-manual"]').count() == 1
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()
