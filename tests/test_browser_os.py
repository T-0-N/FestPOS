"""Wartung › Update › Karte «Betriebssystem» und Hinweis in der Übersicht nach 6 Monaten (T-0-N/FestPOS#5)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp("os")
    (d / "stand.json").write_text(json.dumps({"date": "2025-01-15", "result": "repaired", "open": 3}))
    (d / "reboot").write_text("")
    proc, base, c = _start(d, {"FESTPOS_OS_STAND": str(d / "stand.json"), "FESTPOS_REBOOT_FLAG": str(d / "reboot")})
    yield base
    proc.terminate()
    proc.wait(10)


def _admin(browser, base, width, where):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html#" + where)
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    return ctx, page, errs


@pytest.mark.parametrize("width", [1300, 412, 360])
def test_os_card_and_overview_hint(server, browser, width):  # noqa: F811
    ctx, page, errs = _admin(browser, server, width, "maint/update")
    page.wait_for_selector("#os-card table")
    card = page.inner_text("#os-card")
    assert "15.01.2025" in card and "älter als 6 Monate" in card and "abgebrochen, repariert" in card
    assert "Neustart nötig" in card and "Mit Internet holen" in card
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    page.goto(server + "/admin.html#overview")
    page.reload()
    page.wait_for_selector(".todo .os-old")
    assert "Betriebssystem älter als 6 Monate (A)" in page.inner_text(".todo")
    assert not errs, errs
    ctx.close()
