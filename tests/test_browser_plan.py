"""Tischplan im Browser: Editor in der Verwaltung (Bereich, Grundriss, Tische setzen/ziehen, Reihe, Rückgängig) und
Tischwahl am Handy (Umschalter Plan | Nummer, Bereiche, Zoom, Auswahl bei engen Tischen, 360 px).
Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import io

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import AUTH, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402

PHONE = {"X-Test-Cert": "XCover 1", "X-Test-Client": "192.168.55.101"}


def _png(w: int, h: int) -> bytes:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), "white")
    ImageDraw.Draw(im).rectangle((10, 10, w - 10, h - 10), outline="black", width=8)
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("plan"), target="tests.asgi_testphone:app")
    yield base, c
    proc.terminate()
    proc.wait(10)


def _plan(c, tables: dict[str, tuple]) -> int:
    """Bereich «Saal» mit Grundriss und diesen Tischen {Label: (x, y, w, h, shape)}; liefert die Bereichs-ID."""
    p = c.get("/api/admin/plan", auth=AUTH).json()
    for a in p["areas"]:
        c.delete(f"/api/admin/plan/areas/{a['id']}", auth=AUTH)
    p = c.post("/api/admin/plan/areas", auth=AUTH, json={"name": "Saal"}).json()
    aid = p["areas"][0]["id"]
    c.post(f"/api/admin/plan/areas/{aid}/image", auth=AUTH, files={"file": ("g.png", _png(500, 700), "image/png")})
    ids = {x["label"]: x["id"] for x in p["tables"]}
    r = c.put(f"/api/admin/plan/areas/{aid}", auth=AUTH, json={"tables": [
        {"id": ids[k], "x": v[0], "y": v[1], "w": v[2], "h": v[3], "shape": v[4]} for k, v in tables.items()]})
    assert r.status_code == 200, r.text
    c.put("/api/admin/plan/settings", auth=AUTH, json={"start": "plan"})
    return aid


def _phone(browser, base, width=412):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": 892}, extra_http_headers=PHONE, has_touch=True)
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('Plan'))")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    return ctx, page, errs


def test_phone_picks_table_in_plan_and_remembers_view(server, browser):  # noqa: F811
    base, c = server
    _plan(c, {"1": (100, 300, 60, 240, "bench"), "2": (400, 300, 60, 240, "bench"), "3": (700, 900, 90, 90, "round")})
    ctx, page, errs = _phone(browser, base)
    page.wait_for_selector("#plwrap .pl-t")
    assert page.locator('[data-act="table-view"][data-v="plan"]').get_attribute("aria-pressed") == "true"
    assert page.locator("#pl img").count() == 1                         # Grundriss
    assert page.locator("#plwrap .pl-t").count() == 3
    # Tisch antippen öffnet ihn
    page.click('[data-act="plan-table"][data-t="2"]')
    page.wait_for_selector(".grid .tile")
    assert page.evaluate("S.table") == "2"
    page.go_back()
    page.wait_for_selector("#plwrap")
    # Zoom: + vergrössert, ⤢ zeigt wieder alles
    w0 = page.evaluate("document.getElementById('pl').offsetWidth")
    page.click('[data-act="plan-zoom"][data-d="in"]')
    assert page.evaluate("document.getElementById('pl').offsetWidth") > w0 * 1.5
    page.click('[data-act="plan-zoom"][data-d="fit"]')
    assert page.evaluate("document.getElementById('pl').offsetWidth") == w0
    # Nummer gewählt: bleibt nach dem Neuladen
    page.click('[data-act="table-view"][data-v="number"]')
    page.wait_for_selector('[data-act="digit"]')
    page.reload()
    page.wait_for_selector('[data-act="digit"]')
    assert page.locator('[data-act="table-view"][data-v="number"]').get_attribute("aria-pressed") == "true"
    page.click('[data-act="table-view"][data-v="plan"]')
    page.wait_for_selector("#plwrap .pl-t")
    assert errs == [], errs
    ctx.close()


def test_phone_asks_when_tables_overlap_and_fits_360(server, browser):  # noqa: F811
    base, c = server
    _plan(c, {"1": (450, 600, 60, 60, "round"), "2": (480, 620, 60, 60, "round"), "3": (100, 100, 120, 80, "rect")})
    ctx, page, errs = _phone(browser, base, width=360)
    page.wait_for_selector("#plwrap .pl-t")
    assert page.evaluate("document.scrollingElement.scrollWidth") <= 360
    # zwischen die Nummern 1 und 2 tippen: die App fragt, welcher Tisch
    b1 = page.locator('[data-t="1"] b').bounding_box()
    b2 = page.locator('[data-t="2"] b').bounding_box()
    page.mouse.click((b1["x"] + b1["width"] / 2 + b2["x"] + b2["width"] / 2) / 2, (b1["y"] + b1["height"] / 2 + b2["y"] + b2["height"] / 2) / 2)
    page.wait_for_selector(".sheet .pl-which")
    assert page.locator(".sheet .pl-which button").count() == 2
    page.click('.sheet .pl-which [data-t="2"]')
    page.wait_for_selector(".grid .tile")
    assert page.evaluate("S.table") == "2"
    assert errs == [], errs
    ctx.close()


def test_without_plan_table_choice_is_unchanged(server, browser):  # noqa: F811
    base, c = server
    _plan(c, {"1": (100, 100, 60, 60, "round")})
    c.put("/api/admin/plan/settings", auth=AUTH, json={"start": "off"})
    ctx, page, errs = _phone(browser, base)
    page.wait_for_selector('[data-act="digit"]')
    assert page.locator('[data-act="table-view"]').count() == 0
    c.put("/api/admin/plan/settings", auth=AUTH, json={"start": "plan"})
    ctx.close()


def _admin(browser, base):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 1300, "height": 1000})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html")
    page.fill("#user", "admin")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector(".side")
    page.click('.side button[data-page="tables"]')
    page.wait_for_selector("#plancard h3")
    return ctx, page, errs


def _saved(c, label: str) -> dict:
    return next(x for x in c.get("/api/admin/plan", auth=AUTH).json()["tables"] if x["label"] == label)


def test_admin_editor_place_drag_row_undo(server, browser):  # noqa: F811
    base, c = server
    for a in c.get("/api/admin/plan", auth=AUTH).json()["areas"]:
        c.delete(f"/api/admin/plan/areas/{a['id']}", auth=AUTH)
    ctx, page, errs = _admin(browser, base)
    page.once("dialog", lambda d: d.accept("Terrasse"))
    page.click('[data-act="area-add"]')
    page.wait_for_selector("#pe-cv")
    # Tisch 1 aus «Nicht im Plan» setzen
    page.click('[data-place]:has-text("1")')
    page.wait_for_selector('.pe-t[data-k]')
    _until(page, "document.getElementById('pe-state').textContent === 'gespeichert'")
    first = _saved(c, "1")
    assert first["placed"] and first["shape"] == "rect"
    # ziehen
    t = page.locator(".pe-t").first
    t.scroll_into_view_if_needed()
    box = t.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] - 60, box["y"] - 80, steps=6)
    page.mouse.up()
    _until(page, "document.getElementById('pe-state').textContent === 'gespeichert'")
    moved = _saved(c, "1")
    assert moved["x"] < first["x"] and moved["y"] < first["y"]
    # Form rund
    page.click('[data-shape="round"]')
    _until(page, "document.getElementById('pe-state').textContent === 'gespeichert'")
    assert _saved(c, "1")["shape"] == "round"
    # Rückgängig: wieder eckig
    page.click('[data-act="undo"]')
    _until(page, "document.getElementById('pe-state').textContent === 'gespeichert'")
    assert _saved(c, "1")["shape"] == "rect"
    # Reihe von 3 Festbänken ab T1 (die Tische gibt es noch nicht – werden angelegt)
    page.fill('#pe-rowform [name="n"]', "3")
    page.fill('#pe-rowform [name="from"]', "T1")
    page.select_option('#pe-rowform [name="shape"]', "bench")
    page.click("#pe-rowform button")
    _until(page, "document.querySelectorAll('.pe-t').length === 4")
    _until(page, "document.getElementById('pe-state').textContent === 'gespeichert'")
    rows = [_saved(c, f"T{i}") for i in (1, 2, 3)]
    assert all(r["placed"] and r["shape"] == "bench" for r in rows)
    assert rows[0]["y"] == rows[1]["y"] == rows[2]["y"] and rows[0]["x"] < rows[1]["x"] < rows[2]["x"]
    # Entf nimmt die gewählten (die Reihe) aus dem Plan
    page.locator("#pe-cv").focus()
    page.keyboard.press("Delete")
    _until(page, "document.querySelectorAll('.pe-t').length === 1")
    _until(page, "document.getElementById('pe-state').textContent === 'gespeichert'")
    assert not _saved(c, "T2")["placed"]
    # Grundriss hochladen
    page.set_input_files("#pe-file", files=[{"name": "g.png", "mimeType": "image/png", "buffer": _png(600, 300)}])
    page.wait_for_selector("#pe-cv img")
    area = c.get("/api/admin/plan", auth=AUTH).json()["areas"][0]
    assert area["image"] and area["ratio"] == 500
    assert errs == [], errs
    ctx.close()


def _touch(cdp, kind: str, pts: list[tuple[float, float]]) -> None:
    cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [{"x": x, "y": y, "id": i} for i, (x, y) in enumerate(pts)]})


def test_pinch_zooms_and_moves_at_the_same_time(server, browser):  # noqa: F811
    """Rückmeldung 2.11.: mit zwei Fingern zugleich zoomen und verschieben; leicht gezoomt auch nach links/rechts."""
    base, c = server
    _plan(c, {"1": (100, 300, 60, 240, "bench"), "2": (800, 900, 90, 90, "round")})
    ctx, page, errs = _phone(browser, base)
    page.wait_for_selector("#plwrap .pl-t")
    cdp = ctx.new_cdp_session(page)
    r = page.locator("#plwrap").bounding_box()
    cx, cy = r["x"] + r["width"] / 2, r["y"] + r["height"] / 2
    w0 = page.evaluate("document.getElementById('pl').offsetWidth")
    # zwei Finger auseinander (Zoom ~2) und gleichzeitig 60 px nach rechts
    _touch(cdp, "touchStart", [(cx - 40, cy), (cx + 40, cy)])
    for k in range(1, 9):
        f = k / 8
        _touch(cdp, "touchMove", [(cx - 40 - 40 * f + 60 * f, cy), (cx + 40 + 40 * f + 60 * f, cy)])
    _touch(cdp, "touchEnd", [])
    w1 = page.evaluate("document.getElementById('pl').offsetWidth")
    assert 1.8 < w1 / w0 < 2.2
    # der Punkt unter der Fingermitte ist mitgewandert: ohne Verschieben stünde der Plan 60 px weiter links
    left = page.evaluate("document.getElementById('pl').offsetLeft")
    plain = (r["width"] - w0) / 2 * 2 - r["width"] / 2      # Zoom 2 um die Mitte: left = 2·left0 − W0/2
    assert abs(left - (plain + 60)) < 12, (left, plain)
    # nur wenig gezoomt (Plan schmaler als die Fläche): trotzdem nach links/rechts verschiebbar
    page.click('[data-act="plan-zoom"][data-d="fit"]')
    page.evaluate("zoomAt(1.2, 100, 100)")
    before = page.evaluate("document.getElementById('pl').offsetLeft")
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + 50, cy, steps=5)
    page.mouse.up()
    assert page.evaluate("document.getElementById('pl').offsetLeft") > before + 30
    assert errs == [], errs
    ctx.close()


def test_status_hint_never_hides_the_table_number(server, browser):  # noqa: F811
    """Rückmeldung 2.11.: «Küche – Papier fast leer» im Kopf verdrängte «Platz 102». Der Titel hat Vorrang (360 px)."""
    base, c = server
    c.put("/api/admin/plan/settings", auth=AUTH, json={"start": "number"})
    c.put("/api/admin/settings", auth=AUTH, json={"table_word": "Platz"})
    try:
        ctx, page, errs = _phone(browser, base, width=360)
        page.wait_for_selector('[data-act="digit"]')
        for d in "102":
            page.click(f'[data-act="digit"][data-d="{d}"]')
        page.click('[data-act="table-ok"]')
        page.wait_for_selector(".grid .tile")
        _until(page, "document.getElementById('stchip')?.dataset.s === 'bad'", 15000)   # Testdrucker: Ausfall
        h1 = page.evaluate("(() => { const h = document.querySelector('header.top h1'); return [h.textContent, h.scrollWidth, h.clientWidth]; })()")
        assert h1[0] == "Platz 102" and h1[1] <= h1[2], h1
        assert page.evaluate("document.scrollingElement.scrollWidth") <= 360
        ctx.close()
    finally:
        c.put("/api/admin/settings", auth=AUTH, json={"table_word": "Tisch"})
        c.put("/api/admin/plan/settings", auth=AUTH, json={"start": "plan"})
