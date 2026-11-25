"""Im echten Browser (Version 2026.10.10-26): Verwaltung › Update – Schlüssel sehen, festpos.zip + .sig hochladen,
auf alle Pi verteilen, Fortschritt und Fehler; die Statusanzeige meldet das laufende Update.
Den root-Helfer festpos-update spielt der Test (er beantwortet die Aufträge). Wird übersprungen ohne Chromium."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from app import release  # noqa: E402
from tests.release_util import build_bundle, keypair  # noqa: E402
from tests.test_browser_p4 import browser, _preconfirm, _start, _watch  # noqa: E402,F401  (browser = Fixture)

NEW = "99.0.0"
CUR = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()   # läuft gerade


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("update")
    d = {k: tmp / k for k in ("pakete", "req", "reqneu", "status", "alt")}
    for p in d.values():
        p.mkdir()
    pk, line = keypair("Vereins-Update")
    (d["status"] / "keys.json").write_text(json.dumps({"keys": [release.parse_key_line(line).public()], "problems": []}))
    (d["alt"] / "VERSION").write_text("2026.10.10-25\n")
    z = build_bundle(tmp / "festpos.zip", NEW)
    (tmp / "festpos.zip.sig").write_bytes(release.sign(z, pk))
    proc, base, c = _start(tmp, extra_env={
        "FESTPOS_UPDATE_DIR": str(d["pakete"]), "FESTPOS_UPDATE_REQ": str(d["req"]), "FESTPOS_UPDATE_REQ_NEU": str(d["reqneu"]),
        "FESTPOS_UPDATE_STATUS": str(d["status"]), "FESTPOS_ALT": str(d["alt"]),
        "FESTPOS_UPDATE_INTERVAL": "0.3", "FESTPOS_UPDATE_SETTLE": "0"})
    # Drucker aus: sie zeigen auf einen Port, der nie antwortet – je nach Zeitpunkt der Drucker-Prüfung stünden sie
    # als Störung vor dem Update-Hinweis der Statusanzeige (Test hing im Gesamtlauf von der Laufzeit ab)
    for pr in c.get("/api/admin/printers", auth=("admin", "geheim")).json():
        r = c.put(f"/api/admin/printers/{pr['id']}", auth=("admin", "geheim"), json={**pr, "language": pr.get("language") or "", "enabled": False})
        assert r.status_code == 200, r.text
    yield {"base": base, "c": c, "zip": z, "sig": tmp / "festpos.zip.sig", **d}
    proc.terminate()
    proc.wait(10)


def _admin(browser, base, width=1300):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": 1000})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html#maint/update")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector("#upd-form")
    _preconfirm(page)                                    # kritische Aktionen: schon bestätigt (#9)
    page.wait_for_selector("#upd-form")
    return ctx, page, errs


def _answer(env, ok=True, after=NEW, msg="OK: Server läuft mit Version " + NEW, code=None) -> dict:
    """Wie festpos-update: auf den Auftrag warten und ihn beantworten."""
    for _ in range(100):
        files = list(env["req"].iterdir())
        if files:
            break
        time.sleep(0.1)
    req = json.loads(files[0].read_text())
    files[0].unlink()
    (env["status"] / f"{req['id']}.json").write_text(json.dumps(
        {"id": req["id"], "ok": ok, "code": code, "msg": msg, "after": after, "log": ["==> Sichere Version", msg]}))
    return req


def test_upload_roll_out_and_follow_progress(env, browser):  # noqa: F811
    ctx, page, errs = _admin(browser, env["base"])
    assert "Vereins-Update" in page.inner_text("#upd-keys")
    row = page.locator('.upd-nodes tr[data-pi="a"]')
    assert CUR in row.inner_text() and "2026.10.10-25" in row.inner_text()                  # läuft / Sicherung
    assert "Alle Pi zurück auf 2026.10.10-25" in page.inner_text("#upd-back")
    page.set_input_files("#upd-files", [str(env["zip"])])                                   # nur das ZIP: Hinweis
    page.click("#upd-check")
    page.wait_for_selector(".toast.bad")
    assert "beide Dateien" in page.inner_text(".toast")
    page.set_input_files("#upd-files", [str(env["zip"]), str(env["sig"])])
    page.click("#upd-check")
    page.wait_for_selector("#upd-start")
    info = page.inner_text("#upd-upload")
    assert f"Version {NEW}" in info and "signiert von «Vereins-Update»" in info and "Pi A" in info
    _shot(page, "update-upload.png")
    page.on("dialog", lambda d: d.accept())
    page.click("#upd-start")
    req = _answer(env)                                                                     # die Schleife auf dem Pi
    assert req["action"] == "install" and req["version"] == NEW
    page.wait_for_function("() => document.querySelector('#upd-plan')?.textContent.includes('Fertig')", timeout=15000)
    plan = page.inner_text("#upd-plan")
    assert f"Update auf Version {NEW}" in plan and "Gestartet von admin" in plan
    assert "fertig" in row.inner_text()
    _shot(page, "update-done.png", full_page=True)
    assert not errs, errs
    ctx.close()
    acts = [a["action"] for a in env["c"].get("/api/admin/audit", auth=("admin", "geheim")).json()["entries"]]
    assert {"update_upload", "update_start", "update_pi"} <= set(acts)


def test_failure_stops_and_status_page_tells(env, browser):  # noqa: F811
    """Fehler: Plan hält an (Erneut versuchen / Abbrechen), die Statusanzeige zeigt es rot."""
    c = env["c"]
    # neuer Plan für eine noch neuere Version (über die Schnittstelle), signiert mit einem zweiten Schlüssel
    z2 = build_bundle(env["zip"].with_name("neu.zip"), "99.0.1")
    ctx, page, errs = _admin(browser, env["base"])
    pk, line = keypair("Zweiter Schlüssel")
    keys = json.loads((env["status"] / "keys.json").read_text())
    keys["keys"].append(release.parse_key_line(line).public())
    (env["status"] / "keys.json").write_text(json.dumps(keys))
    r = c.post("/api/admin/update/upload", auth=("admin", "geheim"),
               files={"bundle": ("neu.zip", z2.read_bytes()), "sig": ("neu.zip.sig", release.sign(z2, pk))})
    assert r.status_code == 200, r.text
    assert c.post("/api/admin/update/start", auth=("admin", "geheim"), json={"sha": r.json()["sha"]}).json()["started"]
    # Statusanzeige (am Pi, ohne Anmeldung): Update läuft
    sp = ctx.new_page()
    serrs = _watch(sp)
    sp.goto(env["base"] + "/status.html")
    sp.wait_for_function("() => document.getElementById('detail').textContent.includes('Update auf 99.0.1')", timeout=15000)
    assert "Pi A ist dran (0 von 1 fertig)" in sp.inner_text("#detail")
    _answer(env, ok=False, after=CUR, code="update_failed", msg="FEHLER: Server meldet nach 90 s nicht die Version")
    sp.wait_for_function("() => document.getElementById('detail').textContent.includes('fehlgeschlagen')", timeout=15000)
    assert "bad" in sp.get_attribute("#pill", "class")
    assert f"Update auf 99.0.1: Pi A fehlgeschlagen (läuft wieder mit {CUR})" in sp.inner_text("#detail")
    assert not serrs, serrs
    page.wait_for_selector("#upd-retry", timeout=15000)
    txt = page.inner_text("#upd-status")
    assert "Angehalten" in txt and "FEHLER: Server meldet" in txt and "dieser Pi läuft wieder" in txt
    page.click(".upd-nodes details summary")
    assert "Sichere Version" in page.inner_text(".upd-nodes details pre")
    _shot(page, "update-failed.png", full_page=True)
    page.on("dialog", lambda d: d.accept())
    page.click("#upd-cancel")
    page.wait_for_function("() => document.querySelector('#upd-plan').textContent.includes('Abgebrochen')", timeout=10000)
    assert page.locator("#upd-retry").count() == 0
    assert not errs, errs
    ctx.close()


def test_phone_width_has_no_overflow(env, browser):  # noqa: F811
    ctx, page, errs = _admin(browser, env["base"], width=390)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    _shot(page, "update-phone.png", full_page=True)
    assert not errs, errs
    ctx.close()


def _shot(page, name: str, **kw) -> None:
    import os
    if os.environ.get("FESTPOS_SHOTS"):
        page.screenshot(path=str(Path(os.environ["FESTPOS_SHOTS"]) / name), **kw)
