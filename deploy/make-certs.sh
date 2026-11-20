#!/bin/bash
# Erzeugt die Zertifizierungsstelle (CA) und das Serverzertifikat für HTTPS. Am PC (oder Pi) ausführen.
#
#   bash make-certs.sh festpos.conf              Server-Zertifikat (neu) ausstellen; CAs nur anlegen, wenn sie fehlen
#   bash make-certs.sh festpos.conf --neue-ca    neue Server-CA (mit Namensbeschränkung), dann ca.crt auf ALLEN
#                                             Handys und PCs neu installieren – nicht kurz vor einem Anlass
#
# Ergebnis:
#   festpos-tls/          -> kommt auf die Boot-Partition JEDER Karte (setup.sh kopiert es dorthin)
#     ca.crt              -> zusätzlich auf jedes Handy/jeden PC (oder http://192.168.55.10/ca.crt)
#     server.crt/.key     -> HTTPS-Zertifikat der Pi
#     device-ca.crt/.key  -> damit stellt die Verwaltung die Gerätezertifikate der Handys aus
#   ~/festpos-ca/         -> NUR hier liegt ca.key, der Schlüssel der Server-CA. NICHT auf die Karten,
#                            NICHT in den Projektordner (REV-026). Sicher aufbewahren, z.B. USB-Stick.
#                            Anderer Ort: FESTPOS_CA_DIR=/pfad bash make-certs.sh festpos.conf
#
# Die Server-CA darf nur Zertifikate für das Festnetz (NET_PREFIX/NET_CIDR), 127.0.0.1, festpos.local und
# localhost ausstellen (nameConstraints). Selbst mit einem gestohlenen ca.key lassen sich damit keine
# fremden Webseiten (Bank, Google …) auf den Handys fälschen.
set -euo pipefail
export MSYS2_ARG_CONV_EXCL='/CN='        # Git Bash (Windows): nur "/CN=…" nicht als Pfad umschreiben, Dateipfade schon
CONF=${1:-}
[ -n "$CONF" ] && [ -f "$CONF" ] || { echo "Aufruf: $0 pfad/zu/festpos.conf [--neue-ca]"; exit 1; }
NEUE_CA=""; [ "${2:-}" = --neue-ca ] && NEUE_CA=1
# Konfiguration lesen, nicht ausführen (Passwörter mit $ o.ä. stören so nicht)
# shellcheck source=festpos-conf.sh
source "$(dirname "$0")/festpos-conf.sh"
festpos_read_conf "$CONF"
for v in VIP NODE_A_IP NODE_B_IP NODE_C_IP; do festpos_is_ip "${!v:-}" || { echo "$v ist keine gültige IP-Adresse"; exit 1; }; done
festpos_is_ip "${NET_PREFIX:-}.0" || { echo "NET_PREFIX ungültig: '${NET_PREFIX:-}'"; exit 1; }
[[ ${NET_CIDR:-} =~ ^[0-9]{1,2}$ ]] && ((NET_CIDR >= 8 && NET_CIDR <= 30)) || { echo "NET_CIDR ungültig"; exit 1; }

ip2int() { local IFS=.; read -r a b c d <<< "$1"; echo $(((a << 24) | (b << 16) | (c << 8) | d)); }
int2ip() { echo "$((($1 >> 24) & 255)).$((($1 >> 16) & 255)).$((($1 >> 8) & 255)).$(($1 & 255))"; }
MASK_INT=$(((0xffffffff << (32 - NET_CIDR)) & 0xffffffff))
NET_INT=$(($(ip2int "$NET_PREFIX.0") & MASK_INT))
NET=$(int2ip "$NET_INT"); MASK=$(int2ip "$MASK_INT")
for v in VIP NODE_A_IP NODE_B_IP NODE_C_IP; do
  [ $(($(ip2int "${!v}") & MASK_INT)) = "$NET_INT" ] || { echo "$v=${!v} liegt nicht im Netz $NET/$NET_CIDR"; exit 1; }
done
CONSTRAINTS="critical,permitted;IP:$NET/$MASK,permitted;IP:127.0.0.1/255.255.255.255,permitted;DNS:festpos.local,permitted;DNS:localhost"

OUT=$(pwd)/festpos-tls
CA_DIR=${FESTPOS_CA_DIR:-$HOME/festpos-ca}
PROJEKT=$(cd "$(dirname "$0")/.." && pwd -P)
NEUER_ORDNER=""; [ -d "$CA_DIR" ] || NEUER_ORDNER=1
mkdir -p "$CA_DIR"
CA_DIR=$(cd "$CA_DIR" && pwd -P)                         # echter Pfad (relativ, C:/ vs /c/, Links)
case "$CA_DIR/" in "$PROJEKT/"*)
  [ -n "$NEUER_ORDNER" ] && rmdir "$CA_DIR" 2>/dev/null
  echo "FEHLER: FESTPOS_CA_DIR liegt im Projektordner – ca.key gehört ausserhalb (wird sonst mitkopiert)."; exit 1 ;;
esac
mkdir -p "$OUT"
chmod 700 "$CA_DIR"

# Altes Layout: ca.key lag in festpos-tls/ (und kam so evtl. auf die Karten) -> verschieben
if [ -f "$OUT/ca.key" ]; then
  if [ ! -f "$CA_DIR/ca.key" ]; then
    mv "$OUT/ca.key" "$CA_DIR/ca.key"
    [ -f "$OUT/ca.srl" ] && mv "$OUT/ca.srl" "$CA_DIR/"
    cp "$OUT/ca.crt" "$CA_DIR/ca.crt"
    echo "ca.key aus festpos-tls/ nach $CA_DIR verschoben (gehört nicht auf die Karten)."
  elif cmp -s "$OUT/ca.key" "$CA_DIR/ca.key"; then
    rm -f "$OUT/ca.key" "$OUT/ca.srl"
  else
    echo "FEHLER: festpos-tls/ca.key und $CA_DIR/ca.key sind verschieden – bitte von Hand klären."; exit 1
  fi
fi

if [ -n "$NEUE_CA" ] && [ -f "$CA_DIR/ca.key" ]; then
  ALT="$CA_DIR/alt-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$ALT"
  mv "$CA_DIR"/ca.* "$ALT/"
  echo "Bisherige Server-CA gesichert in $ALT"
fi
if [ ! -f "$CA_DIR/ca.key" ] && [ -f "$OUT/ca.crt" ] && [ -z "$NEUE_CA" ]; then
  # Es gibt schon eine Server-CA (auf allen Handys installiert), aber ihr Schlüssel ist nicht hier.
  # NICHT still eine neue erzeugen – sonst kommen nach dem Einspielen alle Handys nicht mehr auf die App.
  [ -n "$NEUER_ORDNER" ] && rmdir "$CA_DIR" 2>/dev/null
  echo "FEHLER: ca.key nicht gefunden in $CA_DIR."
  echo "        Liegt er z.B. auf dem USB-Stick:  FESTPOS_CA_DIR=/pfad/zum/ordner $0 $CONF"
  echo "        Nur wenn er verloren ist:          $0 $CONF --neue-ca   (dann ca.crt auf allen Geräten neu installieren)"
  exit 1
fi
if [ ! -f "$CA_DIR/ca.key" ]; then
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days "${CA_DAYS:-10950}" \
    -keyout "$CA_DIR/ca.key" -out "$CA_DIR/ca.crt" -subj "/CN=FestPOS Lokale CA $(date +%Y)/O=FestPOS" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "nameConstraints=$CONSTRAINTS"
  chmod 600 "$CA_DIR/ca.key"
  NEU=1
fi
cp "$CA_DIR/ca.crt" "$OUT/ca.crt"
if ! openssl x509 -in "$OUT/ca.crt" -noout -ext nameConstraints 2>/dev/null | grep -q Permitted; then
  echo "HINWEIS: Diese Server-CA ist noch ohne Namensbeschränkung (ältere Version). Bei Gelegenheit, nicht vor"
  echo "         einem Anlass:  $0 $CONF --neue-ca  (danach ca.crt auf allen Handys/PCs neu installieren)."
fi

if [ ! -f "$OUT/device-ca.key" ]; then
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days "${CA_DAYS:-10950}" \
    -keyout "$OUT/device-ca.key" -out "$OUT/device-ca.crt" -subj "/CN=FestPOS Geraete-CA/O=FestPOS" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign"
fi

enddate() {                      # Ablaufdatum -> Sekunden seit 1970 (GNU date oder macOS/BSD date)
  local d
  d=$(openssl x509 -in "$1" -noout -enddate | cut -d= -f2)
  date -d "$d" +%s 2>/dev/null || date -j -f "%b %e %H:%M:%S %Y %Z" "$d" +%s
}
CA_LEFT=$((($(enddate "$OUT/ca.crt") - $(date +%s)) / 86400))
# Laufzeit: so lange wie die CA (SERVER_DAYS). Nur Android/Windows: keine Höchstdauer.
# Apple-Geräte verlangen max. 825 Tage -> SERVER_DAYS=825.
DAYS=${SERVER_DAYS:-10950}
[ "$DAYS" -gt "$CA_LEFT" ] && DAYS=$CA_LEFT
# Geheimnis für die Prüfsummen im Kassenbuch und die Anmeldung: VOR dem neuen Schlüssel festschreiben, sonst hinge es
# am alten server.key (Platzhalter in festpos.conf) – aus dem bisherigen Schlüssel, bei der Ersteinrichtung zufällig
GEHEIM_AUS=""
if [ -f "$OUT/server.key" ]; then GEHEIM_AUS=$OUT/server.key; elif [ -n "${NEU:-}" ] && [ -z "$NEUE_CA" ]; then GEHEIM_AUS=--neu; fi
if [ -n "$GEHEIM_AUS" ] && festpos_secret_fix "$CONF" "$GEHEIM_AUS"; then
  echo "CLUSTER_SECRET in $CONF festgeschrieben (Prüfsummen bleiben prüfbar). Bei 3 Pi: diese festpos.conf auf alle Karten."
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
openssl req -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
  -keyout "$TMP/server.key" -out "$TMP/server.csr" -subj "/CN=festpos.local/O=FestPOS"
cat > "$TMP/server.ext" <<EXT
basicConstraints=CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth,clientAuth          # clientAuth: auch für die Verschlüsselung zwischen den Pi (RAFT_TLS)
subjectAltName=IP:$VIP,IP:$NODE_A_IP,IP:$NODE_B_IP,IP:$NODE_C_IP,IP:127.0.0.1,DNS:festpos.local,DNS:localhost
EXT
openssl x509 -req -in "$TMP/server.csr" -CA "$OUT/ca.crt" -CAkey "$CA_DIR/ca.key" \
  -CAserial "$CA_DIR/ca.srl" -CAcreateserial -days "$DAYS" -extfile "$TMP/server.ext" -out "$TMP/server.crt" 2>/dev/null
openssl verify -CAfile "$OUT/ca.crt" "$TMP/server.crt" >/dev/null || { echo "FEHLER: neues Server-Zertifikat ungültig"; exit 1; }
mv "$TMP/server.key" "$OUT/server.key"
mv "$TMP/server.crt" "$OUT/server.crt"
chmod 600 "$OUT/server.key" "$OUT/device-ca.key"

echo
echo "Server-Zertifikat: $DAYS Tage (CA gültig noch $CA_LEFT Tage)"
openssl x509 -in "$OUT/server.crt" -noout -subject -enddate -ext subjectAltName
echo
echo "Server-CA (Fingerabdruck zum Vergleich auf dem Handy):"
openssl x509 -in "$OUT/ca.crt" -noout -subject -fingerprint -sha256
echo
echo "festpos-tls/ -> auf die Boot-Partition ALLER Karten (alle Pi brauchen dieselben Dateien)."
echo "ca.key liegt in $CA_DIR – sicher aufbewahren, nicht auf die Karten."
if [ -n "${NEU:-}" ] && [ -n "$NEUE_CA" ]; then
  echo
  echo "NEUE Server-CA: auf jedem Handy und PC die alte «FestPOS Lokale CA» entfernen und ca.crt neu installieren"
  echo "(siehe ANLEITUNG «Neue Zertifizierungsstelle»). Die Gerätezertifikate der Handys bleiben gültig."
fi
