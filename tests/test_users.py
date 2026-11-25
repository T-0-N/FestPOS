"""Version 2026.10.10-13: Benutzer und Rollen der Verwaltung (Anzeige / Festleitung / Admin), Notfall-Login,
Herunterfahren / Neu starten der Pi (einzeln oder alle) und der Handys über Fully."""
from __future__ import annotations

import json
import time

import pytest

from tests.test_api import AUTH

PW = "sicher-123"


def _login(c, name, pw=PW):
    return c.post("/api/admin/login", json={"name": name, "password": pw})


def _hdr(c, name, pw=PW, confirmed=True):
    """Token des Benutzers – bestätigt (kritische Aktionen, T-0-N/FestPOS#9), wie nach «Bitte bestätigen»."""
    r = _login(c, name, pw)
    assert r.status_code == 200, r.text
    hdr = {"Authorization": f"Bearer {r.json()['token']}"}
    if confirmed:
        r = c.post("/api/admin/me/confirm", headers=hdr, json={"password": pw})
        assert r.status_code == 200, r.text
        hdr = {"Authorization": f"Bearer {r.json()['token']}"}
    return hdr


@pytest.fixture
def team(client):
    from app import core
    core.login_throttle.clear()
    for name, role in (("Vera", "view"), ("Lea", "lead"), ("Ada", "admin")):
        r = client.post("/api/admin/users", auth=AUTH, json={"name": name, "role": role, "password": PW})
        assert r.status_code == 200, r.text
    return {n: _hdr(client, n) for n in ("Vera", "Lea", "Ada")}


# ------------------------------------------------------------------ Regeln
def test_every_rule_matches_a_route_and_defaults_are_safe():
    from app import auth, main
    paths = main.app.openapi()["paths"]                              # alle Routen, auch aus eingebundenen Routern
    routes = [(m.upper(), p) for p, ops in paths.items() for m in ops if p.startswith(("/api/admin", "/api/status", "/status-einfach"))]
    assert len(routes) > 60
    import re
    concrete = [(m, re.sub(r"\{[^}]+\}", "7", p)) for m, p in routes]
    for m, rx, _ in (*auth.RULES, *auth.CRITICAL_RULES):            # keine Regel ohne Route (Tippfehler)
        assert any(m == m2 and rx.match(p) for m2, p in concrete), f"Regel ohne Route: {m} {rx.pattern}"
    roles = {(m, p): auth.required_role(m, p) for m, p in concrete}
    # Anzeige darf fast nur lesen
    view_writes = {(m, p) for (m, p), r in roles.items() if r == "view" and m != "GET"}
    assert view_writes == {("POST", "/api/admin/me/password"), ("POST", "/api/admin/me/logout-all"),
                           ("POST", "/api/admin/me/refresh"), ("POST", "/api/admin/me/confirm"),
                           *(("POST", f"/api/admin/me/totp/{x}") for x in ("start", "confirm", "later", "off", "codes")),
                           ("POST", "/api/admin/jobs/7/reprint"), ("POST", "/api/admin/jobs/7/resolve")}
    for m, p in concrete:                                            # sicherheitsrelevant: nur Admin
        if p.startswith(("/api/admin/users", "/api/admin/backup", "/api/admin/restore", "/api/admin/cluster/",
                         "/api/admin/reset-orders", "/api/admin/fully/forget", "/api/admin/fully/provision",
                         "/api/admin/fully/apk", "/api/admin/fully/template")) and not (m == "GET" and "/fully/" in p):
            assert roles[(m, p)] == "admin", (m, p)
    # Seite «Geräte» ab Festleitung (T5); löschen nur Admin
    assert roles[("GET", "/api/admin/devices")] == roles[("POST", "/api/admin/fully/cert")] == "lead"
    assert roles[("DELETE", "/api/admin/devices/7")] == "admin"
    assert auth.required_role("GET", "/api/admin/neu-und-unbekannt") == "admin"
    assert auth.required_role("HEAD", "/api/admin/stats") == "view"


# ------------------------------------------------------------------ Anmelden, Rollen
def test_login_and_role_limits(client, team):
    from app import core
    v, l_, a = team["Vera"], team["Lea"], team["Ada"]
    me = client.get("/api/admin/me", headers=l_).json()
    from app import auth
    assert me == {"name": "Lea", "role": "lead", "emergency": False, "role_name": None, "perms": sorted(auth.PERMS), "cert": None,
                  "totp": {"on": False, "required": False, "later": 3, "blocked": False, "codes": 0,
                           "role_required": False}}
    assert _login(client, "lea").json()["role"] == "lead"                       # Name ohne Gross/Klein
    # Anzeige: Übersicht, Druckaufträge, Statusanzeige – sonst nichts
    assert client.get("/api/admin/stats", headers=v).status_code == 200
    assert client.get("/api/admin/jobs", headers=v).status_code == 200
    assert client.get("/api/status", headers=v).json()["me"] == {"name": "Vera", "role": "view", "perms": ["jobs", "orders"]}
    r = client.post("/api/admin/printers", headers=v, json={"name": "X", "host": "127.0.0.1", "port": 1})
    assert r.status_code == 403 and r.json()["detail"]
    assert client.post("/api/admin/system/power", headers=v, json={"action": "reboot"}).status_code == 403
    # Festleitung: Preisliste, Drucker, Tische, Testmodus, Geräte – keine Benutzer, Einstellungen
    assert client.post("/api/admin/printers", headers=l_, json={"name": "Bar", "host": "127.0.0.1", "port": 1}).status_code == 200
    assert client.post("/api/admin/tables", headers=l_, json={"range_from": 1, "range_to": 3}).status_code == 200
    assert client.get("/api/admin/devices", headers=l_).status_code == 200
    assert client.get("/api/admin/users", headers=l_).status_code == 403
    assert client.post("/api/admin/backup", headers=l_, json={"password": "x" * 12}).status_code == 403
    assert client.put("/api/admin/settings", headers=l_, json={"test_enabled": True, "test_pin": "2468"}).status_code == 200
    assert client.put("/api/admin/settings", headers=l_, json={"test_print": True, "event_name": "X"}).status_code == 403
    assert client.put("/api/admin/settings", headers=a, json={"event_name": "Fest"}).status_code == 200
    # ein Benutzer ist kein Notfall-Admin, und «admin» ist reserviert
    assert client.post("/api/admin/users", auth=AUTH, json={"name": "Admin", "role": "view", "password": PW}).status_code == 422
    assert client.post("/api/admin/users", auth=AUTH, json={"name": "lea", "role": "view", "password": PW}).status_code == 409
    assert client.post("/api/admin/users", auth=AUTH, json={"name": "Kurz", "role": "view", "password": "1234"}).status_code == 422
    # Notfall-Login wie bisher (leer oder «admin»), auch per Basic Auth; Benutzer auch per Basic Auth
    assert client.post("/api/admin/login", json={"password": "geheim"}).json()["emergency"] is True
    assert _login(client, "admin", "geheim").json()["role"] == "admin"
    assert client.get("/api/admin/users", auth=("Ada", PW)).status_code == 200
    assert client.get("/api/admin/users", auth=("Vera", PW)).status_code == 403
    assert _login(client, "Vera", "falsch").status_code == 401
    assert _login(client, "Niemand", PW).status_code == 401
    core.login_throttle.clear()
    # Passwort-Hash nie in der Antwort
    assert "pw_hash" not in json.dumps(client.get("/api/admin/users", headers=a).json())


def test_lock_out_and_password_changes_end_sessions(client, team):
    from app import auth, core
    v = team["Vera"]
    users = {u["name"]: u for u in client.get("/api/admin/users", auth=AUTH).json()["users"]}
    vid, aid = users["Vera"]["id"], users["Ada"]["id"]
    # sperren: offene Sitzung endet sofort (ohne als Rateversuch zu zählen)
    assert client.put(f"/api/admin/users/{vid}", auth=AUTH, json={"active": False}).json()["active"] is False
    auth.forget()
    for _ in range(core.LOGIN_MAX_FAILS + 2):
        assert client.get("/api/admin/stats", headers=v).status_code == 401
    assert client.get("/api/admin/stats", auth=AUTH).status_code == 200            # PC nicht gesperrt
    assert _login(client, "Vera").status_code == 401
    client.put(f"/api/admin/users/{vid}", auth=AUTH, json={"active": True})
    v = _hdr(client, "Vera")
    # eigenes Passwort ändern: altes muss stimmen, danach gilt die alte Sitzung nicht mehr
    assert client.post("/api/admin/me/password", headers=v, json={"old": "falsch", "new": "neues-pw-1"}).status_code == 422
    assert client.post("/api/admin/me/password", headers=v, json={"old": PW, "new": "neues-pw-1"}).status_code == 200
    auth.forget()
    assert client.get("/api/admin/stats", headers=v).status_code == 401
    assert _login(client, "Vera", "neues-pw-1").status_code == 200
    # Admin setzt ein Passwort neu, ändert die Rolle, löscht
    client.put(f"/api/admin/users/{vid}", auth=AUTH, json={"password": "zurueck-123", "role": "lead"})
    assert _login(client, "Vera", "zurueck-123").json()["role"] == "lead"
    # sich selbst nicht aussperren
    a = team["Ada"]
    assert client.put(f"/api/admin/users/{aid}", headers=a, json={"role": "view"}).status_code == 422
    assert client.delete(f"/api/admin/users/{aid}", headers=a).status_code == 422
    assert client.delete(f"/api/admin/users/{vid}", headers=a).json() == {"ok": True}
    assert client.delete(f"/api/admin/users/{vid}", headers=a).status_code == 404
    # Notfall-Login kann sein Passwort hier nicht ändern (steht in festpos.conf)
    assert client.post("/api/admin/me/password", auth=AUTH, json={"old": "geheim", "new": "anders-123"}).status_code == 422


def test_restore_ends_user_sessions(client, team):
    """Nach dem Einspielen einer Sicherung passt kein altes Token mehr – auch nicht auf eine andere Person
    mit derselben Nummer in der Sicherung."""
    backup = client.post("/api/admin/backup", auth=AUTH, json={"password": "Sicherung-Test-123"}).content
    vid = next(u["id"] for u in client.get("/api/admin/users", auth=AUTH).json()["users"] if u["name"] == "Vera")
    client.delete(f"/api/admin/users/{vid}", auth=AUTH)
    client.post("/api/admin/users", auth=AUTH, json={"name": "Max", "role": "view", "password": PW})
    max_hdr = _hdr(client, "Max")
    assert client.get("/api/admin/stats", headers=team["Lea"]).status_code == 200
    r = client.post("/api/admin/restore?confirm=JA", auth=AUTH, files={"file": ("s.festpos", backup)},
                    data={"password": "Sicherung-Test-123"})
    assert r.status_code == 200, r.text
    for hdr in (team["Lea"], team["Ada"], max_hdr):
        assert client.get("/api/admin/stats", headers=hdr).status_code == 401
    assert _login(client, "Max").status_code == 401                         # gibt es in der Sicherung nicht
    assert client.get("/api/admin/stats", headers=_hdr(client, "Vera")).status_code == 200
    assert client.get("/api/admin/stats", auth=AUTH).status_code == 200      # Notfall-Login bleibt


def test_password_hashing():
    from app import auth
    h = auth.hash_password("Grüezi-Mitenand")
    assert h.startswith("scrypt$") and "Grüezi" not in h
    assert auth.verify_password("Grüezi-Mitenand", h) and not auth.verify_password("grüezi-mitenand", h)
    assert not auth.verify_password("x", "kaputt") and not auth.verify_password("x", None)
    assert auth.hash_password("gleich") != auth.hash_password("gleich")             # mit Salz


# ------------------------------------------------------------------ Herunterfahren / Neu starten
def test_power_for_all_pis_and_phones(client, team, tmp_path, monkeypatch):
    from app import core, display
    from tests.test_fully import PASSWORD, FakeFully
    monkeypatch.setattr(display, "DISPLAY_FILE", str(tmp_path / "display.json"))
    l_ = team["Lea"]
    node = core.Config.node
    info = client.get("/api/admin/system", headers=team["Vera"]).json()
    assert [n["id"] for n in info["nodes"]] == [node] and info["phones_fully"] == 0
    # Handys mitnehmen geht nur mit eingerichtetem Fully
    r = client.post("/api/admin/system/power", headers=l_, json={"action": "reboot", "phones": "restart_app"})
    assert r.status_code == 422
    assert client.post("/api/admin/system/power", headers=l_, json={"action": "reboot", "node": "zz"}).status_code == 404
    # Fully für ein Handy einrichten (nachgebautes Fully)
    client.put("/api/admin/settings", auth=AUTH, json={"fully_password": PASSWORD, "monitor_devices": [
        {"name": "XCover 1", "kind": "phone", "ip": "127.0.0.1", "port": None}]})
    p12 = client.post("/api/admin/fully/cert", auth=AUTH, json={"ip": "127.0.0.1"}).content
    srv = FakeFully("https", p12)
    try:
        client.put("/api/admin/settings", auth=AUTH, json={"fully_port": srv.port})
        from app import system
        t0 = time.time()
        r = client.post("/api/admin/system/power", headers=l_, json={"action": "poweroff", "node": "all", "phones": "screen_off"})
        assert r.status_code == 200, r.text
        res = r.json()                                   # T27: zuerst nur die Meldung, die Handys erst kurz vor den Pi
        assert res["phones_at"] == res["at"] - system.PHONES_BEFORE and "screenOff" not in srv.commands
        assert t0 + 29 <= res["at"] <= time.time() + 30
        n = client.get("/api/phone/status").json()["notice"]
        assert n["action"] == "poweroff" and 25 <= n["left"] <= 30 and n["cancel"] is True
        assert client.get("/api/admin/lamp", headers=team["Vera"]).json()["notice"]["token"] == res["token"]
        assert client.post("/api/admin/system/power/cancel", headers=team["Vera"]).status_code == 403
        assert client.post("/api/admin/system/power/cancel", headers=l_).status_code == 200      # abgebrochen
        assert client.get("/api/phone/status").json()["notice"] is None
        assert json.loads((tmp_path / "display.json").read_text())["power"]["cancelled"] is True
        assert client.post("/api/admin/system/power/cancel", headers=l_).status_code == 409      # nichts mehr offen
        time.sleep(0.3)
        assert "screenOff" not in srv.commands                                                  # Handys nicht ausgeschaltet
        monkeypatch.setattr(system, "DELAY", 6)          # Handys 1 s nach dem Auftrag (PHONES_BEFORE = 5)
        r = client.post("/api/admin/system/power", headers=l_, json={"action": "poweroff", "node": "all", "phones": "shutdown"})
        assert r.status_code == 200, r.text
        res = r.json()
        assert res["nodes"] == [node]
        for _ in range(60):
            if "shutdownDevice" in srv.commands:
                break
            time.sleep(0.1)
        assert srv.commands[-1] == "shutdownDevice"     # Handy aus, kurz vor den Pi
        assert client.post("/api/admin/system/power/cancel", headers=l_).status_code == 409      # zu spät
        # einzelner Befehl aus dem Dashboard: App neu starten / Handy neu starten (Fully ohne Root lehnt ab)
        ok = client.post("/api/admin/fully/command", headers=l_, json={"ip": "127.0.0.1", "cmd": "restartApp"}).json()
        assert ok["state"] == "ok" and ok["name"] == "XCover 1"
        no = client.post("/api/admin/fully/command", headers=l_, json={"ip": "127.0.0.1", "cmd": "rebootDevice"}).json()
        assert no["state"] == "no_rights" and "device owner" in no["text"]
        srv.delay = 3.0                                  # Fully lässt sich Zeit mit der Absage: trotzdem die Absage zeigen
        slow = client.post("/api/admin/fully/command", headers=l_, json={"ip": "127.0.0.1", "cmd": "rebootDevice"}).json()
        srv.delay = 0.0
        assert slow["state"] == "no_rights"
        off = client.post("/api/admin/fully/command", headers=l_, json={"ip": "127.0.0.1", "cmd": "shutdownDevice"}).json()
        assert off["state"] == "sent"
        # Kiosk-Modus aus (entsperren) und wieder ein: Fully wird gleich danach neu abgefragt
        from app import fully
        monkeypatch.setattr(fully, "KIOSK_SETTLE", 0.05)
        for cmd, locked in (("unlockKiosk", False), ("lockKiosk", True)):
            k = client.post("/api/admin/fully/command", headers=l_, json={"ip": "127.0.0.1", "cmd": cmd}).json()
            assert k["state"] == "ok" and k["kiosk_locked"] is locked and srv.commands[-2:] == [cmd, "deviceInfo"]
            dev = client.get("/api/status", headers=l_).json()["devices"][0]
            assert dev["fully"]["data"]["kiosk_locked"] is locked
        assert client.post("/api/admin/fully/command", headers=team["Vera"], json={"ip": "127.0.0.1", "cmd": "unlockKiosk"}).status_code == 403
        assert client.post("/api/admin/fully/command", headers=l_, json={"ip": "127.0.0.1", "cmd": "loadUrl"}).status_code == 422
        assert client.post("/api/admin/fully/command", headers=team["Vera"], json={"ip": "127.0.0.1", "cmd": "restartApp"}).status_code == 403
        # Whitelist setzen: ab Festleitung wie die übrigen Fully-Befehle, nicht nur Admin
        srv.settings.update(websiteIntegration=True, urlWhitelist="*")
        w = client.post("/api/admin/fully/whitelist", headers=l_, json={"ip": "127.0.0.1"})
        assert w.status_code == 200 and w.json()["web"]["state"] == "ok", w.text
        assert client.post("/api/admin/fully/whitelist", headers=team["Vera"], json={"ip": "127.0.0.1"}).status_code == 403
    finally:
        srv.stop()
    # Auftrag für den root-Helfer: liegt sofort in display.json, auch ohne eingerichteten Bildschirm
    wish = json.loads((tmp_path / "display.json").read_text())
    assert wish["type"] is None and wish["power"]["action"] == "poweroff" and wish["power"]["at"] == res["at"]


def test_display_helper_powers_off_at_the_common_time(tmp_path, monkeypatch):
    from tests.test_stufe2 import _display_helper
    mod, calls = _display_helper(tmp_path, monkeypatch)
    slept = []
    monkeypatch.setattr(mod.time, "sleep", lambda s: slept.append(s))
    wish = tmp_path / "display.json"

    def run(action, token, at=None):
        wish.write_text(json.dumps({"type": None, "power": {"action": action, "token": token, "at": at or time.time()}}))
        calls.clear()
        mod.main()
        return [c for c in calls if c[:1] == ("systemctl",) and c[1] in ("reboot", "poweroff")]

    now = int(time.time())
    assert run("poweroff", f"{now}-0001") == []                          # neue Karte: nur merken
    assert run("poweroff", f"{now}-0002", time.time() + 12) == [("systemctl", "poweroff")]
    assert 10 < sum(slept) <= 12 and max(slept) <= 1                      # gemeinsamer Zeitpunkt, sekundenweise (T27)
    assert run("poweroff", f"{now}-0002") == []                           # derselbe Auftrag nicht nochmals
    assert run("reboot", f"{now}-0003") == [("systemctl", "reboot")]
    assert run("reboot", f"{now - 3600}-0004") == []                      # zu alt (z.B. aus einer Sicherung)
    assert run("halt", f"{now}-0005") == []                               # nur reboot / poweroff
    # T27: in der Verwaltung abgebrochen – während des Wartens (jede Sekunde nachsehen) oder schon vorher
    def cancel_after_first(s):
        slept.append(s)
        w = json.loads(wish.read_text())
        w["power"]["cancelled"] = True
        wish.write_text(json.dumps(w))
    monkeypatch.setattr(mod.time, "sleep", cancel_after_first)
    assert run("poweroff", f"{now}-0006", time.time() + 20) == []
    assert json.loads((tmp_path / "run" / "status.json").read_text()).get("powering_off") is False
    wish.write_text(json.dumps({"type": None, "power": {"action": "reboot", "token": f"{now}-0007",
                                                        "at": time.time() + 5, "cancelled": True}}))
    calls.clear()
    mod.main()
    assert not [c for c in calls if c[:2] == ("systemctl", "reboot")]


# ------------------------------------------------------------------ Rollen und Rechte (Punkt 6)
def test_custom_role_with_single_rights(client, team):
    """Eigene Rolle «Kasse» mit Bestellungen ändern und offenen Tischen: genau das, sonst nichts."""
    from app import auth
    v, a = team["Vera"], team["Ada"]
    d = client.get("/api/admin/users", headers=a).json()
    assert [r["id"] for r in d["roles"]] == ["view", "lead", "admin"] and d["perms"] == list(auth.PERMS)
    assert next(r for r in d["roles"] if r["id"] == "admin")["admin"] is True
    r = client.post("/api/admin/roles", headers=a, json={"name": "Kasse", "perms": ["orders", "orders_edit", "tabs"]})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert rid == "r1" and r.json()["perms"] == ["orders", "orders_edit", "tabs"]
    assert client.post("/api/admin/roles", headers=a, json={"name": "kasse", "perms": []}).status_code == 409   # gleicher Name
    assert client.post("/api/admin/roles", headers=a, json={"name": "Admin", "perms": []}).status_code == 409   # feste Rolle
    assert client.post("/api/admin/roles", headers=a, json={"name": "Chef", "perms": ["admin"]}).status_code == 422
    assert client.post("/api/admin/roles", headers=v, json={"name": "X", "perms": []}).status_code == 403         # nur Admin
    r2 = client.post("/api/admin/roles", headers=a, json={"name": "Storno", "perms": ["orders_edit"]}).json()
    assert r2["perms"] == ["orders", "orders_edit"]                    # ändern ohne ansehen nützt nichts: ansehen dazu
    assert client.delete(f"/api/admin/roles/{r2['id']}", headers=a).status_code == 200
    assert client.post("/api/admin/users", headers=a, json={"name": "Kai", "role": "r9", "password": PW}).status_code == 422
    assert client.post("/api/admin/users", headers=a, json={"name": "Kai", "role": rid, "password": PW}).status_code == 200
    auth.forget()
    k = _hdr(client, "Kai")
    me = client.get("/api/admin/me", headers=k).json()
    assert me["role"] == rid and me["role_name"] == "Kasse" and me["perms"] == ["orders", "orders_edit", "tabs"]
    assert _login(client, "Kai").json()["perms"] == ["orders", "orders_edit", "tabs"]
    assert client.get("/api/admin/tabs", headers=k).status_code == 200
    assert client.get("/api/admin/orders", headers=k).status_code == 200
    assert client.get("/api/admin/stats", headers=k).status_code == 200                      # Übersicht: jede Rolle
    assert client.get("/api/admin/jobs", headers=k).status_code == 403                       # Druckaufträge: nein
    assert client.post("/api/admin/tables", headers=k, json={"range_from": 1, "range_to": 2}).status_code == 403
    assert client.put("/api/admin/settings", headers=k, json={"test_enabled": True}).status_code == 403
    assert client.get("/api/admin/users", headers=k).status_code == 403
    # Rolle ändern: wirkt sofort (Sitzung bleibt), Rolle mit Benutzern lässt sich nicht löschen
    assert client.put(f"/api/admin/roles/{rid}", headers=a, json={"name": "Kasse", "perms": ["jobs"]}).status_code == 200
    auth.forget()
    assert client.get("/api/admin/jobs", headers=k).status_code == 200
    assert client.get("/api/admin/tabs", headers=k).status_code == 403
    assert client.delete(f"/api/admin/roles/{rid}", headers=a).status_code == 409
    uid = next(u["id"] for u in client.get("/api/admin/users", headers=a).json()["users"] if u["name"] == "Kai")
    assert client.put(f"/api/admin/users/{uid}", headers=a, json={"role": "view"}).status_code == 200
    assert client.delete(f"/api/admin/roles/{rid}", headers=a).status_code == 200
    assert client.delete(f"/api/admin/roles/{rid}", headers=a).status_code == 404
    log = client.get("/api/admin/audit?q=role_", headers=a).json()["entries"]
    assert {e["action"] for e in log} >= {"role_create", "role_update", "role_delete"}


def test_admin_role_and_last_admin_are_protected(client, team):
    """Aussperrschutz: feste Rollen lassen sich nicht ändern/löschen; der letzte aktive Admin bleibt; niemand ändert
    seine eigene Rolle; der Notfall-Login bleibt Admin."""
    a = team["Ada"]
    for rid in ("admin", "lead", "view"):
        assert client.put(f"/api/admin/roles/{rid}", headers=a, json={"name": "X", "perms": []}).status_code == 422
        assert client.delete(f"/api/admin/roles/{rid}", headers=a).status_code == 422
    users = {u["name"]: u for u in client.get("/api/admin/users", headers=a).json()["users"]}
    ada = users["Ada"]["id"]
    # Ada ist die einzige Admin-Benutzerin: weder herabstufen, sperren noch löschen (auch nicht per Notfall-Login)
    assert client.put(f"/api/admin/users/{ada}", auth=AUTH, json={"role": "lead"}).json()["detail"].startswith("Das ist der letzte")
    assert client.put(f"/api/admin/users/{ada}", auth=AUTH, json={"active": False}).status_code == 422
    assert client.delete(f"/api/admin/users/{ada}", auth=AUTH).status_code == 422
    assert client.put(f"/api/admin/users/{ada}", headers=a, json={"role": "lead"}).status_code == 422          # sich selbst
    # mit einem zweiten Admin geht es
    lea = users["Lea"]["id"]
    assert client.put(f"/api/admin/users/{lea}", auth=AUTH, json={"role": "admin"}).status_code == 200
    assert client.put(f"/api/admin/users/{ada}", auth=AUTH, json={"role": "lead"}).status_code == 200
    assert client.delete(f"/api/admin/users/{lea}", auth=AUTH).status_code == 422                            # jetzt Lea die letzte
    assert "admin" in _login(client, "admin", "geheim").json()["perms"]                                     # Notfall-Login


def test_roles_saved_at_the_same_time_do_not_overwrite(client, team):
    """Zwei Admins (auch auf zwei Pi) speichern Rollen zugleich: wer zuletzt kommt, bekommt 409 statt still zu
    überschreiben; der letzte Admin bleibt auch, wenn die Prüfung vorher noch zwei Admins sah (atomar im SQL)."""
    import pytest
    from fastapi import HTTPException
    from app import core, users
    a = team["Ada"]
    assert client.post("/api/admin/roles", headers=a, json={"name": "Bar", "perms": ["jobs"]}).status_code == 200
    before, roles = users._roles_now()
    core.rt.store.execute("UPDATE settings SET value=? WHERE key=?", (before.replace("Bar", "Bar2"), users.ROLES_KEY))
    with pytest.raises(HTTPException) as e:
        users._save_roles(before, roles + [{"id": "r9", "name": "X", "perms": []}], "test", "role_create")
    assert e.value.status_code == 409
    ada = next(u for u in client.get("/api/admin/users", headers=a).json()["users"] if u["name"] == "Ada")
    res = core.rt.store.execute(f"UPDATE users SET role='lead' WHERE id=? AND {users.NOT_LAST_ADMIN}", (ada["id"],))
    assert res.rows_affected == 0                                       # einzige Admin-Benutzerin bleibt Admin


def test_audit_only_with_the_change(client, team):
    """CodeRabbit zu PR 78: Änderung und Protokolleintrag zusammen – abgewiesen (letzter Admin) heisst auch kein Eintrag;
    gelungen heisst immer ein Eintrag (auch gegen rqlite: SQLite changes() im selben Batch)."""
    a = team["Ada"]
    users = {u["name"]: u for u in client.get("/api/admin/users", headers=a).json()["users"]}
    count = lambda: len(client.get("/api/admin/audit?q=user_update", headers=a).json()["entries"])  # noqa: E731
    n = count()
    assert client.put(f"/api/admin/users/{users['Ada']['id']}", auth=AUTH, json={"role": "lead"}).status_code == 422
    assert count() == n
    assert client.put(f"/api/admin/users/{users['Vera']['id']}", auth=AUTH, json={"role": "lead"}).status_code == 200
    assert count() == n + 1
    d = client.get("/api/admin/audit?q=user_delete", headers=a).json()["entries"]
    assert client.delete(f"/api/admin/users/{users['Ada']['id']}", auth=AUTH).status_code == 422
    assert client.get("/api/admin/audit?q=user_delete", headers=a).json()["entries"] == d
    roles = len(client.get("/api/admin/audit?q=role_create", headers=a).json()["entries"])
    assert client.post("/api/admin/roles", headers=a, json={"name": "Garderobe", "perms": ["jobs"]}).status_code == 200
    assert len(client.get("/api/admin/audit?q=role_create", headers=a).json()["entries"]) == roles + 1
