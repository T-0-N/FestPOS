"""Im echten Browser: Verwaltung › Wartung › Update › «Aus dem Internet» – einrichten (WLAN), neuen Release suchen,
herunterladen und prüfen, danach «Auf alle Pi verteilen». GitHub und der root-Helfer sind Attrappen
(tests/test_update_net.py). Wird übersprungen ohne Chromium."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api")

from app import release  # noqa: E402
from tests.release_util import build_bundle, keypair  # noqa: E402
from tests.test_api import AUTH  # noqa: E402
from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402
from tests.test_update_net import FakeGitHub, FakeHelper  # noqa: E402

NEW = "99.0.0"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("netupdate")
    d = {k: tmp / k for k in ("pakete", "req", "reqneu", "status", "alt")}
    for p in d.values():
        p.mkdir()
    pk, line = keypair("Vereins-Update")
    (d["status"] / "keys.json").write_text(json.dumps({"keys": [release.parse_key_line(line).public()], "problems": []}))
    (tmp / "sysnet" / "wlan0" / "wireless").mkdir(parents=True)
    z = build_bundle(tmp / "festpos.zip", NEW)
    gh = FakeGitHub()
    gh.publish(NEW, z.read_bytes(), release.sign(z, pk), name="Herbstfest 2026")
    helper = FakeHelper(d)
    proc, base, c = _start(tmp, extra_env={
        "FESTPOS_UPDATE_DIR": str(d["pakete"]), "FESTPOS_UPDATE_REQ": str(d["req"]), "FESTPOS_UPDATE_REQ_NEU": str(d["reqneu"]),
        "FESTPOS_UPDATE_STATUS": str(d["status"]), "FESTPOS_ALT": str(d["alt"]), "FESTPOS_UPDATE_SETTLE": "0",
        "FESTPOS_GITHUB_API": gh.base, "FESTPOS_SYS_NET": str(tmp / "sysnet"), "FESTPOS_NET_WAIT": "10"})
    yield {"base": base, "c": c, "gh": gh, "helper": helper}
    helper.stop = True
    gh.srv.shutdown()
    proc.terminate()
    proc.wait(10)


def _admin(browser, base, width=1300, where="maint/update"):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": 1000})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + f"/admin.html#{where}")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector("#net-form")
    return ctx, page, errs


def test_search_download_and_offer_to_all_pis(env, browser):  # noqa: F811
    # T5: die Verbindung (WLAN des Pi) unter «Netz & Internet», Quelle und Suchen unter Wartung › Update
    ctx, page, errs = _admin(browser, env["base"], where="devices/net")
    assert page.locator('.net-way[data-via="wlan"]').is_hidden()
    page.check('#net-form input[name="via"][value="wlan"]')
    assert page.locator('.net-way[data-via="wlan"]').is_visible() and page.locator('.net-way[data-via="lan"]').is_hidden()
    page.fill('#net-form input[name="ssid"]', "Fest-Hotspot")
    page.fill('#net-form input[name="psk"]', "geheim-wlan-123")
    page.click("#net-form button.pri")
    _until(page, "!!document.querySelector('.toast:not(.bad)')")
    assert page.input_value('#net-form input[name="psk"]') == ""
    assert "gespeichert" in (page.get_attribute('#net-form input[name="psk"]', "placeholder") or "")
    page.click('.side button[data-page="maint"]')
    page.wait_for_selector("#net-check")
    assert "über WLAN" in page.inner_text("#net-form")
    page.fill('#net-form input[name="repo"]', "https://github.com/o/r")
    page.click("#net-check")                                      # speichert zuerst, dann suchen
    _until(page, "document.getElementById('net-state').textContent.includes('verfügbar')", 15000)
    st = page.inner_text("#net-state")
    assert f"Version {NEW} verfügbar" in st and "Herbstfest 2026" in st and "über WLAN" in st
    assert page.input_value('#net-form input[name="repo"]') == "o/r"            # so gespeichert
    assert env["helper"].reqs[0]["ssid"] == "Fest-Hotspot"
    page.on("dialog", lambda d: d.accept())
    page.click("#net-download")
    _until(page, "document.getElementById('net-state').textContent.includes('heruntergeladen')", 20000)
    _until(page, "!!document.getElementById('upd-start')", 10000)
    assert "Aus dem Internet (o/r " + NEW + ")" in page.inner_text("#upd-upload")
    assert env["helper"].actions() == ["net_up", "net_down", "net_up", "net_down"]
    assert not errs, errs
    ctx.close()


def test_updates_switch_off_blocks_search(env, browser):  # noqa: F811
    ctx, page, errs = _admin(browser, env["base"], where="devices/net")
    page.locator(".swrow .sw").first.click()                     # (a) Internet für Updates aus
    _until(page, "!!document.querySelector('.toast')?.textContent.includes('aus')")
    page.click('.side button[data-page="maint"]')
    page.wait_for_selector("#net-check")
    assert page.locator("#net-check").is_disabled() and "ausgeschaltet" in page.inner_text("#net-form")
    page.click('.side button[data-page="devices"]')             # T34: Geräte & Netz › Netz & Internet
    page.click('.tabs [data-tab="net"]')
    page.wait_for_selector(".swrow .sw")
    page.locator(".swrow .sw").first.click()                     # wieder an
    _until(page, "!!document.querySelector('.toast')?.textContent.includes('erlaubt')")
    # (c) dauerhaft verbunden: dritter Schalter, gespeichert, nach dem Neuladen «Wird verbunden …»
    assert page.locator(".swrow").count() == 3 and "Dauerhaft verbunden" in page.inner_text(".swrow >> nth=2")
    page.locator(".swrow .sw").nth(2).click()
    _until(page, "!!document.querySelector('.toast')?.textContent.includes('Dauerhaft verbunden: ein')")
    assert env["c"].get("/api/admin/update/net", auth=AUTH).json()["keep"]["on"] is True
    page.reload()
    page.wait_for_selector(".keep-sw")
    assert page.locator(".keep-sw").is_checked() and "verbunden" in page.inner_text(".swrow >> nth=2").lower()
    page.locator(".swrow .sw").nth(2).click()
    _until(page, "!!document.querySelector('.toast')?.textContent.includes('Dauerhaft verbunden: aus')")
    assert not errs, errs
    ctx.close()


@pytest.mark.parametrize("width", [412, 360])
def test_phone_width_has_no_overflow(env, browser, width):  # noqa: F811
    ctx, page, errs = _admin(browser, env["base"], width=width, where="devices/net")
    page.check('#net-form input[name="via"][value="lan"]')
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    assert not errs, errs
    ctx.close()
