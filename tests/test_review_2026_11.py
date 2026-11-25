"""Abschluss-Review 2026-11: Anmeldung von fremden Seiten (S5), Bremse pro Name (S6),
Pfadprüfung (S8), Handy-API (S9), Drucker-Adresse und Bildgrösse (S11), Wortlaut von Datenbankfehlern (S12),
Sitzungsschlüssel ohne Geheimnis (S16)."""
from __future__ import annotations

import asyncio
import io
import uuid

import pytest
from fastapi.testclient import TestClient

from tests.test_api import AUTH, setup_shop

PW = "sicher-123"


def _user(c, name="Lea", role="lead"):
    from app import core
    core.login_throttle.clear()
    assert c.post("/api/admin/users", auth=AUTH, json={"name": name, "role": role, "password": PW}).status_code == 200


# -- S5: Basic Auth nie von einer fremden Seite -----------------------------------------------------------------
def test_basic_auth_refuses_writes_from_other_sites(client):
    """Hat ein Browser Name und Passwort für Basic Auth gespeichert, schickt er sie auch mit, wenn eine fremde Seite ein
    Formular an den Pi abschickt. Schreibende Anfragen mit Basic Auth gehen darum nur ohne «fremde» Herkunft."""
    body = {"name": "Eva", "role": "view", "password": PW}
    for hdr in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}, {"Origin": "https://boese.example"},
                {"Origin": "null"}):
        r = client.post("/api/admin/users", auth=AUTH, json=body, headers=hdr)
        assert r.status_code == 403 and "Webseite" in r.json()["detail"], hdr
    assert client.post("/api/admin/reset-orders?confirm=JA", auth=AUTH, headers={"Origin": "https://boese.example"}).status_code == 403
    assert client.post("/api/admin/restore?confirm=JA", auth=AUTH, headers={"Sec-Fetch-Site": "cross-site"},
                       files={"file": ("x.festpos", b"x")}).status_code == 403
    assert not client.get("/api/admin/users", auth=AUTH).json()["users"]
    # lesen geht weiter (Statusanzeige für alte Geräte), ebenso curl/Skripte und die eigene Seite
    assert client.get("/api/admin/users", auth=AUTH, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    assert client.post("/api/admin/users", auth=AUTH, json=body, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert client.post("/api/admin/users", auth=AUTH, json={**body, "name": "Ida"},
                       headers={"Origin": "http://testserver"}).status_code == 200
    assert client.post("/api/admin/users", auth=AUTH, json={**body, "name": "Urs"}).status_code == 200
    # die Verwaltung arbeitet mit dem Sitzungs-Token: das schickt keine fremde Seite mit
    tok = client.post("/api/admin/login", json={"name": "admin", "password": AUTH[1]}).json()["token"]
    tok = client.post("/api/admin/me/confirm", json={"password": AUTH[1]},            # kritisch: bestätigt (#9)
                      headers={"Authorization": f"Bearer {tok}"}).json()["token"]
    assert client.post("/api/admin/users", json={**body, "name": "Kai"},
                       headers={"Authorization": f"Bearer {tok}", "Sec-Fetch-Site": "cross-site"}).status_code == 200


# -- S6: Bremse auch pro Name ------------------------------------------------------------------------------------
def test_login_throttle_per_name_over_all_addresses(client):
    """Wer nach ein paar Versuchen die Adresse wechselt, umging die Sperre pro Adresse. Jetzt zählen die Fehlversuche
    auch pro Name; durch kommt dann nur noch, wer sich von seiner Adresse aus schon richtig angemeldet hat."""
    from app import core
    _user(client)
    good = {"name": "Lea", "password": PW}
    assert client.post("/api/admin/login", json=good).status_code == 200          # PC der Festleitung: bekannt
    others = [TestClient(client.app, client=(f"192.168.55.{150 + i}", 4000)) for i in range(8)]
    n = 0
    for c in others[:6]:                                                           # je 5: unter der Sperre pro Adresse
        for _ in range(5):
            assert c.post("/api/admin/login", json={"name": "lea", "password": "falsch"}).status_code == 401
            n += 1
    assert n == core.LOGIN_NAME_MAX_FAILS
    r = others[6].post("/api/admin/login", json=good)                              # neue Adresse, richtiges Passwort
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert others[6].get("/api/admin/me", auth=("Lea", PW)).status_code == 429     # auch mit Basic Auth
    assert client.post("/api/admin/login", json=good).status_code == 200           # die bekannte Adresse kommt durch
    assert client.get("/api/admin/me", auth=("Lea", PW)).status_code == 200
    assert others[7].post("/api/admin/login", json={"name": "admin", "password": AUTH[1]}).status_code == 200   # anderer Name
    core.login_throttle.clear()
    assert others[6].post("/api/admin/login", json=good).status_code == 200


# -- S8: ein Pfad für Schranke und Routing ------------------------------------------------------------------------
def _raw(app, path: str, port: int = 8443) -> int:
    """Anfrage mit genau diesem dekodierten Pfad (der Testclient räumt «..» schon selbst auf)."""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "https",
             "path": path, "raw_path": path.encode("latin-1", "replace"), "query_string": b"", "root_path": "",
             "headers": [(b"host", b"192.168.55.10:8443")], "client": ("192.168.55.77", 5555),
             "server": ("192.168.55.10", port), "state": {}}
    out = {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        if m["type"] == "http.response.start":
            out["status"] = m["status"]

    asyncio.run(app(scope, receive, send))
    return out["status"]


def test_path_tricks_do_not_pass_the_admin_port_guard(client, monkeypatch):
    """«/admin?/../app/order.js» (kodiert %3F) sah für die Schranke wie «/admin» aus und lieferte am Verwaltungs-Port
    doch die Bestell-App. Schranke und Routing sehen jetzt denselben Pfad; «?», «#», «%» und Steuerzeichen darin gibt
    es nicht."""
    from app import core
    monkeypatch.setattr(core.Config, "phone_port", 443)
    app = client.app
    for path in ("/admin?/../app/order.js", "/admin#/../app/order.js", "/status?/../gast/gast.js", "/admin?x/../index.html",
                 "/api/health\n", "/admin.html\t", "/admin/%2e%2e/app/order.js", "/admin/../app/order.js", "/status//x"):
        assert _raw(app, path) == 404, path
    assert _raw(app, "/api/admin//users") == 401                # Verwaltung: zuerst die Anmeldung, dann der Pfad
    assert _raw(app, "/app/order.js") == 404                    # Bestell-App gibt es am Verwaltungs-Port nicht
    assert _raw(app, "/api/health") == 200
    assert _raw(app, "/api/admin/users") == 401
    assert _raw(app, "/api/health", port=443) == 200


def test_role_is_checked_for_the_routed_path(client):
    from starlette.requests import Request
    from app import core
    _user(client, "Vera", "view")
    tok = client.post("/api/admin/login", json={"name": "Vera", "password": PW}).json()["token"]

    def role_ok(path: str) -> bool:
        req = Request({"type": "http", "method": "GET", "path": path, "query_string": b"", "state": {},
                       "headers": [(b"authorization", f"Bearer {tok}".encode())], "client": ("192.168.55.9", 1)})
        try:
            core.admin(req)
            return True
        except Exception:  # noqa: BLE001
            return False
    assert role_ok("/api/admin/orders") and not role_ok("/api/admin/users")
    assert not role_ok("/api/admin/users?/../orders") and not role_ok("/api/admin/orders?x/y")


# -- S9: Handy-API --------------------------------------------------------------------------------------------------
def test_phone_cannot_claim_payrexx_and_tab_items_are_checked(client):
    setup_shop(client)
    prods = client.get("/api/bootstrap").json()["products"]
    cu = str(uuid.uuid4())
    assert client.post("/api/orders", json={"client_uuid": cu, "table": "5", "lines": [{"product_id": prods[0]["id"], "qty": 1}]}).status_code == 200
    assert client.post("/api/orders/payment", json={"client_uuid": cu, "payment": "payrexx"}).status_code == 422
    assert client.post("/api/orders/payment", json={"client_uuid": cu, "payment": "twint"}).status_code == 200
    base = {"key": "k-0000001", "amount_cents": 100, "payment": "cash"}
    for items in ({"x": 1}, {"1": 0}, {"1": -3}, {"1": 1000}, {"1234567": 1}, {str(i): 1 for i in range(501)}):
        assert client.post("/api/tabs/1/pay", json={**base, "items": items}).status_code == 422, list(items)[:2]
    assert client.post("/api/tabs/1/pay", json={**base, "items": {"1": 2}}).status_code == 404     # gültig, Tisch gibt es nicht


# -- S11: Drucker-Adresse, Bildgrösse ----------------------------------------------------------------------------------
def test_printer_address_is_never_a_service_of_the_pi(client):
    def add(host, port, name):
        return client.post("/api/admin/printers", auth=AUTH, json={"name": name, "host": host, "port": port}).status_code
    assert add("127.0.0.1", 4001, "Datenbank") == 422 and add("localhost", 8443, "FestPOS") == 422
    assert add("127.0.0.5", 22, "SSH") == 422 and add("kein host!", 9100, "Unsinn") == 422
    assert add("192.168.55.21 -x", 9100, "Leer") == 422
    assert add("192.168.55.21", 9100, "Bar") == 200 and add("127.0.0.1", 9100, "Test") == 200      # simulierter Drucker
    pid = client.get("/api/admin/printers", auth=AUTH).json()[0]["id"]
    assert client.put(f"/api/admin/printers/{pid}", auth=AUTH, json={"name": "Bar", "host": "127.0.0.1", "port": 4001}).status_code == 422


def test_huge_image_is_refused_before_it_is_unpacked(client, monkeypatch):
    from PIL import Image
    from app import catalog
    setup_shop(client)
    pid = client.get("/api/admin/products", auth=AUTH).json()[0]["id"]
    monkeypatch.setattr(catalog, "MAX_PIXELS", 10_000)
    buf = io.BytesIO()
    Image.new("RGB", (200, 100), "red").save(buf, "PNG")
    r = client.post(f"/api/admin/products/{pid}/image", auth=AUTH, files={"file": ("b.png", buf.getvalue())})
    assert r.status_code == 413 and "Megapixel" in r.json()["detail"]
    monkeypatch.setattr(catalog, "MAX_PIXELS", 60_000_000)
    assert client.post(f"/api/admin/products/{pid}/image", auth=AUTH, files={"file": ("b.png", buf.getvalue())}).status_code == 200


# -- S12: Wortlaut von Datenbankfehlern -----------------------------------------------------------------------------
def test_database_error_text_only_for_the_signed_in_admin(client):
    from app import main
    from app.store import StoreError, StoreUnavailable
    exc = StoreError("NOT NULL constraint failed: orders.geheime_spalte")
    assert "geheime_spalte" in main.store_error_text(exc) and "geheime_spalte" not in main.store_error_text(exc, False)

    @main.app.get("/api/test-s12/kaputt")
    @main.app.get("/api/admin/test-s12/kaputt")
    async def kaputt(weg: bool = False):
        raise (StoreUnavailable("rqlite nicht erreichbar: http://127.0.0.1:4001") if weg else exc)
    try:
        assert "geheime_spalte" not in client.get("/api/test-s12/kaputt").text
        assert "4001" not in client.get("/api/test-s12/kaputt?weg=1").text
        assert "geheime_spalte" in client.get("/api/admin/test-s12/kaputt", auth=AUTH).text
        assert "4001" in client.get("/api/admin/test-s12/kaputt?weg=1", auth=AUTH).text
    finally:
        main.app.router.routes[:] = [r for r in main.app.router.routes if "test-s12" not in getattr(r, "path", "")]


# -- S16: Sitzungsschlüssel ---------------------------------------------------------------------------------------------
def test_session_key_is_not_predictable_without_a_secret(client, monkeypatch):
    import hashlib
    from app import core
    predictable = hashlib.sha256(b"festpos-session\0" + b"\0" + b"geheim").digest()
    keys = set()
    for secret in ("", "bitte-aendern", "bitte-aendern-lang-und-zufaellig"):
        monkeypatch.setattr(core.Config, "cluster_secret", secret)
        assert core._session_key() != predictable
        assert core._session_key() != hashlib.sha256(b"festpos-session\0" + secret.encode() + b"\0" + b"geheim").digest()
        keys.add(core._session_key())
    assert len(keys) == 1                                        # immer der zufällige dieses Starts
    tok = core.issue_session(0, 0)["token"]
    assert core.session_state(tok) == "ok"                       # in diesem Prozess gültig
    monkeypatch.setattr(core.Config, "cluster_secret", "ein-echtes-geheimnis-0123456789abcdef")
    assert core._session_key() not in keys and core._session_key() == core._session_key()
    assert core.session_state(tok) == "invalid"


@pytest.mark.parametrize("path", ["/api/gast/menu\n", "/api/gast/order/abcdefgh12\n", "/media/abcdef123456\n"])
def test_gast_proxy_refuses_paths_with_a_trailing_newline(path):
    from app import gast_proxy as gp
    assert not gp.FORWARD_GET.fullmatch(path) and gp.FORWARD_GET.fullmatch(path.rstrip("\n"))


# -- D2, D4: Lizenzübersicht und feste Stände im Bau ------------------------------------------------------------------
def test_third_party_list_names_every_bundled_package():
    """Jedes Python-Paket aus requirements.txt (holt tools/pakete.py ins Update-Paket) mit Version in der Liste."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    text = (root / "THIRD-PARTY-LICENSES.md").read_text(encoding="utf-8")
    low = text.lower().replace("_", "-")
    missing = []
    import sys
    sys.path.insert(0, str(root / "tools"))
    import pakete
    for name, (version, _) in pakete.requirements().items():      # die Python-Pakete, die ins Update-Paket kommen
        if f"| {name} | {version} |" not in low.replace("_", "-"):
            missing.append(f"{name} {version}")
    assert not missing, f"THIRD-PARTY-LICENSES.md nachführen: {missing}"
    for other in ("cloudflared", "jsQR", "Barlow Condensed", "rqlite"):
        assert other in text


def test_workflows_use_pinned_actions():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for f in (root / ".github" / "workflows").glob("*.yml"):
        for m in re.finditer(r"uses:\s*(\S+)", f.read_text(encoding="utf-8")):
            if m.group(1).startswith("./.github/workflows/"):              # eigener Workflow (release.yml)
                assert (root / m.group(1)).is_file(), m.group(1)
                continue
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", m.group(1)), f"{f.name}: {m.group(1)} ist nicht auf einen Commit festgelegt"
    assert "PI_GEN_PINNED=" in (root / "deploy" / "pi-gen" / "build.sh").read_text(encoding="utf-8")


# -- Teil 2 (restliche Dateien): N1–N3 ----------------------------------------------------------------------------
def test_n1_rollout_settings_never_hand_out_the_real_fully_password(client, monkeypatch, tmp_path):
    """Die Einstellungsdatei beim Ausrollen gibt es nur mit dem Einmal-Passwort: nach dem Umstellen auf das echte
    Passwort (und für «Fully auf vorhandenem Handy») nichts mehr – auch MAC und Fully-Zertifikat bleiben."""
    import json

    from app import core, dhcp, fully
    auth = AUTH
    client.put("/api/admin/settings", auth=auth, json={"fully_password": "echtes-fully-passwort", "monitor_devices": []})
    client.put("/api/admin/fully/provision", auth=auth, json={"apk_url": "https://example.org/x.apk",
                                                              "checksum": "A" * 43, "apk_local": "0"})
    token = client.post("/api/admin/fully/rollout", auth=auth, json={"name": "XCover 7", "ip": "192.168.55.107"}).json()["id"]
    monkeypatch.setattr(fully, "LOCAL_HOSTS", {"testclient"})
    monkeypatch.setattr(dhcp, "HOSTS_DIR", tmp_path)                 # nie das echte Verzeichnis (auf einem Pi gäbe es das)
    monkeypatch.setattr(dhcp, "mac_for_ip", lambda ip: {"192.168.55.161": "6a:00:00:00:00:01",
                                                        "192.168.55.200": "6a:00:00:00:00:99"}.get(ip))
    url = f"/api/device-cert/fully/settings/{token}"
    first = client.get(url, headers={"X-Festpos-Client": "192.168.55.161"})
    assert first.status_code == 200 and first.json()["remoteAdminPassword"] != "echtes-fully-passwort"
    dev = json.loads(core.rt.store.setting("monitor_devices"))
    dev[0]["fully_pin"] = "a" * 64                                   # abgesichert: Zertifikat der Fernverwaltung
    core.rt.store.execute("UPDATE settings SET value=? WHERE key='monitor_devices'", (json.dumps(dev),))
    fully.rollout_pw_done("192.168.55.107")                          # FestPOS hat über HTTPS umgestellt
    again = client.get(url, headers={"X-Festpos-Client": "192.168.55.200"})      # jemand mit dem mitgelesenen Code
    assert again.status_code == 404 and "echtes-fully-passwort" not in again.text
    dev = json.loads(core.rt.store.setting("monitor_devices"))
    assert dev[0]["mac"] == "6a:00:00:00:00:01" and dev[0]["fully_pin"] == "a" * 64
    # «Fully auf vorhandenem Handy»: der Code dafür gehört zu keiner Einstellungsdatei
    manual = client.post("/api/admin/fully/prepare", auth=auth, json={"name": "XCover 8"}).json()["id"]
    assert client.get(f"/api/device-cert/fully/settings/{manual}", headers={"X-Festpos-Client": "192.168.55.200"}).status_code == 404
    assert len(json.loads(core.rt.store.setting("monitor_devices"))) == 1


def test_n2_csv_import_keeps_the_limits_of_the_admin_pages(client):
    """Der Import lässt nicht mehr zu als die Verwaltung: Name, Preis, Kategorie, Sortierung."""
    from app import core, csv_import
    rows = ["name;preis;kategorie;sort",
            "Bier 5dl;6.50;Getränke;1",
            f"{'X' * 41};5.00;Getränke;2",                       # Name zu lang
            "Wein;65000;Getränke;3",                             # «6500.00» statt «6.50»: über CHF 10'000
            f"Wurst;8.00;{'K' * 33};4",                          # Kategorie zu lang
            "Brot;2.00;Essen;99999999999999999999",              # Sortierung sprengt jede Zahl
            "\"Kaf\tfee\";3.50;Getränke;5"]                       # Tabulator im Namen: wird ein Leerzeichen
    plan = csv_import.preview(core.rt.store, "\n".join(rows).encode())
    by = {r.line: r for r in plan.rows}
    assert not plan.ok and by[2].action == "new" and not by[2].errors
    assert "40" in by[3].errors[0] and "65000" in by[4].errors[0] and "32" in by[5].errors[0] and by[6].errors
    assert by[7].name == "Kaf fee" and not by[7].errors
    assert csv_import.parse_price("10000") == 1_000_000          # die Grenze selbst geht
    r = client.post("/api/admin/import/products?commit=true", auth=AUTH,
                    files={"file": ("p.csv", "\n".join(rows).encode(), "text/csv")})
    assert r.status_code == 422 and not r.json()["committed"]
    assert core.rt.store.scalar("SELECT COUNT(*) FROM products WHERE name IN ('Bier 5dl', 'Wein')") == 0


def test_n3_bon_design_is_logged_with_the_real_user(client):
    import json

    from app import core
    auth = AUTH
    assert client.post("/api/admin/users", auth=auth, json={"name": "Vreni", "password": PW, "role": "admin"}).status_code == 200
    d = client.get("/api/admin/bon-design", auth=("Vreni", PW)).json()
    r = client.put("/api/admin/bon-design", auth=("Vreni", PW),
                   json={"receipt": d["receipt"], "ticket": d["ticket"]})
    assert r.status_code == 200
    row = core.rt.store.query_one("SELECT actor, details FROM audit_log WHERE action='bon_design' ORDER BY id DESC LIMIT 1")
    assert row["actor"] == "Vreni" and "items" in json.loads(row["details"])["ticket"]


# -- Hinweise von CodeRabbit zu PR 74 (Version 2026.10.10-91) --------------------------------------------------------
def test_phone_cannot_overwrite_a_confirmed_card_payment(client):
    """Nach «Bezahlt» über Payrexx bleibt die Zahlart – ein versehentliches «Bar» am Handheld ändert sie nicht mehr;
    die Verwaltung darf weiterhin korrigieren."""
    from app import core
    setup_shop(client)
    prods = client.get("/api/bootstrap").json()["products"]
    cu = str(uuid.uuid4())
    oid = client.post("/api/orders", json={"client_uuid": cu, "table": "5", "lines": [{"product_id": prods[0]["id"], "qty": 1}]}).json()["order_id"]
    core.rt.store.execute("UPDATE orders SET payment='payrexx' WHERE id=?", (oid,))
    assert client.post("/api/orders/payment", json={"client_uuid": cu, "payment": "cash"}).status_code == 200
    assert core.rt.store.scalar("SELECT payment FROM orders WHERE id=?", (oid,)) == "payrexx"
    assert client.put(f"/api/admin/orders/{oid}/payment", auth=AUTH, json={"payment": "cash"}).status_code == 200
    assert core.rt.store.scalar("SELECT payment FROM orders WHERE id=?", (oid,)) == "cash"
    # «gast-…» vergibt nur der Server: vom Handheld nie
    r = client.post("/api/phone/payrexx", json={"client_uuid": cu, "amount_cents": 100, "key": "gast-12345678"})
    assert r.status_code == 422


def test_cancelled_guest_order_keeps_its_follow_up_window(client, monkeypatch):
    """Aufräumen zählt bei abgebrochenen Online-Bestellungen ab dem Abbruch, nicht ab dem Anlegen: sonst fiele die
    Nachkontrolle (2 Stunden) weg, wenn ein Pi lange aus war und eine alte Bestellung erst jetzt abbricht."""
    import time

    from app import core, gast as g
    monkeypatch.setattr(g, "tick", lambda: None)      # der Monitor im Hintergrund räumt hier nicht selbst auf
    g._cleaned[0] = time.time()                       # ... und ein schon laufender Takt nicht ein zweites Mal
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for key, cancelled_at in (("key-eben-ab01", now), ("key-lang-ab01", "2020-01-02T10:00:00"), ("key-ohne-ab01", None)):
        core.rt.store.execute("INSERT INTO gast_orders(token, key, table_label, lines, amount_cents, status, created_at, "
                              "cancelled_at) VALUES (?,?,?,?,?,?,?,?)",
                              (f"tok-{key}", key, "7", "[]", 650, "cancelled", "2020-01-01T10:00:00", cancelled_at))
    assert g.cleanup() == 2
    assert [r["key"] for r in core.rt.store.query("SELECT key FROM gast_orders")] == ["key-eben-ab01"]


def test_csv_import_keeps_existing_names_with_control_characters(client):
    """Ein Produkt, das (aus einem früheren Import) einen Tabulator im Namen hat, bleibt dasselbe Produkt – die Zeile
    trifft nicht ein anderes mit dem bereinigten Namen."""
    from app import core, csv_import
    core.rt.store.batch([("INSERT INTO products(name, price_cents, active, sort) VALUES (?,?,1,1)", ("Kaf\tfee", 350)),
                         ("INSERT INTO products(name, price_cents, active, sort) VALUES (?,?,1,2)", ("Kaf fee", 400))])
    plan = csv_import.preview(core.rt.store, 'name;preis\n"Kaf\tfee";3.80\n'.encode(), deactivate_missing=True)
    assert plan.ok and plan.rows[0].name == "Kaf\tfee" and plan.rows[0].changes == {"price_cents": [350, 380]}
    assert [r.name for r in plan.deactivate] == ["Kaf fee"]


def test_root_helpers_write_everything_even_with_short_writes(tmp_path, monkeypatch):
    """os.write darf weniger schreiben als verlangt (volle Platte): Lease-Datei und APK-Kopie schreiben bis zum Ende."""
    import importlib.machinery
    import importlib.util
    import os
    from pathlib import Path
    monkeypatch.setenv("FESTPOS_DIENST_USER", "")
    real, calls = os.write, []

    def short(fd, data):
        calls.append(len(data))
        return real(fd, bytes(data[:3]))                 # nie mehr als 3 Bytes auf einmal
    deploy = Path(__file__).parent.parent / "deploy"
    mods = {}
    for name, file in (("dhcp_reload_cr", "festpos-dhcp-reload.py"), ("sicherung_cr", "festpos-sicherung")):
        loader = importlib.machinery.SourceFileLoader(name, str(deploy / file))
        mods[name] = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
        loader.exec_module(mods[name])
    monkeypatch.setattr(os, "write", short)
    leases = tmp_path / "leases"
    leases.write_text("alt\n")
    mods["dhcp_reload_cr"].schreibe(leases, "1 6a:00:00:00:00:01 192.168.55.101 a *\n")
    assert leases.read_text() == "1 6a:00:00:00:00:01 192.168.55.101 a *\n"
    src, dst = tmp_path / "quelle.apk", tmp_path / "ziel.apk"
    src.write_bytes(b"PK" + bytes(range(256)) * 4)
    mods["sicherung_cr"].copy_file(src, dst, False, False)
    assert dst.read_bytes() == src.read_bytes() and len(calls) > 300
