#!/bin/bash
# FestPOS – Image aufräumen und prüfen (T12): alles weg, was in karte-ohne.txt steht (gleiche Regeln wie «SD-Karte
# schreiben», T10), und sicherstellen, dass keine Konfiguration, Schlüssel oder Datenbank im Image steckt.
#   clean-image.sh ROOTFS BOOTFS karte-ohne.txt
set -euo pipefail
ROOT=${1:?ROOTFS}; BOOTFS=${2:?BOOTFS}; LISTE=${3:?karte-ohne.txt}
[ -d "$ROOT/etc" ] || { echo "kein Root-Dateisystem: $ROOT"; exit 1; }
shopt -s nullglob dotglob
while IFS= read -r zeile || [ -n "$zeile" ]; do
  zeile=${zeile%%#*}
  zeile=$(echo "$zeile" | sed -e 's/[[:space:]]*$//' -e 's/^[[:space:]]*//')
  [ -n "$zeile" ] || continue
  case "$zeile" in
    boot:*) muster=${zeile#boot:}; for p in "$BOOTFS"/$muster; do rm -rf -- "$p"; done ;;
    /proc/*|/sys/*|/dev/*|/run/*|/tmp/*|/mnt/*|/media/*|/boot/firmware/*|/lost+found/*) ;;   # im Image egal
    *)
      muster=${zeile% ordner}
      ordner=""; [ "$muster" != "$zeile" ] && ordner=1
      for p in "$ROOT"$muster; do
        if [ -n "$ordner" ] && [ -d "$p" ]; then find "$p" -mindepth 1 ! -type d -delete; else rm -rf -- "$p"; fi
      done ;;
  esac
done < "$LISTE"
: > "$ROOT/etc/machine-id"                                   # leer: systemd erzeugt beim ersten Start eine neue
# Prüfen: nichts Gerätespezifisches
fehler=0
for p in "$BOOTFS/festpos.conf" "$BOOTFS/node.conf" "$BOOTFS/festpos-tls" "$ROOT/var/lib/festpos/rqlite/db.sqlite"; do
  [ -e "$p" ] && { echo "FEHLER: $p im Image"; fehler=1; }
done
for p in "$ROOT"/etc/ssh/ssh_host_*_key "$ROOT"/var/lib/festpos/rqlite/*; do
  echo "FEHLER: $p im Image"; fehler=1
done
[ -s "$ROOT/etc/machine-id" ] && { echo "FEHLER: machine-id nicht leer"; fehler=1; }
[ "$fehler" = 0 ] && echo "Image sauber (keine Konfiguration, Schlüssel, Datenbank)"
exit "$fehler"
