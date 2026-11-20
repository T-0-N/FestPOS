"""T2: Systemzeit – Zeitstand pro Pi, Bestätigung und Fristen, Rollen, manuell setzen, Zeitserver per USB,
RTC-Batterie laden (config.txt), Zeitserver per DHCP. Unabhängig vom Testrechner: RTC, chrony und /run über Testordner."""
from __future__ import annotations

import calendar
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from app import timesync
from tests.test_api import AUTH

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
DAY = 86400


# -- Regeln ------------------------------------------------------------------------------------------------
def _pi(node="a", boot=0.0, sure=True, offset=0.0):
    return {"node": node, "boot": boot, "sure": sure, "offset": offset}


def test_prompt_reasons_deadlines():
    now = 2_000_000_000
    conf = lambda days: {"at": now - days * DAY, "how": "ok", "by": "x", "node": "a"}   # noqa: E731
    assert timesync.prompt_reasons([_pi()], None, now) == ["never"]
    # nach einem Neustart: erst wenn die Bestätigung älter als 7 Tage ist (tägliches Ein-/Ausschalten am Fest fragt nicht)
    assert timesync.prompt_reasons([_pi(boot=now - 3600)], conf(6), now) == []
    assert timesync.prompt_reasons([_pi(boot=now - 3600)], conf(8), now) == ["restart"]
    assert timesync.prompt_reasons([_pi(boot=now - 9 * DAY)], conf(8), now) == []          # vor der Bestätigung gestartet
    # ohne Neustart erst nach 60 Tagen
    assert timesync.prompt_reasons([_pi(boot=now - 70 * DAY)], conf(59), now) == []
    assert timesync.prompt_reasons([_pi(boot=now - 70 * DAY)], conf(61), now) == ["old"]
    # unsichere Uhr (RTC-Batterie leer, nicht gestellt) sofort; Pi untereinander > 5 s
    assert timesync.prompt_reasons([_pi(), _pi("b", sure=False)], conf(1), now) == ["unsure"]
    assert timesync.prompt_reasons([_pi(), _pi("b", offset=6.0)], conf(1), now) == ["cluster"]
    assert timesync.prompt_reasons([_pi(), _pi("b", offset=-3.0), _pi("c", offset=1.5)], conf(1), now) == []


def test_parse_tracking():
    peers = ["192.168.55.11", "192.168.55.12"]
    assert timesync.parse_tracking("", peers) is None
    assert timesync.parse_tracking("A29FC87B,162.159.200.123,3,1.7e9,0.0001,0,0,0,0,0,0.01,0.001,64.0,Normal\n", peers) == "internet"
    assert timesync.parse_tracking("C0A8370C,192.168.55.12,11,1.7e9,0,0,0,0,0,0,0,0,64,Normal", peers) == "peer:192.168.55.12"
    assert timesync.parse_tracking("7F7F0101,127.127.1.1,10,1.7e9,0,0,0,0,0,0,0,0,64,Normal", peers) == "local"
    assert timesync.parse_tracking("00000000,,0,0,0,0,0,0,0,0,0,0,0,Not synchronised", peers) is None


@pytest.fixture
def zurich(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Zurich")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_local_to_epoch_summer_and_winter(zurich):
    assert timesync.local_to_epoch("2026-07-01T12:00:00") == calendar.timegm((2026, 7, 1, 10, 0, 0))     # MESZ, UTC+2
    assert timesync.local_to_epoch("2026-12-01T12:00") == calendar.timegm((2026, 12, 1, 11, 0, 0))       # MEZ, UTC+1
    assert timesync.tz_info(calendar.timegm((2026, 7, 1, 10, 0, 0))) == {"tz": "Europe/Zurich", "offset_min": 120, "dst": True}
    assert timesync.tz_info(calendar.timegm((2026, 12, 1, 10, 0, 0)))["dst"] is False
    for bad in ("2026-02-31T10:00", "2026-03-29T02:30", "morgen", "2026-10-28 25:00"):           # 02:30 gibt es nicht
        with pytest.raises(ValueError):
            timesync.local_to_epoch(bad)


def test_state_sources(tmp_path, monkeypatch):
    rtc = tmp_path / "rtc"
    (rtc / "device").mkdir(parents=True)
    (rtc / "hctosys").write_text("0\n")
    (rtc / "device" / "battery_voltage").write_text("410000\n")
    (rtc / "device" / "charging_voltage").write_text("0\n")
    monkeypatch.setattr(timesync, "RTC_DIR", str(rtc))
    monkeypatch.setattr(timesync, "ZEIT_STAND", str(tmp_path / "stand"))
    monkeypatch.setattr(timesync, "chrony_source", lambda peers=(): None)
    st = timesync.state("a")
    assert st["sure"] is False and st["rtc"]["battery_uv"] == 410000 and st["rtc"]["charging_uv"] == 0
    (tmp_path / "stand").write_text(json.dumps({"at": time.time(), "source": "peer"}))
    assert timesync.state("a")["sure"] is True                                          # seit dem Start gestellt
    (tmp_path / "stand").unlink()
    monkeypatch.setattr(timesync, "chrony_source", lambda peers=(): "internet")
    assert timesync.state("a")["sure"] is True
    monkeypatch.setattr(timesync, "RTC_DIR", str(tmp_path / "fehlt"))
    monkeypatch.setattr(timesync, "chrony_source", lambda peers=(): "local")
    assert timesync.state("a")["rtc"] is None and timesync.state("a")["sure"] is False


def test_rtc_values_pi5_layout_and_drift(tmp_path, monkeypatch):
    """T16: Pi 5 hat battery_voltage/charging_voltage direkt unter rtc0/ (ältere Kernel: rtc0/device/). Weicht die RTC
    weit ab (hat beim Stromunterbruch die Zeit nicht gehalten), gilt die Uhr als unsicher – auch mit hctosys=1."""
    rtc = tmp_path / "rtc0"
    (rtc / "device").mkdir(parents=True)
    (rtc / "hctosys").write_text("1\n")
    (rtc / "battery_voltage").write_text("3012000\n")
    (rtc / "charging_voltage").write_text("3000000\n")
    (rtc / "device" / "battery_voltage").write_text("1\n")                         # nur Rückfall
    (rtc / "since_epoch").write_text(f"{int(time.time())}\n")
    monkeypatch.setattr(timesync, "RTC_DIR", str(rtc))
    monkeypatch.setattr(timesync, "ZEIT_STAND", str(tmp_path / "stand"))
    monkeypatch.setattr(timesync, "chrony_source", lambda peers=(): None)
    st = timesync.state("a")
    assert st["rtc"]["battery_uv"] == 3012000 and st["rtc"]["charging_uv"] == 3000000
    assert abs(st["rtc"]["drift"]) <= 2 and st["rtc"]["lost"] is False and st["sure"] is True
    (rtc / "battery_voltage").unlink()
    assert timesync.state("a")["rtc"]["battery_uv"] == 1                              # rtc0/device/ (älterer Kernel)
    (rtc / "since_epoch").write_text("946684800\n")                                  # RTC auf 2000 zurückgefallen
    st = timesync.state("a")
    assert st["rtc"]["lost"] is True and st["sure"] is False and st["rtc"]["drift"] < -10 ** 8
    assert "unsure" in timesync.prompt_reasons([st], {"at": time.time() - 60, "how": "ok", "by": "x", "node": "a"})


# -- Verwaltung ---------------------------------------------------------------------------------------------
def _users(client):
    for name, role in (("Lea", "lead"), ("Vera", "view")):
        assert client.post("/api/admin/users", auth=AUTH, json={"name": name, "role": role,
                                                                  "password": f"{name}-passwort"}).status_code == 200


def test_roles_only_lead_and_admin(client, monkeypatch):
    from app import time_admin
    monkeypatch.setattr(time_admin.timesync, "set_local", lambda *a, **k: (True, "ok"))
    _users(client)
    view, lead = ("Vera", "Vera-passwort"), ("Lea", "Lea-passwort")
    for method, path, body in (("GET", "/api/admin/time", None), ("POST", "/api/admin/time/confirm", None),
                               ("POST", "/api/admin/time", {"epoch_ms": int(time.time() * 1000)}),
                               ("GET", "/api/admin/time/usb", None)):
        assert client.request(method, path, auth=view, json=body).status_code == 403, path
        assert client.request(method, path, auth=lead, json=body).status_code == 200, path


def test_confirm_set_manual_and_audit(client, monkeypatch, zurich):
    from app import time_admin
    calls = []
    monkeypatch.setattr(time_admin.timesync, "set_local", lambda ms, wait_s=10.0, source="": calls.append((ms, source)) or (True, "ok"))
    r = client.get("/api/admin/time", auth=AUTH).json()
    assert r["reasons"] == ["never"] and r["confirmed"] is None and r["pis"][0]["tz"] == "Europe/Zurich"
    assert client.post("/api/admin/time/confirm", auth=AUTH).status_code == 200
    r = client.get("/api/admin/time", auth=AUTH).json()
    assert r["reasons"] == [] and r["confirmed"]["how"] == "ok" and r["confirmed"]["by"] == "admin"
    assert calls[-1][1] == "ok" and abs(calls[-1][0] / 1000 - time.time()) < 5           # alle Pi auf die Uhr dieses Pi
    # manuell: in der Zeitzone des Pi umgerechnet, nicht in der des Browsers
    assert client.post("/api/admin/time", auth=AUTH, json={"local": "2026-12-01T12:00:00"}).status_code == 200
    want = calendar.timegm((2026, 12, 1, 11, 0, 0)) * 1000         # dazu die Zeit seit Empfang (genau bleiben), CI: ≥ 1 ms
    assert calls[-1][1] == "manual" and 0 <= calls[-1][0] - want < 1000, calls[-1]
    assert client.get("/api/admin/time", auth=AUTH).json()["confirmed"]["how"] == "manual"
    for body in ({"local": "2026-02-31T10:00"}, {"local": "2020-01-01T10:00"}, {}, {"epoch_ms": 1}):
        assert client.post("/api/admin/time", auth=AUTH, json=body).status_code == 400, body
    acts = [(e["action"], e["details"].get("how")) for e in client.get("/api/admin/audit", auth=AUTH).json()["entries"]]
    assert ("time_set", "ok") in acts and ("time_set", "manual") in acts


def test_confirm_counts_even_if_time_service_missing(client, monkeypatch):
    """«Zeit stimmt» ist eine Aussage der Festleitung – sie gilt, auch wenn ein Pi die Zeit nicht neu setzen kann.
    «Übernehmen» ohne Zeit-Dienst bestätigt dagegen nichts."""
    from app import time_admin
    monkeypatch.setattr(time_admin.timesync, "set_local", lambda *a, **k: (False, "Zeit-Dienst fehlt"))
    r = client.post("/api/admin/time", auth=AUTH, json={"epoch_ms": int(time.time() * 1000)}).json()
    assert r["nodes"]["lokal"] == "Zeit-Dienst fehlt"
    assert client.get("/api/admin/time", auth=AUTH).json()["confirmed"] is None
    client.post("/api/admin/time/confirm", auth=AUTH)
    assert client.get("/api/admin/time", auth=AUTH).json()["confirmed"]["how"] == "ok"


def test_internal_time_needs_secret(client, monkeypatch):
    from app import main as m
    monkeypatch.setattr(m.Config, "cluster_secret", "Grüessli-vom-Pi-2026")
    assert client.get("/api/internal/time").status_code == 403
    assert client.get("/api/internal/time", headers={"X-Festpos-Secret": "falsch"}).status_code == 403
    r = client.get("/api/internal/time", headers={"X-Festpos-Secret": "Grüessli-vom-Pi-2026".encode()})
    assert r.status_code == 200 and r.json()["sure"] is True and abs(r.json()["time"] - time.time()) < 5


def test_peer_sync_only_when_unsure(client, monkeypatch):
    """Pi mit unsicherer Uhr (RTC-Batterie leer) holt die Zeit von einem Pi mit sicherer Uhr – nie umgekehrt."""
    from app import time_admin
    sets = []
    monkeypatch.setattr(time_admin.timesync, "set_local", lambda ms, wait_s=10.0, source="": sets.append(source) or (True, "ok"))
    monkeypatch.setattr(time_admin.timesync, "peer_states", lambda *a, **k: {
        "https://192.168.55.12:8443": {"node": "b", "sure": False, "offset": 3600.0},
        "https://192.168.55.13:8443": {"node": "c", "sure": True, "offset": 1800.0}})
    time_admin._sync_from_peer({"sure": False})
    assert sets == ["peer"]
    audit = client.get("/api/admin/audit", auth=AUTH).json()["entries"][0]
    assert audit["action"] == "time_set" and audit["details"]["source"] == "c" and audit["details"]["diff_s"] == 1800


def test_usb_results_ingested_and_shown(client, tmp_path, monkeypatch):
    from app import time_admin
    monkeypatch.setattr(time_admin, "USB_DIR", str(tmp_path))
    start = client.get("/api/admin/time/usb", auth=AUTH).json()["server_time"]
    (tmp_path / "R58N123.json").write_text(json.dumps({"serial": "R58N123", "at": round(time.time()), "ok": True,
                                                       "ip": "192.168.55.101", "model": "SM-G556B", "android": "15",
                                                       "steps": {"ntp_server": True, "auto_time": True}}))
    r = client.get(f"/api/admin/time/usb?since={start - 5}", auth=AUTH).json()["results"]
    assert r["R58N123"]["ok"] is True and r["R58N123"]["ip"] == "192.168.55.101"
    assert client.get("/api/admin/time", auth=AUTH).json()["usb"]["R58N123"]["model"] == "SM-G556B"
    client.get("/api/admin/time/usb", auth=AUTH)                                         # zweimal gelesen: ein Eintrag
    acts = [e for e in client.get("/api/admin/audit", auth=AUTH).json()["entries"] if e["action"] == "phone_ntp"]
    assert len(acts) == 1 and acts[0]["details"]["result"] == "ok"


def test_usb_result_of_other_pi_is_not_overwritten_by_older_local_one(client, tmp_path, monkeypatch):
    """Handy zuerst an Pi A, danach an Pi B – angezeigt wurde trotzdem «an Pi A». Pi A hatte sein älteres
    Ergebnis (liegt bis zum Neustart in /run) alle 10 s wieder über das neuere von Pi B geschrieben."""
    from app import time_admin
    from app.core import rt
    monkeypatch.setattr(time_admin, "USB_DIR", str(tmp_path))
    now = round(time.time())
    newer = {"at": now, "ok": True, "ip": "192.168.55.101", "node": "b"}
    rt.store.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (time_admin.USB_KEY, json.dumps({"R58N555": newer})))
    (tmp_path / "R58N555.json").write_text(json.dumps({"serial": "R58N555", "at": now - 600, "ok": True,
                                                       "ip": "192.168.55.101"}))     # älter, hier an diesem Pi
    time_admin._ingest_usb()
    time_admin._ingest_usb()
    got = client.get("/api/admin/time", auth=AUTH).json()["usb"]["R58N555"]
    assert got["node"] == "b" and got["at"] == now
    (tmp_path / "R58N555.json").write_text(json.dumps({"serial": "R58N555", "at": now + 60, "ok": True}))
    time_admin._ingest_usb()                                               # neuer hier: wird übernommen
    assert client.get("/api/admin/time", auth=AUTH).json()["usb"]["R58N555"]["at"] == now + 60


def test_usb_results_ingested_once_when_called_at_the_same_time(client, tmp_path, monkeypatch):
    """Monitor-Takt und Verwaltung übernehmen gleichzeitig: trotzdem nur ein Eintrag im Protokoll (Sperre)."""
    import threading

    from app import time_admin
    monkeypatch.setattr(time_admin, "USB_DIR", str(tmp_path))
    monkeypatch.setattr(time_admin, "tick", lambda: None)                  # der Monitor im Hintergrund stört hier nicht
    (tmp_path / "R58N777.json").write_text(json.dumps({"serial": "R58N777", "at": round(time.time()), "ok": True}))
    real, gate = time_admin.usb_results, threading.Barrier(2)

    def usb_results():                                     # ohne Sperre lesen hier beide den alten Stand
        r = real()
        try:
            gate.wait(1)
        except threading.BrokenBarrierError:
            pass
        return r
    monkeypatch.setattr(time_admin, "usb_results", usb_results)
    threads = [threading.Thread(target=time_admin._ingest_usb) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    acts = [e for e in client.get("/api/admin/audit", auth=AUTH).json()["entries"] if e["action"] == "phone_ntp"]
    assert len(acts) == 1


def test_restore_check_time_uses_state(client, monkeypatch):
    """Prüfliste nach dem Einspielen (T8): «Zeit korrekt» auch nur, wenn die Uhr aus einer sicheren Quelle kommt."""
    from app import restore_check, time_admin
    assert restore_check._time_item()["state"] == "ok"
    monkeypatch.setattr(time_admin, "local_state", lambda: {"sure": False})
    assert restore_check._time_item()["state"] == "bad"


# -- Fully: ADB-Einstellung ---------------------------------------------------------------------------------
def test_fully_adb_setting():
    from app import fully
    assert fully.adb_setting({"kioskMode": True, "screenOn": False}) is None
    assert fully.adb_setting({"disableAdb": True}) == ("disableAdb", False)               # «disable…»: false = ADB ein
    assert fully.adb_setting({"enableAdb": "false"}) == ("enableAdb", True)
    assert fully.adb_setting({"adbPassword": "x"}) is None                                # nie Geheimes, nie Text


# -- root-Helfer festpos-handy-usb mit einem Ersatz für adb -----------------------------------------------------
def _fake_adb(tmp_path, state="device"):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "adb.log"
    (bin_ / "adb").write_text(f"""#!/bin/sh
echo "$*" >> {log}
[ "$1" = "-s" ] && shift 2
case "$*" in
  "devices -l") printf 'List of devices attached\\nR58N123 {state} usb:1-1 product:x model:SM_G556B\\n' ;;
  "shell getprop ro.product.model") echo SM-G556B ;;
  "shell getprop ro.build.version.release") echo 15 ;;
  "shell ip -4 -o addr show wlan0") echo "3: wlan0    inet 192.168.55.101/24 brd 192.168.55.255 scope global wlan0" ;;
  "shell settings get global ntp_server") cat {tmp_path}/ntp 2>/dev/null ;;
  "shell settings get global auto_time") echo 1 ;;
  "shell settings put global ntp_server "*) echo "$6" > {tmp_path}/ntp ;;
esac
exit 0
""")
    (bin_ / "adb").chmod(0o755)
    return {**os.environ, "FESTPOS_ADB": str(bin_ / "adb"), "FESTPOS_HANDY_USB_DIR": str(tmp_path / "out"),
            "FESTPOS_VIP": "192.168.55.10", "FESTPOS_HANDY_USB_WAIT": "2"}, log


def test_handy_usb_sets_ntp_and_turns_adb_off(tmp_path):
    env, log = _fake_adb(tmp_path)
    r = subprocess.run([str(DEPLOY / "festpos-handy-usb"), "1-1:1.0"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads((tmp_path / "out" / "R58N123.json").read_text())
    assert out["ok"] is True and out["ip"] == "192.168.55.101" and out["model"] == "SM-G556B" and out["steps"]["adb_off"]
    calls = log.read_text().splitlines()
    assert "-s R58N123 shell settings put global ntp_server 192.168.55.10" in calls
    assert calls[-2].endswith("settings put global adb_enabled 0") and calls[-1] == "kill-server"


def test_handy_usb_not_allowed(tmp_path):
    env, _ = _fake_adb(tmp_path, state="unauthorized")
    r = subprocess.run([str(DEPLOY / "festpos-handy-usb"), "1-1:1.0"], env=env, capture_output=True, text=True)
    assert r.returncode == 3
    assert json.loads((tmp_path / "out" / "R58N123.json").read_text())["error"] == "not_allowed"
    env["FESTPOS_VIP"] = ""
    assert subprocess.run([str(DEPLOY / "festpos-handy-usb"), "1-1:1.0"], env=env).returncode == 2


# -- festpos-conf.sh: RTC-Batterie laden, Zeitserver per DHCP ---------------------------------------------------
LIB = DEPLOY / "festpos-conf.sh"


def _charge(f: Path, want: int) -> str:
    return subprocess.run(["bash", "-c", f'source "{LIB}"; festpos_rtc_charge "{f}" {want}'],
                          capture_output=True, text=True, check=True).stdout.strip()


def test_rtc_charge_config_txt(tmp_path):
    f = tmp_path / "config.txt"
    orig = "dtoverlay=disable-bt\n[pi4]\narm_boost=1\ndtparam=rtc_bbat_vchg=2000000\n"
    f.write_text(orig)
    assert _charge(f, 1) == "geändert"
    text = f.read_text()
    assert text.count("rtc_bbat_vchg") == 1 and text.endswith("[all]\ndtparam=rtc_bbat_vchg=3000000\n")
    assert (tmp_path / "config.txt.festpos-alt").read_text() == orig
    assert _charge(f, 1) == "" and f.read_text() == text                                # zweiter Start: nichts
    assert _charge(f, 0) == "geändert"
    assert "rtc_bbat_vchg" not in f.read_text() and "[pi4]" in f.read_text()
    assert _charge(f, 0) == ""
    f.write_text("dtparam=rtc_bbat_vchg=3000000\n")                                    # von Hand: bleibt bei 0
    assert _charge(f, 0) == "" and f.read_text() == "dtparam=rtc_bbat_vchg=3000000\n"


def _fake_hwclock(tmp_path, uv: str | None, units=("fake-hwclock-load.service", "fake-hwclock-save.timer")):
    """festpos_fake_hwclock mit nachgebildetem systemctl (Zustand pro Dienst als Datei) und RTC-Testordner."""
    bin_, state = tmp_path / "bin", tmp_path / "units"
    bin_.mkdir(exist_ok=True)
    state.mkdir(exist_ok=True)
    for u in units:
        if not (state / u).exists():
            (state / u).write_text("enabled")
    (bin_ / "systemctl").write_text(f"""#!/bin/bash
S="{state}"
case "$1" in
  list-unit-files) for f in "$S"/*; do [ -e "$f" ] && echo "$(basename "$f") $(cat "$f")"; done ;;
  is-enabled) cat "$S/$2" ;;
  mask) echo masked > "$S/$2" ;;
  unmask) echo enabled > "$S/$2" ;;
esac
""")
    (bin_ / "systemctl").chmod(0o755)
    rtc = tmp_path / "rtc0"
    rtc.mkdir(exist_ok=True)
    (rtc / "battery_voltage").unlink(missing_ok=True)
    if uv is not None:
        (rtc / "battery_voltage").write_text(uv + "\n")
    data = tmp_path / "fake-hwclock.data"
    out = subprocess.run(["bash", "-c", f'source "{LIB}"; festpos_fake_hwclock "{rtc}" "{data}"'], capture_output=True,
                         text=True, check=True, env={**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}"}).stdout.strip()
    return out, {u.name: u.read_text().strip() for u in state.iterdir()}, data


def test_fake_hwclock_only_without_rtc(tmp_path):
    """T16: fake-hwclock stellte auf dem Pi 5 die richtige RTC-Zeit beim Start zurück – mit Batterie abschalten."""
    (tmp_path / "fake-hwclock.data").write_text("2026-11-07 16:01:50\n")
    out, units, data = _fake_hwclock(tmp_path, "2997433")
    assert out == "aus" and set(units.values()) == {"masked"} and not data.exists()
    assert _fake_hwclock(tmp_path, "2997433")[0] == ""                                  # nächster Start: nichts
    out, units, _ = _fake_hwclock(tmp_path, "120000")                                    # Batterie leer: wieder ein
    assert out == "ein" and set(units.values()) == {"enabled"}
    assert _fake_hwclock(tmp_path, "120000")[0] == ""
    assert _fake_hwclock(tmp_path, None)[0] == ""                                        # Pi ohne RTC: bleibt ein
    assert _fake_hwclock(tmp_path, "unsinn")[1] == {u: "enabled" for u in units}


def test_fake_hwclock_removed_package(tmp_path):
    """fake-hwclock von Hand entfernt (apt purge): keine Dienste, nichts zu tun; eine alte Datei wird gelöscht."""
    out, units, data = _fake_hwclock(tmp_path, "3002561", units=())
    assert out == "" and units == {} and not data.exists()


def test_dhcp_ntp_option():
    args = ["192.168.55", "24", "eth0", "150-189", "192.168.55.10", "1.1.1.1", "a", "1", "192.168.55.10"]
    q = " ".join(f'"{a}"' for a in args)
    r = subprocess.run(["bash", "-c", f'source "{LIB}"; festpos_dhcp_conf {q}'], capture_output=True, text=True)
    assert r.returncode == 0 and "dhcp-option=option:ntp-server,192.168.55.10" in r.stdout.splitlines()
    r = subprocess.run(["bash", "-c", f'source "{LIB}"; festpos_dhcp_conf {q[:-16]} "kein-ip"'], capture_output=True, text=True)
    assert r.returncode == 1
