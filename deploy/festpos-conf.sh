# shellcheck shell=bash
# FestPOS – Konfiguration sicher lesen und schreiben (wird von den Skripten eingebunden, nicht ausgeführt).
#
# festpos.conf / node.conf / /etc/festpos/env werden ZEILENWEISE gelesen, nie als Shell-Code ausgeführt.
# Dadurch dürfen Passwörter beliebige Zeichen enthalten ($, &, Leerzeichen, Klammern, Umlaute).
#
#   KEY=Wert                 Wert bis zum Zeilenende; ein " # Kommentar" (mit Leerzeichen davor) wird entfernt
#   KEY="Wert mit # drin"    in Anführungszeichen: alles wörtlich (\" und \\ für " und \)
#   KEY='Wert'               in einfachen Anführungszeichen: alles wörtlich
#   # Kommentar, Leerzeilen, Windows-Zeilenenden: werden ignoriert
#
# Nur bekannte Namen werden übernommen (sonst könnte eine Zeile z.B. PATH verändern).

FESTPOS_CONF_KEYS="CLUSTER_SIZE NET NET_PREFIX NET_CIDR IFACE VIP NODE_A_IP NODE_B_IP NODE_C_IP VRRP_ID TZ PHONE_CERTS
ADMIN_PASSWORD ADMIN_PASSWORD_HASH ADMIN_PASSWORD_LAENGE CLUSTER_SECRET NODE NODE_IP JOIN_OTHERS CA_DAYS SERVER_DAYS SSH_FROM ADMIN_FROM RAFT_TLS
DHCP DHCP_POOL DHCP_GATEWAY DHCP_DNS RTC_AKKU INTERNET_GATEWAY INTERNET_DNS TUNNEL_TOKEN GATEWAY DNS NTP"

_festpos_unescape() {           # \x -> x (für Werte in doppelten Anführungszeichen)
  local s=$1 out="" i c
  for ((i = 0; i < ${#s}; i++)); do
    c=${s:i:1}
    if [[ $c == "\\" ]] && ((i + 1 < ${#s})); then i=$((i + 1)); c=${s:i:1}; fi
    out+=$c
  done
  printf '%s' "$out"
}

festpos_read_conf() {           # festpos_read_conf DATEI  -> setzt und exportiert die Variablen
  local file=$1 line key val n=0
  local LC_ALL=C                # Bytes statt Zeichen: auch Umlaute aus alten Windows-Editoren gehen nicht verloren
  [ -r "$file" ] || { echo "festpos: $file nicht lesbar" >&2; return 1; }
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    line=${line%$'\r'}
    ((n == 1)) && line=${line#$'\xEF\xBB\xBF'}               # BOM (Windows-Editor «UTF-8 mit BOM»)
    [[ $line =~ ^[[:space:]]*(#.*)?$ ]] && continue
    if [[ ! $line =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=(.*)$ ]]; then
      echo "festpos: $file Zeile $n nicht verstanden (ignoriert)" >&2      # Inhalt nicht zeigen (Passwort?)
      continue
    fi
    key=${BASH_REMATCH[1]}; val=${BASH_REMATCH[2]}
    case " ${FESTPOS_CONF_KEYS//$'\n'/ } " in
      *" $key "*) ;;
      *) if [[ $key != FESTPOS_* || $key == FESTPOS_CONF_KEYS ]]; then
           echo "festpos: unbekannter Eintrag $key in $file (ignoriert)" >&2; continue
         fi ;;
    esac
    if [[ $val =~ ^[[:space:]]*$ || $val =~ ^[[:space:]]+# ]]; then  # leer, oder nur " # Kommentar"
      val=""
    else
      val=${val#"${val%%[![:space:]]*}"}                        # führende Leerzeichen weg
    fi
    if [[ -z $val ]]; then
      :
    elif [[ $val =~ ^\"(.*)\"[[:space:]]*(#.*)?$ ]]; then
      val=$(_festpos_unescape "${BASH_REMATCH[1]}")
    elif [[ $val =~ ^\'([^\']*)\'[[:space:]]*(#.*)?$ ]]; then
      val=${BASH_REMATCH[1]}
    else
      val=${val%%[[:space:]]#*}                                # ab dem ersten " #": Kommentar
      val=${val%"${val##*[![:space:]]}"}                       # Leerzeichen am Ende weg
    fi
    printf -v "$key" '%s' "$val"
    export "${key?}"
  done < "$file"
}

festpos_env_line() {            # festpos_env_line KEY WERT -> KEY="..." (für systemd EnvironmentFile und festpos_read_conf)
  local v=$2
  v=${v//\\/\\\\}; v=${v//\"/\\\"}; v=${v//\$/\\\$}; v=${v//\`/\\\`}
  printf '%s="%s"\n' "$1" "$v"
}

festpos_ip_list() {             # festpos_ip_list "TEXT" -> nft-Liste «a, b/24, c-d» oder Rückgabe 1 bei Fehler
  local t=$1 out=() e a b
  t=${t//,/ }
  for e in $t; do
    if [[ $e == */* ]]; then
      a=${e%/*}; b=${e#*/}
      festpos_is_ip "$a" && [[ $b =~ ^[0-9]{1,2}$ ]] && ((b >= 8 && b <= 32)) || return 1
    elif [[ $e == *-* ]]; then
      festpos_is_ip "${e%-*}" && festpos_is_ip "${e#*-}" || return 1
      (($(festpos_ip2int "${e%-*}") <= $(festpos_ip2int "${e#*-}"))) || return 1      # nft: Anfang <= Ende
    else
      festpos_is_ip "$e" || return 1
    fi
    out+=("$e")
  done
  ((${#out[@]})) || return 1
  local IFS=,
  echo "${out[*]}" | sed 's/,/, /g'
}

festpos_secret_fix() {          # festpos_secret_fix FESTPOS.CONF SERVER.KEY|--neu -> 0 = CLUSTER_SECRET festgeschrieben
  # Steht in festpos.conf der Platzhalter, leitet FestPOS das Geheimnis aus server.key ab (app/core.py). Ein neuer
  # Schlüssel änderte damit den Schlüssel der Prüfsummen im Kassenbuch (alte nicht mehr prüfbar) und beendete alle
  # Anmeldungen. Darum VOR einem neuen Schlüssel festschreiben: das bisher abgeleitete (aus dem alten server.key) –
  # oder bei der Ersteinrichtung (--neu) ein zufälliges. Rückgabe 1: schon gesetzt oder nichts, woraus es ginge.
  local conf=$1 key=$2 cur val tmp
  cur=$( (festpos_read_conf "$conf" >/dev/null 2>&1 && printf '%s' "${CLUSTER_SECRET:-}") ) || return 1
  case "$cur" in ""|admin|bitte-aendern|bitte-aendern-lang-und-zufaellig) ;; *) return 1 ;; esac
  if [ "$key" = --neu ]; then
    val=$(openssl rand -hex 32) || return 1
  elif [ -f "$key" ]; then
    val="auto-$( { printf 'festpos-cluster-secret\0'; cat "$key"; } | sha256sum | cut -d' ' -f1)"
  else
    return 1
  fi
  tmp=$(mktemp "$conf.XXXXXX") || return 1
  if grep -qE '^[[:space:]]*CLUSTER_SECRET[[:space:]]*=' "$conf"; then
    awk -v v="$val" '/^[[:space:]]*CLUSTER_SECRET[[:space:]]*=/ && !d { print "CLUSTER_SECRET=" v; d = 1; next } { print }' \
      "$conf" > "$tmp"
  else
    { cat "$conf"; printf '\nCLUSTER_SECRET=%s\n' "$val"; } > "$tmp"
  fi
  cat "$tmp" > "$conf"                                 # Rechte und Besitzer der Datei bleiben
  rm -f "$tmp"
}

festpos_ip2int() { local IFS=. a b c d; read -r a b c d <<< "$1"; echo $(((a << 24) | (b << 16) | (c << 8) | d)); }

festpos_is_ip() { [[ $1 =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] &&
  ((BASH_REMATCH[1] <= 255 && BASH_REMATCH[2] <= 255 && BASH_REMATCH[3] <= 255 && BASH_REMATCH[4] <= 255)); }

festpos_int2ip() { echo "$((($1 >> 24) & 255)).$((($1 >> 16) & 255)).$((($1 >> 8) & 255)).$(($1 & 255))"; }

# Netz (T-0-N/FestPOS#2): NET=Adresse/Grösse (z.B. 10.20.0.0/22), frei wählbar bei der Ersteinrichtung. Ältere
# festpos.conf haben nur NET_PREFIX (drei Zahlen) und NET_CIDR. festpos_net_from_conf setzt daraus NET (genau die
# Netzadresse), NET_CIDR, NET_PREFIX (die ersten drei Zahlen der Netzadresse) und gibt 1 zurück, wenn es nicht passt.
festpos_net_from_conf() {
  local addr cidr m
  if [ -n "${NET:-}" ]; then
    [[ $NET =~ ^([0-9.]+)/([0-9]{1,2})$ ]] || return 1
    addr=${BASH_REMATCH[1]}; cidr=${BASH_REMATCH[2]}
  else
    addr="${NET_PREFIX:-}.0"; cidr=${NET_CIDR:-}
  fi
  festpos_is_ip "$addr" && [[ $cidr =~ ^[0-9]{1,2}$ ]] && ((cidr >= 8 && cidr <= 30)) || return 1
  m=$(((0xFFFFFFFF << (32 - cidr)) & 0xFFFFFFFF))
  if [ -n "${NET:-}" ] && (($(festpos_ip2int "$addr") & ~m & 0xFFFFFFFF)); then
    return 1                                              # «10.20.0.5/22»: nicht die Netzadresse
  fi
  addr=$(festpos_int2ip $(($(festpos_ip2int "$addr") & m)))
  NET="$addr/$cidr"; NET_CIDR=$cidr; NET_PREFIX=${addr%.*}
  export NET NET_CIDR NET_PREFIX
}

festpos_in_net() {              # festpos_in_net IP NETZ/GRÖSSE -> 0, wenn die Adresse im Netz liegt (nicht Netz/Broadcast)
  local ip=$1 net=${2%/*} cidr=${2#*/} m i n
  festpos_is_ip "$ip" || return 1
  m=$(((0xFFFFFFFF << (32 - cidr)) & 0xFFFFFFFF)); i=$(festpos_ip2int "$ip"); n=$(festpos_ip2int "$net")
  (((i & m) == n && i != n && i != (n | (~m & 0xFFFFFFFF)))) || return 1
}

# Gateway, DNS und Zeitserver für Pi und Handhelds (GATEWAY, DNS, NTP in festpos.conf):
#   GATEWAY leer/auto  automatisch wie bisher: Handhelds nach Verwaltung › Internet (Router oder VIP), der Pi über
#                      INTERNET_GATEWAY/INTERNET_DNS (älter), DHCP_GATEWAY/DHCP_DNS gelten weiter
#   GATEWAY=ohne       geschlossenes Netz: kein Gateway, kein DNS (Pi und Handhelds)
#   GATEWAY=IP         fester Router im FestPOS-Netz für Pi und Handhelds, DNS=IP[,IP] (leer: der Router)
#   NTP=Name oder IP   Zeitserver der Pi (leer: ch.pool.ntp.org); die Handhelds holen die Zeit immer von den Pi
# festpos_gateway setzt GW_ART (auto|fest|ohne), PI_GW, PI_DNS (für NetworkManager), DHCP_GW, DHCP_DNS_LIST (für
# festpos_dhcp_conf) und NTP_SERVER; gibt 1 zurück (und gilt automatisch), wenn GATEWAY oder DNS ungültig sind.
festpos_dns_list() {            # festpos_dns_list "a, b" -> «a,b» (höchstens zwei Adressen), sonst 1
  local liste="" d n=0
  for d in ${1//,/ }; do
    festpos_is_ip "$d" || return 1
    n=$((n + 1)); ((n <= 2)) || return 1
    liste="${liste:+$liste,}$d"
  done
  [ -n "$liste" ] || return 1
  echo "$liste"
}

# shellcheck disable=SC2034  # setzt Variablen für festpos-node.sh
festpos_gateway() {
  local g=${GATEWAY:-auto} d rc=0
  GW_ART=auto; PI_GW=""; PI_DNS=""; DHCP_GW=${DHCP_GATEWAY:-auto:$VIP}; DHCP_DNS_LIST=${DHCP_DNS:-1.1.1.1}
  NTP_SERVER=""
  if [ -n "${NTP:-}" ]; then
    if [[ $NTP =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$ ]]; then NTP_SERVER=$NTP; else rc=1; fi
  fi
  case "$g" in
    auto) ;;
    ohne) GW_ART=ohne; DHCP_GW=ohne; DHCP_DNS_LIST=""; return $rc ;;
    *)
      if festpos_in_net "$g" "$NET" && d=$(festpos_dns_list "${DNS:-$g}"); then
        GW_ART=fest; PI_GW=$g; PI_DNS=$d; DHCP_GW=$g; DHCP_DNS_LIST=$d
        return $rc
      fi
      rc=1 ;;
  esac
  if [ -n "${INTERNET_GATEWAY:-}" ]; then        # automatisch: Internet für den Pi wie bisher (T22)
    if festpos_in_net "$INTERNET_GATEWAY" "$NET"; then
      PI_GW=$INTERNET_GATEWAY; PI_DNS=${INTERNET_DNS:-$INTERNET_GATEWAY}
      festpos_is_ip "$PI_DNS" || PI_DNS=$INTERNET_GATEWAY
    else
      rc=1
    fi
  fi
  return $rc
}

FESTPOS_DHCP_HOSTS=/var/lib/festpos/dhcp          # feste Zuteilungen, schreibt die App (app/dhcp.py)
FESTPOS_DHCP_LEASES=/var/lib/festpos/dhcp.leases
FESTPOS_DHCP_OPTS=/var/lib/festpos/dhcp-opts      # Gateway der Handhelds bei «auto», schreibt die App (app/dhcp.py)

# festpos_dhcp_conf NETZ/GRÖSSE IFACE POOL GATEWAY DNS KNOTEN CLUSTER_SIZE [NTP] -> dnsmasq.conf (nur DHCP)
# (ältere Form: NET_PREFIX NET_CIDR statt NETZ/GRÖSSE – geht weiter)
# GATEWAY «auto:IP»: das Gateway der Handhelds folgt dem Weg ins Internet (Verwaltung › Internet) – die App
# schreibt es nach $FESTPOS_DHCP_OPTS (Startwert IP); eine feste IP gilt immer; «ohne»: kein Gateway (geschlossenes
# Netz). DNS: eine oder zwei Adressen mit Komma, leer = keiner.
# POOL «150-189»: Abstand zur Netzadresse (im /24 die letzte Stelle) oder ganze Adressen «10.20.1.1-10.20.3.200»;
# bei 3 Pi bekommt jeder ein Drittel (a, b, c), damit nie zwei Pi dieselbe Adresse vergeben. Feste Zuteilungen sind
# auf allen Pi gleich (aus der Datenbank).
festpos_dhcp_conf() {
  local net cidr iface pool gw dns node size ntp von bis n teil idx m base last mask auto=""
  if [[ $1 == */* ]]; then net=${1%/*}; cidr=${1#*/}; shift
  else net="$1.0"; cidr=$2; shift 2; fi
  iface=$1 pool=$2 gw=$3 dns=$4 node=$5 size=$6 ntp=${7:-}
  festpos_is_ip "$net" && [[ $cidr =~ ^[0-9]{1,2}$ ]] && ((cidr >= 8 && cidr <= 30)) || return 1
  m=$(((0xFFFFFFFF << (32 - cidr)) & 0xFFFFFFFF))
  base=$(($(festpos_ip2int "$net") & m)); last=$((base | (~m & 0xFFFFFFFF)))
  if [[ $gw == auto:* ]]; then auto=1; gw=${gw#auto:}; fi
  if [[ $pool =~ ^([0-9]{1,7})-([0-9]{1,7})$ ]]; then
    von=$((base + BASH_REMATCH[1])); bis=$((base + BASH_REMATCH[2]))
  elif [[ $pool =~ ^([0-9.]+)-([0-9.]+)$ ]]; then
    von=${BASH_REMATCH[1]}; bis=${BASH_REMATCH[2]}          # festpos_is_ip überschreibt BASH_REMATCH
    festpos_is_ip "$von" && festpos_is_ip "$bis" || return 1
    von=$(festpos_ip2int "$von"); bis=$(festpos_ip2int "$bis")
  else
    return 1
  fi
  ((von > base && bis < last && von <= bis)) || return 1
  [ "$gw" = ohne ] || festpos_is_ip "$gw" || return 1
  [ -z "$dns" ] || festpos_dns_list "$dns" >/dev/null || return 1
  [ -z "$ntp" ] || festpos_is_ip "$ntp" || return 1
  if [ "$size" = 3 ]; then
    n=$((bis - von + 1)); teil=$((n / 3)); ((teil >= 1)) || return 1
    case "$node" in a) idx=0 ;; b) idx=1 ;; c) idx=2 ;; *) return 1 ;; esac
    von=$((von + idx * teil)); ((idx == 2)) || bis=$((von + teil - 1))
  fi
  mask=$(festpos_int2ip "$m")
  cat <<EOF
# generiert von festpos-node.sh – nicht von Hand ändern (DHCP, DHCP_POOL, GATEWAY, DNS bzw. DHCP_GATEWAY, DHCP_DNS in festpos.conf)
port=0
interface=$iface
bind-dynamic
dhcp-range=$(festpos_int2ip "$von"),$(festpos_int2ip "$bis"),$mask,2m
$( if [ -n "$auto" ]; then echo "dhcp-optsdir=$FESTPOS_DHCP_OPTS"; elif [ "$gw" = ohne ]; then echo "dhcp-option=option:router"
   else echo "dhcp-option=option:router,$gw"; fi )
$( [ -n "$dns" ] && echo "dhcp-option=option:dns-server,$dns" || echo "dhcp-option=option:dns-server" )
$( [ -n "$ntp" ] && echo "dhcp-option=option:ntp-server,$ntp" )
dhcp-hostsdir=$FESTPOS_DHCP_HOSTS
dhcp-leasefile=$FESTPOS_DHCP_LEASES
user=festpos
group=festpos
EOF
}

# Vorgabe für DHCP_POOL, wenn festpos.conf keinen hat: im /24 und grösser wie bisher .150–.189 (ab der Netzadresse),
# in kleineren Netzen die zweite Hälfte (ohne Broadcast)
festpos_dhcp_pool_default() {   # festpos_dhcp_pool_default GRÖSSE -> «von-bis» (Abstand zur Netzadresse)
  local cidr=$1 n
  if ((cidr <= 24)); then echo "150-189"; else n=$((1 << (32 - cidr))); echo "$((n / 2))-$((n - 2))"; fi
}

# RTC-Batterie laden (T2): festpos_rtc_charge CONFIG.TXT 0|1 -> gibt «geändert» aus, wenn config.txt angepasst wurde.
# 1 nur bei der offiziellen, aufladbaren Batterie (SC1163): Dann steht genau ein markierter Block mit
# dtparam=rtc_bbat_vchg=3000000 am Ende (unter [all]); andere rtc_bbat_vchg-Zeilen werden ersetzt, beim ersten Ändern
# bleibt eine Kopie CONFIG.TXT.festpos-alt. 0 entfernt nur den eigenen Block. Wirkt erst nach dem nächsten Neustart.
FESTPOS_RTC_MARK="# FestPOS RTC_AKKU (festpos.conf): offizielle RTC-Batterie laden"
festpos_rtc_charge() {
  local f=$1 want=$2 neu
  [ -f "$f" ] || return 0
  # ohne eigenen Block und ohne fremde rtc_bbat_vchg-Zeilen
  neu=$(awk -v mark="$FESTPOS_RTC_MARK" '
    $0 == mark { skip = 2; next }
    skip > 0 { skip--; next }
    { print }' "$f")
  if [ "$want" = 1 ]; then
    neu=$(printf '%s\n' "$neu" | grep -v '^[[:space:]]*dtparam=rtc_bbat_vchg' || true)
    neu=$(printf '%s\n%s\n[all]\ndtparam=rtc_bbat_vchg=3000000' "$neu" "$FESTPOS_RTC_MARK")
  fi
  [ "$neu" = "$(cat "$f")" ] && return 0
  [ -f "$f.festpos-alt" ] || cp "$f" "$f.festpos-alt"
  printf '%s\n' "$neu" > "$f.neu" && mv "$f.neu" "$f"
  echo "geändert"
}

# fake-hwclock nur ohne funktionierende RTC (T16): festpos_fake_hwclock RTC_DIR DATENDATEI -> gibt «aus» bzw. «ein»
# aus, wenn es umgestellt wurde. Hält die RTC die Zeit (Batterie ab 2,5 V, wie RTC_WEAK_UV), stellte fake-hwclock die
# Uhr beim Start auf den zuletzt gespeicherten Stand – auch rückwärts über die richtige RTC-Zeit (Pi OS, gemessen am
# 8.11.2026 auf allen drei Pi). Dann werden seine Dienste maskiert und die gespeicherte Zeit gelöscht (festpos-settime
# speichert nur, solange es die Datei gibt). Ohne RTC oder mit leerer Batterie kommt es zurück: ungefähre Zeit statt 1970.
festpos_fake_hwclock() {
  local rtc=$1 data=$2 uv u units=() changed=""
  uv=$(cat "$rtc/battery_voltage" 2>/dev/null || cat "$rtc/device/battery_voltage" 2>/dev/null || echo 0)
  [[ $uv =~ ^[0-9]+$ ]] || uv=0
  mapfile -t units < <(systemctl list-unit-files --no-legend 'fake-hwclock*' 2>/dev/null | awk '{print $1}')
  if ((uv >= 2500000)); then
    for u in "${units[@]}"; do
      [ "$(systemctl is-enabled "$u" 2>/dev/null)" = masked ] && continue
      systemctl mask "$u" >/dev/null 2>&1 && changed=aus
    done
    [ -e "$data" ] && rm -f "$data" && changed=aus
  else
    for u in "${units[@]}"; do
      [ "$(systemctl is-enabled "$u" 2>/dev/null)" = masked ] || continue
      systemctl unmask "$u" >/dev/null 2>&1 && changed=ein
    done
  fi
  [ -z "$changed" ] || echo "$changed"
}

festpos_rq_member() {           # festpos_rq_member DATENVERZEICHNIS -> 0, wenn dieser Knoten Cluster-Mitglied ist
  # Sicher: Markierung (nach erfolgreichem Beitritt / Gründung geschrieben).
  # Für Knoten aus älteren Versionen ohne Markierung: Snapshots oder Tabellen vorhanden = war Mitglied.
  # Ein Knoten mit gescheitertem Beitritt hat zwar raft.db, aber weder Snapshots noch Tabellen.
  local d=$1
  [ -e "$d/.festpos-member" ] && return 0
  [ -e "$d/raft.db" ] || return 1
  [ -n "$(find "$d/wsnapshots" "$d/rsnapshots" "$d/snapshots" -mindepth 1 -print -quit 2>/dev/null)" ] && return 0
  [ -f "$d/db.sqlite" ] || return 1
  python3 - "$d/db.sqlite" <<'PY' 2>/dev/null
import sqlite3, sys
try:
    n = sqlite3.connect(sys.argv[1], timeout=1).execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
except Exception:
    sys.exit(1)
sys.exit(0 if n > 0 else 1)
PY
}

festpos_rq_verwerfen() {        # festpos_rq_verwerfen DATENVERZEICHNIS -> gibt den neuen Ort aus
  # Datenbank beiseite legen statt löschen (REV-030): DATA.verworfen-nr<N>-<Zeit>. Die drei mit den höchsten
  # Nummern bleiben. Aufgeräumt wird nach der Nummer, nicht nach der Uhrzeit – die Uhr eines Pi kann falsch gehen.
  local d=$1 ziel alt nr max=0 liste=()
  for alt in "$d".verworfen-nr*; do
    [[ -e $alt && ${alt##*.verworfen-nr} =~ ^([0-9]+)- ]] || continue
    nr=$((10#${BASH_REMATCH[1]}))
    ((nr > max)) && max=$nr
    liste+=("$nr $alt")
  done
  ziel="$d.verworfen-nr$((max + 1))-$(date +%Y%m%d-%H%M%S)"
  mv "$d" "$ziel" || return 1
  mkdir -p "$d" || return 1
  chown --reference="$ziel" "$d" 2>/dev/null || true
  chmod --reference="$ziel" "$d" 2>/dev/null || true
  if [ -f "$ziel/.festpos-node" ]; then cp -p "$ziel/.festpos-node" "$d/" || true; fi   # Rolle der Karte bleibt
  if ((${#liste[@]} > 2)); then                       # mit der neuen sind es dann mehr als drei
    printf '%s\n' "${liste[@]}" | sort -rn | tail -n +3 | while read -r _ alt; do
      [ "$alt" != "$ziel" ] && rm -rf "$alt"
    done
  fi
  echo "$ziel"
}

festpos_rq_einzelbetrieb() {    # festpos_rq_einzelbetrieb DATENVERZEICHNIS KNOTEN RAFT-ADRESSE
  # rqlite (gestoppt!) beim nächsten Start als alleiniges Mitglied wiederherstellen und das merken:
  # die Markierung schützt die Daten aus dem Einzelbetrieb vor den zurückkehrenden Pi (REV-029).
  local d=$1 node=$2 addr=$3
  mkdir -p "$d/raft"
  printf '[{"id":"%s","address":"%s","non_voter":false}]\n' "$node" "$addr" > "$d/raft/peers.json"
  date -Is > "$d/.festpos-einzelbetrieb"
  chown --reference="$d" "$d/raft" "$d/raft/peers.json" "$d/.festpos-einzelbetrieb" 2>/dev/null || true
}

festpos_fstab_private() {       # festpos_fstab_private FSTAB EINHÄNGEPUNKT -> 0 = geändert, 1 = schon so / nicht da
  # Boot-Partition (FAT) nur für root lesbar: fmask=0077,dmask=0077 (REV-026). Dort liegen festpos.conf
  # (Admin-Passwort) und die Schlüssel. Sonst kann sie jeder Benutzer lesen, z.B. der Kiosk-Benutzer.
  local fstab=$1 mp=$2 tmp
  [ -f "$fstab" ] || return 1
  tmp=$(mktemp "$fstab.XXXXXX") || return 1
  if ! awk -v mp="$mp" '
    BEGIN { changed = 0 }
    /^[[:space:]]*#/ || NF < 4 { print; next }
    $2 == mp && $3 == "vfat" {
      n = split($4, o, ","); out = ""
      for (i = 1; i <= n; i++) if (o[i] !~ /^(fmask|dmask|umask)=/) out = out (out == "" ? "" : ",") o[i]
      want = out (out == "" ? "" : ",") "fmask=0077,dmask=0077"
      if (want != $4) { $4 = want; changed = 1 }
      line = $1; for (i = 2; i <= NF; i++) line = line "  " $i
      print line; next
    }
    { print }
    END { exit changed ? 0 : 3 }' "$fstab" > "$tmp"; then
    rm -f "$tmp"; return 1                               # nichts zu ändern (oder Fehler): Datei bleibt
  fi
  cp -p "$fstab" "$fstab.festpos-alt"
  chmod --reference="$fstab" "$tmp" 2>/dev/null || chmod 644 "$tmp"
  mv "$tmp" "$fstab"
}

festpos_boot_private_now() {    # festpos_boot_private_now EINHÄNGEPUNKT -> sofort anwenden (neu einhängen)
  local mp=$1 mode
  mode=$(stat -c %a "$mp/festpos.conf" 2>/dev/null || stat -c %a "$mp/config.txt" 2>/dev/null || echo 0)
  ((8#$mode & 8#077)) || return 0                        # schon privat
  mount -o remount "$mp" 2>/dev/null || true
  mode=$(stat -c %a "$mp/festpos.conf" 2>/dev/null || stat -c %a "$mp/config.txt" 2>/dev/null || echo 0)
  ((8#$mode & 8#077)) || return 0
  if umount "$mp" 2>/dev/null; then mount "$mp" || return 1; fi
  mode=$(stat -c %a "$mp/festpos.conf" 2>/dev/null || stat -c %a "$mp/config.txt" 2>/dev/null || echo 0)
  ((8#$mode & 8#077)) && return 1
  return 0
}

festpos_wifi_prepare() {        # festpos_wifi_prepare BOOT -> WLAN nicht mehr ganz abschalten, aber Funk aus
  # Bis 2026.10.10-32 schaltete dtoverlay=disable-wifi das WLAN ganz ab. Für «Update aus dem Internet» bleibt es
  # vorhanden; der Funk ist aus (nmcli/rfkill) und nur während eines Updates an (festpos-update). Wirkt ab Neustart.
  local cfg="$1/config.txt" cmd="$1/cmdline.txt" rc=1
  if [ -f "$cfg" ] && grep -qE '^dtoverlay=disable-wifi[[:space:]]*$' "$cfg"; then
    sed -i -E '/^dtoverlay=disable-wifi[[:space:]]*$/d' "$cfg" && rc=0
  fi
  # WLAN-Land CH ab dem Start (Kanal 12/13 und 5 GHz; sonst findet der Pi manchen Router oder Hotspot nicht)
  if [ -f "$cmd" ] && ! grep -q "cfg80211.ieee80211_regdom=" "$cmd"; then
    sed -i -E '1 s/[[:space:]]*$/ cfg80211.ieee80211_regdom=CH/' "$cmd" && rc=0
  fi
  # Funk aus – aber nicht, solange «dauerhaft verbunden» über das WLAN läuft (z.B. mitten in einem Update)
  if command -v nmcli >/dev/null 2>&1 &&
     ! nmcli -t -f NAME connection show --active 2>/dev/null | grep -qx festpos-internet; then
    nmcli radio wifi off >/dev/null 2>&1 || true
  fi
  return $rc
}
