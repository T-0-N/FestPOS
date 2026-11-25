"""Kritische Aktionen bestätigen und zweiter Faktor (TOTP) für die Verwaltung (T-0-N/FestPOS#9)."""
from __future__ import annotations

import base64
import json
import time

import pytest

from tests.test_api import AUTH

PW = "sicher-123"


def _login(c, name, pw=PW):
    return c.post("/api/admin/login", json={"name": name, "password": pw})


def _bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def ada(client):
    """Admin «Ada» (Pflicht: zweiter Faktor), angemeldet, noch nicht bestätigt."""
    from app import core
    core.login_throttle.clear()
    assert client.post("/api/admin/users", auth=AUTH, json={"name": "Ada", "role": "admin", "password": PW}).status_code == 200
    r = _login(client, "Ada")
    assert r.status_code == 200 and "token" in r.json(), r.text
    return r.json()


@pytest.fixture
def clock(monkeypatch):
    """Uhr für die Codes: steht fest, lässt sich weiterdrehen (jeder Code gilt nur einmal)."""
    from app import totp
    now = {"t": 1_800_000_000.0}
    monkeypatch.setattr(totp, "_now", lambda: now["t"])
    return now


def _setup(client, hdr, clock):
    """Zweiten Faktor einrichten -> (Geheimnis, Wiederherstellungs-Codes)."""
    from app import totp
    s = client.post("/api/admin/me/totp/start", headers=hdr).json()
    secret = s["secret"].replace(" ", "")
    assert s["uri"].startswith("otpauth://totp/FestPOS%3AAda?secret=") and s["qr"].startswith("<svg")
    r = client.post("/api/admin/me/totp/confirm", headers=hdr, json={"code": totp.code_at(secret, int(clock["t"]) // 30)})
    assert r.status_code == 200, r.text
    return secret, r.json()["codes"]


def _code(secret, clock, ahead=1):
    from app import totp
    clock["t"] += 30 * ahead
    return totp.code_at(secret, int(clock["t"]) // 30)


# ------------------------------------------------------------------ TOTP selbst
def test_rfc6238_vector_and_window():
    from app import totp
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert secret == "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp.code_at(secret, 59 // 30) == "287082"                      # RFC 6238, T=59 (8 Ziffern: 94287082)
    assert totp.code_at(secret, 1111111109 // 30) == "081804"
    assert totp.check(secret, "287 082", now=59) == ("ok", 1)              # Leerzeichen egal
    assert totp.check(secret, "287082", now=59 + 30)[0] == "ok"            # ein Schritt daneben: noch gut
    assert totp.check(secret, "287082", now=59, last_step=1) == ("used", 1)
    assert totp.check(secret, "287082", now=59 + 30 * 5) == ("clock", -5)  # Uhr 2.5 Minuten daneben: klare Meldung
    assert totp.check(secret, "000000", now=59)[0] == "wrong" and totp.check(secret, "12", now=59)[0] == "wrong"


def test_recovery_codes_single_use():
    from app import totp
    codes = totp.new_codes()
    assert len(codes) == 10 and len(set(codes)) == 10 and all(len(c) == 9 and c[4] == "-" for c in codes)
    hashes = [totp.code_hash(c, "salz") for c in codes]
    rest = totp.use_code(codes[3].lower().replace("-", ""), "salz", hashes)   # klein, ohne Strich: geht auch
    assert rest is not None and len(rest) == 9
    assert totp.use_code(codes[3], "salz", rest) is None                      # zweites Mal: nein


# ------------------------------------------------------------------ Kritische Aktionen
def test_critical_action_needs_confirmation(client, ada):
    hdr = _bearer(ada["token"])
    assert client.get("/api/admin/users", headers=hdr).status_code == 200            # lesen: ohne Bestätigung
    r = client.post("/api/admin/users", headers=hdr, json={"name": "Ben", "role": "view", "password": PW})
    assert r.status_code == 428 and r.json()["detail"]["code"] == "confirm_needed"
    assert client.put("/api/admin/settings", headers=hdr, json={"admin_idle_minutes": 20}).status_code == 428
    assert client.put("/api/admin/settings", headers=hdr, json={"event_name": "Fest"}).status_code == 200   # unkritisch
    r = client.post("/api/admin/me/confirm", headers=hdr, json={"password": "falsch"})
    assert r.status_code == 422                                                      # 422: bleibt angemeldet
    r = client.post("/api/admin/me/confirm", headers=hdr, json={"password": PW})
    assert r.status_code == 200 and 250 < r.json()["confirmed_in"] <= 300
    ok = _bearer(r.json()["token"])
    assert client.post("/api/admin/users", headers=ok, json={"name": "Ben", "role": "view", "password": PW}).status_code == 200
    # nach dem Verlängern (Bedienung) bleibt die Bestätigung
    ok2 = _bearer(client.post("/api/admin/me/refresh", headers=ok).json()["token"])
    assert client.put("/api/admin/settings", headers=ok2, json={"admin_idle_minutes": 20}).status_code == 200
    audit = json.dumps(client.get("/api/admin/audit", auth=AUTH).json())
    assert '"confirm"' in audit and '"critical"' in audit and "/api/admin/users" in audit


def test_confirmation_expires(client, ada, monkeypatch):
    from app import core
    tok = client.post("/api/admin/me/confirm", headers=_bearer(ada["token"]), json={"password": PW}).json()["token"]
    real = time.time
    monkeypatch.setattr(core.time, "time", lambda: real() + 301)
    r = client.post("/api/admin/users", headers=_bearer(tok), json={"name": "Ben", "role": "view", "password": PW})
    assert r.status_code == 428


# ------------------------------------------------------------------ Zweiter Faktor
def test_setup_login_two_steps_and_recovery(client, ada, clock):
    from app import core
    hdr = _bearer(ada["token"])
    assert ada["totp"]["required"] and ada["totp"]["later"] == 3 and not ada["totp"]["on"]
    secret, codes = _setup(client, hdr, clock)
    assert len(codes) == 10
    me = client.get("/api/admin/me", headers=hdr).json()["totp"]
    assert me["on"] and not me["required"] and me["codes"] == 10 and me["role_required"]
    # Anmeldung: erst Passwort, dann Code
    r = _login(client, "Ada")
    assert r.status_code == 200 and r.json()["second_step"] and "token" not in r.json()
    pending = r.json()["pending"]
    r = client.post("/api/admin/login/totp", json={"pending": pending, "code": "000000"})
    assert r.status_code == 401
    r = client.post("/api/admin/login/totp", json={"pending": pending, "code": _code(secret, clock)})
    assert r.status_code == 200 and r.json()["token"], r.text
    # derselbe Code nochmals: schon benutzt
    r = client.post("/api/admin/login/totp", json={"pending": pending, "code": _code(secret, clock, 0)})
    assert r.status_code == 401 and "schon benutzt" in r.json()["detail"]
    # Uhr daneben: klare Meldung
    from app import totp
    far = totp.code_at(secret, int(clock["t"]) // 30 + 8)
    r = client.post("/api/admin/login/totp", json={"pending": pending, "code": far})
    assert r.status_code == 401 and "Uhr" in r.json()["detail"]
    core.login_throttle.clear()
    # Wiederherstellungs-Code: einmal
    r = client.post("/api/admin/login/totp", json={"pending": pending, "code": codes[0]})
    assert r.status_code == 200
    assert client.post("/api/admin/login/totp", json={"pending": pending, "code": codes[0]}).status_code == 401
    assert client.get("/api/admin/me", headers=hdr).json()["totp"]["codes"] == 9
    assert client.post("/api/admin/login/totp", json={"pending": "1.2.3.x.y", "code": "123456"}).status_code == 409


def test_confirm_with_code_and_basic_auth_read_only(client, ada, clock):
    hdr = _bearer(ada["token"])
    secret, _ = _setup(client, hdr, clock)
    assert client.post("/api/admin/me/confirm", headers=hdr, json={"password": PW}).status_code == 422   # Code fehlt
    r = client.post("/api/admin/me/confirm", headers=hdr, json={"password": PW, "code": _code(secret, clock)})
    assert r.status_code == 200
    # Basic Auth: Passwort allein reicht mit zweitem Faktor nur zum Lesen
    assert client.get("/api/admin/stats", auth=("Ada", PW)).status_code == 200
    assert client.put("/api/admin/settings", auth=("Ada", PW), json={"event_name": "x"}).status_code == 403


def test_later_three_times_then_blocked(client, ada):
    from app import auth, core
    hdr = _bearer(ada["token"])
    for left in (2, 1, 0):
        assert client.post("/api/admin/me/totp/later", headers=hdr).json()["later"] == left
    assert client.post("/api/admin/me/totp/later", headers=hdr).status_code == 409
    assert client.get("/api/admin/stats", headers=hdr).status_code == 200      # drittes «Später»: diese Anmeldung noch
    store = core.rt.store
    d = json.loads(store.scalar("SELECT totp FROM users WHERE name='Ada'"))
    store.execute("UPDATE users SET totp=? WHERE name='Ada'", (json.dumps({**d, "later_until": 0}),))
    auth.forget()
    r = client.get("/api/admin/stats", headers=hdr)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "totp_setup"
    me = client.get("/api/admin/me", headers=hdr).json()["totp"]                 # Einrichten geht weiter
    assert me["blocked"] and me["later"] == 0
    assert client.post("/api/admin/me/totp/start", headers=hdr).status_code == 200


def test_required_roles_setting_and_admin_reset(client, ada, clock):
    from app import auth, twofa
    hdr = _bearer(ada["token"])
    secret, _ = _setup(client, hdr, clock)
    assert client.post("/api/admin/me/totp/off", headers=hdr, json={"password": PW, "code": _code(secret, clock)}).status_code == 409
    # Pflicht für Festleitung statt Admin (Basic Auth des Notfall-Logins gilt als bestätigt)
    r = client.put("/api/admin/settings", auth=AUTH, json={"totp_required_roles": ["lead", "gibts-nicht"]})
    assert r.status_code == 200 and json.loads(r.json()["totp_required_roles"]) == ["lead"]
    twofa.forget()
    assert client.post("/api/admin/users", auth=AUTH, json={"name": "Lea", "role": "lead", "password": PW}).status_code == 200
    assert _login(client, "Lea").json()["totp"]["required"]
    # Ada: nicht mehr Pflicht -> selbst ausschalten geht
    r = client.post("/api/admin/me/totp/off", headers=hdr, json={"password": PW, "code": _code(secret, clock)})
    assert r.status_code == 200 and not client.get("/api/admin/me", headers=hdr).json()["totp"]["on"]
    # Admin setzt den zweiten Faktor einer anderen Person zurück (Handy verloren) – deren Sitzungen enden
    lea = _login(client, "Lea").json()
    s = client.post("/api/admin/me/totp/start", headers=_bearer(lea["token"])).json()["secret"].replace(" ", "")
    from app import totp
    client.post("/api/admin/me/totp/confirm", headers=_bearer(lea["token"]),
                json={"code": totp.code_at(s, int(clock["t"]) // 30)})
    uid = next(u["id"] for u in client.get("/api/admin/users", auth=AUTH).json()["users"] if u["name"] == "Lea")
    assert next(u for u in client.get("/api/admin/users", auth=AUTH).json()["users"] if u["name"] == "Lea")["totp_on"]
    assert client.put(f"/api/admin/users/{uid}", auth=AUTH, json={"totp_reset": True}).status_code == 200
    auth.forget()
    assert client.get("/api/admin/me", headers=_bearer(lea["token"])).status_code == 401
    assert "token" in _login(client, "Lea").json()                                # wieder ohne zweiten Schritt


def test_secret_stored_encrypted(client, ada, clock):
    from app import core
    secret, codes = _setup(client, _bearer(ada["token"]), clock)
    raw = core.rt.store.scalar("SELECT totp FROM users WHERE name='Ada'")
    assert secret not in raw and codes[0] not in raw and codes[0].replace("-", "") not in raw


def test_enabled_factor_not_replaced_and_code_used_once(client, ada, clock):
    """Ein eingeschalteter Faktor lässt sich mit der Sitzung allein nicht ersetzen; ein Code gilt genau einmal, auch
    wenn der gespeicherte Stand dazwischen geändert wurde (Vergleichen und Setzen in einem Schritt)."""
    from app import core, twofa
    hdr = _bearer(ada["token"])
    secret, codes = _setup(client, hdr, clock)
    assert client.post("/api/admin/me/totp/start", headers=hdr).status_code == 409
    assert client.post("/api/admin/me/totp/confirm", headers=hdr, json={"code": "123456"}).status_code == 409
    uid = core.rt.store.scalar("SELECT id FROM users WHERE name='Ada'")
    code = _code(secret, clock)
    real = twofa._raw
    calls = []

    def stale(u):                       # andere Anfrage war schneller: zwischen Lesen und Schreiben verbraucht
        raw = real(u)
        if not calls:
            calls.append(1)
            assert twofa.verify_code(u, code, "Ada") == "ok"
        return raw
    twofa._raw = stale
    try:
        assert twofa.verify_code(uid, code, "Ada") == "used"
    finally:
        twofa._raw = real
    assert twofa.verify_code(uid, codes[0], "Ada") == "ok" and twofa.verify_code(uid, codes[0], "Ada") == "wrong"


def test_recovery_code_competing_redemption(client, ada, clock, monkeypatch):
    """Zwei Anfragen lesen denselben noch unbenutzten Wiederherstellungs-Code: nur eine kommt durch."""
    from app import core, twofa
    hdr = _bearer(ada["token"])
    _, codes = _setup(client, hdr, clock)
    uid = core.rt.store.scalar("SELECT id FROM users WHERE name='Ada'")
    real, results, busy = twofa._raw, [], []

    def stale(u):                       # die zweite Anfrage löst genau zwischen Lesen und Schreiben ein
        raw = real(u)
        if not busy:
            busy.append(1)
            results.append(twofa.verify_code(u, codes[1], "Ada"))
        return raw
    monkeypatch.setattr(twofa, "_raw", stale)
    results.append(twofa.verify_code(uid, codes[1], "Ada"))
    assert sorted(results) == ["ok", "used"]
    n = core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='totp_recovery_used'")
    assert n == 1


def test_setup_confirm_fails_if_restarted_meanwhile(client, ada, clock, monkeypatch):
    """Einrichten im zweiten Fenster neu begonnen, während das erste bestätigt: das erste gilt nicht (anderes Geheimnis
    in der App), nichts eingeschaltet, kein Protokolleintrag."""
    from app import core, totp, twofa
    hdr = _bearer(ada["token"])
    first = client.post("/api/admin/me/totp/start", headers=hdr).json()["secret"].replace(" ", "")
    real, done = twofa._raw, []

    def stale(u):
        raw = real(u)
        if not done:
            done.append(0)
            done[0] = client.post("/api/admin/me/totp/start", headers=hdr).status_code
        return raw
    monkeypatch.setattr(twofa, "_raw", stale)
    r = client.post("/api/admin/me/totp/confirm", headers=hdr, json={"code": totp.code_at(first, int(clock["t"]) // 30)})
    assert done == [200] and r.status_code == 409
    uid = core.rt.store.scalar("SELECT id FROM users WHERE name='Ada'")
    assert not twofa.data_of(uid).get("on")
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='totp_on'") == 0
