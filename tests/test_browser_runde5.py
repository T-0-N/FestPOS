"""Rückmeldung (Runde 5, Mockups freigegeben):
2. QR fürs Personal ein-/ausschaltbar (Standard aus): aus = ✕ in der Tischwahl (löscht alles), kein Schritt «QR» in der
   Einführung; der Scanner liest auch die QR-Codes der Gäste (?t=7) – ein Code pro Tisch für beide.
3. Produkte und Kategorien per Drag & Drop (Griff ⠿) oder ↑/↓ sortieren, Produkte nach Kategorie gruppiert.
5. PIN-Eingabe: kleine Punkte ● getippt / ○ fehlt noch, Text einzeilig – auch 8-stellig auf 360 px.
Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_api import AUTH  # noqa: E402
from tests.test_browser_kasse import PHONE  # noqa: E402
from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("runde5"), target="tests.asgi_testphone:app")
    yield base, c
    proc.terminate()
    proc.wait(10)


def _phone(browser, base, width=412):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": 892}, has_touch=True, extra_http_headers=PHONE)
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('XCover 1'))")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    _until(page, "S.boot != null")
    return ctx, page, errs


def test_staff_qr_off_shows_clear_and_on_shows_scan(server, browser):  # noqa: F811
    base, c = server
    assert c.get("/api/bootstrap", headers=PHONE).json()["staff_qr"] is False            # Standard: aus
    ctx, page, errs = _phone(browser, base)
    assert page.locator('[data-act="scan"]').count() == 0
    for d in "123":
        page.click(f'[data-act="digit"][data-d="{d}"]')
    assert page.inner_text("#tin") == "123"
    page.click('[data-act="digit-clear"]')                                             # ✕ löscht alles
    assert page.inner_text("#tin") == ""
    assert page.evaluate("tourSteps().some((s) => s.sel === '[data-act=\"scan\"]')") is False
    # Gäste-QR (QR-Bestellung am Tisch) und Tischkarten fürs Personal: beide geben den Tisch
    assert page.evaluate("parseTableCode('https://bestellen.example.ch/?t=7&s=AbC123xyz')") == "7"
    assert page.evaluate("parseTableCode('https://bestellen.example.ch/?t=Bar%201&s=x')") == "Bar 1"
    assert page.evaluate("parseTableCode('tisch:12')") == "12"
    ctx.close()
    assert c.put("/api/admin/settings", auth=AUTH, json={"staff_qr": True}).status_code == 200
    ctx, page, errs2 = _phone(browser, base)
    assert page.locator('[data-act="scan"]').count() == 1 and page.locator('[data-act="digit-clear"]').count() == 0
    assert page.evaluate("tourSteps().some((s) => s.sel === '[data-act=\"scan\"]')") is True
    c.put("/api/admin/settings", auth=AUTH, json={"staff_qr": False})
    assert not errs and not errs2, errs + errs2
    ctx.close()


def _admin(browser, base):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 1280, "height": 640}, extra_http_headers={"X-Test-Client": "192.168.55.50"})   # niedrig: es muss gescrollt werden
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html")
    page.wait_for_selector("#login")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector(".side")
    return ctx, page, errs


def _drag(page, src, dst, below=False):
    page.locator(dst).scroll_into_view_if_needed()                       # CI: andere Schriften, Tabelle tiefer
    page.locator(src).scroll_into_view_if_needed()
    a, b = page.locator(src).bounding_box(), page.locator(dst).bounding_box()
    page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
    page.mouse.down()
    y = b["y"] + b["height"] * (0.8 if below else 0.2)
    page.mouse.move(b["x"] + b["width"] / 2, y, steps=8)
    page.mouse.up()


def _api_until(page, get, ok, tries=100):
    """Speichern läuft im Hintergrund: so lange nachfragen, bis die API den neuen Stand hat."""
    for _ in range(tries):
        v = get()
        if ok(v):
            return v
        page.wait_for_timeout(100)
    raise AssertionError(f"nicht gespeichert: {get()}")


def test_drag_and_drop_products_and_categories(server, browser):  # noqa: F811
    base, c = server
    for name in ("Rivella", "Cola"):
        cat = next(x["id"] for x in c.get("/api/admin/categories", auth=AUTH).json() if x["name"] == "Getränke")
        c.post("/api/admin/products", auth=AUTH, json={"name": name, "price_cents": 450, "category_id": cat, "sort": 99})
    ctx, page, errs = _admin(browser, base)
    page.goto(page.url.split("#")[0] + "#products")
    page.reload()
    page.wait_for_selector("#ptable .grip")
    assert page.locator('#ptable input[data-f="sort"]').count() == 0                   # keine Nummern mehr
    groups = [" ".join(x.split()) for x in page.locator("#ptable tr.grp td").all_inner_texts()]
    assert [g.split("Reihenfolge")[0].strip() for g in groups] == ["Getränke", "Essen"]
    names = lambda: [page.inner_text(f'#ptable tr[data-id] >> nth={i} >> .p-name')            # noqa: E731
                     for i in range(page.locator("#ptable tr[data-id]").count())]
    before = names()
    cola = before.index("Cola")
    _drag(page, f'#ptable tr[data-id] >> nth={cola} >> .grip', "#ptable tr[data-id] >> nth=0")   # Cola ganz nach oben
    _until(page, "document.querySelector('#ptable tr[data-id] .p-name')?.textContent === 'Cola'")   # nach dem Landen
    assert not page.locator(".dr-ghost, #ptable .dr-hole").count()                    # schwebende Kopie wieder weg
    api = _api_until(page, lambda: [p["name"] for p in c.get("/api/admin/products", auth=AUTH).json()], lambda x: x[0] == "Cola")
    assert api[0] == "Cola" and api.index("Bratwurst") > api.index("Rivella")         # Essen bleibt nach Getränken
    # nicht in eine andere Kategorie ziehen
    brat = names().index("Bratwurst")
    _drag(page, f'#ptable tr[data-id] >> nth={brat} >> .grip', "#ptable tr[data-id] >> nth=0")
    page.wait_for_timeout(300)
    assert names().index("Bratwurst") == brat
    # Tastatur: ↓ am Griff
    page.focus("#ptable tr[data-id] >> nth=0 >> .grip")
    page.keyboard.press("ArrowDown")
    _until(page, "document.querySelector('#ptable tr[data-id] .p-name').textContent !== 'Cola'")
    _api_until(page, lambda: [p["name"] for p in c.get("/api/admin/products", auth=AUTH).json()], lambda x: x[1] == "Cola")
    # Kategorien: Essen vor Getränke ziehen -> Gruppen und Bestell-App folgen
    page.click('#main .tabs [data-tab="categories"]')
    page.wait_for_selector("#ctable .grip")
    _drag(page, "#ctable tr[data-id] >> nth=1 >> .grip", "#ctable tr[data-id] >> nth=0")
    _until(page, "document.querySelector('#ctable tr[data-id] [data-f=name]')?.value === 'Essen'")
    _api_until(page, lambda: [x["name"] for x in c.get("/api/admin/categories", auth=AUTH).json()][:2],
               lambda x: x == ["Essen", "Getränke"])
    page.click('#main .tabs [data-tab="products"]')
    _until(page, "document.querySelector('#ptable tr.grp td')?.textContent.startsWith('Essen')")   # Gruppen folgen
    assert [x["name"] for x in c.get("/api/bootstrap", headers=PHONE).json()["categories"]][:2] == ["Essen", "Getränke"]
    log = c.get("/api/admin/audit", auth=AUTH).json()["entries"]
    assert {"products_order", "categories_order"} <= {e["action"] for e in log}
    assert not errs, errs
    ctx.close()


@pytest.fixture(scope="module")
def pin_server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("pin8"), target="tests.asgi_testphone:app")
    c.put("/api/admin/settings", auth=AUTH, json={"pin_enabled": True, "pin": "24681357"})
    yield base
    proc.terminate()
    proc.wait(10)


def test_pin_dots_fit_eight_digits_at_360(pin_server, browser):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 360, "height": 780}, extra_http_headers=PHONE)
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('XCover 1'))")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(pin_server + "/index.html")
    page.wait_for_selector("#lock #pinout")
    assert page.inner_text("#pinout") == "○" * 8
    for d in "24681":
        page.click(f'#lock [data-act="pin"][data-d="{d}"]')
    assert page.inner_text("#pinout") == "●●●●●○○○"
    span = "document.querySelector('#lock .table-display span')"
    assert page.evaluate(f"{span}.getClientRects().length") == 1
    assert page.evaluate(f"{span}.scrollWidth <= {span}.clientWidth")                   # «PIN eingeben» ganz, einzeilig
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    for d in "357":
        page.click(f'#lock [data-act="pin"][data-d="{d}"]')
    page.wait_for_selector("#lock", state="detached")                                 # richtig: entsperrt
    assert not errs, errs
    ctx.close()
