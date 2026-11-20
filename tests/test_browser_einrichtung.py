"""Ersteinrichtung im Browser (T11): Code, «Neu einrichten» mit Standardwerten (Weiter, Weiter, Fertig), ca.key sichern,
Neustart-Knopf – am Handy 412 und 360 px ohne Überbreite. Der root-Helfer läuft hier als normaler Benutzer."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import pytest

pytest.importorskip("playwright.sync_api")
httpx = pytest.importorskip("httpx2")

from tests.conftest import free_port  # noqa: E402
from tests.test_browser_p4 import _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
WH = ROOT / "deploy" / "festpos-wiederherstellen"


@pytest.fixture
def server(tmp_path):
    boot, run, data = tmp_path / "boot", tmp_path / "run", tmp_path / "data"
    for d in (boot, run, data, tmp_path / "jobs", tmp_path / "neu", tmp_path / "out", tmp_path / "etc"):
        d.mkdir()
    (run / "einrichtung").touch()
    (data / "code").write_text("ABCD-EFGH\n")
    dirs = {"FESTPOS_WH_DIR": str(tmp_path / "jobs"), "FESTPOS_WH_NEU": str(tmp_path / "neu"),
            "FESTPOS_WH_ERGEBNIS": str(tmp_path / "out")}
    helper_env = {"PATH": "/usr/bin:/bin", "FESTPOS_RELEASE_LIB": str(ROOT), "FESTPOS_BOOT": str(boot),
                  "FESTPOS_ETC": str(tmp_path / "etc"), "FESTPOS_WH_TROCKEN": "1", "FESTPOS_DEST": str(ROOT),
                  "FESTPOS_EINRICHTUNG_RUN": str(run / "einrichtung"), "FESTPOS_EINRICHTUNG_DATA": str(data), **dirs}
    stop = threading.Event()

    def helper():
        while not stop.wait(0.2):
            if any((tmp_path / "jobs").iterdir()):
                subprocess.run([sys.executable, "-I", str(WH), "--anfrage"], env=helper_env, capture_output=True, timeout=120)
    threading.Thread(target=helper, daemon=True).start()
    port = free_port()
    env = {**os.environ, **dirs, "FESTPOS_STATIC": str(ROOT / "static"), "FESTPOS_EINRICHTUNG_CODE": str(data / "code"),
           "FESTPOS_EINRICHTUNG_STASH": str(tmp_path / "stash")}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.setup_mode:app", "--host", "127.0.0.1", "--port",
                             str(port)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(base + "/api/health", timeout=1).json().get("setup"):
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    yield base, boot
    stop.set()
    proc.terminate()
    proc.wait(10)


@pytest.mark.parametrize("width", [412, 360])
def test_weiter_weiter_fertig(server, browser, width):  # noqa: F811
    base, boot = server
    ctx = browser.new_context(viewport={"width": width, "height": 892}, accept_downloads=True)
    page = ctx.new_page()
    errs = _watch(page)
    page.on("dialog", lambda d: d.accept())
    page.goto(base + "/admin.html")
    page.wait_for_selector("#s-code")
    assert "noch nicht eingerichtet" in page.inner_text("h1")
    page.fill("#s-code", "falsch")
    page.click("#s-next")
    page.wait_for_selector("#s-err")
    page.fill("#s-code", "abcd efgh")
    page.click("#s-next")
    page.wait_for_selector("#s-way")
    assert page.is_checked('#s-way input[value="neu"]')                   # Vorwahl: neu einrichten
    page.click("#s-next")
    page.wait_for_selector("#s-pw")
    assert page.is_disabled("#s-next")
    page.fill("#s-pw", "Theater-2026!")
    page.fill("#s-pw2", "Theater-2026!")
    for _ in range(4):                                                    # Zeit, Netz, RTC, Zusammenfassung
        page.click("#s-next")
        page.wait_for_timeout(100)
        assert page.locator(".btn.pri").count() == 1                      # ein grüner Hauptknopf
        assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert "192.168.55.10" in page.inner_text("#swiz")
    page.click("#s-next")                                                 # Einrichten
    page.wait_for_selector("#s-done", timeout=30000)
    assert (boot / "festpos.conf").exists() and (boot / "node.conf").read_text() == "NODE=a\n"
    assert page.is_disabled("#s-restart")                                 # erst wenn ca.key gesichert ist
    with page.expect_download() as dl:
        page.click("#s-key")
    assert dl.value.suggested_filename == "festpos-ca.key"
    page.check("#s-keyok")
    assert not page.is_disabled("#s-restart")
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    _until(page, "!!document.getElementById('s-restart')")
    ctx.close()
