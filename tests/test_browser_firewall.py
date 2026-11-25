"""Verwaltung › Geräte & Netz › Netz & Internet: Firewall ins Internet mit drei Modi (T-0-N/FestPOS#10)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import _confirm, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp("firewall")
    stand = d / "stand.json"
    stand.write_text(json.dumps({"mode": "strict", "at": 1, "unknown": 17, "error": "", "addrs": 40,
                                 "seen": [{"ip": "142.250.203.99", "proto": "tcp", "port": 443, "left": 86000}]}))
    proc, base, c = _start(d, {"FESTPOS_FW_STAND": str(stand), "FESTPOS_FW_WISH": str(d / "fw" / "modus.json")})
    yield base, d
    proc.terminate()
    proc.wait(10)


def _open(browser, base, width):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html#devices/net")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector("#fw-card")
    return ctx, page, errs


@pytest.mark.parametrize("width", [1300, 412, 360])
def test_card_and_switch_mode(server, browser, width):  # noqa: F811
    base, d = server
    ctx, page, errs = _open(browser, base, width)
    card = page.inner_text("#fw-card")
    assert "Nur FestPOS-Dienste" in card and "Beobachten" in card and "Offen" in card
    assert "142.250.203.99:443 (tcp)" in card and "17" in card
    assert page.is_checked('#fw-modes input[value="strict"]')
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    if width == 1300:
        page.check('#fw-modes input[value="watch"]')
        _confirm(page)                                                   # kritisch: Passwort nochmals
        _until(page, "!!document.querySelector('#fw-card .warnbox')")
        assert "danach wieder" in page.inner_text("#fw-card .warnbox")
        assert json.loads((d / "fw" / "modus.json").read_text())["mode"] == "watch"
        page.check('#fw-modes input[value="strict"]')                   # 5 Minuten bestätigt: ohne Fenster
        _until(page, "!document.querySelector('#fw-card .warnbox')")
        assert json.loads((d / "fw" / "modus.json").read_text())["mode"] == "strict"
    assert not errs, errs
    ctx.close()
