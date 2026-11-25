#!/bin/bash
# FestPOS – wendet bei JEDEM Boot die Rolle aus /boot/firmware/node.conf an.
# Dadurch ist ein Kartentausch: Karte rein, Strom an, fertig.
#
# Erzeugt: Hostname, feste IP, /etc/festpos/env, TLS-Dateien, keepalived.conf,
#          chrony.conf, nftables.conf, dnsmasq.conf (DHCP), neue SSH-Hostkeys bei Rollenwechsel.
set -euo pipefail

BOOT=${FESTPOS_BOOT:-/boot/firmware}
[ -d "$BOOT" ] || BOOT=/boot
CONF="$BOOT/festpos.conf"
NODECONF="$BOOT/node.conf"
ETC=/etc/festpos
# Arbeitskopien mit Geheimnissen (env, Schlüssel, Tunnel) nur im Arbeitsspeicher, /etc/festpos verweist dorthin
# (T-0-N/FestPOS#3). Ist der Datenbereich verschlüsselt, hat festpos-krypt die geheimen Werte aus festpos.conf (dort
# steht nur «im-container») und die Schlüssel beim Start nach $QUELLE entpackt.
GRUN=/run/festpos-geheim
QUELLE=$GRUN/quelle
GAST_RUN=/run/festpos-gast

log() { echo "festpos-node: $*"; }
die() { log "FEHLER: $*"; exit 1; }

# Konfiguration LESEN, nicht ausführen: Passwörter dürfen jedes Zeichen enthalten ($ & Leerzeichen …)
# shellcheck source=festpos-conf.sh
source "$(dirname "$0")/festpos-conf.sh"

# --- Ersteinrichtung im Browser (T11): ohne festpos.conf ---------------------------------------------------
# Dann laufen App, Datenbank und https nicht (ConditionPathExists=!/run/festpos/einrichtung), sondern nur
# festpos-einrichtung.service auf http Port 80. Erreichbar: per DHCP vom Heimrouter (Adresse auf dem Bildschirm,
# festpos.local) und immer zusätzlich unter 192.168.55.10. Antwortet kein Router, verteilt der Pi selbst Adressen
# (Laptop direkt am Festplatz-Netz). Schutz: Einrichtungscode (Bildschirm, festpos-einrichtung.txt auf der Karte).
# --- Härtung von SSH und sudo (T23): Schlüssel von der Boot-Partition, nur Schlüssel, sudo mit Passwort ----------
FESTPOS_BOOT=$BOOT /usr/local/sbin/festpos-haertung || log "WARNUNG: Härtung von SSH/sudo gescheitert"

EINR_IP=192.168.55.10
EINR_DIR=/var/lib/festpos/einrichtung
if [ ! -f "$CONF" ]; then
  if [ "${1:-}" = "--pruefen" ]; then echo "Ersteinrichtung offen (keine festpos.conf) – Einrichtung im Browser."; exit 0; fi
  log "keine $CONF – Ersteinrichtung im Browser"
  install -d /run/festpos
  touch /run/festpos/einrichtung
  rm -f /run/festpos/cluster
  install -d -o festpos -g festpos -m 0700 /run/festpos/wiederherstellen /run/festpos/wiederherstellen-neu
  install -d -g festpos -m 0750 /run/festpos/wiederherstellen-ergebnis
  install -d -o root -g festpos -m 0750 "$EINR_DIR"
  if [ ! -s "$EINR_DIR/code" ]; then
    C=$(tr -dc 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789' < /dev/urandom | head -c 8 || true)
    (umask 027; echo "${C:0:4}-${C:4:4}" > "$EINR_DIR/code")
    chown root:festpos "$EINR_DIR/code"
  fi
  CODE=$(cat "$EINR_DIR/code")
  IFACE=${IFACE:-eth0}
  if [ "$(hostname)" != festpos ]; then
    hostnamectl set-hostname festpos 2>/dev/null || hostname festpos
    sed -i "s/^127\.0\.1\.1.*/127.0.1.1\tfestpos/" /etc/hosts
  fi
  # Netz: DHCP vom Router und zusätzlich fest 192.168.55.10
  for _ in $(seq 30); do nmcli -t general status >/dev/null 2>&1 && break; sleep 1; done
  CON=$(nmcli -g GENERAL.CONNECTION device show "$IFACE" 2>/dev/null | head -1 || true)
  if [ -z "$CON" ]; then
    CON="festpos-$IFACE"
    nmcli con show "$CON" >/dev/null 2>&1 || nmcli con add type ethernet ifname "$IFACE" con-name "$CON" >/dev/null || true
  fi
  nmcli con mod "$CON" ipv4.method auto ipv4.addresses "$EINR_IP/24" connection.autoconnect yes 2>/dev/null || true
  nmcli con up "$CON" >/dev/null 2>&1 || true
  ROUTER=""
  for _ in $(seq 20); do
    ROUTER=$(nmcli -g IP4.ADDRESS device show "$IFACE" 2>/dev/null | tr '|' '\n' | sed 's/^ *//' | grep -v "^$EINR_IP/" | grep -v "^$" | head -1 || true)
    [ -n "$ROUTER" ] && break
    sleep 1
  done
  install -d -m 0750 -o root -g festpos "$ETC"
  rm -f "$ETC/dnsmasq.conf"
  if [ -z "$ROUTER" ] && command -v dnsmasq >/dev/null &&
     festpos_dhcp_conf 192.168.55 24 "$IFACE" 150-189 "$EINR_IP" 1.1.1.1 a 1 "$EINR_IP" > "$ETC/dnsmasq.conf.neu"; then
    mv "$ETC/dnsmasq.conf.neu" "$ETC/dnsmasq.conf"          # kein Router: der Pi verteilt die Adressen selbst
    log "kein DHCP-Server im Netz – Pi verteilt Adressen (192.168.55.150–189)"
  fi
  rm -f "$ETC/dnsmasq.conf.neu"
  cat > /etc/nftables.conf <<EOF
#!/usr/sbin/nft -f
# generiert von festpos-node.sh (Ersteinrichtung)
flush ruleset
table inet festpos {
    chain input {
        type filter hook input priority 0; policy drop;
        iif lo accept
        ct state established,related accept
        ip protocol icmp accept
        tcp dport { 80, 443 } accept
        tcp dport 22 ct state new limit rate 6/minute burst 6 packets accept   # SSH: Bremse (T23)
        udp dport { 67, 68, 5353 } accept
    }
}
EOF
  nft -f /etc/nftables.conf || log "WARNUNG: Firewall-Regeln konnten nicht geladen werden"
  systemctl start avahi-daemon 2>/dev/null || true             # festpos.local
  ADR=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v ":" | grep -v "^$" | tr '\n' ' ' || true)
  # Vorläufiges, selbst signiertes Zertifikat nur für die Einrichtung (https; der Browser warnt einmal). Bei jedem
  # Start neu, damit die aktuelle Adresse drin steht; der Fingerabdruck steht zum Vergleichen auf dem Bildschirm.
  SAN="DNS:festpos.local,DNS:festpos,IP:$EINR_IP,IP:127.0.0.1"
  for a in $ADR; do [ "$a" = "$EINR_IP" ] || SAN="$SAN,IP:$a"; done
  install -d -o root -g festpos -m 0750 "$EINR_DIR/tls"
  FP=""
  if openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 60 \
       -subj "/CN=FestPOS Ersteinrichtung" -addext "subjectAltName=$SAN" \
       -keyout "$EINR_DIR/tls/key.pem.neu" -out "$EINR_DIR/tls/cert.pem.neu" >/dev/null 2>&1; then
    chown root:festpos "$EINR_DIR/tls/key.pem.neu" "$EINR_DIR/tls/cert.pem.neu"
    chmod 0640 "$EINR_DIR/tls/key.pem.neu"; chmod 0644 "$EINR_DIR/tls/cert.pem.neu"
    mv "$EINR_DIR/tls/key.pem.neu" "$EINR_DIR/tls/key.pem"; mv "$EINR_DIR/tls/cert.pem.neu" "$EINR_DIR/tls/cert.pem"
    FP=$(openssl x509 -in "$EINR_DIR/tls/cert.pem" -noout -fingerprint -sha256 | cut -d= -f2 | cut -c1-23)
  else
    log "WARNUNG: vorläufiges Zertifikat nicht erzeugt – Einrichtung über http"
  fi
  P=https; [ -n "$FP" ] || P=http
  TEXT="FestPOS – Pi ist noch nicht eingerichtet.
Im Browser öffnen: $P://festpos.local/  oder  $P://${ADR%% *}/  (fest: $P://$EINR_IP/)
Einrichtungscode: $CODE${FP:+
Der Browser warnt vor dem Zertifikat (vorläufig, nur für die Einrichtung). Fingerabdruck SHA-256 beginnt mit: $FP}"
  install -d /etc/issue.d
  printf '\n%s\n\n' "$TEXT" > /etc/issue.d/festpos.issue        # Bildschirm am Pi (Anmeldeaufforderung)
  # tty1 zeigt den Text nur beim Start (ohne Anmeldung, festpos-haertung): lief getty schon, neu zeigen
  who 2>/dev/null | grep -qw tty1 || systemctl try-restart getty@tty1.service 2>/dev/null || true
  printf '%s\n' "$TEXT" | sed 's/$/\r/' > "$BOOT/festpos-einrichtung.txt" 2>/dev/null || true   # am PC lesbar
  log "Einrichtungscode: $CODE, Adressen: $ADR"
  exit 0
fi
[ -f "$NODECONF" ] || die "$NODECONF fehlt"
# Eingerichtet: Spuren der Ersteinrichtung entfernen (Code, Hinweise)
rm -f /run/festpos/einrichtung "$BOOT/festpos-einrichtung.txt" /etc/issue.d/festpos.issue "$EINR_DIR/code"
rm -rf "$EINR_DIR/tls"                                    # vorläufiges Zertifikat der Ersteinrichtung
# Notfall-Passwort nur als Prüfwert (T-0-N/FestPOS#15): steht es noch im Klartext in festpos.conf, durch
# ADMIN_PASSWORD_HASH (scrypt, Salz aus CLUSTER_SECRET) und ADMIN_PASSWORD_LAENGE ersetzen (app/emergency.py)
if [ -x /opt/festpos/venv/bin/python3 ] && grep -qE '^[[:space:]]*ADMIN_PASSWORD[[:space:]]*=' "$CONF" &&
   ! grep -qE '^[[:space:]]*ADMIN_PASSWORD[[:space:]]*=[[:space:]]*"?im-container' "$CONF"; then
  /opt/festpos/venv/bin/python3 -I -c 'import sys; sys.path.insert(0, "/opt/festpos"); sys.dont_write_bytecode = True
from app.emergency import main; sys.exit(main(sys.argv[1:]))' --conf "$CONF" --tls-key "$BOOT/festpos-tls/server.key" ||
    log "WARNUNG: Notfall-Passwort nicht in einen Prüfwert umgewandelt – bleibt vorerst im Klartext"
fi
festpos_read_conf "$CONF"     || die "$CONF nicht lesbar"
festpos_read_conf "$NODECONF" || die "$NODECONF nicht lesbar"
# Geheime Werte aus dem verschlüsselten Bereich (T-0-N/FestPOS#3) – was dort fehlt, bleibt leer
if [ -f "$QUELLE/festpos.conf" ]; then
  festpos_read_conf "$QUELLE/festpos.conf" || die "geheime Werte (festpos-krypt) nicht lesbar"
fi
for v in CLUSTER_SECRET ADMIN_PASSWORD ADMIN_PASSWORD_HASH ADMIN_PASSWORD_LAENGE TUNNEL_TOKEN; do
  [ "${!v-}" = im-container ] && unset "$v"
done

# Werte prüfen, bevor sie in Netzwerk-, Firewall- und keepalived-Dateien landen
for v in VIP NODE_A_IP NODE_B_IP NODE_C_IP; do festpos_is_ip "${!v:-}" || die "$v ist keine gültige IP-Adresse: '${!v:-}'"; done
# Netz (T-0-N/FestPOS#2): NET=Adresse/Grösse, ältere festpos.conf mit NET_PREFIX und NET_CIDR gehen weiter
festpos_net_from_conf || die "Netz ungültig: NET='${NET:-}' bzw. NET_PREFIX='${NET_PREFIX:-}', NET_CIDR='${NET_CIDR:-}' (z.B. NET=10.20.0.0/22)"
for v in VIP NODE_A_IP NODE_B_IP NODE_C_IP; do festpos_in_net "${!v}" "$NET" || die "$v=${!v} liegt nicht im Netz $NET"; done
[[ ${IFACE:-} =~ ^[A-Za-z0-9._-]{1,15}$ ]] || die "IFACE ungültig: '${IFACE:-}'"
[[ ${VRRP_ID:-55} =~ ^[0-9]{1,3}$ ]] && ((${VRRP_ID:-55} >= 1 && ${VRRP_ID:-55} <= 255)) || die "VRRP_ID muss 1..255 sein"
[[ ${TZ:-Europe/Zurich} =~ ^[A-Za-z0-9_/+-]+$ ]] || die "TZ ungültig: '${TZ:-}'"
[[ ${PHONE_CERTS:-1} =~ ^[01]$ ]] || die "PHONE_CERTS muss 0 oder 1 sein"
[[ ${RTC_AKKU:-0} =~ ^[01]$ ]] || { log "WARNUNG: RTC_AKKU muss 0 oder 1 sein (ist: '${RTC_AKKU}') – gilt als 0"; RTC_AKKU=0; }
[[ ${RAFT_TLS:-0} =~ ^[01]$ ]] || die "RAFT_TLS muss 0 oder 1 sein"
[ -n "${ADMIN_PASSWORD:-}${ADMIN_PASSWORD_HASH:-}" ] || die "ADMIN_PASSWORD fehlt"
# Notfall-Passwort: der Platzhalter sperrt den Notfall-Login (die App weist ihn ab); kürzer als 16 Zeichen sperrt
# ihn, sobald es einen eigenen Admin-Benutzer gibt (prüft die App) – hier nur die Warnung
if [ -n "${ADMIN_PASSWORD_HASH:-}" ]; then
  [[ ${ADMIN_PASSWORD_HASH} =~ ^scrypt\$[0-9]+\$[0-9]+\$[0-9]+\$[0-9a-f]+\$[0-9a-f]+$ ]] || die "ADMIN_PASSWORD_HASH ungültig"
  [ "${ADMIN_PASSWORD_LAENGE:-0}" -ge 16 ] 2>/dev/null ||
    log "WARNUNG: Notfall-Passwort kürzer als 16 Zeichen – ein längeres als ADMIN_PASSWORD in festpos.conf eintragen"
else
  case "$ADMIN_PASSWORD" in
    admin|bitte-aendern) log "WARNUNG: ADMIN_PASSWORD ist noch der Platzhalter – Notfall-Login gesperrt, in festpos.conf ändern" ;;
    *) [ "${#ADMIN_PASSWORD}" -ge 16 ] || log "WARNUNG: ADMIN_PASSWORD ist kürzer als 16 Zeichen – in festpos.conf ändern" ;;
  esac
fi
# SSH nur von diesen Adressen (REV-050), z.B. «192.168.55.200-192.168.55.254» (Admin-PC) – leer: ganzes Netz
# Ein Tippfehler darf den Pi nicht lahmlegen: dann gilt das ganze Netz (mit Warnung).
SSH_SET=""
if [ -n "${SSH_FROM:-}" ]; then
  SSH_SET=$(festpos_ip_list "$SSH_FROM") ||
    { log "WARNUNG: SSH_FROM ungültig: '$SSH_FROM' (IP, IP/24 oder IP-IP mit Komma) – SSH vorerst aus dem ganzen Netz"; SSH_SET=""; }
fi

# Verwaltung (Port 8443) nur von diesen Adressen – leer: ganzes Netz. Die App prüft das (Zertifikat-Download für neue
# Handys und die Pi untereinander bleiben offen). Ein Tippfehler darf niemanden aussperren: dann ganzes Netz.
ADMIN_SET=""
if [ -n "${ADMIN_FROM:-}" ]; then
  ADMIN_SET=$(festpos_ip_list "$ADMIN_FROM") ||
    { log "WARNUNG: ADMIN_FROM ungültig: '$ADMIN_FROM' (IP, IP/24 oder IP-IP mit Komma) – Verwaltung vorerst aus dem ganzen Netz"; ADMIN_SET=""; }
fi

NODE=$(echo "${NODE:-}" | tr '[:upper:]' '[:lower:]')
case "$NODE" in
  a) NODE_IP=$NODE_A_IP ;;
  b) NODE_IP=$NODE_B_IP ;;
  c) NODE_IP=$NODE_C_IP ;;
  *) die "NODE muss a, b oder c sein (ist: '$NODE')" ;;
esac
CLUSTER_SIZE=${CLUSTER_SIZE:-3}
case "$CLUSTER_SIZE" in
  1) [ "$NODE" = a ] || die "Einzelbetrieb (CLUSTER_SIZE=1) nur mit NODE=a" ;;
  3) ;;
  *) die "CLUSTER_SIZE muss 1 oder 3 sein" ;;
esac
ALL_IPS=("$NODE_A_IP" "$NODE_B_IP" "$NODE_C_IP")
OTHER_IPS=()
for ip in "${ALL_IPS[@]}"; do [ "$ip" != "$NODE_IP" ] && OTHER_IPS+=("$ip"); done
log "Rolle $NODE, IP $NODE_IP, VIP $VIP, Cluster-Grösse $CLUSTER_SIZE"
# --- Übergangszeit nach einem Netz-Umzug (T-0-N/FestPOS#2, festpos-netz): bis NET_ALT_BIS hat jeder Pi zusätzlich
# seine alte Adresse (und die alte VIP) mit den alten Zertifikaten – Handhelds, die beim Umzug aus waren, kommen so
# noch herein. Danach (beim nächsten Start) weg: Einträge aus festpos.conf, festpos-tls-alt/.
ALT_NET=""; ALT_CIDR=""; ALT_NODE_IP=""; ALT_VIP=""
if [ -n "${NET_ALT:-}" ] && [ "${1:-}" != "--pruefen" ]; then
  ALT_IP_VAR="NODE_${NODE^^}_IP_ALT"
  if [[ ${NET_ALT_BIS:-} =~ ^[0-9]{9,11}$ ]] && [ "$(date +%s)" -lt "$NET_ALT_BIS" ] &&
     [[ $NET_ALT =~ ^([0-9.]+)/([0-9]{1,2})$ ]] && festpos_is_ip "${BASH_REMATCH[1]}" &&
     festpos_in_net "${!ALT_IP_VAR:-}" "$NET_ALT" && festpos_in_net "${VIP_ALT:-}" "$NET_ALT"; then
    ALT_NET=$NET_ALT; ALT_CIDR=${NET_ALT#*/}; ALT_NODE_IP=${!ALT_IP_VAR}; ALT_VIP=$VIP_ALT
    log "Übergang nach dem Netz-Umzug: zusätzlich $ALT_NODE_IP und $ALT_VIP ($ALT_NET) bis $(date -d "@$NET_ALT_BIS" '+%d.%m. %H:%M')"
  else
    log "Übergang nach dem Netz-Umzug vorbei – alte Adressen weg"
    sed -i -E '/^[[:space:]]*(NET_ALT|VIP_ALT|NODE_[ABC]_IP_ALT|NET_ALT_BIS)[[:space:]]*=/d' "$CONF"
    rm -rf "$BOOT/festpos-tls-alt"
  fi
fi
SRC_SET=$NET; [ -n "$ALT_NET" ] && SRC_SET="{ $NET, $ALT_NET }"
# festpos-node --pruefen: nur lesen und prüfen, nichts ändern (update.sh ruft das auf, damit ein Fehler
# in festpos.conf nicht erst beim nächsten Neustart – vielleicht am Anlass – auffällt)
if [ "${1:-}" = "--pruefen" ]; then
  echo "Konfiguration in Ordnung."
  if [ -r "$ETC/env" ]; then
    (
      festpos_read_conf "$ETC/env" 2>/dev/null || exit 0
      [ "${FESTPOS_ADMIN_PASSWORD-}" = "${ADMIN_PASSWORD:-}" ] || [ -n "${ADMIN_PASSWORD_HASH:-}" ] ||
        echo "ACHTUNG: Das Admin-Passwort wird ab dem nächsten Neustart anders gelesen als bisher." \
             "Sonderzeichen in festpos.conf prüfen (Wert ggf. in \"…\" setzen)."
      [ "${FESTPOS_CLUSTER_SECRET-}" = "${CLUSTER_SECRET:-}" ] ||
        echo "ACHTUNG: CLUSTER_SECRET wird ab dem nächsten Neustart anders gelesen – bei 3 Pi alle zusammen neu starten."
    )
  fi
  exit 0
fi

# --- Zeitzone & Hostname ----------------------------------------------------
timedatectl set-timezone "${TZ:-Europe/Zurich}" 2>/dev/null || ln -sf "/usr/share/zoneinfo/${TZ:-Europe/Zurich}" /etc/localtime
HOST="festpos-$NODE"
if [ "$(hostname)" != "$HOST" ]; then
  hostnamectl set-hostname "$HOST"
  sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$HOST/" /etc/hosts
  grep -q "^127.0.1.1" /etc/hosts || echo -e "127.0.1.1\t$HOST" >> /etc/hosts
fi

# --- SSH-Hostkeys: geklonte Karten sollen nicht identische Schlüssel haben ----
# Auch ohne Schlüssel (neue Karte aus «SD-Karte schreiben», T10, oder aus dem Image, T12)
if [ "$(cat /etc/ssh/.festpos-node 2>/dev/null)" != "$NODE" ] || ! compgen -G "/etc/ssh/ssh_host_*_key" >/dev/null; then
  rm -f /etc/ssh/ssh_host_*
  ssh-keygen -A >/dev/null
  echo "$NODE" > /etc/ssh/.festpos-node
  log "neue SSH-Hostkeys erzeugt"
fi

# --- Feste IP (NetworkManager) ---------------------------------------------
for _ in $(seq 30); do nmcli -t general status >/dev/null 2>&1 && break; sleep 1; done
nmcli -t general status >/dev/null 2>&1 || { log "NetworkManager läuft nicht – starte ihn"; systemctl start NetworkManager || true; sleep 3; }
CON=$(nmcli -g GENERAL.CONNECTION device show "$IFACE" 2>/dev/null | head -1 || true)
if [ -z "$CON" ]; then
  CON="festpos-$IFACE"
  nmcli con show "$CON" >/dev/null 2>&1 || nmcli con add type ethernet ifname "$IFACE" con-name "$CON"
fi
# Einzelbetrieb: VIP fest auf diesem Pi (kein keepalived). Cluster: keepalived verwaltet die VIP.
if [ "$CLUSTER_SIZE" = 1 ]; then WANT="$NODE_IP/$NET_CIDR,$VIP/$NET_CIDR"; else WANT="$NODE_IP/$NET_CIDR"; fi
if [ -n "$ALT_NET" ]; then                                 # Übergang nach dem Netz-Umzug: alte Adresse zusätzlich
  WANT="$WANT,$ALT_NODE_IP/$ALT_CIDR"
  [ "$CLUSTER_SIZE" = 1 ] && WANT="$WANT,$ALT_VIP/$ALT_CIDR"
fi
# Internet für den Pi (T22 QR-Bestellung: Payrexx und Cloudflare Tunnel): INTERNET_GATEWAY = Mobilfunk-Router im
# Festnetz. Leer: kein Internet (wie bisher).
GW=""; DNS=""
if [ -n "${INTERNET_GATEWAY:-}" ]; then
  if festpos_in_net "$INTERNET_GATEWAY" "$NET"; then
    GW=$INTERNET_GATEWAY; DNS=${INTERNET_DNS:-$INTERNET_GATEWAY}
    festpos_is_ip "$DNS" || DNS=$INTERNET_GATEWAY
  else
    log "WARNUNG: INTERNET_GATEWAY ungültig (muss im Netz $NET liegen): '$INTERNET_GATEWAY' – kein Internet"
  fi
fi
HAVE=$(nmcli -g ipv4.addresses con show "$CON" 2>/dev/null | tr -d ' ' || true)
METHOD=$(nmcli -g ipv4.method con show "$CON" 2>/dev/null || true)
HAVE_GW=$(nmcli -g ipv4.gateway con show "$CON" 2>/dev/null || true)
if [ "$HAVE" != "$WANT" ] || [ "$METHOD" != "manual" ] || [ "$HAVE_GW" != "$GW" ]; then
  log "setze feste IP $WANT auf $CON${GW:+ (Internet über $GW)}"
  nmcli con mod "$CON" ipv4.method manual ipv4.addresses "$WANT" ipv4.gateway "$GW" ipv4.dns "$DNS" \
        ipv6.method disabled connection.autoconnect yes
  nmcli con up "$CON" >/dev/null || true
fi

# Funk aus (stört sonst den AP). An ist er nur kurz für «Update aus dem Internet» (festpos-update); bricht das ab,
# bleibt er so höchstens bis zum nächsten Start an.
nmcli radio wifi off >/dev/null 2>&1 || true

# --- TLS (gleiches Zertifikat auf allen Knoten, liegt auf der Boot-Partition) --
# Die Schlüssel kommen von der Boot-Partition oder – Daten verschlüsselt – aus dem Container (festpos-krypt). Die
# Arbeitskopien liegen nur im Arbeitsspeicher; /etc/festpos/tls verweist dorthin (T-0-N/FestPOS#3).
key_src() { if [ -f "$BOOT/festpos-tls/$1" ]; then echo "$BOOT/festpos-tls/$1"; elif [ -f "$QUELLE/$1" ]; then echo "$QUELLE/$1"; fi; }
install -d -m 0750 -o root -g festpos "$ETC" "$GRUN" "$GRUN/tls"
if [ -d "$ETC/tls" ] && [ ! -L "$ETC/tls" ]; then rm -rf "$ETC/tls"; fi
ln -sfn "$GRUN/tls" "$ETC/tls"
if [ -f "$BOOT/festpos-tls/server.crt" ] && [ -n "$(key_src server.key)" ]; then
  install -m 0644 "$BOOT/festpos-tls/server.crt" "$BOOT/festpos-tls/ca.crt" "$ETC/tls/"
  install -m 0640 -o root -g festpos "$(key_src server.key)" "$ETC/tls/server.key"
  rm -f "$ETC/tls/alt-server.crt" "$ETC/tls/alt-ca.crt"
  if [ -n "$ALT_NET" ] && [ -f "$BOOT/festpos-tls-alt/server.crt" ]; then   # Übergang: gleicher Schlüssel, alte CA
    install -m 0644 "$BOOT/festpos-tls-alt/server.crt" "$ETC/tls/alt-server.crt"
    [ -f "$BOOT/festpos-tls-alt/ca.crt" ] && install -m 0644 "$BOOT/festpos-tls-alt/ca.crt" "$ETC/tls/alt-ca.crt"
  fi
  # Zum PRÜFEN der Handys genügt device-ca.crt; den Schlüssel braucht es nur zum Ausstellen (REV-027).
  if [ -f "$BOOT/festpos-tls/device-ca.crt" ]; then
    install -m 0644 "$BOOT/festpos-tls/device-ca.crt" "$ETC/tls/"
    if [ -n "$(key_src device-ca.key)" ]; then
      install -m 0640 -o root -g festpos "$(key_src device-ca.key)" "$ETC/tls/device-ca.key"
    else
      rm -f "$ETC/tls/device-ca.key"
      log "HINWEIS: device-ca.key fehlt – Handys werden geprüft, aber dieser Pi kann keine neuen Gerätezertifikate ausstellen"
    fi
  elif [ "${PHONE_CERTS:-1}" = 1 ]; then
    log "WARNUNG: device-ca.crt fehlt – Gerätezertifikate AUS, jedes Gerät im WLAN kann bestellen. make-certs.sh erneut ausführen."
    PHONE_CERTS=0
    PHONE_CERTS_PROBLEM="Gerätezertifikate aus (device-ca.crt fehlt): jedes Gerät im WLAN kann bestellen"
  fi
else
  log "WARNUNG: $BOOT/festpos-tls/ fehlt – HTTPS (und damit QR-Scanner) geht nicht"
fi

# --- Verschlüsselung zwischen den Pi (rqlite-Knoten-TLS) -----------------------
# Gegenseitige Prüfung mit dem Server-Zertifikat (gleich auf allen Karten). Es braucht dafür «clientAuth»;
# ältere Zertifikate (vor 2026.10.10-6) haben das nicht -> dann bleibt es aus, auf allen Pi gleich.
RAFT_TLS=${RAFT_TLS:-0}
if [ "$RAFT_TLS" = 1 ]; then
  if ! openssl x509 -in "$ETC/tls/server.crt" -noout -ext extendedKeyUsage 2>/dev/null | grep -q "Client Authentication"; then
    log "WARNUNG: RAFT_TLS=1, aber das Server-Zertifikat taugt nicht dafür (make-certs.sh neu ausführen) – Verschlüsselung zwischen den Pi AUS"
    RAFT_TLS=0
  fi
fi

# --- Datenbank-Verzeichnis gehört zu genau einer Rolle -----------------------
# Wurde eine Karte von einem LAUFENDEN Knoten geklont und umbenannt (a -> b), darf sie dessen
# Raft-Identität nicht übernehmen: dann Daten verwerfen, der Knoten holt sie frisch vom Cluster.
DATA=/var/lib/festpos/rqlite
install -d -o festpos -g festpos "$DATA"
MARK="$DATA/.festpos-node"
if [ -f "$MARK" ] && [ "$(cat "$MARK")" != "$NODE" ]; then
  if [ "$CLUSTER_SIZE" = 1 ]; then
    die "Datenbank gehört Knoten '$(cat "$MARK")', diese Karte ist '$NODE' – im Einzelbetrieb wird nichts gelöscht"
  fi
  log "WARNUNG: Datenbank gehörte Knoten '$(cat "$MARK")' – wird beiseite gelegt und neu synchronisiert"
  ZIEL=$(festpos_rq_verwerfen "$DATA") || die "alte Datenbank konnte nicht beiseite gelegt werden"   # REV-030
  log "alte Datenbank: $ZIEL"
fi
echo "$NODE" > "$MARK"; chown festpos:festpos "$MARK"

# --- Markierung auf der Bootpartition: diesen Pi frisch in den Cluster holen ----------
# Nach einem Notfall-Einzelbetrieb am PC eine leere Datei «festpos-neu-beitreten» auf die Karte von B und C
# legen: dann bilden sie nie mit ihrem alten Stand eine Mehrheit, egal in welcher Reihenfolge eingeschaltet wird.
for f in "$BOOT/festpos-neu-beitreten" "$BOOT/festpos-neu-beitreten.txt"; do
  [ -e "$f" ] || continue
  if [ "$CLUSTER_SIZE" = 1 ]; then
    log "WARNUNG: $f im Einzelbetrieb ignoriert (nichts, wovon neu geladen werden könnte)"
  elif [ -e "$DATA/.festpos-einzelbetrieb" ]; then
    log "WARNUNG: $f ignoriert – dieser Pi lief im Einzelbetrieb und hat die aktuellen Daten"
  elif ZIEL=$(festpos_rq_verwerfen "$DATA"); then
    log "$f gefunden – alte Datenbank beiseite gelegt: $ZIEL; Pi tritt neu bei"
  else
    log "FEHLER: $f gefunden, aber die Datenbank konnte nicht beiseite gelegt werden – Markierung bleibt"
    continue
  fi
  rm -f "$f"
done

# --- Umgebung für rqlited und die App ----------------------------------------
JOIN_OTHERS=$(printf "%s:4002," "${OTHER_IPS[@]}"); JOIN_OTHERS=${JOIN_OTHERS%,}
install -d /run/festpos
install -d -o festpos -g festpos -m 0700 /run/festpos/zeit /run/festpos/zeit-neu   # Uhrzeit-Aufträge der App
install -d -m 0755 /run/festpos/zeit-ergebnis                                         # Antworten (festpos-settime)
install -d -m 0755 /run/festpos/handy-usb                                             # Zeitserver per USB (T2)

# --- RTC-Batterie laden (T2): nur mit RTC_AKKU=1 (offizielle, aufladbare Batterie SC1163) ----------------------
if [ -f "$BOOT/config.txt" ] && [ -n "$(festpos_rtc_charge "$BOOT/config.txt" "${RTC_AKKU:-0}")" ]; then
  log "config.txt: RTC-Batterie laden $([ "${RTC_AKKU:-0}" = 1 ] && echo ein || echo aus) – wirkt nach dem nächsten Neustart"
  touch /run/festpos/rtc-neustart
fi
# --- fake-hwclock nur ohne funktionierende RTC (T16): sonst stellt es die richtige RTC-Zeit beim Start zurück ------
case "$(festpos_fake_hwclock /sys/class/rtc/rtc0 /etc/fake-hwclock.data)" in
  aus) log "fake-hwclock aus: die RTC hält die Zeit (wirkt ab dem nächsten Start)" ;;
  ein) log "fake-hwclock wieder ein: keine RTC oder RTC-Batterie leer" ;;
esac
install -d -o festpos -g festpos -m 0700 /run/festpos/wiederherstellen /run/festpos/wiederherstellen-neu  # Aufträge (T8)
install -d -g festpos -m 0750 /run/festpos/wiederherstellen-ergebnis                 # Antworten (festpos-wiederherstellen)
if [ "$CLUSTER_SIZE" = 1 ]; then
  PEERS="https://$NODE_IP:8443"
  rm -f /run/festpos/cluster                    # keepalived startet nicht (ConditionPathExists)
else
  PEERS=$(printf "https://%s:8443," "${ALL_IPS[@]}"); PEERS=${PEERS%,}
  touch /run/festpos/cluster
fi
umask 027
# Werte gequotet (systemd-EnvironmentFile-Format); gelesen wird die Datei nur mit festpos_read_conf
{
  echo "# generiert von festpos-node.sh – nicht von Hand ändern (Quelle: $CONF)"
  festpos_env_line FESTPOS_NODE "$NODE"
  festpos_env_line NODE_IP "$NODE_IP"
  festpos_env_line FESTPOS_IFACE "$IFACE"                # Drucker-Netz (festpos-druckernetz, app/printer_net.py)
  festpos_env_line FESTPOS_NET_CIDR "$NET_CIDR"
  festpos_env_line FESTPOS_NET "$NET"                    # Netz mit Grösse (app/netz.py, T-0-N/FestPOS#2)
  festpos_env_line JOIN_OTHERS "$JOIN_OTHERS"
  festpos_env_line CLUSTER_SIZE "$CLUSTER_SIZE"
  festpos_env_line FESTPOS_DB "rqlite://127.0.0.1:4001"
  if [ -n "${ADMIN_PASSWORD_HASH:-}" ]; then           # Prüfwert (T-0-N/FestPOS#15); sonst ältere Konfiguration
    festpos_env_line FESTPOS_ADMIN_PASSWORD_HASH "$ADMIN_PASSWORD_HASH"
    festpos_env_line FESTPOS_ADMIN_PASSWORD_LEN "${ADMIN_PASSWORD_LAENGE:-0}"
  else
    festpos_env_line FESTPOS_ADMIN_PASSWORD "$ADMIN_PASSWORD"
  fi
  festpos_env_line FESTPOS_CLUSTER_SECRET "${CLUSTER_SECRET:-}"
  festpos_env_line FESTPOS_ADMIN_FROM "$ADMIN_SET"
  festpos_env_line FESTPOS_PEERS "$PEERS"
  festpos_env_line FESTPOS_CA "$ETC/tls/ca.crt"
  festpos_env_line FESTPOS_PHONE_CERTS "${PHONE_CERTS:-1}"
  festpos_env_line FESTPOS_RTC_AKKU "${RTC_AKKU:-0}"
  festpos_env_line FESTPOS_VIP "$VIP"
  festpos_env_line FESTPOS_RAFT_TLS "$RAFT_TLS"
  festpos_env_line FESTPOS_PHONE_CERTS_PROBLEM "${PHONE_CERTS_PROBLEM:-}"
  festpos_env_line FESTPOS_TLS_CERT "$ETC/tls/server.crt"
  festpos_env_line FESTPOS_NET_ALT "$ALT_NET"             # Übergang nach dem Netz-Umzug (app/serve.py, app/netz.py)
  if [ -n "$ALT_NET" ] && [ -f "$ETC/tls/alt-server.crt" ]; then
    festpos_env_line FESTPOS_LISTEN "$NODE_IP,$VIP,127.0.0.1"
    festpos_env_line FESTPOS_LISTEN_ALT "$ALT_NODE_IP,$ALT_VIP"
    festpos_env_line FESTPOS_TLS_ALT_CERT "$ETC/tls/alt-server.crt"
  fi
  festpos_env_line FESTPOS_TLS_KEY "$ETC/tls/server.key"
  festpos_env_line FESTPOS_DEVICE_CA "$ETC/tls/device-ca.crt"
  festpos_env_line FESTPOS_DEVICE_CA_KEY "$ETC/tls/device-ca.key"
} > "$GRUN/env.neu"
mv "$GRUN/env.neu" "$GRUN/env"
chown root:festpos "$GRUN/env"
[ -L "$ETC/env" ] || rm -f "$ETC/env"                   # früher als Datei auf der Karte
ln -sfn "$GRUN/env" "$ETC/env"
umask 022

# --- keepalived: VIP folgt dem Raft-Leader -----------------------------------
PEER_LINES=$(printf "        %s\n" "${OTHER_IPS[@]}")
cat > /etc/keepalived/keepalived.conf <<EOF
# generiert von festpos-node.sh – nicht von Hand ändern
global_defs {
    router_id festpos-$NODE
    enable_script_security
    script_user nobody
}

vrrp_script chk_leader {
    # 200 nur auf dem Knoten, der Raft-Leader ist und druckt
    script "/usr/bin/curl -skf --max-time 1 https://127.0.0.1:8443/api/health/leader"
    interval 1
    fall 2
    rise 1
    weight 50
}

vrrp_instance FESTPOS {
    state BACKUP
    interface $IFACE
    virtual_router_id $VRRP_ID
    priority 100
    advert_int 1
    unicast_src_ip $NODE_IP
    unicast_peer {
$PEER_LINES
    }
    virtual_ipaddress {
        $VIP/$NET_CIDR dev $IFACE
${ALT_VIP:+        $ALT_VIP/$ALT_CIDR dev $IFACE}
    }
    track_script {
        chk_leader
    }
}
EOF

# --- chrony: Knoten halten die Zeit untereinander synchron (ohne Internet) ----
PEER_NTP=""
[ "$CLUSTER_SIZE" = 3 ] && PEER_NTP=$(printf "peer %s iburst\n" "${OTHER_IPS[@]}")
cat > /etc/chrony/chrony.conf <<EOF
# generiert von festpos-node.sh
$PEER_NTP
# Mit Internet (Update, Router) holt sich der Pi die Zeit selbst; offline findet chrony den Server einfach nicht (T2)
pool ch.pool.ntp.org iburst maxsources 2
local stratum 10 orphan
makestep 1 -1
rtcsync
driftfile /var/lib/chrony/chrony.drift
allow $NET
${ALT_NET:+allow $ALT_NET}
EOF

# --- DHCP für die Handys (dnsmasq, nur DHCP) ------------------------------------
# festpos-dhcp.service läuft nur, wenn es /etc/festpos/dnsmasq.conf gibt. Die festen Zuteilungen schreibt die App.
DHCP_RULE=""
rm -f "$ETC/dnsmasq.conf.neu"
if [ "${DHCP:-1}" = 1 ]; then
  if ! command -v dnsmasq >/dev/null; then
    log "WARNUNG: DHCP=1, aber dnsmasq fehlt (einmal mit Internet: sudo apt install dnsmasq-base) – DHCP aus"
    rm -f "$ETC/dnsmasq.conf"
  elif festpos_dhcp_conf "$NET" "$IFACE" "${DHCP_POOL:-$(festpos_dhcp_pool_default "$NET_CIDR")}" "${DHCP_GATEWAY:-auto:$VIP}" \
      "${DHCP_DNS:-1.1.1.1}" "$NODE" "$CLUSTER_SIZE" "$VIP" > "$ETC/dnsmasq.conf.neu"; then
    mv "$ETC/dnsmasq.conf.neu" "$ETC/dnsmasq.conf"
    install -d -o festpos -g festpos -m 0755 "$FESTPOS_DHCP_HOSTS"
    if [ -z "${DHCP_GATEWAY:-}" ]; then                 # Gateway automatisch: Startwert der Pi (VIP), danach die App
      install -d -o festpos -g festpos -m 0755 "$FESTPOS_DHCP_OPTS"
      [ -s "$FESTPOS_DHCP_OPTS/festpos-gateway" ] || {
        echo "option:router,$VIP" > "$FESTPOS_DHCP_OPTS/festpos-gateway"
        chown festpos:festpos "$FESTPOS_DHCP_OPTS/festpos-gateway"; }
    fi
    DHCP_RULE="        udp dport 67 accept                                   # DHCP-Anfragen (Handys, DHCP=1)"
  else
    log "WARNUNG: DHCP_POOL, DHCP_GATEWAY oder DHCP_DNS ungültig (z.B. 150-189, 192.168.55.1, 1.1.1.1) – DHCP aus"
    rm -f "$ETC/dnsmasq.conf"
  fi
else
  rm -f "$ETC/dnsmasq.conf"
fi
[[ ${DHCP:-1} =~ ^[01]$ ]] || log "WARNUNG: DHCP muss 0 oder 1 sein (ist: '${DHCP}')"

# --- QR-Bestellung am Tisch (T22): Gästedienst und Cloudflare Tunnel ---------------------------------------------
# Token aus festpos.conf (TUNNEL_TOKEN, hat Vorrang) oder aus der Verwaltung (festpos-update legt ihn in
# tunnel-app.env ab). Benutzer, Gruppe und Firewall des Gästedienstes gibt es immer: So lässt sich der Tunnel in der
# Verwaltung ohne Neustart einschalten (die App muss schon beim Start in der Gruppe sein). Der Gästedienst darf nur
# FestPOS auf 127.0.0.1:8443 erreichen; sein Schlüssel ist pro Pi zufällig ($GAST_RUN/token, Arbeitsspeicher) und
# liegt nur da, solange der Tunnel eingeschaltet ist. Tunnel-Token ebenso nur im Arbeitsspeicher ($GRUN/tunnel.env).
id festpos-gast >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin festpos-gast
id -nG festpos 2>/dev/null | grep -qw festpos-gast || usermod -aG festpos-gast festpos
install -d -o root -g festpos-gast -m 0750 "$GAST_RUN"
rm -rf /etc/festpos-gast                                  # früher auf der Karte
[ -L "$ETC/tunnel.env" ] || rm -f "$ETC/tunnel.env"
ln -sfn "$GRUN/tunnel.env" "$ETC/tunnel.env"
TUNNEL_SRC=""
if [ -n "${TUNNEL_TOKEN:-}" ]; then
  if [[ $TUNNEL_TOKEN =~ ^[A-Za-z0-9+/_-]{60,2000}={0,2}$ ]]; then
    (umask 077; printf 'TUNNEL_TOKEN=%s\n' "$TUNNEL_TOKEN" > "$GRUN/tunnel.env")
    touch "$ETC/tunnel-conf"; chmod 0644 "$ETC/tunnel-conf"     # für App und festpos-update: festpos.conf hat Vorrang
    TUNNEL_SRC=festpos.conf
  else
    log "WARNUNG: TUNNEL_TOKEN in festpos.conf ist kein Token von Cloudflare – Tunnel aus"
    rm -f "$ETC/tunnel-conf"
  fi
else
  rm -f "$ETC/tunnel-conf"
fi
TUNNEL_APP=$ETC/tunnel-app.env; [ -s "$QUELLE/tunnel-app.env" ] && TUNNEL_APP=$QUELLE/tunnel-app.env
if [ -z "$TUNNEL_SRC" ] && [ -s "$TUNNEL_APP" ]; then
  (umask 077; cp "$TUNNEL_APP" "$GRUN/tunnel.env")
  TUNNEL_SRC=Verwaltung
fi
GAST_UID=$(id -u festpos-gast)
# cloudflared (Review 2026-11, S4): eigener Benutzer statt DynamicUser, damit die Firewall ihn kennt. Wohin der Tunnel
# zeigt, bestimmt das Cloudflare-Konto – auf dem Pi darf cloudflared darum nur den Gästedienst (127.0.0.1:8090)
# erreichen: nicht die Datenbank (rqlite, 127.0.0.1:4001, ohne Anmeldung), nicht FestPOS selbst, nicht die Drucker,
# Handhelds oder den Router im Festnetz. Offen bleiben DNS (der Router im Festnetz kann der DNS-Server sein) und das
# Internet (Cloudflare).
id festpos-tunnel >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin festpos-tunnel
TUNNEL_UID=$(id -u festpos-tunnel)
GAST_RULES="    chain output {
        type filter hook output priority 0; policy accept;
        meta skuid $GAST_UID ct state established,related accept   # Antworten an cloudflared (Port 8090)
        meta skuid $GAST_UID ip daddr 127.0.0.1 tcp dport 8443 accept   # nur FestPOS selbst
        meta skuid $GAST_UID drop                                 # sonst nichts (nicht rqlite, nicht ins Netz)
        meta skuid $TUNNEL_UID ct state established,related accept
        meta skuid $TUNNEL_UID ip daddr 127.0.0.1 tcp dport 8090 accept   # cloudflared: lokal nur der Gästedienst
        meta skuid $TUNNEL_UID udp dport 53 accept                # DNS
        meta skuid $TUNNEL_UID tcp dport 53 accept
        meta skuid $TUNNEL_UID oifname \"lo\" drop                 # sonst nichts auf diesem Pi (rqlite, FestPOS, SSH)
        meta skuid $TUNNEL_UID ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 100.64.0.0/10 } drop   # kein privates Netz
        meta skuid $TUNNEL_UID ip6 daddr { fc00::/7, fe80::/10 } drop
    }"
if [ -n "$TUNNEL_SRC" ]; then
  if [ ! -s "$GAST_RUN/token" ]; then
    (umask 037; head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' > "$GAST_RUN/token")
  fi
  chown root:festpos-gast "$GAST_RUN/token"; chmod 0640 "$GAST_RUN/token"
  log "QR-Bestellung: Tunnel ein (Token aus $TUNNEL_SRC)"
  command -v cloudflared >/dev/null ||
    log "HINWEIS: Tunnel eingeschaltet, cloudflared fehlt noch – FestPOS installiert es, sobald der Pi Internet hat"
  systemctl enable festpos-gast.service festpos-tunnel.service >/dev/null 2>&1 || true
else
  systemctl disable --now festpos-tunnel.service festpos-gast.service >/dev/null 2>&1 || true
  rm -f "$GRUN/tunnel.env" "$GAST_RUN/token"
fi

# --- Firewall ------------------------------------------------------------------
# Raft (rqlite, ohne eigene Anmeldung) und VRRP nur im Cluster – und nur von den ANDEREN Knoten.
# Im Einzelbetrieb bleiben beide zu, sonst könnte jemand mit der IP .12/.13 direkt an die Datenbank.
[ -n "$SSH_SET" ] || SSH_SET="$NET${ALT_NET:+, $ALT_NET}"
CLUSTER_RULES=""
if [ "$CLUSTER_SIZE" = 3 ]; then
  PEERS_SET=$(IFS=,; echo "${OTHER_IPS[*]}")
  CLUSTER_RULES="        ip saddr { $PEERS_SET } tcp dport 4002 accept        # rqlite Raft
        ip saddr { $PEERS_SET } ip protocol 112 accept       # VRRP (keepalived)"
fi
cat > /etc/nftables.conf.neu <<EOF
#!/usr/sbin/nft -f
# generiert von festpos-node.sh
flush ruleset
table inet festpos {
    chain input {
        type filter hook input priority 0; policy drop;
        iif lo accept
        ct state established,related accept
        ct state invalid drop
        ip protocol icmp accept
        ip saddr $SRC_SET tcp dport { 80, 443, 8443 } accept
        ip saddr { $SSH_SET } tcp dport 22 ct state new limit rate 6/minute burst 6 packets accept   # SSH (SSH_FROM), Bremse (T23)
$CLUSTER_RULES
        ip saddr $SRC_SET udp dport 123 accept # NTP
        udp sport 67 udp dport 68 accept                      # DHCP-Antworten («Update aus dem Internet»: WLAN/USB)
$DHCP_RULE
    }
$GAST_RULES
}
EOF

# Erst prüfen, dann ersetzen: eine fehlerhafte Datei würde sonst bei jedem Start gar keine Firewall laden
if nft -c -f /etc/nftables.conf.neu; then
  mv /etc/nftables.conf.neu /etc/nftables.conf
elif grep -q "generiert von festpos-node.sh" /etc/nftables.conf 2>/dev/null; then
  log "WARNUNG: neue Firewall-Regeln fehlerhaft – die bisherigen bleiben aktiv (/etc/nftables.conf.neu prüfen)"
else
  # noch keine FestPOS-Regeln (erster Start): lieber eine einfache, sichere Grundregel als gar keine
  log "WARNUNG: Firewall-Regeln fehlerhaft – einfache Grundregel aktiv (nur lokales Netz, /etc/nftables.conf.neu prüfen)"
  cat > /etc/nftables.conf <<EOF
#!/usr/sbin/nft -f
# generiert von festpos-node.sh (Notfall-Grundregel)
flush ruleset
table inet festpos {
    chain input {
        type filter hook input priority 0; policy drop;
        iif lo accept
        ct state established,related accept
        ip protocol icmp accept
        ip saddr $NET tcp dport { 80, 443, 8443 } accept
        ip saddr $NET tcp dport 22 ct state new limit rate 6/minute burst 6 packets accept
        ip saddr $NET udp dport 123 accept
    }
}
EOF
fi
nft -f /etc/nftables.conf || log "WARNUNG: Firewall-Regeln konnten nicht geladen werden"
# Verbindungen ins Internet (T-0-N/FestPOS#10): gleich mit den zuletzt aufgelösten Adressen, ohne auf DNS zu warten
# («flush ruleset» oben hat die Tabelle entfernt); festpos-firewall.timer löst danach neu auf.
[ -x /usr/local/sbin/festpos-firewall ] && { /usr/local/sbin/festpos-firewall --ohne-dns ||
  log "WARNUNG: Firewall ins Internet nicht geladen (festpos-firewall)"; }
# Betriebssystem (T-0-N/FestPOS#5): ein unterbrochenes apt (Strom weg) zu Ende bringen, Stand für die Verwaltung
[ -x /usr/local/sbin/festpos-os-update ] && { /usr/local/sbin/festpos-os-update --reparieren ||
  log "WARNUNG: unterbrochenes Update des Betriebssystems nicht repariert (Verwaltung › Wartung)"; }

# --- Boot-Partition nur für root lesbar (Passwort, Schlüssel; REV-026) ---------------
# Zuletzt, damit ein Problem beim neu Einhängen nichts von oben verhindert.
if festpos_fstab_private /etc/fstab "$BOOT"; then
  log "Boot-Partition wird nur noch für root lesbar eingehängt (fmask/dmask in /etc/fstab)"
fi
festpos_boot_private_now "$BOOT" || log "HINWEIS: Boot-Partition ist erst nach dem nächsten Neustart nur für root lesbar"

log "fertig"
