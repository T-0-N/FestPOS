"""Tischplan (app/plan.py): Bereiche, Lage der Tische, Grundriss, Bootstrap fürs Handy, Rechte."""
from __future__ import annotations

import io

from tests.test_api import AUTH


def _png(w: int, h: int) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(out, "PNG")
    return out.getvalue()


def _setup(c) -> dict:
    c.post("/api/admin/tables", auth=AUTH, json={"range_from": 1, "range_to": 4, "labels": ["Bar"]})
    plan = c.post("/api/admin/plan/areas", auth=AUTH, json={"name": " Saal "}).json()
    return {x["label"]: x["id"] for x in plan["tables"]} | {"_area": plan["areas"][0]["id"]}


def test_area_tables_and_bootstrap(client):
    ids = _setup(client)
    aid = ids["_area"]
    plan = client.get("/api/admin/plan", auth=AUTH).json()
    assert [a["name"] for a in plan["areas"]] == ["Saal"] and plan["areas"][0]["ratio"] == 1400
    assert plan["start"] == "plan" and not any(x["placed"] for x in plan["tables"])
    # ohne Tisch im Plan: am Handy kein Plan (Tischwahl wie früher)
    assert client.get("/api/bootstrap").json()["plan"] is None

    r = client.put(f"/api/admin/plan/areas/{aid}", auth=AUTH, json={
        "tables": [{"id": ids["1"], "x": 100, "y": 200, "w": 60, "h": 250, "rot": -330, "shape": "bench"},
                   {"id": ids["Bar"], "x": 700, "y": 300, "w": 80, "h": 80, "shape": "round"}],
        "labels": [{"t": "  Bühne ", "x": 50, "y": 50, "w": 900, "h": 190}], "opacity": 30})
    assert r.status_code == 200, r.text
    t1 = next(x for x in r.json()["tables"] if x["label"] == "1")
    assert t1["placed"] and t1["area_id"] == aid and t1["rot"] == 30 and t1["shape"] == "bench"
    boot = client.get("/api/bootstrap").json()["plan"]
    assert boot["start"] == "plan" and len(boot["areas"]) == 1
    a = boot["areas"][0]
    assert a["name"] == "Saal" and a["opacity"] == 30 and a["image"] is None
    assert a["labels"] == [{"t": "Bühne", "x": 50, "y": 50, "w": 900, "h": 190}]
    assert [x["l"] for x in a["tables"]] == ["1", "Bar"] and a["tables"][0] == \
        {"l": "1", "x": 100, "y": 200, "w": 60, "h": 250, "r": 30, "s": "bench"}

    # aus dem Plan nehmen; Startansicht «kein Plan» blendet ihn am Handy aus
    client.put(f"/api/admin/plan/areas/{aid}", auth=AUTH, json={"remove": [ids["Bar"]]})
    assert [x["l"] for x in client.get("/api/bootstrap").json()["plan"]["areas"][0]["tables"]] == ["1"]
    assert client.put("/api/admin/plan/settings", auth=AUTH, json={"start": "off"}).json()["start"] == "off"
    assert client.get("/api/bootstrap").json()["plan"] is None
    client.put("/api/admin/plan/settings", auth=AUTH, json={"start": "number"})
    assert client.get("/api/bootstrap").json()["plan"]["start"] == "number"

    # Bereich löschen: Tische bleiben, nur nicht mehr im Plan
    plan = client.delete(f"/api/admin/plan/areas/{aid}", auth=AUTH).json()
    assert plan["areas"] == [] and len(plan["tables"]) == 5 and not any(x["placed"] for x in plan["tables"])


def test_validation_and_duplicates(client):
    ids = _setup(client)
    aid = ids["_area"]
    r = client.post("/api/admin/plan/areas", auth=AUTH, json={"name": "saal"})
    assert r.status_code == 409 or r.status_code == 422, r.text
    assert "Bereich" in r.json()["detail"]
    bad = client.put(f"/api/admin/plan/areas/{aid}", auth=AUTH,
                     json={"tables": [{"id": ids["1"], "x": 1, "y": 1, "w": 5, "h": 50}]})   # zu klein
    assert bad.status_code == 422
    bad = client.put(f"/api/admin/plan/areas/{aid}", auth=AUTH,
                     json={"tables": [{"id": ids["1"], "x": 1, "y": 1, "w": 50, "h": 50, "shape": "stern"}]})
    assert bad.status_code == 422
    assert client.put("/api/admin/plan/areas/999", auth=AUTH, json={"opacity": 10}).status_code == 404
    assert client.put("/api/admin/plan/settings", auth=AUTH, json={"start": "egal"}).status_code == 422


def test_floor_plan_image_sets_ratio_and_survives_cleanup(client):
    ids = _setup(client)
    aid = ids["_area"]
    r = client.post(f"/api/admin/plan/areas/{aid}/image", auth=AUTH, files={"file": ("plan.png", _png(800, 400), "image/png")})
    assert r.status_code == 200, r.text
    area = r.json()["areas"][0]
    assert area["ratio"] == 500 and area["image"].startswith("/media/")
    assert client.get(area["image"]).status_code == 200
    # Produktbild löschen räumt unbenutzte Bilder auf – der Grundriss bleibt
    client.post("/api/admin/logo", auth=AUTH, files={"file": ("l.png", _png(50, 20), "image/png")})
    client.delete("/api/admin/logo", auth=AUTH)
    assert client.get(area["image"]).status_code == 200
    # Grundriss entfernen: Bild weg, Verhältnis bleibt
    area2 = client.delete(f"/api/admin/plan/areas/{aid}/image", auth=AUTH).json()["areas"][0]
    assert area2["image"] is None and area2["ratio"] == 500
    assert client.get(area["image"]).status_code == 404
    bad = client.post(f"/api/admin/plan/areas/{aid}/image", auth=AUTH, files={"file": ("x.png", b"kein Bild", "image/png")})
    assert bad.status_code == 422


def test_roles(client):
    from app import auth
    for method, path in (("GET", "/api/admin/plan"), ("POST", "/api/admin/plan/areas"), ("PUT", "/api/admin/plan/areas/1"),
                         ("DELETE", "/api/admin/plan/areas/1"), ("POST", "/api/admin/plan/areas/1/image"),
                         ("DELETE", "/api/admin/plan/areas/1/image"), ("PUT", "/api/admin/plan/settings")):
        assert auth.required_role(method, path) == "lead", (method, path)
    assert client.get("/api/admin/plan").status_code == 401
