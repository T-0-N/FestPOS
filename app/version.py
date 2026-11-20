"""Die Versionsnummer steht nur an EINER Stelle: in der Datei VERSION (REV-052).
Server, App (app.js) und Service Worker (sw.js) lesen sie von hier."""
from __future__ import annotations

from pathlib import Path


def _read() -> str:
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip() or "unbekannt"
    except OSError:
        return "unbekannt"


APP_VERSION = _read()
