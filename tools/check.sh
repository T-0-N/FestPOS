#!/bin/bash
# FestPOS – alle Prüfungen wie in der CI: Lint (ruff, shellcheck, JavaScript-Syntax), Sprachdateien und Tests (pytest).
#
#   tools/check.sh            alles
#   tools/check.sh --schnell  ohne die langsamen Cluster-Tests mit echtem rqlite
#
# Voraussetzungen: pip install --require-hashes -r requirements.txt && pip install -r requirements-dev.txt,
# ausserdem die Programme shellcheck, node (JS-Syntax) und openssl; rqlited im PATH oder RQLITED=/pfad.
set -euo pipefail
cd "$(dirname "$0")/.."
fehler=0
schritt() { echo; echo "==> $*"; }

schritt "ruff"
ruff check app tests tools deploy/festpos-rqcheck.py deploy/festpos-redirect.py deploy/festpos-display deploy/festpos-settime \
  deploy/festpos-update deploy/festpos-sicherung deploy/festpos-wiederherstellen deploy/festpos-handy-usb deploy/festpos-karte deploy/pi-gen/os_list.py \
  deploy/festpos-druckernetz.py || fehler=1

schritt "shellcheck"
if command -v shellcheck >/dev/null; then
  shellcheck -S warning deploy/*.sh deploy/festpos-rqlited deploy/festpos-notfall deploy/festpos-zuruecksetzen \
    deploy/festpos-zertifikate deploy/festpos-haertung tools/*.sh deploy/pi-gen/*.sh deploy/pi-gen/stage-festpos/*.sh \
    deploy/pi-gen/stage-festpos/*/*.sh || fehler=1
else
  echo "shellcheck fehlt – übersprungen"; fehler=1
fi

schritt "Sprachdateien"
python3 tools/i18n_check.py || fehler=1

schritt "JavaScript-Syntax"
if command -v node >/dev/null; then
  for f in static/*.js static/app/*.js; do node --check "$f" || fehler=1; done
  # Skripte einer Seite teilen sich die globalen Namen (t, $ …): zusammen prüfen, sonst fällt eine doppelte
  # Deklaration erst im Browser auf
  app_skripte=$(grep -o '<script src="[^"]*"' static/index.html | sed 's/<script src="//; s/"$//' | tr '\n' ' ')
  for seite in "$app_skripte" "status-frame.js i18n.js status.js"; do
    (cd static && cat $seite) | node --check --input-type=commonjs - || { echo "Seite: $seite"; fehler=1; }
  done
  # Verwaltung: ES-Module (jedes hat seinen eigenen Namensraum)
  for f in static/admin/*.js; do node --check --input-type=module - < "$f" || { echo "Modul: $f"; fehler=1; }; done
  for f in static/*.html; do         # eingebettete Skripte
    python3 - "$f" <<'PY' | node --check --input-type=commonjs - || fehler=1
import re, sys
print("\n".join(re.findall(r"<script>(.*?)</script>", open(sys.argv[1], encoding="utf-8").read(), re.S)))
PY
  done
else
  echo "node fehlt – übersprungen"
fi
# Imports und Exports der Verwaltung (vergessener Import, Zuweisung an einen importierten Namen …)
python3 tools/js_module_check.py static/admin || fehler=1

# Nur im Entwicklungs-Repo (dort gibt es .oeffentlich/): nichts Privates in Dateien, die veröffentlicht werden
if [ -f .oeffentlich/pruefen.py ]; then
  schritt "Veröffentlichung"
  python3 .oeffentlich/pruefen.py || fehler=1
fi

schritt "pytest"
if [ "${1:-}" = --schnell ]; then
  python3 -m pytest -q -k "not cluster and not rqlited_wrapper and not rqlite" || fehler=1
else
  python3 -m pytest -q || fehler=1
fi

echo
if [ "$fehler" = 0 ]; then echo "ALLES OK"; else echo "FEHLER (siehe oben)"; fi
exit "$fehler"
