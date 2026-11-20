"""PIN-Sperre klar anders als die Tischwahl (Rückmeldung, Vorschlag A): orange Fläche, Balken mit Schloss –
hell und dunkel, passt bei 360 px."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_api import AUTH  # noqa: E402
from tests.test_browser_kasse import PHONE  # noqa: E402
from tests.test_browser_p4 import _start, browser  # noqa: E402,F401  (browser = Fixture)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("lock"), target="tests.asgi_testphone:app")
    c.put("/api/admin/settings", auth=AUTH, json={"pin_enabled": True, "pin": "2580"})
    yield base
    proc.terminate()
    proc.wait(10)


@pytest.mark.parametrize("scheme,bg", [("light", "rgb(255, 226, 191)"), ("dark", "rgb(90, 51, 0)")])
def test_lock_screen_looks_different(server, browser, scheme, bg):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 360, "height": 780}, color_scheme=scheme, extra_http_headers=PHONE)
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('XCover 1'))")
    page = ctx.new_page()
    page.goto(server + "/index.html")
    page.wait_for_selector("#lock .lock-top")
    assert page.evaluate("getComputedStyle(document.querySelector('#lock')).backgroundColor") == bg
    assert page.evaluate("getComputedStyle(document.querySelector('#lock .lock-top')).backgroundColor") == "rgb(168, 67, 0)"
    assert page.locator("#lock .lock-ico svg").count() == 1 and "Gesperrt" in page.inner_text("#lock .lock-top")
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    page.click('#lock [data-act="pin"][data-d="1"]')
    assert page.inner_text("#pinout") == "●○○○"                      # ● getippt, ○ fehlt noch (4-stellige PIN)
    ctx.close()
