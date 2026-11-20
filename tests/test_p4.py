"""Version 2026.10.10-8: Bilder löschen, Einstellungen für die Handys (Bilder, Wischen, Einführung), Testmodus,
Übersetzung (Sprachdateien, Sprachwahl, Bon-Sprache pro Drucker, Fehlercodes statt Textvergleich)."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from tests.i18n_pseudo import leftover_words, write_pseudo
from tests.test_api import AUTH, setup_shop

ROOT = Path(__file__).resolve().parent.parent


def _order(client, test=False, table="3", headers=None, qty=1):
    pid = client.get("/api/bootstrap").json()["products"][0]["id"]
    body = {"client_uuid": str(uuid.uuid4()), "table": table, "lines": [{"product_id": pid, "qty": qty}]}
    if test:
        body["test"] = True
    return client.post("/api/orders", json=body, headers=headers or {})


def _bon_text(data: bytes) -> list[str]:
    t = re.sub(rb"\x1d[!B].|\x1b[aEtd3J-].|\x1b2|\x1d\x56..?|\x1b@", b"", data, flags=re.S)
    return [line.strip() for line in t.decode("cp858", "replace").splitlines()]


# ------------------------------------------------------------------ Bilder
def _jpeg(color: str) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (900, 600), color).save(buf, "JPEG")
    return buf.getvalue()


def test_image_delete_and_unused_images_removed(client):
    from app import core
    setup_shop(client)
    pid = client.get("/api/bootstrap").json()["products"][0]["id"]
    first = client.post(f"/api/admin/products/{pid}/image", auth=AUTH, files={"file": ("a.jpg", _jpeg("red"))}).json()
    second = client.post(f"/api/admin/products/{pid}/image", auth=AUTH, files={"file": ("b.jpg", _jpeg("blue"))}).json()
    assert first["image_id"] != second["image_id"]
    assert client.get(first["url"]).status_code == 404            # ersetztes Bild ist weg (liegt in der DB)
    assert core.rt.store.scalar("SELECT COUNT(*) FROM images") == 1
    assert client.delete(f"/api/admin/products/{pid}/image", auth=AUTH).json() == {"ok": True}
    assert client.get(second["url"]).status_code == 404
    assert core.rt.store.scalar("SELECT COUNT(*) FROM images") == 0
    assert client.get("/api/bootstrap").json()["products"][0]["image_id"] is None
    assert client.delete("/api/admin/products/99999/image", auth=AUTH).status_code == 404


def test_shared_image_stays_while_used(client):
    from app import core
    setup_shop(client)
    a, b = [p["id"] for p in client.get("/api/bootstrap").json()["products"][:2]]
    img = _jpeg("green")
    r1 = client.post(f"/api/admin/products/{a}/image", auth=AUTH, files={"file": ("x.jpg", img)}).json()
    client.post(f"/api/admin/products/{b}/image", auth=AUTH, files={"file": ("x.jpg", img)})
    client.delete(f"/api/admin/products/{a}/image", auth=AUTH)
    assert client.get(r1["url"]).status_code == 200                # b verwendet es noch
    assert core.rt.store.scalar("SELECT COUNT(*) FROM images") == 1


# ------------------------------------------------------------------ Einstellungen für die Handys
def test_phone_settings_in_bootstrap(client):
    boot = client.get("/api/bootstrap").json()
    assert boot["product_images"] == "small" and boot["swipe_categories"] is True and boot["tutorial_auto"] is True
    assert boot["language"] == "de" and boot["languages"][0]["code"] == "de"
    r = client.put("/api/admin/settings", auth=AUTH, json={"product_images": "large", "swipe_categories": False,
                                                            "tutorial_auto": False})
    assert r.status_code == 200
    boot = client.get("/api/bootstrap").json()
    assert boot["product_images"] == "large" and boot["swipe_categories"] is False and boot["tutorial_auto"] is False
    assert client.put("/api/admin/settings", auth=AUTH, json={"product_images": "riesig"}).status_code == 422


# ------------------------------------------------------------------ Testmodus
def _enable_test(client, pin="4321", print_=False):
    r = client.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": pin, "test_print": print_})
    assert r.status_code == 200, r.text
    return r.json()


def test_test_mode_needs_pin_and_is_off_by_default(client):
    setup_shop(client)
    assert client.get("/api/bootstrap").json()["test"]["enabled"] is False
    r = _order(client, test=True)
    assert r.status_code == 422 and "Testmodus" in r.json()["detail"]
    r = client.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True})
    assert r.status_code == 422 and "Test-PIN" in r.json()["detail"]
    s = _enable_test(client)
    assert s["test_pin_set"] is True and "test_pin_hash" not in s and "test_pin_salt" not in s
    cfg = client.get("/api/bootstrap").json()["test"]
    assert cfg["enabled"] and cfg["length"] == 4 and cfg["print"] is False
    assert cfg["hash"] == hashlib.sha256((cfg["salt"] + "4321").encode()).hexdigest()   # Handy prüft offline


def test_test_orders_are_separate_and_not_printed(client):
    from app import core
    setup_shop(client)
    _enable_test(client, print_=False)
    real = _order(client).json()
    test = _order(client, test=True).json()
    assert test["order_id"] != real["order_id"] and test["warnings"] == []
    assert core.rt.store.scalar("SELECT test FROM orders WHERE id=?", (test["order_id"],)) == 1
    assert core.rt.store.scalar("SELECT COUNT(*) FROM print_jobs WHERE order_id=?", (test["order_id"],)) == 0
    st = client.get("/api/admin/stats", auth=AUTH).json()
    assert st["orders"] == 1 and st["total_cents"] == real["total_cents"] and st["test_orders"] == 1
    csv = client.get("/api/admin/export/orders.csv", auth=AUTH).content.decode("utf-8-sig")
    assert csv.count("\r\n") == 2                                      # Kopf + echte Bestellung, keine Übung
    status = client.get("/api/status", auth=AUTH).json()
    assert status["totals"]["count"] == 1
    assert {o["id"]: o["test"] for o in status["recent"]} == {real["order_id"]: 0, test["order_id"]: 1}
    # löschen: nur die Übungen
    assert client.post("/api/admin/test-orders/delete?confirm=JA", auth=AUTH).json() == {"deleted_orders": 1}
    assert core.rt.store.scalar("SELECT COUNT(*) FROM orders") == 1
    assert client.post("/api/admin/test-orders/delete?confirm=nein", auth=AUTH).status_code == 400


def test_test_orders_printed_with_big_marks(client):
    from app import core
    from app.print_service import load_printers, render_job
    setup_shop(client)
    _enable_test(client, print_=True)
    o = _order(client, test=True).json()
    jobs = core.rt.store.query("SELECT * FROM print_jobs WHERE order_id=?", (o["order_id"],))
    assert jobs
    lines = _bon_text(render_job(core.rt.store, jobs[0], load_printers(core.rt.store)))
    assert lines[0] == "TEST" and "nicht zubereiten" in lines[1] and "*** TESTBESTELLUNG ***" in lines
    labels = [j["label"] for j in client.get("/api/admin/jobs", auth=AUTH).json()]
    assert labels and all(lab.startswith("TEST ") for lab in labels)
    # normale Bestellung: kein Testvermerk
    real = _order(client).json()
    job = core.rt.store.query("SELECT * FROM print_jobs WHERE order_id=?", (real["order_id"],))[0]
    text = "\n".join(_bon_text(render_job(core.rt.store, job, load_printers(core.rt.store))))
    assert "TEST" not in text


def test_test_mode_shown_for_phone_in_status(client):
    setup_shop(client)
    client.get("/api/bootstrap", headers={"X-Festpos-Device": "XCover%205", "X-Festpos-Test": "1"})
    devs = {d["name"]: d for d in client.get("/api/status", auth=AUTH).json()["devices"]}
    assert devs["XCover 5"]["test"] is True
    client.get("/api/bootstrap", headers={"X-Festpos-Device": "XCover%205", "X-Festpos-Test": "0"})
    devs = {d["name"]: d for d in client.get("/api/status", auth=AUTH).json()["devices"]}
    assert devs["XCover 5"]["test"] is False


# ------------------------------------------------------------------ Übersetzung
def test_catalog_route_auto_etag_and_unknown(client):
    r = client.get("/i18n/auto.json")
    cat = r.json()
    assert r.status_code == 200 and cat["_meta"]["code"] == "de" and cat["app.order.send"] == "Senden"
    assert client.get("/i18n/auto.json", headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    assert client.get("/i18n/de.json").json()["_meta"]["code"] == "de"
    assert client.get("/i18n/xx.json").status_code == 404
    assert client.get("/i18n/..%2Fmain.json").status_code == 404


@pytest.fixture
def two_langs(tmp_path, monkeypatch):
    """Zusätzlich eine (unvollständige) englische Sprachdatei."""
    d = tmp_path / "i18n"
    d.mkdir()
    (d / "en.json").write_text(json.dumps({
        "_meta": {"name": "English", "locale": "en-GB"},
        "err.order.empty": "Order is empty",
        "err.order.table_unknown": "{word} {table} is not registered",
        "common.word.table": "Table",
        "bon.word.table": "Table",
        "bon.head.reprint": "REPRINT",
        "bon.foot.positions": {"one": "{count} item", "other": "{count} items"},
        "app.order.send": "Send",
    }), encoding="utf-8")
    monkeypatch.setenv("FESTPOS_I18N_DIR", str(d))
    return d


def test_language_per_request_and_fallback(client, two_langs):
    from app import i18n
    assert [x["code"] for x in i18n.available()] == ["de", "en", "fr", "it", "rm-vallader"]
    assert i18n._files()["en"].parent == two_langs                    # Test-Ordner geht der eingebauten Datei vor
    cat = client.get("/i18n/en.json").json()
    assert cat["app.order.send"] == "Send" and cat["app.pay.next"] == "Nächste Bestellung"   # Rest: Deutsch
    setup_shop(client)
    client.put("/api/admin/settings", auth=AUTH, json={"tables_mode": "strict"})
    pid = client.get("/api/bootstrap").json()["products"][0]["id"]
    body = {"client_uuid": str(uuid.uuid4()), "table": "999", "lines": [{"product_id": pid, "qty": 1}]}
    assert client.post("/api/orders", json=body, headers={"X-Festpos-Lang": "en"}).json()["detail"] == "Table 999 is not registered"
    assert client.post("/api/orders", json=body).json()["detail"] == "Tisch 999 ist nicht erfasst"
    # Standardsprache in der Verwaltung
    assert client.put("/api/admin/settings", auth=AUTH, json={"language": "xx"}).status_code == 422
    assert client.put("/api/admin/settings", auth=AUTH, json={"language": "en-GB"}).json()["language"] == "en"
    assert client.get("/api/bootstrap").json()["language"] == "en"
    assert client.post("/api/orders", json=body).json()["detail"] == "Table 999 is not registered"
    assert client.get("/i18n/auto.json").json()["_meta"]["code"] == "en"


def test_printer_language_for_bons(client, two_langs):
    from app import core
    from app.print_service import PrinterCfg, render_table_bon
    setup_shop(client)
    pid = client.get("/api/admin/printers", auth=AUTH).json()[0]["id"]
    base = {"name": "Bar", "host": "127.0.0.1", "port": 1}
    assert client.put(f"/api/admin/printers/{pid}", auth=AUTH, json={**base, "language": "xx"}).status_code == 422
    assert client.put(f"/api/admin/printers/{pid}", auth=AUTH, json={**base, "language": "en"}).status_code == 200
    assert core.rt.store.scalar("SELECT language FROM printers WHERE id=?", (pid,)) == "en"
    p = PrinterCfg(1, "Bar", "x", 9100, 48, True, True, "en")
    items = [{"name": "Bier", "price_cents": 650, "qty": 1, "category": None}, {"name": "Wein", "price_cents": 500, "qty": 2, "category": None}]
    text = "\n".join(_bon_text(render_table_bon(p, p, event_name="Fest", currency="CHF", table="4", order_id=9,
                                                created_at="2026-10-10T20:00:00", items=items, reprint=True)))
    assert "REPRINT" in text and "2 items" in text and "TABLE 4" in text
    assert "TOTAL" in text                                            # fehlt auf Englisch: Deutsch (gleich)


def test_plural_rules_and_missing_key():
    from app import i18n
    assert i18n.plural_category("de", 1) == "one" and i18n.plural_category("de", 0) == "other"
    assert i18n.plural_category("fr", 0) == "one" and i18n.plural_category("fr", 2) == "other"
    assert i18n.plural_category("pl", 3) == "few" and i18n.plural_category("pl", 5) == "many"
    assert i18n.t("bon.foot.positions", "de", count=1) == "1 Position"
    assert i18n.t("bon.foot.positions", "de", count=3) == "3 Positionen"
    assert i18n.t("gibt.es.nicht") == "gibt.es.nicht"                  # fehlender Text: Schlüssel statt Absturz


def test_warn_bon_translated_at_print_time():
    from app.print_service import PrinterCfg, note_text, render_warn_bon, warn_note
    note = warn_note("Küche", "19:30", "paper_out", 3)
    assert json.loads(note)["key"] == "bon.warn.text"
    assert note_text(note, "de") == "Drucker Küche ist seit 19:30 ohne Papier. 3 Bons warten. Drucker, Papier und Kabel prüfen."
    assert note_text(warn_note("Bar", "20:00", "unreachable", 1), "de").startswith("Drucker Bar ist seit 20:00 nicht erreichbar. 1 Bon wartet")
    assert note_text("alter Hinweistext", "de") == "alter Hinweistext"        # ältere Warnbons: Text wie gespeichert
    p = PrinterCfg(2, "Bar", "x", 9100, 48, True, True)
    assert "ACHTUNG" in _bon_text(render_warn_bon(p, event_name="Fest", text=note, created_at="2026-10-10T20:00:00"))


def test_bons_fully_translated_in_pseudo_language(tmp_path, monkeypatch):
    """Alle Bon-Texte kommen aus der Sprachdatei: in der Pseudo-Sprache bleibt nur, was Daten sind."""
    write_pseudo(tmp_path, "qpb", open_="«", close="»")              # «» gibt es im Drucker-Zeichensatz
    monkeypatch.setenv("FESTPOS_I18N_DIR", str(tmp_path))
    from app.print_service import (PrinterCfg, note_text, render_qr_bon, render_single_bon, render_table_bon,
                                   render_test_bon, render_warn_bon, warn_note)
    dev = PrinterCfg(1, "Kasse", "10.0.0.9", 9100, 48, True, True, "qpb")
    station = PrinterCfg(2, "Grill", "10.0.0.8", 9100, 48, True, True, "qpb")
    items = [{"name": "Wurst", "price_cents": 800, "qty": 2, "category": "Essen", "options": "scharf", "note": "gut"},
             {"name": "Bier", "price_cents": 650, "qty": 1, "category": None, "options": None, "note": None}]
    common = dict(event_name="Anlass", currency="CHF", table="12", order_id=5, created_at="2026-10-10T20:00:00",
                  device_name="Handy", delivery="Pause")
    bons = [render_table_bon(dev, station, items=items, reprint=True, test=True, n=1, of=3, **common),
            render_single_bon(dev, station, item=items[0], n=2, of=3, reprint=True, test=True, **common),
            render_test_bon(dev), render_qr_bon(dev, event_name="Anlass", label="Bar"),
            render_warn_bon(dev, event_name="Anlass", text=warn_note("Grill", "19:00", "cover_open", 2),
                            created_at="2026-10-10T20:00:00")]
    data = {"Kasse", "Grill", "Wurst", "Bier", "Essen", "scharf", "gut", "Anlass", "CHF", "PAUSE", "Handy", "Bar",
            "table"}                                                  # «table:Bar» ist der Inhalt des QR-Codes
    for b in bons:
        text = "\n".join(_bon_text(b))
        assert not leftover_words(text, data, "«", "»"), (leftover_words(text, data, "«", "»"), text)
    assert note_text(warn_note("Grill", "19:00", "cover_open", 2), "qpb").startswith("«")


def test_server_messages_translated_in_pseudo_language(client, tmp_path, monkeypatch):
    write_pseudo(tmp_path, "qps")
    monkeypatch.setenv("FESTPOS_I18N_DIR", str(tmp_path))
    setup_shop(client)
    h = {"X-Festpos-Lang": "qps"}
    checks = [
        client.post("/api/orders", json={"client_uuid": "x" * 10, "table": "1", "lines": [{"product_id": 999, "qty": 1}]}, headers=h),
        client.post("/api/orders", json={"client_uuid": "y" * 10, "table": "1", "lines": [{"product_id": 1, "qty": 1}], "test": True}, headers=h),
        client.put("/api/admin/settings", auth=AUTH, json={"pin_enabled": True}, headers=h),
        client.get("/api/admin/products", headers=h),
        client.delete("/api/admin/products/999/image", auth=AUTH, headers=h),
        client.post("/api/admin/import/images", auth=AUTH, files={"file": ("x.zip", b"kein zip")}, headers=h),
        client.post("/api/admin/restore?confirm=JA", auth=AUTH, files={"file": ("x.db", b"kaputt")}, headers=h),
    ]
    for r in checks:
        assert r.status_code >= 400 and r.json()["detail"].startswith("⟦"), (r.status_code, r.text)
    _order(client)
    csv = "name;preis;drucker\nBier;-2;Nirgends\n".encode()
    rows = client.post("/api/admin/import/products", auth=AUTH, files={"file": ("p.csv", csv)}, headers=h).json()["rows"]
    assert rows[0]["errors"] and all(e.startswith("⟦") for e in rows[0]["errors"])
    jobs = client.get("/api/admin/jobs", auth=AUTH, headers=h).json()
    assert jobs and all(j["label"].startswith("⟦") for j in jobs)


def _load_checker(i18n_dir: Path):
    spec = importlib.util.spec_from_file_location("i18n_check_t", ROOT / "tools" / "i18n_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.I18N = i18n_dir
    return mod


def test_i18n_check_tool(tmp_path, capsys):
    assert subprocess.run([sys.executable, str(ROOT / "tools" / "i18n_check.py")], capture_output=True).returncode == 0
    d = tmp_path / "i18n"
    d.mkdir()
    src = json.loads((ROOT / "app" / "i18n" / "de.json").read_text(encoding="utf-8"))
    (d / "de.json").write_text(json.dumps({**src, "app.nie.verwendet": "x"}), encoding="utf-8")
    (d / "fr.json").write_text(json.dumps({"_meta": {"name": "Français"}, "err.order.table_unknown": "{mot} {table}",
                                           "app.inconnu.cle": "?", "bon.foot.positions": {"one": "{count} position"}}),
                               encoding="utf-8")
    chk = _load_checker(d)
    assert chk.main([]) == 1
    out = capsys.readouterr().out
    assert "app.nie.verwendet" in out and "app.inconnu.cle" in out
    assert "err.order.table_unknown: Platzhalter" in out and "mot" in out
    assert "Mehrzahl ohne" in out and "noch nicht übersetzt" in out
