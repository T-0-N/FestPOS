#!/bin/bash
# FestPOS – Entwicklungsumgebung einrichten (Linux x86_64): Python-Pakete, shellcheck, rqlited, Chromium für die
# Browser-Tests. Macht nur, was noch fehlt; mehrmals ausführen schadet nicht.
#
#   tools/setup-dev.sh           (vorher venv aktivieren: python3.13 -m venv .venv && . .venv/bin/activate)
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
RQ_VERSION=10.5.3
RQ_SHA256=93dc04417d9d233e79eb359e325793b1f92daa84ec93694953f4bff6307fda2b   # wie .github/workflows/ci.yml
pip_flags=()
[ -z "${VIRTUAL_ENV:-}" ] && [ "$(id -u)" = 0 ] && pip_flags=(--break-system-packages)

echo "==> Python-Pakete"
python3 -m pip install -q "${pip_flags[@]}" --require-hashes -r requirements.txt
python3 -m pip install -q "${pip_flags[@]}" -r requirements-dev.txt

echo "==> shellcheck"
if ! command -v shellcheck >/dev/null; then
  if [ "$(id -u)" = 0 ]; then
    apt-get install -y -qq shellcheck >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq shellcheck; }
  else
    echo "fehlt – bitte installieren: sudo apt install shellcheck"
  fi
fi

echo "==> rqlited $RQ_VERSION (für die Cluster-Tests)"
if ! command -v rqlited >/dev/null && [ -z "${RQLITED:-}" ]; then
  ziel=/usr/local/bin
  [ -w "$ziel" ] || { ziel="$HOME/.local/bin"; mkdir -p "$ziel"; }
  tmp=$(mktemp -d)
  if curl -fsSL -o "$tmp/rq.tgz" \
      "https://github.com/rqlite/rqlite/releases/download/v$RQ_VERSION/rqlite-v$RQ_VERSION-linux-amd64.tar.gz" &&
     echo "$RQ_SHA256  $tmp/rq.tgz" | sha256sum -c --quiet; then
    tar xzf "$tmp/rq.tgz" -C "$tmp" && install -m 755 "$tmp/rqlite-v$RQ_VERSION-linux-amd64/rqlited" "$ziel/rqlited"
    echo "installiert: $ziel/rqlited"
  else
    echo "Download fehlgeschlagen – Cluster-Tests mit tools/check.sh --schnell überspringen"
  fi
  rm -rf "$tmp"
fi

echo "==> Chromium für Playwright"
if ! python3 - <<'PY' 2>/dev/null
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    p.chromium.launch().close()
PY
then
  if [ "$(id -u)" = 0 ]; then
    python3 -m playwright install --with-deps chromium
  else
    python3 -m playwright install chromium
  fi
fi
echo "==> fertig – prüfen mit: tools/check.sh"
