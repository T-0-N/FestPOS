"""Im echten Browser: Mobilfunk-Router in der Verwaltung einrichten (Zertifikat übernehmen) und den Empfang auf der
Statusanzeige sehen – Balken, Warnung bei schwachem Empfang, Alarm ohne Verbindung, Details mit Verlauf.
Wird übersprungen ohne Chromium."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.fake_router import PASSWORD, USER, FakeRouter  # noqa: E402
from tests.test_browser_p4 import AUTH, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("uplink"))
    yield base, c
    proc.terminate()
    proc.wait(10)


@pytest.fixture(scope="module")
def router():
    r = FakeRouter()
    yield r
    r.stop()


def test_admin_sets_up_router_and_status_shows_signal(server, router, browser):  # noqa: F811
    base, c = server
    ctx = browser.new_context(viewport={"width": 1300, "height": 1000})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector(".side")
    page.click('.side button[data-page="devices"]')             # T34: Geräte & Netz › Netz & Internet
    page.click('.tabs [data-tab="net"]')
    page.wait_for_selector("#upform")
    assert "Kein Router eingerichtet" in page.inner_text("#upform")
    f = page.locator("#upform")
    f.locator('select[name="kind"]').select_option("mikrotik")
    assert f.locator('input[name="iface"]').get_attribute("placeholder") == "lte1"
    f.locator('input[name="host"]').fill("127.0.0.1")
    f.locator('input[name="port"]').fill(str(router.port))
    f.locator('input[name="user"]').fill(USER)
    f.locator('input[name="pw"]').fill(PASSWORD)
    f.locator("button.pri").click()
    page.wait_for_selector("#up-trust")                                   # gleich verbunden: Zertifikat anbieten
    assert router.pin in page.inner_text("#up-result") and not router.authorized
    assert "/ip service set www-ssl" in page.text_content(".up-setup pre")  # Einrichtung zum Kopieren (zugeklappt)
    page.once("dialog", lambda d: d.accept())
    page.click("#up-trust")
    page.wait_for_function("() => document.querySelector('#up-result').textContent.includes('Swisscom')")
    res = page.inner_text("#up-result")
    assert "Verbunden" in res and "RSRP −92 dBm" in res and "mittel (2 von 4)" in res and "LTE B3+B20" in res
    assert "Zertifikat übernommen am" in res and router.authorized
    _shot(page.locator("#upform"), "uplink-admin.png")
    assert not errs, errs
    ctx.close()

    c.post("/api/admin/uplink/check", auth=AUTH)                          # zweiter Messpunkt für den Verlauf
    ctx = browser.new_context(viewport={"width": 1280, "height": 720})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/status.html")                                      # Bildschirm am Pi: ohne Anmeldung
    page.wait_for_selector("#uplink:not([hidden])")
    up = page.locator("#uplink")
    assert "ok" in up.get_attribute("class") and up.locator(".sig i.on").count() == 2
    assert "−92 dBm · mittel" in up.inner_text() and "Swisscom · LTE B3+B20" in up.inner_text()
    up.click()
    page.wait_for_selector("#detail-sheet .upchart path.l", state="attached")
    sheet = page.inner_text("#detail-sheet")
    for want in ("Verbunden", "Signalstärke (RSRP)", "−92 dBm", "Störabstand (SINR)", "14 dB", "Swisscom", "B3+B20 · 20 MHz",
                 "LtAP mini LTE · 7.16.1", "↓ 48 MB", "im Verlauf", "vor 5 min"):
        assert want in sheet, want
    assert page.get_attribute("#detail-sheet .upchart path.l", "d").count("L") >= 1
    _shot(page, "uplink-detail.png")
    page.keyboard.press("Escape")

    router.values.update(rsrp="-106", sinr="2")                           # Empfang schwach: gelb, Hinweis oben
    c.post("/api/admin/uplink/check", auth=AUTH)
    page.wait_for_function("() => document.getElementById('uplink').classList.contains('wait')")
    assert "Empfang schwach (−106 dBm)" in page.inner_text("#detail")
    assert page.locator("#uplink .sig i.on").count() == 1

    router.values.update(rsrp="-92", sinr="14")
    router.mode = "rights"                                                # keine Verbindung, nicht gebraucht: gelb
    c.post("/api/admin/uplink/check", auth=AUTH)
    page.wait_for_function("() => document.getElementById('uplink').classList.contains('wait')")
    c.put("/api/admin/payrexx", auth=AUTH, json={"enabled": True, "method": "", "show_result": False})   # gebraucht: rot
    page.wait_for_function("() => document.getElementById('uplink').classList.contains('bad')")
    assert "Mobilfunk ohne Verbindung" in page.inner_text("#detail") and "bad" in page.get_attribute("#pill", "class")
    assert "zu wenige Rechte" in page.inner_text("#uplink")
    router.mode = "https"
    c.post("/api/admin/uplink/check", auth=AUTH)
    page.wait_for_function("() => document.getElementById('uplink').classList.contains('ok')")
    c.put("/api/admin/payrexx", auth=AUTH, json={"enabled": False, "method": "", "show_result": False})
    assert not errs, errs
    ctx.close()


def test_without_sim_is_an_alarm_not_connected(server, router, browser):  # noqa: F811
    """Router erreichbar, aber keine SIM-Karte: rot mit Grund – nicht «Verbunden» (so beim LtAP ohne SIM gesehen)."""
    base, c = server
    router.mode = "nosim"
    c.put("/api/admin/gast", auth=AUTH, json={"open": True, "domain": "", "instance": "", "max_cents": 30000, "refund_min": 10})
    try:                                                                  # QR-Bestellung an: Router wird gebraucht
        c.post("/api/admin/uplink/check", auth=AUTH)
        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        page = ctx.new_page()
        errs = _watch(page)
        page.goto(base + "/status.html")
        page.wait_for_function("() => document.getElementById('uplink').classList.contains('bad')")
        assert "keine SIM-Karte" in page.inner_text("#uplink") and page.locator("#uplink .sig i.on").count() == 0
        assert "Mobilfunk: keine SIM-Karte" in page.inner_text("#detail") and "bad" in page.get_attribute("#pill", "class")
        page.click("#uplink")
        page.wait_for_selector("#detail-sheet .panel")
        sheet = page.inner_text("#detail-sheet")
        assert "Verbunden" not in sheet and "keine SIM-Karte" in sheet and "SIM not inserted" in sheet
        assert "Datenverbindung" in sheet and "0 kB" in sheet and "Verlauf erscheint" not in sheet
        assert page.locator("#detail-sheet .upchart .now").count() == 0       # kein Punkt von früher
        _shot(page, "uplink-nosim.png")
        assert not errs, errs
        ctx.close()
        # Verwaltung: Grund statt «Verbunden», dazu die Antwort des Routers (ohne IMEI) für die Fehlersuche
        ctx = browser.new_context(viewport={"width": 1300, "height": 1000})
        page = ctx.new_page()
        errs = _watch(page)
        page.goto(base + "/admin.html")
        page.fill("#pw", "geheim")
        page.press("#pw", "Enter")
        page.wait_for_selector(".side")
        page.click('.side button[data-page="devices"]')             # T34: Geräte & Netz › Netz & Internet
        page.click('.tabs [data-tab="net"]')
        page.wait_for_selector("#up-result .fully-st")
        st = page.locator("#up-result .fully-st")
        assert st.inner_text() == "keine SIM-Karte" and "err" in st.get_attribute("class")
        raw = page.text_content("#up-result .up-raw pre")
        assert "pin-status: SIM not inserted" in raw and "imei" not in raw and "860000000000001" not in raw
        assert not errs, errs
        ctx.close()
    finally:
        router.mode = "https"
        c.post("/api/admin/uplink/check", auth=AUTH)
        c.put("/api/admin/gast", auth=AUTH, json={"open": False, "domain": "", "instance": "", "max_cents": 30000, "refund_min": 10})


def test_phone_portrait_shows_signal_without_overflow(server, router, browser):  # noqa: F811
    base, c = server
    br = browser.browser_type.launch(args=["--host-resolver-rules=MAP festpos.test 127.0.0.1"])
    try:
        ctx = br.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
        page = ctx.new_page()
        errs = _watch(page)
        tok = c.post("/api/admin/login", json={"name": "admin", "password": "geheim"}).json()["token"]
        page.add_init_script(f"sessionStorage.setItem('festpos.token', {tok!r})")
        page.goto(base.replace("127.0.0.1", "festpos.test") + "/status.html")
        page.wait_for_selector("#uplink:not([hidden])")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        box = page.locator("#uplink").bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390
        _shot(page, "uplink-phone.png", full_page=True)
        assert not errs, errs
        ctx.close()
    finally:
        br.close()


def _shot(page, name: str, **kw) -> None:
    """Bildschirmfoto (Seite oder Element) zum Anschauen, nur mit FESTPOS_SHOTS=/ordner."""
    import os
    if os.environ.get("FESTPOS_SHOTS"):
        page.screenshot(path=os.path.join(os.environ["FESTPOS_SHOTS"], name), **kw)
