#!/bin/bash
# FestPOS – neue Version einspielen, OHNE Internet (anders als setup.sh).
# Ersetzt Programm, App, Python-Pakete, rqlite (wenn neuer), Skripte und Dienste. Daten (/var/lib/festpos), Konfiguration und
# Zertifikate (/boot/firmware) bleiben unverändert.
#
#   Am PC:  scp -r festpos pi@192.168.55.11:~/          (vorher auf dem Pi: rm -rf ~/festpos)
#   Pi:     sudo bash ~/festpos/deploy/update.sh
#           sudo bash /opt/festpos/deploy/update.sh --zurueck  zur Version in der Sicherung wechseln (fragt nach)
#
# Ab Version 2026.10.10-26 geht ein Update auch ohne diesen Weg: signiert über die Verwaltung (Update) oder mit
# einem USB-Stick – auf alle Pi verteilt von festpos-update (das dann dieses Skript aus dem Paket ausführt).
#
# Sicherheitsnetz (REV-032): Die laufende Version wird vorher nach /opt/festpos.alt gesichert. Neue
# Python-Pakete werden in einer eigenen Umgebung vorbereitet und die neue Version probeweise geladen, bevor
# irgendetwas umgeschaltet wird. Meldet der Server danach nicht die neue Version (oder läuft die App trotz
# gesunder Datenbank nicht an), wird automatisch auf die vorherige Version zurückgeschaltet.
# Im Cluster: einen Pi nach dem anderen, den druckenden (Leader) zuletzt – siehe ANLEITUNG.
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"   # mit «sh …» gestartet (z.B. ohne x-Recht): bash nehmen
set -euo pipefail
SRC=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck source=install-lib.sh
source "$SRC/deploy/install-lib.sh"
WAIT=${FESTPOS_UPDATE_WAIT:-90}
[ -n "$R" ] || [ "$(id -u)" = 0 ] || { echo "Bitte mit sudo ausführen"; exit 1; }

neu_starten() {                 # neu_starten [rqlited] -> Dienste mit der installierten Version starten
  systemctl daemon-reload
  [ "${1:-}" = rqlited ] && systemctl restart rqlited
  systemctl restart festpos festpos-redirect
  systemctl restart festpos-display.path || true
}

zurueck() {                     # Wechsel zur Version in /opt/festpos.alt (die laufende kommt dorthin)
  local alt jetzt
  alt=$(festpos_version "$ALT")
  jetzt=$(festpos_version "$DEST")
  echo "==> Schalte zurück auf Version ${alt:-?}"
  festpos_restore || return 1
  neu_starten "${1:-}"
  if festpos_wait_version "$alt" "$WAIT"; then
    echo "Zurückgeschaltet: Server läuft wieder mit Version $alt."
  else
    echo "WARNUNG: Auch die vorherige Version meldet sich nicht. Prüfen: sudo journalctl -u festpos -n 60"
  fi
  echo "    (Version ${jetzt:-?} liegt jetzt in der Sicherung – «update.sh --zurueck» würde wieder zu ihr wechseln.)"
}

if [ "${1:-}" = --zurueck ]; then
  [ -d "$ALT/app" ] || { echo "Keine Sicherung in $ALT – nichts zum Zurückschalten."; exit 1; }
  echo "Läuft jetzt: Version $(festpos_version "$DEST")   Sicherung: Version $(festpos_version "$ALT")"
  if [ "${2:-}" != --ja ]; then
    read -r -p "Zur Version in der Sicherung wechseln? 'JA' tippen: " a
    [ "$a" = JA ] || { echo "Abgebrochen."; exit 1; }
  fi
  zurueck rqlited
  exit 0
fi

[ -f "$SRC/app/main.py" ] && [ -d "$SRC/static" ] && [ -f "$SRC/VERSION" ] || { echo "Projekt unvollständig in $SRC"; exit 1; }
[ -x "$DEST/venv/bin/python" ] || { echo "$DEST/venv fehlt – zuerst einmal setup.sh (mit Internet) ausführen"; exit 1; }

NEW=$(festpos_version "$SRC")
OLD=$(festpos_version "$DEST")
echo "FestPOS ${OLD:-?} -> $NEW"
DB_VORHER=""; festpos_db_ok && DB_VORHER=1             # war die Datenbank vor dem Update gesund?

# 1. Python-Pakete: nur wenn sich requirements.txt geändert hat – in einer EIGENEN Umgebung vorbereiten
VENV_NEU=""
if festpos_venv_ok "$DEST/venv" "$SRC/requirements.txt"; then
  echo "==> Python-Pakete unverändert"
else
  echo "==> Python-Pakete vorbereiten (feste Versionen mit Prüfsumme, aus deploy/wheels)"
  if ! festpos_build_venv "$SRC" "$DEST/venv.neu"; then
    rm -rf "$DEST/venv.neu"
    echo "FEHLER: Python-Pakete konnten nicht installiert werden – nichts geändert, alte Version läuft weiter."
    exit 1
  fi
  VENV_NEU=1
fi

# 2. Probelauf: lässt sich die neue Version überhaupt laden?
PY=$DEST/venv/bin/python
[ -n "$VENV_NEU" ] && PY=$DEST/venv.neu/bin/python
# -B: keine __pycache__-Dateien (root) im Projektordner, sonst lässt er sich als «pi» nicht mehr löschen.
# app.serve lädt die ganze App und prüft zusätzlich die Anbindung an uvicorn.
if ! (cd "$SRC" && FESTPOS_STATIC=/nonexistent FESTPOS_DB=sqlite:///:memory: "$PY" -B -c "import app.serve" >/dev/null); then
  rm -rf "$DEST/venv.neu"
  echo "FEHLER: Die neue Version lässt sich nicht laden – nichts geändert, alte Version läuft weiter."
  exit 1
fi

# 3. Laufende Version sichern – nicht, wenn dieselbe Version nochmals eingespielt wird (sonst wäre die
#    Sicherung der VORHERIGEN Version weg)
SICHERN=1
if [ "$OLD" = "$NEW" ] && [ -d "$ALT/app" ]; then
  SICHERN=""
  echo "==> Version $NEW ist schon installiert – Sicherung der vorherigen Version ($(festpos_version "$ALT")) bleibt"
else
  echo "==> Sichere Version ${OLD:-?} nach $ALT"
  festpos_backup "$SRC"
fi

# 4. Umschalten
fingerabdruck() { cat "$@" 2>/dev/null | sha256sum || true; }      # fehlende Dateien zählen als leer
RQ_DATEIEN=("$SBIN/festpos-rqlited" "$SBIN/festpos-conf.sh" "$SBIN/festpos-rqcheck.py" "$UNITS/rqlited.service" "$RQ_BIN/rqlited")
NODE_DATEIEN=("$SBIN/festpos-node" "$UNITS/festpos-node.service" "$UNITS/keepalived.service.d/festpos.conf")
RQ_ALT=$(fingerabdruck "${RQ_DATEIEN[@]}")
NODE_ALT=$(fingerabdruck "${NODE_DATEIEN[@]}")
festpos_install_app "$SRC"
if [ -n "$VENV_NEU" ]; then
  if [ -n "$SICHERN" ] || [ ! -d "$ALT/venv" ]; then  # Sicherung ohne eigene venv teilt sich die bisherige
    rm -rf "${ALT:?}/venv"
    festpos_venv_move "$DEST/venv" "$ALT/venv"
  else
    rm -rf "${DEST:?}/venv"
  fi
  festpos_venv_move "$DEST/venv.neu" "$DEST/venv"
fi
festpos_install_system "$SRC"
festpos_install_rqlite "$SRC"                          # rqlite aus dem Paket, nur wenn es neuer ist (gleiche Hauptversion)
RQ_RESTART=""
[ "$RQ_ALT" != "$(fingerabdruck "${RQ_DATEIEN[@]}")" ] && RQ_RESTART=rqlited
NODE_GEAENDERT=""
[ "$NODE_ALT" != "$(fingerabdruck "${NODE_DATEIEN[@]}")" ] && NODE_GEAENDERT=1

# Ab Version 2026.10.10-3 entscheidet eine Markierung, ob ein Knoten Cluster-Mitglied ist (festpos-rqlited).
# Laufende, gesunde Knoten bekommen sie hier sofort.
RQ=$R/var/lib/festpos/rqlite
if [ -e "$RQ/raft.db" ] && [ ! -e "$RQ/.festpos-member" ]; then
  NEED=1; [ -e "$R/run/festpos/cluster" ] && NEED=2          # im 3er-Betrieb: nur mit mindestens 2 Knoten
  if curl -s -m 3 http://127.0.0.1:4001/status | NEED=$NEED python3 -c '
import json, os, sys
s = json.load(sys.stdin)["store"]
ok = s["raft"]["state"] in ("Leader", "Follower") and (s.get("leader") or {}).get("node_id")
sys.exit(0 if ok and len(s.get("nodes") or []) >= int(os.environ["NEED"]) else 1)' 2>/dev/null; then
    date -Is > "$RQ/.festpos-member" && { [ -n "$R" ] || chown festpos:festpos "$RQ/.festpos-member"; }
    echo "==> Datenbank: Mitgliedschaft markiert"
  elif [ "$NEED" = 2 ]; then
    echo "WARNUNG: Dieser Knoten sieht keinen Cluster mit mindestens 2 Knoten – nicht markiert. 'sudo festpos-notfall status' prüfen."
  fi
fi

# Boot-Partition nur für root lesbar (REV-026; festpos-node macht das auch bei jedem Start)
if [ -z "$R" ]; then
  # shellcheck source=festpos-conf.sh
  source "$SRC/deploy/festpos-conf.sh"
  BOOTDIR=/boot/firmware; [ -d "$BOOTDIR" ] || BOOTDIR=/boot
  festpos_fstab_private /etc/fstab "$BOOTDIR" && echo "==> Boot-Partition: ab jetzt nur für root lesbar (fstab)"
  festpos_boot_private_now "$BOOTDIR" || echo "HINWEIS: Boot-Partition ist erst nach dem nächsten Neustart nur für root lesbar"
  festpos_wifi_prepare "$BOOTDIR" && echo "==> WLAN: nicht mehr ganz abgeschaltet, Funk bleibt aus (für «Update aus dem Internet», ab Neustart)"
fi

echo "==> Konfiguration prüfen (wird beim nächsten Neustart angewendet)"
if ! "$SBIN/festpos-node" --pruefen; then
  echo "WARNUNG: festpos.conf oder node.conf hat einen Fehler (siehe oben). VOR dem nächsten Neustart korrigieren,"
  echo "         sonst startet FestPOS danach nicht."
fi
systemctl daemon-reload
systemctl enable festpos-display.service festpos-display.path >/dev/null 2>&1 || true

# Statusanzeige: Startprogramm und Dienst neu schreiben, falls eingerichtet (ohne Internet, Pakete sind schon da)
if [ -f "$UNITS/festpos-kiosk.service" ] && command -v cage >/dev/null && command -v chromium >/dev/null; then
  bash "$SRC/deploy/festpos-kiosk.sh" --vorbereiten >/dev/null || echo "WARNUNG: Statusanzeige nicht aktualisiert"
  if systemctl is-enabled --quiet festpos-kiosk; then systemctl restart festpos-kiosk || true; fi
fi

[ -n "$RQ_RESTART" ] && echo "==> Datenbank-Start hat sich geändert: rqlited wird neu gestartet"
neu_starten "$RQ_RESTART"

# 5. Prüfen – sonst zurück
if ! festpos_wait_version "$NEW" "$WAIT"; then
  echo "FEHLER: Server meldet nach $WAIT s nicht die Version $NEW."
  zurueck "$RQ_RESTART"
  exit 1
fi
if ! festpos_wait_ready 60; then
  if festpos_db_ok; then
    echo "FEHLER: Datenbank läuft, aber die neue Version wird nicht bereit."
    zurueck "$RQ_RESTART"
    exit 1
  fi
  if [ -n "$DB_VORHER" ] && [ -n "$RQ_RESTART" ]; then
    echo "FEHLER: Die Datenbank lief vor dem Update und kommt nach dem Neustart nicht wieder."
    zurueck "$RQ_RESTART"
    exit 1
  fi
  if [ -n "$DB_VORHER" ]; then
    echo "WARNUNG: Version $NEW läuft, aber die Datenbank antwortet nicht mehr (ein anderer Pi ausgefallen?)."
  else
    echo "WARNUNG: Version $NEW läuft, wartet aber auf die Datenbank – die lief schon vor dem Update nicht (Cluster ohne Mehrheit?)."
  fi
  echo "         Prüfen: sudo festpos-notfall status. Im Cluster NICHT mit dem nächsten Pi weitermachen."
  exit 2
fi
echo "OK: Server läuft mit Version $NEW. Handys laden die neue App selbst (sobald die Tischansicht leer offen ist)."
echo "    Zurück zur vorherigen Version: sudo bash $DEST/deploy/update.sh --zurueck"
if [ -z "$R" ] && [ -x "$SBIN/festpos-update" ]; then
  "$SBIN/festpos-update" --schluessel || true      # Updates über die Verwaltung/USB: Schlüssel da?
fi
[ -n "$NODE_GEAENDERT" ] && echo "HINWEIS: festpos-node (Netz, Firewall, Start) hat sich geändert – wirkt ab dem nächsten Neustart." \
  "Nicht eilig; im Cluster die Pi einzeln neu starten, den druckenden zuletzt."
exit 0
