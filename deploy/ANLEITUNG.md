# FestPOS – Aufbau und Betrieb

Start mit **einem Raspberry Pi**. Später lässt sich das System ohne Neuinstallation auf
**drei Pis** erweitern (ausfallsicher). Alle Daten bleiben dabei erhalten.

## Schnellstart

**Was es braucht:** Raspberry Pi 5 (4 GB genügen) mit Netzteil und SD-Karte (32 GB, «High Endurance»), Bondrucker
Epson TM-T20III mit Netzwerkanschluss (einer pro Station), einen Switch und einen WLAN-Access-Point für das
Festnetz `192.168.55.x` (siehe «Netz»), Android-Handhelds (z.B. Samsung XCover 7) mit **Fully Kiosk Browser**
(von fully-kiosk.com; für die Fernverwaltung die PLUS-Lizenz pro Gerät) und einen PC oder Laptop für die Verwaltung.
Internet braucht der Pi nicht.

1. **Karte schreiben:** Raspberry Pi Imager › Betriebssystem › «Eigenes Image verwenden» › `festpos-<VERSION>.img.xz`
   aus den Releases (github.com/T-0-N/FestPOS › Releases). Oder im Imager unter *App-Optionen › Benutzerdefiniertes
   Repository* die Liste `https://github.com/T-0-N/FestPOS/releases/latest/download/festpos-os-list.json` eintragen,
   dann steht FestPOS direkt in der Auswahl.
2. **Ersteinrichtung im Browser:** Karte in den Pi, Netzwerkkabel, Strom. `https://festpos.local/` (am Heimrouter)
   oder `https://192.168.55.10/` öffnen, den Einrichtungscode vom Bildschirm des Pi eingeben, «Weiter, Weiter,
   Fertig» (Kapitel 2). `ca.key` auf einen USB-Stick legen, Pi neu starten.
3. **Anmelden:** am Festplatz `https://192.168.55.10:8443/admin.html`, Benutzer `admin` mit dem Notfall-Passwort aus
   Schritt 2. Drucker, Produkte und Tische einrichten (Kapitel 3).
4. **Handhelds:** pro Gerät einmal über die Verwaltung einrichten (Kapitel 4).
5. **Später auf 3 Pi:** Verwaltung › Geräte & Netz › Pi › «Auf 3 Pi erweitern» – Pi A schreibt die Karten für
   Pi B und C selbst (USB-Kartenleser), Kapitel «Später: Erweitern auf 3 Pis».

**Updates** kommen signiert aus den Releases: Verwaltung › Wartung › Update › «Aus dem Internet» (Kapitel «Neue
Version einspielen»). **Karte/TWINT über Payrexx** und die **QR-Bestellung am Tisch** sind optional und in Version 1.0
noch nicht mit einem echten Payrexx-Konto getestet.

## Netz

| Gerät | IP | Switch-Port (TL-SG108PE) |
|---|---|---|
| **Adresse für Handhelds und PC** | **192.168.55.10** | – |
| Mobilfunk-Router (optional, für Kartenzahlung) | 192.168.55.1 | P1 |
| AP EAP225-Outdoor | 192.168.55.40 | P2 (PoE) |
| Pi A (Einzelbetrieb: trägt zusätzlich .10) | 192.168.55.11 | P3 |
| Pi B / Pi C (erst bei 3 Pis) | 192.168.55.12 / .13 | P4 / P5 |
| Drucker Getränkeausgabe | 192.168.55.20 (im Drucker-Netz 192.168.56.20) | P6 |
| Drucker Küche | 192.168.55.21 (im Drucker-Netz 192.168.56.21) | P7 |
| Reserve-Drucker | 192.168.55.22 (im Drucker-Netz 192.168.56.22) | P8 |
| Switch | 192.168.55.30 | – |
| XCover 1–4 | 192.168.55.100–103 (feste Zuteilung per DHCP) | WLAN |
| Neue Geräte (DHCP-Bereich) | 192.168.55.150–189 | WLAN |
| Admin-PC | z.B. 192.168.55.200 | WLAN (zum Einstellen des Switches kurz an P1) |

Pi, Drucker, Switch und Access Point sind fest eingestellt (die Pi-Adressen stehen in `festpos.conf`). Die Handhelds
bekommen ihre Adresse per **DHCP von den Pi**: jedes Handheld immer dieselbe (feste Zuteilung, die „Neues Handheld
ausrollen“ einträgt), neue Geräte eine aus dem DHCP-Bereich (`DHCP_POOL`). Reserviert ist eine feste IP erst, wenn
der Pi die WLAN-Adresse (MAC) des Handhelds kennt: beim Werksreset und bei „Fully auf vorhandenem Handheld“ sofort, sonst
(feste IP nachträglich in der Geräteansicht eingetragen) übernimmt er sie, sobald sich das Handheld mit seinem
Gerätezertifikat meldet. Die Geräteansicht zeigt, ob die IP schon reserviert ist und welche Adresse das Handheld gerade
hat; die feste bekommt es beim nächsten Erneuern (wenige Minuten) oder sofort mit WLAN aus und ein. Ein Handheld mit von
Hand eingestellter fester IP geht weiterhin. Es darf **keinen zweiten DHCP-Server** im Netz geben – der Mobilfunk-Router ist optional,
sein eigener DHCP-Server muss aus sein (siehe „Mobilfunk-Router: Empfang auf der Statusanzeige“).

**Neue WLAN-Adresse (MAC):** Android kann die MAC eines Handhelds wechseln („Zufällige MAC“, z.B. nach WLAN entfernen und
neu verbinden). Am besten am Handheld unter Einstellungen › WLAN › FestPOS-WLAN › Datenschutz „Geräte-MAC“ wählen – die
Geräteansicht weist darauf hin, wenn ein Handheld eine zufällige MAC hat. Wechselt sie trotzdem, erkennt der Pi das Handheld
an seinem Gerätezertifikat: War die alte MAC 10 Minuten still (Geräte & Netz › Einstellungen › „Feste IP bei neuer MAC“:
aus, 5, 10, 30 oder 60 Minuten), schreibt er die feste IP auf die neue MAC um, gibt die Lease der alten frei
(`festpos-dhcp-reload`) und das Handheld bekommt seine feste IP innert 1–2 Minuten wieder. Bis dahin zeigt die Geräteansicht
„Neue MAC“ mit der Uhrzeit des Wechsels; „Neue MAC übernehmen“ macht es sofort. Melden sich alte und neue MAC
**gleichzeitig** (Zertifikat kopiert oder zwei Handhelds mit demselben Zertifikat), wird nicht umgeschrieben: rote Karte in
der Übersicht, rotes Handheld auf der Statusanzeige („Zertifikat doppelt“) und in der Geräteansicht beide MAC mit
„Neue MAC übernehmen“, „Alte MAC behalten“ oder „Sperren“ (dann das richtige Handheld neu einrichten). Jeder Wechsel,
Alarm und jede Entscheidung steht im Protokoll (Betrieb › Protokoll).

### Drucker-Netz: Drucker im eigenen VLAN (empfohlen)

Port 9100 der Drucker kennt kein Passwort: Im gemeinsamen Netz kann jeder, der im WLAN ist, Bons drucken oder im
Epson-Webinterface die IP verstellen. Mit dem Drucker-Netz hängen die Drucker in einem eigenen VLAN, das **nur die Pi**
erreichen. Zwischen den Netzen leitet niemand weiter, also ist der Drucker aus dem WLAN auch mit einer selbst
eingetragenen IP nicht erreichbar. Es braucht einen Switch mit VLAN (802.1Q), z.B. den TL-SG108PE.

**Einstellungen am Switch** (Weboberfläche `http://192.168.55.30`, nur vom Admin-PC am Kabel an P1, nicht übers WLAN –
der Switch kann kein HTTPS): VLAN › 802.1Q VLAN **einschalten**. So sieht es am Ende aus:

| VLAN | Name | Mitglieder | mit Tag | ohne Tag |
|---|---|---|---|---|
| 1 | Default | P1–P5 | – | P1–P5 |
| 56 | Printer | P3–P8 | P3–P5 (Pi) | P6–P8 (Drucker) |

und unter 802.1Q PVID: **P6–P8 = 56**, alle anderen 1. Die Pi-Ports sind also in beiden Netzen (normales Netz ohne
Tag, Drucker-Netz mit Tag), die Drucker-Ports nur im Drucker-Netz. Der Port des Admin-PC muss im VLAN 1 bleiben, sonst
erreicht man die Weboberfläche des Switches nicht mehr. **Nicht alles auf einmal einstellen:** Ein Drucker-Port kommt
erst ins VLAN 56, wenn dieser Drucker seine neue IP hat (Schritt 3) – sonst erreicht man ihn unter der alten IP nicht mehr.

**Umstellen** (vor dem Anlass, jeder Drucker ist dabei kurz nicht erreichbar):
1. Am Switch VLAN 56 anlegen, vorerst **nur mit den Pi-Ports P3–P5 mit Tag**. Die Drucker-Ports bleiben im VLAN 1
   (PVID 1), die Drucker laufen unverändert weiter.
2. Verwaltung › Geräte & Netz › Reiter **Drucker** › Drucker-Netz: «Drucker in eigenem VLAN» ein, VLAN-Nummer `56`, Netz `192.168.56`,
   **Speichern**. Die Tabelle zeigt pro Pi seine Adresse im Drucker-Netz (192.168.56.11 / .12 / .13) und nach einigen
   Sekunden «bereit (eth0.56)». Die Drucker laufen bis hierher unverändert; die Karte warnt, dass sie noch im alten Netz sind.
3. Pro Drucker: im **Epson-Webinterface** (noch unter der alten IP) die neue IP setzen, z.B. `192.168.56.20`, Maske
   `255.255.255.0`, kein Gateway. Erst dann seinen Switch-Port ins VLAN 56 nehmen (aus VLAN 1 entfernen, im VLAN 56 ohne
   Tag, PVID 56), in der Verwaltung beim Drucker die neue IP eintragen, **Speichern** und **Testdruck**.
4. Sind alle Drucker umgestellt, verschwinden die Warnungen. Prüfen: Testdruck auf jedem Drucker; von einem Handheld im
   WLAN ist `http://192.168.56.20` nicht erreichbar.

Die Adressen .11, .12 und .13 im Drucker-Netz gehören den Pi – nicht für Drucker verwenden. Ins Epson-Webinterface
kommt man danach nur noch über einen Port im VLAN 56 (z.B. Laptop an P8 mit fester IP `192.168.56.200`).

**Zurück:** zuerst die Drucker wieder ins normale Netz nehmen (alte IP im Epson-Webinterface, Port im Switch zurück auf
VLAN 1, IP in der Verwaltung), dann das Drucker-Netz ausschalten. Ausgeschaltet entfernt jeder Pi seine
VLAN-Schnittstelle selbst.

Jeder Pi richtet das selbst ein (`festpos-druckernetz`, bei jedem Start und nach jeder Änderung) – auch eine
Ersatzkarte, sobald sie die Datenbank hat. Zeigt ein Pi «nicht eingerichtet»: Netzwerkkabel und Tag am Switch-Port
des Pi prüfen; Protokoll am Pi mit `journalctl -u festpos-druckernetz`.

---

## 1. Pi einrichten

**Normalfall: fertiges FestPOS-Image (T12, ohne Internet am Pi).**
1. Von der GitHub-Seite des Projekts unter **Releases** die Datei `festpos-<VERSION>.img.xz` herunterladen (dazu
   `.sha256` zum Vergleichen).
2. **Raspberry Pi Imager:** Gerät wählen (Raspberry Pi 5), Betriebssystem › ganz unten **«Eigenes Image verwenden»** ›
   die `.img.xz` wählen, SD-Karte (32 GB empfohlen, mindestens 16 GB, «High Endurance»). Mit der Liste
   `https://github.com/T-0-N/FestPOS/releases/latest/download/festpos-os-list.json` erscheint die neuste Version im
   Imager direkt (Imager › App-Optionen › Benutzerdefiniertes Repository).
3. **«Einstellungen anpassen»** (optional): Benutzer, **Passwort** (braucht es für `sudo`) und unter «Dienste» SSH mit
   **«Nur Authentifizierung über öffentlichen Schlüssel»** und deinem öffentlichen Schlüssel – nur nötig, wer per SSH
   auf den Pi will. Ohne diese Angaben ist der Benutzer gesperrt und SSH aus; alles andere geht im Browser. SSH geht
   **nur mit Schlüssel**, nie mit Passwort (siehe «SSH-Zugang» unten).
4. Karte in den Pi, Netzwerkkabel, Strom – weiter mit **Schritt 2 (Ersteinrichtung im Browser)**. Das Image enthält
   keine Konfiguration, keine Zertifikate und keine Datenbank; SSH-Schlüssel und `machine-id` entstehen beim ersten Start.

Das Image bringt den Projekt-Schlüssel für die offiziellen Updates mit (Kapitel «Neue Version einspielen»). Updates
kommen per Update-Paket (Verwaltung › Wartung › Update); das Image ist nur für neue Karten.

**Variante: Raspberry Pi OS und `setup.sh` (braucht einmal Internet)** – für Sonderfälle oder einen Pi 4:

1. **Raspberry Pi Imager:** „Raspberry Pi OS Lite (64-bit)“ auf die SD-Karte (32 GB empfohlen, mindestens
   16 GB; am besten eine «High Endurance»-Karte, für alle Pi dieselbe).
   In den Imager-Einstellungen Benutzer + Passwort und SSH mit öffentlichem Schlüssel aktivieren (nach `setup.sh`
   geht SSH nur noch mit Schlüssel, siehe «SSH-Zugang» unten).
2. Pi zu Hause am Router mit Netzwerkkabel starten, IP im Router nachsehen.
3. Projekt kopieren und Setup starten:
   ```bash
   scp -r festpos/ pi@<ip>:~/
   ssh pi@<ip>
   sudo bash ~/festpos/deploy/setup.sh
   ```
   Immer mit `bash` davor starten (auch `update.sh` und die anderen Skripte im Projektordner): Wurde das
   ZIP unter Windows entpackt oder mit WinSCP kopiert, fehlen die Ausführungsrechte, und `sudo ./…` meldet
   „command not found“. Ein `chmod` ist nicht nötig – das Setup installiert alle Programme selbst mit den
   richtigen Rechten.
   Ohne `deploy/festpos.conf` im Projekt (Normalfall) legt das Setup keine Konfiguration an: Der Pi startet danach
   in die **Ersteinrichtung im Browser** (Schritt 2).
4. `sudo reboot`. Ab jetzt braucht der Pi kein Internet mehr.

## 2. Ersteinrichtung im Browser

Solange es auf der Boot-Partition keine `festpos.conf` gibt, startet der Pi in die **Ersteinrichtung**: Bestell-App,
Verwaltung und Statusanzeige laufen noch nicht, der Pi zeigt nur die Einrichtungsseite – über https mit einem
**vorläufigen, selbst signierten Zertifikat** (http leitet dorthin um). Der Browser warnt deshalb einmal; den
Fingerabdruck auf dem Bildschirm am Pi bzw. in `festpos-einrichtung.txt` vergleichen und fortfahren. Nach der
Einrichtung erzeugt der Pi die richtigen Zertifikate, das vorläufige wird gelöscht.

**Pi finden:**
- Am **Heimrouter** holt er sich eine Adresse per DHCP: im Browser **https://festpos.local/** öffnen (Windows 10/11,
  macOS, Android mit Chrome) oder die Adresse, die der **Bildschirm am Pi** zeigt (HDMI, Anmeldeaufforderung).
- Am **Festplatz** (ohne Router) ist er immer unter **https://192.168.55.10/** erreichbar; antwortet kein DHCP-Server,
  verteilt er selbst Adressen (192.168.55.150–189), der Laptop braucht also keine feste IP.
- Der **Einrichtungscode** (z.B. `K7MP-3QXA`) steht auf dem Bildschirm am Pi und in der Datei
  `festpos-einrichtung.txt` auf der Boot-Partition (Karte am PC einstecken). Ohne Code geht nichts; nach 10 falschen
  Versuchen 5 Minuten Pause.

**Ablauf** (am Laptop oder Handheld, pro Schritt ein grüner Knopf; «Weiter, Weiter, Fertig» ergibt einen lauffähigen
einzelnen Pi):
1. Sprache (oben rechts) und **Einrichtungscode**.
2. **Was soll dieser Pi werden?**
   - *Neu einrichten* – erster Pi (Pi A). Zertifikate und Schlüssel erzeugt der Pi selbst (wie `make-certs.sh`: nur
     für 192.168.55.x, `festpos.local`, `localhost`).
   - *Sicherung einspielen* – Ersatz für einen Pi: `.festpos`-Datei vom USB-Stick wählen, Passwort vom Ausdruck
     (bzw. das beim Herunterladen gewählte). Schlüssel, Notfall-Passwort und `CLUSTER_SECRET` kommen aus der Sicherung,
     die Daten spielt der Pi nach dem Neustart **von selbst** ein (Protokoll «Sicherung eingespielt», Prüfliste unter
     Wartung › Sicherung). Ein Pi oder «Teil von 3 Pi» mit Buchstabe (Ersatzkarte im Cluster).
   - *Zu Pi A hinzufügen* – Pi B oder C: das **Kartenpaket** von Pi A wählen (Geräte & Netz › Pi › Auf 3 Pi erweitern ›
     Kartenpaket herunterladen), dann den Buchstaben. Einfacher geht es mit «SD-Karte schreiben» an Pi A (T10) – diese
     Karte kommt gar nicht in die Ersteinrichtung.
3. **Notfall-Passwort** (Benutzer `admin`, mindestens 10 Zeichen) – nur bei «Neu einrichten».
4. **Uhrzeit** dieses Geräts übernehmen (Zeitzone Europe/Zurich).
5. **Netz:** Standard passt zur Tabelle oben (Handhelds 192.168.55.10, Pi A .11, B .12, C .13, DHCP für die Handhelds); nur
   bei Bedarf unter «Ändern».
6. **Batterie der Uhr:** offizielle aufladbare RTC-Batterie ja/nein (`RTC_AKKU`).
7. **Zusammenfassung › Einrichten.** Der Pi schreibt `festpos.conf`, `node.conf` und `festpos-tls/` auf die
   Boot-Partition – die Datei bleibt der Ort der Einstellungen und ist am PC lesbar.
8. Bei «Neu einrichten»: **ca.key herunterladen** und auf einen USB-Stick legen (er kommt nicht auf die Karte und ist
   nach dem Neustart weg; gebraucht wird er nur, um später das Server-Zertifikat zu erneuern), dazu `ca.crt`. Erst dann
   **Pi neu starten**.

Danach ist der Modus endgültig aus. Der Pi setzt sich auf die feste Festplatz-IP (am Heimrouter ist er dann nicht mehr
erreichbar) – weiter mit Schritt 3 am Festplatz. Notfall-Login, Update-ZIP und `festpos-zuruecksetzen` funktionieren
auch nach der Einrichtung. Wer die Ersteinrichtung nochmals will: `festpos.conf` auf der Boot-Partition löschen und neu starten.

### Variante für Fortgeschrittene: festpos.conf und Zertifikat am PC
Statt im Browser lässt sich alles auch am PC vorbereiten (z.B. für viele gleiche Karten). Liegt beim Setup
`deploy/festpos.conf` im Projekt, kopiert `setup.sh` sie auf die Boot-Partition, und der Pi startet ohne
Ersteinrichtung:


```bash
cd festpos/deploy
cp festpos.conf.example festpos.conf     # ADMIN_PASSWORD ändern! (mind. 10 Zeichen, CLUSTER_SECRET optional)
bash make-certs.sh festpos.conf          # erzeugt deploy/festpos-tls/ und ~/festpos-ca/
```
(`make-certs.sh` braucht `openssl`: unter Linux/macOS vorhanden, unter Windows z.B. in Git Bash.)

**Passwörter in `festpos.conf`** dürfen jedes Zeichen enthalten (`$`, `&`, Leerzeichen, Umlaute). Die Datei
wird nur gelesen, nicht ausgeführt. Einzige Regel: Ab „Leerzeichen + #“ beginnt ein Kommentar. Enthält ein
Passwort ` #`, den Wert in Anführungszeichen setzen: `ADMIN_PASSWORD="Bier #1"`.
Steht bei `ADMIN_PASSWORD` noch der Platzhalter `bitte-aendern` (oder `admin`), ist der Notfall-Login gesperrt;
ist es kürzer als 10 Zeichen, zeigen Übersicht und Statusanzeige eine Warnung.

- `festpos-tls/` → kommt auf die Boot-Partition jeder Karte (macht `setup.sh`). Darin `ca.crt`, die zusätzlich
  auf **jedes Handheld und jeden PC** kommt (Schritt 4).
- `~/festpos-ca/ca.key` → der Schlüssel der Zertifizierungsstelle. Liegt absichtlich **ausserhalb** des
  Projektordners, damit er nie auf eine Karte kopiert wird. **Sicher aufbewahren** (USB-Stick); gebraucht
  wird er nur, um später ein neues Server-Zertifikat auszustellen.
- Die Zertifizierungsstelle darf nur Zertifikate für das Festnetz (192.168.55.x), `festpos.local` und
  `localhost` ausstellen. Selbst mit einem gestohlenen `ca.key` lassen sich damit keine fremden Webseiten
  (Bank, Google …) auf den Handhelds fälschen.

**Boot-Partition:** Dort liegen Passwort und Schlüssel. Am Pi ist sie deshalb nur für root lesbar –
Ändern mit `sudo nano /boot/firmware/festpos.conf`. Am PC geht es mit einem Kartenleser.

**SSH-Zugang (Fernwartung, T23):** SSH geht **nur mit Schlüssel** – kein Passwort, kein root, höchstens 3 Versuche,
dazu bremst die Firewall auf 6 neue Verbindungen pro Minute. `sudo` verlangt das Passwort des Benutzers (5 Minuten
gültig, protokolliert in `/var/log/sudo.log`). Das stellt `festpos-haertung` bei jedem Start ein; von Hand nichts
ändern (`/etc/ssh/sshd_config.d/10-festpos.conf`, `/etc/sudoers.d/festpos-haertung`).
1. Schlüssel am PC einmal erzeugen (PowerShell, `ssh-keygen` ist in Windows 10/11 dabei):
   ```powershell
   ssh-keygen -t ed25519 -C "FestPOS Fernwartung" -f $HOME\.ssh\festpos-ssh
   ```
   Passphrase wählen. `festpos-ssh` (privat) bleibt auf dem PC; `festpos-ssh.pub` ist der öffentliche Teil.
2. **Auf den Pi bringen:** SD-Karte am PC einstecken und `festpos-ssh.pub` auf die **Boot-Partition** kopieren (neben
   `festpos.conf`). Beim nächsten Start übernimmt der Pi den Schlüssel für den ersten Benutzer und benennt die Datei in
   `festpos-ssh.pub.uebernommen` um. Mehrere Schlüssel: alle in dieselbe Datei, einer pro Zeile. Bei einem neuen
   Image geht es auch über den Imager (Schritt 1).
3. Verbinden: `ssh -i $HOME\.ssh\festpos-ssh pi@192.168.55.11` (Benutzername wie im Imager gesetzt).

**Ausgesperrt?** Schlüssel verloren: neuen erzeugen und wie in Schritt 2 auf die Boot-Partition kopieren. Ohne Passwort
(Image ohne Imager-Angaben) bleibt `sudo` ohne Passwort, damit die Fernwartung überhaupt geht – das meldet
`sudo festpos-haertung --stand` (`"password_set": false`); Passwort setzen mit `sudo passwd <benutzer>`, beim
nächsten Start gilt dann auch für `sudo` das Passwort.

**Weitere Einstellungen in `festpos.conf`:**
- `SSH_FROM` – nur von diesen Adressen ist SSH (Fernzugang) erlaubt, Standard `192.168.55.200-192.168.55.254`
  (Admin-PC). Handhelds und Gäste im WLAN kommen so nicht an den Fernzugang. Leer = ganzes Netz. Wer sich
  ausgesperrt hat, steckt die Karte am PC ein und ändert die Zeile.
- `ADMIN_FROM` – die Verwaltung auf Port 8443 nur von diesen Adressen (gleiche Schreibweise), z.B.
  `192.168.55.200-192.168.55.254`. Leer (Standard) = ganzes Netz. Offen bleiben der Zertifikat-Download für neue
  Handhelds (Link im QR-Code) und der Verkehr zwischen den Pi; Handhelds mit Gerätezertifikat kommen über
  `https://192.168.55.10` weiter in die Verwaltung. Wirkt nur mit `PHONE_CERTS=1`.
- `DHCP=1` (Standard) – die Pi vergeben die Adressen der Handhelds (`dnsmasq`, Dienst `festpos-dhcp`). Feste
  Zuteilungen kommen aus der Verwaltung, neue Geräte bekommen eine aus `DHCP_POOL=150-189` (letzte Stelle; bei 3 Pi
  auf die Pi aufgeteilt, damit nie zwei dieselbe vergeben). `DHCP_GATEWAY`: leer (empfohlen) = **automatisch** nach
  Verwaltung › Geräte & Netz › Netz & Internet: ist «Dauerhaft verbunden» über einen Router im FestPOS-Netz an, bekommen die Handhelds den
  Router als Gateway, sonst die Pi (VIP), die das Internet weitergeben (siehe „Neues Handheld ausrollen“). Die
  Handhelds übernehmen ein neues Gateway beim Erneuern ihrer Adresse, spätestens nach 30 Minuten (sofort: WLAN am
  Handheld aus und wieder ein). Eine feste Adresse, z.B. `192.168.55.1`, gilt immer. `DHCP_DNS`: leer =
  `1.1.1.1`. `DHCP=0` schaltet es ab. Wirkt nach einem Neustart des Pi. `dnsmasq` bringt `setup.sh` mit.
- `RAFT_TLS=1` – der Datenbank-Verkehr zwischen den Pi ist verschlüsselt und gegenseitig geprüft (wirkt erst
  mit 3 Pi). Ändern nur, wenn danach alle Pi zusammen neu gestartet werden.

## Drucker einrichten (Epson TM-T20III, einmal pro Drucker)

Ab Werk meldet der Drucker bei leerem Papier oder offenem Deckel **nichts**: Er beantwortet dann die Statusabfrage von
FestPOS nicht. Darum jeden Drucker einmal so einstellen – auch einen Ersatzdrucker oder einen, der auf
Werkseinstellungen zurückgesetzt wurde.

**Sicherheitsnetz (T15):** FestPOS merkt sich pro Drucker, ob er die Statusabfrage schon einmal beantwortet hat (in
der Datenbank, gilt also nach einem Neustart und auf jedem Pi). Schweigt so ein Drucker später, schickt FestPOS
**keinen** Bon mehr, sondern behandelt ihn wie «nicht bereit»: Statusanzeige und Verwaltung zeigen rot «Antwortet
nicht – Papier und Deckel prüfen», die Bons warten (bzw. gelten die Regel «Wenn nicht erreichbar») und kommen in
richtiger Reihenfolge, sobald er wieder antwortet. Ein Drucker, der **nie** geantwortet hat (anderes Modell, oder ab
Werk eingestellt und noch nie mit Papier abgefragt), wird ohne Statusabfrage bedient; Verwaltung › Geräte & Netz › Drucker ›
**Status** zeigt dann «Erreichbar, aber keine Statusantwort» mit dem Hinweis auf diese Einstellung. Wird die Adresse
eines Druckers geändert, lernt FestPOS das neu.

**Am Windows-PC: «TM-T20III Utility»** (Epson-Website › Support › TM-T20III › Software), Drucker über das Netz
(`192.168.55.20` bzw. `.21`) oder per USB wählen. **Basiseinstellungen › Drucksteuerung:**

| Funktion | Einstellung |
|---|---|
| **Quittungsbetrieb (Zustand BUSY)** | **Empfangspuffer voll** (ab Werk «Offline oder Empfangspuffer voll» – dann keine Antwort bei Papierende) |
| Empfangspufferkapazität | **4KB** (nicht 128 KB: sonst nimmt der Drucker mehrere Bons auf einmal an, und reisst das Papier mitten im Bon ab, merkt FestPOS es nicht) |
| Sensor für das nahende Rollenpapierende | **Aktiviert** – dann zeigt FestPOS gelb «Papier fast leer» (Statusanzeige, Verwaltung und Handhelds mit Rolle Festleitung) |
| Auswahl der Papiersensoren (Papierende-Signale) | Rollenpapierende-Sensor aktiviert (so lassen) |
| Fehlersignalausgabe | Aktiviert (so lassen) |
| Papierbreite | 80 mm – 48 Spalten (passt zu Verwaltung › Geräte & Netz › Drucker › Zeichen pro Zeile 48) |
| Automatischer Zeilenvorschub | Immer deaktiviert (so lassen) |

Dazu: **Papier reduzieren** ganz aus (sonst ändern Ränder und Zeilenabstand der Bons); unter **Detaillierte
Einstellungen** einen Stromsparmodus oder automatisches Ausschalten deaktivieren, falls vorhanden. Druckgeschwindigkeit
und Druckdichte nach Belieben (blasse Bons: Dichte höher). **Einstellen** drücken – der Drucker startet neu.
Danach unter **Backup/Wiederherstellen** die Einstellungen in eine Datei sichern; beim zweiten Drucker einspielen und
die IP-Adresse kontrollieren.

**Netzwerk** (im Utility oder im Webinterface `http://192.168.55.20`):
- Feste IP `.20` (Getränke) bzw. `.21` (Küche), Maske 255.255.255.0, **DHCP aus**.
- **Passwort für das Webinterface** setzen (Optional › Password) – sonst kann jedes Gerät im WLAN den Drucker umstellen.
- Nicht gebrauchte Dienste aus: **Wireless, LPR, Bonjour, SNMP (mit IP Trap), ePOS-Print**. FestPOS braucht nur
  RAW auf Port 9100 und den Ping.
- Timeout RAW (Port 9100): 30 s lassen.
- Besser noch: Drucker und Pi in ein eigenes VLAN, damit Geräte im WLAN gar nicht an Port 9100 kommen
  (siehe «Netz für 3 Pi»).

**Prüfen** (auf einem Pi, während nichts druckt):
```
python3 -c '
import socket
s = socket.create_connection(("192.168.55.20", 9100), 3); s.settimeout(3)
for n in (2, 4):
    s.sendall(bytes([0x10, 0x04, n])); print(n, hex(s.recv(1)[0]))'
```
Mit Papier: `2 0x12` und `4 0x12`. Papier heraus, **Deckel zu**: `2 0x32` und `4 0x72` (oder `0x7e`). Kommt ohne
Papier `TimeoutError`, ist «Quittungsbetrieb (Zustand BUSY)» noch falsch. In der Verwaltung › Geräte & Netz › Drucker › **Status**
und auf der Statusanzeige steht dann innert etwa 20 Sekunden «Papier leer».

## 3. Am Festplatz anschliessen und einrichten

Pi an den Switch, Strom an. Nach ca. 1 Minute ist er unter **https://192.168.55.10** erreichbar.

Am PC (feste IP 192.168.55.200 einstellen, `ca.crt` im Browser/System als vertrauenswürdig
importieren oder die Warnung einmal bestätigen) **https://192.168.55.10:8443/admin.html** öffnen
(Port **8443**: die Verwaltung braucht kein Gerätezertifikat, nur das Passwort),
Benutzer `admin` mit dem Notfall-Passwort (aus der Ersteinrichtung bzw. aus `festpos.conf`). Die Anmeldung gilt 12 Stunden; der Browser merkt sich dabei nur ein
Sitzungs-Token, nicht das Passwort.

Das Menü links ist in drei Gruppen geordnet: **Betrieb** (Bestellungen, Druckaufträge, Tagesabschluss, Protokoll),
**Vorbereiten** (Produkte, Tische, Kasse & App) und **System** (Geräte & Netz, Benutzer, Wartung).
Die Leiste bleibt beim Scrollen stehen; die Gruppen lassen sich mit einem Klick auf ihren Namen auf- und zuklappen
(der Browser merkt sich die Wahl). Ist das Fenster niedrig, sind alle Gruppen ausser der aktiven zu. Am Handheld steht
oben nur die aktuelle Seite mit dem Menü-Knopf ☰. Manche Seiten haben oben Reiter (am Handheld eine Zeile zum
Wischen), seit T34 vor allem:
- **Kasse & App:** Veranstalter (Name, Währung, Sprache, Tisch/Platz, Logo) · Kasse · Bestell-App · Bons & Quittung
- **Geräte & Netz:** Pi · Handhelds & PC · Einstellungen (Fully, Einrichtungs-QR, WLAN, Gültigkeit, feste IP) · Drucker ·
  Netz & Internet
- **Produkte:** Produkte · Kategorien · Lieferzeiten · Import & Export

Die Adresse im Browser zeigt Seite und Reiter, z.B. `…/admin.html#devices/printers`. Eine unbekannte Adresse führt auf die
Übersicht.

1. **Drucker:** beide anlegen (IP .20 und .21, Port 9100). Ob Preise auf den Küchen- und Getränkebons stehen, gilt
   für alle Drucker gemeinsam: Kasse & App › Bons & Quittung › Küchen- und Getränkebon › Baustein **Positionen** ›
   „Preise drucken“.
   **Testdruck** drücken. Kommt nichts: Drucker-IP im Epson-Webinterface prüfen. Die Drucker müssen eingestellt sein
   wie unter «Drucker einrichten» oben – **Status** muss «Bereit, Papier ok» zeigen, nicht «keine Statusantwort».
   Drucker im eigenen VLAN (unten auf derselben Seite, „Drucker-Netz“): siehe „Drucker-Netz: Drucker im
   eigenen VLAN“ oben.
   Oben auf derselben Seite unter **Standard-Drucker** festlegen, wohin Produkte ohne eigenen Drucker gehen
   (ohne Auswahl: der zuerst angelegte Drucker).
2. **Produkte › Import & Export:** Preisliste als CSV hochladen (Vorlage dort herunterladen),
   Vorschau prüfen, **Übernehmen**. **Reihenfolge** (wie in der Bestell-App): am Griff **⋮⋮** ziehen (Maus oder
   Finger; Produkte innerhalb ihrer Kategorie, Kategorien im Reiter „Kategorien“) – wie bei den Bons löst sich die
   Zeile und schwebt, die anderen gleiten zur Seite und eine gestrichelte Lücke zeigt, wo sie landet; am oberen oder
   unteren Rand scrollt die Seite mit. Oder Griff antippen und ↑/↓ – wird sofort gespeichert. **A–Z sortieren** (über der Preisliste) ordnet Kategorien und Produkte einmal
   alphabetisch („Ä“ wie „A“, „Bier 3dl“ vor „Bier 5dl“); danach lässt sich wieder frei ziehen. Bilder optional als ZIP (unlesbare Dateien werden genannt, die übrigen
   trotzdem übernommen). Einzelne Bilder: Produkte › Preisliste, auf das
   Bild bzw. „+“ klicken; das kleine **×** daneben entfernt ein Bild wieder.
3. **Tische:** z.B. 1–30 anlegen, dazu „Bar“, „Terrasse 1“ …
   **Tischkarten als PDF** herunterladen (6 pro A4-Seite, mit QR-Code), drucken, schneiden – nur nötig, wenn das
   Personal Tische scannen soll: **Kasse & App › Bestell-App › „Tisch per QR-Code scannen (Personal)“** (Standard
   aus; dann steht in der Tischwahl **✕** statt QR – löscht die ganze Eingabe). Mit der **QR-Bestellung am Tisch**
   genügen deren QR-Codes: die Bestell-App liest sie auch – ein Code pro Tisch für Gäste und Personal.
   Ist das Scannen aus, fragt die Verwaltung beim Drucken der Tischkarten (PDF oder Bon), ob sie es jetzt einschalten
   soll: **Einschalten und weiter** / **Ohne einschalten weiter** / Abbrechen (Festleitung: nur Hinweis, einschalten
   darf ein Admin).
   **Tischplan** (wahlweise, ab Festleitung): **+ Bereich** (z.B. Saal, Terrasse – jeder Bereich hat einen eigenen
   Plan) › **Grundriss hochladen …** (PNG, JPG oder WebP, höchstens 15 MB; ein PDF vorher als Bild speichern; wird
   auf 2000 Pixel verkleinert und ist in der Sicherung). Ohne Grundriss geht es auch: Bühne, Bar usw. mit
   **+ Beschriftung** als Flächen einzeichnen. Tische unter **Nicht im Plan** antippen (setzt sie in die Mitte) oder
   **Reihe einfügen** (z.B. 5 Festbänke ab Nummer 13 – fehlende Tische werden angelegt). Dann ziehen, an der Ecke
   unten rechts grösser machen, am Punkt oben drehen (in 15°-Schritten, mit Umschalt frei); Form *Festbank*,
   *eckig* oder *rund*. Mehrere mit einem Rahmen oder Umschalt-Klick wählen und **Ausrichten**; Pfeiltasten
   verschieben fein, **Entf** nimmt aus dem Plan, **Strg+Z** macht rückgängig. Gespeichert wird sofort.
   **Am Handheld** prüft, ob Tischnummern auf einem kleinen Handheld übereinanderliegen (dann zoomt die Bedienung oder
   wählt aus einer Liste; liegen sie auch gezoomt übereinander: etwas auseinanderziehen oder Bereich teilen).
   **Tischwahl am Handheld beginnt mit** *Plan*, *Nummer* oder *Kein Plan* (nur Nummer, ohne Umschalter).
4. **Geräte & Netz › Einstellungen:** FestPOS-WLAN, Fully-Passwort und Einrichtungs-QR einstellen (Admin). Danach jedes
   XCover unter **Handhelds & PC › Gerät hinzufügen** einrichten (siehe Schritt 4); unter **Netz & Internet** Switch und
   Access Point.
5. **Kasse & App › Veranstalter:** Anlassname (steht auf jedem Bon), Bezeichnung **Tisch oder Platz** (App, Bon, Tischkarten und in der
   Verwaltung: Menü «Plätze», Platzplan, offene Plätze …), Logo. **Kasse & App › Kasse:** Zahlart, Trinkgeld, MWST.
   **Produkte › Lieferzeiten:** die Lieferzeitpunkte.
   **Kasse & App › Bestell-App:** Bestellverlauf und zuletzt bediente Tische ein/aus, PIN-Sperre, Testmodus, ausserdem:
   - **Produktbilder:** *Keine*, *Klein* (Ecke unten rechts) oder *Gross* (rechts in voller Höhe, Name und Preis
     auf hellem Grund). Die Kacheln bleiben gleich gross.
   - **Wischen wechselt die Kategorie:** im Produktbereich nach links/rechts wischen = nächste/vorherige Kategorie.
   - **Einführung automatisch:** Gibt jemand auf einem Handheld einen (neuen) Gerätenamen ein, startet die Einführung.
   Danach **Wartung › Sicherung**: Sicherungs-Passwort drucken und **Sicherung herunterladen** (mit eigenem Passwort).
6. **Optionen** (optional): in der Produktliste Spalte „Optionen“, z.B. Gruppe „Zucker“ mit
   „ohne, 1 Zucker, 2 Zucker“ als **Pflicht**, oder Freitext erlauben. Geht auch per CSV-Spalte `optionen`.

## 4. Handhelds einrichten (je XCover einmal)

1. WLAN der Festwirtschaft verbinden, **IP fest** (192.168.55.10x, Maske 255.255.255.0,
   Gateway 192.168.55.10). Bei „Kein Internet“ → **verbunden bleiben**.
   „Auf mobile Daten umschalten“ ausschalten.
2. `ca.crt` aufs Handheld: in Chrome **http://192.168.55.10/ca.crt** öffnen (oder per USB, Mail …), dann
   Einstellungen › Sicherheit und Datenschutz › Weitere Sicherheitseinstellungen › Vom Gerätespeicher
   installieren › **CA-Zertifikat**. Zur Kontrolle: Den Fingerabdruck zeigt die Verwaltung › Geräte & Netz › Pi › Zertifikate.
3. **Gerätezertifikat:** am PC in der Verwaltung › **Geräte & Netz › Handhelds & PC › Gerät hinzufügen › Nur Zugang (ohne Fully)** den Namen
   eintragen (z.B. „XCover 1“) › **Weiter**. Der Ablauf zeigt zuerst einen **WLAN-QR** (Kamera-App › «Verbinden»),
   dann den QR-Code für das Zertifikat. Diesen mit der **Kamera-App** des Handhelds scannen, Link öffnen,
   die heruntergeladene Datei antippen (oder: Einstellungen › Sicherheit und Datenschutz ›
   Weitere Sicherheitseinstellungen › Vom Gerätespeicher installieren ›
   **VPN- und App-Benutzerzertifikat**) und das angezeigte **6-stellige Kennwort** eingeben.
   Danach die heruntergeladene Datei `festpos-….p12` im Ordner **Downloads** des Handhelds **löschen** – das kurze
   Kennwort ist zum Abtippen gedacht, nicht als Schutz für eine Datei, die liegen bleibt.
4. Chrome: **192.168.55.10** öffnen. Chrome fragt einmal, welches Zertifikat verwendet werden
   soll → „XCover 1“ wählen. Dann Menü › **Zum Startbildschirm hinzufügen**.
   Ab jetzt startet FestPOS wie eine App, auch ohne Verbindung.
5. In der App oben auf „Verbunden“ tippen → **Gerätename** eintragen, am besten den Namen der Bedienung
   (z.B. „Anna“). Auf Bon, Statusanzeige, Verwaltung und Tagesabschluss steht dann das ausgerollte Gerät aus dem
   Gerätezertifikat und in Klammern dieser Name: **„XCover101 (Anna)“**. So ist klar, welches Handheld und wer.
   Dieser Name in der App darf auf mehreren Handhelds gleich sein (z.B. zweimal „Bar“): Die Statusanzeige erkennt die
   Handhelds am Gerätezertifikat und zeigt den Namen aus dem Zertifikat gross, den aus der App klein darunter.
6. Einmal den QR-Knopf antippen und den **Kamera-Zugriff erlauben** (für die Tisch-QR-Codes).

**Mit Fully Kiosk Browser** geht das alles ohne Handarbeit: Verwaltung › Geräte & Netz › Handhelds & PC › **Gerät hinzufügen** – entweder
**Neues Handheld einrichten (Werksreset)** mit einem QR-Code pro Handheld oder **Fully auf vorhandenem Handheld** (siehe
„Akkustand und Fully Kiosk Browser“). Das darf auch die Festleitung; die Einstellungen für alle (Geräte & Netz ›
Einstellungen) macht der Admin.

**Warum ist das sicher, ohne Passwort?** Die App läuft auf Port 443, und dort lässt der Server nur
Geräte mit einem Zertifikat der FestPOS-Geräte-CA überhaupt verbinden. Das Zertifikat liegt in
Android selbst, nicht im Browser: Cache oder Browserdaten löschen ändert daran nichts. Ein Gast im
WLAN bekommt nicht einmal die Seite zu sehen. Die Gerätezertifikate gelten standardmässig 10 Jahre (einstellbar, siehe unten).

**Ablauf und Erneuern:** Die Gültigkeit stellst du unter Verwaltung › Geräte & Netz › Einstellungen › Gültigkeit ein
(Standard 10 Jahre, Warnung 2 Jahre vorher – passend, wenn das System nur einmal im Jahr läuft).

*Handhelds mit Fully* erneuert der Pi **selbst** (Einstellung „Automatisch erneuern (Fully)“, Standard 2 Jahre vor
Ablauf, „aus“ schaltet es ab). Er wartet, bis das Handheld gerade niemand braucht – keine Bestellung wartet, 10 Minuten
nichts bestellt und der Bildschirm aus (oder 10 Minuten keine Bestellung im ganzen Fest) –, spielt das neue Zertifikat
über die Fernverwaltung ein und startet die App neu. Am Handheld ist nichts zu tun, die App zeigt keinen Hinweis. In der
Verwaltung › Geräte & Netz › Handhelds & PC steht beim Zertifikat „wird automatisch erneuert“ bzw. „wird gerade erneuert“. Meldet sich das
Handheld nicht innert 10 Minuten mit dem neuen Zertifikat, versucht es der Pi gleich nochmals; nach zwei Fehlschlägen an
einem Tag steht dort **„Erneuern gescheitert“** mit dem Grund, die Übersicht zählt es zu „Zertifikate laufen ab“ und
am nächsten Tag versucht es der Pi wieder. Von Hand: Geräteansicht › **Jetzt erneuern**. Das alte Zertifikat gilt in
jedem Fall weiter, bis das Handheld das neue benutzt – aussperren kann sich das Handheld dabei nicht.

*Handhelds ohne Fully* („Nur Zugang“, Chrome) nehmen Zertifikate nur aus dem Android-Speicher, und dort hinein kommt ein
Zertifikat nur mit einer Bestätigung am Handheld. Ab der Warnung zeigt die Bestell-App darum einen
Hinweis mit **Jetzt erneuern**: Ein Tipp lädt das neue Zertifikat herunter und zeigt das Kennwort,
installieren wie oben, Chrome neu starten und beim Nachfragen den Eintrag mit dem neuen Datum
wählen. Sobald das Handheld das neue Zertifikat zum ersten Mal benutzt, sperrt der Server das alte
(in der Verwaltung „ersetzt, gesperrt“). Meldet die App danach „Zertifikat ersetzt“, hat Chrome noch das
alte gewählt: Chrome neu starten und den Eintrag mit dem neuesten Datum wählen. Läuft ein Zertifikat ganz ab,
kommt das Handheld nicht mehr auf die App. Dann in der Verwaltung › Geräte & Netz › Handhelds & PC › Gerät › **Bearbeiten** › **Neues
Zertifikat** (oder bei Fully: **Gerätezertifikat einspielen**).

**Verlorenes Handheld:** Verwaltung › Geräte & Netz › Handhelds & PC › Gerät › **Bearbeiten** › **Sperren** (auch die Festleitung). Gesperrt werden
alle Zertifikate dieses Geräts (auch ein erneuertes) – ein anderes Gerät mit gleichem Namen nicht. Das Gerät kann ab
sofort weder bestellen noch erneuern und bekommt auch die Preisliste nicht mehr (rückgängig mit „Entsperren“, nur
Admin). Das gilt sofort auf allen Pi (der Pi, auf dem du sperrst, sagt es den anderen gleich; sonst spätestens nach 15
Sekunden). Hat das Gerät eine feste IP, bleibt sie eingetragen, damit Entsperren alles zurückbringt; solange es
gesperrt ist, steht es aber nur unter „Gesperrt“, fehlt auf der Statusanzeige und bekommt keine feste WLAN-Zuteilung.
Sein Name ist sofort wieder frei, z.B. für das Ersatzhandy; das Ersatzhandy bekommt dabei **nicht** die Rolle
des gesperrten. Heisst inzwischen ein anderes Gerät so, lässt sich das alte erst nach dem Umbenennen entsperren. **Gerät löschen** (nur Admin)
nimmt es aus der Liste und von der Statusanzeige; ein gültiges Zertifikat wird dabei gesperrt (im Dialog ist
„Zertifikat sperren“ vorgewählt) und steht danach unter „Gesperrt“. Dort nimmt **Endgültig löschen** (nur Admin) es
ganz aus der Liste, samt fester IP – die Sperre bleibt trotzdem bestehen. Soll ein Gerät nur von der Statusanzeige verschwinden: in der Geräteansicht die **feste IP
leeren** und speichern (Rückfrage; Fully-Zugang und feste WLAN-Zuteilung werden dabei vergessen).

**Gerätenamen sind eindeutig:** Zwei gültige Geräte dürfen nicht gleich heissen (ohne Gross-/Kleinschreibung) – beim
Hinzufügen, Umbenennen und Einrichten über Fully meldet die Verwaltung „Es gibt schon ein Gerät …“. Gesperrte und
abgelaufene Geräte zählen nicht. Intern erkennt FestPOS jedes Gerät an seiner Geräte-ID (Zertifikat), nie am Namen.
**Umbenennen** eines Geräts mit Zertifikat: in der Geräteansicht den Namen ändern › **Speichern**. Der Name steht im
Zertifikat, darum bekommt das Gerät ein neues mit dem neuen Namen (gleiches Gerät, die Rolle bleibt): mit Fully kommt
es von selbst aufs Handheld, sonst den QR-Code mit dem Handheld scannen wie beim Hinzufügen. Das alte Zertifikat sperrt der
Server, sobald das Handheld das neue benutzt.

**„Name doppelt“** (nur bei Geräten aus älteren Versionen oder aus einer alten Sicherung):
Hiessen schon vorher zwei gültige Geräte gleich, stehen sie getrennt in der Liste und sind mit **Name doppelt**
markiert; die Übersicht zeigt „Mehrere Geräte heissen gleich“. Beide funktionieren weiter. Lösen: eines der beiden
öffnen und umbenennen – oder das, das nicht mehr gebraucht wird, sperren.

**Server-Zertifikate:** Die Verwaltung › Geräte & Netz › Pi zeigt, bis wann Server-Zertifikat und CAs
gelten, und warnt ebenfalls rechtzeitig (auch in der Übersicht). Das Server-Zertifikat erneuerst du am PC mit
`bash make-certs.sh festpos.conf` und spielst es auf jedem Pi mit `sudo festpos-zertifikate ~/festpos/deploy/festpos-tls`
ein (vorher den Projektordner auf den Pi kopieren) – an den Handhelds ändert sich nichts. Liegt `ca.key` nicht in
`~/festpos-ca` (z.B. auf dem USB-Stick): `FESTPOS_CA_DIR=/pfad/zum/ordner bash make-certs.sh festpos.conf`.
Ohne `ca.key` bricht `make-certs.sh` ab, statt still eine neue Zertifizierungsstelle zu erzeugen.

### Neue Zertifizierungsstelle (einmalig, wenn die Verwaltung es meldet)

Ältere Installationen haben eine Zertifizierungsstelle **ohne** Namensbeschränkung. Die Verwaltung › Geräte & Netz › Pi
zeigt dann einen roten Hinweis. Umstellen, wenn kein Anlass bevorsteht – jedes Handheld und jeder PC braucht danach
die neue `ca.crt`. Die Gerätezertifikate der Handhelds bleiben gültig.

1. Am PC im Ordner `festpos/deploy`: `bash make-certs.sh festpos.conf --neue-ca`
   (die alte CA wird in `~/festpos-ca/alt-…` gesichert; lag `ca.key` noch in `festpos-tls/`, wird er dabei
   nach `~/festpos-ca/` verschoben).
2. Projektordner auf jeden Pi kopieren und dort `sudo festpos-zertifikate ~/festpos/deploy/festpos-tls`
   (prüft die Dateien, sichert die alten auf der Boot-Partition und wendet die neuen sofort an).
3. Jedes Handheld: Einstellungen › Sicherheit und Datenschutz › Weitere Sicherheitseinstellungen ›
   Benutzerzertifikate (bzw. Vertrauenswürdige Anmeldedaten › Benutzer) › alte **„FestPOS Lokale CA“** entfernen.
   Dann **http://192.168.55.10/ca.crt** öffnen und als CA-Zertifikat installieren (Name „FestPOS Lokale CA 2026“).
4. Jeder PC mit Verwaltung: alte CA aus dem Zertifikatsspeicher entfernen, neue `ca.crt` importieren.

**Welche Version läuft?** In der App oben auf „Verbunden“ tippen: Dort steht die App-Version.
Weicht sie vom Server ab, die App zweimal neu laden. Die Verwaltung zeigt die Server-Version unten links (Klick auf den eigenen Namen).

**PIN (optional):** Verwaltung › Kasse & App › Bestell-App › PIN-Sperre. Die App sperrt sich nach einer
wählbaren Zeit ohne Bedienung und/oder sobald der Bildschirm aus war, und verlangt dann die PIN. Die Sperre ist
**orange mit Schloss** – nicht mit der Tischwahl zu verwechseln, die gleich aussehende Zahlentastatur hat.
Die PIN funktioniert auch ohne Verbindung zum Server. Sie ist ein **Bedienschutz** (gegen neugierige Gäste, wenn das
Handheld kurz liegen bleibt), kein Schutz gegen Fachleute: damit sie offline geht, liegt ihr Prüfwert auf jedem Handheld,
und eine 4- bis 8-stellige Zahl lässt sich daraus rasch erraten. Den eigentlichen Schutz gibt das Gerätezertifikat
(bei Verlust **Sperren**).
**PIN pro Gerät (T14):** Verwaltung › Geräte & Netz › Handhelds & PC › Gerät › **PIN dieses Geräts** (nur Admin): eigene PIN setzen, ändern
oder entfernen. Angezeigt wird nur «Eigene PIN gesetzt», «Gemeinsame PIN» oder «Keine PIN». Ein Handheld mit eigener PIN
ist gesperrt, auch wenn die gemeinsame PIN aus ist; es bekommt nur den Prüfwert seiner eigenen PIN. Zeitsperre und
«sperren, wenn der Bildschirm aus war» gelten für alle. Jede Änderung steht im Protokoll (ohne PIN).

**Testmodus zum Üben (optional):** Verwaltung › Kasse & App › Bestell-App › Testmodus: einschalten und eine **Test-PIN**
festlegen. Mit **Test-PIN verlangen** (Standard) braucht es die PIN; ohne Häkchen startet jede Bedienung den
Testmodus selbst (mit kurzer Rückfrage) – dann zählen aber auch Bestellungen, die jemand aus Versehen im Testmodus
aufnimmt, nicht. Auf dem Handheld: oben auf den Gerätenamen tippen › **Testmodus starten (Üben)** (› Test-PIN), oder am
Ende der **Einführung** «Testmodus einschalten?» › **Ja, üben** (vorgewählt).
- Solange er läuft, ist jeder Bildschirm **gelb-schwarz umrandet** und oben steht im gelben Balken **ÜBUNG** mit
  «kein Geld, kein Bon» bzw. «kein Geld, Bon mit TEST» (je nach Einstellung «Übungsbestellungen drucken») und
  „Beenden“. Warenkorb und Tisch sind getrennt von den echten.
- **Beim Start der App ist er immer aus** (Neuladen, Neustart von Fully oder Handheld, Aktualisierung der App); das Handheld
  meldet kurz „Testmodus beendet“. Ein Übungs-Warenkorb wird dabei verworfen.
- Übungsbestellungen zählen nirgends mit (Übersicht, Statusanzeige, Export). Die Statusanzeige zeigt ein
  Handheld im Testmodus gelb mit der Marke **TEST** an – so fällt auf, wenn jemand vergessen hat, ihn zu beenden.
  Beenden von aussen: Statusanzeige › Handheld antippen › **Testmodus beenden** (ab zwei Handhelds oben im Kärtchen
  «Alle aus dem Testmodus»), am Bildschirm des Pi ohne Anmeldung, sonst ab Festleitung; ebenso Verwaltung › Geräte & Netz › Handhelds & PC ›
  Gerät und Kasse & App › Bestell-App › Testmodus › **Alle beenden**. Das Handheld übernimmt es innert 10 Sekunden.
  Die Übersicht der Verwaltung zeigt, wie viele Handhelds gerade im Testmodus sind; Beginn und Ende stehen im Protokoll.
- **Testbestellungen drucken** (Einstellung): Die Bons tragen oben gross „TEST – Übung, nicht zubereiten, nicht
  verrechnen“ und unten „*** TESTBESTELLUNG ***“. Ohne diese Einstellung wird nichts gedruckt.
- Nach dem Üben: Verwaltung › Kasse & App › Bestell-App › Testmodus › **Übungsbestellungen löschen** (echte Bestellungen bleiben).
  Wird der Testmodus in der Verwaltung ausgeschaltet, verlassen ihn alle Handhelds von selbst.

**Einstellungen am Handheld:** Das Fenster zeigt nur den **Namen** des Geräts, **Einführung**, **Testmodus** und
**Erweiterte Einstellungen** (Sprache, Darstellung, Bildschirm, Neu laden, Version, Verwaltung,
Statusanzeige). Einen Speichern-Knopf gibt es nicht: Der Name gilt, sobald man Enter drückt, weitertippt oder das
Fenster schliesst; Sprache, Darstellung und Bildschirm wirken sofort.

**Einführung:** Auf dem Handheld die Einstellungen öffnen (Tisch-/Platzwahl: oben links ☰ oder den Namen antippen,
auf den anderen Seiten: Status oben rechts › Einstellungen) › **Einführung**. Sie zeigt Schritt für Schritt
Tischwahl, Bestellen, Senden und Kasse mit Beispieldaten – es wird dabei nichts bestellt. Funktionen, die
eingeschaltet sind, erklärt sie zusätzlich (Ausverkauft/Restmenge, Lieferzeit, Zahlart, Quittung, PIN-Sperre). Ist
der Testmodus erlaubt, fragt der letzte Schritt **«Testmodus einschalten?»** – vorgewählt ist **Ja, üben**.

## Bedienung (Kurzfassung fürs Personal)

1. Tischnummer tippen (✕ löscht alles, ⌫ die letzte Ziffer) oder – wenn eingeschaltet – QR-Knopf → Tischkarte bzw.
   QR-Code der Gäste scannen → **Weiter**
   Mit Tischplan: oben **Plan | Nummer** umschalten. Im Plan den Bereich wählen (Saal, Terrasse …) und den Tisch
   antippen. Zoomen mit zwei Fingern (dabei gleich verschieben), Doppeltipp oder **+ / −**, **⤢** zeigt wieder alles. Liegen Nummern
   übereinander, fragt die App «Welcher Tisch?».
   Mit **Tisch offen lassen** (Kasse): nach «Senden» bleibt der Tisch offen – «Nächste Bestellung».
   Am Schluss den Tisch wählen › **Abrechnen** (oder gleich nach dem Senden **Jetzt abrechnen**) › kassieren wie
   gewohnt › **Bezahlt · Tisch schliessen**.
2. Produkte antippen (jedes Antippen = +1, Minus-Feld = −1) → **Senden**.
   Kategorie wechseln: oben antippen oder im Produktbereich nach links/rechts wischen.
3. Kasse erscheint sofort: **Rückgeld** (Gegeben antippen oder eintippen – „Gegeben“ und „Rückgeld“ stehen in
   einer Zeile, die Tastatur bleibt stehen; das Feld „Gegeben“ antippen scrollt so weit, dass die ganze Tastatur
   sichtbar ist; zu wenig: „Fehlt noch“ rot, sonst Rückgeld grün; reicht „Gegeben“, heisst der grüne Knopf
   **„Bezahlt · Nächste Bestellung“** und hält die Zahlung fest), **Teilen** oder **Einzeln** – beide pro Person in
   zwei Schritten.
   **Teilen**:
   - **Aufteilen:** oben die Anzahl Personen (− / +), darunter jede Person mit ihrem Betrag – auf 5 Rappen gerundet,
     der höhere zuerst, zusammen genau das Total (z.B. 17.00 für 3: 5.70 / 5.65 / 5.65). Eine offene Person antippen:
     gleich diese Person kassieren (wie der grüne Knopf). **Lange drücken:** **eigener Betrag** (z.B. Person 2 zahlt
     10.00 – die übrigen teilen den Rest), dort auch **„Person 2 jetzt kassieren“**. Unten **„Weiter: Person 1 · CHF 5.70“**.
   - **Person kassieren:** Betrag, **Zahlart dieser Person** (Bar / Karte / TWINT – bei „Pflicht“ muss sie gewählt
     sein), Gegeben/Rückgeld, Scheine, **+ Trinkgeld**, Karte/TWINT über Payrexx → **„Person 1 hat bezahlt“**,
     „Zurück“ führt zur Aufteilung.
   - Danach zurück in der Aufteilung: bezahlte Personen grün mit Zahlart, oben bezahlt / offen und „P1 ✓ 5.70 · Bar“.
     Die Anzahl ändert nur die offenen Personen. Am Schluss **„Alles bezahlt ✓“** mit **Quittung pro Person** (über
     ihren Anteil, MWST anteilig, mit ihrer Zahlart), erst dann „Nächste Bestellung“.
   - **Rückgängig:** jede bezahlte Person (Chip oben oder in der Liste) antippen › **„Zahlung von Person 2
     rückgängig“** – auch eine mittlere; sie ist wieder offen, die anderen bleiben bezahlt. Nicht mehr mit gedruckter
     Quittung (fortlaufende Nummer) und nicht mit Karte/TWINT über Payrexx (Rückerstattung in der Verwaltung).
   - Jede Teilzahlung geht mit Zahlart an den Server (ohne Verbindung später); der Tagesabschluss zählt pro Zahlart.
     Wer Quittungen über Positionen braucht, nimmt „Einzeln“: eine Bestellung wird entweder über Anteile oder über
     Positionen quittiert.
   **Einzeln** – pro Person zwei Schritte:
   - **Schritt 1:** die Positionen der Person antippen (jedes Antippen = 1 Stück, **−** = eins weniger) →
     unten **„Weiter: CHF 10.50 kassieren“**. Über der Liste: **„Alle abwählen“** | **„Alle wählen“** (alles Offene,
     z.B. für die letzte Person). Ganz Bezahltes rutscht ans Ende der Liste. Jede Position ist gleich hoch: oben das
     Produkt, darunter „1 von 2 offen“ (bzw. „P1 bezahlt“), rechts Anzahl, **−** und Preis; nur ein sehr langer
     Name bricht um.
   - **Schritt 2:** wie „Rückgeld“, mit Zahlart dieser Person – Gegeben antippen oder eintippen (freiwillig) →
     **„Person 1 hat bezahlt“**.
     Die Seite springt dabei so, dass die ganze Tastatur über dem grünen Knopf steht – auch im Übungsmodus (bezahlt /
     noch offen darüber: nach oben scrollen). „Zurück“ unten führt zur Auswahl derselben Person.
   - Danach geht es mit Person 2 weiter. Oben stehen bezahlt / noch offen und wer schon bezahlt hat („P1 ✓ 10.50“ –
     antippen: Quittung oder diese Zahlung rückgängig – jede Person, nicht nur die letzte); Bezahltes ist grau mit P1, P2 …
   - Unten steht während „Einzeln“ immer nur der nächste Schritt; **„Nächste Bestellung“ erscheint erst, wenn alles
     bezahlt ist**. Der Zurück-Pfeil fragt nach, wenn schon etwas gewählt oder bezahlt ist. Zahlt der Rest doch
     zusammen: oben „Rückgeld“ oder „Teilen“ wählen – dort steht dann nur noch der Rest.
   - **Eigene Beträge** („ich zahle 20, du den Rest“): in **Rückgeld** den Betrag der ersten Person eintippen – reicht
     er nicht, steht unter dem Ziffernblock **„20.00 als Teilzahlung · Rest 13.00 offen“**. Antippen: oben stehen
     bezahlt / offen und „P1 ✓ 20.00“, unten geht es mit dem Rest weiter (beliebig viele Teile). In **Teilen** zahlt
     eine Person einen eigenen Betrag über die Personenliste (lange drücken, siehe oben). Teilzahlungen gelten in allen drei Arten;
     „P1“ antippen macht diese Zahlung rückgängig. Wechselt man nach einer Teilzahlung zu „Einzeln“, kostet eine
     Person nie mehr als noch offen ist. Ist die Auswahl mehr, zeigt
     eine orange Box die Rechnung („Gewählt 33.00 − schon bezahlt (P1) 10.00 = Person 2 zahlt den Rest 23.00“), die
     übrigen Positionen sind „schon gedeckt“, und der Knopf sagt „Rest der Bestellung“. Deckt die Auswahl den Rest
     genau, stehen die übrigen Positionen ebenfalls auf „schon gedeckt“ (ohne Box). Gedeckte Positionen lassen sich
     nicht mehr antippen. Teilzahlungen ohne Positionen
     stehen oben orange („P1 ✓ 10.00 · Teilzahlung“). Solange etwas offen ist, fragt „Nächste Bestellung“ nach.
   → **Nächste Bestellung** (den gleichen Tisch gibt es danach oben in der Tischwahl zum Antippen)
4. Nur wenn in der Verwaltung eingeschaltet: unten **Bar / Karte / TWINT** antippen, wie bezahlt wurde
   (bei „Pflicht“ geht es erst dann zur nächsten Bestellung). Sonst ändert sich nichts.
5. **Trinkgeld** (nur wenn in der Verwaltung eingeschaltet): unter dem Ziffernblock **„+ Trinkgeld“**.
   - Im Blatt **„Aufrunden auf 34.00 · 1.00“**, **„Stimmt so (50.00) · 17.00“** (nur wenn „Gegeben“ eingetippt ist),
     **+1 / +2 / +5** oder den Betrag eintippen; „Rückgeld danach“ steht gleich daneben. **„… Trinkgeld übernehmen“**.
   - Danach rechnet die Kasse mit Trinkgeld (auch „Passend“ und die Scheine) und zeigt **„inkl. Trinkgeld 1.00 ✓ ·
     ändern“** – antippen zum Ändern oder **„Kein Trinkgeld“**. Unter dem Bon steht das Trinkgeld der Bestellung.
   - **Teilen** und **Einzeln:** im Schritt „kassieren“ für diese Person (fällt bei „rückgängig“ weg).
   - **Karte/TWINT über Payrexx (NFC):** zuerst „+ Trinkgeld“, dann mit NFC zahlen – der Gast zahlt Betrag und
     Trinkgeld in einem; danach „inkl. Trinkgeld … ✓ (Karte/TWINT)“ (nicht mehr änderbar).
   - Die Zahlart des Trinkgelds ist die Zahlart der Bestellung (nicht erfasst = bar). Es geht wie die Zahlart an den
     Pi, auch nach einem Funkloch.

**Quittung für den Gast** (nur wenn in der Verwaltung eingerichtet, siehe „Kassenbuch“): in der Kasse unten links
**Quittung** – druckt die ganze Bestellung auf dem Quittungsdrucker; nochmals antippen druckt eine **Kopie**. Bei
„Einzeln“ pro Person: oben „P1 ✓ 10.50“ antippen › **Quittung drucken**, oder am Schluss in der Übersicht bei jeder
Person; danach lässt sich diese Zahlung nicht mehr rückgängig machen. Auch nachträglich: **Verlauf** › Bestellung › Quittung. Braucht Verbindung zum Server.

**Nachbuchen ohne Bon** (schon geliefert, beim Abrechnen gemerkt oder vom Gast gesagt): Küche und Bar bekommen
nichts, der Betrag zählt wie jede Bestellung (Umsatz, MWST, Restmenge).
- **Bei jedem Tisch:** Produkte wählen › Warenkorb › unten **Nachbuchen ohne Bon**. Der Warenkorb wird orange («Kein
  Bon für Küche und Bar»), der grüne Knopf heisst **Nachbuchen ohne Bon**. **Doch mit Bon senden** schaltet zurück.
  Danach geht es normal weiter (Kasse bzw. «Tisch bleibt offen»).
- **Beim Abrechnen eines offenen Tisches:** unter dem Bon **+ Nachbuchen ohne Bon** › Produkte wählen (oben ein
  oranger Balken, **Abbrechen** führt zurück) › **Nachbuchen** › Warenkorb **Nachbuchen ohne Bon**. Danach steht die
  Abrechnung mit dem neuen Total wieder da; die Position ist mit «nachgebucht» markiert. Auch in **Einzeln**: der Link
  steht unter der Liste der Positionen; danach ist man wieder in Einzeln, die gewählten Positionen bleiben gewählt und
  die nachgebuchte steht unten in der Liste.
- Der Modus gilt nur für diesen einen Vorgang – die nächste Bestellung druckt wieder Bons.
- Wer es darf: Verwaltung › Kasse & App › Kasse › **Nachbuchen ohne Bon** (alle Bedienungen / nur mit Storno-Recht /
  aus). In der Verwaltung › **Bestellungen** steht bei solchen Bestellungen «ohne Bon»; ein Storno druckt dort keinen
  Storno-Bon.

**Kopf der Seiten:** Links auf der Tisch-/Platzwahl **☰** und der Gerätename – beides öffnet die Einstellungen (beim
Namen ist er gleich zum Ändern markiert); auf den anderen Seiten Zurück und der Titel („Platz 12“, „Verlauf“). Rechts
immer der **Status** mit dem Logo und der **Verlauf** (im Verlauf selbst ohne). Der Status-Punkt gilt für Server und
Drucker: **grün** = alles gut. **Orange** = ein Drucker ist ausgefallen und seine Bons kommen auf einem anderen
(„Küche / Umgeleitet“) oder eine Bestellung wird gerade gesendet. **Rot** = Server nicht erreichbar („Offline /
2 warten“) oder Drucker ausgefallen ohne Umleitung („Küche / Ausfall“ – die Bons warten). Bei Orange und Rot steht
der Hinweis anstelle des Logos. Antippen zeigt Server und Drucker im Einzelnen (welcher, warum, wohin umgeleitet);
darunter **Einstellungen**.

**Verlauf:** die letzten 40 Bestellungen dieses Handhelds, gespeichert im Handheld. Beim Start, beim Öffnen des
Verlaufs und vor dem Öffnen der Kasse fragt die App den Pi, was aus ihnen geworden ist, und zeigt es an: **STORNO**
(ganz storniert), **abgerechnet** (offener Tisch abgerechnet), **abgeschlossen** (im Tagesabschluss) oder **gelöscht**
(auf dem Pi nicht mehr da, z.B. nach „Bestellungen löschen“). Abgeschlossene und gelöschte Bestellungen sind grau und
lassen sich nur noch ansehen – kein Kassieren, kein Storno, keine Quittung. Gelöschte Übungsbestellungen (Testmodus)
verschwinden sofort, echte nach einem Tag (falls auf dem Pi eine ältere Sicherung eingespielt wurde, fehlen sie dort
nur vorübergehend). Ohne Verbindung zum Pi bleibt alles, wie es ist; oben steht dann „Stand nicht geprüft“.

**Ausverkauft:** graue Kachel mit „Ausverkauft“ – lässt sich nicht antippen, die Küche bekommt keinen Bon. „noch 8“ auf
der Kachel = Restmenge; mehr als die Restmenge geht nicht in den Warenkorb. Handhelds mit der Rolle **Festleitung**
melden selbst: ☰ auf der Tisch-/Platzwahl › **Ausverkauft melden** › Produkt antippen (nochmals antippen = wieder
verfügbar). **Restmenge:** Produkt lange drücken oder rechts **„Stück“** antippen – mit − / + oder 5 / 10 / 20 / 50
die Anzahl wählen (oder eintippen) und grün **„Noch 7 verfügbar“**; 0 heisst ausverkauft, **„Ohne Restmenge“** verkauft
wieder ohne Zählen. Der Server zählt bei jeder Bestellung herunter. Die anderen Handhelds sehen es innert 10 Sekunden.

**Senden wartet kurz:** Nach «Senden» steht einen Augenblick „Wird gesendet …“ – der Server prüft den Bestand, erst
dann geht es zur Kasse. Geht etwas nicht (eben ausverkauft, nur noch 1): die Bedienung bleibt im Warenkorb, ein roter
Kasten „Nicht gesendet – bitte anpassen“ und die Positionen sind markiert; nichts ist gedruckt, nichts zu kassieren.
Gast fragen, dann von Hand ändern oder **Automatisch anpassen** (Ausverkauftes weg, Mengen gekürzt) und nochmals
senden. Ohne Antwort (offline, langsam) geht es nach höchstens 2 Sekunden trotzdem zur Kasse. Wird die Bestellung
dann doch abgelehnt, zeigt die Kasse **Nicht angenommen** (kein Total, keine Tastatur): **Anpassen** holt sie ohne
das Ausverkaufte in den Warenkorb, **Verwerfen** löscht sie.

**Stornieren am Handheld** (nur Handhelds mit der Rolle „Bedienung + Storno“ oder „Festleitung“, siehe „Kassenbuch“): Kasse oder Verlauf ›
**Stornieren …** › Menge pro Position, Grund antippen › **CHF … stornieren**. Braucht Verbindung zum Server; an der
Station kommt ein Storno-Bon. Ohne diese Rolle: Festleitung fragen (Verwaltung › Bestellungen).
Danach zeigt der Bon in der Kasse, was noch gilt: teilweise Storniertes mit kleinerer Menge und «1 storniert» darunter,
ganz Storniertes durchgestrichen mit «storniert» statt Betrag; bei **Einzeln** lässt sich nur noch wählen, was gilt.
Am **offenen Tisch** geht es beim **Abrechnen** genauso – das Fenster zeigt die Positionen aller Bestellungen am Tisch,
das Storno wird auf die passenden Bestellungen gebucht (die neueste zuerst). Nicht stornierbar sind Stück, die schon
jemand **Einzeln** bezahlt hat, und mehr als noch offen ist (zuerst die Zahlung zurücknehmen).

**Zur Verwaltung oder Statusanzeige:** Einstellungen (☰ auf der Tisch-/Platzwahl oder Status › Einstellungen) ›
Erweiterte Einstellungen › **Verwaltung öffnen** oder
**Statusanzeige**. Zurück zur Bestell-App: bei der Anmeldung der Verwaltung **Bestell-App**, in der Verwaltung links
(am Handheld oben) **Bestell-App**, in der Statusanzeige unten **Bestell-App**. Alles bleibt im selben Fenster – auch in
Fully Kiosk, wo es kein Adressfeld gibt. Beim Wechsel zur Bestell-App meldet sich die Verwaltung ab.

**Helligkeit:** mit den Lautstärketasten heller oder dunkler (nur in Fully, siehe „Akkustand und Fully Kiosk Browser“).

**Fully Kiosk** (nur in Fully, ☰ › Erweiterte Einstellungen): Helligkeit **☾ −** / **☀ +**, **Bildschirm aus**, **Cache
leeren und neu laden**, **Fully neu starten** – Warenkorb, Ausgang und Verlauf bleiben. Dazu **Handheld neu starten**
(alle Rollen) mit Rückfrage. Ist auf dem Handheld noch etwas offen – Bestellungen oder Zahlungen nicht gesendet,
abgelehnte Bestellungen, Warenkorb –, zeigt die App das als Liste und fragt ein zweites Mal. Es bleibt alles
gespeichert, geht aber erst nach dem Neustart weiter – ausser einem Übungs-Warenkorb: Der Testmodus endet beim
Neustart, sein Warenkorb fällt weg (der echte bleibt). Neu starten kann Fully nur als **Geräteinhaber** (beim Einrichten
per QR); passiert nach 15 Sekunden nichts, sagt die App es. **Ausschalten** gibt es in der App nicht: Fully kann das aus
der App heraus nur auf gerooteten Geräten. Die Verwaltung schaltet ein Handheld über die Fernverwaltung von Fully aus
(Handhelds & PC › Gerät › **Ausschalten**). Handhelds mit der Rolle
**Festleitung** haben dazu **Kiosk-Modus aus** / **Kiosk-Modus ein** und **Android-Einstellungen** (entsperrt den Kiosk
und öffnet die Einstellungen). Danach zurück in die App und **Kiosk-Modus ein** – sonst bleibt das Handheld entsperrt
(orange in der Statusanzeige).

**Noch offener Tisch:** Liegen noch ungesendete Artikel im Warenkorb, steht in der Tischwahl unten z.B.
„Tisch 12 noch offen – 3 Artikel nicht gesendet“. Antippen führt zurück zu Tisch 12 mit diesen Artikeln.

**Optionen (z.B. Zucker):** Tippt man neben das Optionsfenster, warnt die App zuerst; erst ein zweites Tippen
daneben schliesst es ohne zu übernehmen. Einstellbar unter Verwaltung › Kasse & App › Bestell-App › **Tippen neben die
Optionsauswahl**: Warnen (Standard), Nichts tun (nur „Abbrechen“ schliesst) oder Schliessen ohne Übernehmen.

**Neu laden:** Einstellungen › Erweiterte Einstellungen › **Neu laden (App und Preisliste)** – holt App und
Preisliste in einem. Warenkorb, noch nicht
gesendete Bestellungen und Verlauf bleiben im Handheld. In Fully **„Enable Pull to refresh“ ausschalten**
(Web Browsing Settings) – sonst lädt die Seite beim Wischen nach unten ungewollt neu.

Ohne WLAN-Empfang wird trotzdem bestellt und kassiert. Oben rechts steht dann rot „Offline / 1 wartet“; die
Bestellung geht automatisch raus, sobald das Handheld wieder Verbindung hat.
Solange eine Bestellung wartet, versucht es die App alle 2 Sekunden. In Fully mit JavaScript-Schnittstelle hilft sie
zusätzlich nach: ein ausgeschaltetes WLAN schaltet sie wieder ein, und nach 20 Sekunden ohne Verbindung schaltet sie
das WLAN kurz aus und wieder ein, damit Android neu sucht und sich verbindet (höchstens alle 90 Sekunden, nach
5 Versuchen alle 5 Minuten). Das Ein- und Ausschalten erlaubt Android ab Version 10 nur, wenn Fully als
**Geräteinhaber** eingerichtet ist (Provisioning, siehe „Akkustand und Fully Kiosk Browser“) – sonst bleibt es beim
häufigeren Senden. Doppelte Bons entstehen dabei nicht.

Lehnt der Server eine Bestellung ab (z.B. Produkt inzwischen deaktiviert, Tisch unbekannt), erscheint oben
rot **„⚠ 1 abgelehnt“**. Antippen: **In den Warenkorb** holt die Bestellung zurück zum Korrigieren und
nochmals Senden, **Verwerfen** löscht sie. Abgelehnte Bestellungen wurden nicht gedruckt. Jede abgelehnte Bestellung bleibt in
der Liste, bis jemand sie korrigiert oder verwirft – auch wenn es viele werden (T42) – und steht in der Verwaltung im
**Protokoll** («Bestellung vom Handheld abgelehnt», mit Tisch und Grund), auch wenn das Handheld danach ausfällt.

---

### Lieferzeit (Sofort / Pause / Schluss)
- Zeitpunkte: Verwaltung › Produkte › Lieferzeiten (der erste ist der Standard).
- Pro Produkt: Verwaltung › Produkte, Spalte **Lieferzeit**:
  *Aus* (immer sofort) · *Wählbar* (Vorgabe Sofort, ändern per langem Drücken oder im Warenkorb „Anpassen“) ·
  *Pflicht* (beim Antippen muss die Bedienung wählen) · zusätzlich erlaubte Zeitpunkte einschränken,
  **nur einer angekreuzt = erzwungen** (Kachel zeigt z.B. „⏱ Schluss“).
- Bons: pro Zeitpunkt eigene Bons mit grossem schwarzem Balken „PAUSE“ / „SCHLUSS“. Einzelbons werden pro
  Zeitpunkt gezählt (Schluss 1/5 … 5/5), so sieht die Küche, wie viele für diesen Zeitpunkt offen sind.
  Pro Drucker und Bestellung zählt die Nummer über Sammelbon und Einzelbons (Sammelbon = «1 von 3», Würste «2 von 3» und «3 von 3»).
- **Lesbarkeit der Bons:** etwas mehr Zeilenabstand, nach jeder Position etwa 2 mm Luft, Optionen und Bemerkung **fett**
  («ohne Senf» fällt auf). Gilt für Sammel-, Einzel- und Storno-Bons; Quittungen bleiben wie sie sind.

### Ausverkauft und Restmenge
- Verwaltung › Produkte, Spalte **Bestand** (ab Festleitung): **Ausverkauft** ankreuzen – die Kachel wird auf allen
  Handhelds grau, Bestellungen dafür lehnt der Server ab (keine Bons).
- **Ohne Bestand** (Normalfall): es wird nicht gezählt, die Felder „Noch da“ und „Warnen ab“ sind grau. **Mit Restmenge**: z.B. „Noch da 40“, „Warnen ab 10“ – jede
  Bestellung zählt herunter, bei 0 ist das Produkt ausverkauft. Ab der Warnschwelle steht „noch 8“ auf der Kachel und
  die Statusanzeige wird orange („Bratwurst: noch 8“), ausverkaufte Produkte ebenso („Bratwurst ausverkauft“).
- Reicht die Restmenge für eine Bestellung nicht (zwei Handhelds gleichzeitig), wird die ganze Bestellung abgelehnt –
  nichts wird halb gedruckt. Die Meldung nennt alle betroffenen Produkte auf einmal.
- **Vor dem Kassieren auf den Server warten** – Verwaltung › Kasse & App › Kasse (Admin):
  *Immer* (Standard, empfohlen) · *Nur bei Produkten mit Restmenge* (sonst sofort zur Kasse) · *Aus* (sofort zur Kasse,
  Ablehnungen erst danach in der Kasse und unter „abgelehnt“). Dazu **Höchstens 2 / 5 Sekunden** (5 bei schwachem
  WLAN). Der Server prüft in jedem Fall: für Ausverkauftes kommt nie ein Bon.
- **CSV** (Produkte › Import & Export): Spalten `bestand` (Restmenge, leer = ohne Bestand), `warnen_ab`,
  `ausverkauft` (ja/leer). Der Export enthält sie. Beim Import überschreiben sie den Bestand, sobald die Spalte in der
  Datei steht; fehlt sie, bleibt der Bestand. Die Vorschau zeigt „Bratwurst jetzt 37 → 120“. **Achtung:** eine alte
  Exportdatei setzt die Zähler auf ihren damaligen Stand zurück.
- Übungen im Testmodus zählen nicht. Ein **Storno bucht nicht zurück** (die Küche hat vielleicht schon gekocht):
  Restmenge bei Bedarf von Hand richtigstellen. Nachschub: neue Restmenge eintragen.
- Am Handheld: Rolle **Festleitung** (Verwaltung › Geräte & Netz › Handhelds & PC › Rolle, ab Festleitung) – darf stornieren und
  ausverkauft melden (☰ › Ausverkauft melden). Wieder freigeben setzt eine Restmenge 0 auf „ohne Bestand“.

### Optionen beim Bestellen
- Produkt mit **Pflicht-Optionen** (grünes Symbol): Antippen öffnet die Auswahl, Anzahl wählen, „Hinzufügen“.
- Produkt mit **freiwilligen Optionen** (graues Symbol): Antippen = +1 ohne Optionen.
  **Lange drücken** öffnet die Auswahl.
- Nur **eines von mehreren** anpassen (z.B. 1 von 3 Kafi mit 2 Zucker): Warenkorb › „Einzeln anpassen“ ›
  Auswahl treffen, Anzahl 1 › „Übernehmen“. Es entsteht eine eigene Position, die auf dem Bon
  mit ihren Optionen direkt unter dem Produkt steht.
- Gibt es dieselbe Auswahl in zwei Gruppen (z.B. „ohne“ bei Zucker und bei Milch), steht der Gruppenname
  dabei: „Zucker: ohne, Milch: ohne“.

## Sprachen (Übersetzung)

Mitgeliefert sind **Deutsch, Englisch, Französisch, Italienisch und Rumantsch Vallader** – für Bestell-App,
Verwaltung, Statusanzeige, Bons und Server-Meldungen. Jede Sprache ist eine Datei in `app/i18n/`:
`de.json` (Ausgangssprache), `en.json`, `fr.json`, `it.json`, `rm-vallader.json`.

Die Übersetzungen sind maschinell erstellt und geprüft (vollständig, gleiche Platzhalter, druckbar, passen auf
den Bildschirm). **Vor dem ersten Einsatz von Muttersprachigen durchlesen lassen** – vor allem Vallader.
Korrekturen direkt in der Datei machen, dann prüfen (Schritt 4) und mit `update.sh` einspielen.
Im Vallader stehen die Android-Menüpfade (Zertifikat installieren) auf Deutsch, weil es Android nicht auf
Rumantsch gibt.

Eine **weitere Sprache** ist eine weitere Datei daneben:

1. `de.json` kopieren, z.B. als `es.json` (Name = Sprachcode: `es`, `pt`, `rm-sursilv` …).
2. Oben `"_meta": {"name": "Español", "locale": "es-ES"}` eintragen.
3. Die Texte rechts übersetzen. **Schlüssel links und Platzhalter in geschweiften Klammern** (`{table}`,
   `{count}`) **unverändert lassen**. Mehrzahl: `{"one": "1 article", "other": "{count} articles"}`.
   Was fehlt, erscheint auf Deutsch.
4. Prüfen: `python3 tools/i18n_check.py` (meldet unbekannte Schlüssel, falsche Platzhalter und Zeichen, die der
   Bondrucker nicht kennt). `python3 tools/i18n_check.py --offen es` listet, was noch nicht übersetzt ist.
5. Mit `update.sh` einspielen.

Auswahl der Sprache:
- **Standardsprache:** Verwaltung › Kasse & App › Veranstalter. Gilt für Handhelds, Verwaltung und Statusanzeige,
  solange dort nichts anderes gewählt ist.
- **Pro Handheld:** oben auf den Gerätenamen tippen › Sprache.
- **Pro Browser (Verwaltung):** links unten auf den eigenen Namen klicken › „Sprache“.
- **Pro Drucker (Bons):** Verwaltung › Geräte & Netz › Drucker › Sprache der Bons – z.B. Küche auf Deutsch, Bar auf Englisch.
- Die Statusanzeige folgt der Sprache der Verwaltung in diesem Browser, am Pi-Bildschirm der Standardsprache.

Was **nicht** übersetzt wird: eigene Inhalte (Produktnamen, Kategorien, Optionen, Lieferzeitpunkte, Anlassname),
die Spaltennamen der CSV-Dateien, diese Anleitung, die Skripte auf dem Pi und die Protokolle (journalctl).
Fehlermeldungen, die gespeichert werden (z.B. bei Druckaufträgen), stehen in der Standardsprache.

## Störungen

**Drucker fällt aus – automatische Regel** (Verwaltung › Geräte & Netz › Drucker, pro Drucker „Wenn nicht erreichbar“):
- *weiter warten*: Bons bleiben in der Warteschlange, bis der Drucker wieder da ist (bisheriges Verhalten).
- *Bons umleiten auf …*: nach der eingestellten Zeit druckt der gewählte Drucker die Bons, mit dem Balken
  „Umgeleitet - Küche - Drucker gestört“ oben. Solange der Drucker gestört bleibt, gehen neue Bons direkt dorthin.
- *Warnung drucken auf …*: der andere Drucker druckt einmal „ACHTUNG – Drucker Küche nicht erreichbar,
  N Bons warten“. Die Bons warten und kommen, sobald der Drucker wieder läuft.

**Tischwahl** (Verwaltung › Tische): *Erzwingen* (nur erfasste Tische), *Warnen* (App fragt „Vertippt?“,
Bestellen bleibt möglich) oder *Freie Wahl*.

| Situation | Was tun |
|---|---|
| Drucker aus / Papier leer / Deckel offen | Nichts verloren: FestPOS fragt den Drucker vor jedem Bon ab und schickt nichts in einen gestörten Drucker. Die Bons warten (oder werden nach der eingestellten Regel umgeleitet) und kommen in richtiger Reihenfolge, sobald er wieder bereit ist. Statusanzeige und Verwaltung zeigen den Grund („Papier leer“, „Deckel offen“). Voraussetzung: Drucker eingestellt wie unter «Drucker einrichten». |
| Drucker rot «Antwortet nicht – Papier und Deckel prüfen» | Der Drucker hat früher auf die Statusabfrage geantwortet und schweigt jetzt – meist Papier leer oder Deckel offen bei einem Drucker mit Werkseinstellung («Zustand BUSY»). FestPOS schickt nichts, die Bons warten. Papier einlegen, Deckel schliessen; kommt die Meldung weiter: Drucker aus- und einschalten, dann «Drucker einrichten» nachholen. |
| Drucker defekt | Verwaltung › Druckaufträge › **Offene Bons umleiten** auf den anderen Drucker (oder Geräte & Netz › Drucker › Bearbeiten). Danach Produkte umstellen. Den defekten Drucker **deaktivieren**, nicht löschen: Löschen geht erst, wenn keine offenen oder unklaren Bons mehr an ihm hängen (sonst wären sie weg). |
| Bon „unklar“ | Druckstatus unbekannt (Server fiel genau beim Drucken aus, die Verbindung zum Drucker brach mitten im Senden ab, oder die Meldung „gedruckt“ ging verloren). Beim Drucker nachschauen, dann Verwaltung › Druckaufträge: **Erledigt** oder **Nachdrucken**. Nachgedruckte Bons tragen oben ein schwarzes **NACHDRUCK**. |
| Bon hängt auf „druckt“ | Nach 2 Minuten lassen sich auch solche Bons **nachdrucken** oder als **erledigt** markieren (vorher nicht – er könnte gerade noch gedruckt werden). |
| Gerät verloren | Verwaltung › Geräte & Netz › Handhelds & PC › Gerät › **Bearbeiten** › **Sperren** (sperrt alle Zertifikate dieses Namens, ab Festleitung). Gesperrte Geräte stehen unten unter „Gesperrt“; **Endgültig löschen** (Admin) nimmt sie aus der Liste, die Sperre bleibt. **Gerät löschen** (Admin) entfernt es aus der Liste und sperrt ein noch gültiges Zertifikat. |
| „Erneuern gescheitert“ bei einem Fully-Handheld | Der Pi konnte das neue Zertifikat zweimal nicht einspielen (Grund in der Geräteansicht). Das alte gilt weiter. Prüfen, ob Fully erreichbar ist (Geräteansicht › **Prüfen**), dann **Jetzt erneuern**. Sonst versucht es der Pi am nächsten Tag wieder. Fully muss die Datei vom Pi abholen können (Port 80 des Pi im FestPOS-WLAN). |
| Falsche Uhrzeit auf Bons | Als Festleitung anmelden: Der Hinweis «Bitte Uhrzeit prüfen» bietet «Uhrzeit dieses Geräts übernehmen» (alle Pi). Siehe «Uhrzeit» unten. |
| Pi 5: Uhr nach dem Einlagern falsch | RTC-Batterie leer oder wird nicht geladen: Geräte & Netz › Pi › Pi antippen › RTC-Batterie zeigt Spannung und «wird geladen». Mit der offiziellen Batterie `RTC_AKKU=1` in `festpos.conf` (siehe «Uhrzeit»). |
| Pi 5: Uhr nach jedem Neustart auf dem Stand des letzten Herunterfahrens, obwohl die Batterie voll ist | fake-hwclock hat die richtige RTC-Zeit beim Start überschrieben (`journalctl -b -u fake-hwclock-load`). Ab dieser Version schaltet FestPOS fake-hwclock auf Pi mit voller RTC-Batterie selbst ab – wirksam ab dem zweiten Start nach dem Update. Sofort: `sudo apt purge fake-hwclock`. |
| **Einzel-Pi: SD-Karte defekt** | Ersatzkarte einsetzen und starten. Neue Karte (Raspberry Pi OS + `setup.sh` bzw. Image, ohne `festpos.conf`): in der **Ersteinrichtung** «Sicherung einspielen» wählen – der Pi übernimmt Schlüssel und Daten von selbst. Von «SD-Karte schreiben» oder mit `festpos.conf`: Verwaltung › Wartung › Sicherung › **Sicherung einspielen** – die neuste vom USB-Stick (Ordner `festpos-sicherung`, siehe unten) oder vom PC, mit dem **Sicherungs-Passwort** vom Ausdruck (oder QR-Code scannen). Die Sicherung bringt alles mit: Daten, Zugangsdaten, Zertifikate und Schlüssel – weichen die Schlüssel der Ersatzkarte ab, schreibt der Pi sie zurück und startet neu. Die Handhelds bestellen danach ohne Neueinrichtung weiter. Danach die **Prüfliste** durchgehen. Bestellungen seit der Sicherung fehlen, die Handhelds senden aber alles nach, was noch bei ihnen wartet. Offene Bons aus der Sicherung werden nicht gedruckt; die Quittungsnummer springt auf die nächste Hunderterzahl (Lücke im Protokoll). Mehr: `docs/backup-restore.md`. |

**Tipp Einzelbetrieb:** Nach dem Einrichten gleich eine zweite Karte genauso vorbereiten, das Sicherungs-Passwort
ausdrucken und einen USB-Stick mit dem Ordner `festpos-sicherung` stecken lassen. Dann dauert ein Kartentausch fünf
Minuten.

### Uhrzeit
Bons, Protokoll und Statusanzeige nehmen die Uhr des Pi. Die Zeitzone kommt aus `TZ` in `festpos.conf`
(Standard `Europe/Zurich`, Sommer- und Winterzeit automatisch).

**Woher der Pi die Zeit hat**
- **RTC mit Batterie** (Pi 5): hält die Uhr, solange der Pi keinen Strom hat. Mit der offiziellen, aufladbaren
  Batterie (SC1163) in `festpos.conf` `RTC_AKKU=1` eintragen (am PC auf der Boot-Partition). Beim nächsten Start
  trägt der Pi `dtparam=rtc_bbat_vchg=3000000` in `config.txt` ein (Kopie: `config.txt.festpos-alt`), ab dem
  Neustart danach lädt er die Batterie. Geräte & Netz › Pi › Pi antippen zeigt «Laden ab dem nächsten Neustart», dann
  «wird geladen». **Bei einer normalen CR2032 `RTC_AKKU=0` lassen** – sie darf nicht geladen werden.
  Vor dem Einlagern die Pi einige Stunden am Strom lassen (Batterie voll). Die Spannung beim Start steht jedes Mal
  im Protokoll («Pi gestartet (RTC-Batterie)»).
- **Ohne RTC oder mit leerer Batterie**: `fake-hwclock` stellt beim Start die zuletzt gespeicherte Zeit ein (stündlich
  und beim Stellen gespeichert) – ungefähr richtig statt 1970. Hält die RTC die Zeit (Batterie ab 2,5 V), schaltet
  `festpos-node` fake-hwclock beim Start ab und löscht die gespeicherte Zeit, denn es stellte sonst die richtige
  RTC-Zeit zurück. Wird die Batterie schwach, kommt es beim nächsten Start von selbst zurück.
- **Internet**: Ist der Pi online (Update, Router), holt chrony die Zeit von `ch.pool.ntp.org`. Das zählt als
  Bestätigung.
- **Andere Pi**: chrony gleicht die Pi untereinander ab. Startet ein Pi mit leerer RTC-Batterie, holt er die Zeit
  selbst von einem Pi mit sicherer Uhr (braucht `CLUSTER_SECRET`).
- **Festleitung**: Die Handhelds stellen den Pi **nicht** (mehr). Stattdessen fragt die Verwaltung nach.

**Hinweis «Bitte Uhrzeit prüfen»** (nur Festleitung und Admin, oben auf jeder Seite nach der Anmeldung):
- wenn die Zeit noch nie bestätigt wurde, nach einem Neustart und letzter Bestätigung vor mehr als 7 Tagen (also am
  ersten Festtag nach dem Einlagern), ohne Neustart nach 60 Tagen, wenn ein Pi mit leerer RTC-Batterie startete und
  noch nicht gestellt ist, wenn ein Pi mehr als 2 Minuten von diesem Gerät abweicht oder die Pi untereinander mehr als
  5 s;
- **Zeit stimmt** (nur bei höchstens 2 Minuten Abweichung), **Uhrzeit dieses Geräts übernehmen** (am besten von
  einem Handheld mit Mobilfunk) oder **Manuell einstellen** (Geräte & Netz › Pi › Uhrzeit › Manuell einstellen: Datum und Uhrzeit in der Zeitzone
  des Pi). **Nicht jetzt** gilt bis zur nächsten Anmeldung.
- Die Uhr des Pi steht in der Seitenleiste (am Handheld oben); rot, solange der Hinweis offen ist.
- Jedes Stellen und Bestätigen steht im Protokoll.

**Geräte & Netz › Pi**: in der Pi-Liste pro Pi Uhrzeit, Abweichung zu diesem Gerät, Quelle beim Start («RTC» oder
«RTC-Batterie leer») und seither (übernommen, bestätigt, Internet, folgt Pi …); Zeitzone und RTC-Batterie in der
Unterseite des Pi. Darunter «Zeit stimmt», «Uhrzeit von diesem Gerät übernehmen» und **Manuell einstellen …**.
Von Hand am Pi: `timedatectl`, `chronyc tracking`, `chronyc sources`, `cat /sys/class/rtc/rtc0/battery_voltage`
(µV, unter etwa 2 500 000 schwach) und `…/charging_voltage` (0 = lädt nicht).

**Uhr der Handhelds – Zeitserver per USB**: Die Handhelds holen ihre Zeit vom Pi, wenn der Pi als Zeitserver eingetragen
ist (bleibt bis zum nächsten Werksreset). Das geht nur per USB-Kabel am Pi (Fully darf das nicht, und Android wertet
den Zeitserver aus dem DHCP nicht aus):
- **Beim Werksreset** ist es der letzte Schritt der Einrichtung: Fully schaltet USB-Debugging ein, dann das Handheld mit
  dem USB-Kabel an einen Pi stecken und auf dem Handheld «USB-Debugging zulassen?» mit **Immer von diesem Computer
  zulassen** und **Zulassen** bestätigen. Der Pi trägt sich als Zeitserver ein und schaltet USB-Debugging wieder aus.
- **Bestehende Handhelds**: Geräte & Netz › Handhelds & PC › Handheld › Uhrzeit › **Zeitserver per USB einstellen (nur Android)** – die Karte erscheint nur bei Android-Handhelds (mit Fully oder Android gemeldet), nicht bei «Nur Zugang» (PC, Laptop, iPhone). Vorher am Handheld
  Einstellungen › Telefoninfo › Softwareinformationen › 7× «Buildnummer», dann Entwickleroptionen › USB-Debugging.
- Jeder Pi hat einen eigenen Schlüssel: an einem anderen Pi fragt das Handheld nochmals «Zulassen».
- Unter Geräte steht danach «Zeitserver: Pi» und, sobald die Bestell-App die Uhr meldet, ob sie mit dem Pi
  übereinstimmt.
- Braucht das Programm `adb` auf dem Pi (`setup.sh` installiert es; auf älteren Installationen einmal mit Internet
  `sudo apt install adb`).
- Mit SIM und mobilen Daten fragt Android den Zeitserver eventuell übers Mobilnetz – dann kommt die Zeit ohnehin von
  dort.

Der Pi gibt sich zusätzlich per DHCP als Zeitserver an (Option 42) – das nutzen manche Laptops, nicht die Handhelds.

### Automatische Sicherung auf einen USB-Stick

Auf einem USB-Stick (FAT32, exFAT, NTFS oder ext4) einen Ordner **`festpos-sicherung`** anlegen und den Stick an
einen Pi stecken. Dieser Pi schreibt gleich und dann alle 15 Minuten eine verschlüsselte Sicherung hinein
(`festpos-a-JJJJMMTT-hhmmss.festpos`) und behält dort die letzten 20 (ältere `.sqlite` dieses Pi zählen mit); andere
Dateien bleiben unberührt. Jede Datei wird nach dem Schreiben einmal zurückgelesen. Die Fully-APK (jeder Pi hat
sie, siehe „Fully aktualisieren“) legt der Pi einmal daneben (`fully.apk`, nicht verschlüsselt, nur wenn sie neu ist). Der Stick ist
nur während der paar Sekunden des Schreibens eingebunden und kann sonst jederzeit abgezogen werden. Ein Stick ohne
diesen Ordner (z.B. ein Update-Stick) wird nie beschrieben.

Übersicht und Statusanzeige zeigen, wann zuletzt gesichert wurde. Mit **einem** Pi warnen sie, solange keine frische
Sicherung da ist; im Cluster (jede Bestellung liegt schon auf zwei Pi) nur, wenn das Schreiben scheitert. Von Hand:
`sudo festpos-sicherung` (Stick suchen) oder `sudo festpos-sicherung --ordner /pfad`. Was zuletzt geschah:
`journalctl -u festpos-sicherung -n 20`.

**Verschlüsselt, mit allem drin:** Jede Sicherung enthält die ganze Datenbank samt Zugangsdaten (Fully, Router,
GitHub, WLAN), die Zertifikate und Schlüssel (`festpos-tls`), `CLUSTER_SECRET` und das Notfall-Passwort – verschlüsselt
(AES-256-GCM, Schlüssel aus dem Passwort mit scrypt). Die automatische Sicherung benutzt das **Sicherungs-Passwort**,
das der Pi bei der Einrichtung erzeugt: Verwaltung › Wartung › Sicherung › **Drucken (A4)** oder **Bon drucken**, dann
«Ausgedruckt und aufbewahrt» (bis dahin erinnert die Übersicht). Zusammen mit dem Notfall-Passwort aufbewahren. Ohne
dieses Passwort lässt sich eine Sicherung auf einem neuen Pi nicht einspielen. **Herunterladen** geht nur mit einem
selbst gewählten Passwort (mindestens 12 Zeichen). Ältere Sicherungen (`.sqlite`) lassen sich weiter einspielen.

**Einspielen auf demselben System** (z.B. nach einem falschen Import): Schlüssel, Zugangsdaten und Sicherungs-Passwort
des laufenden Pi bleiben; Sperren und Gerätezertifikate seit der Sicherung bleiben erhalten; Quittungsnummern laufen ohne
Lücke weiter. Die eigene Sicherung vom Stick braucht kein Passwort. Danach zeigt die Verwaltung eine **Prüfliste**
(Drucker, Handhelds, Zeit, Schlüssel, Zugangsdaten, APK …). Alles dazu: `docs/backup-restore.md`.

Am PC lesen: `python3 tools/sicherung_lesen.py DATEI.festpos ZIELORDNER` (fragt das Passwort; braucht das Paket
`cryptography`). Der Ordner enthält danach alle Schlüssel – löschen, wenn nicht mehr gebraucht.

---

## Später: Erweitern auf 3 Pis (ausfallsicher)

Verwaltung › Geräte & Netz › Pi › **Auf 3 Pi erweitern** (Admin, nur im Einzelbetrieb) führt durch die drei Schritte:

1. **Karten vorbereiten** – am einfachsten **hier schreiben** (T10): USB-Kartenleser mit einer leeren SD-Karte
   (32 GB empfohlen, mindestens 16 GB) an Pi A stecken, «Karte für Pi B schreiben», Buchstaben zum Bestätigen
   eintippen. Pi A kopiert sein System ohne Datenbank, Logs und Schlüssel dieses Pi (5–15 Minuten, nicht während
   eines Anlasses) und legt die Einstellungen für Pi B auf die Karte; danach ist der Leser ausgeworfen. Dasselbe für
   Pi C. Sind beide geschrieben, ist «Karten B und C sind vorbereitet» von selbst abgehakt.
   **Über einen PC** (wenn kein Kartenleser da ist): Pi B und Pi C wie in Schritt 1 einrichten (Raspberry Pi OS,
   `setup.sh`). Dann das **Kartenpaket** herunterladen und den Inhalt von `karte-b` bzw. `karte-c` auf die Boot-Partition der Karte kopieren
   (bestehende Dateien ersetzen). Darin: `festpos.conf` von Pi A mit **`CLUSTER_SIZE=3`** und ausgeschriebenem
   `CLUSTER_SECRET`, `node.conf` (`NODE=b` bzw. `NODE=c`), `festpos-tls/` und die Update-Schlüssel. Das Kartenpaket
   ist **nicht verschlüsselt** (die Karte liest es beim Start) – nach dem Kopieren löschen.
2. **Pi A umstellen:** geht nur im Einzelbetrieb, mit einer Sicherung aus der letzten Stunde und ohne laufenden
   Anlass. Pi A setzt `CLUSTER_SIZE=3` und startet neu; er arbeitet danach allein weiter, bis B und C dabei sind.
3. **Pi B und Pi C einstecken** – möglichst beide zusammen (solange erst einer dabei ist, braucht es beide). Sie treten
   automatisch bei und übernehmen alle Daten; die Seite zeigt den Beitritt live.
   Kontrolle: Verwaltung › Geräte & Netz › Pi zeigt drei erreichbare Pi (Übersicht: „3/3 Pi erreichbar“).

Von Hand geht es so: auf B und C `node.conf` und `festpos.conf` (mit `CLUSTER_SIZE=3`, sonst identisch mit
Pi A) setzen, auf Pi A `CLUSTER_SIZE=3` in `/boot/firmware/festpos.conf` und neu starten.

Die Handhelds merken davon nichts, die Adresse bleibt 192.168.55.10.

Wichtig: Ein neuer Cluster entsteht **immer aus Pi A im Einzelbetrieb**. Ein Pi mit
`CLUSTER_SIZE=3`, der noch nie Mitglied war, versucht nur beizutreten – auch nach vielen
gescheiterten Versuchen oder einem Neustart gründet er nie selbst. Das verhindert, dass zwei
getrennte Datenbestände entstehen. (Mitglied ist ein Pi, sobald er einmal einen Leader gesehen hat;
er merkt sich das in `/var/lib/festpos/rqlite/.festpos-member`.)

**Netz für 3 Pi (empfohlen):** Die Drucker ins eigene Drucker-Netz (VLAN, siehe „Drucker-Netz“ ganz oben – das
richtet FestPOS selbst ein). Weiter ginge noch: das WLAN der Handhelds in ein anderes VLAN als die Pi und vom WLAN zu den
Pi nur Port 80 und 443 durchlassen (Handhelds; 8443 nur für den Admin-PC und beim Einrichten der Gerätezertifikate) –
dafür braucht es aber einen Router zwischen den Netzen. Ohne das schützen Firewall der Pi, Gerätezertifikate,
`RAFT_TLS` und `SSH_FROM` – das genügt für einen Vereinsanlass.

### Störungen mit 3 Pis

| Situation | Was tun |
|---|---|
| 1 Pi fällt aus | Nichts. Die anderen übernehmen in 1–3 s. |
| SD-Karte defekt | Pi aus, Ersatzkarte mit gleichem Buchstaben rein, Pi an. Holt sich alle Daten selbst. Die Ersatzkarte schreibt ein anderer Pi: Geräte & Netz › Pi › **SD-Karte schreiben** (USB-Kartenleser) › Buchstabe des defekten Pi. Ohne Kartenleser: **Ersatzkarte vorbereiten** › Kartenpaket, Ordner auf die Boot-Partition einer mit `setup.sh` eingerichteten Karte. Die alte Karte danach nicht mehr einsetzen. |
| «SD-Karte schreiben» findet den Leser nicht | Leser direkt am Pi einstecken (nicht über einen Hub), andere Karte versuchen. Gesperrte Geräte zeigen den Grund: Systemplatte, USB-SSD (kein Wechselmedium), Sicherungs- oder Update-Stick, zu klein. Abgebrochen oder gescheitert: Die Karte ist leer und startet nicht – einfach nochmals schreiben. Protokoll «SD-Karte schreiben gescheitert» mit Code; Einzelheiten `journalctl -u festpos-karte`. |
| 2 Pis gleichzeitig tot | Bestellen gesperrt (Schutz vor doppelten Bons). Auf dem letzten Pi: `sudo festpos-notfall einzelbetrieb`. Die Statusanzeige zeigt dann „Notfall-Einzelbetrieb“. Zurückholen der anderen: siehe unten. |
| Alle Karten neu, `CLUSTER_SIZE=3` | Niemand gründet. Auf Pi A: `sudo festpos-notfall gruenden` (oder `CLUSTER_SIZE=1` und neu starten). Geht auch, wenn die Pi schon vergeblich versucht haben beizutreten. |
| Neuer Pi tritt nicht bei | Er versucht es alle paar Sekunden weiter. Netzwerkkabel, `CLUSTER_SIZE` und Buchstabe in `node.conf` prüfen; Stand mit `sudo festpos-notfall status`. |
| Statusanzeige: Drucker «unbekannt» auf den anderen Pi, Journal «Pi … antwortet nicht (Infos, Drucker)» | Die Pi holen Druckerstand und Infos direkt beim anderen (Port 8443, mit `CLUSTER_SECRET`). Netzwerk prüfen und ob `CLUSTER_SECRET` in `festpos.conf` auf allen Pi gleich ist. Bestellen und Drucken laufen weiter. |
| Schreibt der Pi zu viel auf die SD-Karte? | `sudo python3 /opt/festpos/tools/sd-messung.py 60` – zeigt pro Stunde, wer wie viel schreibt (liest nur). Im Leerlauf sollen kaum Raft-Einträge entstehen. |
| Datenbank startet nicht, Log: „ÄLTEREM Stand“ | Dieser Pi lief im Einzelbetrieb, und die anderen beiden laufen mit ihrem alten Stand. Er wartet absichtlich, damit nichts überschrieben wird. Auf den anderen beiden `sudo festpos-notfall neu-beitreten`, danach startet er von selbst. |

### Nach dem Notfall-Einzelbetrieb: die anderen Pi zurückholen

Der Pi im Einzelbetrieb hat die aktuellen Daten, die reparierten Pi einen alten Stand. Sie dürfen mit
diesem alten Stand nie eine Mehrheit bilden.

- **Einfachster Weg:** Die anderen Pi einschalten, **während der Pi im Einzelbetrieb läuft** (oder alle
  gleichzeitig). Sie fragen vor dem Start der Datenbank die anderen Pi, merken, dass ihr Stand veraltet ist,
  legen ihn beiseite und treten neu bei. Im Log steht dann „lief im Notfall-Einzelbetrieb … trete neu bei“.
- **Ganz sicher, egal in welcher Reihenfolge:** vorher am PC auf der Boot-Partition der reparierten Karten eine
  leere Datei **`festpos-neu-beitreten`** anlegen (`.txt` dahinter geht auch). Der Pi legt seine Datenbank beim
  Start beiseite, tritt neu bei und löscht die Datei.
- Oder auf dem laufenden reparierten Pi: `sudo festpos-notfall neu-beitreten`.

Sobald ein zweiter Pi wieder dabei ist, endet der Einzelbetrieb von selbst. Beiseite gelegte Datenbanken
liegen unter `/var/lib/festpos/rqlite.verworfen-nr…` (die drei neusten bleiben, ältere werden gelöscht).
Das gilt auch, wenn eine Karte ihren Buchstaben wechselt: Ihre alte Datenbank wird nicht gelöscht, nur beiseite gelegt.

---

## Benutzer und Rollen (Verwaltung)

Jede Person meldet sich mit **eigenem Namen und Passwort** an. Anlegen: Verwaltung › **Benutzer** (nur Admin).

| Rolle | darf |
|---|---|
| **Anzeige** | Übersicht, Statusanzeige, Druckaufträge (nachdrucken, erledigt), Bestellungen ansehen |
| **Festleitung** | dazu Preisliste (Produkte, Kategorien, MWST pro Produkt, Import/Export), Tische, Testmodus (Kasse & App › Bestell-App), unter **Geräte & Netz**: **Handhelds & PC** (hinzufügen in allen drei Arten, feste IP, **Rolle der Handhelds** inkl. Festleitung, „Im Einsatz“, sperren, Fully-Aktionen wie Neustart oder Vorlage anwenden), Pi (Uhrzeit prüfen und stellen, Handhelds und Pi neu starten oder herunterfahren), Drucker, Netz & Internet (Router prüfen), Druckaufträge › offene Bons umleiten, **stornieren, Zahlart ändern, Tagesabschluss, Protokoll** |
| **Admin** | alles, auch Benutzer, Geräte entsperren und löschen, Einstellungen (Anlass, Kasse, Bestell-App, **Geräte**: Einrichtungs-QR, FestPOS-WLAN, Fully-Passwort, Vorlage, Gültigkeit), Internet (Schalter, Verbindung), Netzwerk und Router einrichten, Wartung (Update, Sicherung, Zurücksetzen) |

**Eigene Rollen** (Verwaltung › Benutzer › Rollen › **+ Rolle**, nur Admin): Name und Häkchen pro Recht – Bestellungen
ansehen, Druckaufträge, Bestellungen ändern (Storno, Zahlart, Quittung), offene Tische, Tagesabschluss (auch Bestellungen
als CSV), Protokoll, Produkte, Ausverkauft und Restmenge, Tische, Drucker, Handhelds und Fully, Pi und Uhrzeit,
Testmodus. Beispiel «Küche»: Druckaufträge, Ausverkauft und Restmenge, Drucker. Die Rolle danach beim Benutzer wählen;
eine Änderung der Rechte gilt nach spätestens 5 Sekunden, auch für schon angemeldete Personen. Einstellungen, Benutzer
und Rollen, Sicherung und Update darf nur die Rolle **Admin**. Eine Rolle, die noch jemand hat, lässt sich nicht löschen.

**Schutz vor dem Aussperren:** Anzeige, Festleitung und Admin sind fest (nicht änderbar, nicht löschbar). Der letzte
aktive Admin-Benutzer lässt sich weder sperren noch herabstufen oder löschen, und niemand ändert seine eigene Rolle.
Der Notfall-Login bleibt immer Admin.

**Verwaltung nur mit Gerätezertifikat** (Benutzer › **Zugriff**, nur Admin): eingeschaltet geht die Verwaltung –
auch die Anmeldung – nur noch von Geräten mit gültigem Gerätezertifikat; das Passwort braucht es weiterhin.
1. Für den PC ein Zertifikat holen: Geräte & Netz › Handhelds & PC › **Gerät hinzufügen › Nur Zugang (ohne Fully)**,
   Name z.B. „PC Kasse“, und die `.p12`-Datei mit dem 6-stelligen Kennwort im Browser bzw. in Windows
   (Doppelklick › „Aktueller Benutzer“) installieren. Handhelds haben ihr Zertifikat schon.
2. Verwaltung mit diesem Browser neu öffnen (der Browser fragt nach dem Zertifikat – „PC Kasse“ wählen). Unter
   Benutzer › Zugriff steht dann „Diese Verbindung: Gerätezertifikat «PC Kasse» – gültig“.
3. **Nur mit Gerätezertifikat** einschalten. Ohne Zertifikat lässt es sich nicht einschalten (kein Aussperren).
Ein gesperrtes oder abgelaufenes Zertifikat zählt nicht. Die Statusanzeige bleibt ohne Zertifikat erreichbar.
**Browser fragt nicht nach dem Zertifikat:** Port 8443 fragt bei jeder neuen Verbindung danach. Der Browser zeigt die
Auswahl aber nur, wenn ein passendes Zertifikat installiert ist (von der Geräte-CA dieses Fests), und merkt sich die
Wahl für diese Adresse, bis er ganz geschlossen wird – auch „Abbrechen“ oder „kein Zertifikat“. Ebenso merkt er sich
eine Seite, die er geöffnet hat, bevor das Zertifikat installiert war. Dann alle Fenster des Browsers schliessen
(Chrome/Edge laufen evtl. im Hintergrund weiter: im Infobereich beenden) und die Verwaltung neu öffnen. Auf einer
anderen Adresse (z.B. `192.168.55.11` statt `.10`) fragt er neu. Am Handheld fragt nichts: Fully schickt sein Zertifikat
von selbst.
**Notweg**, falls kein Gerät mit Zertifikat mehr geht: am Pi selbst, z.B. per SSH-Tunnel
`ssh -L 8443:localhost:8443 pi@192.168.55.11` und dann `https://localhost:8443/admin.html` – dort ausschalten.

- **Notfall-Login:** Benutzer `admin` (oder Name leer lassen) mit dem `ADMIN_PASSWORD` aus `festpos.conf` ist
  immer Admin – auch wenn niemand mehr sein Passwort weiss. Dieses Passwort nur der Festleitung oder dem
  Technik-Verantwortlichen geben. Der Name «admin» kann nicht als normaler Benutzer angelegt werden.
  Mit dem Platzhalter `bitte-aendern` ist der Notfall-Login gesperrt (siehe Schritt 1).
- **Falsches Passwort:** Nach 10 Fehlversuchen in 5 Minuten sperrt FestPOS das Gerät 5 Minuten lang. Eine richtige
  Anmeldung löscht nur die eigenen Tippfehler, nicht die Fehlversuche mit anderen Namen. Dazu zählt FestPOS die
  Fehlversuche **pro Name** über alle Geräte zusammen: nach 30 in 5 Minuten kommt mit diesem Namen nur noch durch, wer
  sich von seinem Gerät aus in den letzten 12 Stunden schon richtig angemeldet hat (so sperrt jemand, der im WLAN
  Passwörter durchprobiert und dabei die Adresse wechselt, die Festleitung an ihrem PC nicht aus). Wer ganz sicher
  gehen will, lässt die Verwaltung mit `ADMIN_FROM` in `festpos.conf` nur vom Admin-PC aus zu (Schritt 1).
- **Anmeldung im Browser-Dialog (Basic Auth)**, z.B. für die einfache Statusanzeige auf einem alten iPad: damit lässt
  sich nur **ansehen**. Ändern geht im Browser nur aus der Verwaltung selbst (mit ihrer eigenen Anmeldung); eine
  Anfrage, die eine andere Webseite im Namen des angemeldeten Browsers schickt, weist der Pi ab. `curl` und eigene
  Skripte mit Name und Passwort gehen weiterhin.
- Passwörter: mindestens 8 Zeichen. Jede Person ändert ihr eigenes unten links: auf den eigenen Namen klicken ›
  **Passwort ändern**.
- **Aktiv** ausschalten sperrt einen Benutzer sofort, auch eine offene Anmeldung endet. Ein neues Passwort
  beendet ebenfalls alle offenen Anmeldungen dieser Person. **Überall abmelden** (unten links im Menü beim Namen) beendet alle
  Anmeldungen des eigenen Kontos auf allen Geräten, z.B. wenn ein Browser offen liegen geblieben ist.
- Wer nur einen Teil darf, sieht links nur diese Seiten und Reiter. Der Server prüft die Rechte bei jeder Anfrage selbst.
- Gespeichert wird nur ein Hash (scrypt), nie das Passwort. Die Benutzer sind in der Sicherung enthalten;
  nach dem Einspielen einer Sicherung gelten deren Benutzer und alle melden sich neu an.

## Kassenbuch: Zahlart, Storno, Tagesabschluss, MWST, Protokoll

Dieser Abschnitt beschreibt, wie FestPOS Bestellungen und Geld festhält (Verfahrensdokumentation). FestPOS ist
**keine zertifizierte Registrierkasse** und wickelt keine Zahlungen ab: Karte und TWINT laufen weiter über die
eigenen Geräte (z.B. SumUp, TWINT-QR). FestPOS hält fest, was bestellt, storniert und wie bezahlt wurde, und
gleicht das beim Tagesabschluss mit dem gezählten Bargeld und den Abrechnungen ab.

**Was gespeichert wird**
- Jede Bestellung mit fortlaufender Nummer, Zeit, Tisch, Handheld, Positionen, Preisen (zum Zeitpunkt der Bestellung),
  Zahlart (falls erfasst) und MWST-Satz (falls eingeschaltet). Bestellungen werden **nie geändert oder gelöscht**,
  solange sie nicht in einem Tagesabschluss sind (Ausnahme: Übungsbestellungen des Testmodus).
- Jede Bestellung und jedes Storno bekommt beim Anlegen eine **Prüfsumme** (HMAC-SHA256 mit einem Schlüssel aus
  `CLUSTER_SECRET`, der nur auf den Pi liegt – nicht in der Datenbank; in der Sicherung nur verschlüsselt). Wer nachträglich
  in der Datenbank einen Betrag oder eine Menge ändert, fällt beim Tagesabschluss auf. Bei **Quittungen** deckt die
  Prüfsumme seit T41 alles ab, was gedruckt wird: auch Empfänger (Name und Adresse), Zahlart, MWST-Nummer, Kopf- und
  Fusszeilen und die Beträge pro Position. Ältere Quittungen werden weiter nach der früheren Regel geprüft.
- Steht bei `CLUSTER_SECRET` der Platzhalter, leitet FestPOS den Schlüssel aus dem Server-Zertifikat ab (Übersicht
  zeigt einen Hinweis). Damit ein neues Zertifikat die Prüfsummen nicht unprüfbar macht, schreiben `make-certs.sh`,
  `festpos-zertifikate` und `festpos-zuruecksetzen` das bisherige Geheimnis vorher in `festpos.conf` (bei der
  Ersteinrichtung ein zufälliges). **Bei 3 Pi** danach dieselbe `festpos.conf` auf alle Karten.

**Zahlart an der Kasse** – Verwaltung › **Kasse** (nur Admin):
- *Nicht erfassen* – Standard. Für die Bedienung ändert sich nichts; alles zählt als Bargeld.
- *Freiwillig antippen* – in der Kasse erscheinen **Bar / Karte / TWINT**; antippen ist freiwillig.
- *Pflicht vor der nächsten Bestellung* – „Nächste Bestellung“ geht erst mit Zahlart.
- Die Zahlart geht mit der Bestellung mit oder wird nachgereicht (auch nach einem Funkloch). Bis zum
  Tagesabschluss lässt sie sich am Handheld (Verlauf › Kasse) und in der Verwaltung › **Bestellungen** ändern;
  Änderungen in der Verwaltung stehen im Protokoll.

**Abrechnen am Tisch** – Verwaltung › Kasse & App › Kasse (nur Admin):
- *Sofort* (Standard): nach jeder Bestellung wird kassiert.
- *Tisch offen lassen*: die Bestellungen sammeln sich am Tisch, abgerechnet wird am Schluss. Nach «Senden» zeigt das
  Handheld unter dem Bon «Vorher: 2 Bestellungen seit 18:40 · 45.00» und gross **Total offen Tisch 12** – mit der eben
  gesendeten Bestellung; «Alle Positionen anzeigen» klappt alles am Tisch auf. Grün «Nächste Bestellung», daneben
  «Jetzt abrechnen». In der Tischwahl stehen die offenen Tische über dem grünen Knopf in der Leiste **Offen** (nach
  links und rechts wischen; eigene grün mit Betrag, andere gestrichelt mit Name, lange nichts bestellt orange);
  **Alle ›** zeigt sie als Liste – auch ohne Tischplan. Wählt jemand einen offenen Tisch (Nummer, Leiste, Liste oder
  Plan), fragt das Handheld: grün **Weiter bestellen** (die Bestellung kommt auf
  diesen Tisch) oder **Abrechnen · CHF …**. Abgerechnet wird mit der gewohnten Kasse über alle Bestellungen
  (Rückgeld, Teilen, Einzeln – jede Person, jede Teilzahlung geht sofort an den Server), dann **Bezahlt · Tisch
  schliessen**. Abrechnen geht nur mit Verbindung zum Server. Zahlen zwei Handhelds gleichzeitig am selben Tisch, nimmt der Server nie
  mehr als offen an; kommt beim Schliessen eben eine neue Bestellung oder Zahlung dazu, meldet das Handheld «Am Tisch
  hat sich eben etwas geändert» – neu laden und nochmals prüfen (T40). «Nicht bezahlt» storniert alles in einem
  Schritt: kommt dabei eine Zahlung oder Bestellung dazu, wird nichts storniert (T43).
- **Wer darf einen offenen Tisch abrechnen:** nur das Handheld, das ihn eröffnet hat / dazu Handhelds mit der Rolle
  Festleitung / jedes Handheld. Bestellen darf jedes Handheld an jedem Tisch. Der Tisch gehört dem Gerät (Geräte-ID; bleibt
  beim Erneuern des Zertifikats).
- **Hinweis «lange nichts bestellt»** nach 30/45/60/90 Minuten (oder nie): im Tischplan und in der Verwaltung orange.
- **Verwaltung › Betrieb › Offene Tische** (ab Festleitung): alle offenen Tische mit Handheld, seit wann, letzte
  Bestellung, bezahlt, offen. **Übergeben** an ein anderes Handheld (Akku leer, Schichtwechsel), **Abrechnen** (der Rest
  gilt als bezahlt mit der gewählten Zahlart) oder **Nicht bezahlt** (alles Offene als Storno «Nicht bezahlt», ohne
  Storno-Bons – nur ohne Zahlung; sonst einzelne Positionen unter Bestellungen oder am Handheld beim Abrechnen
  stornieren und den Rest abrechnen).
- **Tagesabschluss:** geht erst, wenn kein Tisch mehr offen ist und keine Kartenzahlung (NFC) mehr offen ist (T45; die
  Seite listet offene Kartenzahlungen mit **Abbrechen** – nur, wenn sie in der Payrexx-App nicht bezahlt sind). Wird während des
  Abschlusses ein Tisch eröffnet, bricht der Abschluss ab, T40). Der Umsatz zählt wie
  bisher bei der Bestellung; die Zahlart der Bestellungen eines Tisches wird nach seinen Zahlungen aufgeteilt
  (z.B. 5.00 bar und 12.00 Karte; pro Bedienung nie negativ, Summen auf den Rappen genau). Jede Zahlung steht in der CSV (`typ` = `tischzahlung`) und im Protokoll.
- **Quittung** beim Abrechnen: über den ganzen Tisch (alle Bestellungen, «Bestellungen #0001, #0003») oder pro Person
  (Einzeln). Jede Position wird pro Tisch nur einmal im Original quittiert – auch wenn die Verwaltung bei einer der
  Bestellungen «Quittung drucken» wählt (dann kommt, was noch offen ist, sonst eine KOPIE). Über CHF 400 mit MWST nur in
  der Verwaltung mit Empfänger (bei einer Bestellung des Tisches).
- **Karte/TWINT mit Payrexx (NFC)** geht auch an einem offenen Tisch (ganz, pro Person oder Teilen): der Server bucht
  die Zahlung selbst an den Tisch (Zahlart Payrexx, höchstens was offen ist). Eine Payrexx-Zahlung lässt sich am Handheld
  nicht rückgängig machen.

**Trinkgeld** – Verwaltung › **Kasse** › „Trinkgeld an der Kasse erfassen“ (nur Admin, Standard aus):
- In der Bestell-App erscheint „+ Trinkgeld“ (Bedienung: siehe oben, Kasse Punkt 5), bei der QR-Bestellung am Tisch
  wählt der Gast im Warenkorb „Kein Trinkgeld / Aufrunden / 5 % / 10 %“.
- Trinkgeld ist **kein Umsatz** und hat **keine MWST** (bitte mit der Treuhand bestätigen); es steht **nicht auf der
  Quittung**. Ein Storno ändert das Trinkgeld nicht; Übungsbestellungen zählen nie.
- Mit Payrexx (NFC oder QR) ist es im Payrexx-Betrag enthalten. Bei der QR-Bestellung geht es nur zurück, wenn die
  ganze Bestellung zurückgeht (Storno von allem, nicht gedruckt, ausverkauft).
- **Tagesabschluss:** eigene Karte **Trinkgeld** (bar / Karte / TWINT / Payrexx, **Total fürs Personal**), Spalte
  „Trinkgeld“ pro Handheld und Bedienung, eigene Zeilen im PDF und in der CSV (`typ` = `trinkgeld`).

**Storno** – eine Gegenbuchung, nie ein Löschen:
- Die Bestellung bleibt sichtbar, der stornierte Betrag wird abgezogen. Pro Storno werden Positionen, Menge,
  Betrag, Grund (Falsch bestellt, Nicht bezahlt, Reklamation, Anderer Grund + Bemerkung), Zeit und wer storniert
  hat gespeichert – mit eigener Prüfsumme. Stornieren zwei Geräte dieselbe Bestellung im selben Moment, rechnet das
  zweite mit dem neuen Stand: es wird nie mehr storniert als bestellt.
- An jeder betroffenen Station druckt ein **Storno-Bon** (gross „STORNO“, Tisch, die stornierten Positionen
  **durchgestrichen**, Grund, wer storniert hat). Ist die ganze
  Bestellung storniert und ein Bon noch gar nicht gedruckt, wird er zurückgehalten statt gedruckt.
- Ein **Nachdruck** – oder ein Bon, der erst nach einem Teil-Storno aus dem Drucker kommt – zeigt oben nur, was
  noch gilt, und darunter unter „Storniert“ das Stornierte durchgestrichen. Bei Einzelbons steht „1 von 3
  storniert“ (welches Stück, weiss die Bedienung).
- **Wer darf:** in der Verwaltung › **Bestellungen** ab Festleitung. Am Handheld nur mit der Rolle
  **Bedienung + Storno** oder **Festleitung** (dazu: ausverkauft melden und gelb «Papier fast leer» in der Statuszeile,
  einmal mit Hinweis und Vibration – die Bedienung sieht erst «Papier leer»): Verwaltung › **Handhelds** › Spalte **Rolle** (ab Festleitung; gilt für den Namen im
  Gerätezertifikat, auch nach dem Erneuern; wirkt spätestens nach 20 Sekunden). Standard ist **Bedienung**
  (kein Storno). Ein Handheld storniert nur Bestellungen aus seinem Verlauf und offene Tische, die es abrechnen darf.
- **Rolle „Nur Statusanzeige“** (7.11.): Das Gerät öffnet statt der Bestell-App gleich die **Statusanzeige** (auch nach
  dem Neustart von Fully) und darf nicht bestellen – z.B. ein Handheld an der Theke oder in der Küche, das nur Drucker und
  Bestellungen zeigt. Ohne Anmeldung, nur anschauen. Wird die Rolle wieder eine andere, kehrt es innert etwa
  20 Sekunden von selbst in die Bestell-App zurück.
- Nach dem Tagesabschluss ist kein Storno mehr möglich – Korrekturen dann im nächsten Zeitraum als neue Buchung.
  Kommt ein Storno, während der Abschluss gerade rechnet, rechnet er neu und nimmt es mit; ein Storno, das erst
  nach dem Abschluss ankommt, wird abgelehnt.

**Tagesabschluss** – Verwaltung › **Tagesabschluss** (ab Festleitung), am Ende jedes Anlasstages:
1. Oben stehen alle Bestellungen seit dem letzten Abschluss: Anzahl und Nummern, bestellt, Storno, Umsatz nach
   Storno, nach Zahlart, pro Gerät und Bedienung („XCover101 (Anna)“ – für die Abrechnung mit dem Personal),
   MWST (falls eingeschaltet) und die **Prüfung** (Prüfsummen, Lücken in der
   Nummernfolge – Lücken entstehen z.B. durch gelöschte Übungsbestellungen).
2. **Kassensturz:** Wechselgeld zu Beginn und gezähltes Bargeld eintragen. Karte und TWINT sind mit den an der
   Kasse erfassten Beträgen vorausgefüllt (mit Trinkgeld, so wie es auf deren Abrechnung steht) – mit den
   Abrechnungen von SumUp und TWINT vergleichen und korrigieren. Mit Trinkgeld zusätzlich **„Trinkgeld ausbezahlt
   (bar)“**: was am Abend bar aus der Kasse ans Personal geht (auch das Trinkgeld, das per Karte/TWINT/Payrexx kam).
   FestPOS rechnet **Bargeld laut Kasse = Wechselgeld + Umsatz + Trinkgeld − Karte − TWINT − Andere − Payrexx −
   Trinkgeld ausbezahlt** und die **Differenz**. Eine Differenz in der Bemerkung erklären.
3. **Tagesabschluss erstellen.** Danach sind diese Bestellungen und Stornos abgeschlossen (nicht mehr änderbar).
   Der Abschluss bekommt eine Nummer und eine Prüfsumme, die mit dem vorherigen Abschluss verkettet ist. Die
   Prüfsumme ist mit dem Schlüssel aus `CLUSTER_SECRET` gesichert (`h2:…`): Auch wer die Datenbank ersetzt (z.B. eine
   veränderte Sicherung einspielt), kann einen Abschluss nicht unbemerkt neu rechnen.
4. **PDF** (mit Feldern für Unterschriften: Kasse gezählt / Kontrolliert) und **CSV** (alle Positionen, Stornos als
   Gegenbuchung mit negativer Menge) herunterladen und zusammen mit den Abrechnungen von SumUp und TWINT und den
   Zählprotokollen aufbewahren (in der Schweiz in der Regel 10 Jahre; im Zweifel beim Kassier nachfragen).
- **Prüfen** neben einem Abschluss rechnet seine Prüfsumme neu und prüft die Kette: „unverändert“ oder eine Warnung.
  Orange: „ältere Prüfsumme ohne Schlüssel“ (Abschlüsse aus früheren Versionen – stimmen, sichern aber
  weniger) oder „mit früherem Schlüssel“ (`CLUSTER_SECRET` wurde geändert – nicht falsch, aber hier nicht prüfbar).
- Übungsbestellungen zählen nie mit. Abschlüsse bleiben auch bei „Alle Bestellungen löschen“ erhalten und sind in
  der Sicherung. „Alle Bestellungen löschen“ löscht auch Trinkgeld und Payrexx-Zahlungen der gelöschten Bestellungen
  (die Nummern beginnen danach neu; alles steht im Abschluss).

**MWST** (nur für Vereine mit MWST-Pflicht) – Verwaltung › Kasse & App › Kasse › **MWST** (nur Admin):
- **MWST ausweisen** einschalten, MWST-Nummer eintragen. Vorgegeben sind Normalsatz 8.1 %, reduzierter Satz 2.6 %
  und Beherbergung 3.8 % (gültig ab 1.1.2024).
- Den Satz legt die Kategorie fest, ein Produkt kann abweichen: Verwaltung › Produkte, Spalte **MWST**.
- Ändert ein Satz, den neuen mit **Gültig ab** im Voraus eintragen. Jede Bestellung speichert den Satz, der an
  ihrem Tag galt – alte Bestellungen bleiben richtig. Löschen lassen sich nur künftige Sätze.
- Der Tagesabschluss weist pro Satz Brutto, MWST und Netto aus. Ausgeschaltet speichert FestPOS keine Sätze.

**Bons & Quittung** – Verwaltung › Kasse & App › **Bons & Quittung** (nur Admin, T24). Oben wählen: **Quittung**
oder **Küchen- und Getränkebon**, dazu **Vorlage** und **Papier** (80 oder 58 mm – nur für die Vorschau; die Breite
gehört zum Drucker unter Geräte › Drucker). Darunter drei Spalten:
- **Bausteine** in Druckreihenfolge. Am **⋮⋮** ziehen (Maus oder Finger): die Zeile löst sich und schwebt, die anderen
  gleiten zur Seite, eine gestrichelte Lücke zeigt, wo sie landet; am oberen oder unteren Rand scrollt die Seite mit.
  Direkt in der Vorschau ziehen geht auch (grüne Linie zeigt den Zielort). Tastatur: Baustein wählen, **Alt+↑/↓**; am
  Handheld die Knöpfe **▲ ▼** – auch dabei gleiten die Zeilen an ihren neuen Platz. Das **Auge** schaltet ein und aus.
  Unten **+ Baustein**: Freitext, Linie, Leerraum, Bild (eigenes Bild, schwarzweiss gerastert, höchstens 3), QR-Code
  (z.B. Website oder Spende, höchstens 3), beim Küchenbon auch Datum und Zeit. Jeder Baustein hat «Abstand davor».
- **Vorschau** genau wie gedruckt (Beispieldaten, auch Ungespeichertes). Antippen wählt den Baustein (grün umrandet).
  Beim Küchenbon: **Vorschau mit Lieferzeit** Sofort / Pause / Schluss. **Probe drucken …** druckt das Beispiel direkt
  auf einen gewählten Drucker, oben «MUSTER» – es entsteht keine Quittung und keine Nummer.
  **Auf Vorlage zurücksetzen** stellt die Bausteine der Vorlage wieder her (Kopf und Fusszeile bleiben).
- **Eigenschaften** des gewählten Bausteins, z.B. Grösse (klein … riesig), Ausrichtung, fett, unterstrichen, weiss
  auf schwarz. Bei **Kopf** und **Fusszeile** der Quittung steht hier der Text (Verein und Adresse so, wie er im
  Geschäftsverkehr auftritt; Fusszeile z.B. „Vielen Dank!“). Beim **Logo** wird das Bon-Logo hochgeladen (PNG, JPG,
  WebP; am besten einfarbig mit klaren Linien – ohne eigenes Bon-Logo gilt das Logo der Verwaltung).
- Beim **Lieferzeitpunkt**: **Drucken bei** – ein Häkchen pro Lieferzeitpunkt (Produkte › Lieferzeiten). Standard:
  alle ausser „Sofort“ (PAUSE, SCHLUSS … fallen auf). Mit Häkchen bei „Sofort“ steht auch SOFORT auf dem
  Bon; ohne Häkchen bei „Pause“ steht PAUSE nicht drauf. Lange Namen werden umgebrochen, nicht abgeschnitten.
- Die Gestaltung der Küchen- und Getränkebons gilt für alle Drucker. Der **Storno-Bon** bleibt fest; er übernimmt nur
  Logo und Tischnummer.
- **PFLICHT**-Bausteine lassen sich verschieben und gestalten, aber nicht ausblenden. KOPIE/TEST steht immer zuoberst,
  die Prüfnummer immer zuletzt (beim Küchenbon TEST, NACHDRUCK, „Umgeleitet“ zuoberst, der Signalton am Schluss).
- Solange etwas nicht gespeichert ist, steht unten der schwarze Balken **Geändert – noch nicht gespeichert** mit
  **Verwerfen** und **Speichern**. Die Gestaltung wird in jeder Quittung festgehalten: eine KOPIE sieht aus wie das
  Original, auch nach einer späteren Änderung oder mit einem früheren Logo (dieses bleibt dafür gespeichert).
- Nach dem Update übernimmt FestPOS die bisherigen Einstellungen (Bon-Design und Kasse › Quittung) – die Bons sehen
  gleich aus. Die Abstände druckt der TM-T20III so, wie die Vorschau sie zeigt.

**Quittung für den Gast** – Verwaltung › Kasse & App › **Bons & Quittung** › **Quittung** (nur Admin):
- Oben den **Drucker für Quittungen** wählen (z.B. Getränkeausgabe). „Keine Quittungen“ blendet den Knopf in der Kasse
  aus. Kopf und Fusszeile stehen bei den Bausteinen **Kopf: Verein und Adresse** und **Fusszeile**.
- Auf der Quittung stehen: Kopf, MWST-Nummer (nur wenn MWST eingeschaltet), **Quittungsnummer** (fortlaufend),
  Datum und Uhrzeit, Tisch, Bestellnummer, Handheld und Bedienung, alle Positionen mit Menge und Preis, Total,
  „Bar gerundet“ (falls nötig), Zahlart (falls erfasst), bei MWST pro Satz Brutto und enthaltene MWST (Buchstaben
  A/B/C an den Positionen, „Preise inkl. MWST“), Fusszeile und eine kurze **Prüfnummer**.
- **Ohne MWST-Pflicht:** MWST ausgeschaltet lassen – die Quittung kommt dann ohne MWST-Angaben.
- Jede Quittung bekommt eine Nummer ohne Lücken; ihr Inhalt wird beim Ausstellen festgehalten und mit einer
  Prüfsumme gesichert (wie Bestellungen). Jede Position wird nur einmal im Original quittiert (bei „Einzeln“ pro
  Person) – auch wenn zwei Geräte im selben Moment quittieren. Wer nochmals druckt, bekommt dieselbe Quittung gross
  als **KOPIE** – auch das wird festgehalten.
  Stornierte Positionen werden nicht mehr quittiert; eine schon gedruckte Quittung bleibt, das Storno steht als
  Gegenbuchung im Kassenbuch.
- **Über CHF 400 mit MWST** müssen Name und Adresse des Gastes auf der Quittung stehen (Kassenzettel bis CHF 400
  brauchen sie nicht, Art. 57 MWSTV): Verwaltung › **Bestellungen** › Bestellung öffnen › **Empfänger** eintragen ›
  **Quittung drucken** – ohne Empfänger druckt die Verwaltung sie nicht. Das Handheld druckt eine solche Quittung gar
  nicht erst, sondern verweist auf die Verwaltung (bei „Einzeln“ zählt der Betrag pro Person).
- In der Verwaltung › Bestellungen zeigt jede Bestellung ihre Quittungen (Nummer, Zeit, wer, Kopien) mit
  **Kopie drucken**. Der **Tagesabschluss** (und sein PDF) nennt Anzahl, Nummern von–bis, Betrag, Kopien und Lücken.
  Übungsbestellungen aus dem Testmodus ergeben „TEST“-Quittungen ohne Nummer.
- In der Schweiz gibt es keine Pflicht zu einer zertifizierten Registrierkasse. Die Quittung enthält die Angaben
  eines Kassenzettels nach Art. 26 MWSTG; im Zweifel beim Kassier oder Treuhänder nachfragen.

**Protokoll** – Verwaltung › Tagesabschluss › Reiter **Protokoll**: wer wann was geändert hat (Preise und Produkte, Kategorien,
Einstellungen, Import, Sicherung eingespielt, Zahlart geändert, Stornos, Quittungen und Kopien, Abschlüsse, MWST,
Rollen der Handhelds, Benutzer). Passwörter und PINs stehen nie darin. Das Protokoll lässt sich in der Verwaltung nicht löschen.

**Wichtig für die Prüfsummen:** In `festpos.conf` ein eigenes, langes `CLUSTER_SECRET` eintragen und behalten
(steht dort nur der Platzhalter, nimmt FestPOS einen Schlüssel aus dem Server-Zertifikat – nach „Alles
zurücksetzen“ ist der dann anders). Mit einem anderen Schlüssel erstellte Buchungen zeigt der Abschluss als „mit
früherem Schlüssel“ an; sie sind nicht falsch, lassen sich aber nicht mehr prüfen. Darum vor einer Neuinstallation
oder „Alles zurücksetzen“ einen Tagesabschluss erstellen.

## Logo

Verwaltung › Kasse & App › Veranstalter › **Logo** (nur Admin): **Logo hochladen** (PNG, JPEG oder WebP; am besten PNG mit
durchsichtigem Hintergrund). Es erscheint statt des grünen FestPOS-Zeichens in der Bestell-App (Tischwahl und
PIN-Sperre), in der Verwaltung (links oben und bei der Anmeldung) und in der Statusanzeige. FestPOS verkleinert
es auf höchstens 600 × 240 Pixel und speichert es in der Datenbank – damit ist es auf allen Pi und in der
Sicherung, und die Handhelds haben es auch offline.

- **Weisser Hintergrund hinter dem Logo** (Standard: ein): die Statusanzeige und die Leiste der Verwaltung sind
  dunkel, ein dunkles Logo wäre dort unsichtbar. Für ein helles Logo (z.B. weisse Schrift) ausschalten.
- **Anderes Logo hochladen** ersetzt es, **Logo entfernen** bringt das grüne Zeichen zurück.
- Auf die Bons wird das Logo (noch) nicht gedruckt.

## Herunterfahren und Neu starten

Verwaltung › **Geräte & Netz › Pi** (ab Festleitung) – alles auf einer Seite: oben die **Pi** mit Rolle, Uhrzeit und
Abweichung, Temperatur, Bildschirm und FestPOS-Version, darunter Uhrzeit, «Alle Pi», Erweitern und Ersatz, dann die
Zertifikate (zugeklappt, Zustand im Titel). Die Netzwerkgeräte stehen unter Netz & Internet. **Pi antippen** öffnet seine Unterseite: Zustand
(Modell, System, Version, Laufzeit, Temperatur, Speicher, RAM, Dienste, Uhrzeit, RTC-Batterie, Sicherung), **Bildschirm
am Pi** und **Pi … neu starten / herunterfahren** nur diesen Pi; «‹ Alle Pi» führt zurück.

- **System neu starten / herunterfahren** (mit 3 Pi: **Alle Pi …**): alle Pi gehen gemeinsam nach
  30 Sekunden – gemeinsam, damit keiner allein ohne Mehrheit übrig bleibt. Einen einzelnen Pi (z.B. nur Pi B) neu
  starten: Pi antippen › **Pi B neu starten**; die anderen arbeiten weiter.
- **Meldung vorher:** Sofort nach dem Klick zeigen alle Handhelds (innert 10 Sekunden), die Statusanzeige und die
  Verwaltung oben einen roten Balken mit Countdown: „System wird in 0:25 heruntergefahren“ bzw. „System startet in
  0:25 neu – in etwa 2 Min. wieder bereit“. Die Bestell-App bleibt bedienbar: Bestellungen gehen bis zuletzt
  hinaus, danach wie immer in den Ausgang und werden nach dem Start gesendet. Nach dem Neustart verschwindet der
  Balken von selbst. **Nur ein Pi von mehreren** (z.B. nur Pi B): Statusanzeige und Verwaltung zeigen „Pi B startet
  in 0:25 neu – die anderen Pi laufen weiter“; die Handhelds zeigen nichts, denn Bestellen geht weiter (ausser sie
  werden dabei über Fully mit ausgeschaltet).
- **Abbrechen:** In der Verwaltung steht im roten Balken **Abbrechen** (ab Festleitung), solange noch mehr als
  12 Sekunden übrig sind. Dann läuft alles weiter, und die Handhelds werden nicht ausgeschaltet.
- Im Fenster davor lässt sich wählen, was mit den **Handhelds** passieren soll (nur, wenn Fully eingerichtet ist,
  siehe unten):
  - beim Neustart: **App auf den Handhelds neu starten**, **Handhelds neu starten** oder **Handhelds nicht anfassen**
  - beim Herunterfahren: **Handhelds ausschalten**, **Bildschirm der Handhelds ausschalten** oder **Handhelds nicht anfassen**
- Die Handhelds zeigen zuerst die Meldung und bekommen den Befehl 5 Sekunden vor den Pi (solange der Server noch
  läuft); ob er angekommen ist, steht im Log des Pi. Ausgeschaltete Handhelds wieder einschalten: am Handheld die
  Einschalttaste lang drücken.
- **Damit Fully die XCover neu starten und ausschalten darf, müssen in Fully die Samsung-KNOX-Funktionen
  eingeschaltet sein** (Einstellung „Enable KNOX Features“; laut Fully braucht die Aktivierung der KNOX-Lizenz
  einmal Internet). Fehlt das, antwortet Fully „Missing device owner, system or root rights“, und FestPOS zeigt
  „Fully darf das auf diesem Handheld nicht. Bei Samsung in Fully die KNOX-Funktionen einschalten“ – das Handheld
  läuft einfach weiter. Vor dem Anlass auf jedem Handheld einmal „Handheld neu starten“ ausprobieren.
- Nach dem Herunterfahren den Strom erst ziehen, wenn die grüne Lampe am Pi nicht mehr blinkt. Wieder
  einschalten: Strom kurz ziehen und wieder einstecken. Bestellungen und Bons bleiben gespeichert.
- Ausgeführt wird das vom Root-Helfer `festpos-display` (die App selbst hat keine Root-Rechte). Ein Auftrag,
  der älter als 10 Minuten ist (z.B. weil ein Pi gerade aus war), wird verworfen.

## Statusanzeige (Bildschirm am Pi)

Oben die Gesamtlampe: grün „Alles in Ordnung“, gelb „Bitte prüfen“, rot „N Störungen“ mit dem Grund.
Darunter:

- **Drucker** als grosse Kacheln: bereit / gestört / Papier leer / Deckel offen / Bons prüfen / Papier fast leer.
  Papier und Deckel fragt nur der druckende Pi ab (sonst stören sich Abfrage und Bon am Drucker); die anderen Pi
  holen seinen Stand alle 10 s direkt bei ihm ab (nicht über die Datenbank, das schont die SD-Karten). Antwortet er
  nicht, zeigen sie den Stand als unbekannt.
- **Letzte Bestellungen** mit Zeit, Platz, Handheld, Betrag und pro Drucker ein Lämpchen
  (grün gedruckt, gelb druckt, rot wartet/prüfen), eine Zeile pro Bestellung, wo der Platz reicht, und so viele,
  wie ganz hineinpassen. **Eine Bestellung antippen** öffnet gleich diese Bestellung: Positionen, Zahlart, Trinkgeld,
  Stornos und die Bons pro Drucker (gedruckt um …, wartet, prüfen, Nachdruck). **„‹ Alle Bestellungen“** darin oder die
  Überschrift («Alle ›») zeigt **alle Bestellungen seit dem Tagesabschluss** mit Anzahl und Summe, neueste oben, zum
  Scrollen («Weitere laden» holt die nächsten 50). Nur anschauen – auch am Pi-Bildschirm und auf Handhelds mit «Nur Statusanzeige».
- **Handhelds** mit Name und IP, so wie unter Verwaltung › **Handhelds** eingetragen (nur die im Einsatz),
  daneben klein der Name, der in der App eingestellt ist und auf den Bestellungen steht (z.B. „XCover 2 Anna“):
  online (App von dieser IP aktiv), „erreichbar, App zu“ (antwortet auf Ping), „nicht erreichbar“, oder
  „N Bestellungen wartet“. Dafür im Access Point feste IPs für die Handhelds vergeben (DHCP-Reservierung).
  Handhelds im Standby antworten manchmal nicht auf Ping, das ist normal.
  Dazu der **Akkustand** mit ⚡, wenn das Handheld lädt. Ohne Ladekabel wird es gelb ab 20 %, rot ab 10 %
  (dann auch oben in der Gesamtlampe). Wird der Platz knapp, fällt zuerst die IP weg.
  **Marke „TEST“:** Das Handheld ist im Testmodus – Handheld antippen › **Testmodus beenden** (siehe «Testmodus zum Üben»).
  **Orange „Kiosk aus“:** Fully meldet, dass der Kiosk-Modus auf diesem Handheld aus oder entsperrt ist – oben steht
  dann „Bitte prüfen: XCover …: Kiosk-Modus aus“. Wieder einschalten: Handheld antippen › **Kiosk-Modus ein**
  (ab Festleitung). Geht nur mit eingerichtetem Fully (siehe unten).
  **Orange „ohne Lizenz“:** Fully läuft ohne Lizenz – Einzellizenz im Lizenzportal von Fully der Geräte-ID zuweisen
  (Verwaltung › Geräte & Netz › Handhelds & PC › Gerät › **Bearbeiten** zeigt Geräte-ID und Link), danach Fully neu starten.
  Beim Antippen eines Handhelds fragt die Statusanzeige Fully gleich neu ab (sonst bis 30 Sekunden alt); ebenso die
  Geräteansicht in der Verwaltung beim Öffnen.
- **Netzwerk:** Switch, Access Point und Mobilfunk-Router – nur die Geräte im Einsatz; ist keines im Einsatz,
  fällt die Karte weg. Ist unter Verwaltung › Geräte & Netz › Netz & Internet **Dauerhaft verbunden** an, steht dort die Zeile **Internet**:
  erreichbar oder nicht, der Weg (Router, WLAN oder USB-Adapter, mit dem Pi, der das Gerät hat) und ob der Tunnel der
  QR-Bestellung läuft. Antippen zeigt pro Pi «verbunden seit …», «über Pi A», «gibt weiter» und wofür das Internet
  gebraucht wird. **Rot** ist sie nur, wenn Karte/TWINT oder QR-Bestellung eingeschaltet sind (sonst gelb); geht der
  Weg über den Mobilfunk-Router und meldet der schon rot, bleibt sie gelb – die Störung steht nur einmal oben. Die
  Handhelds melden dann oben rot «Internet ohne Verbindung».
- **Server:** eine Zeile pro Pi: welcher druckt und wann zuletzt auf den USB-Stick gesichert wurde; Temperatur und
  freier Speicher nur, wenn es warm bzw. knapp wird (genaue Werte und Sicherung beim Antippen). Passt die Zeile nicht
  neben den Namen, steht sie darunter. Läuft auf einem Pi ein Dienst
  nicht (Datenbank, Firewall, Uhrzeit …), steht das bei diesem Pi und oben („Pi C: Firewall läuft nicht“).
  Antippen zeigt die Details des Pi. Mit mehreren Pi steht darunter, ob der Ausfallschutz voll da ist; im
  Einzelbetrieb steht unten „Einzelbetrieb“. Temperatur, Speicher und Laufzeit der anderen Pi kommen direkt von ihnen
  (alle 10 s); ist ein Pi aus, steht sein letzter Stand mit «Stand vor …» da.
- **Netzwerk und Server**: Switch und Access Point erreichbar, Dienste (Datenbank, Weiterleitung, Uhrzeit,
  Firewall, Ausfallschutz), Temperatur und freier Speicher.

**Ansichten** – die Anzeige passt sich dem Bildschirm an:

| Bildschirm | Ansicht |
|---|---|
| 3,5"-Display am Pi (480 × 320), Handheld | kompakt, mit dem Finger scrollbar |
| Bildschirm am Pi, quer (7", 800 × 480) | zwei Spalten, gross – von weitem lesbar |
| grosser Bildschirm / TV am Pi | drei Spalten (Drucker, Bestellungen, Handhelds), gross |
| Tablet quer | drei Spalten, etwas kleiner |
| PC-Bildschirm | drei Spalten, am meisten auf einmal |
| hochkant (Tablet, Touch Display 2) | Drucker, darunter Handhelds und Netzwerk, dann die Bestellungen |

Erkannt wird das von selbst (am Pi selbst „Pi“, mit Maus „PC“, sonst „Tablet“). Von Hand: `status.html?ansicht=pc`
(oder `tablet`, `pi`, `klein`) – der Browser merkt sich das; `?ansicht=auto` schaltet zurück.

**Mauszeiger:** Im Browser erscheint er, sobald die Maus bewegt wird, und verschwindet nach 3 Sekunden Ruhe.
Am Pi: Verwaltung › Geräte & Netz › Pi › Pi antippen › Bildschirm am Pi › **Mauszeiger** – „automatisch“ zeigt ihn nur am HDMI-Monitor
(an Touch-Displays würde der Pfeil bei jeder Berührung mitspringen), oder immer anzeigen / ausblenden.

Welche Geräte geprüft werden: Handhelds unter Verwaltung › Geräte & Netz › **Handhelds & PC** (mit fester IP), Switch und
Access Point unter Verwaltung › Geräte & Netz › **Netz & Internet** › Netzwerk auf der Statusanzeige – pro Gerät Name, IP und optional ein Port. Ohne Port wird angepingt, mit Port genau
dieser Port geprüft. Mit dem Schalter **Im Einsatz** lässt sich ein Gerät für einen Anlass ausblenden (bleibt
eingetragen, wird weder geprüft noch angezeigt); ebenso der Mobilfunk-Router und ausgeschaltete Drucker. Aktualisiert sich alle 3 Sekunden und nach einem Update von selbst.

**Alte Tablets (z.B. iPad mini mit iOS 9):** Die normale Statusanzeige braucht einen neueren Browser und bleibt dort
leer. Für solche Geräte gibt es die **einfache Statusanzeige**: `https://<Pi>:8443/status-einfach` (bzw. die Adresse,
unter der die Verwaltung läuft). Safari fragt nach Benutzer und Passwort der Verwaltung (Rolle **Anzeige** genügt) –
«Kennwort sichern» erspart das erneute Eintippen. Die Seite zeigt Gesamtlampe, Drucker, letzte Bestellungen,
Handhelds, Netzwerk und Server und aktualisiert sich alle 10 Sekunden, ohne dass der Bildschirm kurz weiss aufblitzt.
Erreicht das iPad den Server nicht, erscheint oben rot «Keine Verbindung zum Server – Anzeige nicht aktuell» und die
Anzeige wird abgedunkelt; sobald der Server wieder antwortet, verschwindet der Hinweis von selbst. Nach einem Update
lädt sich die Seite selbst ganz neu. Nur anschauen: keine
Knöpfe, keine Details beim Antippen, kein Internet- und Update-Stand. Die Zertifizierungsstelle der Pi muss auf dem
iPad als Profil installiert sein (wie bei den anderen Geräten). Für den Vollbildmodus: in Safari **Teilen ›
Zum Home-Bildschirm**, dann von dort öffnen.

**Einrichten in der Verwaltung:** Geräte & Netz › Pi › **Bildschirm am Pi**. Pro Pi:

- **Bildschirm:** Kein Bildschirm, HDMI-Monitor, offizielles 7"-Display, Touch Display 2 (7"/5") oder
  3,5"-Display an der Stiftleiste (Joy-IT RB-TFT3.5 / Waveshare 3.5" A). „Kein Bildschirm“ schaltet die
  Anzeige aus. Ein neuer Typ braucht einmal **Pi neu starten** (Knopf erscheint).
- **Drehung** (0/90/180/270°) und **Grösse** (nur nötig, wenn die Anzeige über den Rand geht – beim
  3,5"-Display 320 × 480).
- **Touch einrichten:** am Pi erscheinen nacheinander drei gelbe Kreise, jeweils genau in die Mitte tippen.
  Danach scrollt die Anzeige mit dem Finger; nach 30 Sekunden springt sie wieder nach oben.

Die Programme dafür (`cage`, `chromium`) brauchen **einmal Internet**. Bei neuen Pi macht das `setup.sh`;
bei bestehenden einmal `sudo festpos-kiosk --vorbereiten` (oder `sudo bash ~/festpos/deploy/festpos-kiosk.sh`).
Die Treiberzeilen trägt der Pi selbst in einen eigenen Block in `/boot/firmware/config.txt` ein.

Am Pi selbst ohne Passwort. Am PC: Verwaltung › links oben **Statusanzeige** (nutzt die Anmeldung der Verwaltung). Der
Punkt beim Knopf zeigt den Zustand live: grün alles in Ordnung, gelb bitte prüfen (z.B. Papier fast leer), rot Störung
(z.B. Drucker oder Pi nicht erreichbar); darunter steht, was los ist (alle Meldungen: Maus auf den Knopf halten).
Bestell-Handhelds mit Gerätezertifikat sehen sie ohne Anmeldung (nur anschauen): Bestell-App › Einstellungen ›
**Statusanzeige**. Unten rechts führen **Bestell-App** und **Verwaltung** zurück – am Handheld und am PC, nie am
Bildschirm des Pi (dort gibt es kein Zurück). Bestellungen mit Storno tragen in der Liste ein rotes **STORNO**, der Betrag ist der nach dem Storno.
Am Handheld hochkant steht alles untereinander: Drucker, Handhelds, Netzwerk und Server, darunter die letzten Bestellungen
über die ganze Breite (mit Gerät und Bedienung, z.B. „XCover101 (Anna)“). Das kleine Display am Pi bleibt zweispaltig.

**Gerät antippen (oder anklicken):** Ein Handheld oder ein Pi in der Statusanzeige öffnet ein Fenster mit mehr Infos –
beim Handheld Name in der App, IP, Akku und mit Fully dazu Temperatur, Bildschirm, Kiosk-Modus, WLAN, Speicher,
Android-, Fully-Version und Modell; beim Pi Modell, Betriebssystem, Kernel, Laufzeit, Temperatur, Speicher, RAM
und FestPOS-Version. Wer aus der Verwaltung als **Festleitung oder Admin** kommt, sieht dort zusätzlich
**Seite neu laden** / **App neu starten** / **Handheld neu starten** / **Handheld ausschalten** / **Kiosk-Modus aus** bzw. **ein** (über Fully) bzw. **Pi neu starten** / **Pi herunterfahren** – jeweils
mit Rückfrage. Am Pi-Bildschirm ohne Anmeldung gibt es diese Knöpfe nicht. Das Fenster schliesst mit ✕,
Tippen daneben oder nach 90 Sekunden von selbst.

**Kiosk-Modus aus/ein:** „Kiosk-Modus aus“ entsperrt Fully auf diesem Handheld (Fully-Befehl `unlockKiosk`) – man
kommt dann ohne Kiosk-PIN aus der App, z.B. um am Handheld etwas einzustellen. „Kiosk-Modus ein“ sperrt wieder
(`lockKiosk`). Gleich danach fragt FestPOS das Handheld neu ab; in der Zeile Kiosk-Modus steht dann „aus (entsperrt)“
bzw. „an“, und es erscheint der jeweils andere Knopf. Die Einstellung „Kiosk-Modus“ in Fully selbst bleibt dabei
eingeschaltet; war sie ganz aus, schaltet „Kiosk-Modus ein“ sie zuerst wieder ein. Nach dem Einrichten am Handheld
nicht vergessen, wieder **Kiosk-Modus ein** zu tippen.

**Fully in der Verwaltung** (Geräte & Netz › Handhelds & PC › Handheld › Karte „Fully Kiosk Browser“, Recht „Handhelds
und Fully“): **Kiosk-Modus** mit Zustand (ein / entsperrt / aus) und **Ein (sperren)** / **Aus (entsperren)**,
**Bildschirm** ein / aus und **Helligkeit** 25–100 %, **Fully-App**: **Seite neu laden**, **Cache leeren** (lädt danach
neu), **In den Vordergrund** (falls eine andere App vorne ist), **Neu starten**: Fully, Handheld, **Ausschalten**.
Was die Bedienung stört (entsperren, Bildschirm aus, Cache, neu laden, ausschalten), fragt vorher nach.

## Akkustand und Fully Kiosk Browser

**Akkustand:** Jedes Handheld meldet ihn selbst über die Bestell-App, alle 20 Sekunden, solange die App offen ist.
Dafür muss nichts eingerichtet werden (Chrome kann es; in Fully mit eingeschalteter JavaScript-Schnittstelle
kommt der Wert direkt von Fully). Die Statusanzeige zeigt ihn bei jedem Handheld.

**WLAN neu verbinden:** Mit eingeschalteter JavaScript-Schnittstelle (Fully › Advanced Web Settings ›
*Enable JavaScript Interface*, eine PLUS-Funktion): Wartet eine Bestellung und das Handheld ist offline, schaltet die
App ein ausgeschaltetes WLAN ein und verbindet es nach 20 Sekunden neu. Ab Android 10 erlaubt Android das nur, wenn Fully
als **Geräteinhaber** eingerichtet ist (Provisioning beim Einrichten des Handhelds, siehe Anleitung von Fully Kiosk);
sonst passiert dabei nichts, die App sendet trotzdem alle 2 Sekunden neu.

**Helligkeit mit den Lautstärketasten:** Läuft die App in Fully mit eingeschalteter JavaScript-Schnittstelle
(Fully › Advanced Web Settings › *Enable JavaScript Interface*), machen Lauter/Leiser den Bildschirm in
10-%-Schritten heller bzw. dunkler; ganz schwarz wird er nie (mindestens 5 %). Kurz erscheint „Helligkeit 60 %“.
In den Einstellungen der App steht dann unter „Bildschirm“ ein Hinweis darauf. Sind in Fully die
Lautstärketasten gesperrt („Disable Volume Buttons“ oder KNOX „Disable Hardware Volume Keys“), kommen die
Tasten womöglich nicht bei der App an – einmal ausprobieren. Ohne Fully (z.B. in Chrome) ändern die Tasten wie
gewohnt nur die Lautstärke.

**Zwischen Bestell-App, Verwaltung und Statusanzeige wechseln:** Alle drei liegen auf demselben Server und Port
(`https://192.168.55.10/`, `/admin.html`, `/status.html`), die Links öffnen sich im selben Fenster. Ist in Fully eine
**URL Whitelist** eingetragen, muss sie `https://192.168.55.10/*` erlauben (nicht nur die Startseite). Wer aus der
Verwaltung (oder der daraus geöffneten Statusanzeige) zur **Bestell-App** wechselt, ist in der Verwaltung
**abgemeldet** – die Handhelds gehen von Hand zu Hand, eine Anmeldung der Festleitung soll nicht liegen bleiben.

**JavaScript-Schnittstelle nur mit Whitelist:** Fully gibt seine JavaScript-Schnittstelle (Kiosk entsperren, Apps
starten, Einstellungen ändern, Handheld neu starten) **jeder** Seite, die es lädt. Die Fully-Doku sagt dazu: nur mit
URL Whitelist. Mit der Fernverwaltung (unten) prüft der Pi das alle 15 Minuten und bei **Prüfen**. Ist die
Schnittstelle an und fehlt die Whitelist oder erlaubt sie auch fremde Seiten, steht in der Verwaltung › Geräte & Netz ›
Handhelds & PC beim Handheld rot **JavaScript ohne Whitelist**. Unter **Bearbeiten** setzt **Whitelist setzen** sie auf
die Start-URL des Handhelds und alles darunter (z.B. `https://192.168.55.10` und `https://192.168.55.10/*`). Die bisherige
Whitelist wird dabei ersetzt. Neu ausgerollte Handhelds bekommen diese Whitelist von Anfang an. In die Fully-Vorlage
kommt sie nicht, weil sie von der Start-URL jedes Handhelds abhängt. Kennt eine Fully-Version die Einstellungen nicht,
steht „nicht prüfbar“ und dahinter die Namen der Einstellungen, die danach klingen (für ein Update von FestPOS
melden) – bis dahin von Hand in Fully › Web Content Settings › *URL Whitelist* eintragen.

**Mehr Infos von Fully (optional):** Läuft die App in Fully Kiosk Browser mit **PLUS-Lizenz**, fragt der druckende
Pi alle 30 Sekunden zusätzlich Fully selbst ab: Akku auch bei geschlossener App, Akku-Temperatur (Warnung ab
45 °C), Bildschirm an/aus, Kiosk-Modus, WLAN-Signal, freier Speicher, Fully- und Android-Version. Die Details
stehen in der Verwaltung › Geräte & Netz › Handhelds & PC › Gerät › **Bearbeiten**, Bereich „Fully Kiosk“.

Das läuft **nur über HTTPS**. Für jedes Handheld erstellt die Verwaltung ein eigenes Zertifikat. Der Pi schickt das
Fully-Passwort erst, wenn das Handheld genau dieses Zertifikat vorzeigt. Antwortet Fully unverschlüsselt (HTTP) oder
mit einem anderen Zertifikat, geht kein Passwort hinaus. Fernsteuern tut FestPOS die Handhelds nur auf Knopfdruck ab
der Rolle Festleitung: App neu starten, Handheld neu starten, Handheld ausschalten, Kiosk-Modus aus/ein, Bildschirm
ein/aus, Helligkeit, Seite neu laden, Cache leeren, in den Vordergrund
(siehe „Herunterfahren und Neu starten“ und „Statusanzeige“).

Einmal vorbereiten (Admin): Verwaltung › Geräte & Netz › **Einstellungen** › „Fully Kiosk Browser“ – das Fully-Passwort
(Remote Admin Password) für alle Handhelds eintragen, Port 2323 lassen, speichern; dazu „FestPOS-WLAN“.

**Fully auf vorhandenem Handheld** (ab Festleitung, das Handheld bleibt wie es ist): Verwaltung › Geräte & Netz › Handhelds & PC › **Gerät
hinzufügen** › „Fully auf vorhandenem Handheld“ › Name und Rolle › **WLAN-QR** mit der Kamera scannen (Android fragt
„Verbinden?“) › dann:

1. Fully Kiosk Browser aus dem Play Store installieren und öffnen.
2. In Fully: Settings › **Remote Administration** › *Enable Remote Administration* und *Remote Admin from Local
   Network* einschalten und als *Remote Admin Password* das **Einmal-Passwort** eintragen, das die Verwaltung zeigt.
3. Die IP des Handhelds (steht in Fully unter Remote Administration) in der Verwaltung eintragen › **Einrichten
   starten**. FestPOS merkt sich die MAC des Handhelds (es bekommt so immer diese IP), sichert die Fernverwaltung mit
   HTTPS ab, stellt über HTTPS auf das echte Fully-Passwort um, spielt CA, Gerätezertifikat und Vorlage ein und
   prüft die Lizenz – die Schritte erscheinen mit ✓ oder ✕ wie beim Werksreset (siehe unten). Das echte
   Passwort muss am Handheld niemand kennen.

**Von Hand** (z.B. wenn Fully schon mit dem echten Passwort läuft): in der Geräteansicht › „Fully Kiosk“ ›
**Einrichten** – die Datei `fully-remote-admin-ca.p12` wird heruntergeladen. Per USB-Kabel in den **obersten
Ordner** des internen Speichers kopieren (nicht in „Download“), oder am Handheld herunterladen und mit der App „Eigene
Dateien“ nach oben verschieben; Fully muss auf den Speicher zugreifen dürfen. Fully neu starten, dann **Prüfen**:
„verbunden (HTTPS)“.

**Fernverwaltung öffnen:** Ist Fully für ein Handheld eingerichtet, öffnet dieser Knopf in der Geräteansicht die
Fernverwaltung von Fully (`https://<IP des Handhelds>:2323/`) in einem neuen Tab – Anmeldung mit dem Fully-Passwort.
Der Browser warnt beim ersten Mal vor dem Zertifikat (es ist das eigene Zertifikat, das FestPOS für dieses Handheld
erstellt hat): einmal „Trotzdem öffnen“ wählen. Geht nur von einem Gerät im selben Netz wie die Handhelds.

**Geräteansicht › Fully Kiosk** (ab Festleitung): oben der Zustand als Liste in zwei Spalten – Kiosk-Modus, Seite,
Lizenz, Fully-Version (wie auf dem Pi / älter), Geräteinhaber, Android und Modell, Bildschirm, Akku, WLAN, Speicher,
Zertifikat der Fernverwaltung. Darunter **Apps**: FestPOS Tap (installiert / geschickt / wird installiert, mit
**Installieren** bzw. **Auf … aktualisieren**) und Fully Kiosk mit **Fully aktualisieren**. Die Knöpfe stehen in
Gruppen: **Prüfen** (Prüfen, Vorlage anwenden, Fernverwaltung öffnen), **Neu starten** (Fully, Handheld) und
**Zertifikate** (CA einspielen, Gerätezertifikat, Neues Fully-Zertifikat – die Datei für die Fernverwaltung, siehe oben).
- **Installationssperren (T30):** Fully kann das Installieren von APKs sperren – «Disable APK Installs»
  (Device-Owner-Einstellungen) und auf Samsung «Disable non-Google-Play Apps» (KNOX). Beim Installieren von FestPOS Tap
  und der Fully-APK sucht der Pi diese Einstellungen in den Einstellungen des Handhelds, schaltet die eingeschalteten
  kurz aus und danach wieder ein (Fully selbst: etwa 2 Minuten später, weil Fully beim Aktualisieren neu startet). Die
  Geräteansicht zeigt, welche umgestellt wurden. Bei FestPOS Tap wartet der Pi bis 2½ Minuten auf die Meldung von
  Fully (`getInstallApkState`): «installiert» = von Fully bestätigt, «geschickt» = ohne Bestätigung. Eine Liste aller
  installierten Apps bietet die Fully-Schnittstelle nicht. Nicht mehr abfragen: „Im Einsatz“ aus oder **Gerät löschen** (Admin).

**CA und Gerätezertifikat über Fully (ab Festleitung):** Ist Fully eingerichtet („verbunden (HTTPS)“), geht Schritt 4.2
und 4.3 ohne Handarbeit am Handheld:

- **So kommen die Dateien aufs Handheld:** Fully holt sie selbst als ZIP beim Pi ab (`loadZipFile`) und entpackt sie
  nach `/storage/emulated/0/FestPOS/`. Die Adresse `http://<Pi>/fully/<Code>.zip` gilt nur einmal und höchstens
  eine Minute. Das Handheld muss den Pi also über **http (Port 80)** erreichen – wie für `http://192.168.55.10/ca.crt`.
  Unverschlüsselt ist dabei unbedenklich: Die CA ist öffentlich, das Gerätezertifikat ist mit einem langen
  Kennwort verschlüsselt, und dieses Kennwort geht nur über die HTTPS-Verbindung zu Fully.
- **CA einspielen:** Fully holt die `ca.crt` (als `festpos-ca.crt`) und installiert sie (`installUserCa` mit
  `file://`). Ohne Rückfrage am Handheld geht das nur, wenn Fully **Geräteinhaber** ist (Einrichtung per QR-Code von Fully).
  Auch beim Wechsel auf eine neue CA (siehe „Server-Zertifikate“) so auf jedes Handheld bringen; die alte CA
  lässt sich über Fully nicht entfernen, sie bleibt bis zum Zurücksetzen des Handhelds.
- **Gerätezertifikat:** immer mit dem Namen des Geräts (Rückfrage). Der Pi stellt das Zertifikat aus
  (mit langem Zufallskennwort, das niemand abtippen muss), lässt Fully es abholen, trägt es in Fully ein
  (`clientCaUrl`, `clientCaPassword`) und startet die App neu. Fully zeigt es dann selbst vor – Chrome fragt nicht
  mehr, welches Zertifikat. Gibt es schon eines, wird es **erneuert**: Das alte sperrt der Server,
  sobald das Handheld das neue benutzt. Damit ersetzt der Knopf auch „Jetzt erneuern“ in der App. Vor Ablauf macht
  der Pi das von selbst (siehe „Ablauf und Erneuern“).
- Klappt ein Schritt nicht, steht welcher (z.B. „Datei vom Pi abholen hat nicht geklappt: Fully hat die Datei
  nicht abgeholt“). Hat Fully die Datei nicht abgeholt, bleibt kein neues Zertifikat zurück; scheitert es danach,
  wird das neue sofort gesperrt.

**Neues oder zurückgesetztes Handheld einrichten (ab Festleitung):** Verwaltung › Geräte & Netz › Handhelds & PC › **Gerät hinzufügen** ›
**Neues Handheld einrichten (Werksreset)** – ein QR-Code pro Handheld, danach nichts mehr von Hand.

Einmal vorbereiten (Admin, Verwaltung › Geräte & Netz › **Einstellungen**, Karten „Fully-APK“, „Einrichtungs-QR (Werksreset)“
und „FestPOS-WLAN“; der Ablauf zeigt nur, ob alles bereit ist, und fragt nichts davon):
- **Fully-APK vom Pi (empfohlen):** die Fully-Variante für Geräteinhaber (`…-emm.apk`) einmal am PC von
  fully-kiosk.com herunterladen und in der Karte **Fully-APK** mit **APK hochladen** auf den Pi bringen. Der Pi
  liest Version und Paket aus der APK (eine APK aus dem Play Store, Paket `de.ozerov.fully`, weist er ab), rechnet die
  Prüfsumme selbst und **kopiert die APK auf alle Pi** (innert etwa einer Minute; die Karte zeigt den Stand pro Pi).
  Android lädt sie beim Einrichten über `http://<Pi>/fully/fully.apk` – von einem Pi, der sie schon hat. In der Karte
  „Einrichtungs-QR“ bei **Fully-APK von: diesem Pi** gibt es darum weder Adresse noch Prüfsumme. **Oder aus dem
  Internet:** Fully-APK von **Internet** wählen, dann erscheinen Adresse und Prüfsumme (beide Pflicht) – aus dem
  QR-Code, den Fully für die Geräte-Einrichtung anbietet.
- **FestPOS-WLAN** mit Passwort (eigene Karte; gilt auch für den WLAN-QR der beiden anderen Arten), optional der
  Fully-Code eines Fully-Cloud-Profils. Das WLAN-Passwort steht im QR-Code: nur am Bildschirm zeigen, nicht
  ausdrucken. In der Sicherung steht es nur verschlüsselt.
- **DHCP** muss auf den Pi laufen (`DHCP=1`, siehe `festpos.conf`), sonst steht ein roter Hinweis in der Karte.
- **Internet beim Einrichten:** Android will beim Einrichten als Arbeitsgerät ins Internet (Google, Samsung Knox),
  Fully für die Lizenz – ohne Internet bleibt das Handheld bei „Einrichtung des Arbeitsgeräts wird vorbereitet“
  stehen. Ohne Mobilfunk-Router gibt **der Pi** seine Verbindung weiter, wenn unter Verwaltung › **Internet** der
  Schalter **(b) Internet für die Handhelds beim Einrichten** an ist (Standard). Die Verbindung (WLAN des Pi, z.B.
  Handy-Hotspot, Router im FestPOS-Netz oder USB-Netzwerkadapter) stellt man auf derselben Seite einmal ein. Der Pi
  schaltet sie beim Einrichten ein, leitet den Verkehr aus dem FestPOS-Netz hinaus (Weiterleitung mit
  Adressumsetzung) und schaltet am Ende wieder ab, spätestens nach 30 Minuten. Solange sie an ist, haben **alle**
  Geräte im FestPOS-Netz Internet (auch noch nicht eingetragene); die Seite Internet zeigt „Internet ist an“ mit
  **Internet aus**. Die Verwaltung dafür über `192.168.55.10` öffnen – das ist das Gateway der Handhelds. Ist der Pi
  schon dauerhaft über den Router verbunden (Schalter c), sind die Handhelds direkt über den Router im Internet.
- **Dauerhaft verbunden (Schalter c):** für Kartenzahlung (Payrexx) und QR-Bestellung bleibt die Verbindung offen, auch
  nach einem Neustart (jeder Pi prüft jede Minute). Wege wie oben: Router im FestPOS-Netz, WLAN des Pi (kann den Access
  Point der Handhelds stören – dann Router oder Kabel vorziehen) oder ein **USB-A-Netzwerkadapter** mit Kabel zum
  Router/Modem. **Handys per USB-Tethering werden nie verwendet.** Hat nur ein Pi den WLAN-/USB-Weg, verbindet er sich
  und gibt das Internet an die anderen Pi weiter; die gehen über ihn. **Handhelds:** Über den Router gehen sie direkt
  (Gateway automatisch, siehe `DHCP_GATEWAY`). Über WLAN oder USB-Adapter schicken sie alles an die VIP; deren Pi gibt
  weiter – selbst oder über den Pi mit dem Gerät. Weitergegeben wird **nur an die eingetragenen Handhelds** (Geräte & Netz ›
  Handhelds & PC, «Im Einsatz») und die Pi; ein neu eingetragenes Handheld ist nach höchstens einer Minute dabei. Die Seite zeigt pro Pi «Verbunden seit …»,
  «über Pi A», ob das Internet erreichbar ist und ob der Tunnel läuft.
- Die **Vorlage** (siehe unten) bestimmt, wie Fully eingestellt wird.

Pro Handheld:
1. **Name**, **feste IP** (Vorschlag: nächste freie) und **Rolle** eintragen › **QR erstellen**. Das Gerätezertifikat
   bekommt denselben Namen. Der QR ist gross (er ist dicht); **Gross anzeigen** zeigt ihn bildschirmfüllend.
2. Handheld auf Werkseinstellungen zurücksetzen, auf dem Willkommensbildschirm **6× tippen**, QR-Code scannen.
3. Seite offen lassen. Die Schritte erscheinen mit ✓ oder ✕ und Grund:
   - **QR scannen – Fully holt seine Einstellungen:** Android verbindet das WLAN (Adresse aus dem DHCP-Bereich),
     installiert Fully als Geräteinhaber, Fully holt seine Einstellungen beim Pi
     (`http://<Pi>/fully/<Code>.json`, im QR-Code): die Vorlage mit Kiosk-PIN, dazu Fernverwaltung,
     Gerätename und Speicherzugriff. Dabei merkt sich der Pi die **MAC** des Handhelds und trägt die feste Zuteilung
     ein. Der QR-Code gilt 30 Minuten; die Einstellungsdatei gibt es nur in dieser Zeit und nur mit dem Code aus
     dem QR-Code. Für die Fernverwaltung steht darin ein **Einmal-Passwort**, nicht das echte Fully-Passwort.
     Sobald FestPOS beim Absichern auf das echte Passwort umgestellt hat, gibt es die Datei nicht mehr – wird das
     Handheld danach nochmals zurückgesetzt, braucht es einen neuen QR-Code (**Gerät hinzufügen**).
     Die Datei geht unverschlüsselt durchs FestPOS-WLAN (Fully kann sie beim Einrichten nicht anders holen): wer das
     WLAN-Passwort hat und in diesem Moment mitschneidet, sähe den **Kiosk-PIN** und eine hinterlegte
     Volumenlizenz. Darum Handhelds einrichten, bevor das WLAN-Passwort an viele Leute geht – und das WLAN-Passwort
     nur der Festleitung geben.
   - Fragt Fully nach einem **Provisioning Code**, ist das kein Lizenzschlüssel, sondern ein Profil der Fully
     Cloud. FestPOS gibt ohne eigenen Fully-Code `FFF` mit, dann kommt die Frage nicht; falls doch: überspringen.
   - **Feste Adresse (DHCP):** Nach spätestens etwa 2 Minuten hat das Handheld seine feste IP. Ein zurückgesetztes
     Handheld zeigt eine neue (zufällige) MAC – das ist kein Problem, beim Ausrollen trägt der Pi die neue ein. Hatte
     die Adresse vorher eine andere MAC (gelöschtes oder zurückgesetztes Handheld), gibt `festpos-dhcp-reload` sie
     sofort frei (alte Lease gelöscht, `dnsmasq` neu geladen; Protokoll: `journalctl -u festpos-dhcp-reload`).
   - Beim **Absichern** laufen die unverschlüsselten Befehle (Zertifikat für die Fernverwaltung holen, neu
     starten) nur mit dem Einmal-Passwort. Fully lädt das Zertifikat erst beim Start; reicht ein Neustart der App
     nicht, startet FestPOS das Handheld neu (als Geräteinhaber, ohne Rückfrage) und wartet bis 3 Minuten. Sobald
     Fully über HTTPS antwortet, setzt FestPOS das **echte Fully-Passwort** – es geht also nie unverschlüsselt durchs
     WLAN. Übernimmt Fully es nicht, steht das beim Schritt; **ohne QR weiter** holt es nach (FestPOS merkt sich
     das Einmal-Passwort dafür bis zu 24 Stunden).
   - **Fernverwaltung absichern**, **CA einspielen**, **Gerätezertifikat**, **Einstellungen aus der Vorlage** –
     danach läuft alles nur noch über HTTPS.
   - **Fully-Lizenz:** Ist eine **Volumenlizenz** unter „Einrichtungs-QR“ hinterlegt, steht sie schon in der
     Einstellungsdatei, und FestPOS setzt sie hier nochmals. Bei **Einzellizenzen** zeigt der Schritt die
     **Geräte-ID** von Fully: im Lizenzportal von Fully (<https://license.fully-kiosk.com/license/>) eine neue Lizenz dieser
     Geräte-ID zuweisen oder eine bestehende auf sie verschieben. Nach dem Zurücksetzen hat ein Handheld eine neue
     Geräte-ID, darum muss die Lizenz dann umziehen. Fully prüft die Lizenz beim Start: FestPOS startet Fully
     alle 2 Minuten neu (oder sofort mit **Fully jetzt neu starten**) und prüft alle 10 Sekunden, bis Fully
     „lizenziert“ meldet.
     Das Internet über den Pi bleibt so lange an, weil Fully die Lizenz online prüft. **Ohne Lizenz beenden** geht
     jederzeit. Lizenz und Geräte-ID stehen danach auch in der Geräteansicht: fehlt die Lizenz, steht dort ein
     Hinweis mit Link zum Lizenzportal, in der Liste „ohne Fully-Lizenz“ und auf der Statusanzeige ist das Handheld
     orange („ohne Lizenz“). Die Volumenlizenz steht in der
     Sicherung nur verschlüsselt.

Mit Einzellizenzen lieber **nicht zurücksetzen**, wenn es nicht nötig ist: Für ein Handheld, das schon läuft, genügt
**Handheld ist schon eingerichtet und im WLAN: ohne QR weiter** – Geräte-ID und Lizenz bleiben.

Ein schon zurückgesetztes Handheld hat eine neue zufällige MAC; das ist kein Problem, beim Ausrollen wird die neue
eingetragen. **Ohne QR weiter**: für ein Handheld, das schon im WLAN ist und dessen Fernverwaltung mit dem
Fully-Passwort läuft – dann fragt die Verwaltung, bevor das Passwort einmal unverschlüsselt hinausgeht (besser:
„Fully auf vorhandenem Handheld“ mit Einmal-Passwort).

**Fully aktualisieren (alle Handhelds):** Neue APK in der Karte **Fully-APK** hochladen (Admin). Haben Handhelds mit Fully
eine andere Version, fragt die Verwaltung gleich: „Fully … auf … Handhelds verteilen?“ – sonst später mit **Auf Handhelds
verteilen** in derselben Karte oder **Erneut verteilen** unter Geräte. Fully startet dabei auf jedem Handheld neu: nicht
während Bestellungen.
- Verteilt wird über die Fernverwaltung (`loadApkFile` mit der Adresse eines Pi, der die APK hat). Ohne Rückfrage
  installiert Android nur, wenn Fully **Geräteinhaber** ist (Werksreset mit Einrichtungs-QR); sonst fragt das Handheld.
- **Fully aus dem Play Store** („Fully auf vorhandenem Handheld“, Version „…-play“) ist eine andere App: Es bekommt die
  APK nie, aktualisiert sich über den Play Store und zählt nicht als abweichend.
- Ein Handheld mit noch nicht gesendeter Bestellung wird ausgelassen; ein Handheld, das nicht antwortet (WLAN aus, Standby),
  ebenso. Beide bleiben **offen**: Der Pi, der druckt, versucht es erneut, sobald Fully wieder antwortet und das Handheld
  10 Minuten nichts bestellt hat – eines nach dem anderen, höchstens 3-mal am Tag pro Handheld.
- **Angekommen** ist es erst, wenn Fully die neue Version meldet (spätestens nach 10 Minuten wird nochmals versucht).
- Die Version jedes Handhelds steht unter **Geräte** in der Liste und in der Geräteansicht. Weicht sie ab, steht oben
  der Hinweis „Fully … ist auf den Pi – … Geräte haben eine andere Version“ mit dem Stand pro Handheld (gesendet, nicht
  erreichbar, …) und in der Übersicht eine orange Karte. Ist ein Handheld neuer als die APK, passiert nichts (Android
  stuft nicht zurück).
- Einzelnes Handheld: Geräteansicht › **Fully aktualisieren** (ab Festleitung; ist die Version schon drauf, passiert
  nichts). Alles steht im Protokoll.

**Gleiche Fully-Einstellungen auf allen Handhelds:** Verwaltung › Geräte & Netz › Einstellungen › Karte **Fully-Einstellungen
für alle Handhelds** (Admin).

1. Ein Handheld in Fully fertig einstellen und ausprobieren.
2. Dieses Handheld wählen › **Als Vorlage übernehmen**. Der Pi liest alle Einstellungen von Fully (`listSettings`) und
   speichert sie als Vorlage.
3. **Alle Handhelds abgleichen** – oder pro Handheld in der Geräteansicht › **Vorlage anwenden** (ab Festleitung): zeigt zuerst, welche
   Einstellungen abweichen, und setzt sie erst nach „OK“. Was eine ältere Fully-Version nicht kennt, wird
   übersprungen. Manche Einstellungen wirken erst nach **App neu starten** (Statusanzeige › Handheld antippen).

**Kiosk-PIN:** In derselben Karte eintragen. Er gehört zur Vorlage, ist aber ein Geheimnis: Er wird nie
angezeigt, steht nicht in der heruntergeladenen Datei und in der Sicherung nur verschlüsselt. Beim Ausrollen steht er in der
Einstellungsdatei (der Schritt «Vorlage» setzt ihn dann nicht nochmals), beim Abgleichen setzt FestPOS ihn. Fully
zeigt den PIN nur verschlüsselt; FestPOS merkt sich nach dem ersten Setzen diesen Wert und erkennt so, ob ein Handheld
einen anderen PIN hat. Meldet Fully beim Setzen einen Fehler, hat den PIN aber übernommen, gilt er als gesetzt; sonst
steht beim Fehler Fullys Antwort.

Nie in der Vorlage (bleibt auf jedem Handheld, wie es ist): Gerätename und Titel, Gerätezertifikat, alle anderen
Kennwörter und verschlüsselten Werte (z.B. Fernverwaltungs-Passwort), Lizenz und WLAN. Die Fernverwaltung bleibt in
der Vorlage immer eingeschaltet – sonst sperrte sich FestPOS selbst aus.

**Genau festlegen:** **Vorlage herunterladen** (`fully-vorlage.json`), mit einem Texteditor ändern, **Vorlage
hochladen**. Auch eine von Fully selbst exportierte Einstellungsdatei geht; was nicht in die Vorlage gehört, fällt
beim Hochladen weg (die Meldung nennt es). Die Vorlage steht in der Datenbank und damit auch in der Sicherung.

Was die Meldungen bedeuten:

| Meldung | Was tun |
|---|---|
| kein HTTPS | Datei nicht im obersten Ordner, Fully nicht neu gestartet oder kein Speicherzugriff. Das Passwort wurde nicht gesendet. |
| anderes Zertifikat | Am Handheld liegt eine ältere Datei, oder ein anderes Gerät hat diese IP. Mit **Neues Fully-Zertifikat** eine neue Datei erstellen und aufs Handheld kopieren. |
| Passwort falsch | Passwort in Fully und in der Verwaltung angleichen. |
| nicht erreichbar | Fully zu, Fernverwaltung aus oder Handheld aus. |
| antwortet nicht (Standby?) | Handheld schläft. Der letzte Stand bleibt mit Uhrzeit sichtbar. |
| Fully darf das auf diesem Handheld nicht | Neu starten/Ausschalten: in Fully „Enable KNOX Features“ einschalten (siehe „Herunterfahren und Neu starten“). |

Den WLAN-Namen meldet Android Fully nur mit Standort-Berechtigung; ohne sie steht nur die Signalstärke da.

Gut zu wissen: Das Fully-Passwort steht in der Datenbank (in der Sicherung verschlüsselt), den privaten Schlüssel
hat nur das Handheld. Ein neues Zertifikat (**Neues Fully-Zertifikat**) macht das alte ungültig. „Im Einsatz“ aus
schaltet die Abfrage für dieses Handheld ab. Im Cluster fragt nur der druckende Pi (er hat die Adresse .10, mit ihm reden die Handhelds).

## Mobilfunk-Router: Empfang auf der Statusanzeige (optional)

Steht ein Mobilfunk-Router im Netz (Internet für Kartenzahlungen), zeigt die Statusanzeige im Kasten
„Netzwerk und Server“ seinen **Empfang in Balken** mit Wert und Anbieter, z.B. „Mobilfunk −92 dBm · mittel,
Swisscom · LTE B3+B20“. **Antippen** öffnet die Details: Signalstärke (RSRP), Störabstand (SINR), RSRQ, RSSI,
Anbieter, Netz und Band, Funkzelle, SIM-Steckplatz, Datenmenge seit Routerstart, Router-Modell und -Version und
einen **Verlauf** (bis 30 Minuten). Die Werte kommen alle 10 Sekunden neu – zum **Ausrichten der Antenne** das
Detailblatt offen lassen (es schliesst sich erst nach 10 Minuten) und die Kurve beobachten.

| Balken | Signalstärke (RSRP) | Anzeige |
|---|---|---|
| 4 – sehr gut | ab −80 dBm | grün |
| 3 – gut | −80 bis −90 dBm | grün |
| 2 – mittel | −90 bis −100 dBm | grün |
| 1 – schwach | −100 bis −110 dBm | **gelb**, Hinweis oben |
| 0 – sehr schwach | unter −110 dBm | **gelb**, Hinweis oben |
| kein Netz / keine Verbindung zum Router | – | **rot**, Störung oben – wenn FestPOS den Router braucht, sonst **gelb** |

**Wann rot:** Kein Netz oder keine Verbindung zum Router ist nur dann eine Störung (rot – Statusanzeige, Ampel der
Verwaltung und Status oben rechts auf jedem Handheld), wenn FestPOS den Router braucht: Kasse › Karte/TWINT (Payrexx) oder
die QR-Bestellung ist eingeschaltet, oder der Pi geht unter Netz & Internet › „Dauerhaft verbunden“ über das FestPOS-Netz
(Router) ins Internet. Sonst erscheint es nur gelb als Hinweis (Bar kassieren geht ja weiter).

Ein schlechter Störabstand (SINR unter 13 / 5 / 0 dB) begrenzt die Balken auf 3 / 2 / 1 – gutes Signal mit viel
Störung ist auch schwach. Ohne RSRP (3G) zählt die RSSI. Bestellen und Drucken hängen nicht davon ab.

**Unterstützte Router:** MikroTik mit RouterOS 7 (z.B. LtAP mini LTE kit, Chateau LTE), über die REST-Schnittstelle.
Weitere Modelle lassen sich später ergänzen (eigener Treiber in `app/uplink.py`), ohne an Anzeige und Verwaltung
etwas zu ändern.

**Sicherheit:** FestPOS liest nur, am Router ändert es nichts. Der Zugang läuft nur über HTTPS: Benutzer und Passwort
gehen erst hinaus, wenn der Router genau das Zertifikat zeigt, das in der Verwaltung übernommen wurde. Antwortet
er unverschlüsselt oder mit einem anderen Zertifikat (z.B. ein anderes Gerät unter derselben Adresse), wird nichts
gesendet. Auf dem Router genügt ein Benutzer mit Leserechten, der nur aus dem FestPOS-Netz kommen darf.

### Einrichten am Router (MikroTik, RouterOS 7)

Voraussetzung: Der Router hat im FestPOS-Netz die feste Adresse **192.168.55.1**, sein DHCP-Server ist aus und
das WLAN des Routers ebenfalls. Im Terminal des Routers (WinBox oder WebFig › *Terminal*) einfügen und `PASSWORT`
durch ein langes, eigenes Passwort ersetzen (dieselben Befehle stehen in der Verwaltung unter „Einrichtung am
Router“, dort mit dem Netz der eingetragenen Adresse):

```
/certificate add name=festpos-https common-name=festpos-router days-valid=3650 key-size=2048 key-usage=digital-signature,key-encipherment,tls-server
/certificate sign festpos-https
/ip service set www-ssl certificate=festpos-https disabled=no address=192.168.55.0/24
/user group add name=festpos-lesen policy=read,api,rest-api
/user add name=festpos group=festpos-lesen address=192.168.55.0/24 password=PASSWORT
/certificate print detail where name=festpos-https
```

- `certificate sign` dauert einige Sekunden. Der letzte Befehl zeigt den **Fingerabdruck** (`fingerprint=…`) –
  er wird gleich in der Verwaltung verglichen.
- Kennt der Router `rest-api` nicht (ältere RouterOS-7-Version), `policy=read,api` verwenden.
- Meldet FestPOS danach „Benutzer hat zu wenige Rechte“, der Gruppe zusätzlich `test` geben.
- Die Mobilfunk-Schnittstelle heisst normalerweise `lte1` (`/interface lte print`).

### Einrichten in FestPOS

1. Verwaltung › Geräte & Netz › Netz & Internet › **Mobilfunk-Router**: Modell „MikroTik (RouterOS 7)“, IP-Adresse `192.168.55.1`,
   Port leer lassen (443), Schnittstelle leer lassen (`lte1`), Benutzer `festpos`, das Passwort. Optional ein
   Name (z.B. „Mobilfunk Zelt“), sonst steht „Mobilfunk“ da. **Speichern.**
2. FestPOS verbindet sich sofort und zeigt den Fingerabdruck des Router-Zertifikats. Mit dem Fingerabdruck vom
   Router vergleichen – stimmt er überein: **Zertifikat übernehmen.**
3. Jetzt steht „Verbunden“ mit den Werten da, und die Statusanzeige zeigt die Balken. Fehlt etwas auf der
   Mobilfunk-Seite (keine SIM, PIN, kein Netz, keine Datenverbindung), steht statt „Verbunden“ der Grund in Rot.
   Unter „Antwort des Routers“ steht für die Fehlersuche, was der Router gemeldet hat (ohne IMEI, IMSI, ICCID).

Was die Meldungen bedeuten:

| Meldung | Was tun |
|---|---|
| Zertifikat noch nicht übernommen | Schritt 2 oben. Bis dahin sendet FestPOS keine Zugangsdaten. |
| Router zeigt ein anderes Zertifikat | Zertifikat auf dem Router erneuert? Dann den neuen Fingerabdruck vergleichen und übernehmen. Sonst: Hat ein anderes Gerät die Adresse? Es wurde nichts gesendet. |
| Router antwortet nicht über HTTPS | Dienst `www-ssl` aus oder ohne Zertifikat (`/ip service print`). |
| Benutzer oder Passwort falsch | Benutzer und Passwort auf dem Router und in der Verwaltung angleichen. |
| Benutzer hat zu wenige Rechte | Gruppe des Benutzers prüfen (siehe oben). |
| Mobilfunk-Schnittstelle nicht gefunden | Name der Schnittstelle prüfen (`/interface lte print`). |
| Router nicht erreichbar / antwortet nicht | Strom, Kabel, Adresse; `address=` beim Dienst und beim Benutzer muss das FestPOS-Netz erlauben. |
| keine SIM-Karte | SIM einlegen (nur bei ausgeschaltetem Router) oder in den anderen Steckplatz; RouterOS: `/interface lte settings set sim-slot=…`. |
| SIM-PIN nötig / SIM gesperrt (PUK) | PIN beim Router eintragen (WinBox: *Interfaces › lte1 › PIN*) oder die PIN vorher in einem Handheld abschalten; PUK nur mit einem Handheld. |
| Funk ausgeschaltet | Mobilfunk-Schnittstelle auf dem Router einschalten (`/interface lte enable lte1`). |
| sucht Netz / kein Netz / Netz lehnt die SIM ab | Antenne, Abdeckung, anderer Anbieter; SIM aktiviert und Datenoption gebucht? |
| keine Datenverbindung (APN prüfen) | Im Netz, aber kein Internet: APN des Anbieters auf dem Router (`/interface lte apn`). |

Gut zu wissen: Jeder Pi fragt den Router selbst ab (jede Statusanzeige zeigt ihn). „Verbindung prüfen“ dürfen
Festleitung und Admin, einrichten und Zertifikat übernehmen nur Admin; beides steht im Protokoll (ohne Passwort).
Das Router-Passwort steht in der Datenbank (auf allen Pi, in der Sicherung verschlüsselt) – trotzdem nur ein Benutzer mit
Leserechten.
Kennungen von SIM und Modem (IMSI, ICCID, IMEI) übernimmt FestPOS nicht.

## Kartenzahlung über Payrexx mit NFC (optional, T13)

> **Noch ungetestet (Version 1.0):** Die Zahlung über Payrexx lief noch nie gegen ein echtes Payrexx-Konto. Vor dem
> Einsatz am Anlass mit dem Testmodus von Payrexx alles durchspielen: [docs/testplan-payrexx.md](../docs/testplan-payrexx.md).
> Die Verwaltung zeigt dazu einen Hinweis und fragt beim Einschalten nach.


Der Gast hält **Karte oder Handy (TWINT, Apple/Google Pay) an das XCover** der Bedienung. Abgewickelt wird die Zahlung
von der Payrexx-App **«Tap to Pay»** auf dem XCover; die kleine Begleit-App **«FestPOS Tap»** startet sie mit dem
richtigen Betrag und meldet das Ergebnis an den Pi – «Bezahlt ✓» erscheint in der Bestell-App von selbst, die Zahlung
steht im Kassenbuch (mit Transaktionsnummer). Ist eine ganze Bestellung so bezahlt, bleibt die Zahlart am Handheld
auf «Karte/TWINT (NFC)» (die Knöpfe Bar/Karte/TWINT sind gesperrt); ändern kann sie nur die Verwaltung unter
Bestellungen. Internet braucht nur das **Handheld**, nicht der Pi: entweder eine eigene
SIM im XCover oder Verwaltung › Geräte & Netz › Netz & Internet **Dauerhaft verbunden** (Mobilfunk-Router, WLAN oder USB-Adapter am Pi; das
Gateway der Handhelds stellt sich von selbst ein).
Ohne Verbindung oder ohne die Apps wird bar oder mit dem eigenen Terminal kassiert.

**Einrichten bei Payrexx:** Konto eröffnen (payrexx.com), «Tap to Pay» und die gewünschten Zahlmittel aktivieren. Auf
jedem XCover die App **«Payrexx Tap to Pay»** aus dem Play Store installieren und mit dem Payrexx-Konto anmelden (NFC
eingeschaltet, Android 8 oder neuer). Einmal von Hand eine kleine Zahlung ausprobieren.

**«FestPOS Tap» auf die Handhelds:** Die Datei `festpos-tap.apk` liegt bei jeder FestPOS-Version auf GitHub (Release,
gebaut von `.github/workflows/android.yml`). Verwaltung › Kasse & App › Kasse › **Karte/TWINT (Payrexx)** oder Verwaltung ›
Geräte & Netz › Einstellungen › Karte **App «FestPOS Tap»** (dort mit Stand im Titel; nur Admin): Datei hochladen, dann **Auf die Handhelds installieren** – Fully installiert sie auf allen Handhelds mit
Fully-Fernverwaltung (wie die Fully-APK). **Ein einzelnes Handheld** (neu dazugekommen, Installation nicht
angekommen, neue Version): Handhelds & PC › Gerät öffnen › Karte Fully › **FestPOS Tap installieren** bzw. **Auf … aktualisieren**.
Die Handhelds laden die APK unverschlüsselt vom Pi (`http://<Pi>/fully/…`, Fully kann es nicht anders). Ein Update
einer schon installierten App nimmt Android nur mit derselben Signatur an; bei der **ersten** Installation von
«FestPOS Tap» prüft niemand die Datei – wer im FestPOS-WLAN ist und den Verkehr umleitet, könnte eine andere App
unterschieben. Darum wie beim Ausrollen: installieren, bevor das WLAN-Passwort an viele Leute geht.
Dort steht «wird installiert …», danach «Version … installiert am …» (von Fully bestätigt) oder «geschickt» bzw.
«fehlgeschlagen» (siehe „Installationssperren“ unter Geräteansicht › Fully Kiosk). Ohne Fully-Fernverwaltung: APK von Hand installieren.

**In FestPOS:** auf derselben Karte «In der Bestell-App anbieten» einschalten, **Zahlmittel** wählen (leer: die
Payrexx-App fragt; Karte kontaktlos; TWINT) und speichern. «Ergebnis zuerst in der Payrexx-App zeigen» lässt die
Payrexx-App ihre Bestätigung anzeigen, bevor es zurück in die Bestell-App geht.

**In der Kasse:**
- *Ganze Bestellung:* Zeile «Zahlung» › **Karte/TWINT (NFC)** – die Payrexx-App öffnet sich, der Gast hält Karte oder
  Handheld an die Rückseite. Danach steht die Zahlart auf «Karte/TWINT (NFC)».
- *Einzeln:* bei «Person n» unten **Person n zahlt mit Karte/TWINT** – bezahlt zählt wie «Person n hat bezahlt».
- *Teilen:* bei «Person n» unten **Person n zahlt mit Karte/TWINT** – bezahlt zählt wie «Person n hat bezahlt».
- Ging es nicht durch: **Nochmals an das Handheld halten** (gleiche Zahlung, nie doppelt) oder **Abbrechen** und bar
  kassieren. Im **Testmodus** wird nie echt bezahlt: die Payrexx-App startet nicht, nach wenigen Sekunden «bezahlt».

**Trinkgeld:** zuerst „+ Trinkgeld“, dann mit NFC zahlen – Betrag und Trinkgeld gehen in einer Zahlung an Payrexx.
In der Payrexx-App selbst **keine Trinkgeld-Abfrage** einschalten: sonst meldet sie einen anderen Betrag, die Zahlung
gilt als gescheitert und das Trinkgeld fehlt in der Abrechnung.

**Tagesabschluss:** eigene Zeile «Davon Karte/TWINT (NFC) über Payrexx» (mit Trinkgeld, wie auf der Payrexx-Abrechnung)
– sie wird beim erwarteten Bargeld abgezogen und lässt sich mit der Payrexx-Abrechnung vergleichen (Liste mit Transaktionsnummern: `GET /api/admin/payrexx/payments`).
Der Pi hört «bezahlt» vom Handheld, nicht von Payrexx selbst (er braucht dafür kein Internet). Was die Bedienung
**von Hand bestätigt** hat («Payrexx zeigte bezahlt», ohne Transaktionsnummer), steht darum im Abschluss und im PDF
eigens da: **«Davon von Hand bestätigt … – mit der Abrechnung von Payrexx vergleichen»**. Diese Zahlungen vor dem
Unterschreiben des Abschlusses in der Payrexx-Abrechnung suchen; fehlt eine, fehlt das Geld in der Kasse.

| Störung | Was tun |
|---|---|
| «App FestPOS Tap fehlt auf diesem Handheld» bzw. nichts öffnet sich | In der Verwaltung «Auf die Handhelds installieren» oder APK von Hand installieren. |
| «FestPOS Tap» meldet «Payrexx-App fehlt» | «Payrexx Tap to Pay» aus dem Play Store installieren und anmelden. |
| Payrexx-App zeigte «bezahlt», FestPOS aber nicht (Verbindung zum Pi weg) | Nach 15 Sekunden erscheint **Payrexx zeigte «bezahlt»** – antippen und bestätigen (steht im Protokoll mit dem Gerätenamen). **Nicht nochmals kassieren.** |
| «Diese Kartenzahlung hat ein anderes Handy gestartet» | Bestätigen von Hand, Abbrechen und Abfragen darf nur das Handy, das die Zahlung gestartet hat, ein Handy, das den offenen Tisch abrechnen darf, oder die Festleitung (T39). Die Festleitung holen oder das startende Handy nehmen. |
| «Erfolg, aber ohne Betrag» | Die Payrexx-App meldete Erfolg, aber keinen lesbaren Betrag: FestPOS wertet das nicht als bezahlt und startet die Zahlung weder per Knopf noch beim erneuten Öffnen nochmals (T43/T44 – sonst droht eine zweite Belastung). In der Payrexx-App nachsehen; war es bezahlt, **Payrexx zeigte «bezahlt»** antippen, sonst Abbrechen und neu starten. |
| Protokoll «Kartenzahlung: zweite erfolgreiche Belastung» (am startenden Handheld roter Hinweis, auch später) | Für eine schon bezahlte Zahlung meldete die Payrexx-App eine weitere erfolgreiche Transaktion – der Gast wurde zweimal belastet. Die zweite Transaktion steht im Protokoll: in der Payrexx-App bzw. im Payrexx-Konto zurückerstatten. |
| Protokoll «Kartenzahlung höher als am Tisch offen» | Während der Kartenzahlung wurde am Tisch schon anders bezahlt (z.B. bar) oder der Tisch geschlossen. Die Kartenzahlung zählt, wird aber nicht an den Tisch gebucht: die Differenz im Payrexx-Konto zurückerstatten oder bar herausgeben. |
| Zahlung nach «Abbrechen» doch durchgegangen | FestPOS verbucht sie trotzdem (Geld ist geflossen) und vermerkt es im Protokoll. |
| Protokoll «Ergebnis passt nicht zur Bestellung» | Die Payrexx-App meldete einen anderen Betrag oder Bezug: in der Payrexx-App bzw. im Payrexx-Konto prüfen und allenfalls zurückerstatten. |
| Storno einer mit NFC bezahlten Bestellung | FestPOS bucht das Storno wie immer; das Geld in der Payrexx-App oder im Payrexx-Konto unter Transaktionen zurückerstatten. |
| Tagesabschluss: «Kartenzahlung ist noch offen» | Eine Kartenzahlung wurde gestartet, aber weder bestätigt noch abgebrochen. Am Handy klären oder auf der Seite Tagesabschluss **Abbrechen** (T45, auch die Festleitung). Kommt danach doch «bezahlt», wird sie trotzdem verbucht. Ist der Tag schon abgeschlossen (T46): War die Bestellung mit Karte, TWINT, Andere oder Payrexx bezahlt, hat der Gast zweimal bezahlt – Protokoll «zu viel bezahlt», den Betrag im Payrexx-Konto zurückerstatten, die Kasse bleibt. War sie bar (oder ohne Zahlart), fragt die Seite Tagesabschluss: Kasse jenes Abschlusses prüfen. Stimmte sie, hat der Gast bar bezahlt → im Payrexx-Konto zurückerstatten, dann **Gast hat bar bezahlt – zurückerstattet**. Fehlte genau dieser Betrag → **Gast hat nicht bar bezahlt – nachbuchen**: der nächste Abschluss zeigt «Kartenzahlungen früherer Tage, nachträglich bestätigt» (Payrexx plus, erwartete Kasse kleiner – gleicht den Fehlbetrag aus). Die Festleitung darf beides. |

## QR-Bestellung am Tisch (optional, T22)

> **Noch ungetestet (Version 1.0):** Die Zahlung über Payrexx lief noch nie gegen ein echtes Payrexx-Konto. Vor dem
> Einsatz am Anlass mit dem Testmodus von Payrexx alles durchspielen: [docs/testplan-payrexx.md](../docs/testplan-payrexx.md).
> Die Verwaltung zeigt dazu einen Hinweis und fragt beim Einschalten nach.


Gäste scannen den QR-Code auf dem Tisch, bestellen auf ihrem eigenen Handy und zahlen bei **Payrexx** (TWINT, Karte,
Apple Pay, Google Pay). **Erst nach der Zahlung** entsteht die Bestellung, die Bons drucken wie immer (Gerät «Online»,
Tischnummer aus dem QR-Code), und die Bedienung bringt alles an den Tisch. Geht etwas schief, erstattet FestPOS
**automatisch** zurück: inzwischen ausverkauft, kein Drucker bereit, oder der Bon ist nach der eingestellten Zeit
(Standard 10 Minuten) nicht gedruckt. Der Gast sieht seine Bestellnummer und den Stand auf seinem Handy.

**Sicherheit:** Von aussen erreichbar ist nur ein kleiner **Gästedienst** auf dem Pi (eigener Benutzer, abgeschottet,
nur Speisekarte, Bestellen und Stand). Der Weg dorthin ist ein **Cloudflare Tunnel**: Der Pi verbindet sich selbst nach
aussen, kein Port ist offen. Verwaltung, Bestell-App und Datenbank sind darüber nicht erreichbar. Zeigt der Tunnel aus
Versehen auf FestPOS selbst, sperrt FestPOS jede solche Anfrage (Meldung im Journal). Voraussetzung ist die Härtung
(SSH nur mit Schlüssel, siehe «SSH-Zugang»).

**Was es braucht:**
- **Payrexx-Konto** mit TWINT und Karten; im Payrexx-Admin unter *Integrationen › API & Plugins* einen **API-Schlüssel**.
  Die **Instanz** ist der Name vor `.payrexx.com`.
- **Internet für den Pi**, dauerhaft (Verwaltung › Geräte & Netz › Netz & Internet › Schalter **(c) Dauerhaft verbunden**), auf einem dieser
  Wege (Verwaltung › Geräte & Netz › Netz & Internet › «Verbindung ins Internet»):
  - **Router im FestPOS-Netz**, z.B. der Mobilfunk-Router auf `192.168.55.1` (Kapitel «Mobilfunk-Router»);
  - **WLAN des Pi**, z.B. ein Handy-Hotspot (kann den Access Point der Handhelds stören);
  - **USB-A-Netzwerkadapter** am Pi mit Kabel zu einem Router oder Modem (Adresse per DHCP, nicht im Netz
    `192.168.55.x`).
  Handy-Tethering per USB nimmt FestPOS nie. Hat nur ein Pi den WLAN- oder USB-Weg, gehen die anderen Pi über ihn.
  Für die QR-Bestellung braucht nur der Pi Internet, die Handhelds nicht.
- **Eigene Domain** (etwa CHF 15 pro Jahr) und ein **Cloudflare-Konto** (gratis), die Domain bei Cloudflare eingetragen.

**Einrichten (einmal):**
1. **Tunnel anlegen:** Cloudflare › Zero Trust › Networks › Tunnels › *Create a tunnel* (cloudflared), Name «festpos».
   Den angezeigten **Token** kopieren – er ist geheim (wie ein Passwort). Die Installationsbefehle, die Cloudflare
   dazu anzeigt, braucht es nicht: Sobald der Tunnel an ist, installiert jeder Pi `cloudflared` selbst aus der
   Paketquelle von Cloudflare (braucht das Internet aus Schritt 3) und hält es aktuell (einmal pro Woche).
2. **Öffentliche Adresse:** im Tunnel unter *Public Hostname* z.B. `bestellen.<deine-domain>` → Typ **HTTP**, URL
   **`127.0.0.1:8090`**. Genau das und nichts anderes – nie 443 oder 8443.
3. **Internet:** Verwaltung › Geräte & Netz › Netz & Internet › Verbindung (Router im Netz, WLAN oder USB-A-Netzwerkadapter) und Schalter
   **(c) Dauerhaft verbunden** (Notweg: `INTERNET_GATEWAY` in `festpos.conf`).
4. **Verwaltung › Kasse & App › Kasse › QR-Bestellung am Tisch** (nur Admin): Adresse (`bestellen.<deine-domain>`),
   **Tunnel-Token** aus Schritt 1, Payrexx-Instanz und -Schlüssel (beide geheimen Werte verschlüsselt gespeichert, nie
   wieder angezeigt), Höchstbetrag pro Bestellung, Minuten bis zur automatischen Rückerstattung; **Speichern**, dann
   **Verbindung testen**. Der Token gilt für **alle Pi**, auch für später dazukommende: jeder Pi richtet den Tunnel
   innert einer Minute selbst ein, ohne Neustart. Unter dem Token steht der Stand pro Pi («verbunden», «wird
   eingerichtet …», «cloudflared fehlt – wird installiert …», «läuft nicht»). In Cloudflare muss der Tunnel danach «Healthy» sein.
5. Wenn alles «verbunden» ist: **«Bestellen am Tisch offen»**.
6. Optional in Payrexx unter *Webhooks*: `https://bestellen.<deine-domain>/api/gast/webhook` – dann ist eine Zahlung
   sofort da; ohne fragt der Pi jede Minute selbst nach.
7. **QR-Codes für die Tische (PDF)** drucken und aufstellen. Die Codes sind pro Tisch signiert (niemand kann für einen
   anderen Tisch bestellen) und bleiben gültig, solange `CLUSTER_SECRET` und die Adresse gleich bleiben.

**Cloudflare-Konto schützen:** Wohin der Tunnel zeigt, wird im Cloudflare-Konto eingestellt – wer dieses Konto
übernimmt, könnte ihn auf etwas anderes richten. Darum das Konto mit **Zwei-Faktor-Anmeldung** sichern. Auf dem Pi
läuft `cloudflared` zudem als eigener Benutzer `festpos-tunnel`, und die Firewall lässt ihn dort nur zum Gästedienst
(`127.0.0.1:8090`), zum DNS und ins Internet: die Datenbank, die Verwaltung, die Drucker und die Handhelds erreicht er
nicht, egal was im Cloudflare-Konto steht.
In Cloudflare lohnt sich für `/api/gast/order` zusätzlich eine *Rate Limiting Rule*; FestPOS selbst bremst pro Gast
(IPv6 pro /64) und begrenzt neue Bestellungen über alle Gäste zusammen auf 60 pro Minute.

**Tunnel ausschalten oder Token wechseln:** in derselben Karte **«Tunnel entfernen»** (alle Pi schalten ihn innert
einer Minute aus) bzw. einen neuen Token eintragen. Bei Verdacht, dass der Token in falsche Hände geraten ist: in
Cloudflare im Tunnel den Token erneuern (*Refresh token*) und den neuen hier eintragen – mit dem alten Token könnte
jemand einen eigenen Rechner als «deinen» Tunnel anmelden und Gäste auf eine fremde Seite leiten.

**Notweg ohne Verwaltung:** `TUNNEL_TOKEN=<Token>` in `festpos.conf` (Boot-Partition) und neu starten. Steht der
Token dort, hat er auf diesem Pi **Vorrang** vor dem aus der Verwaltung (Stand: «Token aus festpos.conf»).
`cloudflared` von Hand (nur, falls es sich nicht selbst installiert; einmal mit Internet, Befehle laut Cloudflare):
```bash
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared
```

**Webseite & Rechtliches (für Payrexx und die Gäste):** Verwaltung › Kasse & App › Kasse › **Webseite & Rechtliches**.
Ohne QR-Code zeigt die Gästeseite (`bestellen.<deine-domain>/`) eine Startseite: Fest, Datum und Ort, Ablauf in drei
Schritten, Zahlarten, Speisekarte mit Preisen (aus FestPOS, abschaltbar), Kontakt. Dazu `/impressum`, `/agb`,
`/datenschutz` und `/rueckerstattung`.
- **Veranstalter** (Name, Adresse, E-Mail, Telefon, verantwortliche Person, MWST-Nummer, Webseite des Vereins): daraus
  entsteht das Impressum.
- **Seiten:** AGB, Datenschutz und Rückerstattung & Lieferung je als **Text aus FestPOS** (Knopf «Vorlage einfügen»:
  Muster mit euren Angaben – anpassen und im Verein gegenlesen lassen, keine Rechtsberatung) oder als **Verweis auf
  eine andere Webseite** (z.B. die Datenschutzerklärung des Vereins; dort müssen Payrexx und die QR-Bestellung erwähnt
  sein). Im Titel der Karte steht, ob für Payrexx noch etwas fehlt.
- Gibt es AGB, muss der Gast sie im Warenkorb **akzeptieren** (Kästchen), bevor er bezahlen kann.
- **Aussehen:** Hauptfarbe, Bild oben und **eigenes CSS** (für Fortgeschrittene). Das eigene CSS gilt nur für die
  Startseite und die Rechtsseiten, nie für Speisekarte, Warenkorb und Bezahlen. FestPOS lehnt beim Speichern ab, was
  fremde Inhalte laden oder Daten abfliessen lassen könnte (`@import`, Adressen ausserhalb, `url()` ausser
  `url(/media/…)`, «<», Escapes, mehr als 20 KB) und nennt die Zeile.
- **Vorschau** zeigt alle Seiten direkt in der Verwaltung.
- **Das ganze Jahr online:** Über den Tunnel ist die Webseite nur da, solange ein Pi läuft. Für die Anmeldung bei
  Payrexx: **«Webseite für Cloudflare Pages (ZIP)»** herunterladen, in Cloudflare › Workers & Pages › *Create* › Pages ›
  *Upload assets* das ZIP entpackt hochladen und unter *Custom domains* z.B. `www.<deine-domain>` eintragen. Diese
  Adresse gibst du bei Payrexx an. Nach Änderungen neu hochladen. Die Datei `_headers` im ZIP verbietet dem Browser
  dort ebenfalls Skripte und fremde Server.

**Im Betrieb:** Online-Bestellungen erscheinen wie andere Bestellungen (Gerät «Online», Zahlart «Karte/TWINT»), im
Tagesabschluss in der Zeile Payrexx. Sie sind schon bezahlt und kommen darum auch mit «Tisch offen lassen» **nie** auf
den offenen Tisch (T38). Es gelten die Preise, die der Gast bezahlt hat, auch wenn sich ein Preis inzwischen geändert
hat. Ein Bon, der nicht gedruckt werden kann (auch Renderfehler), führt nach der eingestellten Zeit zu Storno und
Rückerstattung. *Online-Bestellungen* (gleiche Karte) zeigt alle mit Stand und dem Knopf
**Zurückerstatten** (ganze Bestellung: Storno und Geld zurück). Wird eine Online-Bestellung in der Verwaltung oder am
Handheld (Storno-Rolle) teilweise storniert, geht genau dieser Betrag automatisch zurück (das Trinkgeld erst, wenn alles
storniert ist). Jede Rückerstattung ist ein eigener Auftrag, der zusammen mit dem Storno gespeichert wird (T37): fällt
der Pi mitten drin aus, geht das Geld beim nächsten Durchgang trotzdem zurück, und nie mehr als bezahlt. Der
Tagesabschluss zählt eine zugesagte, aber noch nicht ausgeführte Rückerstattung schon als zurück (T44, auch beim
Trinkgeld) – darum darf auch abgeschlossen werden, während Payrexx gerade nicht erreichbar ist. **Nach dem
Tagesabschluss** (Gast reklamiert am nächsten Tag) geht **Zurückerstatten** weiterhin: der alte Abschluss bleibt, wie
er ist, und der nächste Abschluss bucht die Rückerstattung als «Rückerstattungen früherer Tage» (Umsatz, MWST,
Trinkgeld und Payrexx minus; die Kasse ändert sich nicht). Dasselbe gilt für «nicht gedruckt» nach einem Abschluss (T45). Mit eingeschaltetem Trinkgeld (Kasse) wählt der Gast im Warenkorb kein / aufrunden /
5 % / 10 %; es ist im Payrexx-Betrag und steht im Tagesabschluss als Trinkgeld Payrexx. Schliessen: Schalter aus – die
Gäste sehen «gerade geschlossen, bitte bei der Bedienung bestellen».

| Störung | Was tun |
|---|---|
| Gäste sehen «gerade geschlossen» | Schalter in der Verwaltung an? Adresse, Instanz und Schlüssel eingetragen? Ist ein Drucker bereit? |
| Seite lädt nicht | Stand pro Pi in der Karte QR-Bestellung? Tunnel im Cloudflare-Konto «Healthy»? `systemctl status festpos-tunnel festpos-gast`; Verwaltung › Geräte & Netz › Netz & Internet: (c) an und «Verbunden seit …»? |
| «cloudflared fehlt – wird installiert …» | Der Pi hat noch kein Internet oder erreicht `pkg.cloudflare.com` nicht; er versucht es alle 10 Minuten. Internet prüfen (Geräte & Netz › Netz & Internet) oder einmal von Hand (oben, Notweg). |
| «Benutzer des Gästedienstes fehlt» | Pi wurde noch nie mit dieser Version gestartet: einmal neu starten. |
| «Rückerstattung offen» (rot), Zeile «wird gesendet» | Payrexx war nicht erreichbar (kein Internet): FestPOS sendet jede Minute nochmals, bis es klappt. Nichts tun. |
| «Rückerstattung offen», Zeile «gescheitert» | Payrexx hat die Rückerstattung abgelehnt. **Zurückerstatten** (oder **Nochmals senden**) versucht es nochmals; klappt es nicht, im Payrexx-Konto unter Transaktionen von Hand zurückerstatten (Transaktionsnummer in der Liste) und dann **Ist erledigt**. |
| «Rückerstattung offen», Zeile «unklar – im Payrexx-Konto prüfen» | Payrexx hat nicht geantwortet (Timeout); das Geld kann schon zurück sein. FestPOS sendet darum **nicht** von selbst nochmals (T37). Im Payrexx-Konto unter Transaktionen nachsehen: Rückerstattung da → **Ist erledigt**; nicht da → **Nochmals senden**. Beides steht im Protokoll. |
| Journal: «Anfrage über Cloudflare direkt an FestPOS» | Der Tunnel zeigt falsch: in Cloudflare die URL auf `127.0.0.1:8090` stellen. |
| Gast hat bezahlt, sieht aber nichts | Seite offen lassen oder neu laden – der Pi fragt Payrexx nach. Nach 30 Minuten ohne Bestätigung gilt die Zahlung als abgebrochen, aber erst, wenn Payrexx das bestätigt hat (T38); ohne Antwort bleibt sie bis zu 24 h offen. |
| Gast sieht «Abbruch wird geprüft …» | Payrexx war beim Abbrechen nicht erreichbar. Der Pi fragt jede Minute nach; hatte der Gast schon bezahlt, geht das Geld automatisch zurück. |
| Online-Bestellung «Zahlung kam nach dem Abbruch an» | Der Gast hat abgebrochen, die Zahlung kam trotzdem durch. FestPOS druckt nichts und erstattet automatisch zurück (Entscheid 10.11.). Abgebrochene Zahlungen prüft der Pi noch 2 h alle 5 Minuten (eine Payrexx-Zahlungsseite gilt 15 Minuten). |

## Neue Version einspielen

Ein Update geht über die **Verwaltung › Wartung › Update**, mit einem **USB-Stick** oder **aus dem Internet**: einmal
einspielen, FestPOS verteilt es selbst auf alle Pi. Ein Pi nimmt nur Pakete an, die mit einem **Update-Schlüssel**
signiert sind, den er kennt. Ein Update ersetzt Programm, App, Python-Pakete, rqlite (wenn neuer), Skripte und
Dienste; Daten, `festpos.conf` und Zertifikate bleiben. Internet braucht es nur für «Aus dem Internet».

### Offizielle Versionen

Die Versionen des Projekts stehen unter **github.com/T-0-N/FestPOS › Releases**: `festpos.zip` und `festpos.zip.sig`
(signiert mit dem Projekt-Schlüssel), dazu das Image für neue Karten. Am einfachsten **«Aus dem Internet»** (weiter
unten, das Repo ist schon eingetragen); sonst beide Dateien herunterladen und hochladen oder auf einen USB-Stick
legen. Versionen heissen `1.2.3` (Fehlerbehebung: letzte Zahl, neue Funktionen: mittlere, Umstellung nötig: erste);
**Testversionen** (`1.3.0-rc.1`) sind als *Pre-release* markiert und kommen nie über «Aus dem Internet» – nur von Hand.
Was neu ist, steht in `CHANGELOG.md`.

### Update-Schlüssel

Die Schlüssel liegen auf der **Boot-Partition** jedes Pi (`festpos-update*.pub`, nur der öffentliche Teil):
- **Projekt-Schlüssel** `festpos-update-projekt.pub` – bringen das Image und `setup.sh` mit (wenn noch kein
  Schlüssel da ist); damit gehen die offiziellen Versionen.
- **Eigener Schlüssel** – nur nötig, wer eigene Versionen baut und signiert. Bei der Ersteinrichtung im Browser
  («Eigener Update-Schlüssel») oder später von Hand.

Den Fingerabdruck (`SHA256:…`) aller Schlüssel zeigt die Verwaltung › Wartung › Update.

**Eigenen Schlüssel erzeugen** – am PC (PowerShell; `ssh-keygen` ist in Windows 10/11 dabei):
```powershell
mkdir -Force $HOME\.ssh
ssh-keygen -t ed25519 -C "FestPOS Update" -f $HOME\.ssh\festpos-update
```
Nach einer **Passphrase** fragt es zweimal – eine gute wählen und aufschreiben. Es entstehen zwei Dateien:
- `festpos-update` – der **private** Schlüssel. Bleibt auf dem PC, gehört nie auf einen Pi oder in eine Mail.
  Eine Kopie (zusammen mit der `.pub`) auf einem USB-Stick an einem sicheren Ort aufbewahren.
- `festpos-update.pub` – der öffentliche Teil. Er kommt auf **jeden Pi**, auf die Boot-Partition (neben
  `festpos.conf`). Bei einer neuen SD-Karte am PC einfach in den Explorer kopieren; bei laufenden Pi:
```powershell
scp $HOME\.ssh\festpos-update.pub pi@192.168.55.11:~/
```
```bash
sudo cp ~/festpos-update.pub /boot/firmware/
sudo festpos-update --schluessel        # zeigt alle Schlüssel
```
Am PC: `ssh-keygen -l -f $HOME\.ssh\festpos-update.pub`.

Schlüssel weg oder vielleicht in fremden Händen: neuen erzeugen, die neue `festpos-update.pub` auf alle Pi und
die alte dort löschen. Mehrere Schlüssel gehen auch (`festpos-update-2.pub` …) – praktisch für einen Wechsel. Wer
nur eigene Versionen will, löscht `festpos-update-projekt.pub`. Liegt aus Versehen ein privater Schlüssel auf der
Boot-Partition, meldet das die Verwaltung › Wartung › Update.

**Ein Update-Schlüssel kommt nie über die Verwaltung auf einen Pi** – nur mit dem Image, von Hand auf die
Boot-Partition (wie oben), bei der Ersteinrichtung im Browser (eigener Schlüssel, aus der Sicherung oder dem
Kartenpaket) oder mit einer Karte, die ein Pi selbst schreibt. Auch «Schlüssel aus der Sicherung übernehmen»
(Prüfliste nach dem Einspielen) schreibt Zertifikate, `CLUSTER_SECRET` und Notfall-Passwort zurück, aber keinen
Update-Schlüssel. So genügt ein gestohlenes Admin-Passwort nicht, um eigenen Code auf die Pi zu bringen. Fehlt der
Schlüssel auf einem Ersatz-Pi, zeigt es Verwaltung › Wartung › Update («Kein Update-Schlüssel»): dann wie oben kopieren.

### Eigene Version bauen und signieren

Nur für eigene Änderungen am Programm. `festpos.zip` bauen – unter Windows in einem Klon **ohne Umwandlung der
Zeilenenden** (sonst hätten die Skripte Windows-Zeilenenden und liefen auf dem Pi nicht); `build_zip.py` holt dabei
die Python-Pakete und rqlite für den Pi (mit Prüfsumme, braucht einmal Internet; ohne Internet mit
`--pakete-aus` aus einer früheren `festpos.zip`):
```powershell
git clone -c core.autocrlf=false https://github.com/T-0-N/FestPOS.git FestPOS-lf
cd FestPOS-lf
python tools\build_zip.py
```
In `VERSION` eine **neuere** Version eintragen als die installierte – die Pi nehmen nur neuere an.

1. Am PC im Ordner mit `festpos.zip` (das ZIP nicht auspacken und nicht neu packen):
   ```powershell
   ssh-keygen -Y sign -f $HOME\.ssh\festpos-update -n festpos-update festpos.zip
   ```
   Passphrase eingeben – daneben liegt jetzt `festpos.zip.sig`. (Meldet `ssh-keygen` „unknown option -- Y“, ist
   es zu alt: Windows aktualisieren oder das `ssh-keygen` aus Git für Windows nehmen.)
2. Weiter wie bei einer offiziellen Version (nächster Abschnitt).

### Verteilen

1. Verwaltung › **Wartung › Update** › `festpos.zip` und `festpos.zip.sig` auswählen › **Hochladen und prüfen**.
   Angezeigt werden Version, wer signiert hat und welche Pi sie bekommen.
2. **Auf alle Pi verteilen**. Sind noch Druckaufträge offen oder kamen eben Bestellungen, fragt FestPOS nach.

Danach arbeiten die Pi selbst, der Fortschritt steht in der Verwaltung und auf der Statusanzeige:
- Immer nur **ein** Pi aufs Mal, die nicht druckenden zuerst, **der druckende zuletzt**. Jeder ist etwa eine
  Minute weg; Bestellen und Drucken laufen über die anderen weiter. Kurz laufen verschiedene Versionen
  nebeneinander – das ist so vorgesehen.
- Jeder Pi prüft die Signatur selbst und nimmt nur eine **neuere** Version an. Dann läuft dasselbe
  Sicherheitsnetz wie von Hand (Probelauf, Sicherung, automatischer Rückfall).
- Es geht nur weiter, wenn **alle Pi erreichbar** sind – sonst fehlte der Datenbank die Mehrheit, solange einer
  neu startet. Die Verwaltung zeigt, auf welchen Pi gewartet wird.
- **Schlägt es auf einem Pi fehl**, schaltet er selbst auf die vorherige Version zurück und das Update hält an.
  In der Verwaltung stehen Grund und Protokoll; dann „Erneut versuchen“, „Abbrechen“ oder „Alle Pi zurück“.
- Die Handhelds übernehmen die neue App selbst, sobald die leere Tischansicht offen ist.

Updates kommen nie von selbst – nur, wenn Sie eines hochladen, aus dem Internet holen oder einen Stick einstecken.
Nicht während des Anlasses.

### Mit einem USB-Stick

`festpos.zip` und `festpos.zip.sig` auf einen USB-Stick kopieren (oberste Ebene oder Ordner `festpos-update`;
FAT32, exFAT oder NTFS) und an **einen** Pi stecken. Er liest den Stick nur, die Statusanzeige meldet
„USB-Stick: Version … übernommen – Stick kann weg“; verteilt wird wie oben. Ohne gültige Signatur oder mit einer
älteren Version geschieht nichts. (Läuft FestPOS auf diesem Pi gar nicht, spielt er das Update nur bei sich ein.)

Ohne Stick, z.B. per `scp`: `scp festpos.zip festpos.zip.sig pi@192.168.55.11:~/`, dann auf diesem Pi
`sudo festpos-update --ordner ~` – auch das wird auf alle Pi verteilt.

### Aus dem Internet (GitHub)

Statt die Dateien hochzuladen, kann FestPOS sie aus einem GitHub-Release holen.

Genommen wird immer der neuste veröffentlichte Release (keine Entwürfe, keine Testversionen) aus dem Repo
`T-0-N/FestPOS` – das ist vorbelegt. Wer eigene Versionen in einem eigenen Repo veröffentlicht, trägt dort dessen
`Besitzer/Name` ein und hängt an jeden Release `festpos.zip` und `festpos.zip.sig` (signiert mit dem eigenen
Schlüssel).

**Privates Repo** (nur für ein eigenes): Ein Token anlegen: GitHub › Settings › Developer settings › *Fine-grained tokens* › nur dieses
Repo, Berechtigung *Contents: Read-only*, mit Ablaufdatum. Das Token steht danach in der Datenbank (auf allen Pi,
in der Sicherung verschlüsselt) und wird in der Verwaltung nur gekürzt gezeigt.

**In der Verwaltung** › **Internet**: Schalter **(a) Internet für Updates** an (Standard) und unter „Verbindung ins
Internet“ wählen, wie der Pi ins Internet kommt. Es ist immer **der Pi, der die Verwaltung gerade zeigt** (steht in
der Karte). Dieselbe Verbindung nimmt der Pi, um neuen Handhelds beim Einrichten Internet weiterzugeben (Schalter b).
Unter Wartung › Update › **Aus dem Internet (GitHub)** steht das Repo (`T-0-N/FestPOS`, nur für ein eigenes ändern).

| Verbindung | Was es braucht |
|---|---|
| Über das FestPOS-Netz | Router mit Internet im FestPOS-Netz, z.B. der Mobilfunk-Router `192.168.55.1`; Gateway = seine Adresse, DNS z.B. `1.1.1.1` |
| WLAN des Pi | WLAN-Name und Passwort, z.B. Handy-Hotspot. Das WLAN darf **nicht** das Netz `192.168.55.x` haben |
| USB-Netzwerkadapter | Adapter am Pi, Kabel zu einem Router mit Internet, der Adressen vergibt (DHCP) |

Dann **Nach neuer Version suchen** – der Pi ist kurz online, zeigt die neuste Version und ist wieder offline.
Ist sie neuer: **Herunterladen und prüfen**, danach wie oben **Auf alle Pi verteilen**. Die anderen Pi holen das
Paket wie immer über das FestPOS-Netz; nur der eine Pi braucht Internet.

- Das Internet ist nur während Suchen und Herunterladen an. Bleibt es durch einen Fehler an, schaltet ein Zeitgeber
  es nach 30 Minuten ab; von Hand: `sudo festpos-update --netz-aus`.
- Geprüft wird genau wie beim Hochladen: ohne gültige Signatur mit dem Update-Schlüssel oder mit einer älteren
  Version geschieht nichts – auch wenn jemand Fremdes etwas ins Repo stellt.
- **WLAN:** Der Funk des Pi ist aus (stört den Access Point nicht). An ist er nur während eines Updates aus dem
  Internet.
- Meldet die Suche „Zertifikat nicht anerkannt“, stimmt meist die Uhrzeit des Pi nicht (Geräte & Netz › Pi › Uhrzeit).
- Nicht während des Anlasses: Ein WLAN auf demselben Kanal wie der Access Point kann die Handhelds kurz bremsen.

### Zurück zur vorherigen Version

Verwaltung › Wartung › Update › **Alle Pi zurück auf …** – wechselt auf allen Pi zur Sicherung (die Version vor dem letzten
Update), wieder einer nach dem anderen. Nochmals gedrückt („Alle Pi wieder auf …“) wechselt es wieder vor. Die
Datenbank bleibt dabei unberührt. «Zurück» braucht keine neue Signatur (die Sicherung war ein signiertes Paket) und geht darum nur genau
eine Version zurück; es steht im Protokoll.

Einzelner Pi von Hand: `sudo bash /opt/festpos/deploy/update.sh --zurueck` (zeigt beide Versionen und fragt nach).

### rqlite (Datenbank) mit dem Update

Das Update-Paket bringt rqlite in der Version aus `deploy/rqlite.env` mit. Ein Pi spielt es nur ein, wenn es **neuer**
ist als das installierte und **dieselbe Hauptversion** hat (z.B. v10.5.3 → v10.6.0); danach startet er rqlited neu.
Wie jedes Update geht das einen Pi nach dem anderen, den druckenden zuletzt; «Zurück» nimmt auch rqlite zurück.
Ein Wechsel der Hauptversion (v10 → v11) geschieht nie automatisch: zuerst die Hinweise von rqlite zum Upgrade lesen,
eine Sicherung machen und erst dann von Hand einspielen.

### Pi, die gefehlt haben

Der Plan bleibt stehen: Ein Pi, der beim Update aus war oder mit einer neuen SD-Karte (ältere Version) dazukommt,
ist an der Reihe, sobald er wieder läuft, und holt sich das Paket bei den anderen. Voraussetzung: auch er hat
den Update-Schlüssel auf der Boot-Partition – sonst steht in der
Verwaltung, was fehlt.

### Von Hand (ohne Verwaltung)

Als Notweg. Immer mit dem **entpackten `festpos.zip`** (nicht mit einem Git-Ordner): nur darin liegen die
Python-Pakete und rqlite für den Pi (`deploy/wheels`, `deploy/rqlite-…`, feste Versionen mit Prüfsumme).

Auf dem Pi zuerst den alten Projektordner entfernen (liegt dort noch ein `ca.key` aus einer älteren Version
in `~/festpos/deploy/festpos-tls/`, diesen vorher auf den PC sichern):
```bash
rm -rf ~/festpos
```
Am PC (im Ordner, in dem `festpos` entpackt liegt):
```powershell
scp -r festpos pi@192.168.55.11:~/
```
Auf dem Pi:
```bash
sudo bash ~/festpos/deploy/update.sh
```
Am Ende steht „OK: Server läuft mit Version …“. Die Handhelds übernehmen die neue App selbst – nicht mitten
im Bestellen oder Kassieren, sondern sobald die leere Tischansicht offen ist.

`update.sh` prüft dabei auch `festpos.conf` und `node.conf`. Steht dort eine **WARNUNG** oder **ACHTUNG**,
vor dem nächsten Neustart korrigieren – diese Dateien werden erst beim Neustart angewendet.

**Sicherheitsnetz:** Bevor etwas umgeschaltet wird, bereitet `update.sh` die Python-Pakete getrennt vor und lädt
die neue Version probeweise. Geht das schief, bleibt alles beim Alten. Danach sichert es die laufende Version
(`/opt/festpos.alt`); meldet sich der Server nicht mit der neuen Version, schaltet es **automatisch zurück**.
Von Hand: `sudo bash /opt/festpos/deploy/update.sh --zurueck` – zeigt die laufende und die gesicherte Version und
wechselt nach Bestätigung zur gesicherten; die laufende kommt dafür in die Sicherung (nochmals `--zurueck` wechselt
also wieder zurück). Nach einem automatischen Rückfall liegt die fehlgeschlagene Version in der Sicherung.
Die Datenbank bleibt dabei unberührt (neue Versionen ergänzen höchstens Spalten, mit denen ältere weiterlaufen).

Im Cluster einen Pi nach dem anderen, den **druckenden zuletzt** (welcher das ist: Statusanzeige „druckt“ oder
`sudo festpos-notfall status`):

1. Projektordner auf alle drei Pi kopieren.
2. Auf dem ersten nicht druckenden Pi `update.sh` ausführen, auf „OK“ warten. Statusanzeige: wieder 3 von 3 erreichbar.
3. Dasselbe auf dem zweiten nicht druckenden Pi.
4. Zuletzt auf dem druckenden Pi. Der Druck geht dabei für ein paar Sekunden an einen anderen Pi über.

Meldet `update.sh` einen HINWEIS zu Netz/Firewall, die Pi danach ebenfalls einzeln neu starten (wieder den
druckenden zuletzt).

## Nach dem Anlass

1. Verwaltung › **Tagesabschluss** mit Kassensturz erstellen, **PDF** und **CSV** herunterladen und mit den
   Abrechnungen von SumUp und TWINT aufbewahren (siehe „Kassenbuch“)
2. Verwaltung › Bestellungen › **Alle Bestellungen (CSV)** herunterladen
3. Verwaltung › Wartung › Sicherung › **Sicherung herunterladen** (mit eigenem Passwort, zusätzlich zu den
   Sicherungen auf dem USB-Stick)
4. Verwaltung › Wartung › Sicherung › **Alle Bestellungen löschen** – geht erst, wenn alle Bestellungen in einem
   Tagesabschluss sind. Abschlüsse, Protokoll, Preisliste, Drucker und Tische bleiben.
5. Verwaltung › Geräte & Netz › Pi › **System herunterfahren** (auf Wunsch mit „Handhelds ausschalten“),
   dann warten, bis die grüne Lampe nicht mehr blinkt, und den Strom ziehen


## Alles zurücksetzen (alle Geräte raus, Standard-Konfiguration)

Vorher einen **Tagesabschluss** erstellen (siehe „Kassenbuch“: ohne eigenes `CLUSTER_SECRET` lassen sich die
Prüfsummen offener Bestellungen danach nicht mehr prüfen).

Setzt `festpos.conf` auf den Standard zurück – **übernommen werden `ADMIN_PASSWORD`, `CLUSTER_SECRET` und alle Netzwerk-/IP-Einstellungen** –,
erzeugt Zertifizierungsstellen und Server-Zertifikat neu (30 Jahre gültig) und leert die Geräteliste (neue Gerätezertifikate gelten wie eingestellt, Standard 10 Jahre). Produkte, Bestellungen und Einstellungen
bleiben. Vorher wird alles nach `/root/festpos-backup-DATUM/` gesichert.

```bash
cd ~/festpos/deploy                 # aktueller Projektordner (wie beim Update)
sudo bash festpos-zuruecksetzen     # fragt nach, mit JA bestätigen
sudo reboot
```

Danach auf jedem Handheld und PC: alte „FestPOS Lokale CA" und das alte Gerätezertifikat entfernen,
neue `ca.crt` (http://192.168.55.10/ca.crt, oder `~/festpos-ca.crt` auf dem Pi) installieren, in der Verwaltung
neue Gerätezertifikate ausstellen. `~/festpos-ca/` (darin `ca.key`) auf einen USB-Stick kopieren und vom Pi löschen.
Bei 3 Pi: den Ordner `festpos-tls` auf die anderen Pi kopieren und dort `sudo festpos-zertifikate <Ordner>`.
