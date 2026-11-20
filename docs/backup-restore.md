# Sicherung und Wiederherstellen

Was in einer FestPOS-Sicherung steht, was beim Einspielen geschieht und was danach noch zu tun ist (T8).

## Kurz

- **Jede Sicherung ist verschlüsselt** (Datei `.festpos`) und enthält **alles**, was es für einen neuen Pi braucht:
  Datenbank samt Zugangsdaten, Zertifikate und Schlüssel (`festpos-tls`), `CLUSTER_SECRET` und Notfall-Passwort.
- **Automatisch** sichert der Pi alle 15 Minuten auf einen USB-Stick mit dem Ordner `festpos-sicherung` – mit dem
  **Sicherungs-Passwort**, das der Pi bei der Einrichtung erzeugt. Dieses Passwort **ausdrucken** (Verwaltung ›
  Wartung › Sicherung › Drucken) und beim Notfall-Passwort aufbewahren.
- **Herunterladen** über die Verwaltung geht nur mit einem Passwort, das du selbst wählst (mindestens 12 Zeichen).
- Nach dem Einspielen zeigt die Verwaltung eine **Prüfliste** (Wartung › Sicherung, dazu eine Karte in der Übersicht).
- **Ohne Passwort** lässt sich eine Sicherung nicht einspielen. Ältere Sicherungen (`.sqlite`, aus Versionen vor T8)
  gehen weiterhin – sie enthalten aber keine Schlüssel und keine Zugangsdaten.

## Was in der Sicherung steht

| Inhalt | In der Sicherung |
|---|---|
| Preisliste, Kategorien, Bilder, Logo, Tische, Lager | ja |
| Bestellungen, Stornos, Quittungen, Tagesabschlüsse, Protokoll | ja (offene Bons werden beim Einspielen nicht gedruckt) |
| Benutzer, PIN | ja (nur als Hash; alle melden sich nach dem Einspielen neu an) |
| Drucker, Einstellungen (Anlass, Kasse, MWST, Bestell-App, Testmodus erlaubt) | ja |
| Geräte: Name, feste IP, MAC-Reservierung, Rolle, Fully-Kennung | ja |
| Gerätezertifikate und Sperren | ja |
| Zugangsdaten: Fully-Passwort, Kiosk-PIN, WLAN-Passwort, Router-Passwort, GitHub-Token | ja (verschlüsselt) |
| Zertifikate und Schlüssel (`ca.crt`, `server.crt/.key`, `device-ca.crt/.key`) | ja (verschlüsselt) |
| `CLUSTER_SECRET` und Notfall-Passwort (`ADMIN_PASSWORD`) aus `festpos.conf` | ja (verschlüsselt) |
| Update-Schlüssel `festpos-update*.pub` | ja (nur in der automatischen Sicherung) – zurückgeschrieben nur bei der Ersteinrichtung im Browser, **nie** über «Schlüssel aus der Sicherung übernehmen» in der Verwaltung (sonst genügte ein Admin-Passwort, um einen eigenen Update-Schlüssel einzuschleusen); fehlt er danach, von Hand auf die Boot-Partition kopieren (ANLEITUNG «Update-Schlüssel einrichten») |
| Netz und Buchstabe (`festpos.conf` ohne die zwei Werte oben, `node.conf`) | **nein** – die Ersatzkarte braucht sie schon zum Starten |
| Fully-APK | **nein** – liegt einmal unverschlüsselt daneben auf dem Stick (`festpos-sicherung/fully.apk`); in der Sicherung steht nur, welche APK gilt (`fully_apk`, T6). Jeder Pi hat sie, also kommt sie auf den Stick, egal wo er steckt |
| Pi-Infos, Druckerstand, Bildschirm, laufende Updates | **nein** – das ist der Zustand der einzelnen Pi |
| DHCP-Leases, Systemzeit | **nein** – bilden sich von selbst neu |

## Vorbereiten (einmal)

1. USB-Stick mit einem Ordner `festpos-sicherung` an einen Pi stecken und stecken lassen.
2. Verwaltung › Wartung › Sicherung › **Sicherungs-Passwort drucken** (A4, zusätzlich als Bon möglich), dann
   «Ausgedruckt und aufbewahrt». Bis dahin erinnert die Übersicht daran.
3. Eine Ersatzkarte so einrichten wie Pi A (Raspberry Pi OS und `setup.sh`, siehe `deploy/ANLEITUNG.md` Schritt 1) – dann in der Ersteinrichtung «Sicherung einspielen» wählen (T11), oder sie von einem anderen Pi schreiben lassen (T10).

Wer ein neues Sicherungs-Passwort erzeugt: Ältere Sicherungen auf dem Stick brauchen weiterhin das alte. Der Pi
probiert das frühere selbst, solange er läuft – auf einem neuen Pi braucht es den alten Ausdruck.

## Welche Datei? (T17)
Im Ordner `festpos-sicherung` auf dem Stick können verschiedene Dateien liegen – die Verwaltung zeigt nach der Auswahl,
welche Art es ist:

| Datei | Art | Passwort |
|---|---|---|
| `festpos-a-JJJJMMTT-hhmmss.festpos` | **automatisch** von Pi A (alle 15 Minuten, die letzten 20) | Sicherungs-Passwort vom Ausdruck; **auf demselben System keines** (der Pi probiert sein eigenes) |
| `festpos-b-…`, `festpos-c-…` | automatisch von Pi B bzw. C | wie oben |
| `festpos-manuell-a-JJJJMMTT-hhmm.festpos` (früher `festpos-sicherung-…`) | **heruntergeladen** (Wartung › Sicherung) und von Hand auf den Stick kopiert | das Passwort, das beim Herunterladen gewählt wurde |

Für «Stand von vorhin» die **neuste automatische** nehmen: Nach Namen sortiert steht sie bei ihrem Pi zuunterst. Das
Aufräumen des Pi löscht nur seine eigenen automatischen Sicherungen, nie heruntergeladene oder die anderer Pi.

## Fälle

### Einzelner Pi, SD-Karte defekt (neuer Pi)
1. Ersatzkarte in den Pi, starten. Verwaltung öffnen (Notfall-Login `admin` mit dem Passwort aus `festpos.conf` der
   Ersatzkarte).
2. Wartung › Sicherung › **Sicherung einspielen**: neuste Datei vom Stick (`festpos-a-JJJJMMTT-hhmmss.festpos`).
3. Passwort vom Ausdruck eintippen oder den QR-Code scannen («QR scannen» erscheint auf Handys, deren Browser es kann).
4. Der Pi erkennt den neuen Pi (leere Datenbank): Er übernimmt alles aus der Sicherung. Weichen Zertifikate,
   Schlüssel, `CLUSTER_SECRET` oder Notfall-Passwort ab, schreibt er sie auf die Boot-Partition (die bisherigen
   bleiben als `festpos-tls.alt-DATUM` und `festpos.conf.alt-DATUM` daneben) und **startet neu** (1–2 Minuten).
5. Die Handys bestellen danach **ohne Neueinrichtung** weiter – sie vertrauen demselben Server-Zertifikat, und ihre
   Gerätezertifikate stammen von derselben Geräte-CA.
6. Prüfliste durchgehen.

**Quittungsnummern:** Welche Nummern nach der Sicherung noch vergeben wurden, weiss der neue Pi nicht. Er macht darum
mit der nächsten vollen Hunderterzahl nach «letzte Nummer der Sicherung + 100» weiter (1295 → 1400) und vermerkt die
Lücke im Protokoll. In der Prüfliste lässt sich die nächste Nummer erhöhen (nie senken), z.B. nach einem Blick auf die
letzte Papierquittung.

### Gleiches System, ältere Sicherung einspielen
Z.B. nach einem falschen Import. Die laufende Datenbank hat Bestellungen, Gerätezertifikate oder Abschlüsse.
- Schlüssel, Zugangsdaten und Sicherungs-Passwort **dieses** Pi bleiben (eine alte Sicherung dreht sie nicht zurück).
  Weichen die Schlüssel der Sicherung ab, zeigt die Prüfliste das und bietet «Schlüssel übernehmen» an.
- **Sperren und Gerätezertifikate**, die seit der Sicherung dazukamen, bleiben erhalten (eine Sperre wird nie
  aufgehoben). Sonst könnte ein verlorenes, inzwischen gesperrtes Handy wieder bestellen.
- **Quittungsnummern** laufen ohne Lücke weiter (der Pi kennt die höchste vergebene).
- Bestellungen seit der Sicherung fehlen: Die Handys senden nach, was bei ihnen noch wartet; den Rest auf Papier
  nachtragen.
- Die eigene Sicherung vom Stick braucht kein Passwort – der Pi probiert sein eigenes selbst.

### Drei Pi, eine Karte defekt
Kein Einspielen nötig: Die neue Karte holt sich alle Daten von den anderen Pi. Sie braucht nur dieselben Schlüssel
und Einstellungen. Am einfachsten schreibt sie ein anderer Pi: Pi & Netz › **SD-Karte schreiben** (USB-Kartenleser
mit leerer Karte) › Buchstabe des defekten Pi › Buchstaben zum Bestätigen eintippen (T10). Ohne Kartenleser:
**Ersatzkarte vorbereiten** › Buchstabe wählen › Kartenpaket herunterladen, den Ordner `karte-x` auf die
Boot-Partition einer mit `setup.sh` eingerichteten Karte kopieren, Karte einstecken.

### Einzelner Pi: Ersatzkarte im Voraus schreiben
Pi & Netz › **SD-Karte schreiben** › «Pi A – Ersatzkarte für diesen Pi» schreibt eine Karte mit dem System, den
Schlüsseln und der `festpos.conf` dieses Pi, aber **ohne Datenbank**. Fällt die Karte aus: Pi aus, Ersatzkarte rein,
einschalten, Notfall-Login, Wartung › Sicherung › neuste Sicherung vom Stick einspielen (Fall «neuer Pi»). Weil die
Schlüssel schon gleich sind, braucht es keinen Neustart, und die Handys bestellen ohne Neueinrichtung weiter.
Die Ersatzkarte gut beschriftet aufbewahren – sie enthält alle Schlüssel.

### Drei Pi, alle Karten neu
Auf Pi A (einzeln, `CLUSTER_SIZE=1`) die Sicherung einspielen wie beim einzelnen Pi, dann **Auf 3 Pi erweitern**
(nächster Abschnitt).

## Von 1 auf 3 Pi erweitern
Pi & Netz › **Auf 3 Pi erweitern** führt in drei Schritten:
1. **Karten vorbereiten:** am einfachsten **hier schreiben** – USB-Kartenleser an Pi A, «Karte für Pi B schreiben»,
   dann für Pi C (T10, ohne PC). Über einen PC: zwei Karten wie Pi A einrichten (Raspberry Pi OS und `setup.sh`), das **Kartenpaket**
   herunterladen und den Inhalt von `karte-b` bzw. `karte-c` auf deren Boot-Partition kopieren. Das Kartenpaket
   enthält `festpos.conf` (mit `CLUSTER_SIZE=3` und ausgeschriebenem `CLUSTER_SECRET`), `node.conf`, `festpos-tls/`
   und die Update-Schlüssel. **Es ist nicht verschlüsselt** (die Karte liest es beim Start ohne Passwort) – nach dem
   Kopieren löschen.
2. **Pi A umstellen:** nur im Einzelbetrieb, mit frischer Sicherung (letzte Stunde) und ohne laufenden Anlass. Pi A
   setzt `CLUSTER_SIZE=3` und startet neu; er arbeitet danach allein weiter, bis B und C dabei sind.
3. **B und C einstecken** – möglichst beide zusammen. Solange erst einer dabei ist, braucht es beide; fällt dann einer
   aus, ist Bestellen gesperrt, bis der dritte da ist. Die Seite zeigt den Beitritt live.

## Die Prüfliste
Automatisch geprüft (wird alle 15 Sekunden neu gerechnet):

| Punkt | Rot / orange, wenn … |
|---|---|
| Gerätezertifikate passen | rot: andere Geräte-CA als in der Sicherung (Handys kommen nicht hinein) |
| Server-Zertifikat gleich | orange: anderes Server-Zertifikat (Handys zeigen eine Warnung) |
| Neue Gerätezertifikate möglich | orange: Schlüssel der Geräte-CA fehlt |
| Prüfsummen prüfbar | orange: anderes `CLUSTER_SECRET` als beim Erstellen der Sicherung |
| Drucker bereit | rot: nicht erreichbar, Papier leer, Deckel offen |
| Handys verbunden | orange: noch nicht alle eingetragenen Handys gesehen (Standby?) |
| Uhrzeit stimmt | rot: weicht mehr als 2 Minuten von diesem Gerät ab – Knopf «Uhrzeit übernehmen» |
| Zugangsdaten vorhanden | fehlende (nur bei älteren Sicherungen) mit Link zur Seite |
| Fully-APK | orange «wird kopiert»: dieser Pi holt sie gerade von einem anderen Pi (Cluster, T6); orange «fehlt»: «Vom Stick übernehmen» (gilt danach für alle Pi) oder unter Einstellungen › Geräte › «Fully-APK» neu hochladen |
| Feste IPs geschrieben | orange: DHCP-Zuteilungen noch nicht geschrieben |
| Automatische Sicherung | orange: keine aktuelle Sicherung auf einem Stick |
| Quittungsnummern | Hinweis und Feld «Nächste Quittungsnummer» (neuer Pi) |

Von Hand: Testmodus (erlaubt oder nicht, wie in der Sicherung), Bestellungen seit der Sicherung, Testbon drucken und
mit einem Handy bestellen, beim neuen Pi Benutzer, Notfall-Passwort, Internet und Router. «Alles erledigt» blendet die
Karte in der Übersicht aus (sonst nach 7 Tagen); die Prüfliste bleibt unter Wartung › Sicherung abrufbar.

## Am PC lesen
`python3 tools/sicherung_lesen.py DATEI.festpos ZIELORDNER` fragt nach dem Passwort und entpackt die Sicherung
(`festpos.sqlite`, `tls/`, `conf.json`) – z.B. um sie mit «DB Browser for SQLite» anzusehen. Braucht Python mit dem
Paket `cryptography`. Der entpackte Ordner enthält alle Schlüssel: danach löschen.

## Sicherheit
- Verschlüsselt mit AES-256-GCM, Schlüssel aus dem Passwort mit scrypt; ein falsches Passwort und jede veränderte Datei
  fallen sicher auf. Der Kopf der Datei (Pi, Zeitpunkt, Version) ist lesbar und enthält nichts Geheimes.
- Das erzeugte Sicherungs-Passwort hat rund 120 Bit – nicht zu erraten. Ein selbst gewähltes Passwort sollte länger
  als die verlangten 12 Zeichen sein.
- Wer die SD-Karte eines Pi hat, hat ohnehin alle Schlüssel. Der Stick ist mit der Verschlüsselung nicht schwächer
  geschützt als der Pi selbst.
- Ein beschädigtes Byte macht die ganze Datei unlesbar – darum bleiben 20 Sicherungen auf dem Stick, und jede wird
  nach dem Schreiben einmal zurückgelesen.
