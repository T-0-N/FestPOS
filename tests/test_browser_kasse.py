"""Version 2026.10.10-25: Kasse im Browser (XCover-Grösse).
A – Gegeben und Rückgeld in einer festen Zeile: die Tastatur bleibt beim Tippen am selben Ort.
    Zu wenig gegeben: «Fehlt noch» rot, sonst Rückgeld grün (Version -28, in allen Arten).
Teilen: dieselbe Rechenhilfe für den Betrag pro Person, neue Personenzahl leert «Gegeben»; Bon nur mit Total.
Einzeln (Version -31): pro Person zwei Schritte – Positionen wählen («Weiter: CHF … kassieren»), kassieren
    («Person 1 hat bezahlt»). Unten ist immer genau ein grüner Knopf für den nächsten Schritt; «Nächste Bestellung»
    erst, wenn alles bezahlt ist. Bezahltes grau mit P1/P2 gestapelt unter dem Namen.
Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import _open_table, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_api import AUTH  # noqa: E402
from tests.test_browser_receipt import _until  # noqa: E402

PHONE = {"X-Test-Cert": "XCover 1", "X-Test-Client": "192.168.55.101"}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("kasse"), target="tests.asgi_testphone:app")
    yield base, c
    proc.terminate()
    proc.wait(10)


def _pay_view(browser, base, test: bool = False, size=(412, 892)):  # noqa: F811
    """2× Bier 5dl (6.50), 1× Mineral (4.00), 2× Bratwurst (8.00) = 33.00 – Kasse offen (test: im Übungsmodus)."""
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, has_touch=True, extra_http_headers=PHONE)
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('XCover 1'))")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    if test:
        page.evaluate("setTest(true, 'app')")
        page.wait_for_selector("header.top.test")
    _open_table(page, "12")
    for _ in range(2):
        page.click(".grid .tile >> nth=0")
    page.click(".grid .tile >> nth=1")
    page.locator("nav [data-act='cat']").nth(1).click()
    for _ in range(2):
        page.click(".grid .tile >> nth=0")
    page.click("#send")
    page.wait_for_selector('#paystate[data-s="sent"]', timeout=10000)
    page.wait_for_timeout(650)
    return ctx, page, errs


def _pad_top(page) -> int:
    """Lage der Tastatur auf der Seite (unabhängig davon, wie weit gescrollt ist)."""
    return page.evaluate("Math.round(document.querySelector('#paypanel .pad').getBoundingClientRect().top + scrollY)")


RED, GREEN = "rgb(200, 16, 46)", "rgb(8, 117, 47)"


def _color(page) -> str:
    return page.evaluate("getComputedStyle(document.querySelector('#paypanel .calc .c-out b')).color")


def _out(page) -> tuple[str, str]:
    el = page.locator("#paypanel .calc .c-out")
    return el.get_attribute("class").split()[-1], " ".join(el.inner_text().split())


def test_change_keypad_never_moves(server, browser):  # noqa: F811
    base, _ = server
    ctx, page, errs = _pay_view(browser, base)
    top = _pad_top(page)
    assert _out(page) == ("none", "Rückgeld –")
    page.click('#paypanel [data-act="key"][data-k="5"]')
    assert _pad_top(page) == top and _out(page) == ("short", "Fehlt noch 28.00")      # kein Springen
    assert _color(page) == RED
    page.click('#paypanel [data-act="key"][data-k="0"]')
    assert _pad_top(page) == top and _out(page) == ("good", "Rückgeld 17.00")
    assert _color(page) == GREEN
    assert page.inner_text("#paypanel .calc output") == "50"
    page.click('#paypanel [data-act="given"] >> nth=0')                                 # Passend
    assert _pad_top(page) == top and _out(page) == ("good", "Rückgeld 0.00")
    # ganze Tastatur sichtbar (unterste Reihe nicht hinter «Nächste Bestellung»)
    bottom = page.evaluate("document.querySelector('#paypanel .pad').getBoundingClientRect().bottom")
    foot = page.evaluate("document.querySelector('.foot').getBoundingClientRect().top")
    assert bottom <= foot, (bottom, foot)
    assert not errs, errs
    ctx.close()


def _fits(page) -> None:
    """Ohne Scrollen bis und mit Tastatur sichtbar (nicht hinter dem Fuss), nichts ragt seitlich hinaus."""
    assert page.evaluate("scrollY") == 0
    bottom = page.evaluate("document.querySelector('#paypanel .pad').getBoundingClientRect().bottom")
    foot = page.evaluate("document.querySelector('.foot').getBoundingClientRect().top")
    assert bottom <= foot, (bottom, foot)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_split_plan_then_person_step(server, browser):  # noqa: F811
    """Teilen in zwei Schritten wie Einzeln. Aufteilen: jede Person mit ihrem Betrag (der höhere zuerst,
    zusammen genau das Total); «Weiter: Person 1 · CHF …» kassiert sie – Gegeben/Rückgeld, Tastatur über dem Fuss."""
    base, _ = server
    ctx, page, errs = _pay_view(browser, base)                         # 33.00
    page.click('[data-act="mode"][data-m="split"]')
    assert "compact" in page.get_attribute("#bon", "class")                            # Bon nur mit Total
    rows = lambda: [" ".join(r.split()) for r in page.locator("#splitplan .pi").all_inner_texts()]  # noqa: E731
    assert rows() == ["Person 1 offen 16.50", "Person 2 offen 16.50"]
    for _ in range(6):                                                                  # 8 Personen: 4 × 4.15, 4 × 4.10
        page.click('[data-act="split"][data-d="1"]')
    amounts = [r.split()[-1] for r in rows()]
    assert amounts == ["4.15"] * 4 + ["4.10"] * 4 and sum(int(a.replace(".", "")) for a in amounts) == 3300
    assert _foot(page) == ["Weiter: Person 1 · CHF 4.15"] and len(page.locator("#payfoot .go").all()) == 1
    page.click('#payfoot [data-act="split-go"]')
    page.wait_for_selector("#splitpay")
    assert " ".join(page.inner_text("#splitpay .prow").split()) == "Person 1 Anteil 1 von 8 4.15"
    assert page.locator(".seg[role=tablist]").count() == 0                              # wie Einzeln: nur dieser Schritt
    top = _pad_top(page)
    page.click('#paypanel [data-act="key"][data-k="5"]')
    assert _pad_top(page) == top and _out(page) == ("good", "Rückgeld 0.85") and _color(page) == GREEN
    page.click('#paypanel [data-act="key"][data-k="del"]'); page.click('#paypanel [data-act="key"][data-k="4"]')
    assert _out(page) == ("short", "Fehlt noch 0.15") and _color(page) == RED
    assert _foot(page) == ["Zurück", "Person 1 hat bezahlt"]
    _fits(page)
    page.click('#payfoot [data-act="split-back"]')                                      # zurück zur Aufteilung
    page.wait_for_selector("#splitplan")
    page.set_viewport_size({"width": 360, "height": 892})
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    assert not errs, errs
    ctx.close()



def _no_overlap(page) -> bool:
    """«Gegeben» und die Zeilen «bei 11.30  8.70» überdecken sich nicht, jede Zeile bleibt einzeilig, die Rechenhilfe
    endet über der Tastatur (T34: vorher überdeckten sich die Zeilen bei grosser Schrift)."""
    return page.evaluate("""(() => {
      const calc = document.querySelector('#paypanel .calc').getBoundingClientRect();
      const g = document.querySelector('#paypanel .calc .c-given').getBoundingClientRect();
      const pad = document.querySelector('#paypanel .pad').getBoundingClientRect();
      const rows = [...document.querySelectorAll('#paypanel .c-out.two .r')].map((r) => r.getBoundingClientRect());
      const hit = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
      const one = [...document.querySelectorAll('#paypanel .c-out.two .r')].every((r) =>
        r.getBoundingClientRect().height < r.querySelector('b').getBoundingClientRect().height * 1.6);
      return one && rows.every((r) => !hit(r, g) && r.right <= innerWidth && r.bottom <= calc.bottom + 1)
        && calc.bottom <= pad.top + 1;
    })()""")


def _open(page) -> str:
    return " ".join(page.inner_text("#partsum .row").split())


def _foot(page) -> list[str]:
    return [" ".join(x.split()) for x in page.locator("#payfoot button").all_inner_texts()]


def test_split_in_two_steps_per_person(server, browser):  # noqa: F811
    base, _ = server
    ctx, page, errs = _pay_view(browser, base)
    page.click('[data-act="mode"][data-m="part"]')
    rows = page.locator("#pickpanel .pi")
    # Schritt 1: nur die Positionen, Bon ausgeblendet; unten ein einziger (noch gesperrter) Knopf
    assert rows.count() == 3 and page.locator("#bon").count() == 0
    assert _open(page) == "Bezahlt 0.00 Noch offen 33.00" and "Person 1 – was zahlt sie?" in page.inner_text("#pickpanel")
    assert _foot(page) == ["Positionen antippen"] and page.locator('#payfoot [data-act="ez-next"]').is_disabled()
    assert page.locator('#payfoot [data-act="done"]').count() == 0          # kein «Nächste Bestellung» zum Verwechseln
    bier = page.locator("#pickpanel .pi", has_text="Bier 5dl")      # nach Namen: Bezahltes wandert ans Ende
    mineral = page.locator("#pickpanel .pi", has_text="Mineral")
    for _ in range(3):                                   # 2 Bier offen: das dritte Antippen ändert nichts mehr
        bier.click()
    assert bier.locator(".badge").inner_text() == "2×"
    bier.locator('[data-act="unpick"]').click()          # «−»: eins weniger
    assert bier.locator(".badge").inner_text() == "1×"
    mineral.click()
    assert _foot(page) == ["Weiter: CHF 10.50 kassieren"]               # ohne Produkte (Knopf bleibt schmal)
    # Schritt 2: kassieren wie «Rückgeld» – ohne Arten oben, Gegeben ist freiwillig
    page.click('[data-act="ez-next"]')
    assert page.locator(".seg").count() == 0 and _foot(page) == ["Zurück", "Person 1 hat bezahlt"]
    assert " ".join(page.inner_text("#paypanel .prow").split()) == "Person 1 Bier 5dl, Mineral 10.50"
    top = _pad_top(page)
    page.click('#paypanel [data-act="key"][data-k="5"]')
    assert _out(page) == ("short", "Fehlt noch 5.50") and _color(page) == RED and _pad_top(page) == top
    page.click('#paypanel [data-act="key"][data-k="0"]')
    assert _out(page) == ("good", "Rückgeld 39.50") and _color(page) == GREEN and _pad_top(page) == top
    page.click('#paypanel [data-act="part-given"] >> nth=2')                            # oder Schein: 20
    assert _out(page) == ("good", "Rückgeld 9.50")
    # ganze Tastatur sichtbar, ohne zu scrollen
    bottom = page.evaluate("document.querySelector('#paypanel .pad').getBoundingClientRect().bottom")
    assert bottom <= page.evaluate("document.querySelector('.foot').getBoundingClientRect().top")
    page.click('[data-act="ez-back"]')                   # «Zurück»: Auswahl derselben Person
    assert "Person 1 – was zahlt sie?" in page.inner_text("#pickpanel") and bier.locator(".badge").inner_text() == "1×"
    page.click('[data-act="ez-next"]')
    page.click('[data-act="part-pay"]')                  # ohne «Gegeben»: geht auch
    # weiter mit Person 2, oben P1
    assert page.evaluate("scrollY") == 0
    assert _open(page) == "Bezahlt 10.50 Noch offen 22.50" and "Person 2 – was zahlt sie?" in page.inner_text("#pickpanel")
    assert page.locator(".pc.done").all_inner_texts() == ["P1 ✓ 10.50"]
    assert "paid" in mineral.get_attribute("class") and mineral.locator(".tag").inner_text() == "P1"
    assert "Mineral" in rows.nth(2).inner_text()                                          # ganz bezahlt: am Ende
    assert "1 von 2 offen" in bier.inner_text() and "paid" not in bier.get_attribute("class")
    mineral.click()                                     # schon bezahlt: lässt sich nicht mehr wählen
    assert mineral.locator(".badge").count() == 0
    # P1 antippen: letzte Zahlung rückgängig
    page.click(".pc.done")
    page.click('.sheet [data-act="part-undo"]')
    assert _open(page) == "Bezahlt 0.00 Noch offen 33.00" and page.locator(".pc.done").count() == 0
    # nochmals P1, dann «Alles Offene» für Person 2
    bier.click(); mineral.click()
    page.click('[data-act="ez-next"]'); page.click('[data-act="part-pay"]')
    assert page.inner_text('[data-act="ez-all"]').strip() == "Alle wählen"
    assert page.locator('[data-act="ez-none"]').is_disabled()                             # noch nichts gewählt
    names = page.locator("#pickpanel .pi").all_inner_texts()
    paid = page.locator("#pickpanel .pi.paid").count()
    assert paid and all("bezahlt" in n.lower() for n in names[-paid:])                    # ganz Bezahltes am Ende
    page.click('[data-act="ez-all"]')
    assert _foot(page) == ["Weiter: CHF 22.50 kassieren"] and page.locator('[data-act="ez-all"]').is_disabled()
    page.click('[data-act="ez-none"]')                                                    # alles wieder ab
    assert page.locator("#pickpanel .pi.sel").count() == 0 and page.locator('[data-act="ez-none"]').is_disabled()
    page.click('[data-act="ez-all"]')
    page.click('[data-act="ez-next"]'); page.click('[data-act="part-pay"]')
    # fertig: Übersicht, erst jetzt «Nächste Bestellung»
    assert "Alles bezahlt" in page.inner_text("#partdone") and page.locator("#partdone .r").count() == 2
    assert _foot(page) == ["Nächste Bestellung"]                       # «Gleicher Tisch» gibt es nicht mehr
    # wechseln und zurück: bezahlte Personen bleiben
    page.click('[data-act="mode"][data-m="change"]')
    assert _open(page) == "Bezahlt 33.00 Noch offen 0.00" and page.locator("#bon").count() == 1
    page.click('[data-act="mode"][data-m="part"]')
    assert page.locator("#partdone .r").count() == 2
    page.click('[data-act="done"]')
    page.wait_for_selector('[data-act="digit"]')
    assert page.locator(".sheet").count() == 0
    assert not errs, errs
    ctx.close()


def test_split_pay_step_scrolls_to_person(server, browser):  # noqa: F811
    """Einzeln, «Weiter: … kassieren» auf kleinem Bildschirm: gleich bei «Person 1» unter der Kopfzeile, die ganze
    Tastatur steht über der Fusszeile (vorher oben am Seitenanfang, die Tastatur halb verdeckt)."""
    base, _ = server
    ctx, page, errs = _pay_view(browser, base)
    page.set_viewport_size({"width": 360, "height": 720})   # z.B. mit Systemleisten
    page.click('[data-act="mode"][data-m="part"]')
    page.click("#pickpanel .pi >> nth=0")
    page.click("#pickpanel .pi >> nth=1")
    page.click('[data-act="ez-next"]')
    assert page.evaluate("scrollY") > 0
    head = page.evaluate("document.querySelector('.top').getBoundingClientRect().bottom")
    prow = page.evaluate("document.querySelector('#partpay').getBoundingClientRect().top")
    assert head <= prow <= head + 12                     # «Person 1» direkt unter der Kopfzeile
    bottom = page.evaluate("document.querySelector('#paypanel .pad').getBoundingClientRect().bottom")
    assert bottom <= page.evaluate("document.querySelector('.foot').getBoundingClientRect().top")
    page.click('[data-act="ez-back"]')                   # zurück zur Auswahl: wieder oben
    assert page.evaluate("scrollY") == 0
    assert errs == []
    ctx.close()


def test_back_and_rest_under_change(server, browser):  # noqa: F811
    """Kein «Einzeln abbrechen» mehr: der Zurück-Pfeil fragt nach; den Rest kassiert man oben unter «Rückgeld»."""
    base, _ = server
    ctx, page, errs = _pay_view(browser, base)
    page.click('[data-act="mode"][data-m="part"]')
    assert page.locator('[data-act="ez-cancel"]').count() == 0
    page.locator("#pickpanel .pi").nth(1).click()        # Mineral 4.00
    page.click('[data-act="ez-next"]'); page.click('[data-act="part-pay"]')
    page.click('[data-act="back"]')                      # Zurück-Pfeil mit offenem Rest: Rückfrage
    page.wait_for_selector(".sheet")
    assert "Noch nicht alles bezahlt" in page.inner_text(".sheet") and "29.00" in page.inner_text(".sheet")
    page.click('.sheet [data-act="close-sheet"]')        # «Zurück zur Kasse»
    page.click('[data-act="mode"][data-m="change"]')     # Rest zusammen
    assert _open(page) == "Bezahlt 4.00 Noch offen 29.00"                  # Teilzahlungen gelten in allen Arten
    page.click('#paypanel [data-act="given"] >> nth=0')  # Passend
    assert page.inner_text("#paypanel .calc output") == "29"
    assert _foot(page)[-1:] == ["Bezahlt · Nächste Bestellung"] and "Gleicher Tisch" not in _foot(page)
    assert not errs, errs
    ctx.close()


def test_many_people_on_one_item_are_stacked(server, browser):  # noqa: F811
    """Mehrere Personen auf derselben Position (schmales Handy): P1, P2 … stehen unter dem Namen und überdecken nichts."""
    base, _ = server
    ctx, page, errs = _pay_view(browser, base)
    page.set_viewport_size({"width": 360, "height": 780})
    page.click('[data-act="mode"][data-m="part"]')
    bier = page.locator("#pickpanel .pi", has_text="Bier 5dl")
    for _ in range(2):
        bier.click()
        page.click('[data-act="ez-next"]')
        page.click('[data-act="part-pay"]')
    assert bier.locator(".tag").all_inner_texts() == ["P1", "P2"] and "paid" in bier.get_attribute("class")
    inside = page.evaluate("""(() => { const r = [...document.querySelectorAll('#pickpanel .pi')].find(x => x.textContent.includes('Bier 5dl')), n = r.querySelector('.nm');
        const nb = n.getBoundingClientRect(), rb = r.getBoundingClientRect();
        return [...r.querySelectorAll('.tag')].every(t => { const b = t.getBoundingClientRect();
            return b.left >= nb.left - 0.5 && b.right <= nb.right + 0.5 && b.bottom <= rb.bottom; }); })()""")
    assert inside
    assert not errs, errs
    ctx.close()


def test_own_amounts_in_change_and_split(server, browser):  # noqa: F811
    """Rückmeldung: «ich zahle 20, du den Rest» – R: in Rückgeld zu wenig gegeben -> Knopf unter dem Ziffernblock
    bucht eine Teilzahlung; T: in Teilen «+ Jemand zahlt einen eigenen Betrag», der Rest wird auf die übrigen verteilt."""
    base, _ = server
    ctx, page, errs = _pay_view(browser, base)                       # 33.00
    for k in "10":
        page.click(f'#paypanel [data-act="key"][data-k="{k}"]')
    assert _out(page) == ("short", "Fehlt noch 23.00")
    btn = page.locator('#paypanel [data-act="part-amount"]')
    assert " ".join(btn.inner_text().split()) == "10.00 als Teilzahlung · Rest 23.00 offen"
    pad = page.evaluate("document.querySelector('#paypanel .pad').getBoundingClientRect().bottom")
    assert page.evaluate("document.querySelector('[data-act=\"part-amount\"]').getBoundingClientRect().top") >= pad   # unter dem Ziffernblock
    btn.click()
    assert _open(page) == "Bezahlt 10.00 Noch offen 23.00" and page.locator(".pc.done").all_inner_texts() == ["P1 ✓ 10.00 · Teilzahlung"]
    assert page.inner_text("#paypanel .calc output") == ""
    page.click('#paypanel [data-act="given"][data-v="5000"]')           # Rest mit Rückgeld
    assert _out(page) == ("good", "Rückgeld 27.00") and page.locator('[data-act="part-amount"]').count() == 0
    assert _foot(page)[-1] == "Bezahlt · Nächste Bestellung"               # Gegeben reicht -> einbuchen
    for _ in range(2):
        page.click('#paypanel [data-act="key"][data-k="del"]')             # ohne Eingabe weg: Rest offen
    page.click('#payfoot [data-act="done"]')                             # Rest offen: nachfragen
    page.wait_for_selector(".sheet")
    assert "23.00" in page.inner_text(".sheet")
    page.click('.sheet [data-act="close-sheet"]')
    # T: Teilen mit 3 Personen, Person 2 übernimmt 5.00 – Person 1 und 3 teilen den Rest 18.00 (= 33 − 10 − 5)
    page.click('[data-act="mode"][data-m="split"]')
    page.click('[data-act="split"][data-d="1"]')
    assert page.inner_text("#paypanel .split-row output").startswith("3")
    box = page.locator('#splitplan [data-act="split-own"][data-n="2"]').bounding_box()   # lange drücken: eigener Betrag
    page.mouse.move(box["x"] + 40, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.wait_for_timeout(700)
    page.mouse.up()
    page.wait_for_selector("#ownsheet")
    assert _foot(page)[-1].startswith("Weiter: Person 1")                # der Klick beim Loslassen kassiert nicht
    assert page.locator('#ownsheet [data-act="own-pay"]').is_disabled()
    page.click('#ownsheet [data-act="key"][data-k="5"]')
    assert " ".join(page.inner_text("#ownsheet .c-out").split()) == "Rest je Person 9.00"
    page.click('#ownsheet [data-act="own-pay"]')
    assert [" ".join(r.split()) for r in page.locator("#splitplan .pi").all_inner_texts()] == [
        "Person 1 offen 9.00", "Person 2 · eigener Betrag offen 5.00", "Person 3 offen 9.00"]
    assert _open(page) == "Bezahlt 10.00 Noch offen 23.00"
    page.click(".pc.done >> nth=0")                                      # Teilzahlung rückgängig
    page.click('.sheet [data-act="part-undo"]')
    assert [" ".join(r.split()) for r in page.locator("#splitplan .pi").all_inner_texts()] == [
        "Person 1 offen 14.00", "Person 2 · eigener Betrag offen 5.00", "Person 3 offen 14.00"]
    page.click('#splitplan [data-act="split-own"][data-n="3"]')          # antippen = gleich kassieren
    assert page.locator("#ownsheet").count() == 0 and _foot(page) == ["Zurück", "Person 3 hat bezahlt"]
    assert " ".join(page.inner_text("#paypanel .prow").split()).endswith("14.00")
    page.click('#payfoot [data-act="split-back"]')
    page.set_viewport_size({"width": 360, "height": 892})
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    assert not errs, errs
    ctx.close()


def test_own_amount_then_separately_never_charges_too_much(server, browser):  # noqa: F811
    """Rückmeldung: in Teilen hat eine Person bezahlt, danach «Einzeln» und «Alle wählen» – es darf nur noch der
    Rest kassiert werden (die Person hatte ohne Positionen bezahlt)."""
    base, _ = server
    ctx, page, errs = _pay_view(browser, base)                       # 33.00
    page.click('[data-act="mode"][data-m="split"]')
    page.click('#payfoot [data-act="split-go"]')
    page.click('#payfoot [data-act="split-paid"]')                   # Person 1: 16.50
    assert _open(page) == "Bezahlt 16.50 Noch offen 16.50"
    page.click('[data-act="mode"][data-m="part"]')
    page.click('#pickpanel [data-act="ez-all"]')
    assert _foot(page) == ["Weiter: CHF 16.50 kassieren Rest der Bestellung (Auswahl 33.00)"]
    box = " ".join(page.inner_text("#pickpanel .ez-over").split())
    assert box == "Gewählt 33.00 − schon bezahlt (P1) −16.50 Person 2 zahlt den Rest 16.50", box
    page.click('#payfoot [data-act="ez-next"]')
    page.click('#payfoot [data-act="part-pay"]')
    page.wait_for_selector("#partdone")
    assert _open(page) == "Bezahlt 33.00 Noch offen 0.00"
    paid = page.evaluate("S.part.paid.map((p) => p.amount)")
    assert paid == [1650, 1650] and sum(paid) == 3300                 # nie mehr als das Total
    assert not errs, errs
    ctx.close()



def test_separately_rows_fixed_height_long_names_wrap(server, browser):  # noqa: F811
    """Rückmeldung: Positionen bei «Einzeln» gleich hoch – oben das Produkt, darunter «1 von 2 offen»,
    «−» Anzahl und Preis rechts. Nur ein zu langer Name bricht um und macht seine Zeile höher."""
    base, _ = server
    ctx, page, errs = _pay_view(browser, base)
    page.click('[data-act="mode"][data-m="part"]')
    rows = page.locator("#pickpanel .pi")
    heights = lambda: page.evaluate("[...document.querySelectorAll('#pickpanel .pi')].map((e) => e.offsetHeight)")  # noqa: E731
    before = heights()
    assert len(set(before)) == 1, before                                   # alle gleich hoch
    rows.first.click()                                                    # gewählt: «−» und Anzahl – gleich hoch
    assert heights() == before and "2 von 2 offen" in rows.first.inner_text()
    line = lambda sel: page.evaluate(f"document.querySelector('#pickpanel .pi {sel}').getBoundingClientRect().top")  # noqa: E731
    assert line(".nm") < line(".st") and abs(line(".st") - line(".mark")) < 12   # Stand und «−» in der zweiten Zeile
    page.evaluate("S.pay.items[0].name = 'Grillteller mit Pommes frites und Salat vom Buffet'; render()")
    long = page.evaluate("[...document.querySelectorAll('#pickpanel .pi')].find((e) => e.innerText.includes('Grillteller')).offsetHeight")
    assert long > before[0] and "Grillteller mit Pommes frites und Salat vom Buffet" in page.inner_text("#pickpanel")
    page.set_viewport_size({"width": 360, "height": 892})
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    assert not errs, errs
    ctx.close()


@pytest.mark.parametrize("test_mode", [False, True])
def test_separately_pay_step_keypad_visible_on_small_phone(server, browser, test_mode):  # noqa: F811
    """Rückmeldung: im Übungsmodus (Kopf mit Balken höher) lag bei «Person n kassieren» die unterste
    Tastenreihe hinter dem grünen Knopf. Jetzt wird so gescrollt, dass die ganze Tastatur sichtbar ist."""
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": "2468", "test_print": False})
    ctx, page, errs = _pay_view(browser, base, test=test_mode, size=(360, 690))
    page.click('[data-act="mode"][data-m="part"]')
    page.locator("#pickpanel .pi").first.click()
    page.click('#payfoot [data-act="ez-next"]')
    page.wait_for_selector("#partpay")
    page.wait_for_timeout(100)
    pad = page.evaluate("document.querySelector('#partpay .pad').getBoundingClientRect().bottom")
    foot = page.evaluate("document.querySelector('#payfoot').getBoundingClientRect().top")
    assert pad <= foot, (pad, foot)
    assert page.locator('[data-act="ez-back"]').count() == 1                  # nur noch «Zurück» unten
    if test_mode:
        page.evaluate("setTest(false, 'app')")
    assert not errs, errs
    ctx.close()


def test_t25_covered_exactly_count_before_minus_and_scroll_to_pad(server, browser):  # noqa: F811
    """T25: Teilbetrag 13.00, dann «Einzeln»: deckt die Auswahl den Rest genau (20.00), stehen die übrigen
    Positionen schon auf «schon gedeckt», sind nicht mehr wählbar, und oben steht keine Rechnung. Die Anzahl steht vor
    dem «−». Antippen von «Gegeben» scrollt zur Tastatur."""
    from tests.test_browser_receipt import _until
    base, _ = server
    ctx, page, errs = _pay_view(browser, base, size=(360, 640))          # 33.00
    page.evaluate("window.scrollTo(0, 0)")
    page.click("#paypanel .calc .c-given")                              # Tipp auf «Gegeben»: Tastatur ganz sichtbar
    _until(page, "document.querySelector('#paypanel .pad').getBoundingClientRect().bottom"
                 " <= document.querySelector('#payfoot').getBoundingClientRect().top")
    for k in "13":
        page.click(f'#paypanel [data-act="key"][data-k="{k}"]')
    page.click('#paypanel [data-act="part-amount"]')
    assert _open(page) == "Bezahlt 13.00 Noch offen 20.00"
    page.click('[data-act="mode"][data-m="part"]')
    row = lambda name: page.locator("#pickpanel .pi", has_text=name)  # noqa: E731
    row("Mineral").click()
    row("Bratwurst").click()
    assert page.locator("#pickpanel .pi.cov").count() == 0                # 12.00 von 20.00: noch nicht gedeckt
    row("Bratwurst").click()                                              # 4 + 2 × 8 = 20.00 = Rest
    bier = row("Bier")
    assert "cov" in bier.get_attribute("class") and "schon gedeckt" in bier.inner_text()
    assert bier.get_attribute("aria-disabled") == "true"
    assert page.locator("#pickpanel .ez-over").count() == 0                # keine Rechnung oben
    bier.click()                                                          # nicht wählbar
    assert page.evaluate("S.part.sel")
    assert page.evaluate("Object.keys(S.part.sel).length") == 2 and page.locator("#pickpanel .ez-over").count() == 0
    assert _foot(page) == ["Weiter: CHF 20.00 kassieren"]
    order = page.evaluate("[...document.querySelector('#pickpanel .pi.sel .mark').children].map((e) => e.className)")
    assert order == ["badge", "minus"], order                             # Anzahl vor dem Minus
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    assert not errs, errs
    ctx.close()



def test_split_each_person_with_payment_and_undo_any(server, browser):  # noqa: F811
    """jede Person mit Zahlart kassieren; eine bezahlte Person (nicht nur die letzte) antippen macht ihre
    Zahlung rückgängig – sie ist wieder offen, die anderen bleiben bezahlt; der Server zählt pro Zahlart."""
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"payment_mode": "optional"})
    try:
        ctx, page, errs = _pay_view(browser, base)                     # 33.00
        page.click('[data-act="mode"][data-m="split"]')
        page.click('[data-act="split"][data-d="1"]')                    # 3 × 11.00
        for kind in ("cash", "card", "twint"):
            page.click('#payfoot [data-act="split-go"]')
            page.click(f'#personkind [data-v="{kind}"]')
            page.click('#payfoot [data-act="split-paid"]')
        page.wait_for_selector("#splitdone")
        assert _open(page) == "Bezahlt 33.00 Noch offen 0.00" and _foot(page) == ["Nächste Bestellung"]
        assert page.locator(".pc.done").all_inner_texts() == ["P1 ✓ 11.00 · Bar", "P2 ✓ 11.00 · Karte", "P3 ✓ 11.00 · TWINT"]
        _until(page, "!partq.length")                                   # beim Server angekommen
        pays = c.get("/api/admin/closings/open", auth=AUTH).json()["summary"]["by_payment"]
        assert pays.get("card", 0) >= 1100 and pays.get("twint", 0) >= 1100, pays
        # Person 2 (die mittlere) rückgängig
        page.click(".pc.done >> nth=1")
        assert "Zahlung von Person 2 rückgängig" in page.inner_text(".sheet")
        page.click('.sheet [data-act="part-undo"]')
        page.wait_for_selector("#splitplan")
        assert _open(page) == "Bezahlt 22.00 Noch offen 11.00"
        assert [" ".join(r.split()) for r in page.locator("#splitplan .pi").all_inner_texts()] == [
            "Person 1 ✓ Bar 11.00", "Person 2 offen 11.00", "Person 3 ✓ TWINT 11.00"]
        assert _foot(page) == ["Weiter: Person 2 · CHF 11.00"]
        _until(page, "!partq.length")
        undone = c.get("/api/admin/audit?q=order_part_undo", auth=AUTH).json()["entries"]
        assert any(e["details"].get("payment") == "card" for e in undone), undone
        assert not errs, errs
        ctx.close()
    finally:
        c.put("/api/admin/settings", auth=AUTH, json={"payment_mode": "off"})
