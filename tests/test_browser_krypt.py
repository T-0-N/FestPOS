"""Verwaltung › Geräte & Netz › Pi: Karte «Daten verschlüsselt» und Dialog «Umstellen …» (T-0-N/FestPOS#3)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp("krypt")
    (d / "stand.json").write_text(json.dumps({"state": "aus", "chip": "empty", "tool": True, "offen": False}))
    proc, base, c = _start(d, {"FESTPOS_KRYPT_STAND": str(d / "stand.json")})
    yield base
    proc.terminate()
    proc.wait(10)


@pytest.mark.parametrize("width", [1300, 412, 360])
def test_card_and_switch_dialog(server, browser, width):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(server + "/admin.html#devices/pi")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector("#kryptcard table")
    card = page.inner_text("#kryptcard")
    assert "Daten verschlüsselt (Schlüssel im Chip)" in card and "noch nicht" in card
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    page.click("[data-kr-on]")
    page.wait_for_selector("dialog .kr-key")
    key = page.inner_text("dialog .kr-key").replace("\n", "-")
    assert len(key) == 39 and key.count("-") == 7, key
    go = page.locator('dialog [data-dlg="save"]')
    assert go.is_disabled() and "Jetzt umstellen" in go.inner_text()
    page.check('dialog [name="printed"]')
    assert go.is_enabled()
    box = page.locator("dialog").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    page.click('dialog [data-dlg="cancel"]')
    page.goto(server + "/admin.html#overview")
    page.reload()
    page.wait_for_selector(".todo .krypt-off")
    assert "Daten von Pi" in page.inner_text(".todo") and "noch nicht verschlüsselt" in page.inner_text(".todo")
    assert not errs, errs
    ctx.close()
