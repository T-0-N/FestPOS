"""Testmodus und Einführung (T7) im echten Browser: Frage am Ende der Einführung (Vorwahl ja), Testmodus ohne PIN,
beim Start der App aus, Beenden aus der Statusanzeige (am Pi ohne Anmeldung) und der Verwaltung, Protokoll.
Wird übersprungen, wenn Playwright oder Chromium fehlt (z.B. auf dem Pi)."""
from __future__ import annotations

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_api import AUTH  # noqa: E402
from tests.test_browser_p4 import _enter_pin, _start, _watch, browser  # noqa: E402,F401
from tests.test_browser_receipt import _until  # noqa: E402

PHONE = {"X-Test-Cert": "XCover 7", "X-Test-Client": "192.168.55.107"}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("t7"), target="tests.asgi_testphone:app")
    yield base, c
    proc.terminate()
    proc.wait(10)


def _phone(browser, base, name="Anna", headers=PHONE, width=412):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": 892}, extra_http_headers=headers)
    ctx.add_init_script(f"localStorage.setItem('festpos.device', JSON.stringify({name!r}))")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    _until(page, "S.boot && S.boot.test")
    return ctx, page, errs


def _tour_to_end(page):
    page.click('[data-act="settings"]')
    page.click('[data-act="tour-start"]')
    for _ in range(40):
        page.wait_for_selector(".tour-bubble")
        if page.locator(".tour-choice").count() or page.locator('.tour-nav [data-act="tour-end"]').count():
            return
        page.click('[data-act="tour-next"]')
    pytest.fail("Einführung endet nicht")


def _audit(c, action):
    return [r for r in c.get("/api/admin/audit", auth=AUTH).json()["entries"] if r["action"] == action]


def test_tour_asks_for_test_mode_yes_is_default(server, browser):  # noqa: F811
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin_required": False, "test_print": False})
    for width in (412, 360):
        ctx, page, errs = _phone(browser, base, width=width)
        _tour_to_end(page)
        assert page.inner_text(".tour-bubble h2") == "Testmodus einschalten?"
        main = page.locator(".tour-bubble .go")
        assert main.count() == 1 and main.get_attribute("data-v") == "1"            # Vorwahl: Testmodus (ja)
        assert page.evaluate("document.activeElement?.dataset.v") == "1"
        assert page.locator('[data-act="tour-test"][data-v="0"]').is_visible()
        bb = page.locator(".tour-bubble").bounding_box()
        assert bb["x"] >= 0 and bb["x"] + bb["width"] <= width and bb["y"] + bb["height"] <= 892
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        page.click('[data-act="tour-test"][data-v="1"]')
        page.wait_for_selector("body.testmode .testbar")                            # ohne PIN sofort an
        assert page.locator("#tour").count() == 0 and page.locator(".sheet").count() == 0
        # Neuladen = Start der App: Testmodus aus, mit Hinweis; Übungs-Warenkorb weg
        page.reload()
        page.wait_for_selector('[data-act="digit"]')
        page.wait_for_selector(".toast")
        assert "Testmodus beendet" in page.inner_text(".toast")
        assert page.evaluate("S.test") is False and page.locator(".testbar").count() == 0
        assert page.evaluate("localStorage.getItem('festpos.test')") is None
        assert not errs, errs
        ctx.close()
    assert {r["details"].get("how") for r in _audit(c, "test_on")} >= {"tour"}
    assert {r["details"].get("how") for r in _audit(c, "test_off")} >= {"start"}


def test_tour_no_and_with_pin_and_switched_off(server, browser):  # noqa: F811
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": "2468", "test_pin_required": True})
    ctx, page, errs = _phone(browser, base)
    _tour_to_end(page)
    assert page.inner_text(".tour-bubble .go") == "Ja, üben (Test-PIN)"
    page.click('[data-act="tour-test"][data-v="0"]')                                # nein: normal weiter
    page.wait_for_function("() => !document.getElementById('tour')")
    assert page.evaluate("S.test") is False and page.locator(".sheet").count() == 0
    _tour_to_end(page)
    page.click('[data-act="tour-test"][data-v="1"]')                                # ja: PIN-Fenster bleibt offen
    page.wait_for_selector("#tpinout")
    page.wait_for_timeout(400)
    assert page.locator("#tpinout").count() == 1
    _enter_pin(page, "tpin", "2468")
    page.wait_for_selector("body.testmode .testbar")
    page.click('.testbar [data-act="test-exit"]')
    page.wait_for_function("() => !document.body.classList.contains('testmode')")
    # Testmodus in der Verwaltung aus: keine Frage, Schluss wie bisher
    c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": False})
    page.evaluate("loadBoot()")
    _until(page, "S.boot.test.enabled === false")
    _tour_to_end(page)
    assert page.locator(".tour-choice").count() == 0
    assert "☰" in page.inner_text(".tour-bubble")
    page.click('.tour-nav [data-act="tour-end"]')
    assert not errs, errs
    ctx.close()


def test_start_without_pin_asks_once(server, browser):  # noqa: F811
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin_required": False})
    ctx, page, errs = _phone(browser, base)
    _until(page, "S.boot.test.enabled === true && S.boot.test.pin === false")
    page.click('[data-act="settings"]')
    page.click('[data-act="test-start"]')
    page.wait_for_selector('.sheet [data-act="test-go"]')                           # Bestätigung statt PIN
    assert page.locator("#tpinout").count() == 0
    page.click('.sheet [data-act="test-go"]')
    page.wait_for_selector("body.testmode .testbar")
    page.click('.testbar [data-act="test-exit"]')
    page.wait_for_function("() => !document.body.classList.contains('testmode')")
    assert not errs, errs
    ctx.close()


def test_status_display_ends_test_mode(server, browser):  # noqa: F811
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin_required": False})
    phones = []
    for name, cert, ip in (("Anna", "XCover 7", "192.168.55.107"), ("Beat", "XCover 8", "192.168.55.108")):
        ctx, page, errs = _phone(browser, base, name, {"X-Test-Cert": cert, "X-Test-Client": ip})
        page.click('[data-act="settings"]')
        page.click('[data-act="test-start"]')
        page.click('.sheet [data-act="test-go"]')
        page.wait_for_selector("body.testmode")
        page.evaluate("pollStatus()")
        phones.append((ctx, page, errs))
    # Statusanzeige am Pi (127.0.0.1, ohne Anmeldung): Marke TEST, «Alle aus dem Testmodus», Knopf im Detail
    st_ctx = browser.new_context(viewport={"width": 1280, "height": 720})
    st = st_ctx.new_page()
    st_errs = _watch(st)
    st.goto(base + "/status.html")
    _until(st, "document.querySelectorAll('#devices .testtag').length === 2")
    assert st.locator("#testall").is_visible()
    first = st.locator("[data-dev]").first
    first.click()
    st.wait_for_selector("#detail-sheet .panel")
    name = st.inner_text("#detail-sheet .head b")
    st.click('#detail-sheet [data-act="go"] >> text=Testmodus beenden')
    st.click('#detail-sheet [data-act="yes"]')
    st.wait_for_selector("#detail-sheet .msg")
    hit, other = (phones[0], phones[1]) if name == "XCover 7" else (phones[1], phones[0])
    hit[1].evaluate("pollStatus()")
    hit[1].wait_for_function("() => !document.body.classList.contains('testmode')")
    assert "Statusanzeige" in hit[1].inner_text(".toast")
    other[1].evaluate("pollStatus()")
    other[1].wait_for_timeout(500)
    assert other[1].evaluate("S.test") is True                                     # nur das gewählte Handy
    # das andere über «alle» aus der Verwaltung (angemeldet)
    r = c.post("/api/admin/devices/test-exit", auth=AUTH, json={"all": True})
    assert r.status_code == 200
    other[1].evaluate("pollStatus()")
    other[1].wait_for_function("() => !document.body.classList.contains('testmode')")
    # später wieder gestartet: ein früheres «beenden» gilt nicht mehr
    other[1].wait_for_timeout(1100)
    other[1].click('[data-act="settings"]')
    other[1].click('[data-act="test-start"]')
    other[1].click('.sheet [data-act="test-go"]')
    other[1].evaluate("pollStatus()")
    other[1].wait_for_timeout(600)
    other[1].evaluate("pollStatus()")
    other[1].wait_for_timeout(600)
    assert other[1].evaluate("S.test") is True
    other[1].click('.testbar [data-act="test-exit"]')
    assert len(_audit(c, "test_exit")) >= 2
    for ctx, _, errs in phones:
        assert not errs, errs
        ctx.close()
    assert not st_errs, st_errs
    st_ctx.close()


def test_test_exit_access_and_pin_setting(server):
    base, c = server
    # Statusanzeige-Route nur vom Pi selbst, nie mit Anmeldung; Verwaltung ab Festleitung
    assert c.post("/api/status/test-exit", json={"all": True}, headers={"X-Test-Client": "192.168.55.50"}).status_code == 403
    assert c.post("/api/status/test-exit", json={"all": True}).status_code == 200
    assert c.post("/api/status/test-exit", json={}).status_code == 422
    assert c.post("/api/admin/devices/test-exit", json={"all": True}).status_code == 401
    c.post("/api/admin/users", auth=AUTH, json={"name": "Lea", "role": "lead", "password": "geheim123"})
    c.post("/api/admin/users", auth=AUTH, json={"name": "Vreni", "role": "view", "password": "geheim123"})
    assert c.post("/api/admin/devices/test-exit", auth=("Lea", "geheim123"), json={"all": True}).status_code == 200
    assert c.post("/api/admin/devices/test-exit", auth=("Vreni", "geheim123"), json={"all": True}).status_code == 403
    # (eigener Ausgangszustand: der Test lief allein nicht, er verliess sich auf die Tests davor – Review 2026-11, T1)
    assert c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": "2468",
                                                         "test_pin_required": True}).status_code == 200
    # Festleitung darf «Test-PIN verlangen» ändern
    assert c.put("/api/admin/settings", auth=("Lea", "geheim123"), json={"test_pin_required": True}).status_code == 200
    # Bootstrap: pin true/false, ohne PIN kein Hash
    phone = {"X-Test-Cert": "XCover 9"}
    boot = c.get("/api/bootstrap", headers=phone).json()["test"]
    assert boot["enabled"] and boot["pin"] and boot["hash"]
    c.put("/api/admin/settings", auth=AUTH, json={"test_pin_required": False})
    boot = c.get("/api/bootstrap", headers=phone).json()["test"]
    assert boot["enabled"] and boot["pin"] is False and boot["hash"] == ""


def test_tour_explains_enabled_features(server, browser):  # noqa: F811
    """Ausverkauft, Lieferzeit, Quittung und PIN-Sperre: eigener Schritt nur, wenn eingeschaltet – mit Rahmen am Ziel."""
    base, c = server
    prods = c.get("/api/admin/products", auth=AUTH).json()
    pid = prods[1]["id"]
    printer = c.get("/api/admin/printers", auth=AUTH).json()[0]["id"]
    c.put(f"/api/admin/products/{pid}/stock", auth=AUTH, json={"soldout": True})
    c.put("/api/admin/settings", auth=AUTH, json={"delivery_times": "Sofort, Schluss", "receipt_printer_id": printer,
                                                  "pin_enabled": True, "pin": "2580", "test_enabled": False})
    c.put(f"/api/admin/products/{prods[0]['id']}/delivery", auth=AUTH, json={"enabled": True})
    try:
        ctx, page, errs = _phone(browser, base)
        _enter_pin(page, "pin", "2580")                                     # PIN-Sperre beim Start
        page.wait_for_function("() => !document.getElementById('lock')")
        page.click('[data-act="settings"]')
        page.click('[data-act="tour-start"]')
        texts, spots = [], {}
        for _ in range(40):
            page.wait_for_selector(".tour-bubble")
            txt = page.inner_text(".tour-bubble p")
            texts.append(txt)
            spots[txt[:12]] = page.evaluate("!document.querySelector('.tour-spot').classList.contains('none')")
            if page.locator('.tour-nav [data-act="tour-end"]').count():
                break
            page.click('[data-act="tour-next"]')
        joined = "\n".join(texts)
        for want in ("ausverkauft", "Lieferzeit", "«Schluss»", "Quittung", "PIN-Sperre"):
            assert want in joined, want
        assert spots["Grau mit «au"] and spots["Quittung: Mö"]                   # Rahmen um Kachel bzw. Quittungsknopf
        page.click('.tour-nav [data-act="tour-end"]')
        assert page.evaluate("S.view") == "table" and page.evaluate("cartCount()") == 0
        assert not errs, errs
        ctx.close()
    finally:
        c.put(f"/api/admin/products/{pid}/stock", auth=AUTH, json={"soldout": False})
        c.put(f"/api/admin/products/{prods[0]['id']}/delivery", auth=AUTH, json={"enabled": False})
        c.put("/api/admin/settings", auth=AUTH, json={"pin_enabled": False, "receipt_printer_id": 0})


def test_bar_says_whether_printed(server, browser):  # noqa: F811
    """T19: Der gelbe Balken sagt, ob Übungsbons gedruckt werden; passt am XCover (360 px) in eine Zeile und folgt einer
    geänderten Einstellung nach der nächsten Abfrage."""
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin_required": False, "test_print": False})
    for width in (412, 360):
        ctx, page, errs = _phone(browser, base, width=width)
        page.click('[data-act="settings"]')
        page.click('[data-act="test-start"]')
        page.click('.sheet [data-act="test-go"]')
        page.wait_for_selector("body.testmode .testbar")
        assert page.inner_text("#testbar-sub") == "kein Geld, kein Bon"
        one = "(() => { const s = document.getElementById('testbar-sub'); return s.getClientRects().length === 1 && " \
              "s.getBoundingClientRect().height < parseFloat(getComputedStyle(s).lineHeight) * 1.5; })()"
        assert page.evaluate(one), page.evaluate("document.getElementById('testbar-sub').getBoundingClientRect().height")
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        c.put("/api/admin/settings", auth=AUTH, json={"test_print": True})
        _until(page, "document.getElementById('testbar-sub').textContent === 'kein Geld, Bon mit TEST'", timeout_ms=25000)
        assert page.evaluate(one)
        c.put("/api/admin/settings", auth=AUTH, json={"test_print": False})
        assert not errs, errs
        ctx.close()
