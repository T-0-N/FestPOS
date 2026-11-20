"""Bestell-App aus mehreren Skripten (static/app.js + static/app/*.js): jede Datei wird geladen und ist im Service
Worker – fehlt eine in sw.js (SHELL), startet die App offline (z.B. während eines Failovers) nicht."""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


def _scripts() -> list[str]:
    return re.findall(r'<script src="([^"]+)"', (STATIC / "index.html").read_text(encoding="utf-8"))


def test_every_app_script_is_loaded_in_order():
    scripts = _scripts()
    assert scripts[:3] == ["common.js", "i18n.js", "app.js"]       # app.js zuerst: Version, Hilfen, Zustand
    assert scripts[-1] == "app/start.js"                             # Start zuletzt: alles andere ist dann geladen
    on_disk = {f"app/{p.name}" for p in (STATIC / "app").glob("*.js")}
    assert set(scripts[3:]) == on_disk, "jede Datei in static/app/ gehört in index.html (und umgekehrt)"
    assert len(scripts) == len(set(scripts))


def test_service_worker_caches_every_app_script():
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    shell = re.findall(r'"(/[^"]*)"', sw[sw.index("const SHELL"):sw.index("];")])
    app = [s for s in shell if s.endswith(".js")]
    assert app == ["/" + s for s in _scripts()], "SHELL in sw.js: dieselben Skripte wie index.html, gleiche Reihenfolge"


def test_every_app_script_is_strict():
    """«use strict» gilt nur für die eigene Datei: fehlt es, liefe dieser Teil der App im lockeren Modus."""
    for p in [STATIC / "app.js", *sorted((STATIC / "app").glob("*.js"))]:
        head = "\n".join(p.read_text(encoding="utf-8").split("\n")[:20])
        assert '\n"use strict";\n' in head, f"{p.name}: \"use strict\" fehlt"
