"""Review-Punkte P3 (REV-034 bis REV-051) und optionale Punkte: Sitzungen, Schranken, Eingabegrenzen."""
from __future__ import annotations

import base64
import time

import pytest

from tests.test_api import AUTH, setup_shop


def _login(c, pw="geheim"):
    return c.post("/api/admin/login", json={"password": pw})


# ---------------------------------------------------------------------------
# Sitzungs-Token statt Passwort im Browser
# ---------------------------------------------------------------------------

def test_session_login_and_use(client):
    from app import core
    core.login_throttle.clear()
    assert _login(client, "falsch").status_code == 401
    r = _login(client)
    assert r.status_code == 200
    tok = r.json()["token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/admin/settings", headers=hdr).status_code == 200
    assert client.get("/api/status", headers=hdr).status_code == 200            # Statusanzeige vom PC
    # Basic Auth (curl) geht weiterhin, auch mit Umlauten im Passwort (UTF-8)
    assert client.get("/api/admin/settings", auth=AUTH).status_code == 200
    # gefälschtes / verändertes Token
    exp, uid, gen, nonce, sig = tok.split(".")
    bad = f"{int(exp) + 3600}.{uid}.{gen}.{nonce}.{sig}"
    assert client.get("/api/admin/settings", headers={"Authorization": f"Bearer {bad}"}).status_code == 401
    other = f"{exp}.5.{gen}.{nonce}.{sig}"                                     # anderer Benutzer: Signatur passt nicht
    assert client.get("/api/admin/settings", headers={"Authorization": f"Bearer {other}"}).status_code == 401


def test_session_invalid_after_password_change(client, monkeypatch):
    from app import core
    tok = _login(client).json()["token"]
    monkeypatch.setattr(core.Config, "admin_password", "neues-passwort")
    assert client.get("/api/admin/settings", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_session_expired_does_not_count_as_guessing(client, monkeypatch):
    """Ein offener Status-Tab mit abgelaufener Anmeldung darf den PC nicht sperren (REV-035)."""
    from app import core
    core.login_throttle.clear()
    tok = _login(client).json()["token"]
    monkeypatch.setattr(core.time, "time", lambda: time.monotonic() + 10**10)      # 12 h sind längst um
    for _ in range(core.LOGIN_MAX_FAILS + 3):
        assert client.get("/api/status", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
    monkeypatch.undo()
    assert client.get("/api/admin/settings", auth=AUTH).status_code == 200


def test_umlaut_password_via_basic(client, monkeypatch):
    from app import core
    monkeypatch.setattr(core.Config, "admin_password", "Grüezi€1")
    cred = base64.b64encode("admin:Grüezi€1".encode()).decode()
    assert client.get("/api/admin/settings", headers={"Authorization": f"Basic {cred}"}).status_code == 200
    assert _login(client, "Grüezi€1").status_code == 200


# ---------------------------------------------------------------------------
# REV-037: Anmeldung und Grösse VOR dem Lesen
# ---------------------------------------------------------------------------

def test_admin_upload_rejected_before_reading(client, monkeypatch):
    from app import catalog
    called = []
    orig = catalog.process_image
    monkeypatch.setattr(catalog, "process_image", lambda raw: called.append(1) or orig(raw))
    r = client.post("/api/admin/products/1/image", files={"file": ("x.png", b"x" * 2_000_000)})
    assert r.status_code == 401 and not called
    r = client.post("/api/admin/restore?confirm=JA", files={"file": ("s.sqlite", b"x" * 10)})
    assert r.status_code == 401


def test_body_limits(client):
    big = b"x" * (2 * 1024 * 1024)
    r = client.post("/api/orders", content=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    r = client.post("/api/admin/settings", content=big, headers={"Content-Type": "application/json"}, auth=AUTH)
    assert r.status_code == 413


# ---------------------------------------------------------------------------
# REV-036 / REV-038 / REV-039 / REV-040 / REV-041
# ---------------------------------------------------------------------------

def test_device_heartbeat_needs_cert_when_phone_port(client, monkeypatch):
    from app import core, main
    monkeypatch.setattr(core.Config, "phone_port", 443)
    main.monitor.devices.clear()
    client.get("/api/health", headers={"X-Festpos-Device": "Fake"})               # Admin-Port, ohne Zertifikat
    assert "Fake" not in main.monitor.devices


def test_device_list_is_bounded():
    from app.monitor import MAX_DEVICES, Monitor
    m = Monitor()
    for i in range(MAX_DEVICES + 25):
        m.seen(f"Gerät {i}", "192.168.55.50", "0", "x")
    assert len(m.devices) == MAX_DEVICES and any(d["name"] == f"Gerät {MAX_DEVICES + 24}" for d in m.devices.values())


def test_input_limits(client):
    setup_shop(client)
    assert client.post("/api/admin/tables", auth=AUTH, json={"range_from": 1, "range_to": 10**9}).status_code == 422
    assert client.post("/api/admin/tables", auth=AUTH, json={"range_from": 1, "range_to": 5000}).status_code == 422
    pid = client.get("/api/bootstrap").json()["products"][0]["id"]
    lines = [{"product_id": pid, "qty": 1}] * 201
    assert client.post("/api/orders", json={"client_uuid": "u" * 10, "table": "1", "lines": lines}).status_code == 422
    # nach dem Zusammenfassen über 999
    lines = [{"product_id": pid, "qty": 600}, {"product_id": pid, "qty": 600}]
    r = client.post("/api/orders", json={"client_uuid": "v" * 10, "table": "1", "lines": lines})
    assert r.status_code == 422 and "unplausibel" in r.json()["detail"]


def test_cert_file_name_ascii_and_no_store(client, monkeypatch, tmp_path):
    from app import core, devices
    from tests.test_api import make_device_ca
    assert devices.ascii_name("Kellner Žižek Böhm") == "kellner-zizek-boehm"
    assert devices.ascii_name("€€€") == "geraet"
    crt, key, _ = make_device_ca(tmp_path)
    monkeypatch.setattr(core.Config, "device_ca", str(crt))
    monkeypatch.setattr(core.Config, "device_ca_key", str(key))
    r = client.post("/api/admin/devices/cert", auth=AUTH, json={"name": "Kellner Žižek"}).json()
    assert r["filename"].startswith("festpos-kellner-zizek-")
    d = client.get("/api/device-cert/" + r["url"].rsplit("/", 1)[1])
    assert d.status_code == 200 and d.headers["cache-control"] == "no-store"


def test_legacy_monitor_hosts_validated():
    from app.monitor import parse_targets
    t = parse_targets(None, "Switch=192.168.55.30; Böse=-s 65000; AP=192.168.55.40:8080; X=a b")
    assert [x["ip"] for x in t] == ["192.168.55.30", "192.168.55.40"]
    assert t[1]["port"] == 8080


def test_path_traversal_and_docs(client, monkeypatch):
    from app import core
    monkeypatch.setattr(core.Config, "phone_port", 443)
    # (der Test-Client und Browser lösen «..» selbst auf; curl --path-as-is oder %2e%2e kommen so an)
    for path in ("/admin/%2e%2e/app.js", "/admin/%2E%2E/index.html", "/admin//app.js"):
        r = client.get(path)
        assert r.status_code == 404 and r.json()["detail"] == "Ungültiger Pfad", path
    assert client.get("/api/docs").status_code == 404                      # nur mit FESTPOS_API_DOCS=1
    assert client.get("/api/openapi.json").status_code == 404


@pytest.fixture(autouse=True)
def _reset():
    yield
    try:
        from app import core
        core.login_throttle.clear()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# REV-042 bis REV-046
# ---------------------------------------------------------------------------

def test_display_settings_per_pi_do_not_overwrite(client):
    import json
    from app import core, display
    # alte gemeinsame Einstellung bleibt als Vorgabe gültig
    core.rt.store.execute("INSERT INTO settings(key,value) VALUES ('display_cfg', ?)",
                          (json.dumps({"a": {"type": "tft35", "rotate": 270}, "b": {"type": "hdmi"}}),))
    assert display._display_cfg("a")["rotate"] == 270
    display._display_update("a", rotate=90)
    display._display_update("b", size="320x480")
    assert display._display_cfg("a")["rotate"] == 90 and display._display_cfg("a")["type"] == "tft35"
    assert display._display_cfg("b") ["size"] == "320x480" and display._display_cfg("b")["type"] == "hdmi"
    assert core.rt.store.setting("display_cfg.a") and core.rt.store.setting("display_cfg.b")


def _bon_text(data: bytes) -> list[str]:
    import re
    t = re.sub(rb"\x1d[!B].|\x1b[aEtd3J-].|\x1b2|\x1d\x56..?|\x1b@", b"", data, flags=re.S)
    return [l.strip() for l in t.decode("cp858", "replace").splitlines()]


def test_long_table_name_and_reprint_marker():
    from app.print_service import PrinterCfg, render_single_bon
    p = PrinterCfg(1, "Küche", "x", 9100, 42, True, True)
    item = {"name": "Bratwurst", "price_cents": 800, "qty": 1, "options": None, "note": None, "category": None}
    data = render_single_bon(p, p, event_name="Theater", currency="CHF", table="Bühne links hinten",
                             order_id=7, created_at="2026-10-10T19:30:00", item=item, n=2, of=3, reprint=True)
    lines = _bon_text(data)
    assert lines[0] == "NACHDRUCK"
    assert "2 von 3" in lines and all(len(l) <= 21 for l in lines[2:5])     # doppelt breit: 21 Zeichen pro Zeile


@pytest.mark.parametrize("width", [48, 32])
@pytest.mark.parametrize("table", ["12", "Bühne links hinten beim Ausgang"])
def test_bon_part_of(width, table):
    """T21: «1 von 3» statt «1/3» – auf 80 mm (48) und 58 mm (32), kurzer und langer Tischname: nie abgeschnitten."""
    from app.print_service import PrinterCfg, render_single_bon
    p = PrinterCfg(1, "Küche", "x", 9100, width, True, True)
    item = {"name": "Bratwurst", "price_cents": 800, "qty": 1, "options": None, "note": None, "category": None}
    lines = _bon_text(render_single_bon(p, p, event_name="Theater", currency="CHF", table=table, order_id=7,
                                        created_at="2026-10-10T19:30:00", item=item, n=1, of=3))
    text = " ".join(lines)
    assert "1 von 3" in text and "1/3" not in text
    if table != "12":                                                         # umgebrochen: doppelt breit, halbe Zeile
        assert all(len(ln) <= width // 2 for ln in lines if "TISCH" in ln.upper() or "von 3" in ln)
    words = f"TISCH {table}".upper().split()
    assert all(w in text.upper() for w in words)                              # nichts abgeschnitten
    if table == "12" and width == 48:
        assert any("TISCH 12" in ln and "1 von 3" in ln for ln in lines)       # passt in eine Zeile


def test_reprint_job_is_marked(client):
    from app import core
    setup_shop(client)
    import uuid
    pid = client.get("/api/bootstrap").json()["products"][0]["id"]
    client.post("/api/orders", json={"client_uuid": str(uuid.uuid4()), "table": "3", "lines": [{"product_id": pid, "qty": 1}]})
    j = client.get("/api/admin/jobs", auth=AUTH).json()[0]["id"]
    core.rt.store.execute("UPDATE print_jobs SET status='done' WHERE id=?", (j,))
    new = client.post(f"/api/admin/jobs/{j}/reprint", auth=AUTH).json()["job_id"]
    assert core.rt.store.scalar("SELECT reprint FROM print_jobs WHERE id=?", (new,)) == 1
    assert core.rt.store.scalar("SELECT reprint FROM print_jobs WHERE id=?", (j,)) == 0


def test_default_printer_prefers_enabled(client):
    from app import core
    from app.orders import default_printer
    setup_shop(client)
    rows = core.rt.store.query("SELECT id FROM printers ORDER BY id")
    first, second = rows[0]["id"], rows[1]["id"]
    core.rt.store.execute("UPDATE printers SET enabled=0 WHERE id=?", (first,))
    assert default_printer(core.rt.store) == second
    core.rt.store.execute("INSERT INTO settings(key,value) VALUES ('default_printer_id', ?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(first),))
    assert default_printer(core.rt.store) == second                     # gewählter ist aus -> eingeschalteter
    core.rt.store.execute("UPDATE printers SET enabled=0")
    assert default_printer(core.rt.store) == first                      # alle aus: lieber warten als verlieren


def test_csv_negative_price_umlaut_category_missing_price_and_formulas(client):
    from app import core, csv_import
    setup_shop(client)
    assert csv_import.parse_price("4.–") == 400 and csv_import.parse_price("CHF 4.50") == 450
    with pytest.raises(ValueError):
        csv_import.parse_price("-2.00")
    csv = ("name;preis;kategorie\r\n"
           "Eistee;4.00;GETRÄNKE\r\n"
           "Neu ohne Preis;;Essen\r\n"
           "Rabatt;-2.00;Essen\r\n").encode()
    r = client.post("/api/admin/import/products", auth=AUTH, files={"file": ("p.csv", csv)}).json()
    errs = {row["name"]: row["errors"] for row in r["rows"] if row["errors"]}
    assert "Neu ohne Preis" in errs and "Rabatt" in errs and "Eistee" not in errs
    csv = "name;preis;kategorie\r\nEistee;4.00;GETRÄNKE\r\n=HYPERLINK(1);3.00;Getränke\r\n".encode()
    r = client.post("/api/admin/import/products?commit=true", auth=AUTH, files={"file": ("p.csv", csv)})
    assert r.status_code == 200 and r.json()["committed"]
    cat = core.rt.store.scalar("SELECT c.name FROM products p JOIN categories c ON c.id=p.category_id WHERE p.name='Eistee'")
    assert cat == "Getränke"
    assert core.rt.store.scalar("SELECT COUNT(*) FROM categories WHERE name LIKE 'GETR%'") == 1
    out = client.get("/api/admin/export/products.csv", auth=AUTH).content.decode("utf-8-sig")
    assert "\r\n'=HYPERLINK(1);" in out
    # Export -> Import ändert nichts (auch nicht am Formel-Schutz)
    r = client.post("/api/admin/import/products", auth=AUTH, files={"file": ("e.csv", out.encode("utf-8"))}).json()
    assert r["summary"]["ok"] and not r["summary"].get("update")


# ---------------------------------------------------------------------------
# REV-049 / REV-050: root-Helfer
# ---------------------------------------------------------------------------

def _settime_env(tmp_path):
    import os
    for d in ("zeit", "zeit-neu", "ergebnis"):
        (tmp_path / d).mkdir(exist_ok=True)
    return {**os.environ, "FESTPOS_ZEIT_DIR": str(tmp_path / "zeit"), "FESTPOS_ZEIT_STAND": str(tmp_path / "stand"),
            "FESTPOS_ZEIT_ERGEBNIS": str(tmp_path / "ergebnis"), "FESTPOS_ZEIT_TROCKEN": "1"}


def _helper():
    from pathlib import Path
    return str(Path(__file__).resolve().parent.parent / "deploy" / "festpos-settime")


def test_settime_helper_requests(tmp_path):
    import subprocess
    env = _settime_env(tmp_path)
    z = tmp_path / "zeit"
    (z / "anfrage-abc123").write_text("abc123 1800000000 take\n")
    (z / "anfrage-x1").write_text("x1 99\n")                             # unplausibel
    geheim = tmp_path / "geheim"
    geheim.write_text("s3cr3t 1800000000\n")
    (z / "anfrage-sym").symlink_to(geheim)                               # Symlink: nie gelesen
    (z / "fremd").write_text("irgendwas")
    subprocess.run([_helper(), "--anfrage"], env=env)
    erg = tmp_path / "ergebnis"
    assert (erg / "abc123").read_text().startswith("ok ")
    assert (erg / "x1").read_text().strip() == "fehler unplausible Zeit"
    assert (erg / "sym").read_text().strip() == "fehler Auftrag unlesbar"
    assert not list(z.iterdir())                                         # alles weg, sonst startet systemd endlos
    assert "s3cr3t" not in "".join(f.read_text() for f in erg.iterdir())
    # T2: gestellt -> zeit-stand mit der Quelle (die App sieht daran, dass die Uhr seit dem Start gestellt ist)
    import json
    assert json.loads((tmp_path / "stand").read_text())["source"] == "take"


def test_settime_rtc_ohne_hwclock(tmp_path, monkeypatch):
    """Die Zeit kommt per ioctl ins RTC (Pi 5), auch wenn hwclock fehlt; ohne echtes RTC einfach False."""
    import importlib.machinery
    import importlib.util
    import struct
    loader = importlib.machinery.SourceFileLoader("festpos_settime", _helper())
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("festpos_settime", loader))
    loader.exec_module(mod)
    # Fr 23.10.2026 13:51:52 UTC: Monat 0–11, Jahr ab 1900, Freitag = 5, 296. Tag (0-basiert 295)
    assert struct.unpack("9i", mod.rtc_bytes(1792763512)) == (52, 51, 13, 23, 9, 126, 5, 295, 0)
    assert len(mod.rtc_bytes(0)) == 36 and mod.RTC_SET_TIME & 0x3FFF0000 == 36 << 16
    monkeypatch.setattr(mod, "RTC", str(tmp_path / "fehlt"))
    assert mod.rtc_schreiben() is False
    (tmp_path / "datei").write_text("")
    monkeypatch.setattr(mod, "RTC", str(tmp_path / "datei"))              # kein Gerät: ioctl scheitert
    assert mod.rtc_schreiben() is False


def test_set_local_concurrent_requests(tmp_path, monkeypatch):
    """Zwei Aufträge gleichzeitig (z.B. Doppelklick): beide bekommen ihre Antwort."""
    import subprocess
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from app import timesync
    env = _settime_env(tmp_path)
    monkeypatch.setattr(timesync, "ZEIT_DIR", str(tmp_path / "zeit"))
    monkeypatch.setattr(timesync, "ZEIT_NEU", str(tmp_path / "zeit-neu"))
    monkeypatch.setattr(timesync, "ZEIT_ERGEBNIS", str(tmp_path / "ergebnis"))
    stop = threading.Event()

    def root_dienst():                                                   # spielt festpos-settime.path
        while not stop.is_set():
            if any((tmp_path / "zeit").iterdir()):
                subprocess.run([_helper(), "--anfrage"], env=env)
            time.sleep(0.05)
    t = threading.Thread(target=root_dienst)
    t.start()
    try:
        with ThreadPoolExecutor(3) as pool:
            res = list(pool.map(lambda ms: timesync.set_local(ms), [1_800_000_000_000] * 3))
    finally:
        stop.set()
        t.join()
    assert all(ok for ok, _ in res), res
    monkeypatch.setattr(timesync, "ZEIT_DIR", str(tmp_path / "fehlt"))
    assert timesync.set_local(1_800_000_000_000)[0] is False


def test_display_helper_ignores_symlink_and_huge(tmp_path, monkeypatch):
    from tests.test_stufe2 import _display_helper
    mod, _ = _display_helper(tmp_path, monkeypatch)
    target = tmp_path / "anders.json"
    target.write_text('{"type": "hdmi"}')
    (tmp_path / "link.json").symlink_to(target)
    assert mod.read(str(tmp_path / "link.json"), "nein") == "nein"
    big = tmp_path / "gross.json"
    big.write_text('{"type": "' + "x" * 70000 + '"}')
    assert mod.read(str(big), "nein") == "nein"
    assert mod.read(str(target)) == {"type": "hdmi"}


def test_ssh_from_list():
    import subprocess
    from pathlib import Path
    conf = Path(__file__).resolve().parent.parent / "deploy" / "festpos-conf.sh"

    def ip_list(t):
        r = subprocess.run(["bash", "-c", f'source "{conf}"; festpos_ip_list "$1"', "_", t], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    assert ip_list("192.168.55.200-192.168.55.254, 10.0.0.0/8") == "192.168.55.200-192.168.55.254, 10.0.0.0/8"
    assert ip_list("-s 65000") is None and ip_list("1.2.3") is None and ip_list("") is None
    assert ip_list("192.168.55.0/40") is None
