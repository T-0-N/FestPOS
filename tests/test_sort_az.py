"""T26: Kategorien und Produkte einmal alphabetisch sortieren – Gross/klein egal, «Ä» wie «A», Zahlen der
Grösse nach. Danach lässt sich wieder frei ziehen; ab Festleitung; steht im Protokoll."""
from app.catalog import az_key
from tests.test_api import AUTH


def test_az_key_umlauts_case_and_numbers():
    names = ["Wein 10dl", "bier 5dl", "Äpfel", "Wein 2dl", "Bier 3dl", "Zitrone", "apfelschorle", "Öl"]
    assert sorted(names, key=az_key) == ["Äpfel", "apfelschorle", "Bier 3dl", "bier 5dl", "Öl", "Wein 2dl",
                                         "Wein 10dl", "Zitrone"]


def test_sort_az_categories_and_products(client):
    cats = {}
    for name in ("Zvieri", "Getränke", "Ässe"):
        cats[name] = client.post("/api/admin/categories", auth=AUTH, json={"name": name}).json()["id"]
    for name, cat in [("Wein 10dl", "Getränke"), ("Wein 2dl", "Getränke"), ("Bier", "Getränke"), ("Wurst", "Ässe"),
                      ("Brot", "Zvieri"), ("Älplermagronen", "Ässe")]:
        assert client.post("/api/admin/products", auth=AUTH, json={"name": name, "price_cents": 500,
                                                                   "category_id": cats[cat]}).status_code == 200
    r = client.post("/api/admin/catalog/sort-az", auth=AUTH)
    assert r.status_code == 200 and r.json() == {"categories": 3, "products": 6}
    order = [c["name"] for c in client.get("/api/admin/categories", auth=AUTH).json()]
    assert order == ["Ässe", "Getränke", "Zvieri"]
    prods = sorted(client.get("/api/admin/products", auth=AUTH).json(), key=lambda p: p["sort"])
    assert [p["name"] for p in prods] == ["Älplermagronen", "Wurst", "Bier", "Wein 2dl", "Wein 10dl", "Brot"]
    ids = [p["id"] for p in prods]                                        # danach wieder frei ziehen
    assert client.put("/api/admin/products/order", auth=AUTH, json={"ids": ids[::-1]}).status_code == 200
    prods = sorted(client.get("/api/admin/products", auth=AUTH).json(), key=lambda p: p["sort"])
    assert prods[0]["name"] == "Brot"
    log = [e["action"] for e in client.get("/api/admin/audit", auth=AUTH).json()["entries"]]
    assert "catalog_sort_az" in log


def test_sort_az_roles(client):
    for name, role in (("Lea", "lead"), ("Vera", "view")):
        assert client.post("/api/admin/users", auth=AUTH, json={"name": name, "role": role,
                                                                "password": f"{name}-passwort"}).status_code == 200
    assert client.post("/api/admin/catalog/sort-az", auth=("Lea", "Lea-passwort")).status_code == 200
    assert client.post("/api/admin/catalog/sort-az", auth=("Vera", "Vera-passwort")).status_code == 403
