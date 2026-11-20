#!/usr/bin/env python3
"""requirements.txt (exakte Versionen + Prüfsummen) aus requirements.in neu erzeugen (REV-031).

Braucht Internet (PyPI). Ablauf:
    python3 tools/pin-requirements.py            # löst auf, schreibt requirements.txt

Die Pakete für den Pi (aarch64) holt tools/pakete.py beim Bauen von ZIP und Image (mit diesen Prüfsummen).

Aufgelöst wird in einer frischen venv mit dem Python, das dieses Skript ausführt (für den Pi: Python 3.13).
Aufgeführt werden nur Dateien, die für Linux (aarch64 = Pi, x86_64 = Tests/CI) mit Python 3.13 passen.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = "3.13"
PLATFORMS = ["manylinux2014_aarch64", "manylinux_2_17_aarch64", "manylinux_2_28_aarch64", "manylinux_2_34_aarch64"]


def wanted(filename: str) -> bool:
    if filename.endswith((".tar.gz", ".zip")):
        return True                                            # Quellpaket
    m = re.match(r".+-(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^-]+)\.whl$", filename)
    if not m:
        return False
    py, abi, plat = m["py"], m["abi"], m["plat"]
    if plat == "any":
        return True
    if not re.search(r"(many)linux.*_(aarch64|x86_64)", plat):
        return False
    return "cp313" in py or abi == "abi3" or py.startswith("py3")


def main() -> None:
    req_in = ROOT / "requirements.in"
    with tempfile.TemporaryDirectory() as tmp:
        venv = Path(tmp) / "v"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        pip = str(venv / "bin" / "pip")
        subprocess.run([pip, "install", "-q", "--upgrade", "pip"], check=True)
        subprocess.run([pip, "install", "-q", "-r", str(req_in)], check=True)
        frozen = subprocess.run([pip, "freeze"], check=True, capture_output=True, text=True).stdout.split()
    extras = {}
    for line in req_in.read_text().splitlines():
        m = re.match(r"([A-Za-z0-9_.-]+)(\[[^\]]+\])?==", line.strip())
        if m and m[2]:
            extras[m[1].lower()] = m[2]
    out = ["# FestPOS – exakte Versionen mit Prüfsummen. NICHT von Hand ändern:",
           "# requirements.in anpassen und  python3 tools/pin-requirements.py  ausführen.",
           f"# Python {PY} (Raspberry Pi OS Trixie); Dateien für Linux aarch64 (Pi) und x86_64 (Tests/CI).", ""]
    for line in sorted(frozen, key=str.lower):
        name, ver = line.split("==")
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{ver}/json", timeout=30) as r:
            files = json.load(r)["urls"]
        hashes = sorted({f["digests"]["sha256"] for f in files if wanted(f["filename"])})
        if not hashes:
            sys.exit(f"Keine passende Datei für {name}=={ver}")
        out.append(f"{name}{extras.get(name.lower(), '')}=={ver} \\")
        out += [f"    --hash=sha256:{h} \\" for h in hashes[:-1]]
        out.append(f"    --hash=sha256:{hashes[-1]}")
    (ROOT / "requirements.txt").write_text("\n".join(out) + "\n")
    print(f"requirements.txt: {len(frozen)} Pakete")


if __name__ == "__main__":
    main()
