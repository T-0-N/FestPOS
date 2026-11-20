# shellcheck shell=bash
# FestPOS – gemeinsame Installationsschritte für setup.sh und update.sh (wird eingebunden, nicht ausgeführt).
#
# Alle Systempfade beginnen mit $R. Auf dem Pi ist $R leer; nur die automatischen Tests setzen
# FESTPOS_TEST_ROOT auf ein Testverzeichnis.
R=${FESTPOS_TEST_ROOT:-}
DEST=$R/opt/festpos
ALT=$R/opt/festpos.alt                   # Sicherung der vorherigen Version (update.sh --zurueck)
SBIN=$R/usr/local/sbin
RQ_BIN=$R/usr/local/bin                  # rqlited und rqlite (setup.sh, festpos_install_rqlite)
UNITS=$R/etc/systemd/system

# Quelle in deploy/ -> Name in /usr/local/sbin -> Rechte
FESTPOS_SCRIPTS=(
  "festpos-conf.sh festpos-conf.sh 0644"
  "festpos-node.sh festpos-node 0755"
  "festpos-settime festpos-settime 0755"
  "festpos-notfall festpos-notfall 0755"
  "festpos-rqlited festpos-rqlited 0755"
  "festpos-rqcheck.py festpos-rqcheck.py 0755"
  "festpos-redirect.py festpos-redirect.py 0755"
  "festpos-display festpos-display 0755"
  "festpos-kiosk.sh festpos-kiosk 0755"
  "festpos-zertifikate festpos-zertifikate 0755"
  "festpos-update festpos-update 0755"
  "festpos-sicherung festpos-sicherung 0755"
  "festpos-wiederherstellen festpos-wiederherstellen 0755"
  "festpos-karte festpos-karte 0755"
  "festpos-dhcp-reload.py festpos-dhcp-reload 0755"
  "festpos-druckernetz.py festpos-druckernetz 0755"
  "festpos-handy-usb festpos-handy-usb 0755"
  "festpos-haertung festpos-haertung 0755"
)
UDEV_RULE=$R/etc/udev/rules.d/99-festpos-usb.rules     # USB-Stick mit Update -> festpos-usb@.service
UDEV_HANDY=$R/etc/udev/rules.d/99-festpos-handy-usb.rules   # Handy per USB -> festpos-handy-usb@.service (T2)

festpos_version() {             # festpos_version VERZEICHNIS -> Versionsnummer (Datei VERSION, früher in main.py)
  if [ -f "$1/VERSION" ]; then tr -d '[:space:]' < "$1/VERSION"; return; fi
  grep -m1 '^APP_VERSION' "$1/app/main.py" 2>/dev/null | cut -d'"' -f2 || true
}

festpos_req_hash() { sha256sum < "$1" | cut -d' ' -f1; }

festpos_venv_ok() {             # festpos_venv_ok VENV requirements.txt -> 0, wenn genau diese Pakete installiert sind
  [ -x "$1/bin/python" ] && [ -f "$1/.festpos-requirements" ] &&
    [ "$(cat "$1/.festpos-requirements")" = "$(festpos_req_hash "$2")" ]
}

festpos_build_venv() {          # festpos_build_venv QUELLE VENV -> neue venv mit den festen Versionen (REV-031)
  local src=$1 venv=$2 args
  rm -rf "$venv"
  python3 -m venv "$venv" || return 1
  args=(install -q --no-cache-dir --require-hashes -r "$src/requirements.txt")
  if compgen -G "$src/deploy/wheels/*.whl" >/dev/null; then
    "$venv/bin/pip" "${args[@]}" --no-index --find-links "$src/deploy/wheels" || return 1   # ohne Internet
  else
    echo "    (deploy/wheels fehlt – lade die Pakete aus dem Internet)"
    "$venv/bin/pip" "${args[@]}" || return 1
  fi
  festpos_req_hash "$src/requirements.txt" > "$venv/.festpos-requirements"
  chmod -R u=rwX,go=rX "$venv"                         # lesbar für den Dienstbenutzer festpos
}

festpos_venv_paths() {          # festpos_venv_paths VENV ALTER_PFAD -> Startskripte (pip …) auf VENV umschreiben
  local venv=$1 alt=$2 f
  for f in "$venv"/bin/*; do
    [ -f "$f" ] && [ ! -L "$f" ] && head -c 2 "$f" | grep -q '#!' || continue
    sed -i "1s|^#!$alt/|#!$venv/|" "$f"
  done
}

festpos_venv_move() {           # festpos_venv_move VON NACH -> venv umbenennen (Startskripte passen danach)
  mv "$1" "$2"
  festpos_venv_paths "$2" "$1"
}

festpos_system_files() {        # alle Dateien, die festpos_install_system schreibt (für Sicherung und Rückfall)
  local src=$1 s name f
  for s in "${FESTPOS_SCRIPTS[@]}"; do
    read -r _ name _ <<< "$s"
    echo "$SBIN/$name"
  done
  for f in "$src"/deploy/systemd/*.service "$src"/deploy/systemd/*.path "$src"/deploy/systemd/*.timer; do
    echo "$UNITS/${f##*/}"
  done
  echo "$UNITS/keepalived.service.d/festpos.conf"
  echo "$R/etc/tmpfiles.d/festpos.conf"
  echo "$UDEV_RULE"
  echo "$UDEV_HANDY"
  echo "$R/etc/sudoers.d/festpos"                      # gab es bis 2026.10.10-5 (sudo für die Uhrzeit)
  echo "$RQ_BIN/rqlited"                               # rqlite: mitgesichert, damit «Zurück» auch rqlite zurücknimmt
  echo "$RQ_BIN/rqlite"
}

festpos_rqlite_version() {      # festpos_rqlite_version -> installierte Version von rqlited (z.B. v10.5.3) oder leer
  [ -x "$RQ_BIN/rqlited" ] || return 0
  "$RQ_BIN/rqlited" -version 2>/dev/null | grep -o -m1 'v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*' | head -1 || true
}

festpos_install_rqlite() {      # festpos_install_rqlite QUELLE -> rqlite aus dem Paket, nur wenn es neuer ist
  local src=$1 want sha tgz have tmp
  [ -f "$src/deploy/rqlite.env" ] || return 0
  want=$(sed -n 's/^RQLITE_VERSION=//p' "$src/deploy/rqlite.env")
  sha=$(sed -n 's/^RQLITE_SHA256=//p' "$src/deploy/rqlite.env")
  tgz="$src/deploy/rqlite-$want-linux-arm64.tar.gz"
  [ -f "$tgz" ] || return 0                            # Paket ohne rqlite: nichts zu tun
  if ! echo "$sha  $tgz" | sha256sum -c --quiet >/dev/null 2>&1; then
    echo "WARNUNG: rqlite $want: Prüfsumme stimmt nicht – nicht installiert"
    return 0
  fi
  have=$(festpos_rqlite_version)
  if [ -n "$have" ]; then
    [ "$have" = "$want" ] && return 0
    [ "$(printf '%s\n%s\n' "$have" "$want" | sort -V | tail -1)" = "$want" ] || return 0   # nie zurück auf älter
    if [ "${have%%.*}" != "${want%%.*}" ]; then          # v10 -> v11: nur von Hand (ANLEITUNG «rqlite»)
      echo "HINWEIS: rqlite $have -> $want wechselt die Hauptversion – nicht automatisch, siehe ANLEITUNG"
      return 0
    fi
  fi
  tmp=$(mktemp -d)
  if tar xzf "$tgz" -C "$tmp" && [ -x "$tmp/rqlite-$want-linux-arm64/rqlited" ]; then
    install -d "$RQ_BIN"
    install -m 0755 "$tmp/rqlite-$want-linux-arm64/rqlited" "$tmp/rqlite-$want-linux-arm64/rqlite" "$RQ_BIN/"
    echo "==> rqlite $want installiert (vorher ${have:-keins})"
  else
    echo "WARNUNG: rqlite $want liess sich nicht entpacken – nicht installiert"
  fi
  rm -rf "$tmp"
}

festpos_install_system() {      # festpos_install_system QUELLE -> Skripte, Dienste, Laufzeit-Verzeichnisse
  local src=$1 s from name mode
  install -d "$SBIN" "$UNITS/keepalived.service.d" "$R/etc/tmpfiles.d" "${UDEV_RULE%/*}"
  for s in "${FESTPOS_SCRIPTS[@]}"; do
    read -r from name mode <<< "$s"
    install -m "$mode" "$src/deploy/$from" "$SBIN/$name"
  done
  install -m 0644 "$src"/deploy/systemd/*.service "$src"/deploy/systemd/*.path "$src"/deploy/systemd/*.timer "$UNITS/"
  install -m 0644 "$src/deploy/systemd/keepalived.service.d/festpos.conf" "$UNITS/keepalived.service.d/"
  install -m 0644 "$src/deploy/festpos-tmpfiles.conf" "$R/etc/tmpfiles.d/festpos.conf"
  install -m 0644 "$src/deploy/festpos-usb.rules" "$UDEV_RULE"
  install -m 0644 "$src/deploy/festpos-handy-usb.rules" "$UDEV_HANDY"
  # Die App braucht kein sudo mehr (Uhrzeit über festpos-settime.path, REV-050)
  rm -f "$R/etc/sudoers.d/festpos"
  if [ -z "$R" ]; then
    systemd-tmpfiles --create /etc/tmpfiles.d/festpos.conf 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable --now festpos-settime.path >/dev/null 2>&1 || true
    # Updates (Verwaltung, USB-Stick): der Dienst selbst läuft bei jedem Start einmal (meldet die Schlüssel).
    # Hier NICHT «--now»: update.sh kann gerade aus diesem Dienst heraus laufen – das würde aufeinander warten.
    systemctl enable festpos-update.service >/dev/null 2>&1 || true
    systemctl enable --now festpos-update.path >/dev/null 2>&1 || true
    systemctl enable --now festpos-sicherung.timer >/dev/null 2>&1 || true   # alle 15 min auf einen USB-Stick
    # Schlüssel aus einer Sicherung, Kartenpaket, auf 3 Pi umstellen, APK vom Stick (Verwaltung, T8)
    systemctl enable --now festpos-wiederherstellen.path >/dev/null 2>&1 || true
    # Ersteinrichtung im Browser (T11): läuft nur ohne festpos.conf (ConditionPathExists)
    systemctl enable festpos-einrichtung.service >/dev/null 2>&1 || true
    # DHCP für die Handys: startet nach dem nächsten Neustart, wenn festpos-node die Konfiguration geschrieben hat
    systemctl enable festpos-dhcp.service >/dev/null 2>&1 || true
    systemctl enable --now festpos-dhcp-reload.path >/dev/null 2>&1 || true
    # Drucker im eigenen VLAN (Verwaltung › Drucker): bei jedem Start und nach jeder Änderung einrichten
    systemctl enable festpos-druckernetz.service >/dev/null 2>&1 || true
    systemctl enable --now festpos-druckernetz.path >/dev/null 2>&1 || true
    udevadm control --reload-rules 2>/dev/null || true
    # Benutzer des Tunnels (festpos-tunnel.service, Review 2026-11 S4): schon beim Update, nicht erst beim nächsten
    # Start – sonst startete der Tunnel bis dahin nicht mehr. Die Firewall-Regel dazu kommt mit festpos-node.
    id festpos-tunnel >/dev/null 2>&1 ||
      useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin festpos-tunnel 2>/dev/null || true
    "$SBIN/festpos-haertung" || true                     # SSH nur mit Schlüssel, sudo mit Passwort (T23)
  fi
}

festpos_install_app() {         # festpos_install_app QUELLE -> Programm und App ersetzen (venv bleibt)
  local src=$1 d
  install -d "$DEST"
  # update.sh und install-lib.sh der installierten Version: «Zurück» geht damit auch ohne Projektordner
  # (sudo bash /opt/festpos/deploy/update.sh --zurueck; so macht es auch festpos-update)
  rm -rf "${DEST:?}/deploy.neu"
  install -d "$DEST/deploy.neu"
  install -m 0644 "$src/deploy/update.sh" "$src/deploy/install-lib.sh" "$DEST/deploy.neu/"
  install -m 0644 "$src/deploy/karte-ohne.txt" "$DEST/deploy.neu/"     # was nicht auf eine neue Karte gehört (T10)
  install -m 0644 "$src/deploy/festpos.conf.example" "$DEST/deploy.neu/"   # Vorlage für die Ersteinrichtung (T11)
  # Schlüssel der Paketquelle von Cloudflare (cloudflared für den Tunnel, festpos-update), falls mitgeliefert
  if [ -f "$src/deploy/cloudflare-main.gpg" ]; then install -m 0644 "$src/deploy/cloudflare-main.gpg" "$DEST/deploy.neu/"; fi
  rm -rf "${DEST:?}/deploy"
  mv "$DEST/deploy.neu" "$DEST/deploy"
  for d in app static; do
    rm -rf "${DEST:?}/$d.neu"
    cp -r "$src/$d" "$DEST/$d.neu"
    find "$DEST/$d.neu" -name __pycache__ -prune -exec rm -rf {} +
  done
  for d in app static; do
    rm -rf "${DEST:?}/$d"
    mv "$DEST/$d.neu" "$DEST/$d"
  done
  cp "$src/requirements.txt" "$src/VERSION" "$DEST/"
  [ -z "$R" ] && chown -R root:root "$DEST/app" "$DEST/static" "$DEST/deploy" "$DEST/requirements.txt" "$DEST/VERSION"
  # lesbar für den Dienstbenutzer festpos – egal, mit welchen Rechten die Dateien auf den Pi kamen
  chmod -R u=rwX,go=rX "$DEST/app" "$DEST/static" "$DEST/deploy" "$DEST/requirements.txt" "$DEST/VERSION"
}

festpos_snapshot() {            # festpos_snapshot ZIEL "SYSTEMDATEIEN…" -> Programm + diese Systemdateien sichern
  local ziel=$1 files=$2 f rel
  rm -rf "$ziel"
  install -d "$ziel/system"
  : > "$ziel/system.neu"
  for f in app static deploy requirements.txt VERSION; do
    if [ -e "$DEST/$f" ]; then cp -a "$DEST/$f" "$ziel/"; fi
  done
  while read -r f; do
    [ -n "$f" ] || continue
    rel=${f#"$R"}
    if [ -e "$f" ]; then
      install -d "$ziel/system${rel%/*}"
      cp -a "$f" "$ziel/system$rel"
    else
      echo "$rel" >> "$ziel/system.neu"                 # gab es in dieser Version (noch) nicht
    fi
  done <<< "$files"
}

festpos_backup() {              # festpos_backup QUELLE -> laufende Version nach $ALT (venv kommt beim Umschalten)
  festpos_snapshot "$ALT.neu" "$(festpos_system_files "$1")"
  rm -rf "$ALT"
  mv "$ALT.neu" "$ALT"
}

festpos_restore() {             # Wechsel zur Version in $ALT. Die bisherige kommt dafür nach $ALT –
  local f rel liste            # ein zweiter Aufruf wechselt also wieder zurück (nie eine Mischung).
  [ -d "$ALT/app" ] || { echo "Keine Sicherung in $ALT gefunden."; return 1; }
  liste=$( (cd "$ALT/system" && find . -type f | sed "s|^\.|$R|"); sed "s|^|$R|" "$ALT/system.neu")
  festpos_snapshot "$ALT.tausch" "$liste"
  for f in app static; do
    rm -rf "${DEST:?}/$f"
    cp -a "$ALT/$f" "$DEST/"
  done
  for f in deploy requirements.txt VERSION; do         # (deploy/ gibt es erst ab 2026.10.10-26)
    rm -rf "${DEST:?}/$f"
    if [ -e "$ALT/$f" ]; then cp -a "$ALT/$f" "$DEST/"; fi
  done
  if [ -d "$ALT/venv" ]; then                         # venv tauschen (schnell, statt 80 MB zu kopieren)
    [ -d "$DEST/venv" ] && festpos_venv_move "$DEST/venv" "$ALT.tausch/venv"
    festpos_venv_move "$ALT/venv" "$DEST/venv"
  fi
  while read -r rel; do
    [ -n "$rel" ] || continue
    install -d "$R${rel%/*}"
    cp -a "$ALT/system$rel" "$R$rel"
  done < <(cd "$ALT/system" && find . -type f | sed 's|^\.||')
  while read -r rel; do
    [ -n "$rel" ] && rm -f "$R$rel"
  done < "$ALT/system.neu"
  rm -rf "$ALT"
  mv "$ALT.tausch" "$ALT"
  [ -d "$ALT/venv" ] && festpos_venv_paths "$ALT/venv" "$ALT.tausch/venv"
  return 0
}

festpos_health() {              # festpos_health -> JSON von /api/health (leer, wenn keine Antwort)
  curl -sk -m 3 https://127.0.0.1:8443/api/health 2>/dev/null || true
}

festpos_wait_version() {        # festpos_wait_version VERSION SEKUNDEN -> 0, sobald der Server sie meldet
  local want=$1 n=$2 v
  while ((n-- > 0)); do
    v=$(festpos_health | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version",""))' 2>/dev/null || true)
    [ "$v" = "$want" ] && return 0
    sleep 1
  done
  return 1
}

festpos_wait_ready() {          # festpos_wait_ready SEKUNDEN -> 0, sobald die App mit der Datenbank läuft
  local n=$1 r
  while ((n-- > 0)); do
    r=$(festpos_health | python3 -c 'import json,sys; print(json.load(sys.stdin).get("ready", True))' 2>/dev/null || true)
    [ "$r" = True ] && return 0
    sleep 1
  done
  return 1
}

festpos_db_ok() {               # Datenbank selbst in Ordnung (Leader bekannt)?
  curl -s -m 3 http://127.0.0.1:4001/status 2>/dev/null | python3 -c '
import json, sys
s = json.load(sys.stdin)["store"]
sys.exit(0 if (s.get("leader") or {}).get("node_id") else 1)' 2>/dev/null
}
