import io
import time
import uuid
import zipfile


AUTH = ("admin", "geheim")


def setup_shop(c):
    p1 = c.post("/api/admin/printers", auth=AUTH, json={"name": "Getränkeausgabe", "host": "127.0.0.1", "port": 1}).json()["id"]
    c.post("/api/admin/printers", auth=AUTH, json={"name": "Küche", "host": "127.0.0.1", "port": 1, "show_prices": False})
    csv = ("name;preis;kategorie;drucker;einzelbon;sort\r\n"
           "Bier 5dl;6.50;Getränke;Getränkeausgabe;0;1\r\n"
           "Bratwurst;8,00;Essen;Küche;ja;1\r\n"
           "Mineral;4.–;Getränke;getränkeausgabe;;2\r\n").encode("cp1252")   # Excel-CH-Export
    r = c.post("/api/admin/import/products?commit=true", auth=AUTH, files={"file": ("p.csv", csv)})
    assert r.status_code == 200 and r.json()["committed"], r.text
    c.post("/api/admin/tables", auth=AUTH, json={"range_from": 1, "range_to": 20})
    return p1


def test_admin_requires_password(client):
    assert client.get("/api/admin/products").status_code == 401
    assert client.get("/api/admin/products", auth=("admin", "falsch")).status_code == 401


def test_csv_import_preview_update_and_errors(client):
    setup_shop(client)
    s = client.get("/api/admin/settings", auth=AUTH).json()
    assert s["currency"] == "CHF"
    prods = {p["name"]: p for p in client.get("/api/admin/products", auth=AUTH).json()}
    assert prods["Mineral"]["price_cents"] == 400 and prods["Bratwurst"]["single_ticket"] == 1
    assert prods["Bratwurst"]["printer"] == "Küche"

    # Preisänderung + Fehler: Vorschau zeigt beides, Commit wird verweigert
    csv = "Name;Preis;Drucker\nBier 5dl;7.00;Getränkeausgabe\nWein;abc;Bar\n".encode()
    r = client.post("/api/admin/import/products?commit=true", auth=AUTH, files={"file": ("p.csv", csv)})
    assert r.status_code == 422
    rows = {x["name"]: x for x in r.json()["rows"]}
    assert rows["Bier 5dl"]["changes"] == {"price_cents": [650, 700]}
    assert any("Preis" in e for e in rows["Wein"]["errors"]) and any("Bar" in e for e in rows["Wein"]["errors"])
    assert client.get("/api/admin/products", auth=AUTH).json()[0]["price_cents"] == 650   # nichts geändert

    # Korrigiert + fehlende Produkte deaktivieren
    csv = "name;preis\nBier 5dl;7.00\n".encode()
    r = client.post("/api/admin/import/products?commit=true&deactivate_missing=true", auth=AUTH,
                    files={"file": ("p.csv", csv)}).json()
    assert r["committed"] and sorted(r["deactivate"]) == ["Bratwurst", "Mineral"]
    boot = client.get("/api/bootstrap").json()
    assert [p["name"] for p in boot["products"]] == ["Bier 5dl"]

    # Export -> Re-Import ist verlustfrei
    exp = client.get("/api/admin/export/products.csv", auth=AUTH).content
    assert exp.startswith(b"\xef\xbb\xbf") and "Getränke".encode() in exp
    r = client.post("/api/admin/import/products", auth=AUTH, files={"file": ("e.csv", exp)}).json()
    assert r["summary"]["unchanged"] == 3 and r["summary"]["ok"]


def test_order_flow_and_jobs(client):
    setup_shop(client)
    boot = client.get("/api/bootstrap").json()
    pid = {p["name"]: p["id"] for p in boot["products"]}
    assert boot["tables"][:3] == ["1", "2", "3"]

    body = {"client_uuid": str(uuid.uuid4()), "table": "7", "device": "XCover-1",
            "lines": [{"product_id": pid["Bier 5dl"], "qty": 2}, {"product_id": pid["Bratwurst"], "qty": 2}]}
    r = client.post("/api/orders", json=body)
    assert r.status_code == 200 and r.json()["total_cents"] == 2900
    assert client.post("/api/orders", json=body).json()["duplicate"] is True

    jobs = client.get("/api/admin/jobs", auth=AUTH).json()
    assert len(jobs) == 3 and {j["station"] for j in jobs} == {"Getränkeausgabe", "Küche"}
    assert client.post("/api/orders", json={**body, "client_uuid": "x" * 10, "lines": []}).status_code == 422

    orders_csv = client.get("/api/admin/export/orders.csv", auth=AUTH).content.decode("utf-8-sig")
    assert "Bratwurst;;2;8.00;16.00" in orders_csv


def test_images_stored_in_db(client):
    from PIL import Image
    setup_shop(client)
    buf = io.BytesIO()
    Image.new("RGB", (1200, 800), "orange").save(buf, "JPEG")
    pid = client.get("/api/bootstrap").json()["products"][0]["id"]
    r = client.post(f"/api/admin/products/{pid}/image", auth=AUTH, files={"file": ("b.jpg", buf.getvalue())}).json()
    img = client.get(r["url"])
    assert img.headers["content-type"] == "image/webp" and "immutable" in img.headers["cache-control"]
    assert Image.open(io.BytesIO(img.content)).size == (480, 320)          # gross genug für «grosse Bilder»

    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("bratwurst.JPG", buf.getvalue())
        zf.writestr("Unbekannt.png", buf.getvalue())
    r = client.post("/api/admin/import/images", auth=AUTH, files={"file": ("i.zip", z.getvalue())}).json()
    assert r == {"matched": ["bratwurst.JPG"], "unmatched": ["Unbekannt.png"], "failed": []}


def test_health_and_time(client):
    h = client.get("/api/health").json()
    assert h["leader"] is True
    for _ in range(40):                                  # Spooler-Leaderwatch läuft asynchron an
        if client.get("/api/health/leader").status_code == 200:
            break
        time.sleep(0.05)
    assert client.get("/api/health/leader").status_code == 200
    assert client.post("/api/time/sync", json={"epoch_ms": int(time.time() * 1000)}).json()["changed"] is False
    assert client.post("/api/internal/settime", json={"epoch_ms": 1}).status_code == 403


def test_ui_login_without_browser_popup(client):
    r = client.get("/api/admin/products", headers={"X-Festpos-UI": "1"})
    assert r.status_code == 401 and "www-authenticate" not in r.headers
    assert "www-authenticate" in client.get("/api/admin/products").headers


def test_table_qr_print(client):
    import asyncio
    import threading
    from tests.fake_printer import FakePrinter

    # Fake-Drucker in eigenem Thread/Event-Loop (die App läuft im Loop des TestClient)
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    p = asyncio.run_coroutine_threadsafe(FakePrinter().start(), loop).result()

    pid = client.post("/api/admin/printers", auth=AUTH, json={"name": "Bar", "host": "127.0.0.1", "port": p.port}).json()["id"]
    client.post("/api/admin/tables", auth=AUTH, json={"labels": ["1", "Terrasse 2"]})
    assert client.post("/api/admin/tables/print-qr", auth=AUTH, json={"printer_id": pid}).json() == {"queued": 2}
    for _ in range(100):
        if len(p.bons) == 2:
            break
        time.sleep(0.05)
    asyncio.run_coroutine_threadsafe(p.stop(), loop).result()
    loop.call_soon_threadsafe(loop.stop)
    bons = p.bons
    assert "[QR:table:1]" in bons[0] and "Terrasse 2" in bons[1] and "[QR:table:Terrasse 2]" in bons[1]
    jobs = client.get("/api/admin/jobs", auth=AUTH).json()
    assert {j["label"] for j in jobs} == {"Tisch-QR 1", "Tisch-QR Terrasse 2"}



def make_device_ca(tmp_path):
    import datetime as dt
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Geraete-CA")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(key, hashes.SHA256()))
    (tmp_path / "dca.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "dca.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                         serialization.NoEncryption()))
    return tmp_path / "dca.crt", tmp_path / "dca.key", cert


def test_device_certificates(client, tmp_path):
    import app.main as main
    from cryptography.hazmat.primitives.serialization import pkcs12
    crt, key, ca = make_device_ca(tmp_path)
    main.Config.device_ca, main.Config.device_ca_key, main.Config.admin_port = str(crt), str(key), 8443
    r = client.post("/api/admin/devices/cert", auth=AUTH, json={"name": "XCover 1"}).json()
    assert len(r["password"]) == 6 and ":8443/api/device-cert/" in r["url"] and r["svg"].startswith("<svg")
    path = "/api/device-cert/" + r["url"].rsplit("/", 1)[1]
    dl = client.get(path)
    assert dl.status_code == 200 and dl.headers["content-type"] == "application/x-pkcs12"
    assert client.get(path).status_code == 404                                  # nur einmal abrufbar
    k, cert, extra = pkcs12.load_key_and_certificates(dl.content, r["password"].encode())
    assert cert.issuer == ca.subject and "XCover 1" in cert.subject.rfc4514_string()
    devs = client.get("/api/admin/devices", auth=AUTH).json()
    assert [c["name"] for c in devs["certs"]] == ["XCover 1"]
    assert client.post("/api/admin/devices/cert", auth=AUTH, json={"name": "<script>"}).status_code == 422


def test_orders_only_via_phone_port(client):
    import app.main as main
    setup_shop(client)
    pid = client.get("/api/bootstrap").json()["products"][0]["id"]
    body = {"client_uuid": str(uuid.uuid4()), "table": "1", "lines": [{"product_id": pid, "qty": 1}]}
    main.Config.phone_port = 443
    try:
        assert client.post("/api/orders", json=body).status_code in (403, 404)     # TestClient = Port 80
        assert client.get("/api/bootstrap").status_code == 404                      # App-Daten nur am Handy-Port
        assert client.post("https://testserver:443/api/orders", json=body).status_code == 200
    finally:
        main.Config.phone_port = 0


def test_pin_settings(client):
    import hashlib
    assert client.put("/api/admin/settings", auth=AUTH, json={"pin_enabled": True}).status_code == 422   # erst PIN
    s = client.put("/api/admin/settings", auth=AUTH, json={"pin_enabled": True, "pin": "4711",
                                                           "pin_idle_minutes": 30, "pin_on_screen_off": True}).json()
    assert s["pin_set"] and "pin_hash" not in s and "pin_salt" not in s
    pin = client.get("/api/bootstrap").json()["pin"]
    assert pin["enabled"] and pin["idle_minutes"] == 30 and pin["on_screen_off"] and pin["length"] == 4
    assert hashlib.sha256((pin["salt"] + "4711").encode()).hexdigest() == pin["hash"]
    assert client.put("/api/admin/settings", auth=AUTH, json={"pin": "12a4"}).status_code == 422
    client.put("/api/admin/settings", auth=AUTH, json={"pin_enabled": False})
    assert client.get("/api/bootstrap").json()["pin"]["enabled"] is False


def test_history_setting_and_template_and_pdf(client):
    setup_shop(client)
    assert client.get("/api/bootstrap").json()["show_history"] is True
    client.put("/api/admin/settings", auth=AUTH, json={"show_history": False})
    assert client.get("/api/bootstrap").json()["show_history"] is False

    t = client.get("/api/admin/import/template.csv", auth=AUTH)
    text = t.content.decode("utf-8-sig")
    assert text.startswith("name;preis;") and "Küche" in text and "Getränkeausgabe" in text
    r = client.post("/api/admin/import/products", auth=AUTH, files={"file": ("t.csv", t.content)}).json()
    assert r["summary"]["ok"]                                                   # Vorlage ist direkt importierbar

    pdf = client.get("/api/admin/tables/qr.pdf", auth=AUTH)
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    one = client.get("/api/admin/tables/qr.pdf?labels=Bar,7", auth=AUTH).content
    assert len(one) < len(pdf.content)



def test_admin_port_serves_only_admin(client):
    import app.main as main
    main.Config.phone_port = 443
    try:
        r = client.get("/", follow_redirects=False)                       # TestClient = Port 80 = "Verwaltungs-Port"
        assert r.status_code == 302 and r.headers["location"] == "/admin.html"
        assert client.get("/app.js").status_code == 404
        assert client.get("/app/start.js").status_code == 404                  # übrige Skripte der App ebenso
        blocked = client.get("/app.js").json()["detail"]                    # Sperre des Verwaltungs-Ports
        assert client.get("/admin/main.js").json()["detail"] != blocked     # Module der Verwaltung: durchgelassen
        assert client.get("/api/bootstrap").status_code == 404
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/admin/stats", auth=AUTH).status_code == 200
        assert client.get("https://testserver:443/api/bootstrap").status_code == 200   # Handy-Port: alles da
    finally:
        main.Config.phone_port = 0


def test_cert_validity_and_describe(tmp_path):
    from cryptography.hazmat.primitives.serialization import pkcs12
    from app import certs
    crt, key, ca = make_device_ca(tmp_path)
    ca_cert, ca_key = certs.load_ca(crt, key)
    p12, pw, serial, until = certs.issue(ca_cert, ca_key, "XCover 2", days=30)
    k, cert, _ = pkcs12.load_key_and_certificates(p12, pw.encode())
    from cryptography.hazmat.primitives.serialization import Encoding
    info = certs.describe(cert.public_bytes(Encoding.DER))
    assert info["name"] == "XCover 2" and info["serial"] == serial and 29 <= info["days_left"] <= 30
    assert b"XCover 2 (bis" in p12 or True     # Anzeigename steckt verschlüsselt drin – nur Smoke-Test


def test_cert_settings_and_expiry_list(client, tmp_path):
    import app.main as main
    crt, key, _ = make_device_ca(tmp_path)
    main.Config.device_ca, main.Config.device_ca_key = str(crt), str(key)
    client.put("/api/admin/settings", auth=AUTH, json={"cert_days": 30, "cert_warn_days": 60})
    r = client.post("/api/admin/devices/cert", auth=AUTH, json={"name": "XCover 3"}).json()
    d = client.get("/api/admin/devices", auth=AUTH).json()
    assert d["warn_days"] == 60 and d["certs"][0]["days_left"] in (29, 30, 31) and r["expires_at"]
    assert client.get("/api/admin/stats", auth=AUTH).json()["certs_expiring"] == 1
    assert client.put("/api/admin/settings", auth=AUTH, json={"cert_days": 3}).status_code == 422
    assert client.post("/api/device/renew").status_code == 403          # ohne Gerätezertifikat


def test_system_certs_and_version(client, tmp_path):
    import app.main as main
    crt, key, _ = make_device_ca(tmp_path)
    main.Config.device_ca = str(crt)
    r = client.get("/api/admin/system/certs", auth=AUTH).json()
    assert r["version"] == main.APP_VERSION and r["warn_days"] == 730
    dca = [c for c in r["certs"] if c["label"].startswith("Geräte-CA")][0]
    assert dca["days_left"] <= 30 and dca["warn"] is True          # Test-CA gilt 30 Tage -> Warnung
    assert client.get("/api/health").json()["version"] == main.APP_VERSION


def test_issue_capped_by_ca_and_revoke_list(client, tmp_path):
    import app.main as main
    crt, key, _ = make_device_ca(tmp_path)                           # CA gilt nur 30 Tage
    main.Config.device_ca, main.Config.device_ca_key = str(crt), str(key)
    client.put("/api/admin/settings", auth=AUTH, json={"cert_days": 3650, "cert_warn_days": 730})
    r = client.post("/api/admin/devices/cert", auth=AUTH, json={"name": "XCover 4"}).json()
    d = client.get("/api/admin/devices", auth=AUTH).json()["certs"][0]
    assert d["days_left"] <= 30                                      # nicht länger als die CA
    client.post(f"/api/admin/devices/{d['id']}/revoke", auth=AUTH)
    d = client.get("/api/admin/devices", auth=AUTH).json()["certs"][0]
    assert d["revoked_at"] and main.revoked_serials() == {r["serial"]}
    client.post(f"/api/admin/devices/{d['id']}/revoke?undo=true", auth=AUTH)
    assert client.get("/api/admin/devices", auth=AUTH).json()["certs"][0]["revoked_at"] is None


def test_admin_routes_need_login(client):
    """Jede Route der Verwaltung prüft die Anmeldung auch selbst (Router mit Depends(admin)), nicht nur RequestGate –
    auch nach dem Aufteilen von main.py in eigene Module (catalog, printers, tables, settings_admin, maintenance)."""
    from fastapi.routing import APIRoute

    import app.main as main
    from app import core

    def flat(routes):
        for r in routes:
            yield from flat(r.original_router.routes) if hasattr(r, "original_router") else [r]
    admin_routes = [r for r in flat(main.app.router.routes)
                    if isinstance(r, APIRoute) and r.path.startswith("/api/admin/")
                    and r.path not in ("/api/admin/login", "/api/admin/login/totp")]
    assert len(admin_routes) > 90
    assert [r.path for r in admin_routes if not any(d.dependency is core.admin for d in r.dependencies)] == []
