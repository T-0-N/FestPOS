"""Version 2026.10.10-18: Logo aus der Verwaltung (Bestell-App, Verwaltung, Statusanzeige)."""
from __future__ import annotations

import io

import pytest

from tests.test_api import AUTH

PIL = pytest.importorskip("PIL.Image")


def _png(w=1200, h=300, color=(20, 40, 160, 255)) -> bytes:
    im = PIL.new("RGBA", (w, h), (0, 0, 0, 0))                       # durchsichtig …
    im.paste(color, (w // 4, h // 4, w * 3 // 4, h * 3 // 4))          # … mit einem Balken
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def test_logo_upload_everywhere_and_remove(client):
    assert client.get("/api/logo").json() == {"logo": None, "logo_plate": True}
    r = client.post("/api/admin/logo", auth=AUTH, files={"file": ("logo.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    url = r.json()["logo"]
    assert url.startswith("/media/")
    # verkleinert, Transparenz bleibt
    img = client.get(url)
    assert img.status_code == 200 and img.headers["content-type"] == "image/webp"
    im = PIL.open(io.BytesIO(img.content))
    assert im.size[0] <= 600 and im.size[1] <= 240 and im.size == (600, 150) and "A" in im.getbands()
    # überall bekannt: öffentlich (Anmeldung), Bestell-App, Statusanzeige
    assert client.get("/api/logo").json() == {"logo": url, "logo_plate": True}
    assert client.get("/api/bootstrap").json()["logo"] == url
    assert client.get("/api/status", auth=AUTH).json()["logo"] == url
    assert client.get("/api/admin/settings", auth=AUTH).json()["logo_image"] == url.rsplit("/", 1)[1]
    # weisser Grund abschaltbar
    client.put("/api/admin/settings", auth=AUTH, json={"logo_plate": "none"})
    assert client.get("/api/logo").json()["logo_plate"] is False
    assert client.put("/api/admin/settings", auth=AUTH, json={"logo_plate": "rot"}).status_code == 422
    # Produktbilder aufräumen löscht das Logo nicht
    pid = client.post("/api/admin/products", auth=AUTH, json={"name": "Kaffee", "price_cents": 350, "category_id": None}).json()["id"]
    client.post(f"/api/admin/products/{pid}/image", auth=AUTH, files={"file": ("p.png", _png(100, 100), "image/png")})
    client.delete(f"/api/admin/products/{pid}/image", auth=AUTH)
    assert client.get(url).status_code == 200
    # neues Logo ersetzt das alte (das alte Bild verschwindet)
    url2 = client.post("/api/admin/logo", auth=AUTH, files={"file": ("l.png", _png(300, 300), "image/png")}).json()["logo"]
    assert url2 != url and client.get(url).status_code == 404
    # entfernen
    assert client.delete("/api/admin/logo", auth=AUTH).status_code == 200
    assert client.get("/api/logo").json()["logo"] is None and client.get(url2).status_code == 404
    assert client.get("/api/bootstrap").json()["logo"] is None


def test_logo_only_for_admin_and_only_images(client):
    from app import core
    core.login_throttle.clear()
    client.post("/api/admin/users", auth=AUTH, json={"name": "Lea", "role": "lead", "password": "sicher-123"})
    tok = client.post("/api/admin/login", json={"name": "Lea", "password": "sicher-123"}).json()["token"]
    lea = {"Authorization": f"Bearer {tok}"}
    assert client.post("/api/admin/logo", headers=lea, files={"file": ("l.png", _png(), "image/png")}).status_code == 403
    assert client.delete("/api/admin/logo", headers=lea).status_code == 403
    assert client.put("/api/admin/settings", headers=lea, json={"logo_plate": "none"}).status_code == 403
    bad = client.post("/api/admin/logo", auth=AUTH, files={"file": ("x.svg", b"<svg onload='alert(1)'/>", "image/svg+xml")})
    assert bad.status_code == 422 and client.get("/api/logo").json()["logo"] is None
