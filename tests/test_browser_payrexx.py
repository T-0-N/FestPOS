"""T13 im Browser (XCover 412 und 360 px): Karte/TWINT mit NFC – die Bestell-App startet «FestPOS Tap» (hier
fully.startIntent als Attrappe), das Ergebnis der Payrexx-App kommt wie von der Begleit-App über /api/tap/result,
«Bezahlt ✓» und Zahlart in der Zeile; Einzeln: Person zahlt mit Karte und gilt als bezahlt; Bestätigen von Hand."""
from __future__ import annotations

import json
from urllib.parse import unquote

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_api import AUTH  # noqa: E402
from tests.test_browser_kasse import _pay_view  # noqa: E402
from tests.test_browser_p4 import _start, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402
from tests.test_payrexx import _result  # noqa: E402

FULLY = "window.__taps = []; window.fully = { startIntent: (u) => window.__taps.push(u) };"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("prx"), target="tests.asgi_testphone:app")
    r = c.put("/api/admin/payrexx", auth=AUTH, json={"enabled": True})
    assert r.status_code == 200, r.text
    yield base, c
    proc.terminate()
    proc.wait(10)


def _launched(page) -> dict:
    _until(page, "window.__taps && window.__taps.length > 0", 8000)
    url = page.evaluate("window.__taps.at(-1)")
    return {k[2:]: unquote(v) for k, v in (p.split("=", 1) for p in url.split(";") if p.startswith("S."))}


def _report(c, x, ok=True):
    r = c.post("/api/tap/result", json={"id": int(x["id"]), "token": x["token"], "ok": ok,
                                        "result": _result(x["amount"], x["ref"]) if ok else None,
                                        "message": None if ok else "cancelled"})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("width", [412, 360])
def test_whole_order_by_nfc(server, browser, width):  # noqa: F811
    base, c = server
    ctx, page, errs = _pay_view(browser, base)
    page.evaluate(FULLY)
    page.set_viewport_size({"width": width, "height": 892})
    page.wait_for_selector('#payrow [data-act="payrexx-all"]')
    page.click('#payrow [data-act="payrexx-all"]')
    x = _launched(page)
    assert x["amount"] == "33.00"
    page.wait_for_selector("#tapsheet")
    assert "33.00" in page.inner_text("#tapsheet h2")
    assert page.locator('#tapsheet [data-act="payrexx-again"]').count() == 1          # ein grüner Knopf: nochmals
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert _report(c, x)["status"] == "confirmed"                    # der Gast hat bezahlt
    page.wait_for_selector("#tapsheet .big-out.good", timeout=10000)
    _until(page, "!document.querySelector('#tapsheet') && !!document.querySelector('#payrow .qrdone')", 8000)
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()


def test_person_pays_by_nfc(server, browser):  # noqa: F811
    base, c = server
    ctx, page, errs = _pay_view(browser, base)
    page.evaluate(FULLY)
    page.click('[data-act="mode"][data-m="part"]')
    page.click("#pickpanel .pi >> nth=1")                           # Mineral 4.00
    page.click('[data-act="ez-next"]')
    page.click('[data-act="payrexx-part"]')
    x = _launched(page)
    assert x["amount"] == "4.00"
    _report(c, x)
    _until(page, "!document.querySelector('#tapsheet') && document.querySelectorAll('.pc.done').length === 1", 12000)
    assert "Person 2" in page.inner_text("#pickpanel")
    ctx.close()


def test_failed_then_manual(server, browser):  # noqa: F811
    base, c = server
    ctx, page, errs = _pay_view(browser, base)
    page.evaluate(FULLY)
    page.click('#payrow [data-act="payrexx-all"]')
    x = _launched(page)
    assert _report(c, x, ok=False)["status"] == "failed"            # Ergebnis «abgebrochen» aus der Payrexx-App
    page.wait_for_selector("#tapsheet .err", timeout=8000)
    page.evaluate("window.__taps = []")
    page.click('#tapsheet [data-act="payrexx-again"]')               # nochmals starten: gleiche Zahlung
    assert _launched(page)["id"] == x["id"]
    page.evaluate("TAP.started = Date.now() - 20000; tapShow(TAP.last)")   # 15 s vorbei: «von Hand» erscheint
    page.on("dialog", lambda d: d.accept())
    page.click('#tapsheet [data-act="payrexx-manual"]')
    _until(page, "!document.querySelector('#tapsheet') && !!document.querySelector('#payrow .qrdone')", 8000)
    audit = json.dumps(c.get("/api/admin/audit", auth=AUTH).json())
    assert "manual:XCover 1" in audit
    ctx.close()
