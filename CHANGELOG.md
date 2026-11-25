# Änderungen

Alle wichtigen Änderungen an FestPOS. Versionen nach [Semantic Versioning](https://semver.org/lang/de/):
MAJOR.MINOR.PATCH – PATCH für Fehlerbehebungen, MINOR für neue Funktionen, MAJOR, wenn ein Update eine Neuinstallation
oder Umstellung braucht. Testversionen heissen `X.Y.Z-rc.N` und sind älter als `X.Y.Z`.

Neue Einträge zuerst unter «Unveröffentlicht» sammeln; beim Release wird daraus die neue Version.

## [Unveröffentlicht]

### Neu
- **Netz im Betrieb umziehen.** Geräte & Netz › Pi › «Netz umziehen …»: planen (Prüfung, Checkliste was automatisch und
  was von Hand geht), neue CA für das neue Netz (Server-Schlüssel und Gerätezertifikate bleiben), CA über Fully auf die
  Handhelds, alle Pi stellen gleichzeitig um (rqlite mit den neuen Adressen), Adressen in Einstellungen, Druckern und
  DHCP-Zuteilungen umgerechnet. Bestätigt wird über die neue Adresse innert 10 Minuten, sonst stellen alle Pi selbst
  zurück. Die alten Adressen bleiben 24 Stunden mit den alten Zertifikaten (Handhelds, die aus waren). Sicherungen aus
  dem alten Netz werden umgerechnet, ihre Schlüssel nie eingespielt.
- **Netz bei der Ersteinrichtung frei wählen.** Netz mit Grösse (z.B. `10.20.0.0/22`, /16 bis /27), Adressen der Pi und
  DHCP-Bereich; die Seite zeigt den Umfang und schlägt passende Adressen vor, geprüft wird auf Überschneidungen (auch
  mit dem Druckernetz). `festpos.conf` hat dafür `NET`; ältere Dateien mit `NET_PREFIX`/`NET_CIDR` gehen weiter.
  Firewall, DHCP, Zeitserver, Zertifikate und die Vorschläge in der Verwaltung rechnen mit dem gewählten Netz.

### Sicherheit
- **Korrekturen nach Review:** Eine unlesbare Liste zurückgezogener Update-Schlüssel sperrt alle Schlüssel (statt sie
  zu übergehen); beim Schlüsselwechsel zuerst die neuen ablegen. Zweiter Faktor: ein eingeschalteter lässt sich nicht
  mit der Sitzung allein ersetzen, jeder Code gilt auch bei gleichzeitigen Anfragen nur einmal. Bootloader: Werte aus
  EEPROM-Abschnitten, die für diesen Pi nicht gelten, zählen nicht. Geheimnisse der Einstellungen werden erst
  verschlüsselt geschrieben, wenn alle Pi die neue Version haben (das Umschlüsseln startete im Betrieb bisher nie).
  Firewall: zuerst die strengen Regeln laden, dann DNS; Zähler bleiben über ein Neuladen erhalten. Sicherheitsupdates:
  jede Paketquelle muss aktuell sein, abgebrochene Installationen werden zuerst repariert. Boot-Partition: nach einem
  Neustart jede Prüfsumme neu rechnen.
- **Daten verschlüsselt, Schlüssel im Chip des Pi.** Datenbank, Druckaufträge, Gastdaten und alle Geheimnisse
  (`CLUSTER_SECRET`, Notfall-Passwort, Tunnel-Token, `server.key`, `device-ca.key`) liegen in einem verschlüsselten
  Container (LUKS2, AES-XTS) auf der bestehenden Partition; in `festpos.conf` steht dafür nur noch `im-container`, ein
  am PC neu eingetragener Wert wird beim nächsten Start übernommen. Der Schlüssel liegt einmalig im Chip des Pi (OTP) –
  eine gestohlene Karte ist ohne genau diesen Pi unlesbar. Ausnahme: Kann ein Pi keinen Schlüssel im Chip speichern
  (älteres Modell oder alte Firmware), liegt er auf der Karte – verschlüsselt, aber mit der Karte allein lesbar; die
  Verwaltung zeigt das an. Neue Pi verschlüsseln sich beim ersten Start selbst (vorläufig, bis ihr
  Wiederherstellungs-Schlüssel in der Verwaltung gesichert ist; bis dahin ist die Karte allein lesbar); bestehende Pi werden unter Geräte &
  Netz › Pi «Umstellen …» umgestellt (Schlüssel drucken, zwei Neustarts, Rückfall bei einem Fehler). Mit dem
  Wiederherstellungs-Schlüssel lässt sich die Karte eines defekten Pi in einem anderen Pi oder am Linux-PC lesen.
  Arbeitskopien der Geheimnisse (`/etc/festpos/env`, `tls`, Tunnel, Schlüssel des Gästedienstes) liegen nur noch im
  Arbeitsspeicher. Das Update-Paket bringt `cryptsetup` für ältere Images mit.
- **Sicherheitsupdates für das Betriebssystem.** Das Update-Paket bringt die Sicherheitsupdates von Debian und
  Raspberry Pi für die Pakete des Images mit (in der CI aus den offiziellen, signierten Quellen geholt); jeder Pi spielt
  sie nach dem FestPOS-Update ohne Internet ein und repariert ein unterbrochenes apt beim nächsten Start selbst.
  Wartung › Update zeigt pro Pi Stand, Ergebnis, Kernel und «Neustart nötig»; mit Internet geht es auch direkt (nur
  Sicherheitsquellen). Hinweis in der Übersicht, wenn der Stand älter als 6 Monate ist. Grenze für das Paket: 600 MB.
- **Saison abschliessen.** Unter Tagesabschluss entsteht ein verschlüsseltes Archiv aller Tagesabschlüsse und
  Quittungen der Saison (unverändert, mit Prüfsummen, dazu jeder Abschluss als PDF). Nach dem Sichern auf den
  USB-Stick (zurückgelesen) oder dem Herunterladen und Wiederhochladen löscht FestPOS die Quittungen samt Gastdaten
  auf dem Pi; die Nummern laufen weiter. Erinnerung in der Übersicht nach 3 Monaten. Am PC: `tools/archiv.py`.
- **Quittung über CHF 400 nur mit Name und Ort** des Gastes (Art. 26 MWSTG) statt der ganzen Adresse.
- **Firewall ins Internet mit drei Modi.** Die Pi selbst verbinden sich nur noch mit Payrexx, Cloudflare (Tunnel,
  Paketquelle), GitHub, den Paketquellen von Debian und Raspberry Pi, Zeitservern und DNS; alles andere wird
  verworfen und gezählt (Netz & Internet › Firewall, pro Pi mit dem zuletzt verworfenen Ziel). «Beobachten» erlaubt
  alles und zählt nur, «Offen» wie bisher – beide höchstens 24 Stunden. Die Handhelds sind nicht betroffen.
- **Sicherheitsereignisse in der Verwaltung.** SSH-Anmeldungen (Benutzer, Adresse), `sudo`, Neustarts, geänderte
  Dateien auf der Boot-Partition (Prüfsumme beim Start), abgewiesene fremd signierte Updates, Anmeldesperren und
  Notfall-Logins stehen im Protokoll (Häkchen «Nur Sicherheit») und als gelbe Karte oben in der Übersicht, bis
  jemand «Gesehen» klickt – auf allen Pi, innert einer Minute. Gesammelt vom neuen root-Dienst `festpos-ereignisse`.
- **Zweiter Faktor und Bestätigung für kritische Aktionen.** Wer die Rolle Admin hat, richtet nach der Anmeldung einen
  zweiten Faktor ein: einen Code aus einer Authenticator-App (QR-Code, dazu 10 Wiederherstellungs-Codes zum
  Ausdrucken). Dreimal geht «Später», danach ist die Verwaltung bis zum Einrichten gesperrt. Für welche Rollen er
  Pflicht ist, steht unter Benutzer › Zugriff (Standard: Admin); alle anderen schalten ihn freiwillig ein
  (Benutzermenü › Zweiter Faktor). Ein Admin setzt ihn bei verlorenem Handy zurück. Kritische Aktionen – Update
  verteilen und zurück, Update aus dem Internet, Benutzer und Rollen, Netz, Drucker-Netz, Sicherung einspielen,
  Bestellungen löschen, neues Sicherungs-Passwort, Kartenpaket und Karte schreiben, Zugriffs-Einstellungen – verlangen
  Passwort und Code nochmals; die Bestätigung gilt 5 Minuten (auf allen Pi) und steht im Protokoll. Mit zweitem
  Faktor geht Basic Auth nur noch zum Lesen. Der Notfall-Login bleibt ohne zweiten Faktor (nur im FestPOS-Netz).
- **Verwaltung: Abmelden nach Inaktivität** statt fester 12 Stunden: Ohne Bedienung endet die Anmeldung nach 30 Minuten
  (einstellbar 10–120 Minuten unter Benutzer › Zugriff auf die Verwaltung), eine Minute vorher fragt die Verwaltung
  «Noch da?». Klicken, Tippen und Eingaben verlängern, das automatische Aktualisieren nicht. Die Frist prüft der Server
  (im Sitzungs-Token, auf allen Pi gleich); die Höchstdauer bleibt 12 Stunden. Rolle Anzeige (Statusanzeige) bleibt
  angemeldet.
- **Ersteinrichtung: `ca.key` nur verschlüsselt.** Der Schlüssel der Zertifizierungsstelle geht nur noch mit einem
  Passwort verschlüsselt hinaus (mindestens 12 Zeichen, PKCS#8 mit AES-256 und 600 000 PBKDF2-Runden – liest
  OpenSSL) – als Download oder direkt auf einen USB-Stick am Pi (`festpos-ca/`). `make-certs.sh` erzeugt neue CAs
  ebenfalls verschlüsselt und fragt beim Erneuern des Server-Zertifikats nach dem Passwort
  (`FESTPOS_CA_PASS_FILE` für Skripte); ein älterer, unverschlüsselter `ca.key` geht weiter, mit Hinweis.
- **Notfall-Passwort nur als Prüfwert.** In `festpos.conf` steht statt des Passworts ein Prüfwert (scrypt,
  `ADMIN_PASSWORD_HASH`); ein Passwort im Klartext wandelt der Pi beim nächsten Start um, ohne Handgriff. Neu
  mindestens 16 Zeichen (die Ersteinrichtung schlägt eines vor); kürzer sperrt den Notfall-Login, sobald es einen
  eigenen Admin-Benutzer gibt, sonst nur Warnung. Der Notfall-Login geht nur noch aus dem FestPOS-Netz oder am Pi
  selbst und steht jedes Mal im Protokoll. Sitzungen hängen nicht mehr am Klartext-Passwort. Sicherungen tragen den
  Prüfwert.
- **Herkunftsnachweis und Paketliste für Releases.** Der Release-Workflow erstellt für ZIP, Image, App und
  Paketlisten einen Herkunftsnachweis von GitHub (`gh attestation verify festpos.zip -R T-0-N/FestPOS`) und legt
  eine SBOM (CycloneDX) für `festpos.zip` und das Image bei (Python-Pakete, rqlite, beim Image alle Debian-Pakete).
  Dependabot meldet neue Versionen der Python-Pakete, der Android-App und der GitHub Actions.
- **Update-Schlüssel zurückziehen und ersetzen.** Fingerabdrücke in `festpos-update-zurueckgezogen.txt` (Boot-Partition)
  gelten nie mehr, auch nicht für ein Paket vom USB-Stick. Ein vertrauenswürdig signiertes Paket kann in
  `deploy/update-keys/` neue Schlüssel und Rückzüge mitbringen; der Pi übernimmt sie nach dem Einspielen. Eine
  `.sig`-Datei darf mehrere Signaturen enthalten (alter und neuer Schlüssel). Ablauf in `SECURITY.md`.
- **Alle gespeicherten Geheimnisse verschlüsselt.** Sicherungs-Passwort, Fully-, WLAN- und Router-Passwort,
  Kiosk-PIN, Fully-Lizenz, Token für ein privates Repo, Payrexx-Schlüssel und Tunnel-Token liegen mit AES-GCM
  verschlüsselt in der Datenbank, mit einem eigenen Schlüssel pro Zweck (aus `CLUSTER_SECRET`). Bestehende Werte
  schreibt FestPOS um, sobald alle Pi die neue Version haben; Sicherung einspielen und Kartenschreiben gehen weiter.
- **Pi startet nur von der SD-Karte, Bluetooth aus, keine Anmeldung am Bildschirm.** `festpos-haertung` stellt bei
  Ersteinrichtung, Start und Update den Bootloader auf «nur SD-Karte» (`BOOT_ORDER=0xf1`, keine
  Netzwerk-Installation; wirkt nach dem nächsten Neustart), schaltet Bluetooth ab und nimmt an der Konsole keine
  Anmeldung mehr an (Hinweistext und Einrichtungscode bleiben sichtbar). Verwaltung › Geräte & Netz › Pi zeigt den
  Zustand pro Pi und warnt, solange ein Pi noch von USB startet (auch in der Übersicht). Rückgängig mit einer
  Wiederherstellungs-Karte (Anleitung).

### Behoben
- **Internet «dauerhaft verbunden» bricht nach Neustart oder Update nicht mehr ab.** Nach einem Neustart der App (auch
  beim Update) übernimmt sie die bestehende Verbindung, statt sie abzubauen; während eines Updates bleibt der Funk an,
  und Internet-Aufträge laufen in einem eigenen Dienst (`festpos-update-netz`), statt hinter dem Update zu warten.
  «Online» heisst jetzt DNS **und** HTTPS; neu verbunden wird erst nach 3 Fehlversuchen, danach mit wachsender Pause
  (1 bis 15 Minuten). Der Grund steht in der Verwaltung (mit letztem und nächstem Versuch) und auf der Statusanzeige.
  Weg «Router im Netz»: Route und DNS über NetworkManager (bleiben, wenn eth0 kurz weg war; Rückfall auf den alten
  Weg). WLAN: Land CH im Image und beim Verbinden, Suche bis 30 s, mit 3 Pi nur der Pi mit der VIP am Hotspot.

### Geändert
- **Einfache Statusanzeige** (`/status-einfach`, iPad mini mit iOS 9): aktualisiert sich ohne weisses Aufblitzen –
  ein kleines Skript (ES5, für Safari 9) holt alle 10 s nur den Inhalt und tauscht ihn aus, statt die ganze Seite neu
  zu laden. Ohne Verbindung zum Server erscheint ein Hinweis und die Anzeige wird abgedunkelt; nach einem Update lädt
  sie sich selbst neu. Ohne JavaScript wie bisher mit Neuladen.

## [1.0.0]

Erste öffentliche Version.

### Enthalten
- **Bestell-App** für Android-Handhelds (Fully Kiosk): Tisch per Ziffernblock, Tischplan oder QR-Code, Produkte nach
  Kategorie mit Optionen, Ausgang ohne Verbindung (wird nachgesendet), Verlauf, Rechenhilfe, Teilen und Einzeln
  kassieren, Trinkgeld, offene Tische, Ausverkauft und Restmengen.
- **Bondruck** auf Epson TM-T20III (ESC/POS, Port 9100): Küchen- und Getränkebons pro Station, Quittung mit
  fortlaufender Nummer und Prüfsumme, Nachdruck als KOPIE, MWST optional, Bon-Baukasten mit Vorschau.
- **Kassenbuch**: Zahlart, Storno als Gegenbuchung, Tagesabschluss mit Kassensturz, verketteten Prüfsummen, PDF und
  CSV, Protokoll. Keine zertifizierte Registrierkasse.
- **Verwaltung** im Browser: Produkte, Tische, Drucker, Handhelds (Gerätezertifikate, Fully-Fernverwaltung), Benutzer
  und Rollen, Sicherung auf USB-Stick, Statusanzeige.
- **Betrieb**: ein Raspberry Pi 5 oder drei Pi mit rqlite-Cluster (ausfallsicher), fertiges Image für den Raspberry Pi
  Imager, Ersteinrichtung im Browser, weitere Karten schreibt der Pi selbst.
- **Updates**: signiertes Update-Paket über die Verwaltung, einen USB-Stick oder aus dem Internet (GitHub-Release);
  Pi für Pi mit automatischem Rückfall. Bringt Python-Pakete und rqlite mit (ohne Internet am Pi).
- **Optional**: Karte/TWINT mit NFC am Handheld und QR-Bestellung am Tisch über Payrexx (siehe unten), Tunnel über
  Cloudflare (`cloudflared` installiert jeder Pi bei Bedarf selbst).
- Oberfläche in Deutsch, Englisch, Französisch, Italienisch und Rumantsch Vallader.

### Bekannte Einschränkungen
- **Payrexx ist ungetestet.** Alles mit Payrexx (Karte/TWINT mit NFC samt App «FestPOS Tap», QR-Bestellung am Tisch,
  Rückerstattungen) lief noch nie gegen ein echtes Payrexx-Konto. Vor dem Einsatz: `docs/testplan-payrexx.md`.

### Umstieg von einer Version vor 1.0
Pi mit einer früheren Version (Format `JJJJ.M.T-N`) brauchen zuerst die Version `2026.10.10-102` oder neuer; sie kennt das neue
Versionsformat und nimmt danach `1.0.0` an.
