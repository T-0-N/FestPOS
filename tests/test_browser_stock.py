"""Ausverkauft und Restmenge am Handy (XCover-Grösse, auch 360 px breit): Kachel grau, «noch N» auf der Kachel,
nicht mehr als die Restmenge in den Warenkorb, Handy mit der Rolle «Festleitung» meldet ausverkauft.
Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_api import AUTH  # noqa: E402
from tests.test_browser_p4 import _open_table, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402

PHONE = {"X-Test-Cert": "XCover 1", "X-Test-Client": "192.168.55.101"}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("stock"), target="tests.asgi_testphone:app")
    yield base, c
    proc.terminate()
    proc.wait(10)


def _ids(c):
    return {p["name"]: p["id"] for p in c.get("/api/admin/products", auth=AUTH).json()}


def _tile(page, name):
    return page.locator(".grid .tile", has_text=name)


@pytest.mark.parametrize("width", [412, 360])
def test_soldout_and_stock_on_phone(server, browser, width):  # noqa: F811
    base, c = server
    ids = _ids(c)
    assert c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "lead"}).status_code == 200
    c.put(f"/api/admin/products/{ids['Bier 5dl']}/stock", auth=AUTH, json={"soldout": False})
    c.put(f"/api/admin/products/{ids['Bratwurst']}/stock", auth=AUTH, json={"stock": 2, "stock_warn": 5})
    ctx = browser.new_context(viewport={"width": width, "height": 892}, has_touch=True, extra_http_headers=PHONE)
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('XCover 1'))")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    _until(page, "S.boot?.can_soldout === true")          # Rolle bekannt (die Tischwahl steht schon vor der Preisliste)
    # Festleitung meldet Bier ausverkauft: im Menü auf der Tischseite
    page.click('[data-act="settings"]')
    page.click('[data-act="soldout-open"]')
    row = page.locator(".soldrow", has_text="Bier 5dl")
    assert row.get_attribute("aria-pressed") == "false"
    assert page.evaluate("document.documentElement.scrollWidth") <= width               # nichts ragt heraus
    row.click()
    _until(page, "document.querySelector('.soldrow[aria-pressed=\"true\"]')?.textContent.includes('Bier 5dl')")
    page.locator(".sheet-bg").click(position={"x": 20, "y": 20})                          # daneben tippen: zu
    page.wait_for_selector(".sheet-bg", state="detached")
    _open_table(page, "12")
    # Kachel grau, Antippen geht nicht
    bier = _tile(page, "Bier 5dl")
    assert "soldout" in bier.get_attribute("class") and bier.locator(".stocktag").inner_text() == "Ausverkauft"
    bier.click()
    assert bier.locator(".q").inner_text() == "0"
    _until(page, "document.querySelector('.toast')?.textContent.includes('ausverkauft')")
    assert c.get("/api/phone/status", headers=PHONE).json()["stock"]["soldout"] == [ids["Bier 5dl"]]

    # Restmenge: «noch 2» auf der Kachel, mehr geht nicht in den Warenkorb
    page.locator("nav [data-act='cat']", has_text="Essen").click()
    wurst = _tile(page, "Bratwurst")
    assert wurst.locator(".stocktag").inner_text() == "noch 2"
    for _ in range(3):
        wurst.click()
    assert wurst.locator(".q").inner_text() == "2"
    _until(page, "document.querySelector('.toast')?.textContent.includes('nur noch 2')")

    # die Bratwürste gehen noch: senden, Restmenge 0 -> grau (auch auf anderen Handys über /api/phone/status)
    page.click("#send")
    page.wait_for_selector('#paystate[data-s="sent"]', timeout=10000)
    boot = {p["name"]: p for p in c.get("/api/bootstrap", headers=PHONE).json()["products"]}
    assert boot["Bratwurst"]["stock"] == 0
    assert not [e for e in errs if "422" not in e], errs
    ctx.close()


def _phone_unaware(page):
    """Das Handy erfährt den neuen Stand nicht von selbst (wie in den Sekunden zwischen zwei Abfragen)."""
    import json

    def h(route):
        r = route.fetch()
        j = r.json()
        j.pop("stock", None)
        route.fulfill(response=r, body=json.dumps(j))
    page.route("**/api/phone/status", h)


def _fill_cart(page):
    _open_table(page, "12")
    for _ in range(3):
        _tile(page, "Bier 5dl").click()
    page.locator("nav [data-act='cat']", has_text="Essen").click()
    for _ in range(2):
        _tile(page, "Bratwurst").click()
    page.click('[data-act="cart"]')


def _reset(c, ids, mode):
    c.put("/api/admin/settings", auth=AUTH, json={"stock_check": mode})
    for n in ("Bier 5dl", "Bratwurst", "Mineral"):
        c.put(f"/api/admin/products/{ids[n]}/stock", auth=AUTH, json={"soldout": False})


@pytest.mark.parametrize("width", [412, 360])
def test_send_waits_and_rejection_stays_in_cart(server, browser, width):  # noqa: F811
    base, c = server
    ids = _ids(c)
    _reset(c, ids, "always")
    ctx = browser.new_context(viewport={"width": width, "height": 892}, has_touch=True, extra_http_headers=PHONE)
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('XCover 1'))")
    page = ctx.new_page()
    errs = _watch(page)
    _phone_unaware(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    _fill_cart(page)
    # in diesem Moment: Bratwurst ausverkauft, vom Bier nur noch 1
    c.put(f"/api/admin/products/{ids['Bratwurst']}/stock", auth=AUTH, json={"soldout": True})
    c.put(f"/api/admin/products/{ids['Bier 5dl']}/stock", auth=AUTH, json={"stock": 1, "stock_warn": 5})
    orders = len(c.get("/api/admin/orders?scope=all", auth=AUTH).json()["orders"])
    page.click('.sheet [data-act="send"]')
    page.wait_for_selector(".sheet .chkbox")
    box = page.inner_text(".sheet .chkbox")
    assert "Nicht gesendet" in box and "Ausverkauft: Bratwurst; Von Bier 5dl hat es nur noch 1" in box
    assert page.locator(".sheet .row.rowbad").count() == 2
    send = page.locator('.sheet [data-act="send"]')
    assert send.is_disabled() and send.inner_text() == "Zuerst anpassen"
    assert page.locator(".prob").count() == 0                                          # nichts unter «abgelehnt»
    assert len(c.get("/api/admin/orders?scope=all", auth=AUTH).json()["orders"]) == orders
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    # Kachel im Hintergrund sofort grau (neuer Stand kam mit der Ablehnung)
    assert "soldout" in _tile(page, "Bratwurst").get_attribute("class")
    page.click('.sheet [data-act="check-fix"]')
    page.wait_for_selector(".sheet .chkbox.okd")
    assert "2× Bratwurst entfernt (ausverkauft), Bier 5dl von 3 auf 1" in page.inner_text(".sheet .chkbox")
    assert not send.is_disabled()
    send.click()
    page.wait_for_selector('#paystate[data-s="sent"]', timeout=10000)
    assert "1× Bier 5dl" in page.inner_text("#bon") and "Bratwurst" not in page.inner_text("#bon")
    boot = {p["name"]: p for p in c.get("/api/bootstrap", headers=PHONE).json()["products"]}
    assert boot["Bier 5dl"]["stock"] == 0
    assert not [e for e in errs if "422" not in e], errs
    ctx.close()


def test_without_waiting_rejection_shows_in_pay_view(server, browser):  # noqa: F811
    base, c = server
    ids = _ids(c)
    _reset(c, ids, "off")
    c.put(f"/api/admin/products/{ids['Bier 5dl']}/stock", auth=AUTH, json={"stock": None})
    ctx = browser.new_context(viewport={"width": 360, "height": 892}, has_touch=True, extra_http_headers=PHONE)
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('XCover 1'))")
    page = ctx.new_page()
    errs = _watch(page)
    _phone_unaware(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    _fill_cart(page)
    c.put(f"/api/admin/products/{ids['Bratwurst']}/stock", auth=AUTH, json={"soldout": True})
    page.click('.sheet [data-act="send"]')                                            # gleich zur Kasse (Schalter aus)
    page.wait_for_selector("#rejbox", timeout=10000)
    assert "Nicht angenommen" in page.inner_text("#rejbox") and "Ausverkauft: Bratwurst" in page.inner_text("#rejbox")
    assert page.locator("#paypanel").count() == 0 and "Nicht kassieren" in page.inner_text("#bon")
    assert page.locator("#bon .line.soldline").count() == 1
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    page.click('[data-act="payrej-fix"]')                                              # zurück in den Warenkorb, ohne Wurst
    page.wait_for_selector(".grid .tile")
    _until(page, "document.querySelector('.toast')?.textContent.includes('2× Bratwurst entfernt')")
    assert page.evaluate("cartCount()") == 3
    assert page.locator(".prob").count() == 0
    # Stufe «Nur bei Produkten mit Restmenge»: im Warenkorb ist nur Bier (ohne Bestand) -> nicht warten
    wait = page.evaluate("""() => { const r = {}, w = () => waitForServer(cartLines());
      S.boot.stock_check = { mode: "stock", wait: 2 }; r.stock = w();
      productById(cartLines()[0].p.id).stock = 5; r.stock_counted = w();
      S.boot.stock_check.mode = "always"; r.always = w(); S.boot.stock_check.mode = "off"; r.off = w();
      return r; }""")
    assert wait == {"stock": False, "stock_counted": True, "always": True, "off": False}
    c.put("/api/admin/settings", auth=AUTH, json={"stock_check": "always"})
    assert not [e for e in errs if "422" not in e], errs
    ctx.close()


def test_paper_low_only_for_lead_in_yellow(server, browser):  # noqa: F811
    """Rückmeldung: «Papier fast leer» auch auf Handys mit Rolle Festleitung – gelb wie in der Statusanzeige;
    die Bedienung sieht es nicht (erst «Papier leer»)."""
    import json
    base, c = server

    def low(route):
        r = route.fetch()
        j = r.json()
        for p in j["printers"]:
            p.update(state="ok", paper_low=True, code=None)
        route.fulfill(response=r, body=json.dumps(j))

    def look(name):
        ctx = browser.new_context(viewport={"width": 360, "height": 892}, has_touch=True,
                                  extra_http_headers={**PHONE, "X-Test-Cert": name})
        ctx.add_init_script(f"localStorage.setItem('festpos.device', JSON.stringify({name!r}))")
        page = ctx.new_page()
        errs = _watch(page)
        page.route("**/api/phone/status", low)
        page.goto(base + "/index.html")
        page.wait_for_selector('[data-act="digit"]')
        _until(page, "S.boot != null && PST.printers != null")
        page.evaluate("pollStatus()")
        _until(page, "document.querySelector('#stchip') !== null")
        return ctx, page, errs

    assert c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "lead"}).status_code == 200
    ctx, page, errs = look("XCover 1")
    _until(page, "S.boot?.can_soldout === true && document.querySelector('#stchip').dataset.s === 'low'")
    chip = " ".join(page.inner_text("#stchip").split())
    assert "Papier fast leer" in chip
    bg = page.evaluate("getComputedStyle(document.querySelector('#stchip')).backgroundColor")
    dot = page.evaluate("getComputedStyle(document.querySelector('#stchip i')).backgroundColor")
    assert bg == "rgb(255, 241, 191)" and dot == "rgb(242, 183, 5)"                   # gelb (Statusanzeige: #f2b705)
    assert "Papier fast leer – Rolle bereitlegen" in page.inner_text(".toast")
    page.click("#stchip")
    assert page.locator("#statussheet .pl-low").count() >= 1
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    assert not errs, errs
    ctx.close()
    c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 2", "role": "service"})
    ctx, page, errs = look("XCover 2")                                                 # Bedienung: nichts
    page.wait_for_timeout(300)
    assert page.locator("#stchip").get_attribute("data-s") == "ok" and page.locator(".toast").count() == 0
    ctx.close()


@pytest.mark.parametrize("width", [360, 412])
def test_testmode_header_stays_one_row_with_paper_low(server, browser, width):  # noqa: F811
    """Rückmeldung: Übung und «Papier fast leer» – Druckerhinweis und Verlauf rutschten auf eine zweite
    Zeile, der Kopf wurde höher. Jetzt: Balken oben, darunter eine Zeile, gleich hoch wie ohne Übung."""
    import json
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": "2468", "test_print": False})
    assert c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "lead"}).status_code == 200

    def low(route):
        r = route.fetch()
        j = r.json()
        for p in j["printers"]:
            p.update(state="ok", paper_low=True, code=None)
        route.fulfill(response=r, body=json.dumps(j))

    ctx = browser.new_context(viewport={"width": width, "height": 892}, has_touch=True, extra_http_headers=PHONE)
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('Theke 1'))")
    page = ctx.new_page()
    errs = _watch(page)
    page.route("**/api/phone/status", low)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    _until(page, "S.boot?.can_soldout === true && S.boot?.test?.enabled === true")
    page.evaluate("pollStatus()")
    _until(page, "document.querySelector('#stchip')?.dataset.s === 'low'")
    plain = page.evaluate("document.querySelector('header.top').offsetHeight")
    page.evaluate("setTest(true, 'app')")
    page.wait_for_selector("header.top.test .testbar")
    bar = page.evaluate("document.querySelector('.testbar').offsetHeight")
    head = page.evaluate("document.querySelector('header.top').offsetHeight")
    assert head <= plain + bar + 4, (plain, bar, head)                     # nur der Balken kommt dazu
    mid = lambda s: page.evaluate(f"(() => {{ const r = document.querySelector('{s}').getBoundingClientRect(); return r.top + r.height / 2; }})()")  # noqa: E731
    menu = mid('header.top [data-act="settings"]')
    assert abs(mid("#stchip") - menu) < 3 and abs(mid('header.top [data-act="history"]') - menu) < 3   # eine Zeile
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    page.evaluate("setTest(false, 'app')")
    assert not errs, errs
    ctx.close()


def test_status_chip_names_router_or_internet(server, browser):  # noqa: F811
    """ohne Verbindung, obwohl Karte/TWINT oder QR-Bestellung es brauchen, steht oben rot der Router –
    oder «Internet», wenn die Pi über ihr WLAN bzw. einen USB-Adapter verbunden sind."""
    import json
    base, _ = server
    up = {"v": {"kind": "internet"}}

    def h(route):
        r = route.fetch()
        j = r.json()
        j["uplink"], j["printers"] = up["v"], []                  # gestörte Drucker gingen vor
        route.fulfill(response=r, body=json.dumps(j))
    ctx = browser.new_context(viewport={"width": 360, "height": 892}, has_touch=True, extra_http_headers=PHONE)
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('XCover 1'))")
    page = ctx.new_page()
    errs = _watch(page)
    page.route("**/api/phone/status", h)
    page.goto(base + "/index.html")
    page.wait_for_selector('#stchip[data-s="bad"]')
    assert " ".join(page.inner_text("#stchip").split()).startswith("Internet ohne Verbindung")
    up["v"] = {"name": "Router Zelt"}
    _until(page, "document.getElementById('stchip').textContent.includes('Router Zelt')", 15000)   # alle 10 s
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    assert not errs, errs
    ctx.close()


@pytest.mark.parametrize("width", [412, 360])
def test_lead_sets_remaining_stock(server, browser, width):  # noqa: F811
    """Rückmeldung Punkt 5: Antippen meldet weiterhin sofort ausverkauft; «Stück» oder lange drücken öffnet die
    Restmenge (− / + / eintippen / 5 10 20 50), grün «Noch N verfügbar»; lange drücken schaltet NICHT um."""
    base, c = server
    ids = _ids(c)
    assert c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "lead"}).status_code == 200
    for n in ("Bier 5dl", "Bratwurst"):
        c.put(f"/api/admin/products/{ids[n]}/stock", auth=AUTH, json={"soldout": False, "stock": None})
    ctx = browser.new_context(viewport={"width": width, "height": 892}, has_touch=True, extra_http_headers=PHONE)
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('XCover 1'))")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    _until(page, "S.boot?.can_soldout === true")
    page.click('[data-act="settings"]')
    page.click('[data-act="soldout-open"]')
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    # «Stück» bei der Bratwurst: 20 antippen, eine weniger, speichern
    page.locator(".soldrow-w", has_text="Bratwurst").locator('[data-act="stock-edit"]').click()
    page.wait_for_selector("#stockin")
    assert page.locator('#soldoutsheet [data-act="stock-save"]').is_disabled()
    page.click('#soldoutsheet [data-act="stock-set"][data-v="20"]')
    page.click('#soldoutsheet [data-act="stock-q"][data-d="-1"]')
    assert page.input_value("#stockin") == "19" and page.locator(".foot .go, .sheet .go").count() == 1
    assert " ".join(page.inner_text('#soldoutsheet [data-act="stock-save"]').split()) == "Noch 19 verfügbar"
    page.fill("#stockin", "7")
    assert " ".join(page.inner_text('#soldoutsheet [data-act="stock-save"]').split()) == "Noch 7 verfügbar"
    page.click('#soldoutsheet [data-act="stock-save"]')
    _until(page, "document.querySelector('.soldrow-w') && [...document.querySelectorAll('.soldrow')].some(b => b.textContent.includes('Bratwurst') && b.textContent.includes('noch 7'))")
    assert c.get("/api/phone/status", headers=PHONE).json()["stock"]["stock"][str(ids["Bratwurst"])] == 7
    # lange drücken auf Bier: Restmenge statt umschalten
    box = page.locator(".soldrow", has_text="Bier 5dl").bounding_box()
    page.mouse.move(box["x"] + 40, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.wait_for_timeout(700)
    page.mouse.up()
    page.wait_for_selector("#stockin")
    assert page.locator("#soldoutsheet h2").inner_text() == "Bier 5dl"
    assert ids["Bier 5dl"] not in c.get("/api/phone/status", headers=PHONE).json()["stock"]["soldout"]
    page.click('#soldoutsheet [data-act="stock-back"]')
    # einfaches Antippen: sofort ausverkauft wie bisher
    page.locator(".soldrow", has_text="Bier 5dl").click()
    _until(page, "document.querySelector('.soldrow[aria-pressed=\"true\"]')?.textContent.includes('Bier 5dl')")
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert not errs, errs
    for n in ("Bier 5dl", "Bratwurst"):
        c.put(f"/api/admin/products/{ids[n]}/stock", auth=AUTH, json={"soldout": False, "stock": None})
    ctx.close()
