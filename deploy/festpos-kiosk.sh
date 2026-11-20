#!/bin/bash
# FestPOS – Statusanzeige auf einem Bildschirm am Pi (Kiosk-Modus).
#
# Nach dem Start zeigt der Pi im Vollbild https://127.0.0.1:8443/status.html:
# Drucker, Warteschlange, Bestellungen, Handys. Ohne Desktop, startet von selbst neu (Mauszeiger: Verwaltung).
# Technik: "cage" (minimaler Wayland-Kiosk) + Chromium, eigener Benutzer "kiosk".
#
#   sudo bash festpos-kiosk.sh          einrichten (braucht EINMAL Internet für apt)
#   sudo bash festpos-kiosk.sh --aus    wieder entfernen (Pi läuft dann ohne Bildschirm-Anzeige)
#   sudo festpos-kiosk --vorbereiten    nur installieren, nicht einschalten (Ein/Aus dann in der Verwaltung)
#
# Display-Typ, Drehung, Grösse und Touch stellt man in der Verwaltung ein (Einstellungen › Bildschirm am Pi).
#
# Bildschirm: HDMI, offizielles DSI-Display oder kleines SPI-Display (z. B. Joy-IT RB-TFT3.5, siehe ANLEITUNG).
# FestPOS läuft unabhängig davon weiter.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Bitte mit sudo ausführen"; exit 1; }
UNIT=/etc/systemd/system/festpos-kiosk.service
URL=${KIOSK_URL:-https://127.0.0.1:8443/status.html?kiosk=1}
PREP=0; [ "${1:-}" = "--vorbereiten" ] && PREP=1

if [ "${1:-}" = "--aus" ]; then
  systemctl disable --now festpos-kiosk 2>/dev/null || true
  rm -rf /usr/local/share/icons/festpos-leer /usr/local/share/icons/default
  rm -f "$UNIT" /usr/local/bin/festpos-kiosk-start /usr/local/bin/festpos-kiosk-touch /etc/udev/rules.d/99-festpos-touch.rules /etc/chromium/policies/managed/festpos-kiosk.json /etc/festpos-kiosk-cursor
  systemctl daemon-reload
  systemctl enable --now getty@tty1 2>/dev/null || true
  echo "Statusanzeige entfernt."
  exit 0
fi

echo "==> Pakete (cage, chromium)"
PKGS="cage chromium fonts-dejavu-core"
MISSING=$(for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || echo "$p"; done)
if [ -n "$MISSING" ]; then                 # nur dann Internet nötig
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $MISSING
else
  echo "    schon installiert"
fi

echo "==> Benutzer kiosk"
id kiosk >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/festpos-kiosk --shell /usr/sbin/nologin kiosk
# input (REV-050 geprüft): bleibt bewusst. Cage bekommt die Touch-Geräte zwar meist über die logind-Sitzung
# auf tty1, beim SPI-Display (ADS7846) fehlt diese Freigabe aber je nach Startzeitpunkt – dann ginge Touch nicht.
# Risiko gering: der Benutzer kiosk zeigt nur die Statusseite an, ohne Passwort und ohne Anmeldung (nologin).
for g in video render input; do getent group "$g" >/dev/null && usermod -aG "$g" kiosk; done

echo "==> Einstellungen"
# Drehung der Anzeige: 0, 90, 180 oder 270 (Grad). Ändern: /etc/festpos-kiosk.conf, dann
#   sudo systemctl restart festpos-kiosk
[ -f /etc/festpos-kiosk.conf ] || cat > /etc/festpos-kiosk.conf <<'EOS'
# FestPOS Statusanzeige
# Drehung in Grad: 0, 90, 180 oder 270. Danach: sudo systemctl restart festpos-kiosk
DREHUNG=0
# Touch passt nicht zur Anzeige (Finger nach oben, Punkt geht zur Seite)? Drehung des Touch:
# 0, 90, 180 oder 270, mit "m" dahinter gespiegelt (z. B. 90m). Zum Ausprobieren TOUCHTEST=1.
TOUCH=0
# TOUCHTEST=1 zeigt unten links, ob und wo Berührungen ankommen (zur Fehlersuche)
TOUCHTEST=0
# Sichtbare Fläche erzwingen, falls die Anzeige über den Rand hinausgeht, z. B. GROESSE=480x320
GROESSE=
EOS
grep -q "^TOUCH=" /etc/festpos-kiosk.conf || printf '%s\n' "# Drehung des Touch: 0, 90, 180, 270, mit m gespiegelt (z. B. 90m)" "TOUCH=0" >> /etc/festpos-kiosk.conf
grep -q "^TOUCHTEST=" /etc/festpos-kiosk.conf || printf '%s\n' "TOUCHTEST=0" >> /etc/festpos-kiosk.conf
grep -q "^GROESSE=" /etc/festpos-kiosk.conf || printf '%s\n' "# Sichtbare Fläche erzwingen, z. B. GROESSE=480x320 (leer = automatisch)" "GROESSE=" >> /etc/festpos-kiosk.conf
# Chromium: nie "Seite übersetzen?" fragen, deutsche Oberfläche
mkdir -p /etc/chromium/policies/managed
cat > /etc/chromium/policies/managed/festpos-kiosk.json <<'EOS'
{ "TranslateEnabled": false, "ApplicationLocaleValue": "de-CH", "DefaultBrowserSettingEnabled": false,
  "PasswordManagerEnabled": false, "AutofillAddressEnabled": false, "AutofillCreditCardEnabled": false }
EOS

echo "==> Unsichtbarer Mauszeiger"
# cage zeichnet sonst einen Pfeil, sobald der Touch als Maus gemeldet wird. Leeres Cursor-Thema (1×1, durchsichtig).
# Sichtbar nur, wenn die Verwaltung es will (festpos-display schreibt /etc/festpos-kiosk-cursor, siehe Startprogramm).
python3 - <<'EOS'
import os, struct
d = "/usr/local/share/icons/festpos-leer/cursors"
os.makedirs(d, exist_ok=True)
img = struct.pack("<9I", 36, 0xfffd0002, 24, 1, 1, 1, 0, 0, 0) + struct.pack("<I", 0)
data = b"Xcur" + struct.pack("<3I", 16, 0x10000, 1) + struct.pack("<3I", 0xfffd0002, 24, 28) + img
with open(f"{d}/left_ptr", "wb") as f:
    f.write(data)
for n in ["default", "arrow", "pointer", "hand1", "hand2", "text", "xterm", "grab", "grabbing", "move", "wait",
          "watch", "progress", "crosshair", "top_left_arrow", "left_ptr_watch", "not-allowed", "all-scroll"]:
    if not os.path.lexists(f"{d}/{n}"):
        os.symlink("left_ptr", f"{d}/{n}")
with open("/usr/local/share/icons/festpos-leer/index.theme", "w") as f:
    f.write("[Icon Theme]\nName=festpos-leer\n")
EOS
# auch als "default": manche cage-Versionen lesen XCURSOR_THEME nicht, suchen aber "default" im XCURSOR_PATH
ln -sfn festpos-leer /usr/local/share/icons/default

echo "==> Touch-Ausrichtung"
cat > /usr/local/bin/festpos-kiosk-touch <<'EOS'
#!/bin/sh
# Setzt die Touch-Drehung aus /etc/festpos-kiosk.conf (TOUCH=0|90|180|270, "m" = gespiegelt) – läuft als root vor der Anzeige.
TOUCH=0; [ -r /etc/festpos-kiosk.conf ] && . /etc/festpos-kiosk.conf
case "$TOUCH" in
  90)   M="0 -1 1 1 0 0" ;;   180)  M="-1 0 1 0 -1 1" ;;  270)  M="0 1 0 -1 0 1" ;;
  0m)   M="-1 0 1 0 1 0" ;;   90m)  M="0 1 0 1 0 0" ;;    180m) M="1 0 0 0 -1 1" ;;  270m) M="0 -1 1 -1 0 1" ;;
  *)    M="" ;;
esac
R=/etc/udev/rules.d/99-festpos-touch.rules
NEW=""; [ -n "$M" ] && NEW="ENV{ID_INPUT_TOUCHSCREEN}==\"1\", ENV{LIBINPUT_CALIBRATION_MATRIX}=\"$M\""
OLD=$(cat "$R" 2>/dev/null || true)
[ "$NEW" = "$OLD" ] && exit 0
if [ -n "$NEW" ]; then echo "$NEW" > "$R"; else rm -f "$R"; fi
udevadm control --reload
udevadm trigger --subsystem-match=input --action=change
udevadm settle --timeout=5 || true
EOS
chmod 755 /usr/local/bin/festpos-kiosk-touch

echo "==> Startprogramm"
cat > /usr/local/bin/festpos-kiosk-start <<'EOS'
#!/bin/sh
# Kleines SPI-Display (z. B. Joy-IT RB-TFT3.5, ili9486) gefunden? Dann nur dieses benutzen,
# ohne Grafikbeschleunigung (das SPI-Display hat keine). Sonst HDMI/DSI wie gewohnt.
GPU=""
for c in /sys/class/drm/card*-SPI-*; do
  [ -e "$c" ] && [ "$(cat "$c/status" 2>/dev/null)" != "disconnected" ] || continue
  card=${c##*/}; card=${card%%-*}
  export WLR_DRM_DEVICES=/dev/dri/$card WLR_RENDERER=pixman WLR_NO_HARDWARE_CURSORS=1
  GPU="--disable-gpu"
  break
done
URL=$1
# Mauszeiger (Verwaltung › Bildschirm am Pi): sonst unsichtbares Thema, sichtbar = Standard des Systems
# (fehlt eines, zeichnen cage und Chromium ihren eingebauten Pfeil)
if [ "$(head -c 3 /etc/festpos-kiosk-cursor 2>/dev/null)" = on ]; then
  export XCURSOR_PATH=/usr/share/icons XCURSOR_THEME=default
fi
DREHUNG=0; [ -r /etc/festpos-kiosk.conf ] && . /etc/festpos-kiosk.conf
Q=""
case "$DREHUNG" in 90|180|270) Q="drehung=$DREHUNG" ;; esac          # die Seite dreht sich selbst
[ "${TOUCHTEST:-0}" = 1 ] && Q="${Q:+$Q&}touchtest=1"                 # zeigt, ob Berührungen ankommen
case "${GROESSE:-}" in [0-9]*x[0-9]*) Q="${Q:+$Q&}groesse=$GROESSE" ;; esac   # sichtbare Fläche erzwingen
case "$URL" in *\?*) SEP="&" ;; *) SEP="?" ;; esac
[ -n "$Q" ] && URL="$URL$SEP$Q"     # alte Einstellungen; was in der Verwaltung gewählt ist, hat Vorrang
exec /usr/bin/cage -d -s -- /usr/bin/chromium --kiosk --ozone-platform=wayland --touch-events=enabled --force-device-scale-factor=1 --noerrdialogs $GPU --lang=de-CH --disable-pinch --overscroll-history-navigation=0 \
  --disable-infobars --no-first-run --disable-session-crashed-bubble --disable-features=Translate,TranslateUI \
  --check-for-update-interval=31536000 --password-store=basic --ignore-certificate-errors \
  --user-data-dir=/var/lib/festpos-kiosk/chromium --disk-cache-size=1 "$URL"
EOS
chmod 755 /usr/local/bin/festpos-kiosk-start

echo "==> Dienst festpos-kiosk"
cat > "$UNIT" <<EOF
[Unit]
Description=FestPOS Statusanzeige (Kiosk)
After=systemd-user-sessions.service festpos.service getty@tty1.service
Wants=festpos.service
Conflicts=getty@tty1.service

[Service]
User=kiosk
PAMName=login
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardInput=tty-fail
StandardOutput=journal
StandardError=journal
UtmpIdentifier=tty1
UtmpMode=user
Environment=XDG_SESSION_TYPE=wayland
Environment=XCURSOR_PATH=/usr/local/share/icons XCURSOR_THEME=festpos-leer XCURSOR_SIZE=24
Environment=LANG=de_CH.UTF-8 LANGUAGE=de_CH:de
# Anzeige darf FestPOS nie ausbremsen
Nice=10
# warten, bis der Server antwortet (max. 2 Minuten). nohup + trap: beim Übernehmen von tty1 schickt das
# Terminal ein Auflegen-Signal (HUP), das sonst diese Warteschleife abbricht.
ExecStartPre=-+/usr/local/bin/festpos-kiosk-touch
ExecStartPre=-/usr/bin/nohup /bin/sh -c 'trap "" HUP; for i in \$\$(seq 120); do curl -sk -o /dev/null $URL && exit 0; sleep 1; done; exit 0'
ExecStart=/usr/local/bin/festpos-kiosk-start $URL
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
# --ignore-certificate-errors: die Anzeige öffnet nur den eigenen Pi (127.0.0.1)
systemctl daemon-reload
if [ "$PREP" = 1 ]; then
  echo "Vorbereitet. Einschalten: Verwaltung › Einstellungen › Bildschirm am Pi."
  exit 0
fi
systemctl disable --now getty@tty1 2>/dev/null || true
systemctl enable festpos-kiosk
systemctl restart festpos-kiosk
# Display-Typ "HDMI" vormerken, falls in der Verwaltung noch nichts gewählt ist
[ -x /usr/local/sbin/festpos-display ] && systemctl start festpos-display.service 2>/dev/null || true
echo
echo "Fertig. Die Statusanzeige startet jetzt und nach jedem Neustart automatisch."
echo "Prüfen:   systemctl status festpos-kiosk     Entfernen:   sudo bash $0 --aus"
