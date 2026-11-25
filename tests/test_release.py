"""Version 2026.10.10-26: Signatur und Aufbau von Update-Paketen (app/release.py).

Signiert wird am PC mit «ssh-keygen -Y sign» (in Windows schon dabei). Geprüft wird hier auch gegen das echte
ssh-keygen in beide Richtungen, falls es installiert ist."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app import release
from tests.release_util import ROOT, build_bundle, keypair, sign


def test_version_order():
    assert release.newer("2026.10.10-26", "2026.10.10-25")
    assert release.newer("2026.10.10-100", "2026.10.10-99")          # Zahlen, nicht Text
    assert release.newer("2026.11.1-1", "2026.10.10-99")
    assert not release.newer("2026.10.10-25", "2026.10.10-25")
    assert not release.newer("2026.10.10-24", "2026.10.10-25")
    assert release.newer("2026.10.10-1", "unbekannt") and not release.newer("unbekannt", "2026.10.10-1")
    assert release.version_key("2026.10.10-26 ") == (0, 2026, 10, 10, 26, 0) and release.version_key("26") is None


def test_version_order_from_1_0():
    """Ab 1.0: MAJOR.MINOR.PATCH, Testversionen -rc.N. Jede Version ab 1.0 ist neuer als das frühere Format – auch
    eine Testversion, damit die Brückenversion 2026.10.10-102 den Probelauf 1.0.0-rc.1 annimmt."""
    assert release.newer("1.0.0", "2026.10.10-102") and release.newer("1.0.0-rc.1", "2026.12.31-9999")
    assert not release.newer("2026.10.10-103", "1.0.0")
    assert release.newer("1.0.0", "1.0.0-rc.3") and release.newer("1.0.0-rc.3", "1.0.0-rc.2")
    assert release.newer("1.0.10", "1.0.9") and release.newer("1.10.0", "1.9.99") and release.newer("2.0.0", "1.99.0")
    assert release.newer("1.1.0-rc.1", "1.0.5") and not release.newer("1.0.0-rc.9", "1.0.0")
    assert not release.newer("1.0.0", "1.0.0")
    for bad in ("1.0", "v1.0.0", "01.0.0", "1.0.0-rc.0", "1.0.0-beta.1", "1.0.0-rc.1x", "1.0.0 2"):
        assert release.version_key(bad) is None, bad
    assert release.is_prerelease("1.1.0-rc.2") and not release.is_prerelease("1.1.0")
    assert not release.is_prerelease("2026.10.10-102")


def test_sign_and_verify_with_python(tmp_path):
    pk, line = keypair()
    f = tmp_path / "x.zip"
    f.write_bytes(b"inhalt" * 1000)
    sig = release.sign(f, pk)
    key = release.parse_key_line(line)
    assert release.verify(f, sig, [key]).fp == key.fp
    assert release.verify(f, release.sign(f, pk, hash_alg="sha256"), [key])
    f.write_bytes(b"inhalt" * 999 + b"anders")                       # Datei verändert
    with pytest.raises(release.ReleaseError) as e:
        release.verify(f, sig, [key])
    assert e.value.code == "bad_signature"


def test_wrong_key_namespace_and_garbage(tmp_path):
    pk, line = keypair()
    other, other_line = keypair("Fremd")
    f = tmp_path / "x.zip"
    f.write_bytes(b"x")
    for sig, keys, code in ((release.sign(f, other), [release.parse_key_line(line)], "unknown_key"),
                            (release.sign(f, pk, namespace="file"), [release.parse_key_line(line)], "sig_namespace"),
                            (release.sign(f, pk), [], "no_key"),
                            (b"-----BEGIN SSH SIGNATURE-----\nAAAA\n-----END SSH SIGNATURE-----\n", [release.parse_key_line(line)], "sig_format"),
                            (b"irgendwas", [release.parse_key_line(line)], "sig_format")):
        with pytest.raises(release.ReleaseError) as e:
            release.verify(f, sig, keys)
        assert e.value.code == code
    with pytest.raises(release.ReleaseError) as e:
        release.verify(f, release.sign(f, other), [release.parse_key_line(line)])
    assert e.value.kw["fp"] == release.parse_key_line(other_line).fp       # zeigt, welcher Schlüssel unterschrieb


def test_key_files(tmp_path):
    _, a = keypair("A")
    _, b = keypair("B")
    (tmp_path / "festpos-update.pub").write_text(a + "\n\n# Kommentar\n")
    (tmp_path / "festpos-update-neu.pub").write_text(b + "\n" + a + "\n")      # doppelt zählt einmal
    (tmp_path / "festpos-update-rsa.pub").write_text("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ nein\n")
    (tmp_path / "anderes.pub").write_text(b)                                    # anderer Name: nicht beachtet
    keys, problems = release.load_keys(tmp_path)
    assert sorted(k.comment for k in keys) == ["A", "B"]
    assert len(problems) == 1 and "ssh-ed25519" in problems[0]
    os.symlink(tmp_path / "festpos-update.pub", tmp_path / "festpos-update-link.pub")
    _, problems = release.load_keys(tmp_path)
    assert any("link" in p for p in problems)                                    # Verknüpfungen werden nicht gelesen
    assert [k.fp for k in release.keys_from_public([k.public() for k in keys])] == [k.fp for k in keys]


@pytest.mark.skipif(not shutil.which("ssh-keygen"), reason="ssh-keygen fehlt")
def test_interop_with_ssh_keygen(tmp_path):
    """Am PC signiert ssh-keygen – das muss hier gelten; und was hier signiert wird, prüft auch ssh-keygen."""
    k = tmp_path / "festpos-update"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "FestPOS Update", "-f", str(k)], check=True)
    f = tmp_path / "festpos.zip"
    f.write_bytes(os.urandom(300_000))
    subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(k), "-n", "festpos-update", str(f)], check=True,
                   capture_output=True)
    keys, problems = release.load_keys(tmp_path)
    assert not problems and len(keys) == 1
    fp = subprocess.run(["ssh-keygen", "-l", "-f", str(k) + ".pub"], capture_output=True, text=True).stdout.split()[1]
    assert release.verify(f, (tmp_path / "festpos.zip.sig").read_bytes(), keys).fp == fp
    # andersrum
    pk, line = keypair()
    (tmp_path / "py.sig").write_bytes(release.sign(f, pk))
    (tmp_path / "allowed").write_text("festpos " + " ".join(line.split()[:2]) + "\n")
    with open(f, "rb") as data:
        r = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(tmp_path / "allowed"), "-I", "festpos", "-n",
                            "festpos-update", "-s", str(tmp_path / "py.sig")], stdin=data, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_bundle_check_and_extract(tmp_path):
    pk, line = keypair()
    z = build_bundle(tmp_path / "festpos.zip", "99.0.0")
    info = release.check_bundle(z, sign(z, pk).read_bytes(), [release.parse_key_line(line)])
    assert info["version"] == "99.0.0" and info["signer"]["comment"] == "Test Update"
    assert info["sha256"] == release.sha256_file(z)
    root = release.extract_zip(z, tmp_path / "aus")
    assert root.name == "festpos" and (root / "app" / "main.py").is_file()
    assert os.stat(root / "deploy" / "festpos-update").st_mode & stat.S_IXUSR             # Ausführungsrecht bleibt
    assert not os.stat(root / "VERSION").st_mode & stat.S_IXUSR
    with pytest.raises(release.ReleaseError):                                           # Ziel muss leer sein
        release.extract_zip(z, tmp_path / "aus")


def test_bundle_without_top_folder(tmp_path):
    z = build_bundle(tmp_path / "flach.zip", "99.0.1", prefix="")
    assert release.inspect_zip(z)["root"] == ""
    assert (release.extract_zip(z, tmp_path / "aus") / "VERSION").read_text().strip() == "99.0.1"


@pytest.mark.parametrize("name, code", [("festpos/../../etc/passwd", "zip_unsafe"), ("/etc/passwd", "zip_unsafe"),
                                        ("festpos\\..\\x", "zip_unsafe"), ("C:/x", "zip_unsafe")])
def test_unsafe_names_are_refused(tmp_path, name, code):
    z = build_bundle(tmp_path / "b.zip", "99.0.2", extra={name: b"boese"})
    with pytest.raises(release.ReleaseError) as e:
        release.inspect_zip(z)
    assert e.value.code == code


def test_windows_line_endings_are_refused(tmp_path):
    """Gebaut unter Windows mit core.autocrlf=true: update.sh endet sofort mit «invalid option name» (Rückgabe 2)
    – das Paket soll gar nicht erst verteilt werden. CSV-Vorlagen (für Excel CRLF) und Binärdateien sind erlaubt."""
    z = build_bundle(tmp_path / "crlf.zip", "99.0.2", extra={"festpos/deploy/neu.sh": b"#!/bin/bash\r\nset -e\r\n"})
    with pytest.raises(release.ReleaseError) as e:
        release.inspect_zip(z)
    assert e.value.code == "crlf" and e.value.kw["name"] == "festpos/deploy/neu.sh"
    ok = build_bundle(tmp_path / "ok.zip", "99.0.2", extra={"festpos/x.csv": b"a;b\r\n1;2\r\n",
                                                                  "festpos/x.bin": b"\0\r\n"})
    assert release.inspect_zip(ok)["version"] == "99.0.2"
    assert (ROOT / "static" / "preisliste-vorlage.csv").read_bytes().count(b"\r\n") > 0    # Vorlage bleibt CRLF


def test_build_zip_refuses_windows_line_endings(tmp_path, monkeypatch):
    import tools.build_zip as bz
    src = tmp_path / "projekt"
    (src / "deploy").mkdir(parents=True)
    (src / "deploy" / "update.sh").write_bytes(b"#!/bin/bash\r\nset -euo pipefail\r\n")
    (src / "vorlage.csv").write_bytes(b"a;b\r\n")
    (src / "VERSION").write_text("99.0.5\n")
    monkeypatch.setattr(bz, "ROOT", src)                                 # ohne .git: alle Dateien im Ordner
    monkeypatch.setattr(sys, "argv", ["build_zip.py", str(tmp_path / "out.zip")])
    with pytest.raises(SystemExit) as e:
        bz.main()
    assert "deploy/update.sh" in str(e.value) and "vorlage.csv" not in str(e.value)
    assert not (tmp_path / "out.zip").exists()
    (src / "deploy" / "update.sh").write_bytes(b"#!/bin/bash\nset -euo pipefail\n")
    bz.main()                                                            # mit LF: Paket entsteht
    assert (tmp_path / "out.zip").exists()


def test_symlink_and_incomplete_and_bad_version(tmp_path):
    z = tmp_path / "link.zip"
    build_bundle(z, "99.0.3")
    with zipfile.ZipFile(z, "a") as zz:
        info = zipfile.ZipInfo("festpos/static/boese")
        info.external_attr = (0o120777 & 0xFFFF) << 16
        zz.writestr(info, "/etc/shadow")
    with pytest.raises(release.ReleaseError) as e:
        release.inspect_zip(z)
    assert e.value.code == "zip_unsafe"
    leer = tmp_path / "leer.zip"
    with zipfile.ZipFile(leer, "w") as zz:
        zz.writestr("festpos/VERSION", "99.0.4")
    with pytest.raises(release.ReleaseError) as e:
        release.inspect_zip(leer)
    assert e.value.code == "zip_incomplete" and "deploy/update.sh" in e.value.kw["missing"]
    z2 = build_bundle(tmp_path / "v.zip", "neueste")
    with pytest.raises(release.ReleaseError) as e:
        release.inspect_zip(z2)
    assert e.value.code == "version_bad"
    (tmp_path / "kein.zip").write_bytes(b"PK kaputt")
    with pytest.raises(release.ReleaseError) as e:
        release.inspect_zip(tmp_path / "kein.zip")
    assert e.value.code == "zip_bad"


def test_read_regular_refuses_links_and_pipes(tmp_path):
    (tmp_path / "echt").write_bytes(b"x")
    os.symlink(tmp_path / "echt", tmp_path / "link")
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(OSError):
        release.read_regular(tmp_path / "link", 10)
    with pytest.raises(release.ReleaseError):
        release.read_regular(tmp_path / "pipe", 10)                 # blockiert nicht
    with pytest.raises(release.ReleaseError):
        release.read_regular(tmp_path / "echt", 0)
    assert release.copy_regular(tmp_path / "echt", tmp_path / "kopie") == release.sha256_file(tmp_path / "echt")
    with pytest.raises(OSError):
        release.copy_regular(tmp_path / "echt", tmp_path / "kopie")   # Ziel existiert schon: nie überschreiben
    assert Path(tmp_path / "kopie").read_bytes() == b"x"


def test_build_zip_refuses_when_tracked_files_are_missing(tmp_path, monkeypatch):
    """Fehlt im Ordner eine Datei, die Git kennt (z.B. app/uplink.py), bricht build_zip ab – sonst entstünde ein
    Paket, das auf dem Pi nicht lädt."""
    import shutil
    import subprocess
    import sys

    import tools.build_zip as bz
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    for name in ("__init__.py", "uplink.py"):
        (repo / "app" / name).write_text("")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    (repo / "app" / "uplink.py").unlink()
    monkeypatch.setattr(bz, "ROOT", repo)
    monkeypatch.setattr(sys, "argv", ["build_zip.py", str(tmp_path / "out.zip")])
    with pytest.raises(SystemExit) as e:
        bz.main()
    assert "fehlen im Ordner" in str(e.value) and "app/uplink.py" in str(e.value)
    assert not (tmp_path / "out.zip").exists()
    shutil.rmtree(repo)


def test_several_signatures_and_revoked_keys(tmp_path):
    """Schlüsselwechsel (T-0-N/FestPOS#13): mehrere Signaturen in einer .sig-Datei, eine gültige genügt;
    zurückgezogene Schlüssel gelten nie."""
    from tests.release_util import build_bundle, keypair
    z = build_bundle(tmp_path / "f.zip", "99.0.0")
    old_pk, old_line = keypair("alt")
    new_pk, new_line = keypair("neu")
    sig = release.sign(z, new_pk) + release.sign(z, old_pk)
    old_keys = release.parse_keys(old_line)[0]
    assert release.verify(z, sig, old_keys).comment == "alt"                    # nur der alte bekannt
    assert release.verify(z, sig, release.parse_keys(new_line)[0]).comment == "neu"
    with pytest.raises(release.ReleaseError) as e:
        release.verify(z, sig, release.parse_keys(keypair("fremd")[1])[0])
    assert e.value.code == "unknown_key"
    (tmp_path / "festpos-update.pub").write_text(old_line + "\n" + new_line + "\n")
    (tmp_path / release.REVOKED_FILE).write_text(f"{old_keys[0].fp}  alt\nkein fingerabdruck\n")
    keys, problems = release.load_keys(tmp_path)
    assert [k.comment for k in keys] == ["neu"] and "zurückgezogen" in problems[0]
    # Liste da, aber nicht lesbar (Verknüpfung, zu gross): kein Schlüssel gilt – nie ein zurückgezogener wieder
    (tmp_path / release.REVOKED_FILE).unlink()
    (tmp_path / "anderswo.txt").write_text("")
    (tmp_path / release.REVOKED_FILE).symlink_to(tmp_path / "anderswo.txt")
    keys, problems = release.load_keys(tmp_path)
    assert keys == [] and "nicht lesbar" in problems[0]
    with pytest.raises(OSError):
        release.load_revoked(tmp_path, strict=True)
    (tmp_path / release.REVOKED_FILE).unlink()
    (tmp_path / release.REVOKED_FILE).write_text("x" * (release.MAX_KEYFILE + 1))
    assert release.load_keys(tmp_path)[0] == []
    (tmp_path / release.REVOKED_FILE).unlink()
    assert len(release.load_keys(tmp_path)[0]) == 2                             # fehlt: keine zurückgezogen
