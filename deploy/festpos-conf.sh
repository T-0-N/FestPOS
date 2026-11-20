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

FESTPOS_CONF_KEYS="CLUSTER_SIZE NET_PREFIX NET_CIDR IFACE VIP NODE_A_IP NODE_B_IP NODE_C_IP VRRP_ID TZ PHONE_CERTS
ADMIN_PASSWORD CLUSTER_SECRET NODE NODE_IP JOIN_OTHERS CA_DAYS SERVER_DAYS SSH_FROM ADMIN_FROM RAFT_TLS
DHCP DHCP_POOL DHCP_GATEWAY DHCP_DNS RTC_AKKU INTERNET_GATEWAY INTERNET_DNS TUNNEL_TOKEN"

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

FESTPOS_DHCP_HOSTS=/var/lib/festpos/dhcp          # feste Zuteilungen, schreibt die App (app/dhcp.py)
FESTPOS_DHCP_LEASES=/var/lib/festpos/dhcp.leases
FESTPOS_DHCP_OPTS=/var/lib/festpos/dhcp-opts      # Gateway der Handhelds bei «auto», schreibt die App (app/dhcp.py)

# festpos_dhcp_conf NET_PREFIX NET_CIDR IFACE POOL GATEWAY DNS KNOTEN CLUSTER_SIZE -> dnsmasq.conf (nur DHCP)
# GATEWAY «auto:IP»: das Gateway der Handhelds folgt dem Weg ins Internet (Verwaltung › Internet) – die App
# schreibt es nach $FESTPOS_DHCP_OPTS (Startwert IP); eine feste IP (DHCP_GATEWAY in festpos.conf) gilt immer.
# POOL «150-189»: letzte Stelle der Adressen für neue Geräte; bei 3 Pi bekommt jeder ein Drittel (a, b, c), damit
# nie zwei Pi dieselbe Adresse vergeben. Feste Zuteilungen sind auf allen Pi gleich (aus der Datenbank).
festpos_dhcp_conf() {
  local prefix=$1 cidr=$2 iface=$3 pool=$4 gw=$5 dns=$6 node=$7 size=$8 ntp=${9:-} von bis n teil idx mask m i auto=""
  if [[ $gw == auto:* ]]; then auto=1; gw=${gw#auto:}; fi
  [[ $pool =~ ^([0-9]{1,3})-([0-9]{1,3})$ ]] || return 1
  von=${BASH_REMATCH[1]}; bis=${BASH_REMATCH[2]}
  ((von >= 1 && bis <= 254 && von <= bis)) || return 1
  festpos_is_ip "$gw" || return 1
  [ -z "$dns" ] || festpos_is_ip "$dns" || return 1
  [ -z "$ntp" ] || festpos_is_ip "$ntp" || return 1
  if [ "$size" = 3 ]; then
    n=$((bis - von + 1)); teil=$((n / 3)); ((teil >= 1)) || return 1
    case "$node" in a) idx=0 ;; b) idx=1 ;; c) idx=2 ;; *) return 1 ;; esac
    von=$((von + idx * teil)); ((idx == 2)) || bis=$((von + teil - 1))
  fi
  m=$((0xFFFFFFFF << (32 - cidr) & 0xFFFFFFFF)); mask=""
  for i in 24 16 8 0; do mask+="$(((m >> i) & 255))"; ((i)) && mask+="."; done
  cat <<EOF
# generiert von festpos-node.sh – nicht von Hand ändern (DHCP, DHCP_POOL, DHCP_GATEWAY, DHCP_DNS in festpos.conf)
port=0
interface=$iface
bind-dynamic
dhcp-range=$prefix.$von,$prefix.$bis,$mask,2m
$( [ -n "$auto" ] && echo "dhcp-optsdir=$FESTPOS_DHCP_OPTS" || echo "dhcp-option=option:router,$gw" )
$( [ -n "$dns" ] && echo "dhcp-option=option:dns-server,$dns" || echo "dhcp-option=option:dns-server" )
$( [ -n "$ntp" ] && echo "dhcp-option=option:ntp-server,$ntp" )
dhcp-hostsdir=$FESTPOS_DHCP_HOSTS
dhcp-leasefile=$FESTPOS_DHCP_LEASES
user=festpos
group=festpos
EOF
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
  local cfg="$1/config.txt" rc=1
  if [ -f "$cfg" ] && grep -qE '^dtoverlay=disable-wifi[[:space:]]*$' "$cfg"; then
    sed -i -E '/^dtoverlay=disable-wifi[[:space:]]*$/d' "$cfg" && rc=0
  fi
  if command -v nmcli >/dev/null 2>&1; then nmcli radio wifi off >/dev/null 2>&1 || true; fi
  return $rc
}
