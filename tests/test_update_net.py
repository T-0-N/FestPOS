"""Update aus dem Internet (app/update_net.py): Einstellungen (Token/WLAN-Passwort nie zurück), neusten GitHub-Release
suchen und herunterladen – mit einem nachgebauten GitHub (Weiterleitung zu den Dateien wie bei GitHub) und dem
root-Helfer als Attrappe (beantwortet net_up/net_down). Danach liegt das Paket wie nach dem Hochladen bereit."""
from __future__ import annotations

import http.server
import json
import threading
import time

import pytest

from app import release
from tests.release_util import build_bundle, keypair
from tests.test_api import AUTH
from tests.test_update_app import NEW, upd  # noqa: F401  (upd = Fixture)

TOKEN = "github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz"


class FakeGitHub:
    """api.github.com im Kleinen: /repos/o/r, /releases/latest, Dateien über die API -> 302 auf einen anderen Host."""

    def __init__(self):
        self.release: dict | None = None
        self.files: dict[str, bytes] = {}
        self.private = False
        self.seen: list[tuple[str, str | None]] = []           # (Pfad, Authorization)
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", ctype="application/json", headers=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                auth = self.headers.get("Authorization")
                fake.seen.append((self.path, auth))
                if self.path.startswith("/dl/"):                    # «anderer Server» (Speicher hinter GitHub)
                    data = fake.files.get(self.path[4:])
                    return self._send(200, data, "application/octet-stream") if data else self._send(404)
                if fake.private and auth != f"Bearer {TOKEN}":
                    return self._send(404 if auth is None else 401, b"{}")
                if self.path == "/repos/o/r":
                    return self._send(200, b'{"full_name": "o/r"}')
                if self.path == "/repos/o/r/releases/latest":
                    if fake.release is None:
                        return self._send(404, b"{}")
                    return self._send(200, json.dumps(fake.release).encode())
                if self.path.startswith("/repos/o/r/releases/assets/"):
                    name = self.path.rsplit("/", 1)[1]
                    assert self.headers.get("Accept") == "application/octet-stream"
                    return self._send(302, headers={"Location": f"http://localhost:{fake.port}/dl/{name}"})
                return self._send(404, b"{}")

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def publish(self, tag: str, zip_data: bytes, sig: bytes, name="Herbstfest"):
        self.files = {"zip": zip_data, "sig": sig}
        self.release = {"tag_name": tag, "name": name, "published_at": "2026-10-19T10:00:00Z", "assets": [
            {"name": "festpos.zip", "size": len(zip_data), "url": f"{self.base}/repos/o/r/releases/assets/zip"},
            {"name": "festpos.zip.sig", "size": len(sig), "url": f"{self.base}/repos/o/r/releases/assets/sig"}]}


class FakeHelper:
    """festpos-update: beantwortet net_up / net_down (code: Fehler für net_up)."""

    def __init__(self, u):
        self.u, self.reqs, self.code, self.stop = u, [], None, False
        self.t = threading.Thread(target=self.loop, daemon=True)
        self.t.start()

    def loop(self):
        while not self.stop:
            for f in list(self.u["req"].iterdir()):
                req = json.loads(f.read_text())
                f.unlink()
                if not req.get("action", "").startswith("net_"):
                    continue
                self.reqs.append(req)
                ok = req["action"] == "net_down" or not self.code
                out = {"id": req["id"], "ok": ok, "code": None if ok else self.code, "kw": {} if ok else {"name": "x"},
                       "iface": "wlan0" if ok else ""}
                (self.u["status"] / f"{req['id']}.json").write_text(json.dumps(out))
            time.sleep(0.05)

    def actions(self):
        return [r["action"] for r in self.reqs]


@pytest.fixture
def net(upd, monkeypatch):  # noqa: F811
    import sys
    m = sys.modules["app.update_net"]
    gh = FakeGitHub()
    monkeypatch.setattr(m, "API", gh.base)
    monkeypatch.setattr(m, "NET_WAIT", 10)
    sysnet = upd["tmp"] / "sysnet"
    (sysnet / "wlan0" / "wireless").mkdir(parents=True)
    monkeypatch.setattr(m, "SYS_NET", sysnet)
    h = FakeHelper(upd)
    yield {**upd, "net": m, "gh": gh, "helper": h}
    h.stop = True
    gh.srv.shutdown()


def configure(u, **kw):
    body = {"repo": "o/r", "via": "wlan", "ssid": "Fest-Hotspot", "psk": "geheim-wlan-123", **kw}
    return u["c"].put("/api/admin/update/net", auth=AUTH, json=body)


def wait_job(u, timeout=20) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        v = u["c"].get("/api/admin/update/net", auth=AUTH).json()
        if not v["busy"] and v["job"] and v["job"]["state"] in ("done", "failed"):
            return v
        time.sleep(0.1)
    raise AssertionError("Vorgang endet nicht")


def signed(u, version=NEW, pk=None) -> tuple[bytes, bytes]:
    z = build_bundle(u["tmp"] / f"gh-{version}.zip", version)
    return z.read_bytes(), release.sign(z, pk or u["pk"])


def test_settings_never_return_secrets(net):
    u = net
    r = configure(u, token=TOKEN)
    assert r.status_code == 200, r.text
    c = r.json()["config"]
    assert c["repo"] == "o/r" and c["via"] == "wlan" and c["ssid"] == "Fest-Hotspot" and c["psk_set"]
    assert c["token"] == "github_pat_11AB…xyz" and TOKEN not in r.text and "geheim-wlan-123" not in r.text
    assert r.json()["devices"]["wlan"] == ["wlan0"]
    s = u["c"].get("/api/admin/settings", auth=AUTH).text
    assert TOKEN not in s and "geheim-wlan-123" not in s and "update_net" not in s
    # leer = unverändert; löschen nur ausdrücklich
    c = configure(u, psk=None, repo="https://github.com/o/r.git").json()["config"]
    assert c["repo"] == "o/r" and c["token"] and c["psk_set"]
    assert not configure(u, clear_token=True).json()["config"]["token"]
    log = u["c"].get("/api/admin/audit", auth=AUTH).text
    assert TOKEN not in log and "geheim-wlan-123" not in log
    for bad in ({"repo": "nur-name"}, {"via": "lan", "gateway": "x", "dns": "1.1.1.1"}, {"psk": "kurz"},
                {"token": "falsch!"}, {"via": "funk"}):
        assert configure(u, **bad).status_code == 422, bad


def test_check_finds_newer_release_and_switches_internet_off(net):
    u, gh, h = net, net["gh"], net["helper"]
    configure(u)
    gh.publish(f"v{NEW}", *signed(u))
    assert u["c"].post("/api/admin/update/net/check", auth=AUTH).status_code == 200
    v = wait_job(u)
    assert v["job"]["state"] == "done", v
    f = v["job"]["found"]
    assert f["version"] == NEW and f["newer"] and f["name"] == "Herbstfest" and "zip" not in f
    assert h.actions() == ["net_up", "net_down"]
    up = h.reqs[0]
    assert up["via"] == "wlan" and up["ssid"] == "Fest-Hotspot" and up["psk"] == "geheim-wlan-123"
    assert not any(p.startswith("/dl/") for p, _ in gh.seen)                    # suchen lädt nichts herunter


def test_download_private_repo_verifies_and_offers_bundle(net):
    u, gh, h = net, net["gh"], net["helper"]
    gh.private = True
    configure(u, token=TOKEN, via="usb")
    gh.publish(NEW, *signed(u))
    assert u["c"].post("/api/admin/update/net/download", auth=AUTH).status_code == 200
    v = wait_job(u)
    assert v["job"]["state"] == "done" and v["job"]["version"] == NEW, v
    assert h.actions() == ["net_up", "net_down"] and h.reqs[0]["via"] == "usb" and "psk" not in h.reqs[0]
    # Token nur an die API, nie an den Server hinter der Weiterleitung
    assert all(a == f"Bearer {TOKEN}" for p, a in gh.seen if p.startswith("/repos/"))
    assert [a for p, a in gh.seen if p.startswith("/dl/")] == [None, None]
    up = u["c"].get("/api/admin/update", auth=AUTH).json()["upload"]
    assert up["version"] == NEW and up["source"] == f"o/r {NEW}" and up["by"] == "admin"
    assert (u["pakete"] / f"{up['sha']}.zip").is_file()
    assert not list(u["pakete"].glob("*.part"))


@pytest.mark.parametrize("case,code", [
    ("no_token", "gh_not_found"), ("bad_token", "gh_auth"), ("no_release", "gh_no_release"),
    ("no_assets", "gh_no_assets"), ("foreign_key", "unknown_key"), ("old", "not_newer"), ("helper", "net_wlan")])
def test_failures_are_reported_and_internet_goes_off(net, case, code):
    u, gh, h = net, net["gh"], net["helper"]
    configure(u, **({"token": "ghp_" + "x" * 36} if case == "bad_token" else {}))
    gh.publish(NEW, *signed(u, pk=keypair("Fremd")[0] if case == "foreign_key" else None))
    if case in ("no_token", "bad_token"):
        gh.private = True
    if case == "no_release":
        gh.release = None
    if case == "no_assets":
        gh.release["assets"] = gh.release["assets"][:1]
    if case == "old":
        gh.publish("2020.1.1-1", *signed(u, "2020.1.1-1"))
    if case == "helper":
        h.code = "net_wlan"
    assert u["c"].post("/api/admin/update/net/download", auth=AUTH).status_code == 200
    v = wait_job(u)
    assert v["job"]["state"] == "failed" and v["job"]["code"] == code, v
    assert v["job"]["text"] and "{" not in v["job"]["text"]
    assert h.actions() == (["net_up"] if case == "helper" else ["net_up", "net_down"])
    if case == "helper":
        assert gh.seen == []                                   # ohne Internet nichts versucht
    assert u["c"].get("/api/admin/update", auth=AUTH).json()["upload"] is None
    assert not list(u["pakete"].iterdir())


def test_project_repo_by_default_and_one_job_at_a_time(net):
    u, gh = net, net["gh"]
    v = u["c"].get("/api/admin/update/net", auth=AUTH).json()
    assert v["config"]["repo"] == "T-0-N/FestPOS" == v["config"]["default_repo"]       # ohne Eintrag: das Projekt
    assert configure(u, repo="kein repo").status_code == 422
    configure(u)
    gh.publish(NEW, *signed(u))
    net["net"]._task = None
    gate = threading.Event()
    orig = net["net"].latest
    net["net"].latest = lambda *a: (gate.wait(5), orig(*a))[1]
    try:
        assert u["c"].post("/api/admin/update/net/check", auth=AUTH).status_code == 200
        assert u["c"].post("/api/admin/update/net/download", auth=AUTH).status_code == 409
    finally:
        gate.set()
        wait_job(u)
        net["net"].latest = orig


def _keep(u, on=True):
    r = u["c"].put("/api/admin/settings", auth=AUTH, json={"net_keep": on})
    assert r.status_code == 200, r.text


def test_keep_connected_through_own_device_or_another_pi(net, monkeypatch):
    """Seite «Internet», Schalter (c): jeder Pi hält die Verbindung (ohne Zeitgeber). Mit dem Gerät hier: verbinden und
    weitergeben; ohne: über einen Pi, der weitergibt; aus: abbauen. Updates und Ausrollen stören sie nicht."""
    import sys
    from app import core, system
    u, m = net, net["net"]
    # Der Monitor der Test-App ruft keep_tick selbst auf (einmal pro Minute, das erste Mal gleich beim Start). Lief das
    # zwischen zwei Schritten dieses Tests, war der Stand schon «up» und der Test sah den alten Auftrag (CI, PR #48).
    monkeypatch.setattr(sys.modules["app.main"], "_keep_last", [time.time() + 3600])
    configure(u, via="wlan")
    m._keep.clear()
    m.keep_tick()
    assert u["helper"].reqs == []                                         # (c) aus: nichts
    _keep(u)
    m.keep_tick()
    up = u["helper"].reqs[-1]
    assert up["action"] == "net_up" and up["keep"] is True and up["share"] is True and up["via"] == "wlan"
    assert m.keep_view()["state"] == "up" and system.node_report()["internet"]["share"] is True
    # Update aus dem Internet: nimmt die bestehende Verbindung, baut sie nicht ab
    n = len(u["helper"].reqs)
    m._net_up(m._raw(), "x")
    m._net_down()
    assert len(u["helper"].reqs) == n
    # ein Pi ohne WLAN: über den Pi, der weitergibt
    m._keep.clear()
    monkeypatch.setattr(m, "SYS_NET", u["tmp"] / "leer")
    monkeypatch.setattr(core.rt.monitor, "peers", {"b": {"at": time.time(), "data": {"internet": {
        "up": True, "share": True, "device": True, "ip": "192.168.55.12"}}}}, raising=False)
    m.keep_tick()
    up = u["helper"].reqs[-1]
    assert up["via"] == "lan" and up["gateway"] == "192.168.55.12" and up["keep"] is True and "share" not in up
    assert m.keep_view()["peer"] == "b"
    # kein Pi gibt weiter: «kein Weg», kein Auftrag
    m._keep.clear()
    monkeypatch.setattr(core.rt.monitor, "peers", {}, raising=False)
    n = len(u["helper"].reqs)
    m.keep_tick()
    assert len(u["helper"].reqs) == n and m.keep_view()["state"] == "no_way"
    # (c) aus: Verbindung abbauen
    m._keep.update(state="up")
    _keep(u, False)
    m.keep_tick()
    assert u["helper"].reqs[-1]["action"] == "net_down" and m.keep_view()["state"] is None
    v = u["c"].get("/api/admin/update/net", auth=AUTH).json()
    assert v["keep"]["on"] is False


def test_keep_shares_only_to_handhelds_and_pis(net, monkeypatch):
    """die Handhelds gehen über die VIP ins Internet. Deren Pi gibt weiter – auch ohne eigenes Gerät, dann
    über den Pi mit dem WLAN – und nur an die eingetragenen Handhelds und die Pi. Neues Handheld: Liste nachführen;
    beim Ausrollen kurz ans ganze Netz."""
    import sys
    from app import core, dhcp, fully
    u, m = net, net["net"]
    monkeypatch.setattr(sys.modules["app.main"], "_keep_last", [time.time() + 3600])
    monkeypatch.setattr(core.Config, "peers", ["https://192.168.55.11:8443", "https://192.168.55.12:8443"])
    phones = [{"kind": "phone", "ip": "192.168.55.101"}, {"kind": "phone", "ip": "192.168.55.102", "active": False},
              {"kind": "printer", "ip": "192.168.55.50"}]
    monkeypatch.setattr(dhcp, "handheld_ips", lambda: sorted(d["ip"] for d in phones
                                                             if d["kind"] == "phone" and d.get("active", True)))
    monkeypatch.setattr(m, "_holds_vip", lambda: True)
    configure(u, via="wlan")
    _keep(u)
    m._keep.clear()
    m.keep_tick()
    up = u["helper"].reqs[-1]
    assert up["action"] == "net_up" and up["share"] is True
    assert up["allow"] == ["192.168.55.10", "192.168.55.11", "192.168.55.12", "192.168.55.101"]
    n = len(u["helper"].reqs)
    m.keep_tick()                                                         # nichts geändert: kein Auftrag
    assert len(u["helper"].reqs) == n
    phones.append({"kind": "phone", "ip": "192.168.55.103"})              # neues Handheld eingetragen
    m.keep_tick()
    sh = u["helper"].reqs[-1]
    assert sh["action"] == "net_share" and "192.168.55.103" in sh["allow"] and "off" not in sh
    fully._share.update(on=True)                                           # Ausrollen (Schalter b): ganzes Netz
    try:
        m.keep_tick()
        assert u["helper"].reqs[-1]["action"] == "net_share" and "allow" not in u["helper"].reqs[-1]
    finally:
        fully._share.clear()
    m.keep_tick()
    assert "192.168.55.103" in u["helper"].reqs[-1]["allow"]              # fertig: wieder nur die Liste
    # VIP-Pi ohne WLAN: verbindet sich über den Pi mit dem Gerät und reicht die Handhelds an ihn weiter
    m._keep.clear()
    monkeypatch.setattr(m, "SYS_NET", u["tmp"] / "leer")
    monkeypatch.setattr(core.rt.monitor, "peers", {"b": {"at": time.time(), "data": {"internet": {
        "up": True, "share": True, "device": True, "ip": "192.168.55.12"}}}}, raising=False)
    m.keep_tick()
    up = u["helper"].reqs[-1]
    assert up["via"] == "lan" and up["gateway"] == "192.168.55.12" and up["share"] is True and up["allow"]
    # die VIP wandert weg: nicht mehr weitergeben
    monkeypatch.setattr(m, "_holds_vip", lambda: False)
    m.keep_tick()
    assert u["helper"].reqs[-1]["action"] == "net_share" and u["helper"].reqs[-1]["off"] is True
    assert m.keep_report()["share"] is False
    _keep(u, False)
    m.keep_tick()


def test_internet_status_for_display_and_handhelds(net, monkeypatch):
    """Statusanzeige bekommt pro Pi den Stand der dauerhaften Verbindung; die Bestell-App meldet «Internet
    ohne Verbindung», wenn WLAN/USB des Pi weg sind und Karte/TWINT oder QR-Bestellung es brauchen."""
    import sys
    from app import status, system
    u, m = net, net["net"]
    monkeypatch.setattr(sys.modules["app.main"], "_keep_last", [time.time() + 3600])
    configure(u, via="wlan")
    assert m.keep_status() is None and status.internet_view([]) is None             # (c) aus: keine Zeile
    _keep(u)
    try:
        m._keep.clear()
        m._keep.update(state="up", via="wlan", online=True, device=True, share=True, since=1)
        assert system.local_info()["net"]["online"] is True and system.local_info()["net"]["device"] is True
        v = status.internet_view([{"id": "a", "reachable": True, "info": {"net": m.keep_status()}},
                                  {"id": "b", "reachable": False, "info": {"net": {"state": "up"}}}])
        assert v["via"] == "wlan" and [p["id"] for p in v["pis"]] == ["a"] and v["needed"] is False
        phone = lambda: u["c"].get("/api/phone/status").json()["uplink"]  # noqa: E731
        assert phone() is None
        m._keep.update(online=False)
        assert phone() is None                                                     # nicht gebraucht: keine Meldung
        monkeypatch.setattr(status, "internet_needed", lambda: True)
        assert phone() == {"kind": "internet"}
        m._keep.update(state="failed")
        assert phone() == {"kind": "internet"}
        m._keep.update(state="up", online=True)
        assert phone() is None
    finally:
        m._keep.clear()
        _keep(u, False)


# -- Stabil halten (T-0-N/FestPOS#1): übernehmen statt abbauen, Fehlversuche mit Pause, Grund ----------------------
def _status_file(u, data: dict) -> None:
    (u["status"] / "internet.json").write_text(json.dumps(data))


def test_keep_adopts_running_connection_after_restart(net, monkeypatch):
    """Neustart der App (auch beim Update): die dauerhafte Verbindung steht noch -> übernehmen, nicht abbauen."""
    import sys
    u, m = net, net["net"]
    monkeypatch.setattr(sys.modules["app.main"], "_keep_last", [time.time() + 3600])
    configure(u, via="wlan")
    _keep(u)
    try:
        _status_file(u, {"via": "wlan", "dev": "wlan0", "keep": True, "at": 1700000000, "share": True,
                         "ssid": "Fest-Hotspot"})
        m._keep.clear()
        n = len(u["helper"].reqs)
        m.keep_tick()
        assert u["helper"].actions()[n:] in ([], ["net_share"])            # kein net_up, kein net_down
        v = m.keep_view()
        assert v["state"] == "up" and v["since"] == 1700000000 and m._keep.get("adopted") is True
        # anderes WLAN eingestellt: nicht übernehmen
        _status_file(u, {"via": "wlan", "dev": "wlan0", "keep": True, "at": 1, "ssid": "Alter-Hotspot"})
        m._keep.clear()
        assert m._adopt(m._raw(), time.time()) is False
        # anderer Weg eingestellt (USB statt WLAN): nicht übernehmen, neu verbinden
        _status_file(u, {"via": "wlan", "dev": "wlan0", "keep": True, "at": 1})
        configure(u, via="usb")
        m._keep.clear()
        assert m._adopt(m._raw(), time.time()) is False
        # nur kurz verbunden (Update aus dem Internet, ohne keep): nicht übernehmen
        configure(u, via="wlan")
        _status_file(u, {"via": "wlan", "dev": "wlan0", "keep": False})
        assert m._adopt(m._raw(), time.time()) is False
        # (c) aus, aber der Helfer hält noch eine dauerhafte Verbindung (aus vor dem Neustart): abbauen
        _status_file(u, {"via": "wlan", "dev": "wlan0", "keep": True})
        _keep(u, False)
        m._keep.clear()
        m.keep_tick()
        assert u["helper"].reqs[-1]["action"] == "net_down"
    finally:
        m._keep.clear()
        (u["status"] / "internet.json").unlink(missing_ok=True)
        _keep(u, False)


def test_keep_reconnects_only_after_several_failures_with_growing_pause(net, monkeypatch):
    """Schwaches Netz: nicht bei jeder gescheiterten Prüfung neu verbinden, sondern erst nach KEEP_FAILS, dann mit
    Pause 1, 2, 4 … 15 Minuten. Der Grund steht dabei (kein DNS, Uhrzeit, keine Antwort, WLAN nicht gefunden)."""
    import sys
    u, m = net, net["net"]
    monkeypatch.setattr(sys.modules["app.main"], "_keep_last", [time.time() + 3600])
    configure(u, via="wlan")
    _keep(u)
    clock = [time.time()]
    monkeypatch.setattr(m.time, "time", lambda: clock[0])
    try:
        m._keep.clear()
        m.keep_tick()
        assert u["helper"].reqs[-1]["action"] == "net_up" and m.keep_view()["state"] == "up"
        monkeypatch.setattr(m, "_probe", lambda timeout=5.0: "dns")
        n = len(u["helper"].reqs)
        for i in range(1, m.KEEP_FAILS):
            clock[0] += 60
            m.keep_tick()
            assert len(u["helper"].reqs) == n, i                           # noch nicht neu verbinden
            v = m.keep_view()
            assert v["online"] is False and v["reason"] == "dns" and v["fails"] == i
        clock[0] += 60
        m.keep_tick()                                                      # 3. Mal: neu verbinden
        assert u["helper"].reqs[-1]["action"] == "net_up" and len(u["helper"].reqs) == n + 1
        assert m.keep_view()["reason"] == "dns" and m.keep_view()["tries"] == 1
        for _ in range(m.KEEP_FAILS):                                      # weiter offline, Pause noch nicht um
            clock[0] += 15
            m.keep_tick()
        assert len(u["helper"].reqs) == n + 1
        # Verbinden scheitert: Grund und wachsende Pause
        u["helper"].code = "net_wlan_not_found"
        clock[0] += 60
        m.keep_tick()
        v = m.keep_view()
        assert v["state"] == "failed" and v["reason"] == "wlan_not_found" and v["tries"] == 2
        assert v["next"] - clock[0] == m.BACKOFF[1]
        clock[0] += m.BACKOFF[1] - 1
        k = len(u["helper"].reqs)
        m.keep_tick()
        assert len(u["helper"].reqs) == k                                  # Pause noch nicht um
        clock[0] += 2
        m.keep_tick()
        assert m.keep_view()["tries"] == 3 and m.keep_view()["next"] - clock[0] == m.BACKOFF[2]
        # wieder da: alles zurück
        u["helper"].code = None
        monkeypatch.setattr(m, "_probe", lambda timeout=5.0: None)
        clock[0] += m.BACKOFF[2] + 1
        m.keep_tick()
        v = m.keep_view()
        assert v["state"] == "up" and v["online"] is True and v["reason"] is None and v["tries"] == 0
        assert m.keep_status()["reason"] is None
    finally:
        m._keep.clear()
        _keep(u, False)


def test_keep_wlan_only_one_pi_at_the_hotspot(net, monkeypatch):
    """3 Pi mit WLAN: nur der Pi mit der VIP verbindet sich mit dem Hotspot, die anderen gehen über ihn."""
    import sys
    from app import core
    u, m = net, net["net"]
    monkeypatch.setattr(sys.modules["app.main"], "_keep_last", [time.time() + 3600])
    monkeypatch.setattr(core.Config, "peers", ["https://192.168.55.12:8443"])
    monkeypatch.setattr(m, "_holds_vip", lambda: False)
    monkeypatch.setattr(core.rt.monitor, "peers", {"b": {"at": time.time(), "data": {"internet": {
        "up": True, "share": True, "device": True, "ip": "192.168.55.12"}}}}, raising=False)
    configure(u, via="wlan")
    _keep(u)
    try:
        m._keep.clear()
        m.keep_tick()
        up = u["helper"].reqs[-1]
        assert up["via"] == "lan" and up["gateway"] == "192.168.55.12" and m.keep_view()["peer"] == "b"
        monkeypatch.setattr(core.rt.monitor, "peers", {}, raising=False)  # noch keiner verbunden: trotzdem nicht selbst
        m._keep.clear()
        n = len(u["helper"].reqs)
        m.keep_tick()
        assert len(u["helper"].reqs) == n and m.keep_view()["state"] == "no_way"
        monkeypatch.setattr(m, "_holds_vip", lambda: True)                # mit der VIP: selbst ans WLAN
        m._keep.clear()
        m.keep_tick()
        assert u["helper"].reqs[-1]["via"] == "wlan" and m.keep_report()["device"] is True
        # beide gleichzeitig gestartet: wer die VIP nicht (mehr) hat, überlässt das WLAN dem anderen
        monkeypatch.setattr(m, "_holds_vip", lambda: False)
        monkeypatch.setattr(core.rt.monitor, "peers", {
            "a": {"at": time.time(), "data": {"internet": {"up": True, "share": True, "device": False,   # reicht nur weiter
                                                           "ip": "192.168.55.11"}}},
            "a2": {"at": time.time(), "data": {"internet": {"up": True, "share": True,   # älter: ohne «device»
                                                            "ip": "192.168.55.13"}}},
            "b": {"at": time.time(), "data": {"internet": {"up": True, "share": True, "device": True,
                                                           "ip": "192.168.55.12"}}}}, raising=False)
        m.keep_tick()
        up = u["helper"].reqs[-1]
        assert up["action"] == "net_up" and up["via"] == "lan" and up["gateway"] == "192.168.55.12"   # nie A (Kreis)
    finally:
        m._keep.clear()
        _keep(u, False)


def test_probe_reasons(monkeypatch):
    """«Online» = DNS und HTTPS mit gültigem Zertifikat; sonst dns, time (Uhr falsch) oder https."""
    import socket
    import ssl
    from app import update_net as m
    monkeypatch.setenv("FESTPOS_HOST_CHECKS", "1")

    def no_dns(*a, **k):
        raise socket.gaierror("kein DNS")
    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    assert m._probe() == "dns"
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [("x",)])

    def refused(*a, **k):
        raise ConnectionRefusedError()
    monkeypatch.setattr(socket, "create_connection", refused)
    assert m._probe() == "https"

    class Raw:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def early(*a, **k):
        raise ssl.SSLCertVerificationError(1, "certificate verify failed: certificate is not yet valid")
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: Raw())
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", lambda self, raw, server_hostname=None: early())
    assert m._probe() == "time"
