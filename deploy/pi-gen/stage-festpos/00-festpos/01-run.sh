#!/bin/bash -e
# FestPOS ins Image: dasselbe setup.sh wie auf einem laufenden Pi (install-lib.sh), nur im chroot (FESTPOS_CHROOT=1).
# Das Projekt liegt neben dieser Stufe in «festpos/» (build.sh bzw. die CI kopiert es dorthin).
SRC="${STAGE_DIR}/festpos"
[ -f "$SRC/deploy/setup.sh" ] || { echo "FestPOS-Projekt fehlt in $SRC"; exit 1; }
rm -rf "${ROOTFS_DIR}/opt/festpos-src"
cp -a "$SRC" "${ROOTFS_DIR}/opt/festpos-src"
rm -f "${ROOTFS_DIR}/opt/festpos-src/deploy/festpos.conf"          # nie eine vorbereitete Konfiguration ins Image
rm -rf "${ROOTFS_DIR}/opt/festpos-src/deploy/festpos-tls"
on_chroot << CHROOT
FESTPOS_CHROOT=1 bash /opt/festpos-src/deploy/setup.sh --image
bash /opt/festpos-src/deploy/festpos-kiosk.sh --vorbereiten || true
passwd -l "${FIRST_USER_NAME}" || true
CHROOT
# Aufräumen nach denselben Regeln wie «SD-Karte schreiben» (T10): deploy/karte-ohne.txt
bash "${SRC}/deploy/pi-gen/clean-image.sh" "${ROOTFS_DIR}" "${ROOTFS_DIR}/boot/firmware" "${SRC}/deploy/karte-ohne.txt"
# Projekt-Schlüssel (öffentlicher Teil): damit nimmt ein Pi aus dem Image die signierten Updates des Projekts an –
# hochgeladen, vom USB-Stick oder «Aus dem Internet». Eigene Schlüssel kommen bei der Ersteinrichtung dazu.
if [ -f "${SRC}/deploy/festpos-update-projekt.pub" ]; then
  install -m 0644 "${SRC}/deploy/festpos-update-projekt.pub" "${ROOTFS_DIR}/boot/firmware/festpos-update-projekt.pub"
else
  echo "WARNUNG: deploy/festpos-update-projekt.pub fehlt – Pi aus diesem Image nehmen keine Updates an"
fi
rm -rf "${ROOTFS_DIR}/opt/festpos-src"
