"""Im echten Browser: die Bestell-App in jeder mitgelieferten Sprache auf einem schmalen Handy (360 px) –
keine abgeschnittenen Knöpfe, nichts ragt über den Rand, Überschriften gross geschrieben, keine Skriptfehler.
Wird übersprungen, wenn Playwright oder Chromium fehlt (z.B. auf dem Pi)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import AUTH, _enter_pin, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)

ROOT = Path(__file__).resolve().parent.parent
LANGS = ["de", "en", "fr", "it", "rm-vallader"]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("lang"))
    kaffee = c.post("/api/admin/products", auth=AUTH, json={"name": "Kaffee", "price_cents": 350, "category_id": None}).json()["id"]
    c.put(f"/api/admin/products/{kaffee}/options", auth=AUTH,
          json={"groups": [{"name": "Zucker", "choices": ["ohne", "mit"], "required": True}], "free": True})
    c.put(f"/api/admin/products/{kaffee}/delivery", auth=AUTH, json={"enabled": True, "allowed": ["Pause", "Schluss"]})
    c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": "1357"})
    yield base, c
    proc.terminate()
    proc.wait(10)


# Knöpfe, Chips, Überschriften: Text darf nicht über den Rand hinausgehen (Ellipse «…» ist Absicht, zählt nicht).
# Reine Text-Knöpfe höchstens zwei Zeilen, Umschalter (Rückgeld/Teilen/Einzeln …) und Chips genau eine.
_OVERFLOW = """() => {
  const bad = [];
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const say = (e, why) => bad.push(`${why} ${e.tagName}.${e.className}: «${e.textContent.trim().slice(0, 40)}»`);
  const lines = (e) => { const r = document.createRange(); r.selectNodeContents(e);
                         return new Set([...r.getClientRects()].map((x) => Math.round(x.bottom))).size; };
  for (const e of document.querySelectorAll("button, .chip, h1, h2, label, .seg > *, .testbar *")) {
    if (!vis(e) || getComputedStyle(e).textOverflow === "ellipsis") continue;
    if (e.scrollWidth > e.clientWidth + 1) say(e, "zu breit:");
    if (e.matches("button, .chip, .seg > *") && e.scrollHeight > e.clientHeight + 2) say(e, "zu hoch:");
    if (e.children.length === 0 && e.matches(".seg > *, .chip") && lines(e) > 1) say(e, `${lines(e)} Zeilen:`);
    if (e.children.length === 0 && e.matches("button") && lines(e) > 2) say(e, `${lines(e)} Zeilen:`);
  }
  if (document.documentElement.scrollWidth > innerWidth + 1) bad.push(`Seite ${document.documentElement.scrollWidth} px breit`);
  return bad;
}"""


def _check(page, where: str, problems: list[str]):
    for b in page.evaluate(_OVERFLOW):
        problems.append(f"{where}: {b}")


@pytest.mark.parametrize("lang", LANGS)
def test_phone_app_fits_in_every_language(server, browser, lang):  # noqa: F811
    base, _ = server
    cat = json.loads((ROOT / "app" / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
    ctx = browser.new_context(viewport={"width": 360, "height": 760})
    ctx.add_init_script(f"localStorage.setItem('festpos.device', JSON.stringify('XCover 1'));"
                        f"localStorage.setItem('festpos.lang', JSON.stringify({lang!r}))")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    problems: list[str] = []
    assert page.evaluate("document.documentElement.lang") == cat["_meta"]["locale"]
    title = page.inner_text("header h1")
    assert title[0].isupper() and title.lower() == cat["app.word.table.one"].lower()      # «Table», nicht «table»
    _check(page, "Tischwahl", problems)
    page.click('[data-act="digit"][data-d="7"]')
    assert page.inner_text('[data-act="table-ok"]').strip() == cat["app.table.go"].format(word=cat["app.word.table.one"], table="7")
    page.click('[data-act="table-ok"]')
    page.wait_for_selector(".grid .tile")
    assert page.inner_text("header h1").startswith(title + " 7")
    page.click(".grid .tile >> nth=0")
    _check(page, "Bestellen", problems)
    page.click('[data-act="cat"][data-id="none"]')
    page.click(".grid .tile >> nth=0")                                   # Kaffee: Pflicht-Option -> Fenster
    page.wait_for_selector(".sheet .optg")
    _check(page, "Optionen", problems)
    page.click('[data-act="opt-pick"] >> nth=0')
    page.click('[data-act="opt-when"] >> nth=0')
    page.click('[data-act="opt-ok"]')
    page.click('[data-act="cart"]')
    _check(page, "Warenkorb", problems)
    page.click('.sheet [data-act="send"]')
    page.wait_for_selector('#paystate[data-s="sent"]', timeout=10000)
    assert cat["app.pay.mode_change"] in page.inner_text(".seg")
    _check(page, "Kasse", problems)
    for mode in ("split", "part"):
        page.click(f'[data-act="mode"][data-m="{mode}"]')
        _check(page, f"Kasse {mode}", problems)
    page.click('[data-act="pick"] >> nth=0')                            # Einzeln: Schritt 1 und 2
    _check(page, "Einzeln gewählt", problems)
    page.click('[data-act="ez-next"]')
    _check(page, "Einzeln kassieren", problems)
    page.click('[data-act="back"]')                                    # Zurück-Pfeil, gewählt: Rückfrage
    page.wait_for_selector('.sheet [data-act="back"]')
    _check(page, "Rückfrage Einzeln", problems)
    page.click('.sheet [data-act="close-sheet"]')
    page.click('[data-act="ez-back"]')
    page.click('[data-act="mode"][data-m="change"]')
    page.click("#stchip")                                               # Status (Server, Drucker)
    _check(page, "Status", problems)
    page.evaluate("closeSheet()")
    page.wait_for_timeout(700)
    page.click('[data-act="done"]')
    page.click('[data-act="settings"]')
    _check(page, "Einstellungen", problems)
    page.click('[data-act="test-start"]')
    _enter_pin(page, "tpin", "1357")
    page.wait_for_selector("body.testmode")
    assert cat["app.test.bar"] in page.inner_text(".testbar")
    _check(page, "Testmodus", problems)
    assert not errs, errs
    ctx.close()
    assert not problems, "\n".join(problems)
