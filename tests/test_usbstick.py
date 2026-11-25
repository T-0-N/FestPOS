"""USB-Stick kurz einbinden (app/usbstick.py): auch wenn sync hängt oder umount scheitert, wird ausgehängt (notfalls
«umount -l») – der Fehler von sync bleibt. Geprüft über first_stick selbst (nicht nur _release)."""
from __future__ import annotations

import subprocess

import pytest

from app import usbstick


def test_release_after_sync_timeout_and_umount_error(tmp_path, monkeypatch):
    monkeypatch.setattr(usbstick, "MNT", tmp_path)
    monkeypatch.setattr(usbstick, "candidates", lambda: ["sda1"])
    mnt = tmp_path / "sda1"
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "blkid":
            return subprocess.CompletedProcess(cmd, 0, stdout="vfat\n")
        if cmd[0] == "mount":
            (mnt / "offen").write_text("x")             # «eingehängt»: rmdir scheitert, bis wirklich ausgehängt
            return subprocess.CompletedProcess(cmd, 0)
        if cmd == ["sync"]:
            raise subprocess.TimeoutExpired(cmd, 60)
        if cmd[:1] == ["umount"] and "-l" not in cmd:
            raise OSError("umount kaputt")
        if "-l" in cmd:
            (mnt / "offen").unlink()
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(usbstick.subprocess, "run", fake_run)
    with pytest.raises(subprocess.TimeoutExpired):
        with usbstick.first_stick() as (dev, path):
            assert (dev, path) == ("sda1", mnt)
    assert ["umount", "-l", str(mnt)] in calls and not mnt.exists()
