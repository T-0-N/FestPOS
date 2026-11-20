#!/bin/bash
# FestPOS – macht aus einem frischen Raspberry Pi OS Lite (64-bit) das Master-Image.
#
# Voraussetzungen: Pi 4 oder Pi 5, Raspberry Pi OS Lite 64-bit, Internet (nur für dieses Setup),
#                  dieses Projekt nach /home/<user>/festpos kopiert.
# Aufruf:          sudo bash deploy/setup.sh            Erstinstallation oder Update (Daten bleiben)
#                  sudo bash deploy/setup.sh --image    zusätzlich Datenbank leeren (Master-Image zum Klonen)
#
# Danach: sudo reboot – ohne festpos.conf startet der Pi in die Ersteinrichtung im Browser (T11).
# Image (T12): pi-gen ruft dieses Skript im chroot mit FESTPOS_CHROOT=1 auf (deploy/pi-gen/) – dann wird nichts
# gestartet und nichts an der laufenden Maschine geändert; was Hardware braucht, macht festpos-node beim ersten Start.
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"   # mit «sh …» gestartet (z.B. ohne x-Recht): bash nehmen
set -euo pipefail

SRC=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck source=install-lib.sh
source "$SRC/deploy/install-lib.sh"
# shellcheck source=festpos-conf.sh
source "$SRC/deploy/festpos-conf.sh"
BOOT=/boot/firmware
[ -d "$BOOT" ] || BOOT=/boot

step() { echo; echo "==> $*"; }
CHROOT=${FESTPOS_CHROOT:-}                  # 1: im Image-Bau (pi-gen), kein laufendes systemd
[ "$(id -u)" = 0 ] || { echo "Bitte mit sudo ausführen"; exit 1; }
[ "$(uname -m)" = "aarch64" ] || { echo "Nur 64-bit Raspberry Pi OS (aarch64) wird unterstützt"; exit 1; }

step "Pakete installieren"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  python3-venv python3-pip keepalived chrony nftables curl openssl fake-hwclock dnsmasq-base dosfstools e2fsprogs
# hwclock (RTC des Pi 5 prüfen: sudo hwclock -r); ältere Pi OS haben es ohne dieses Paket
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends util-linux-extra 2>/dev/null || true
# adb: Handy per USB am Pi bekommt den Pi als Zeitserver (T2, festpos-handy-usb); fehlt es, zeigt die Verwaltung das
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends adb 2>/dev/null || true

step "Systembenutzer festpos"
id festpos >/dev/null 2>&1 || useradd --system --home /var/lib/festpos --shell /usr/sbin/nologin festpos
install -d -o festpos -g festpos /var/lib/festpos /var/lib/festpos/rqlite

step "Anwendung $(festpos_version "$SRC") nach $DEST"
festpos_install_app "$SRC"
if festpos_venv_ok "$DEST/venv" "$SRC/requirements.txt"; then
  echo "Python-Pakete unverändert"
else
  # feste Versionen mit Prüfsumme (REV-031), aus deploy/wheels (Update-Paket, Image) – sonst aus dem Internet
  festpos_build_venv "$SRC" "$DEST/venv.neu"
  rm -rf "$DEST/venv"
  mv "$DEST/venv.neu" "$DEST/venv"
fi
chmod -R u=rwX,go=rX "$DEST/venv"

# rqlite: feste Version mit Prüfsumme aus deploy/rqlite.env (REV-031). Andere Version nur zusammen mit ihrer SHA256
# angeben (RQLITE_VERSION=… RQLITE_SHA256=… sudo -E bash deploy/setup.sh).
RQ_V=${RQLITE_VERSION:-}; RQ_S=${RQLITE_SHA256:-}
# shellcheck source=rqlite.env
source "$SRC/deploy/rqlite.env"
RQLITE_VERSION=${RQ_V:-$RQLITE_VERSION}; RQLITE_SHA256=${RQ_S:-$RQLITE_SHA256}
step "rqlite $RQLITE_VERSION"
TMP=$(mktemp -d)
RQ_TGZ="rqlite-$RQLITE_VERSION-linux-arm64.tar.gz"
if [ -f "$SRC/deploy/$RQ_TGZ" ]; then
  cp "$SRC/deploy/$RQ_TGZ" "$TMP/rq.tgz"                  # liegt bei (Update-Paket, Image: tools/pakete.py)
else
  curl -fsSL -o "$TMP/rq.tgz" "https://github.com/rqlite/rqlite/releases/download/$RQLITE_VERSION/$RQ_TGZ"
fi
if ! echo "$RQLITE_SHA256  $TMP/rq.tgz" | sha256sum -c --quiet; then
  rm -rf "$TMP"
  echo "FEHLER: rqlite-Download hat eine falsche Prüfsumme – abgebrochen (nichts installiert)."
  exit 1
fi
tar xzf "$TMP/rq.tgz" -C "$TMP"
install -m 0755 "$TMP"/rqlite-*/rqlited "$TMP"/rqlite-*/rqlite /usr/local/bin/
rm -rf "$TMP"
rqlited -version

step "Skripte und Dienste"
festpos_install_system "$SRC"

step "SD-Karte schonen"
install -d /etc/systemd/journald.conf.d
printf "[Journal]\nStorage=volatile\nRuntimeMaxUse=64M\n" > /etc/systemd/journald.conf.d/festpos.conf
systemctl disable --now dphys-swapfile 2>/dev/null || true
systemctl disable --now rpi-swap 2>/dev/null || true
systemctl enable tmp.mount 2>/dev/null || true
# noatime auf der Root-Partition
sed -i -E '/\s\/\s+ext4\s/ { /noatime/! s/(ext4\s+)([^ \t]+)/\1\2,noatime/ }' /etc/fstab

step "Funk des Pi abschalten (stört sonst den AP; WLAN nur kurz für «Update aus dem Internet»)"
grep -q "^dtoverlay=disable-bt" "$BOOT/config.txt" || echo "dtoverlay=disable-bt" >> "$BOOT/config.txt"
festpos_wifi_prepare "$BOOT" || true
systemctl disable --now hciuart bluetooth 2>/dev/null || true

step "Konfiguration auf der Boot-Partition"
# Normalfall (T11): keine festpos.conf – der Pi startet in die Ersteinrichtung im Browser. Wer die Datei am PC
# vorbereitet (Variante für Fortgeschrittene), legt sie als deploy/festpos.conf ins Projekt.
if [ ! -f "$BOOT/festpos.conf" ] && [ -f "$SRC/deploy/festpos.conf" ]; then
  install -m 0644 "$SRC/deploy/festpos.conf" "$BOOT/festpos.conf"
fi
if [ -f "$BOOT/festpos.conf" ] && [ ! -f "$BOOT/node.conf" ]; then
  install -m 0644 "$SRC/deploy/node.conf.example" "$BOOT/node.conf"
fi
# Projekt-Schlüssel für die offiziellen Updates, solange noch kein Update-Schlüssel da ist (wie im Image)
if [ -f "$SRC/deploy/festpos-update-projekt.pub" ] && ! compgen -G "$BOOT/festpos-update*.pub" >/dev/null; then
  install -m 0644 "$SRC/deploy/festpos-update-projekt.pub" "$BOOT/festpos-update-projekt.pub"
fi
# Gefunden über festpos.local während der Ersteinrichtung (auf Raspberry Pi OS meist schon da)
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends avahi-daemon 2>/dev/null || true
if [ -d "$SRC/deploy/festpos-tls" ]; then
  install -d "$BOOT/festpos-tls"
  # Fehlende Dateien ergänzen (z.B. device-ca bei einem Update), vorhandene nicht überschreiben
  for f in ca.crt server.crt server.key device-ca.crt device-ca.key; do
    if [ -f "$SRC/deploy/festpos-tls/$f" ] && [ ! -f "$BOOT/festpos-tls/$f" ]; then
      install -m 0644 "$SRC/deploy/festpos-tls/$f" "$BOOT/festpos-tls/"
    fi
  done
fi

step "Boot-Partition nur für root lesbar (Passwort und Schlüssel liegen dort)"
festpos_fstab_private /etc/fstab "$BOOT" && echo "fstab angepasst (fmask=0077,dmask=0077)" || echo "fstab schon angepasst"
[ -n "$CHROOT" ] || festpos_boot_private_now "$BOOT" || echo "wirkt nach dem nächsten Neustart"

step "Dienste aktivieren (starten beim nächsten Boot)"
[ -n "$CHROOT" ] || systemctl daemon-reload
systemctl enable festpos-node rqlited festpos festpos-redirect keepalived chrony nftables festpos-settime.path \
  festpos-update.service festpos-update.path festpos-dhcp-reload.path festpos-wiederherstellen.path festpos-einrichtung \
  festpos-druckernetz.service festpos-druckernetz.path
systemctl enable festpos-display.service festpos-display.path
# Statusanzeige vorbereiten (Pakete jetzt, solange Internet da ist). Ein- und ausschalten: Verwaltung › Bildschirm am Pi
bash "$SRC/deploy/festpos-kiosk.sh" --vorbereiten || echo "WARNUNG: Statusanzeige nicht vorbereitet (später: sudo festpos-kiosk --vorbereiten)"
# fake-hwclock richtet seine Dienste (…-load/-save) bei der Installation selbst ein. Auf Pi, deren RTC die Zeit hält,
# schaltet festpos-node es beim Start wieder ab (maskiert; T16), sonst stellte es die richtige Zeit zurück.
systemctl enable fake-hwclock-load.service fake-hwclock-save.timer 2>/dev/null || true
systemctl disable --now systemd-timesyncd 2>/dev/null || true

# Datenbank NUR beim Erstellen eines Master-Images zum Klonen leeren (sudo bash setup.sh --image).
# Ein normaler Aufruf (Erstinstallation oder Update) lässt alle Daten unangetastet.
if [ "${1:-}" = "--image" ]; then
  step "Master-Image säubern (Datenbank wird geleert)"
  systemctl stop festpos rqlited 2>/dev/null || true
  rm -rf /var/lib/festpos/rqlite && install -d -o festpos -g festpos /var/lib/festpos/rqlite
  rm -f /etc/ssh/.festpos-node
fi

cat <<EOF

FERTIG. Nächste Schritte:
  Neuer Pi (keine festpos.conf): sudo reboot – danach im Browser http://festpos.local/ (oder die Adresse auf dem
     Bildschirm, am Festplatz http://192.168.55.10/) öffnen und die Ersteinrichtung durchklicken. Den Code dazu zeigt
     der Bildschirm am Pi und die Datei festpos-einrichtung.txt auf der Boot-Partition.
  Update bzw. festpos.conf vorhanden: sudo reboot. Verwaltung: https://192.168.55.10:8443/admin.html
EOF
