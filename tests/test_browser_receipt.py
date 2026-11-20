"""Im Browser (XCover-Grösse): Kopf mit Status-Punkt (Server und Drucker, Version -31), WLAN neu verbinden solange eine
Bestellung wartet (-30), Quittung an der Kasse und pro Person bei «Einzeln» (-30/-31).
Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_api import AUTH  # noqa: E402
from tests.test_browser_p4 import _open_table, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)

PHONE = {"X-Test-Cert": "XCover 1", "X-Test-Client": "192.168.55.101"}
FULLY = """window.fully = { isWifiConnected: () => window.__wifi !== false, isWifiEnabled: () => window.__wifiEnabled !== false,
  getWifiSignalLevel: () => String(window.__level ?? 3), getWifiSsid: () => '"FestPOS"', getBatteryLevel: () => window.__bat ?? 78,
  isPlugged: () => !!window.__plugged, bind: () => {}, getScreenBrightness: () => 128, setScreenBrightness: () => {},
  enableWifi: () => { window.__on = (window.__on || 0) + 1; }, disableWifi: () => { window.__off = (window.__off || 0) + 1; } };"""


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("rcpt"), target="tests.asgi_testphone:app")
    pid = c.get("/api/admin/printers", auth=AUTH).json()[0]["id"]
    c.put("/api/admin/settings", auth=AUTH, json={"receipt_printer_id": pid, "receipt_header": "Verein Muster"})
    yield base, c
    proc.terminate()
    proc.wait(10)


def _phone(browser, base, fully=True, **kw):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 412, "height": 892}, has_touch=True, extra_http_headers=PHONE, **kw)
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('Theke 1'))")
    if fully:
        ctx.add_init_script(FULLY)
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    return ctx, page, errs


def _until(page, js: str, timeout_ms: int = 6000) -> None:
    """Warten, bis js im Browser wahr ist (wait_for_function geht nicht: die App erlaubt kein eval, CSP)."""
    for _ in range(timeout_ms // 100):
        if page.evaluate(js):
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"nicht erfüllt: {js}")


def _chip(page) -> list[str]:
    return page.evaluate("""(() => { const e = document.getElementById('stchip');
        return [e.dataset.s, e.innerText.replace(/\\s+/g, ' ').replace('›', '').trim(), !!e.querySelector('img.logo')]; })()""")


PRN_OK = "{name:'Getränkeausgabe',state:'ok',code:null,target:null,waiting:0}"


def test_header_status_dot_logo_and_settings(server, browser):  # noqa: F811
    base, _ = server
    ctx, page, errs = _phone(browser, base)
    page.wait_for_timeout(700)
    # Platzwahl: ☰ und Name links, rechts Punkt mit Logo und Verlauf; WLAN, Akku und Uhrzeit nicht mehr
    assert page.inner_text(".top .who") == "Theke 1"
    assert _chip(page) == ["ok", "", False]                      # kein Logo eingerichtet: nur der Punkt
    assert page.locator(".top [data-act='history']").count() == 1 and page.locator(".pstat").count() == 0
    size = page.evaluate("parseFloat(getComputedStyle(document.querySelector('.table-display span')).fontSize)")
    assert size >= 26                                            # «Tischnummer» gross
    # Name antippen: Einstellungen, Name markiert
    page.click(".top .who")
    page.wait_for_selector("#devname")
    _until(page, "document.activeElement && document.activeElement.id === 'devname'")
    page.evaluate("closeSheet()")
    page.click(".top .menu")                                     # ☰: Einstellungen
    page.wait_for_selector("#devname")
    page.evaluate("closeSheet()")
    # Drucker umgeleitet: orange, Hinweis statt Logo; Ausfall: rot; mehrere: Anzahl
    page.evaluate(f"PST.printers = [{PRN_OK}, {{name:'Küche',state:'rerouted',code:'paper_out',target:'Getränkeausgabe',waiting:0}}]; refreshStatus()")
    assert _chip(page) == ["warn", "Küche Umgeleitet", False]
    page.evaluate(f"PST.printers = [{PRN_OK}, {{name:'Küche',state:'down',code:'paper_out',target:null,waiting:3}}]; refreshStatus()")
    assert _chip(page) == ["bad", "Küche Ausfall", False]
    page.evaluate("PST.printers = PST.printers.map(p => ({...p, state: 'down', code: 'unreachable'})); refreshStatus()")
    assert _chip(page) == ["bad", "2 Drucker Ausfall", False]
    # antippen: nur Server und Drucker, von dort in die Einstellungen
    page.click("#stchip")
    page.wait_for_selector("#statussheet")
    txt = page.inner_text("#statussheet")
    assert "Server" in txt and "Küche: Ausfall (nicht erreichbar)" in txt and "wartend: 3" in txt
    assert "WLAN" not in txt and "Akku" not in txt and "Uhrzeit" not in txt
    page.click('.sheet [data-act="settings"]')
    page.wait_for_selector("#devname")
    page.evaluate("closeSheet()")
    # andere Seiten: Zurück + Titel, rechts Status und Verlauf (im Verlauf selbst ohne Verlauf-Knopf)
    page.evaluate(f"PST.printers = [{PRN_OK}]")
    _open_table(page, "12")
    assert page.inner_text(".top h1").startswith("Tisch 12") and page.locator(".top #stchip").count() == 1
    assert page.locator(".top [data-act='history']").count() == 1
    page.click(".top [data-act='history']")
    assert page.inner_text(".top h1") == "Verlauf" and page.locator(".top [data-act='history']").count() == 0
    assert page.locator(".top #stchip").count() == 1
    assert not errs, errs
    ctx.close()


# Start der App fertig: Einstellungen und erster Status sind da. Sonst kann eine Antwort, die vor dem «Server weg»
# unterwegs war, den Status nachträglich wieder auf «wartet» setzen (warn statt bad – in der CI zufällig gesehen).
STARTED = "!!S.boot && PST.at > 0"


def test_offline_shows_in_status(server, browser):  # noqa: F811
    base, _ = server
    ctx, page, errs = _phone(browser, base, fully=False)
    _until(page, STARTED)
    page.route("**/api/**", lambda route: route.abort())
    page.evaluate("window.pollStatus = () => {}; outbox.push({client_uuid: 'x-1', table: '5', lines: []}, {client_uuid: 'x-2', table: '6', lines: []}); setNet('off')")
    assert _chip(page) == ["bad", "Offline 2 warten", False]
    page.evaluate("outbox.length = 0; refreshStatus()")
    assert _chip(page) == ["bad", "Offline kein Server", False]
    page.evaluate("outbox.push({client_uuid: 'x-3', table: '5', lines: []}); S.net = 'ok'; refreshStatus()")
    assert _chip(page) == ["warn", "Senden 1 wartet", False]       # wird gerade gesendet
    page.unroute("**/api/**")
    ctx.close()


def test_offline_with_waiting_order_reconnects_wifi(server, browser):  # noqa: F811
    base, _ = server
    ctx, page, errs = _phone(browser, base)
    _until(page, STARTED)
    page.route("**/api/**", lambda route: route.abort())         # Server weg
    page.evaluate("outbox.push({client_uuid: 'x-1', table: '5', lines: []}); setNet('off')")
    assert _chip(page)[0] == "bad"
    page.evaluate("NETFIX.offSince = Date.now() - 5000; netFix()")
    assert page.evaluate("window.__off || 0") == 0               # erst nach 20 Sekunden
    page.evaluate("NETFIX.offSince = Date.now() - 25000; netFix()")
    assert page.evaluate("window.__off") == 1
    page.wait_for_timeout(3300)
    assert page.evaluate("window.__on") == 1                     # nach 3 s wieder eingeschaltet
    page.evaluate("netFix()")
    assert page.evaluate("window.__off") == 1                    # höchstens alle 90 Sekunden
    page.evaluate("window.__wifiEnabled = false; NETFIX.lastKick = 0; netFix()")
    assert page.evaluate("window.__on") == 2                     # WLAN war aus: einschalten
    assert page.evaluate("setTimeout.toString().length > 0")
    # ohne wartende Bestellung: nichts
    page.evaluate("outbox.length = 0; window.__wifiEnabled = true; NETFIX.lastKick = 0; NETFIX.offSince = Date.now() - 99999; netFix()")
    assert page.evaluate("window.__off") == 1
    page.unroute("**/api/**")
    ctx.close()


def _order(page, table="12"):
    _open_table(page, table)
    tiles = page.locator(".grid .tile")
    for _ in range(2):
        tiles.nth(0).click()
    tiles.nth(1).click()
    page.click("#send")
    page.wait_for_selector('#paystate[data-s="sent"]', timeout=10000)
    page.wait_for_timeout(700)


def test_receipt_at_the_till_and_per_person(server, browser):  # noqa: F811
    base, c = server
    ctx, page, errs = _phone(browser, base)
    _order(page)
    assert page.inner_text("#rcptbtn").strip() == "Quittung"
    page.click("#rcptbtn")
    page.wait_for_selector(".toast")
    assert "Quittung Nr." in page.inner_text(".toast") and "Getränkeausgabe" in page.inner_text(".toast")
    _until(page, "document.querySelector('#rcptbtn')?.innerText.trim() === 'Kopie'")
    page.click("#rcptbtn")
    _until(page, "document.querySelector('.toast')?.innerText.includes('Kopie von Quittung')")
    # Einzeln: kein Knopf unten, dafür pro Person
    page.click('[data-act="mode"][data-m="part"]')
    assert page.locator("#rcptbtn").count() == 0
    page.click('[data-act="mode"][data-m="change"]')
    page.wait_for_timeout(700)
    page.click('[data-act="done"]')
    _order(page, "13")
    page.click('[data-act="mode"][data-m="part"]')
    page.locator('#pickpanel [data-act="pick"]').nth(1).click()  # Mineral
    page.click('[data-act="ez-next"]')
    page.click('[data-act="part-pay"]')
    page.click(".pc.done")                                       # P1 antippen: Quittung
    page.evaluate("document.querySelectorAll('.toast').forEach((e) => e.remove())")
    page.click('.sheet [data-act="receipt-part"]')
    _until(page, "document.querySelector('.toast')?.innerText.startsWith('Quittung Nr.')")
    page.click(".pc.done")
    assert page.locator('.sheet [data-act="part-undo"]').count() == 0      # mit Quittung nicht mehr zurücknehmen
    assert "Kopie" in page.inner_text('.sheet [data-act="receipt-part"]')
    page.evaluate("closeSheet()")
    orders = c.get("/api/admin/orders", auth=AUTH).json()
    rows = orders["orders"] if isinstance(orders, dict) else orders
    last = max(rows, key=lambda o: o["id"])
    d = c.get(f"/api/admin/orders/{last['id']}", auth=AUTH).json()
    assert [(r["part"], r["total_cents"]) for r in d["receipts"]] == [(1, 400)]
    # Verlauf: Hinweis «Quittung» (Zurück-Pfeil: Rest offen -> «Trotzdem weiter»)
    page.click('[data-act="back"]')
    page.click('.sheet [data-act="back"]')
    page.click('[data-act="history"]')
    assert page.locator('.hist .paytag', has_text="Quittung").count() == 2
    assert not errs, errs
    ctx.close()


def test_receipt_per_person_when_splitting(server, browser):  # noqa: F811
    """Teilen pro Person – nach «Alles bezahlt» pro Person eine Quittung über ihren Anteil; danach lässt
    sich diese Person nicht mehr zurücknehmen (fortlaufende Nummer), die andere schon."""
    base, c = server
    ctx, page, errs = _phone(browser, base)
    _order(page, "15")                                           # 2 × Bier, Mineral = 17.00
    page.click('[data-act="mode"][data-m="split"]')
    for _ in range(2):
        page.click('#payfoot [data-act="split-go"]')
        page.click('#payfoot [data-act="split-paid"]')
    page.wait_for_selector("#splitdone")
    page.evaluate("document.querySelectorAll('.toast').forEach((e) => e.remove())")
    page.click('#splitdone [data-act="receipt-split"][data-n="1"]')
    _until(page, "document.querySelector('.toast')?.innerText.startsWith('Quittung Nr.')")
    page.click('#splitdone [data-act="ez-person"] >> nth=0')
    assert page.locator('.sheet [data-act="part-undo"]').count() == 0 and "Quittung" in page.inner_text(".sheet")
    page.evaluate("closeSheet()")
    page.click('#splitdone [data-act="ez-person"] >> nth=1')
    assert page.locator('.sheet [data-act="part-undo"]').count() == 1
    page.evaluate("closeSheet()")
    orders = c.get("/api/admin/orders", auth=AUTH).json()
    rows = orders["orders"] if isinstance(orders, dict) else orders
    last = max(rows, key=lambda o: o["id"])
    d = c.get(f"/api/admin/orders/{last['id']}", auth=AUTH).json()
    assert [(r["part"], r["total_cents"]) for r in d["receipts"]] == [(1, 850)]
    assert not errs, errs
    ctx.close()
