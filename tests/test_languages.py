"""Version 2026.10.10-10: mitgelieferte Sprachen Englisch, Französisch, Italienisch und Rumantsch Vallader.
Vollständig, gleiche Platzhalter wie Deutsch, Server-Meldungen und Bons in jeder Sprache, druckbar auf dem Bondrucker."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from tests.test_api import AUTH, setup_shop

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "app" / "i18n"
SHIPPED = {"en": "English", "fr": "Français", "it": "Italiano", "rm-vallader": "Rumantsch (Vallader)"}


def _cat(code: str) -> dict:
    return json.loads((I18N / f"{code}.json").read_text(encoding="utf-8"))


def _bon_text(data: bytes) -> list[str]:
    t = re.sub(rb"\x1d[!B].|\x1b[aEtd3J-].|\x1b2|\x1d\x56..?|\x1b@", b"", data, flags=re.S)
    return [line.strip() for line in t.decode("cp858", "replace").splitlines()]


@pytest.mark.parametrize("code", sorted(SHIPPED))
def test_shipped_language_is_complete(code):
    de, cat = _cat("de"), _cat(code)
    assert cat["_meta"]["name"] == SHIPPED[code]
    assert set(cat) == set(de), "gleiche Schlüssel wie de.json"
    for k, v in de.items():
        if k == "_meta":
            continue
        assert cat[k] not in (None, ""), k
        assert isinstance(cat[k], dict) == isinstance(v, dict), f"{k}: Mehrzahl wie im Deutschen"
    # Wörter, die übersetzt sein müssen (Stichprobe – fiele auf, wenn Deutsch kopiert wurde)
    for k in ("app.order.send", "app.common.cancel", "admin.overview.todo", "status.head.all_ok", "bon.warn.title"):
        assert cat[k] != de[k], k


def test_checker_finds_nothing_open():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "i18n_check.py")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    assert "HINWEIS" not in r.stdout, r.stdout                                  # nichts «noch nicht übersetzt»


def test_language_list_and_variants():
    from app import i18n
    assert [x["code"] for x in i18n.available()] == ["de", "en", "fr", "it", "rm-vallader"]
    assert i18n.normalize("rm") == "rm-vallader" and i18n.normalize("rm-CH") == "rm-vallader"
    assert i18n.normalize("fr-CH") == "fr" and i18n.normalize("IT") == "it" and i18n.normalize("en_GB") == "en"
    assert i18n.normalize("es") is None
    assert i18n.t("app.cart.show_n", "fr", count=0) == "Afficher 0 article"           # fr: 0 ist Einzahl
    assert i18n.t("app.cart.show_n", "rm-vallader", count=2) == "Muossar 2 artichels"


EXPECT_TABLE_UNKNOWN = {
    None: "Tisch 999 ist nicht erfasst",
    "en": "Table 999 is not registered",
    "fr": "Table 999 n’est pas enregistrée",
    "it": "Tavolo 999 non è registrato",
    "rm-vallader": "Maisa 999 nun es registrada",
}


def test_server_messages_in_every_language(client):
    setup_shop(client)
    client.put("/api/admin/settings", auth=AUTH, json={"tables_mode": "strict"})
    pid = client.get("/api/bootstrap").json()["products"][0]["id"]
    for lang, want in EXPECT_TABLE_UNKNOWN.items():
        body = {"client_uuid": str(uuid.uuid4()), "table": "999", "lines": [{"product_id": pid, "qty": 1}]}
        r = client.post("/api/orders", json=body, headers={"X-Festpos-Lang": lang} if lang else {})
        assert r.status_code == 422 and r.json()["detail"] == want
    # Sprachdatei für die Browser-Seiten, auch über die kurze Form «rm»
    assert client.get("/i18n/rm.json").json()["app.order.send"] == "Trametter"
    assert client.put("/api/admin/settings", auth=AUTH, json={"language": "rm"}).json()["language"] == "rm-vallader"
    assert client.get("/api/bootstrap").json()["language"] == "rm-vallader"
    assert {x["code"] for x in client.get("/api/bootstrap").json()["languages"]} >= set(SHIPPED)


@pytest.mark.parametrize("code,reprint,foot", [
    ("en", "REPRINT", "*** TEST ORDER ***"),
    ("fr", "RÉIMPRESSION", "*** COMMANDE TEST ***"),
    ("it", "RISTAMPA", "*** ORDINAZIONE DI PROVA ***"),
    ("rm-vallader", "STAMPA DA NOUV", "*** ORDINAZIUN DA TEST ***"),
])
@pytest.mark.parametrize("width", [32, 42, 48])
def test_bons_in_every_language_print_cleanly(code, reprint, foot, width):
    from app.print_service import PrinterCfg, note_text, render_table_bon, render_test_bon, warn_note
    p = PrinterCfg(1, "Bar", "x", 9100, width, True, True, code)
    items = [{"name": "Bier", "price_cents": 650, "qty": 1, "category": None},
             {"name": "Wein", "price_cents": 500, "qty": 2, "category": "Wein"}]
    raw = render_table_bon(p, p, event_name="Fest", currency="CHF", table="4", order_id=9,
                           created_at="2026-10-10T20:00:00", items=items, reprint=True, test=True)
    text = _bon_text(raw)
    assert "?" not in "\n".join(text), text                                   # jedes Zeichen im Zeichensatz PC858
    assert any(reprint in line for line in text) and foot in text
    assert all(len(line) <= width for line in text), [line for line in text if len(line) > width]
    note = " ".join(text)
    assert _cat(code)["bon.test.note"].split()[0] in note                     # Testhinweis umgebrochen, nicht abgeschnitten
    assert _cat(code)["bon.test.note"].split()[-1] in note
    test_print = "\n".join(_bon_text(render_test_bon(p)))
    assert "?" not in test_print and "äöü ÄÖÜ éèà ç €" in test_print
    warn = note_text(warn_note("Küche", "19:30", "paper_out", 3), code)
    assert "Küche" in warn and "19:30" in warn and "3" in warn


def test_oe_is_printable():
    from app.escpos import Bon
    out = Bon(48).text("Bœuf Œuf")._buf
    assert out.endswith(b"Boeuf OEuf")
