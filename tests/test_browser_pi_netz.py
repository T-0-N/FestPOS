"""T33: Geräte & Netz › Pi auf einer Seite – Pi mit Uhrzeit als Liste, Pi antippen öffnet seine Unterseite mit
Zustand, Bildschirm-Einstellungen und neu starten / herunterfahren. Wird übersprungen, wenn Playwright fehlt."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("pinetz"))
    yield base, c
    proc.terminate()
    proc.wait(10)


@pytest.mark.parametrize("width", [1300, 360])
def test_pi_list_and_pi_page(server, browser, width):  # noqa: F811
    base, _ = server
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html#devices/pi")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector("#systemcard .pi-row")
    assert page.locator('#main .tabs [aria-selected="true"]').get_attribute("data-tab") == "pi"   # T34: Geräte & Netz › Pi
    assert page.locator("#dispcard").count() == 0                           # Bildschirm nur in der Unterseite
    assert page.locator("#timecard [data-time]").count() >= 1 and page.locator("#netform").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    page.locator("#systemcard .pi-row").first.click()
    page.wait_for_selector("#dispcard form.disp")
    main = page.inner_text("#main")
    assert "Zustand" in main and "Bildschirm am Pi" in main and page.locator("#dispcard .disp-h").count() == 0
    assert page.locator('#main [data-power="reboot"]').count() == 1 and page.locator('#main [data-power="poweroff"]').count() == 1
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert page.evaluate("[...document.querySelectorAll('#dispcard select')].every((e) => e.getBoundingClientRect().right <= document.querySelector('#dispcard').getBoundingClientRect().right)")
    page.click("[data-pi-back]")
    page.wait_for_selector("#systemcard .pi-row")
    page.locator("#systemcard .pi-row").first.click()                        # Menü «Geräte & Netz»: wieder die Liste
    page.wait_for_selector("#dispcard")
    page.evaluate("document.querySelector('.side [data-page=\"devices\"]').click()")
    page.wait_for_selector("#systemcard .pi-row")
    assert not errs, errs
    ctx.close()


@pytest.fixture(scope="module")
def server_harden(tmp_path_factory):
    """Pi, dessen Bootloader noch von USB startet: festpos-haertung hat die Einstellung geschrieben (wirkt nach dem
    Neustart)."""
    tmp = tmp_path_factory.mktemp("haertung")
    state = tmp / "haertung.json"
    state.write_text('{"boot": "pending", "boot_order": "0xf461", "bt_off": true, "console_off": true, "at": 1}\n')
    proc, base, c = _start(tmp, extra_env={"FESTPOS_HAERTUNG_STATE": str(state)})
    yield base, c, state
    proc.terminate()
    proc.wait(10)


@pytest.mark.parametrize("width", [1300, 360])
def test_boot_only_from_sd_card_is_shown(server_harden, browser, width):  # noqa: F811
    base, c, state = server_harden
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html#overview")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector("#main h2")
    _until(page, "document.body.innerText.includes('startet noch von USB')")       # Übersicht › Zu erledigen
    page.goto(base + "/admin.html#devices/pi")
    page.reload()
    page.wait_for_selector("#systemcard .pi-row")
    assert "Start auch von USB" in page.inner_text("#systemcard .pi-row") and page.locator("#harden-list").count() == 1
    page.locator("#systemcard .pi-row").first.click()
    page.wait_for_selector("#harden-box")
    main = page.inner_text("#main")
    assert "wirkt nach dem nächsten Neustart" in main and "Anmeldung gesperrt" in main and "Bluetooth" in main
    assert page.locator('#harden-box [data-power="reboot"]').count() == 1
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert not errs, errs
    ctx.close()
