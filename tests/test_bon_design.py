"""Bons & Quittung (app/bon_design.py, T24 Bon-Baukasten): Einstellung, Bon-Logo, eigene Bilder, Vorschau,
Probedruck, Gestaltung der Quittung festgehalten (Kopie wie das Original, Logo bleibt), Pflicht-Bausteine,
ältere Quittungen unverändert, Küchenbons (Tischnummer, Kategorien, Lieferzeitpunkt)."""
from __future__ import annotations

import io
import json
import re

from tests.test_api import AUTH, setup_shop


def _png(color="black", size=(300, 120)) -> bytes:
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle((10, 10, size[0] - 10, size[1] - 10), fill=color)
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def _text(data: bytes) -> str:
    t = re.sub(rb"\x1d[!B].|\x1b[aEtd3JM-].|\x1b2|\x1d\x56..?|\x1b@", b"", data, flags=re.S)
    return t.decode("cp858", "replace")


def _find(blocks: list[dict], typ: str) -> dict:
    return next(b for b in blocks if b["type"] == typ)


def _types(blocks: list[dict]) -> list[str]:
    return [b["type"] for b in blocks]


V1 = {"receipt": {"logo": True, "head_big": False, "show_table": False, "show_order": False, "show_device": False,
                  "qr": "https://example.org/spende", "qr_text": "Danke fürs Spenden"},
      "ticket": {"logo": False, "table_size": "huge", "categories": False, "time_device": False, "spacing": "compact",
                 "beep": True}}


def test_view_schema_and_old_form(client):
    d = client.get("/api/admin/bon-design", auth=AUTH).json()
    assert d["version"] == 2 and d["logo"] is None
    assert _types(d["receipt"]["blocks"])[0] == "top" and _types(d["receipt"]["blocks"])[-1] == "check"
    assert d["receipt"]["template"] == "standard" and d["ticket"]["template"] == "standard"
    assert d["schema"]["receipt"]["header"]["req"] and d["schema"]["ticket"]["delivery"]["req"]
    assert {"text", "rule", "space", "image", "qr"} <= {k for k, s in d["schema"]["receipt"].items() if s["add"]}
    assert d["schema"]["ticket"]["datetime"]["add"] and "datetime" not in d["schema"]["receipt"]
    assert d["delivery_times"][0] == "Sofort" and d["table_word"] == "Tisch"
    assert _find(d["ticket"]["blocks"], "delivery")["when"] is None       # Standard: alle ausser «Sofort»
    # alte Form (Version 1) wird noch angenommen und in Bausteine übersetzt
    r = client.put("/api/admin/bon-design", auth=AUTH, json=V1)
    assert r.status_code == 200, r.text
    d = r.json()
    assert _find(d["ticket"]["blocks"], "table")["size"] == "3" and _find(d["ticket"]["blocks"], "beep")["on"]
    assert _find(d["ticket"]["blocks"], "items")["spacing"] == "compact" and not _find(d["ticket"]["blocks"], "time")["on"]
    assert _find(d["receipt"]["blocks"], "qr")["data"] == "https://example.org/spende"
    assert _find(d["receipt"]["blocks"], "header")["size"] == "1"
    bad = {**V1, "ticket": {**V1["ticket"], "table_size": "riesig"}}
    assert client.put("/api/admin/bon-design", auth=AUTH, json=bad).status_code == 422
    from app import auth
    assert auth.required_role("PUT", "/api/admin/bon-design") == "admin"
    assert auth.required_role("POST", "/api/admin/bon-design/test") == "admin"


def test_blocks_rules_required_fixed_and_texts(client):
    d = client.get("/api/admin/bon-design", auth=AUTH).json()
    rec, tic = d["receipt"]["blocks"], d["ticket"]["blocks"]
    # Reihenfolge ändern, Freitext dazu, Kopf- und Fusstext mitschicken; KOPIE/Prüfnummer «falsch» hingestellt
    rec = [b for b in rec if b["type"] not in ("top", "check")]
    _find(rec, "header")["text"] = "Verein Muster\n  Musterweg 1 \n\n6000 Luzern"
    _find(rec, "footer")["text"] = "Merci und bis bald!"
    rec = [{"type": "check", "on": True}, *rec[:3], {"type": "text", "on": True, "text": "Danke!", "size": "2",
                                                     "bold": True}, *rec[3:], {"type": "top", "on": True}]
    tic = [b for b in tic if b["type"] != "head"]                        # fehlt: wird ergänzt (ausgeschaltet)
    _find(tic, "delivery")["when"] = ["*"]
    r = client.put("/api/admin/bon-design", auth=AUTH, json={"receipt": {"blocks": rec, "template": "standard"},
                                                            "ticket": {"blocks": tic}})
    assert r.status_code == 200, r.text
    d = r.json()
    types = _types(d["receipt"]["blocks"])
    assert types[0] == "top" and types[-1] == "check" and "text" in types
    assert _find(d["receipt"]["blocks"], "header")["text"] == "Verein Muster\nMusterweg 1\n6000 Luzern"
    assert _find(d["receipt"]["blocks"], "footer")["text"] == "Merci und bis bald!"
    s = client.get("/api/admin/settings", auth=AUTH).json()
    assert s["receipt_header"].startswith("Verein Muster") and s["receipt_footer"] == "Merci und bis bald!"
    head = _find(d["ticket"]["blocks"], "head")
    assert head["on"] is False and _find(d["ticket"]["blocks"], "delivery")["when"] == ["*"]
    from app import main
    stored = json.loads(main.store.setting("bon_design"))
    assert stored["version"] == 2 and "text" not in _find(stored["receipt"]["blocks"], "header")
    # PFLICHT nicht ausblenden, unbekannte Bausteine/Werte, zu viele Bilder -> 422
    blocks = d["receipt"]["blocks"]
    for change in ({"type": "items", "on": False}, {"type": "hack"}, {"type": "title", "size": "9"},
                   {"type": "check", "on": False}):
        bad = [({**b, **change} if b["type"] == change["type"] else b) for b in blocks]
        if change["type"] == "hack":
            bad.append(change)
        r = client.put("/api/admin/bon-design", auth=AUTH, json={"receipt": {"blocks": bad}, "ticket": {"blocks": tic}})
        assert r.status_code == 422, change
    many = blocks + [{"type": "qr", "data": "x"}] * 4
    assert client.put("/api/admin/bon-design", auth=AUTH,
                      json={"receipt": {"blocks": many}, "ticket": {"blocks": tic}}).status_code == 422
    two_heads = blocks + [{"type": "header"}]
    assert client.put("/api/admin/bon-design", auth=AUTH,
                      json={"receipt": {"blocks": two_heads}, "ticket": {"blocks": tic}}).status_code == 422
    # Drucker für Quittungen: muss es geben
    assert client.put("/api/admin/bon-design", auth=AUTH, json={"receipt": {"blocks": blocks}, "ticket": {"blocks": tic},
                                                                "receipt_printer_id": 999}).status_code == 422


def test_logo_image_preview_and_sample(client):
    d = client.post("/api/admin/bon-design/logo", auth=AUTH, files={"file": ("l.png", _png(), "image/png")}).json()
    assert d["logo"].startswith("/media/")
    img = client.post("/api/admin/bon-design/image", auth=AUTH, files={"file": ("i.png", _png("gray"), "image/png")}).json()
    assert re.match(r"^[0-9a-f]{20}$", img["id"]) and client.get(img["url"]).status_code == 200
    v = client.get("/api/admin/bon-design", auth=AUTH).json()
    rec = v["receipt"]["blocks"]
    _find(rec, "logo")["on"] = True
    rec.insert(3, {"type": "image", "on": True, "image": img["id"], "width": "13", "align": "left"})
    rec.insert(4, {"type": "text", "on": True, "text": "Klein gedruckt", "size": "s"})
    for kind, design in (("receipt", {"blocks": rec}), ("ticket", None)):
        r = client.post("/api/admin/bon-design/preview", auth=AUTH, json={"kind": kind, "width": 32, "design": design})
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
        where = json.loads(r.headers["x-blocks"])
        assert where and all(b > a for _, a, b in where)
        if kind == "receipt":                                            # Logo, Bild und Freitext sind zu sehen
            seen = {i for i, _, _ in where}
            assert {1, 3, 4} <= seen
    # Lieferzeitpunkt «immer»: auch mit Sofort sichtbar -> ein Baustein mehr in der Vorschau
    tic = v["ticket"]["blocks"]
    n_not = len(json.loads(client.post("/api/admin/bon-design/preview", auth=AUTH, json={
        "kind": "ticket", "design": {"blocks": tic}, "delivery": "Sofort"}).headers["x-blocks"]))
    _find(tic, "delivery")["when"] = ["*"]
    n_always = len(json.loads(client.post("/api/admin/bon-design/preview", auth=AUTH, json={
        "kind": "ticket", "design": {"blocks": tic}, "delivery": "Sofort"}).headers["x-blocks"]))
    assert n_always == n_not + 1
    assert client.post("/api/admin/bon-design/preview", json={"kind": "receipt"}).status_code == 401
    # Bild wird beim Speichern behalten, ohne Verweis wieder entfernt
    r = client.put("/api/admin/bon-design", auth=AUTH, json={"receipt": {"blocks": rec}, "ticket": {"blocks": tic}})
    assert r.status_code == 200 and client.get(img["url"]).status_code == 200
    rec = [b for b in rec if b["type"] != "image"]
    client.put("/api/admin/bon-design", auth=AUTH, json={"receipt": {"blocks": rec}, "ticket": {"blocks": tic}})
    assert client.get(img["url"]).status_code == 404
    # Probedruck: unbekannter Drucker 404, Drucker nicht erreichbar 502 (nichts in der Warteschlange)
    pid = setup_shop(client)
    assert client.post("/api/admin/bon-design/test", auth=AUTH, json={"kind": "receipt", "printer_id": 999}).status_code == 404
    from app import main
    before = main.store.scalar("SELECT COUNT(*) FROM print_jobs")
    r = client.post("/api/admin/bon-design/test", auth=AUTH, json={"kind": "ticket", "printer_id": pid})
    assert r.status_code in (200, 502)
    assert main.store.scalar("SELECT COUNT(*) FROM print_jobs") == before
    assert main.store.scalar("SELECT COUNT(*) FROM receipts") == 0


def test_sample_banner_and_send(client, monkeypatch):
    from app import escpos, print_service as ps
    pid = setup_shop(client)
    sent = []

    async def fake_send(addr, payload, **kw):
        sent.append(payload)
        return True
    monkeypatch.setattr(escpos, "send_raw", fake_send)
    r = client.post("/api/admin/bon-design/test", auth=AUTH, json={"kind": "receipt", "printer_id": pid})
    assert r.status_code == 200, r.text
    txt = _text(sent[0])
    assert "MUSTER" in txt and "QUITTUNG" in txt and "Prüfnr." in txt
    dev = ps.PrinterCfg(1, "Bar", "x", 9100, 32, True, True)
    assert "MUSTER" in _text(ps.render_sample_banner(dev))


def test_receipt_keeps_design_and_images_for_copies(client):
    from app import bon_design, main, print_service as ps
    pid = setup_shop(client)
    client.put("/api/admin/settings", auth=AUTH, json={"receipt_printer_id": pid, "receipt_header": "Verein Muster\nDorf 1"})
    first_logo = client.post("/api/admin/bon-design/logo", auth=AUTH, files={"file": ("l.png", _png(), "image/png")}).json()["logo"]
    v = client.get("/api/admin/bon-design", auth=AUTH).json()
    rec = v["receipt"]["blocks"]
    _find(rec, "logo")["on"] = True
    info = _find(rec, "info")
    info.update(table=False, order=False, device=False)
    rec.insert(-1, {"type": "qr", "data": "https://example.org/spende", "caption": "Danke fürs Spenden"})
    assert client.put("/api/admin/bon-design", auth=AUTH, json={"receipt": {"blocks": rec}, "ticket": v["ticket"]}).status_code == 200
    prods = {p["name"]: p["id"] for p in client.get("/api/admin/products", auth=AUTH).json()}
    o = client.post("https://testserver:443/api/orders", json={"client_uuid": "rcpt-design-1", "table": "4",
                                                               "lines": [{"product_id": prods["Bier 5dl"], "qty": 2}]}).json()
    r = client.post(f"/api/admin/orders/{o['order_id']}/receipt", auth=AUTH, json={"request_id": "rq-design-1"})
    assert r.status_code == 200, r.text
    rec_row = main.store.query_one("SELECT * FROM receipts WHERE request_id='rq-design-1'")
    data = json.loads(rec_row["data"])
    assert data["design"]["version"] == 2
    assert _find(data["design"]["blocks"], "logo")["image"] == first_logo.split("/")[-1]
    assert "text" not in _find(data["design"]["blocks"], "header")
    # neues Logo und andere Gestaltung: die Kopie bleibt wie das Original, das alte Logo wird nicht gelöscht
    client.post("/api/admin/bon-design/logo", auth=AUTH, files={"file": ("l2.png", _png("gray", (200, 200)), "image/png")})
    v = client.get("/api/admin/bon-design", auth=AUTH).json()
    plain = [b for b in v["receipt"]["blocks"] if b["type"] != "qr"]
    client.put("/api/admin/bon-design", auth=AUTH, json={"receipt": {"blocks": plain}, "ticket": v["ticket"]})
    assert client.get(first_logo).status_code == 200
    dev = ps.PrinterCfg(1, "Bar", "x", 9100, 48, True, True)
    images = bon_design.load_images(main.store, bon_design.image_ids(data["design"]["blocks"]))
    raw = ps.render_receipt(dev, rec_row, copy=True, images=images)
    txt = _text(raw)
    assert b"\x1dv0" in raw                                              # Logo als Rasterbild
    assert b"\x1d(k" in raw                                              # QR-Code
    for must in ("QUITTUNG", "Nr. ", "TOTAL", "Verein Muster", "Prüfnr.", "KOPIE"):   # Pflichtangaben bleiben
        assert must in txt, must
    assert txt.index("KOPIE") < txt.index("Verein Muster")
    assert "Tisch 4" not in txt and "Bestellung #" not in txt            # abgewählt
    assert "Danke fürs Spenden" in txt
    # über die Warteschlange gedruckt (render_job): dieselben Bilder
    job = {"printer_id": pid, "station_id": pid, "kind": "receipt", "note": "rq-design-1", "reprint": 1}
    assert ps.render_job(main.store, job, ps.load_printers(main.store)) == ps.render_receipt(
        ps.load_printers(main.store)[pid], rec_row, copy=True, lang=ps.load_printers(main.store)[pid].lang, images=images)


def test_old_receipt_without_design_prints_as_before():
    from app import print_service as ps
    dev = ps.PrinterCfg(1, "Bar", "x", 9100, 48, True, True)
    rec = {"id": 1, "no": 5, "order_id": 3, "created_at": "2026-10-10T20:00:00", "total_cents": 650, "test": 0,
           "digest": "h2:abcdef123456", "lines": json.dumps([{"line_no": 1, "name": "Bier", "qty": 1, "price": 650, "amount": 650}]),
           "data": json.dumps({"header": ["Verein"], "table": "7", "table_word": "Tisch", "order_no": 3, "device": "XCover 1"})}
    raw = ps.render_receipt(dev, rec)
    txt = _text(raw)
    assert "Tisch 7" in txt and "Bestellung #0003" in txt and "XCover 1" in txt
    assert b"\x1dv0" not in raw and b"\x1d(k" not in raw


def test_ticket_blocks():
    from app import bon_design as bd, print_service as ps
    dev = ps.PrinterCfg(1, "Küche", "x", 9100, 48, True, True)
    items = [{"qty": 1, "name": "Wurst", "price_cents": 800, "category": "Essen"},
             {"qty": 1, "name": "Bier", "price_cents": 650, "category": "Getränke"}]
    common = dict(event_name="Fest", currency="CHF", table="12", order_id=1, created_at="2026-11-02T19:00:00", items=items,
                  device_name="XCover 1")
    normal = ps.render_table_bon(dev, dev, **common)
    assert b"\x1d!\x11" in normal and "Essen" in _text(normal) and "19:00" in _text(normal)
    huge = ps.render_table_bon(dev, dev, design=V1["ticket"], **common)
    assert b"\x1d!\x22" in huge                                          # Tischnummer dreifach
    assert "Essen" not in _text(huge) and "19:00" not in _text(huge)
    assert b"\x1b(A" in huge                                             # Signalton
    # Bausteine: riesig, weiss auf schwarz, ohne Wort, Zeit vor der Tischnummer, Freitext und Datum
    blocks = bd.clean("ticket", [{"type": "time"}, {"type": "table", "size": "4", "invert": True, "word": False,
                                                    "align": "center"},
                                 {"type": "text", "text": "Bon bitte abgeben", "invert": True},
                                 {"type": "datetime", "size": "h"}])
    raw = ps.render_table_bon(dev, dev, design={"blocks": blocks}, **common)
    txt = _text(raw)
    assert b"\x1d!\x33" in raw and "TISCH" not in txt and "12" in txt
    assert txt.index("19:00") < txt.index("12 ") and "Bon bitte abgeben" in txt and "02.11.2026 19:00" in txt
    from app import bon_preview
    assert bon_preview.render_png(huge)[:4] == b"\x89PNG"


def test_delivery_always_or_not_now_and_wraps():
    """Lieferzeitpunkt: Standard «nicht bei Sofort» (wie bisher); «immer» druckt auch SOFORT. Lange Namen werden
    umgebrochen statt abgeschnitten (58 mm)."""
    from app import bon_design as bd, print_service as ps
    dev = ps.PrinterCfg(1, "Küche", "x", 9100, 32, True, True)
    items = [{"qty": 1, "name": "Wurst", "price_cents": 800}]
    common = dict(event_name="Fest", currency="CHF", table="12", order_id=1, created_at="2026-11-02T19:00:00", items=items,
                  now_label="Sofort")
    std = {"blocks": bd.template_blocks("ticket", "standard")}
    always = {"blocks": [({**b, "show": "always"} if b["type"] == "delivery" else b) for b in std["blocks"]]}
    for when in (None, "Pause", "Schluss"):
        a = _text(ps.render_table_bon(dev, dev, design=std, delivery=when, **common))
        b = _text(ps.render_table_bon(dev, dev, design=always, delivery=when, **common))
        assert ("SOFORT" in a) is False and ("SOFORT" in b) is (when is None)
        if when:
            assert when.upper() in a and when.upper() in b
    # jede Lieferzeit einzeln wählbar – nur «Schluss» (und «Sofort») drucken, «Pause» nicht
    pick = {"blocks": [({**b, "when": ["Sofort", "schluss"]} if b["type"] == "delivery" else b) for b in std["blocks"]]}
    assert "SOFORT" in _text(ps.render_table_bon(dev, dev, design=pick, delivery=None, **common))
    assert "PAUSE" not in _text(ps.render_table_bon(dev, dev, design=pick, delivery="Pause", **common))
    assert "SCHLUSS" in _text(ps.render_table_bon(dev, dev, design=pick, delivery="Schluss", **common))
    none = {"blocks": [({**b, "when": []} if b["type"] == "delivery" else b) for b in std["blocks"]]}
    assert "PAUSE" not in _text(ps.render_table_bon(dev, dev, design=none, delivery="Pause", **common))
    # Preise einmal für alle (Bons & Quittung › Positionen), nicht mehr pro Drucker
    noprice = ps.PrinterCfg(2, "Küche", "x", 9100, 48, False, True)
    for on in (True, False):
        d = {"blocks": [({**k, "prices": on} if k["type"] == "items" else k) for k in std["blocks"]]}
        assert ("CHF" in _text(ps.render_table_bon(noprice, noprice, design=d, **common))) is on
        assert ("CHF" in _text(ps.render_table_bon(dev, dev, design=d, **common))) is on
    long = _text(ps.render_table_bon(dev, dev, design=std, delivery="Nach dem Theater", **common))
    assert "NACH DEM" in long and "THEATER" in long                     # 16 Zeichen pro Zeile: umgebrochen


def test_storno_keeps_layout_takes_logo_and_table():
    from app import bon_design as bd, print_service as ps
    from tests.bon_golden import logo
    dev = ps.PrinterCfg(1, "Küche", "x", 9100, 48, True, True)
    kw = dict(event_name="Fest", table="12", order_id=1, created_at="2026-11-02T19:00:00", by="XCover 1",
              reason="Gast", items=[{"qty": 1, "name": "Wurst"}])
    plain = ps.render_storno_bon(dev, dev, **kw)
    assert ps.render_storno_bon(dev, dev, design={"blocks": bd.clean("ticket", [])}, **kw) == plain
    blocks = bd.clean("ticket", [{"type": "logo", "on": True}, {"type": "table", "size": "3", "invert": True},
                                 {"type": "text", "text": "kommt nicht auf den Storno-Bon"}])
    raw = ps.render_storno_bon(dev, dev, design={"blocks": blocks}, logo=logo(), **kw)
    assert b"\x1dv0" in raw and b"\x1d!\x22" in raw and b"\x1dB\x01" in raw
    assert "kommt nicht" not in _text(raw) and "STORNO" in _text(raw)


def test_spacing_in_printer_units_and_preview_match():
    """Rückmeldung 2.11.: Der TM-T20III zählt ESC 3 / ESC J in 1/360 Zoll (Druckkopf 203 dpi). Gesendet wird umgerechnet,
    die Vorschau liest es genauso zurück – 40 Punkte Zeilenabstand sind auf Papier und in der Vorschau 40 Punkte."""
    from PIL import Image

    from app import bon_preview
    from app.escpos import Bon, motion_dots, motion_units
    assert motion_units(40) == 71 and abs(motion_dots(71) - 40) < 0.1 and motion_units(1000) == 255

    def height(spacing):
        b = Bon(48).line_spacing(spacing)
        for n in range(10):
            b.line(f"Zeile {n}")
        return Image.open(io.BytesIO(bon_preview.render_png(b.cut().build()))).height

    # zehn Zeilen: mit 40 Punkten 160 Punkte höher als mit 24 (Schrifthöhe), bei 16 Punkten bleibt es bei 24 (Schrift)
    assert height(40) - height(24) == 160
    assert height(16) == height(24)
