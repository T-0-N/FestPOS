"""Version 2026.10.10-20: Kassenbuch im Browser – Zahlart in der Kasse (aus / freiwillig / Pflicht, auch nachgereicht),
Storno am Handy mit Rolle, Bestellungen und Tagesabschluss in der Verwaltung, Links zwischen Bestell-App, Verwaltung und
Statusanzeige (für Fully Kiosk). Wird übersprungen, wenn Playwright oder Chromium fehlt.

Der Server läuft hinter tests/asgi_testphone.py: Der Kopf X-Test-Cert spielt das Gerätezertifikat (wie am Handy-Port),
X-Test-Client einen anderen Rechner im Netz (sonst gilt 127.0.0.1 als Bildschirm am Pi)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import AUTH, _open_table, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)

PHONE = {"X-Test-Cert": "XCover 1", "X-Test-Client": "192.168.55.101"}
PC = {"X-Test-Client": "192.168.55.50"}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("ledger"), target="tests.asgi_testphone:app")
    yield base, c
    proc.terminate()
    proc.wait(10)


def _phone(browser, base, headers=PHONE, name="XCover 1"):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 412, "height": 900}, extra_http_headers=headers)
    ctx.add_init_script(f"localStorage.setItem('festpos.device', JSON.stringify({name!r}))")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    return ctx, page, errs


def _order(page, table="5", taps=2):
    _open_table(page, table)
    for _ in range(taps):
        page.click(".grid .tile >> nth=0")
    page.click("#send")
    page.wait_for_selector('#paystate[data-s="sent"]', timeout=10000)
    page.wait_for_timeout(650)                                      # Schutz gegen Doppeltipp abwarten


def _last(c):
    return c.get("/api/admin/orders?scope=all", auth=AUTH).json()["orders"][0]


def _ignore_net(errs):
    # absichtlich abgewiesene Anfragen (503 im Test) meldet Chromium als Konsolenfehler
    return [e for e in errs if "Failed to load resource" not in e]


def test_payment_off_optional_required(server, browser):  # noqa: F811
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"payment_mode": "off"})
    ctx, page, errs = _phone(browser, base)
    _order(page)
    assert page.locator("#payrow").count() == 0                     # wie bisher: nichts Neues für die Bedienung
    page.click('[data-act="done"]')
    page.wait_for_selector('[data-act="digit"]')
    # Pflicht: «Nächste Bestellung» erst mit Zahlart
    c.put("/api/admin/settings", auth=AUTH, json={"payment_mode": "required"})
    page.evaluate("loadBoot()")
    page.wait_for_function("() => S.boot.payment_mode === 'required'")
    _order(page, "7")
    assert "need" in page.get_attribute("#payrow", "class")
    page.click('[data-act="done"]')
    assert page.locator(".pay-view").count() == 1 and "Zahlart" in page.inner_text(".toast")
    page.click('[data-act="pay-kind"][data-v="twint"]')
    page.wait_for_function("() => !document.querySelector('#payrow').classList.contains('need')")
    page.wait_for_function("() => !payq.length")
    assert _last(c)["payment"] == "twint"
    page.click('[data-act="pay-kind"][data-v="card"]')               # umentscheiden geht bis zum Abschluss
    page.wait_for_function("() => !payq.length")
    assert _last(c)["payment"] == "card"
    page.click('[data-act="done"]')
    page.wait_for_selector('[data-act="digit"]')
    # Verlauf zeigt die Zahlart
    page.click('[data-act="history"]')
    assert "Karte" in page.inner_text(".hist")
    assert not errs, errs
    ctx.close()


def test_payment_waits_offline_and_goes_with_order(server, browser):  # noqa: F811
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"payment_mode": "optional"})
    ctx, page, errs = _phone(browser, base)
    # Server weg, während die Zahlart gewählt wird: bleibt im Handy und wird nachgereicht
    _order(page, "8")
    page.route("**/api/orders/payment", lambda r: r.fulfill(status=503, body="{}"))
    page.click('[data-act="pay-kind"][data-v="cash"]')
    page.wait_for_function("() => payq.length === 1 && JSON.parse(localStorage.getItem('festpos.payq')).length === 1")
    assert _last(c)["payment"] is None
    page.unroute("**/api/orders/payment")
    page.evaluate("flushPay()")
    page.wait_for_function("() => !payq.length")
    assert _last(c)["payment"] == "cash"
    page.click('[data-act="done"]')
    # Bestellung selbst noch im Ausgang: Zahlart geht mit der Bestellung mit
    page.route("**/api/orders", lambda r: r.fulfill(status=503, body="{}"))
    _open_table(page, "9")
    page.click(".grid .tile >> nth=0")
    page.click("#send")
    page.wait_for_selector("#payrow")
    page.click('[data-act="pay-kind"][data-v="twint"]')
    assert page.evaluate("outbox[0].payment") == "twint"
    page.unroute("**/api/orders")
    page.evaluate("flush()")
    page.wait_for_selector('#paystate[data-s="sent"]', timeout=10000)
    last = _last(c)
    assert last["table_label"] == "9" and last["payment"] == "twint"
    assert not _ignore_net(errs), errs
    ctx.close()


def test_phone_storno_only_with_role(server, browser):  # noqa: F811
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"payment_mode": "off"})
    c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "service"})
    ctx, page, errs = _phone(browser, base)
    _order(page, "11", taps=3)
    assert page.locator('[data-act="storno"]').count() == 0          # Rolle «Bedienung»: kein Storno
    page.click('[data-act="done"]')
    c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "storno"})
    page.evaluate("loadBoot()")
    page.wait_for_function("() => S.boot.can_storno === true")
    page.click('[data-act="history"]')
    page.click(".hist button >> nth=0")
    page.click('[data-act="storno"]')
    page.wait_for_selector(".sheet [data-storno]")
    ok = page.locator('.sheet [data-act="storno-ok"]')
    assert ok.is_disabled()
    page.click('.sheet [data-act="storno-q"][data-d="1"] >> nth=0')
    assert ok.is_disabled() and "Grund" in ok.inner_text()             # erst mit Grund
    page.click('.sheet [data-act="storno-reason"][data-v="wrong"]')
    page.fill("#stornonote", "Gast hat gewechselt")
    assert "stornieren" in ok.inner_text()
    ok.click()
    page.wait_for_selector(".sheet", state="detached")
    assert "Storniert" in page.inner_text(".toast")
    assert "Storniert" in page.inner_text(".bon")
    o = _last(c)
    assert o["table_label"] == "11" and o["storno_cents"] == 650
    d = c.get(f"/api/admin/orders/{o['id']}", auth=AUTH).json()
    assert d["cancellations"][0]["by_device"] == "XCover 1" and "gewechselt" in d["cancellations"][0]["reason"]
    jobs = c.get("/api/admin/jobs?limit=50", auth=AUTH).json()
    assert any(j["kind"] == "storno" and j["order_id"] == o["id"] for j in jobs)
    # Rest stornieren: «Alles auswählen» nimmt nur, was noch offen ist
    page.click('[data-act="storno"]')
    page.wait_for_selector(".sheet [data-storno]")
    assert "1 schon storniert" in page.inner_text(".sheet")
    page.click('.sheet [data-act="storno-all"]')
    page.click('.sheet [data-act="storno-reason"][data-v="complaint"]')
    page.click('.sheet [data-act="storno-ok"]')
    page.wait_for_selector(".sheet", state="detached")
    assert _last(c)["storno_cents"] == 3 * 650
    page.click('[data-act="storno"]')
    page.wait_for_selector(".sheet [data-storno]")
    assert "ganz storniert" in page.inner_text(".sheet")
    assert not errs, errs
    ctx.close()


def _admin(browser, base, headers=PC):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, extra_http_headers=headers)
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html")
    page.wait_for_selector("#login")
    return ctx, page, errs


def _login(page):
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector(".side")


def test_admin_orders_storno_and_closing(server, browser):  # noqa: F811
    base, c = server
    ctx, page, errs = _admin(browser, base)
    _login(page)
    page.on("dialog", lambda d: d.accept())
    pids = {p["name"]: p["id"] for p in c.get("/api/bootstrap").json()["products"]}
    oid = c.post("/api/orders", json={"client_uuid": "admin-test-0001", "table": "21", "device": "XCover 2",
                                      "lines": [{"product_id": pids["Mineral"], "qty": 2}]}).json()["order_id"]
    page.click('.side [data-page="orders"]')
    page.wait_for_selector(".ordtbl")
    page.fill("#ordq", "#" + str(oid))
    page.wait_for_function("() => document.querySelectorAll('.ordtbl [data-order]').length === 1")
    page.click(f'[data-order="{oid}"]')
    page.wait_for_selector("dialog[open] #ordform")
    page.select_option("#ordpay", "card")
    page.wait_for_timeout(400)
    assert _last(c)["payment"] == "card"
    page.fill("dialog .sq >> nth=0", "1")
    page.select_option("#sreason", "complaint")
    assert "stornieren" in page.inner_text("#sgo")
    page.click("#sgo")
    page.wait_for_selector("dialog[open] .plainlist")                # neu geöffnet: Storno steht in der Liste
    assert c.get(f"/api/admin/orders/{oid}", auth=AUTH).json()["storno_cents"] == 400
    page.click("#ordclose")
    # Tagesabschluss mit Kassensturz
    page.click('.side [data-page="closing"]')
    page.wait_for_selector("#cashform")
    s = c.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    page.fill('#cashform [name="float"]', "100")
    page.fill('#cashform [name="counted"]', "100")
    exp = 10000 + s["net"] - s["by_payment"].get("card", 0) - s["by_payment"].get("twint", 0) - s["by_payment"].get("other", 0)
    assert page.inner_text("#cashres").count(".") >= 2
    page.click("#cashform button.pri")
    page.wait_for_selector("[data-verify]")
    cl = c.get("/api/admin/closings", auth=AUTH).json()["closings"][0]
    assert cl["expected_cents"] == exp and cl["difference_cents"] == 10000 - exp and cl["orders"] == s["orders"]
    page.click("[data-verify]")
    page.wait_for_selector(".vres.status.ok")
    assert page.locator('[data-dl$="/pdf"]').count() == 1
    page.click('.side [data-page="audit"]')                  # T20: Protokoll ist ein eigener Menüpunkt
    page.wait_for_selector("#auditlist .tbl, #auditlist table")
    assert "Tagesabschluss" in page.inner_text("#auditlist")
    # danach: Bestellungen geschlossen, Storno nicht mehr möglich
    page.click('.side [data-page="orders"]')
    page.click('[data-oscope="all"]')
    page.wait_for_selector(f'[data-order="{oid}"]')
    page.click(f'[data-order="{oid}"]')
    page.wait_for_selector("dialog[open] #ordform")
    assert page.locator("#sgo").count() == 0 and page.locator("#ordpay").is_disabled()
    assert not errs, errs
    ctx.close()


def test_vat_settings_and_device_roles(server, browser):  # noqa: F811
    base, c = server
    ctx, page, errs = _admin(browser, base)
    _login(page)
    page.click('.side [data-page="pos"]')                                # T34: Kasse & App › Kasse
    page.click('.tabs [data-tab="kasse"]')
    page.wait_for_selector("#vatform", state="attached")
    page.click('[data-card="pay"] > summary')
    page.select_option('[data-k="payment_mode"]', "optional")
    page.click('[data-card="vat"] > summary')
    page.check('#vatform [data-k="enabled"]')                             # MWST ein (Schalter)
    page.click("#save-go")                                                # ein Balken speichert beides
    page.wait_for_selector("#savebar", state="hidden")
    page.wait_for_timeout(300)
    assert c.get("/api/admin/settings", auth=AUTH).json()["payment_mode"] == "optional"
    assert c.get("/api/admin/vat", auth=AUTH).json()["enabled"] is True
    page.wait_for_selector("#vat-add")
    page.click("#vat-add")                                                # T28 Stufe 2: neuer Satz im Fenster
    page.wait_for_selector("dialog[open] #vatadd")
    page.select_option('#vatadd [name="code"]', "reduced")
    page.fill('#vatadd [name="rate"]', "2,7")
    page.fill('#vatadd [name="from"]', "2031-01-01")
    page.click('dialog[open] [data-dlg="save"]')
    page.wait_for_selector("[data-vatdel]")
    assert any(r["rate_bp"] == 270 and r["valid_from"] == "2031-01-01" for r in c.get("/api/admin/vat", auth=AUTH).json()["rates"])
    page.click("[data-vatdel]")
    page.wait_for_selector("[data-vatdel]", state="detached")
    # Preisliste: MWST-Spalte für Kategorie und Produkt
    page.click('.side [data-page="products"]')
    page.wait_for_selector("#ptable [data-edit]")
    page.click('.tabs [data-tab="categories"]')                           # Produkte › Kategorien
    page.select_option('#ctable tr >> nth=1 >> [data-f="vat_code"]', "reduced")
    page.wait_for_timeout(300)
    cats = c.get("/api/admin/categories", auth=AUTH).json()
    assert any(x["vat_code"] == "reduced" for x in cats)
    page.click('.tabs [data-tab="products"]')
    page.click("#ptable [data-edit] >> nth=0")                            # T28 Stufe 2: MWST im Fenster
    page.select_option('dialog[open] [data-f="vat"]', "lodging")
    page.click('dialog[open] [data-dlg="save"]')
    page.wait_for_selector("dialog[open]", state="detached")
    page.wait_for_timeout(300)
    assert any(p["vat_code"] == "lodging" for p in c.get("/api/admin/products", auth=AUTH).json())
    c.put("/api/admin/vat", auth=AUTH, json={"enabled": False, "number": ""})
    assert not errs, errs
    ctx.close()


def test_category_colors_match_products(server, browser):  # noqa: F811
    """Kategorie ohne eigene Farbe zeigt die automatische Farbe der Kacheln; Änderungen erscheinen sofort bei den Produkten."""
    base, c = server
    cid = c.post("/api/admin/categories", auth=AUTH, json={"name": "Farbtest", "sort": 999}).json()["id"]
    pid = c.post("/api/admin/products", auth=AUTH, json={"name": "Farbprodukt", "price_cents": 100, "category_id": cid}).json()["id"]
    ctx, page, errs = _admin(browser, base)
    _login(page)
    page.click('.side [data-page="products"]')
    page.wait_for_selector(f'#ptable tr[data-id="{pid}"] .pcolor input')
    cpick, ppick = f'#ctable tr[data-id="{cid}"] [data-f="color"]', f'#ptable tr[data-id="{pid}"] .pcolor input'
    auto = page.input_value(cpick)
    assert auto != "#3a3a3a" and page.input_value(ppick) == auto          # nicht grau, sondern wie die Kachel
    page.click('.tabs [data-tab="categories"]')
    page.fill(f'#ctable tr[data-id="{cid}"] [data-f="name"]', "Farbtest 2")
    page.locator(f'#ctable tr[data-id="{cid}"] [data-f="name"]').blur()     # echtes change, kein zweites beim Klick später
    page.wait_for_timeout(300)
    assert next(x for x in c.get("/api/admin/categories", auth=AUTH).json() if x["id"] == cid)["color"] == "#3a3a3a"
    page.eval_on_selector(cpick, "el => { el.value = '#dddd22'; el.dispatchEvent(new Event('change', {bubbles: true})); }")
    page.wait_for_selector(f'#ctable tr[data-id="{cid}"] [data-color-auto]')
    assert next(x for x in c.get("/api/admin/categories", auth=AUTH).json() if x["id"] == cid)["color"] == "#dddd22"
    assert page.input_value(ppick) == "#dddd22"                           # Produkt erbt ohne Neuladen
    page.click(f'#ctable tr[data-id="{cid}"] [data-color-auto]')            # ↺ = wieder automatisch
    page.wait_for_selector(f'#ctable tr[data-id="{cid}"] [data-color-auto]', state="detached")
    assert next(x for x in c.get("/api/admin/categories", auth=AUTH).json() if x["id"] == cid)["color"] == "#3a3a3a"
    assert page.input_value(ppick) == auto
    assert not errs, errs
    ctx.close()
    c.delete(f"/api/admin/products/{pid}", auth=AUTH)
    c.delete(f"/api/admin/categories/{cid}", auth=AUTH)


def _side(page, sel):
    """Eintrag der Seitenleiste anklicken – am Handy steckt er im Menü (☰, T5)."""
    item = page.locator(f".side {sel}")
    if not item.is_visible() and page.locator(".side .menu-btn").is_visible():
        page.click(".side .menu-btn")
    item.click()


def test_links_between_app_admin_and_status(server, browser):  # noqa: F811
    base, c = server
    # Bestell-App -> Verwaltung (gleicher Server und Port, auch in Fully)
    ctx, page, errs = _phone(browser, base)
    page.click('[data-act="settings"]')
    page.click('[data-act="settings-more"]')
    page.click('.navlinks a[href="/admin.html"]')
    page.wait_for_selector("#login")
    # Anmeldung der Verwaltung -> zurück zur Bestell-App (wichtig im Kiosk-Modus: kein Adressfeld)
    page.click('.login-jump [data-jump="app"]')
    page.wait_for_selector('[data-act="digit"]')
    # Verwaltung (angemeldet) -> Statusanzeige -> Verwaltung / Bestell-App
    page.goto(base + "/admin.html")
    _login(page)
    assert page.locator('.side [data-jump="app"]').count() == 1
    # ohne Fully und ohne Handy-Port (hier ein Server auf einem Port): Statusanzeige im eigenen Tab
    with page.expect_popup() as pop:
        _side(page, '[data-jump="status"]')
    st = pop.value
    st.wait_for_selector("#jump:not([hidden])")
    assert st.is_visible("#j-admin") and st.is_visible("#j-app")
    st.click("#j-app")
    st.wait_for_selector('[data-act="digit"]')
    assert st.evaluate("sessionStorage.getItem('festpos.token')") is None   # in die Bestell-App = abgemeldet
    _side(page, '[data-page="orders"]')                              # im ersten Tab weiterhin angemeldet
    page.wait_for_selector("#ordq")
    assert not errs, errs
    ctx.close()
    # in Fully: alles im selben Fenster (kein Tab-Wechsel im Kiosk-Modus)
    ctx = browser.new_context(viewport={"width": 412, "height": 900}, extra_http_headers=PHONE)
    ctx.add_init_script("window.fully = {};")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html")
    _login(page)
    _side(page, '[data-jump="status"]')
    page.wait_for_selector("#jump:not([hidden])")
    page.click("#j-admin")
    page.wait_for_selector(".side")                                  # noch angemeldet (gleiches Fenster)
    _side(page, '[data-jump="app"]')                                 # zur Bestell-App: abgemeldet
    page.wait_for_selector('[data-act="digit"]')
    page.goto(base + "/admin.html")
    page.wait_for_selector("#login")
    assert len(ctx.pages) == 1
    assert not errs, errs
    ctx.close()
    # Am PC ohne Fully: keine Bestell-App am Verwaltungs-Port, Statusanzeige im eigenen Tab – hier ein Server auf einem
    # Port, also ist die Bestell-App erreichbar; der Bildschirm am Pi bekommt nie Links
    kiosk = browser.new_context(viewport={"width": 800, "height": 480})
    k = kiosk.new_page()
    kerrs = _watch(k)
    k.goto(base + "/status.html")
    k.wait_for_function("() => document.getElementById('k-orders').textContent !== '0' || document.querySelector('#printers .printer')")
    k.wait_for_timeout(500)
    assert k.locator("#jump").is_hidden()
    assert not kerrs, kerrs
    kiosk.close()


def test_status_on_phone_one_column_orders_last(server, browser):  # noqa: F811
    """Am Handy hochkant (nicht am Pi): Drucker, Handys, Netzwerk untereinander, die Bestellungen zuletzt."""
    base, c = server
    port = base.rsplit(":", 1)[1]
    b2 = browser.browser_type.launch(args=["--host-resolver-rules=MAP festpos.test 127.0.0.1"])   # nicht «am Pi»
    try:
        ctx = b2.new_context(viewport={"width": 412, "height": 900}, extra_http_headers=PC)
        page = ctx.new_page()
        errs = _watch(page)
        page.goto(f"http://festpos.test:{port}/admin.html")
        _login(page)
        page.goto(f"http://festpos.test:{port}/status.html")
        page.wait_for_selector("#orders .order")
        assert "phone" in page.evaluate("document.documentElement.className")
        y = {k: page.locator(sel).bounding_box() for k, sel in
             (("prn", "#printers"), ("dev", ".phones"), ("net", ".net"), ("log", ".log"))}
        assert y["prn"]["y"] < y["dev"]["y"] < y["net"]["y"] < y["log"]["y"]
        assert y["log"]["width"] > 380                                    # Bestellungen über die ganze Breite
        assert page.locator("#orders .order .where small").first.is_visible()   # Gerät (Bedienung) sichtbar
        assert not errs, errs
        ctx.close()
    finally:
        b2.close()


def test_closing_with_tips(server, browser):  # noqa: F811
    """Trinkgeld im Tagesabschluss: eigene Karte (pro Zahlart, Total fürs Personal), Spalte pro Handy, Karte mit
    Trinkgeld vorausgefüllt, «Trinkgeld ausbezahlt (bar)» zählt im Bargeld laut Kasse mit."""
    from tests.test_browser_receipt import _until
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"tips_enabled": True})
    pids = {p["name"]: p["id"] for p in c.get("/api/bootstrap").json()["products"]}
    for cu, pay, tip in (("tip-browser-0001", "cash", 300), ("tip-browser-0002", "card", 150)):
        c.post("/api/orders", json={"client_uuid": cu, "table": "30", "device": "Tina", "payment": pay,
                                    "lines": [{"product_id": pids["Mineral"], "qty": 1}]})
        assert c.post("/api/orders/tip", headers=PHONE, json={"client_uuid": cu, "tip_cents": tip}).status_code == 200
    s = c.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    ctx, page, errs = _admin(browser, base)
    _login(page)
    page.on("dialog", lambda d: d.accept())
    page.click('.side [data-page="closing"]')
    page.wait_for_selector("#tipcard")
    card_txt = " ".join(page.inner_text("#tipcard").split())
    assert "Trinkgeld Bar 3.00" in card_txt and "Trinkgeld Karte" in card_txt and "Total fürs Personal 4.50" in card_txt
    card = s["by_payment"].get("card", 0) + 150
    assert page.input_value('#cashform [name="card"]') == f"{card / 100:.2f}"           # mit Trinkgeld
    page.fill('#cashform [name="tipout"]', "4.50")                                     # ganzes Trinkgeld ausbezahlt
    exp = s["net"] + 450 - card - s["by_payment"].get("twint", 0) - s["by_payment"].get("other", 0) - 450
    page.fill('#cashform [name="counted"]', f"{exp / 100:.2f}")
    _until(page, "document.querySelector('#cashres .pos') !== null")
    page.click("#cashform button.pri")
    page.wait_for_selector("#tipcard", state="detached")
    cl = c.get("/api/admin/closings", auth=AUTH).json()["closings"][0]
    assert cl["expected_cents"] == exp and cl["difference_cents"] == 0
    c.put("/api/admin/settings", auth=AUTH, json={"tips_enabled": False})
    assert not errs, errs
    ctx.close()


def _storno_sheet(page, picks: dict[int, int], reason="wrong"):
    page.click('[data-act="storno"]')
    page.wait_for_selector(".sheet [data-storno]")
    for row, n in picks.items():
        for _ in range(n):
            page.click(f'.sheet [data-act="storno-q"][data-d="1"] >> nth={row}')
    page.click(f'.sheet [data-act="storno-reason"][data-v="{reason}"]')
    page.click('.sheet [data-act="storno-ok"]')
    page.wait_for_selector(".sheet", state="detached")


def test_phone_storno_shows_on_bon_and_in_einzeln(server, browser):  # noqa: F811
    """Nach dem Storno zeigt der Bon in der Kasse, was noch gilt: teilweise «1 storniert», ganz durchgestrichen;
    bei «Einzeln» lässt sich nur noch wählen, was gilt."""
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"payment_mode": "off"})
    c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "storno"})
    ctx, page, errs = _phone(browser, base)
    _open_table(page, "14")
    for n in (0, 0, 1):                                  # 2× Bier 5dl, 1× Mineral
        page.click(f".grid .tile >> nth={n}")
    page.click("#send")
    page.wait_for_selector('#paystate[data-s="sent"]', timeout=10000)
    page.wait_for_timeout(650)
    _storno_sheet(page, {0: 1, 1: 1})                    # 1 Bier und das Mineral
    bon = page.inner_text("#bon")
    assert "1× Bier 5dl" in bon and "1 storniert" in bon and "6.50" in bon
    assert page.locator("#bon .line.gone").count() == 1 and "Mineral" in page.inner_text("#bon .line.gone")
    assert "Storniert 10.50" in bon
    # bleibt nach dem Neuladen (Verlauf im Handy)
    page.reload()
    page.wait_for_selector('[data-act="digit"]')
    page.click('[data-act="history"]')
    page.click(".hist button >> nth=0")
    page.wait_for_selector("#bon .line.gone")
    page.click('[data-act="mode"][data-m="part"]')
    page.wait_for_selector("#pickpanel")
    assert page.locator(".pi").count() == 1 and "1 von 1 offen" in page.inner_text(".pi")
    page.click('[data-act="ez-all"]')
    assert "6.50" in page.inner_text("#payfoot")
    assert not errs, errs
    c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "service"})
    ctx.close()
