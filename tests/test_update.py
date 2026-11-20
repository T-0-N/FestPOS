"""REV-032: deploy/update.sh in einem Test-Wurzelverzeichnis (systemctl und curl sind Attrappen).

Geprüft wird: Erfolg, automatischer Rückfall, wenn die neue Version nicht anläuft, nichts geändert bei
kaputten Paketen oder kaputtem Programm, und «update.sh --zurueck»."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
OLD = "2026.10.10-4"

SYSTEMCTL = r"""#!/bin/bash
echo "systemctl $*" >> "$FESTPOS_TEST_ROOT/systemctl.log"
case "$1" in
  is-enabled|is-active) exit 1 ;;
  restart)
    # Attrappe: «festpos» startet die installierte Version – ausser die Testmarke «kaputt» liegt da
    if [[ " $* " == *" festpos "* ]]; then
      d=$FESTPOS_TEST_ROOT/opt/festpos
      if [ -f "$d/VERSION" ]; then v=$(tr -d '[:space:]' < "$d/VERSION"); else v=$(grep -m1 '^APP_VERSION' "$d/app/main.py" | cut -d'"' -f2); fi
      if [ -e "$FESTPOS_TEST_ROOT/kaputt" ] && [ "$v" != "$(cat "$FESTPOS_TEST_ROOT/alt-version")" ]; then
        rm -f "$FESTPOS_TEST_ROOT/health.json"
      else
        echo "{\"version\": \"$v\", \"ready\": true}" > "$FESTPOS_TEST_ROOT/health.json"
      fi
    fi ;;
esac
exit 0
"""
CURL = r"""#!/bin/bash
for a in "$@"; do url=$a; done
case "$url" in
  *8443/api/health) [ -f "$FESTPOS_TEST_ROOT/health.json" ] && cat "$FESTPOS_TEST_ROOT/health.json" && exit 0; exit 7 ;;
  *) exit 7 ;;
esac
"""


@pytest.fixture
def fake(tmp_path):
    r = tmp_path / "root"
    dest = r / "opt" / "festpos"
    (dest / "app").mkdir(parents=True)
    (dest / "app" / "main.py").write_text(f'APP_VERSION = "{OLD}"\n')
    (dest / "static").mkdir()
    (dest / "static" / "index.html").write_text("alt")
    (dest / "requirements.txt").write_text("alt\n")
    venv = dest / "venv" / "bin"
    venv.mkdir(parents=True)
    # T43: Startskript statt Verknüpfung – ein Link auf den Interpreter einer venv verliert deren pyvenv.cfg (dann
    # fehlen ihre Pakete); exec über den ursprünglichen Pfad behält sie
    (venv / "python").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    (venv / "python").chmod(0o755)
    # installierte Pakete = die der neuen Version (sonst würde update.sh eine venv bauen)
    (dest / "venv" / ".festpos-requirements").write_text(
        hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest() + "\n")
    sbin = r / "usr" / "local" / "sbin"
    sbin.mkdir(parents=True)
    (sbin / "festpos-rqlited").write_text("alt-rqlited\n")
    (sbin / "festpos-node").write_text("#!/bin/sh\nexit 0\n")
    (sbin / "festpos-node").chmod(0o755)
    (r / "alt-version").write_text(OLD)
    (r / "health.json").write_text(f'{{"version": "{OLD}", "ready": true}}')
    stub = tmp_path / "stub"
    stub.mkdir()
    for name, body in (("systemctl", SYSTEMCTL), ("curl", CURL)):
        (stub / name).write_text(body)
        (stub / name).chmod(0o755)
    env = {**os.environ, "FESTPOS_TEST_ROOT": str(r), "FESTPOS_UPDATE_WAIT": "3",
           "PATH": f"{stub}:{os.environ['PATH']}"}
    return r, dest, env


def run(src: Path, env, *args):
    return subprocess.run(["bash", str(src / "deploy" / "update.sh"), *args], env=env,
                          capture_output=True, text=True, timeout=180)


def project_copy(tmp_path) -> Path:
    dst = tmp_path / "festpos"
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns("wheels", "dist", ".git", "__pycache__", ".pytest_cache", ".ruff_cache"))
    return dst


def test_update_success_and_manual_rollback(fake):
    r, dest, env = fake
    res = run(ROOT, env)
    assert res.returncode == 0, res.stdout + res.stderr
    new = (ROOT / "VERSION").read_text().strip()
    assert "OK: Server läuft mit Version " + new in res.stdout
    assert (dest / "VERSION").read_text().strip() == new
    assert (dest / "app" / "core.py").exists()
    assert (r / "usr/local/sbin/festpos-rqlited").read_text() != "alt-rqlited\n"
    assert (r / "etc/systemd/system/festpos.service").exists()
    assert (r / "opt/festpos.alt/app/main.py").read_text() == f'APP_VERSION = "{OLD}"\n'
    assert "rqlited wird neu gestartet" in res.stdout              # Startskript der Datenbank hat sich geändert
    # von Hand zurück
    res = run(ROOT, env, "--zurueck", "--ja")
    assert res.returncode == 0 and f"Zurückgeschaltet: Server läuft wieder mit Version {OLD}" in res.stdout
    assert not (dest / "VERSION").exists() and (dest / "app" / "main.py").read_text().startswith("APP_VERSION")
    assert (r / "usr/local/sbin/festpos-rqlited").read_text() == "alt-rqlited\n"
    assert not (r / "etc/systemd/system/festpos.service").exists()   # kam erst mit der neuen Version


def test_update_rolls_back_when_new_version_does_not_start(fake):
    r, dest, env = fake
    (r / "kaputt").touch()
    res = run(ROOT, env)
    assert res.returncode == 1
    assert "FEHLER: Server meldet" in res.stdout and f"Zurückgeschaltet: Server läuft wieder mit Version {OLD}" in res.stdout
    assert (dest / "app" / "main.py").read_text() == f'APP_VERSION = "{OLD}"\n'
    assert (dest / "static" / "index.html").read_text() == "alt"
    assert (r / "usr/local/sbin/festpos-rqlited").read_text() == "alt-rqlited\n"


def test_update_changes_nothing_when_packages_or_program_are_broken(fake, tmp_path):
    r, dest, env = fake
    src = project_copy(tmp_path)
    before = sorted(str(p.relative_to(r)) for p in r.rglob("*"))
    # a) neue Pakete nötig, aber nicht installierbar (keine Wheels, kein Internet für diese Version)
    (src / "requirements.txt").write_text("festpos-gibt-es-nicht==0.0.1 --hash=sha256:" + "0" * 64 + "\n")
    res = run(src, {**env, "PIP_INDEX_URL": "http://127.0.0.1:9/simple"})
    assert res.returncode == 1 and "nichts geändert" in res.stdout
    # b) Programm lässt sich nicht laden
    shutil.copy(ROOT / "requirements.txt", src / "requirements.txt")
    (src / "app" / "main.py").write_text("das ist kein python(\n")
    res = run(src, env)
    assert res.returncode == 1 and "lässt sich nicht laden" in res.stdout
    after = sorted(str(p.relative_to(r)) for p in r.rglob("*"))
    assert before == after and (dest / "app" / "main.py").read_text() == f'APP_VERSION = "{OLD}"\n'


def test_double_rollback_toggles_consistently_and_rerun_keeps_backup(fake, tmp_path):
    r, dest, env = fake
    src = project_copy(tmp_path)
    assert run(src, env).returncode == 0
    new = (src / "VERSION").read_text().strip()
    assert not list(src.rglob("__pycache__")), "Probelauf darf nichts in den Projektordner schreiben"
    # dieselbe Version nochmals: Sicherung der VORHERIGEN Version bleibt
    res = run(src, env)
    assert res.returncode == 0 and "schon installiert" in res.stdout
    assert (r / "opt/festpos.alt/app/main.py").read_text() == f'APP_VERSION = "{OLD}"\n'
    # zweimal --zurueck: alt, dann wieder neu – nie eine Mischung
    assert run(src, env, "--zurueck", "--ja").returncode == 0
    assert not (dest / "VERSION").exists() and not (dest / "app/core.py").exists()
    assert (r / "usr/local/sbin/festpos-rqlited").read_text() == "alt-rqlited\n"
    assert not (r / "usr/local/sbin/festpos-zertifikate").exists()
    res = run(src, env, "--zurueck", "--ja")
    assert res.returncode == 0 and f"Server läuft wieder mit Version {new}" in res.stdout
    assert (dest / "VERSION").read_text().strip() == new and (dest / "app/core.py").exists()
    assert (r / "usr/local/sbin/festpos-zertifikate").exists()
    assert (r / "opt/festpos.alt/app/main.py").read_text() == f'APP_VERSION = "{OLD}"\n'


def test_venv_swap_on_rollback_and_relocated_scripts(fake):
    """Neue Pakete: alte venv wandert in die Sicherung; zurück tauscht sie wieder (Startskripte passen)."""
    import subprocess as sp
    r, dest, env = fake
    (dest / "venv" / ".festpos-requirements").write_text("alt\n")           # erzwingt neue venv
    (dest / "venv" / "bin" / "alt-marke").write_text("x")
    script = ROOT / "deploy" / "install-lib.sh"
    # festpos_build_venv ersetzen (kein Internet / keine x86-Wheels im Test): venv mit Startskript anlegen
    shim = f'''source "{script}"
festpos_build_venv() {{ mkdir -p "$2/bin"; ln -s "{sys.executable}" "$2/bin/python";
  printf '#!%s/bin/python\\nprint(1)\\n' "$2" > "$2/bin/pip"; chmod +x "$2/bin/pip";
  festpos_req_hash "$1/requirements.txt" > "$2/.festpos-requirements"; }}'''
    res = sp.run(["bash", "-c", shim + '; source /dev/stdin'], input=open(ROOT / "deploy/update.sh").read()
                 .replace('source "$SRC/deploy/install-lib.sh"', ":").replace('SRC=$(cd "$(dirname "$0")/.." && pwd)', f'SRC="{ROOT}"'),
                 env=env, capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr
    assert (r / "opt/festpos.alt/venv/bin/alt-marke").exists()
    pip = (dest / "venv" / "bin" / "pip").read_text().splitlines()[0]
    assert pip == f"#!{dest}/venv/bin/python", pip                          # nach dem Umbenennen angepasst
    assert run(ROOT, env, "--zurueck", "--ja").returncode == 0
    assert (dest / "venv" / "bin" / "alt-marke").exists()
    assert (r / "opt/festpos.alt/venv/bin/pip").read_text().splitlines()[0] == f"#!{r}/opt/festpos.alt/venv/bin/python"


def test_installed_copy_of_update_sh_switches_back_without_project_folder(fake, tmp_path):
    """Ab 2026.10.10-26 liegt update.sh auch in /opt/festpos/deploy: «Zurück» geht ohne Projektordner
    (so macht es auch festpos-update). Die Kopie gehört zur Version und wechselt beim Zurückschalten mit."""
    r, dest, env = fake
    src = project_copy(tmp_path)
    assert run(src, env).returncode == 0
    assert (dest / "deploy" / "update.sh").read_text() == (ROOT / "deploy" / "update.sh").read_text()
    assert (dest / "deploy" / "install-lib.sh").exists() and not (dest / "deploy" / "wheels").exists()
    assert (r / "etc/udev/rules.d/99-festpos-usb.rules").exists()
    assert (r / "usr/local/sbin/festpos-update").stat().st_mode & 0o111
    shutil.rmtree(src)
    res = subprocess.run(["bash", str(dest / "deploy" / "update.sh"), "--zurueck", "--ja"], env=env,
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr
    assert f"Server läuft wieder mit Version {OLD}" in res.stdout
    assert not (dest / "deploy").exists()                        # die alte Version hatte keine Kopie
    assert not (r / "etc/udev/rules.d/99-festpos-usb.rules").exists() and not (r / "usr/local/sbin/festpos-update").exists()
    assert (r / "opt/festpos.alt/deploy/update.sh").exists()     # … die neue liegt jetzt in der Sicherung


def _rq_package(src: Path, version: str, sha: str | None = None) -> None:
    """Paket mit rqlite (wie tools/pakete.py): deploy/rqlite.env und das Archiv mit einem rqlited, das seine Version nennt."""
    import io
    import tarfile
    for old in (src / "deploy").glob("rqlite-v*-linux-arm64.tar.gz"):
        old.unlink()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, body in (("rqlited", f"#!/bin/sh\necho 'rqlited {version} linux arm64'\n"), ("rqlite", "#!/bin/sh\n")):
            data = body.encode()
            info = tarfile.TarInfo(f"rqlite-{version}-linux-arm64/{name}")
            info.size, info.mode = len(data), 0o755
            tf.addfile(info, io.BytesIO(data))
    tgz = src / "deploy" / f"rqlite-{version}-linux-arm64.tar.gz"
    tgz.write_bytes(buf.getvalue())
    (src / "deploy" / "rqlite.env").write_text(
        f"RQLITE_VERSION={version}\nRQLITE_SHA256={sha or hashlib.sha256(tgz.read_bytes()).hexdigest()}\n")


def _rq_installed(r: Path, version: str) -> Path:
    b = r / "usr" / "local" / "bin" / "rqlited"
    b.parent.mkdir(parents=True, exist_ok=True)
    b.write_text(f"#!/bin/sh\necho 'rqlited {version} linux arm64'\n")
    b.chmod(0o755)
    return b


def test_update_brings_newer_rqlite_and_rollback_takes_it_back(fake, tmp_path):
    """rqlite aus dem Update-Paket: nur, wenn neuer; dann wird rqlited neu gestartet; «Zurück» nimmt es zurück."""
    r, dest, env = fake
    b = _rq_installed(r, "v10.5.3")
    src = project_copy(tmp_path)
    _rq_package(src, "v10.6.0")
    res = run(src, env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "rqlite v10.6.0 installiert (vorher v10.5.3)" in res.stdout and "v10.6.0" in b.read_text()
    assert "rqlited wird neu gestartet" in res.stdout
    assert "v10.5.3" in (r / "opt/festpos.alt/system/usr/local/bin/rqlited").read_text()      # mitgesichert
    assert run(src, env, "--zurueck", "--ja").returncode == 0
    assert "v10.5.3" in b.read_text()


@pytest.mark.parametrize("have, pkg, bad_sha", [("v10.6.0", "v10.5.3", False),    # nie zurück auf älter
                                                ("v10.5.3", "v10.5.3", False),    # gleich: nichts
                                                ("v10.5.3", "v11.0.0", False),    # neue Hauptversion: nur von Hand
                                                ("v10.5.3", "v10.6.0", True)])    # Prüfsumme falsch
def test_update_keeps_rqlite_when_not_newer_or_not_safe(fake, tmp_path, have, pkg, bad_sha):
    r, dest, env = fake
    b = _rq_installed(r, have)
    src = project_copy(tmp_path)
    _rq_package(src, pkg, sha="0" * 64 if bad_sha else None)
    res = run(src, env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert have in b.read_text() and "installiert (vorher" not in res.stdout
    assert ("Hauptversion" in res.stdout) is (pkg == "v11.0.0") and ("Prüfsumme" in res.stdout) is bad_sha
