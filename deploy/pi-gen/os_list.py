"""Eintrag für den Raspberry Pi Imager (T12): mit dieser Liste ist FestPOS im Imager direkt auswählbar
(Imager › Betriebssystem › «Benutzerdefiniert» bzw. Start mit --repo <URL der Liste>).

  python3 deploy/pi-gen/os_list.py festpos-<VERSION>.img.xz <VERSION> [URL-Basis] [TAG]  -> JSON auf stdout

Die Datei liegt am Release zum Tag (Standard v<VERSION>); dauerhaft erreichbar ist die Liste des neusten Release unter
https://github.com/T-0-N/FestPOS/releases/latest/download/festpos-os-list.json (für den Imager).
"""
from __future__ import annotations

import hashlib
import json
import lzma
import sys
import time
from pathlib import Path

REPO = "https://github.com/T-0-N/FestPOS/releases/download"


def _sizes(img: Path) -> tuple[int, str, int, str]:
    """Grösse und SHA256 der .xz-Datei und des entpackten Images – stückweise (das Image hat mehrere GB)."""
    packed, raw, dec = hashlib.sha256(), hashlib.sha256(), lzma.LZMADecompressor()
    n_packed = n_raw = 0
    with open(img, "rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            packed.update(chunk)
            n_packed += len(chunk)
            out = dec.decompress(chunk)
            raw.update(out)
            n_raw += len(out)
    return n_packed, packed.hexdigest(), n_raw, raw.hexdigest()


def entry(img: Path, version: str, base: str | None = None, tag: str | None = None) -> dict:
    n_packed, sha_packed, n_raw, sha_raw = _sizes(img)
    return {"name": f"FestPOS {version}",
            "description": "Bestellaufnahme und Bondruck – Raspberry Pi OS Lite (64-bit) mit FestPOS. "
                           "Nach dem Start im Browser einrichten.",
            "url": f"{base or REPO}/{tag or 'v' + version}/{img.name}",
            "icon": "https://raw.githubusercontent.com/T-0-N/FestPOS/main/static/icons/icon-192.png",
            "release_date": time.strftime("%Y-%m-%d"),
            "extract_size": n_raw, "extract_sha256": sha_raw,
            "image_download_size": n_packed, "image_download_sha256": sha_packed,
            "devices": ["pi5-64bit", "pi4-64bit"], "init_format": "systemd"}


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    print(json.dumps({"os_list": [entry(Path(argv[1]), argv[2], argv[3] if len(argv) > 3 and argv[3] else None,
                                           argv[4] if len(argv) > 4 and argv[4] else None)]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
