"""Verwaltung › Geräte & Netz › Netz & Internet: Karte «Gateway, DNS & Zeitserver» oben, die Karten darunter je nach
Art; dazu die Felder im Plan von «Netz umziehen». Bei 1300, 412 und 360 px."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import _preconfirm, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp("gateway")
    proc, base, c = _start(d, {"FESTPOS_NET": "192.168.55.0/24", "FESTPOS_VIP": "192.168.55.10",
                               "NODE_IP": "192.168.55.11"})
    yield base, d
    proc.terminate()
    proc.wait(10)


def _admin(browser, base, width, path="devices/net"):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html#" + path)
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector(".side")
    return ctx, page, errs


def _fits(page, width):
    assert page.evaluate("document.documentElement.scrollWidth") <= width


@pytest.mark.parametrize("width", [1300, 412, 360])
def test_card_on_top_and_cards_below_follow_the_mode(server, browser, width):  # noqa: F811
    base, _ = server
    ctx, page, errs = _admin(browser, base, width)
    _preconfirm(page)
    page.wait_for_selector("#gwcard")
    cards = page.evaluate("[...document.querySelectorAll('#main .card')].map(c => c.id)")
    assert cards[0] == "gwcard" and "usecard" in cards and "conncard" in cards
    page.click("#gwcard input[value=fest]")
    page.wait_for_selector("#gwcard [name=gw_gateway]")
    page.fill("#gwcard [name=gw_gateway]", "192.168.55.10")           # VIP: abgelehnt
    page.click("#gw-check")
    page.wait_for_selector("#gw-err")
    assert "Adresse für die Handhelds" in page.inner_text("#gw-err")
    page.fill("#gwcard [name=gw_gateway]", "192.168.55.254")
    page.fill("#gwcard [name=gw_dns1]", "192.168.55.253")
    page.fill("#gwcard [name=gw_ntp]", "ntp.firma.local")
    _fits(page, width)
    page.click("#gw-save")
    page.wait_for_selector("#conncard >> text=Über den festen Router 192.168.55.254")
    text = page.inner_text("#gwcard")
    assert "Fester Router 192.168.55.254" in text and "ntp.firma.local" in text
    assert page.locator(".share-sw").count() == 0                       # (b) entfällt
    page.fill("#gwcard [name=gw_dns1]", "")                             # DNS leeren: dann der Router
    page.click("#gw-save")
    _until(page, "!document.getElementById('gwcard').innerText.includes('192.168.55.253')")
    _fits(page, width)
    page.click("#gwcard input[value=ohne]")
    page.wait_for_selector("#gwcard .warnbox")
    page.click("#gw-save")
    _until(page, "!document.getElementById('usecard') && !document.getElementById('conncard')")
    assert "keines – nur FestPOS erreichbar" in page.inner_text("#gwcard")
    page.click("#gwcard input[value=auto]")
    page.click("#gw-save")
    page.wait_for_selector("#usecard .share-sw")
    _fits(page, width)
    assert not errs, errs
    ctx.close()


@pytest.mark.parametrize("width", [1300, 360])
def test_net_move_plan_has_gateway_fields(server, browser, width):  # noqa: F811
    base, _ = server
    ctx, page, errs = _admin(browser, base, width, "devices/pi")
    _preconfirm(page)
    page.evaluate("""() => fetch('/api/admin/gateway', {method: 'PUT', headers: {'Content-Type': 'application/json',
      'X-Festpos-UI': '1', Authorization: 'Bearer ' + sessionStorage.getItem('festpos.token')},
      body: JSON.stringify({mode: 'fest', gateway: '192.168.55.254', dns: [], ntp: ''})}).then((r) => r.status)""")
    page.reload()
    page.wait_for_selector("#umzugcard")
    page.click("[data-um=plan]")
    page.wait_for_selector("#um-form [name=gw_gateway]")
    assert page.input_value("#um-form [name=gw_gateway]") == "10.20.0.254"      # umgerechnet vorgeschlagen
    page.click("[data-um=check]")
    page.wait_for_selector(".um-checks")
    assert "10.20.0.254" in page.inner_text("#umzugcard") and "nicht im FestPOS-Netz" not in page.inner_text("#umzugcard")
    page.click("#um-form input[name=gw_mode][value=auto]")
    page.wait_for_selector("#um-form [name=gw_mode]")
    page.click("#um-form input[name=gw_mode][value=fest]")
    page.wait_for_selector("#um-form [name=gw_gateway]")
    page.fill("#um-form [name=gw_gateway]", "10.20.3.254")
    page.click("[data-um=check]")
    page.wait_for_selector(".um-checks")
    text = page.inner_text("#umzugcard")
    assert "10.20.3.254" in text                                       # Checkliste: Router auf diese Adresse
    page.fill("#um-form [name=gw_gateway]", "10.20.0.10")
    page.click("[data-um=check]")
    page.wait_for_selector("text=Adresse für die Handhelds oder ein Pi")
    _fits(page, width)
    page.evaluate("""() => fetch('/api/admin/gateway', {method: 'PUT', headers: {'Content-Type': 'application/json',
      'X-Festpos-UI': '1', Authorization: 'Bearer ' + sessionStorage.getItem('festpos.token')},
      body: JSON.stringify({mode: 'auto', gateway: '', dns: [], ntp: ''})})""")
    assert not errs, errs
    ctx.close()
