"""Review 2026-10 (Sicherheit/Robustheit): Storno und Tagesabschluss zugleich, «Alle Bestellungen löschen» während
eine Bestellung eintrifft, Sicherung einspielen ohne Zeitfenster, Bilder-ZIP mit defekten Einträgen."""
from __future__ import annotations

import base64
import io
import sqlite3
import uuid
import zipfile

import pytest

from tests.test_api import AUTH
from tests.test_ledger import _order, shop  # noqa: F401 (shop = Fixture)


def _two_orders(store):
    from app.orders import create_order
    from tests.test_printing import seed
    seed(store, 9, 9)
    a = create_order(store, client_uuid=str(uuid.uuid4()), table_label="5", lines=[(1, 2)])
    b = create_order(store, client_uuid=str(uuid.uuid4()), table_label="6", lines=[(2, 1)])
    return a.order_id, b.order_id


def test_storno_rejected_when_closing_came_in_between(store, monkeypatch):
    """Storno hat die Bestellung noch offen gelesen, dann läuft der Abschluss: kein Storno zu einer abgeschlossenen
    Bestellung, auch keine Storno-Bons und kein Protokolleintrag."""
    from app import ledger
    a, _ = _two_orders(store)
    real = ledger.cancelled_qty
    state = {"done": False}

    def closing_meanwhile(st, order_id):
        res = real(st, order_id)
        if not state["done"]:
            state["done"] = True
            ledger.create_closing(st, "Lea", ledger.ClosingIn())
        return res
    monkeypatch.setattr(ledger, "cancelled_qty", closing_meanwhile)
    with pytest.raises(ledger.LedgerError) as e:
        ledger.cancel(store, order_id=a, lines=None, reason="wrong", by_user="Lea", client_uuid=str(uuid.uuid4()))
    assert e.value.status == 409
    assert store.scalar("SELECT COUNT(*) FROM cancellations") == 0
    assert store.scalar("SELECT COUNT(*) FROM print_jobs WHERE kind='storno'") == 0
    assert [r["action"] for r in store.query("SELECT action FROM audit_log ORDER BY id")] == ["closing"]


def test_closing_recomputes_when_storno_came_in_between(store, monkeypatch):
    """Während der Abschluss rechnet, wird storniert: der Abschluss rechnet neu und enthält das Storno."""
    from app import ledger
    a, _ = _two_orders(store)
    real = ledger.period_csv
    calls = []

    def storno_meanwhile(rows, *rest):
        calls.append(1)
        if len(calls) == 1:
            ledger.cancel(store, order_id=a, lines={1: 1}, reason="wrong", by_user="Anna", client_uuid=str(uuid.uuid4()))
        return real(rows, *rest)
    monkeypatch.setattr(ledger, "period_csv", storno_meanwhile)
    c = ledger.create_closing(store, "Lea", ledger.ClosingIn())
    assert len(calls) == 2                                               # einmal neu gerechnet
    assert c["storno_cents"] > 0 and c["orders"] == 2
    assert store.scalar("SELECT COUNT(*) FROM cancellations WHERE closing_id IS NULL") == 0
    assert store.scalar("SELECT closing_id FROM cancellations") == c["id"]
    assert ledger.verify_closing(store, c["id"])["ok"] is True
    assert store.scalar("SELECT COUNT(*) FROM closings") == 1


def test_reset_orders_keeps_order_that_arrives_meanwhile(client, shop, monkeypatch):  # noqa: F811
    from app import core, ledger
    _order(client, [{"product_id": shop["Bier 5dl"], "qty": 1}])
    client.post("/api/admin/closings", auth=AUTH, json={})
    _order(client, [{"product_id": shop["Mineral"], "qty": 1}])           # kommt nach der Prüfung …
    monkeypatch.setattr(ledger, "open_orders", lambda st: 0)          # … die noch «nichts offen» sah
    r = client.post("/api/admin/reset-orders?confirm=JA", auth=AUTH)
    assert r.status_code == 409
    st = core.rt.store
    assert st.scalar("SELECT COUNT(*) FROM orders") == 2                  # nichts gelöscht
    assert st.scalar("SELECT COUNT(*) FROM print_jobs") > 0
    assert "reset_orders" not in [e["action"] for e in client.get("/api/admin/audit", auth=AUTH).json()["entries"]]


def test_restore_file_already_has_new_gen_and_running_secrets(client, monkeypatch):
    """Die Datei, die eingespielt wird, enthält schon die erhöhten Anmelde-Stände und die Geheimnisse des laufenden
    Systems – kein Zeitfenster nach dem Einspielen, auch nicht bei einem Absturz."""
    from app import main as m
    m.store.execute("INSERT INTO users(name, role, pw_hash, gen) VALUES ('anna', 'lead', 'x', 3)")
    backup = m.store.backup()
    m.store.execute("INSERT INTO settings(key,value) VALUES ('fully_password', 'laufend-123')")
    loaded = []
    real = m.store.restore
    monkeypatch.setattr(m.store, "restore", lambda data: loaded.append(data) or real(data))
    r = client.post("/api/admin/restore?confirm=JA", auth=AUTH, files={"file": ("s.sqlite", backup)})
    assert r.status_code == 200, r.text
    con = sqlite3.connect(":memory:")
    con.deserialize(loaded[0])
    assert con.execute("SELECT gen FROM users WHERE name='anna'").fetchone()[0] == 3 + 4
    assert con.execute("SELECT value FROM settings WHERE key='fully_password'").fetchone()[0] == "laufend-123"
    con.close()
    assert m.store.scalar("SELECT gen FROM users WHERE name='anna'") == 7
    assert m.store.setting("fully_password") == "laufend-123"


def test_image_zip_with_broken_entries_imports_the_rest(client):
    from PIL import Image
    from tests.test_api import setup_shop
    setup_shop(client)
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "orange").save(buf, "PNG")
    good = buf.getvalue()
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("Bier 5dl.png", good)
        zf.writestr("Bratwurst.png", good + b"kaputt")                 # Inhalt passt nicht zur Prüfsumme (unten)
        zf.writestr("Mineral.png", b"kein Bild")
    raw = z.getvalue().replace(good + b"kaputt", good + b"KAPUTT")
    r = client.post("/api/admin/import/images", auth=AUTH, files={"file": ("i.zip", raw)})
    assert r.status_code == 200, r.text
    assert r.json() == {"matched": ["Bier 5dl.png"], "unmatched": [], "failed": ["Bratwurst.png", "Mineral.png"]}


def test_photo_is_read_reduced_and_turned_by_exif():
    """Handyfoto hochkant (EXIF): verkleinert gelesen, richtig gedreht und in beiden Richtungen innerhalb der Box."""
    from PIL import Image
    from app import catalog as m
    buf = io.BytesIO()
    im = Image.new("RGB", (4000, 2000), "red")
    exif = im.getexif()
    exif[0x0112] = 6                                                    # 90° gedreht
    im.save(buf, "JPEG", exif=exif)
    for box, size in ((m.LOGO_BOX, (120, 240)), ((m.IMAGE_PX, m.IMAGE_PX), (240, 480))):
        _, mime, b64 = m.process_image(buf.getvalue(), box)
        assert mime == "image/webp" and Image.open(io.BytesIO(base64.b64decode(b64))).size == size
