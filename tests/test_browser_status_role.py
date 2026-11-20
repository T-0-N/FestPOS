"""Rolle «Nur Statusanzeige»: ein Handheld mit dieser Rolle öffnet statt der Bestell-App die
Statusanzeige, darf nicht bestellen und kehrt in die Bestell-App zurück, sobald die Rolle wieder eine andere ist.
Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_api import AUTH  # noqa: E402
from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402

PHONE = {"X-Test-Cert": "XCover 1", "X-Test-Client": "192.168.55.101"}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("statusrole"), target="tests.asgi_testphone:app", phone_port=True)
    yield base, c
    proc.terminate()
    proc.wait(10)


def test_status_only_role_redirects_and_cannot_order(server, browser):  # noqa: F811
    base, c = server
    assert c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "status"}).status_code == 200
    boot = c.get("/api/bootstrap", headers=PHONE).json()
    assert boot["status_only"] is True
    pid = boot["products"][0]["id"]
    r = c.post("/api/orders", headers=PHONE, json={"client_uuid": "statusrole-0001", "table": "5", "device": "XCover 1",
                                                     "lines": [{"product_id": pid, "qty": 1}]})
    assert r.status_code == 403 and "Nur Statusanzeige" in r.json()["detail"]
    ctx = browser.new_context(viewport={"width": 412, "height": 892}, extra_http_headers=PHONE)
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('XCover 1'))")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    page.wait_for_url("**/status.html", timeout=15000)                  # gleich weiter zur Statusanzeige
    _until(page, "!!document.getElementById('event')")
    assert page.locator("#j-app").is_hidden()                              # kein Weg zurück in die Bestell-App
    # Rolle wieder «Bedienung»: die Statusanzeige schickt das Handheld zurück
    c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "service"})
    page.wait_for_url("**/", timeout=15000)
    page.wait_for_selector('[data-act="digit"]')
    assert c.get("/api/bootstrap", headers=PHONE).json()["status_only"] is False
    assert not [e for e in errs if "Failed to load resource" not in e and "403" not in e], errs
    ctx.close()
