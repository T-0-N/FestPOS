"""Stufe 2 des Reviews: Zeitabgleich (REV-012/013) und weitere Punkte vor dem Ausbau auf 3 Pi."""
from __future__ import annotations

import json
import time

import pytest

from app import timesync
from tests.test_api import AUTH, setup_shop

# ---------------------------------------------------------------------------
# REV-012: Zeitabgleich
# ---------------------------------------------------------------------------


def test_broadcast_uses_monotonic_clock_and_sets_itself_last(monkeypatch):
    calls: list[tuple[str, int]] = []

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout, context):
        calls.append((req.full_url, json.loads(req.data)["epoch_ms"]))
        return Resp()

    def fake_set_local(ms, source=""):
        calls.append(("lokal", ms))
        return True, "ok"

    monkeypatch.setattr(timesync.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(timesync, "set_local", fake_set_local)
    # Systemuhr springt während des Sendens um eine Stunde – darf nicht als Laufzeit zählen
    real = time.time
    monkeypatch.setattr(timesync.time, "time", lambda: real() + 3600)
    peers = ["https://192.168.55.11:8443", "https://192.168.55.12:8443", "https://192.168.55.13:8443"]
    monkeypatch.setattr(timesync.ssl, "create_default_context", lambda cafile: ("geprüft", cafile))
    res = timesync.broadcast(peers, 1_800_000_000_000, "langes-geheimnis-123", "ca.crt", "192.168.55.11")
    assert [c[0] for c in calls] == ["https://192.168.55.12:8443/api/internal/settime",
                                     "https://192.168.55.13:8443/api/internal/settime", "lokal"]
    assert all(0 <= ms - 1_800_000_000_000 < 2000 for _, ms in calls)
    assert res["lokal"] == "ok"
    # Platzhalter-Geheimnis: die anderen Pi werden nicht angefragt, nur lokal gestellt
    calls.clear()
    res = timesync.broadcast(peers, 1_800_000_000_000, "bitte-aendern-lang-und-zufaellig", "ca.crt", "192.168.55.11")
    assert [c[0] for c in calls] == ["lokal"] and "übersprungen" in res["https://192.168.55.12:8443"]
    # ohne CA: das Geheimnis geht nicht über eine ungeprüfte Verbindung
    calls.clear()
    res = timesync.broadcast(peers, 1_800_000_000_000, "langes-geheimnis-123", None, "192.168.55.11")
    assert [c[0] for c in calls] == ["lokal"] and "FESTPOS_CA" in res["https://192.168.55.12:8443"]


def test_time_sync_never_sets_the_pi(client, monkeypatch):
    """T2: Die Handys stellen den Pi nicht mehr – weder zwei übereinstimmende noch eines, wenn die Serveruhr vor der
    letzten Bestellung steht. Die Abweichung wird nur gemerkt (Anzeige unter Geräte)."""
    from app import time_admin
    sent = []
    monkeypatch.setattr(time_admin.timesync, "broadcast", lambda *a, **k: sent.append(a) or {"lokal": "ok"})
    monkeypatch.setattr(time_admin.timesync, "set_local", lambda *a, **k: sent.append(a) or (True, "ok"))
    ahead = int((time.time() + 3600) * 1000)
    for dev in ("Kellner 1", "Kellner 2", "Kellner 3"):
        r = client.post("/api/time/sync", json={"epoch_ms": ahead}, headers={"X-Festpos-Device": dev}).json()
        assert r["changed"] is False and 3500 < r["diff_s"] < 3700
    future = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + 86400))
    time_admin.rt.store.execute("INSERT INTO orders(client_uuid, table_label, device, total_cents, created_at) "
                                "VALUES ('u1','1','x',0,?)", (future,))
    r = client.post("/api/time/sync", json={"epoch_ms": int((time.time() + 2 * 86400) * 1000)}).json()
    assert r["changed"] is False and not sent
    phones = client.get("/api/admin/time", auth=AUTH).json()["phones"]
    assert phones["dev:Kellner 2"]["offset"] == 3600 and phones["dev:Kellner 2"]["android"] is False
    # T31: Android aus der Kennung des Browsers (Fully, Chrome) – nur dort die Karte «Uhrzeit» in der Geräteansicht
    ua = "Mozilla/5.0 (Linux; Android 14; SM-G556B) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36"
    client.post("/api/time/sync", json={"epoch_ms": ahead}, headers={"X-Festpos-Device": "XCover", "User-Agent": ua})
    assert client.get("/api/admin/time", auth=AUTH).json()["phones"]["dev:XCover"]["android"] is True


# ---------------------------------------------------------------------------
# REV-013: /api/internal/settime
# ---------------------------------------------------------------------------

def test_internal_settime_secret_and_throttle(client, monkeypatch):
    from app import main as m
    from app import time_admin as ta
    monkeypatch.setattr(ta.timesync, "set_local", lambda ms, *a, **k: (True, "ok"))
    ta.settime_throttle.clear()
    body = {"epoch_ms": 1_800_000_000_000}
    # kein / Platzhalter-Geheimnis: immer abgelehnt (früher: Admin-Passwort als Rückfall)
    monkeypatch.setattr(m.Config, "cluster_secret", "")
    assert client.post("/api/internal/settime", json=body, headers={"X-Festpos-Secret": ""}).status_code == 403
    assert client.post("/api/internal/settime", json=body, headers={"X-Festpos-Secret": "geheim"}).status_code == 403
    monkeypatch.setattr(m.Config, "cluster_secret", "bitte-aendern-lang-und-zufaellig")
    assert client.post("/api/internal/settime", json=body,
                       headers={"X-Festpos-Secret": "bitte-aendern-lang-und-zufaellig"}).status_code == 403
    # echtes Geheimnis mit Umlaut: kein Fehler 500 mehr
    ta.settime_throttle.clear()
    monkeypatch.setattr(m.Config, "cluster_secret", "Grüessli-vom-Pi-2026")
    hdr = {"X-Festpos-Secret": "Grüessli-vom-Pi-2026".encode()}          # so sendet broadcast()
    assert client.post("/api/internal/settime", json=body,
                       headers={"X-Festpos-Secret": "falsch-ä".encode()}).status_code == 403
    assert client.post("/api/internal/settime", json=body, headers=hdr).status_code == 200
    # Sperre nach 10 Fehlversuchen – danach auch mit dem richtigen Geheimnis
    for _ in range(10):
        client.post("/api/internal/settime", json=body, headers={"X-Festpos-Secret": "falsch"})
    assert client.post("/api/internal/settime", json=body, headers=hdr).status_code == 429


@pytest.fixture(autouse=True)
def _reset_throttles():
    yield
    try:
        from app import main as m
        from app import time_admin as ta
        ta.settime_throttle.clear()
        ta.peer_throttle.clear()
        m.login_throttle.clear()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# REV-014 / REV-016 / REV-023: Druckaufträge
# ---------------------------------------------------------------------------

def _order(c, table="7"):
    import uuid
    boot = c.get("/api/bootstrap").json()
    pid = {p["name"]: p["id"] for p in boot["products"]}
    body = {"client_uuid": str(uuid.uuid4()), "table": table, "device": "XCover-1",
            "lines": [{"product_id": pid["Bier 5dl"], "qty": 1}, {"product_id": pid["Bratwurst"], "qty": 1}]}
    assert c.post("/api/orders", json=body).status_code == 200


def _printers(c):
    return {p["name"]: p["id"] for p in c.get("/api/admin/printers", auth=AUTH).json()}


def test_delete_printer_refused_while_jobs_open(client):
    setup_shop(client)
    pr = _printers(client)
    kueche, bar = pr["Küche"], pr["Getränkeausgabe"]
    client.put(f"/api/admin/printers/{bar}", auth=AUTH, json={"name": "Getränkeausgabe", "host": "127.0.0.1", "port": 1,
                                                             "fail_mode": "reroute", "fail_target": kueche})
    _order(client)
    r = client.delete(f"/api/admin/printers/{kueche}", auth=AUTH)
    assert r.status_code == 409 and "offene" in r.json()["detail"]
    # umgeleitet: Station bleibt Küche -> Löschen würde die Bons immer noch mitnehmen
    client.post(f"/api/admin/printers/{kueche}/reroute?to={bar}", auth=AUTH)
    assert client.delete(f"/api/admin/printers/{kueche}", auth=AUTH).status_code == 409
    for j in client.get("/api/admin/jobs", auth=AUTH).json():
        client.post(f"/api/admin/jobs/{j['id']}/resolve", auth=AUTH)
    assert client.delete(f"/api/admin/printers/{kueche}", auth=AUTH).status_code == 200
    rows = {p["name"]: p for p in client.get("/api/admin/printers", auth=AUTH).json()}
    assert "Küche" not in rows and rows["Getränkeausgabe"]["fail_target"] is None


def test_reprint_and_resolve_stuck_printing_jobs(client):
    from app import main as m, print_service as ps
    setup_shop(client)
    _order(client)
    jobs = client.get("/api/admin/jobs", auth=AUTH).json()
    j1, j2 = jobs[0]["id"], jobs[1]["id"]
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    m.store.execute("UPDATE print_jobs SET status='printing', claimed_by='anderer', claimed_at=? WHERE id IN (?,?)",
                    (now, j1, j2))
    r = client.post(f"/api/admin/jobs/{j1}/reprint", auth=AUTH)
    assert r.status_code == 409 and "gerade gedruckt" in r.json()["detail"]
    assert client.post(f"/api/admin/jobs/{j1}/resolve", auth=AUTH).status_code == 409
    assert m.store.scalar("SELECT COUNT(*) FROM print_jobs") == len(jobs)          # kein zusätzlicher Bon
    # hängt seit mehr als 2 Minuten (bzw. aus älterer Version ohne Zeitstempel)
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - ps.STUCK_AFTER_S - 10))
    m.store.execute("UPDATE print_jobs SET claimed_at=? WHERE id=?", (old, j1))
    m.store.execute("UPDATE print_jobs SET claimed_at=NULL WHERE id=?", (j2,))
    r = client.post(f"/api/admin/jobs/{j1}/reprint", auth=AUTH)
    assert r.status_code == 200
    assert m.store.scalar("SELECT status FROM print_jobs WHERE id=?", (j1,)) == "cancelled"
    assert m.store.scalar("SELECT status FROM print_jobs WHERE id=?", (r.json()["job_id"],)) in ("pending", "printing")
    assert client.post(f"/api/admin/jobs/{j2}/resolve", auth=AUTH).json()["ok"] is True
    # erledigte Bons lassen sich weiterhin nachdrucken
    m.store.execute("UPDATE print_jobs SET status='done' WHERE id=?", (j2,))
    assert client.post(f"/api/admin/jobs/{j2}/reprint", auth=AUTH).status_code == 200


def test_restore_cancels_open_jobs_inside_the_file(client):
    import sqlite3
    from app import main as m, maintenance
    setup_shop(client)
    _order(client)
    backup = m.store.backup()                              # Datenbank wie in der Sicherung
    fixed, n = maintenance._prepare_backup(backup, 1)
    assert n >= 1
    con = sqlite3.connect(":memory:")
    con.deserialize(fixed)
    assert con.execute("SELECT COUNT(*) FROM print_jobs WHERE status IN ('pending','printing')").fetchone()[0] == 0
    con.close()
    r = client.post("/api/admin/restore?confirm=JA", auth=AUTH, files={"file": ("s.sqlite", backup)})
    assert r.status_code == 200 and r.json()["jobs_cancelled"] >= 1
    assert m.store.scalar("SELECT COUNT(*) FROM print_jobs WHERE status IN ('pending','printing')") == 0
    bad = client.post("/api/admin/restore?confirm=JA", auth=AUTH, files={"file": ("s.sqlite", b"SQLite format 3\x00kaputt")})
    assert bad.status_code == 422


# ---------------------------------------------------------------------------
# REV-020 / REV-021: Optionen und App-Updates
# ---------------------------------------------------------------------------

def test_label_choices_same_choice_in_two_groups():
    from app.options import label_choices, parse_text
    d = parse_text("Zucker*: ohne|1 Zucker|2 Zucker; Milch: ohne|Hafer; Extra+: Sahne|Zimt")
    # neue App: schickt schon mit Gruppennamen
    assert label_choices(d, ["Zucker: ohne", "Milch: ohne"]) == ["Zucker: ohne", "Milch: ohne"]
    # ältere App: beide «ohne» in Gruppen-Reihenfolge -> eindeutig zuzuordnen
    assert label_choices(d, ["ohne", "ohne", "Zimt"]) == ["Zucker: ohne", "Milch: ohne", "Zimt"]
    # nur ein «ohne» von einer älteren App: nicht raten
    assert label_choices(d, ["ohne", "Hafer"]) == ["ohne", "Hafer"]
    # gewöhnliche Doppel werden weiterhin zusammengelegt
    assert label_choices(d, ["Sahne", "Sahne"]) == ["Sahne"]
    assert label_choices(None, ["a", "a"]) == ["a"]


def test_order_keeps_both_ohne_on_the_bon(client):
    import uuid
    from app import main as m
    setup_shop(client)
    pid = {p["name"]: p["id"] for p in client.get("/api/bootstrap").json()["products"]}["Bier 5dl"]
    m.store.execute("UPDATE products SET options=? WHERE id=?",
                    ('{"groups":[{"name":"Zucker","choices":["ohne","2 Zucker"]},'
                     '{"name":"Milch","choices":["ohne","Hafer"]}],"free":false}', pid))
    for opts in (["ohne", "ohne"], ["Zucker: ohne", "Milch: ohne"]):          # alte und neue App
        body = {"client_uuid": str(uuid.uuid4()), "table": "3", "lines": [{"product_id": pid, "qty": 1, "options": opts}]}
        oid = client.post("/api/orders", json=body).json()["order_id"]
        assert m.store.scalar("SELECT options FROM order_items WHERE order_id=?", (oid,)) == "Zucker: ohne, Milch: ohne"


def test_app_files_are_revalidated(tmp_path, monkeypatch):
    from pathlib import Path
    from fastapi.testclient import TestClient
    monkeypatch.setenv("FESTPOS_DB", f"sqlite://{tmp_path}/c.db")
    monkeypatch.setenv("FESTPOS_STATIC", str(Path(__file__).resolve().parent.parent / "static"))
    from tests.conftest import load_app
    main = load_app()
    with TestClient(main.app) as c:
        for path in ("/app.js", "/sw.js", "/index.html", "/"):
            r = c.get(path)
            assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", path
        assert "no-cache" not in c.get("/api/health").headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# REV-028: Gerätezertifikate erneuern und sperren
# ---------------------------------------------------------------------------

def _as_phone(monkeypatch, m, serial, name="Kellner 1"):
    info = {"serial": serial, "name": name, "days_left": 3000, "not_after": "2036-01-01"}
    from app import devices
    monkeypatch.setattr(devices, "client_cert", lambda request: dict(info))


def test_renewed_cert_retires_old_one_on_first_use(client, monkeypatch, tmp_path):
    from app import main as m
    from tests.test_api import make_device_ca
    crt, key, _ = make_device_ca(tmp_path)
    monkeypatch.setattr(m.Config, "device_ca", str(crt))
    monkeypatch.setattr(m.Config, "device_ca_key", str(key))
    old = client.post("/api/admin/devices/cert", auth=AUTH, json={"name": "Kellner 1"}).json()["serial"]
    _as_phone(monkeypatch, m, old)
    assert client.post("/api/device/renew").status_code == 200
    new = m.store.scalar("SELECT serial FROM device_certs WHERE replaces=?", (old,))
    assert new and old not in m.revoked_serials()                 # altes gilt weiter, bis das neue benutzt wird
    _as_phone(monkeypatch, m, new)
    assert client.post("/api/time/sync", json={"epoch_ms": int(time.time() * 1000)}).status_code == 200
    assert old in m.revoked_serials() and new not in m.revoked_serials()
    _as_phone(monkeypatch, m, old)                                  # verlorenes Handy mit dem alten Zertifikat
    assert client.get("/api/bootstrap").status_code == 403
    assert client.post("/api/time/sync", json={"epoch_ms": int(time.time() * 1000)}).status_code == 403


def test_revoke_blocks_all_certs_of_the_device(client, monkeypatch, tmp_path):
    from app import main as m
    from tests.test_api import make_device_ca
    crt, key, _ = make_device_ca(tmp_path)
    monkeypatch.setattr(m.Config, "device_ca", str(crt))
    monkeypatch.setattr(m.Config, "device_ca_key", str(key))
    s1 = client.post("/api/admin/devices/cert", auth=AUTH, json={"name": "Kellner 2"}).json()["serial"]
    assert client.post("/api/admin/devices/cert", auth=AUTH, json={"name": "kellner 2"}).status_code == 409   # T1
    s2 = client.post("/api/admin/devices/cert", auth=AUTH, json={"name": "Kellner 2", "renew": True}).json()["serial"]
    s3 = client.post("/api/admin/devices/cert", auth=AUTH, json={"name": "Kellner 3"}).json()["serial"]
    certs = client.get("/api/admin/devices", auth=AUTH).json()["certs"]
    newest = next(c for c in certs if c["serial"] == s2)
    assert client.post(f"/api/admin/devices/{newest['id']}/revoke", auth=AUTH).json()["changed"] == 2
    assert {s1, s2} <= m.revoked_serials() and s3 not in m.revoked_serials()
    _as_phone(monkeypatch, m, s1, "Kellner 2")
    assert client.get("/api/bootstrap").status_code == 403         # auch kein Menü / PIN-Hash mehr
    assert client.post(f"/api/admin/devices/{newest['id']}/revoke?undo=true", auth=AUTH).json()["changed"] == 2
    assert not ({s1, s2} & m.revoked_serials())


# ---------------------------------------------------------------------------
# REV-024: Neustart-Auftrag für den Bildschirm
# ---------------------------------------------------------------------------

def _display_helper(tmp_path, monkeypatch):
    import importlib.machinery
    import importlib.util
    from pathlib import Path
    path = str(Path(__file__).resolve().parent.parent / "deploy" / "festpos-display")
    loader = importlib.machinery.SourceFileLoader("festpos_display", path)
    spec = importlib.util.spec_from_loader("festpos_display", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    monkeypatch.setattr(mod, "WISH", str(tmp_path / "display.json"))
    monkeypatch.setattr(mod, "STATUS", str(tmp_path / "run" / "status.json"))
    monkeypatch.setattr(mod, "STATE", str(tmp_path / "state"))
    monkeypatch.setattr(mod, "CURSOR", str(tmp_path / "festpos-kiosk-cursor"))
    calls = []
    monkeypatch.setattr(mod, "sh", lambda *a: calls.append(a) or 1)
    monkeypatch.setattr(mod, "connectors", lambda: [])
    return mod, calls


def test_reboot_token_only_when_fresh_and_known(tmp_path, monkeypatch):
    mod, calls = _display_helper(tmp_path, monkeypatch)
    wish = tmp_path / "display.json"

    def run(token):
        wish.write_text(json.dumps({"type": None, "reboot": token}))
        calls.clear()
        mod.main()
        return ("systemctl", "reboot") in calls

    fresh = f"{int(time.time())}-abcd"
    assert run(fresh) is False                                     # neue Karte: nur merken
    again = f"{int(time.time())}-beef"                             # einmal: sonst über eine Sekundengrenze zwei Aufträge
    assert run(again) is True                                      # frischer Auftrag: Neustart
    assert run(again) is False                                     # derselbe nicht nochmals
    assert run(f"{int(time.time()) - 3 * 86400}-0ld0") is False    # aus einer Sicherung / von früher
    assert run("a1b2c3d4e5f6") is False                            # altes Format ohne Zeit


# ---------------------------------------------------------------------------
# REV-029 (App-Seite): antwortet schon, bevor die Datenbank läuft
# ---------------------------------------------------------------------------

def test_app_answers_cluster_health_before_db_is_up(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tests.conftest import RQLITED, RqliteNode, free_port, wait_for
    if not __import__("pathlib").Path(RQLITED).exists():
        pytest.skip("rqlited nicht gefunden")
    http, raft = free_port(), free_port()
    data = tmp_path / "rq"
    data.mkdir()
    (data / ".festpos-einzelbetrieb").write_text("2026-10-10")
    monkeypatch.setenv("FESTPOS_DB", f"rqlite://127.0.0.1:{http}")
    monkeypatch.setenv("FESTPOS_RQ_DATA", str(data))
    monkeypatch.setenv("FESTPOS_NODE", "a")
    monkeypatch.setenv("FESTPOS_STATIC", str(tmp_path / "nostatic"))
    from tests.conftest import load_app
    main = load_app()
    node = RqliteNode("a", tmp_path / "db", http, raft, [], cluster_size=1)
    try:
        with TestClient(main.app) as c:
            h = c.get("/api/health/cluster").json()                   # Datenbank läuft noch nicht
            assert h == {"node": "a", "state": None, "leader": None, "members": [], "einzelbetrieb": True}
            assert c.get("/api/health").status_code == 200
            r = c.get("/api/bootstrap")
            assert r.status_code == 503 and "startet" in r.json()["detail"]
            node.start(found=True)
            wait_for(lambda: c.get("/api/bootstrap").status_code == 200, 30, "App bereit, sobald rqlite läuft")
            h = c.get("/api/health/cluster").json()
            assert h["state"] == "Leader" and h["members"] == ["a"] and h["einzelbetrieb"] is True
    finally:
        node.stop()


def test_cluster_secret_derived_from_server_key(tmp_path, monkeypatch):
    key = tmp_path / "server.key"
    key.write_bytes(b"-----BEGIN PRIVATE KEY-----\ngeheim\n-----END PRIVATE KEY-----\n")
    monkeypatch.setenv("FESTPOS_DB", f"sqlite://{tmp_path}/s.db")
    monkeypatch.setenv("FESTPOS_TLS_KEY", str(key))
    monkeypatch.setenv("FESTPOS_CLUSTER_SECRET", "bitte-aendern-lang-und-zufaellig")
    from tests.conftest import load_app
    main = load_app()
    derived = main.Config.cluster_secret
    assert derived.startswith("auto-") and timesync.secret_usable(derived)
    monkeypatch.setenv("FESTPOS_CLUSTER_SECRET", "mein-eigenes-langes-geheimnis")
    main = load_app()
    assert main.Config.cluster_secret == "mein-eigenes-langes-geheimnis"
