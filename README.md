# FestPOS – Bestellaufnahme & Bondruck

Internes Bestellsystem für Bar, Vereinsfest, Gastrobereich: Bestellaufnahme, Bondruck auf Epson-Thermodrucker
(TM-T20III), Rechen-/Split-Hilfe beim Kassieren und ein **einfaches Kassenbuch** (Zahlart, Storno als Gegenbuchung,
Tagesabschluss mit Kassensturz, Prüfsummen, Protokoll, MWST optional). **Keine zertifizierte Registrierkasse.** Karte
und TWINT laufen über die eigenen Geräte (z.B. SumUp) – oder optional mit **NFC am XCover über Payrexx** (T13): der Gast
hält Karte oder Handheld an das Handheld der Bedienung, die Payrexx-App «Tap to Pay» wickelt ab, die Begleit-App «FestPOS Tap»
meldet das Ergebnis an den Pi (Internet braucht nur das Handheld, nicht der Pi). Optional bestellen und zahlen Gäste
auch selbst per **QR-Code am Tisch** (T22, Payrexx, über einen Cloudflare Tunnel; Bons erst nach der Zahlung; Optionen
eindeutig pro Gruppe, gleichnamige Auswahl in zwei Gruppen mit Gruppennamen wie am Handheld) – mit
Startseite, Impressum, AGB und Datenschutz aus der Verwaltung (auch als ZIP für Cloudflare Pages).

- **Start mit einem Raspberry Pi**, später ohne Neuinstallation auf 3 Pis erweiterbar (ausfallsicher).
- **Bestell-App** fürs Handheld (Samsung XCover 7): `https://192.168.55.10`
- **Verwaltung** am PC: `https://192.168.55.10:8443/admin.html`

- **Fertiges Image** (T12): `festpos-<VERSION>.img.xz` aus den Releases mit dem Raspberry Pi Imager auf die Karte –
  kein Internet am Pi nötig.
- **Ersteinrichtung im Browser** (T11): Karte mit `setup.sh` (oder dem Image, T12) ohne `festpos.conf` → der Pi startet
  in die Einrichtung (`https://festpos.local/` bzw. `https://192.168.55.10/`, vorläufiges Zertifikat, Einrichtungscode auf dem Bildschirm und auf
  der Boot-Partition): neu, aus einer Sicherung oder als Pi B/C mit dem Kartenpaket.

Aufbau Schritt für Schritt: **[deploy/ANLEITUNG.md](deploy/ANLEITUNG.md)** (Kapitel «Schnellstart»). Fertige
Versionen mit Image, Update-Paket und App: **Releases** dieses Repos. Änderungen: [CHANGELOG.md](CHANGELOG.md) ·
Sicherheitslücken melden: [SECURITY.md](SECURITY.md) · Lizenz: GPLv3 ([LICENSE](LICENSE)), fremde Software:
[THIRD-PARTY-LICENSES.md](THIRD-PARTY-LICENSES.md).

## Bekannte Einschränkungen

- **Payrexx ist ungetestet.** Karte/TWINT mit NFC (inkl. der Begleit-App «FestPOS Tap»), die QR-Bestellung am Tisch
  und die Rückerstattungen über Payrexx sind umgesetzt und automatisch getestet, liefen aber noch nie gegen ein
  echtes Payrexx-Konto. Vor dem Einsatz: [docs/testplan-payrexx.md](docs/testplan-payrexx.md). Im Code markiert mit
  `PAYREXX-UNGETESTET` (`PAYREXX_GEPRUEFT` in `app/payrexx_api.py`); die Verwaltung zeigt einen Hinweis.
- **Keine zertifizierte Registrierkasse** (siehe oben).

## Bestell-App (static/)

- Tisch per Ziffernblock (✕ löscht alles), zuletzt bediente Tische, Liste benannter Tische oder **QR-Scan**
  (wahlweise, Kasse & App › Bestell-App; liest Tischkarten und die QR-Codes der Gäste-Bestellung; native
  BarcodeDetector-API, Fallback jsQR lokal, Taschenlampe für Abendanlässe)
- **Tischplan** (wahlweise, Verwaltung › Tische › Tischplan): oben Umschalter **Plan | Nummer** (das Handheld merkt sich
  die Wahl, womit es beginnt, stellt die Verwaltung ein), Bereiche (Saal, Terrasse …) als Chips, Plan mit
  hochgeladenem Grundriss (im Dunkeln umgekehrt) oder nur gezeichnet; Tischnummern immer als Kreis von 40 px;
  zoomen und zugleich verschieben mit zwei Fingern, Doppeltipp oder + / − / ⤢, verschieben mit einem Finger (gezoomt
  auch über den Rand hinaus, damit Tische unter den Zoom-Knöpfen erreichbar sind); liegen Nummern übereinander,
  fragt die App «Welcher Tisch?». Ohne Plan oder mit «Kein Plan» bleibt es bei der Tischwahl
- Produktkacheln nach Kategorie, Antippen = +1, Minus-Feld = −1, Warenkorb zum Prüfen; Kategorie wechseln
  auch per **Wischen** (abschaltbar); Produktbilder keine / klein / **gross** (Einstellung)
- **Senden** → sofort die Kasse: Rückgeld (deckt «Gegeben» den Betrag: grün **«Bezahlt · Nächste Bestellung»**, die
  Zahlung ist festgehalten), **Teilen pro Person** wie Einzeln in zwei Schritten: *Aufteilen* – Personen
  − / +, jede Person mit ihrem Betrag (auf 5 Rappen, der höhere zuerst, zusammen genau das Total), Person antippen =
  kassieren (wie der grüne Knopf), lange drücken = eigener Betrag (Rest neu verteilt), unten «Weiter: Person 1 · CHF …»; *Person kassieren* – Betrag,
  **Zahlart pro Person**, Gegeben/Rückgeld, Scheine, Trinkgeld, Karte/TWINT (NFC), «Person 1 hat bezahlt»; am Schluss
  «Alles bezahlt ✓» mit **Quittung pro Person** (über den Anteil, MWST anteilig nach den Sätzen der Bestellung; eine
  Bestellung wird entweder über Positionen oder über Anteile quittiert),
  Einzeln zahlen in zwei Schritten pro Person (Positionen wählen → «Weiter: CHF … kassieren», kassieren → «Person 1
  hat bezahlt»; «Alle abwählen» | «Alle wählen», ganz Bezahltes am Ende der Liste; «Nächste Bestellung» erst, wenn
  alles bezahlt ist; Rest auch unter Rückgeld/Teilen; Zahlart pro Person); **eigene Beträge**: in Rückgeld «… als
  Teilzahlung · Rest … offen», in Teilen pro Person – Teilzahlungen gelten in allen Arten; **jede bezahlte Person**
  (nicht nur die letzte) lässt sich antippen und zurücknehmen, ausser mit Quittung oder Karte/TWINT über Payrexx;
  **alle Teilzahlungen gehen an den Server** (`order_payments`, auch nachgereicht), der Tagesabschluss zählt bar/Karte/
  TWINT pro Teilzahlung,
  in Einzeln kostet eine Person nie mehr als noch offen ist (ist die Auswahl mehr, zeigt eine orange Box «Gewählt −
  schon bezahlt = Rest»; deckt die Auswahl den Rest genau oder mehr, sind die übrigen Positionen «schon gedeckt» und
  nicht mehr wählbar); Positionen in Einzeln gleich hoch (Produkt, darunter «1 von 2 offen», Anzahl − Preis);
  Gegeben eintippen oder Schein antippen, Gegeben und Rückgeld in einer festen Zeile, die Tastatur springt beim
  Tippen nicht; «Gegeben» antippen scrollt zur Tastatur; zu wenig: «Fehlt noch» rot, sonst Rückgeld grün
- **Offene Tische** (wahlweise, Verwaltung › Kasse & App › Kasse › Abrechnen: *Sofort* oder *Tisch
  offen lassen*): nach «Senden» bleibt der Tisch offen («Nächste Bestellung», daneben «Jetzt abrechnen»); wer einen
  offenen Tisch wählt, sieht seit wann, bei wem und was offen ist (grün «Weiter bestellen», «Abrechnen»). Abgerechnet
  wird mit der Kasse (Rückgeld, Teilen, Einzeln) über alle Bestellungen; jede Zahlung liegt sofort auf dem Server
  (Abrechnen nur mit Verbindung), «Bezahlt · Tisch schliessen». Wer abrechnen darf: nur das eigene Handheld / dazu die
  Festleitung / jedes Handheld. Im Tischplan: grün bei mir offen, gestrichelt anderes Handheld, orange lange nichts bestellt.
  Quittung über den ganzen Tisch oder pro Person (jede Position nur einmal im Original, auch über mehrere
  Bestellungen); Karte/TWINT mit Payrexx bucht der Server direkt an den Tisch
- **Trinkgeld** (wahlweise, Verwaltung › Kasse & App › Kasse): unter dem Ziffernblock «+ Trinkgeld» in Rückgeld,
  Teilen (pro Person) und Einzeln – «Aufrunden auf …», «Stimmt so», +1/+2/+5 oder eintippen, danach Rückgeld mit
  Trinkgeld und «inkl. Trinkgeld … ✓»; mit Karte/TWINT über Payrexx im Betrag, bei der QR-Bestellung wählt der Gast.
  Kein Umsatz, keine MWST, nicht auf der Quittung; im Tagesabschluss eigene Zeilen (pro Zahlart, pro Handheld,
  ausbezahlt bar)
- **Bons & Quittung** (Verwaltung › Kasse & App › Bons & Quittung, T24): Bon-Baukasten für Quittung und Küchen-/
  Getränkebon – Bausteine in Druckreihenfolge ziehen (Maus, Finger, Alt+↑/↓, ▲ ▼), ein/aus, Eigenschaften pro Baustein
  (Grösse, Ausrichtung, fett, weiss auf schwarz …), eigene Bausteine (Freitext, Linie, Leerraum, Bild, QR-Code, beim
  Küchenbon Datum und Zeit), Vorlagen, Probe drucken («MUSTER»). Lieferzeitpunkt: jeder Zeitpunkt einzeln wählbar (Standard alle ausser «Sofort»); «Preise drucken» einmal für alle
  Küchen-/Getränkebons (Baustein Positionen, nicht mehr pro Drucker).
  Pflicht-Bausteine der Quittung lassen sich nicht ausblenden, KOPIE/TEST zuoberst, Prüfnummer zuletzt; die
  Gestaltung wird in jeder Quittung festgehalten. Vorschau aus denselben Druckerdaten als Bild, antippen wählt den
  Baustein. Hier auch Drucker, Kopf und Fusszeile der Quittung
- **Quittung für den Gast** (wahlweise): ganze Bestellung oder pro Person (Einzeln), fortlaufende Nummer, Inhalt
  festgehalten und mit Prüfsumme gesichert, Kopien als KOPIE gekennzeichnet und protokolliert, im Tagesabschluss;
  MWST nur wenn eingeschaltet (MWST-Nummer, Satz pro Position, MWST pro Satz), über CHF 400 mit Name und Ort des Gastes
- **Kopf** auf allen Seiten: rechts Status-Punkt für Server und Drucker mit Logo (bei Problemen der Hinweis statt
  des Logos, orange/rot; Festleitung auch gelb «Papier fast leer») und Verlauf; Platzwahl links ☰ und Name (Einstellungen); offline mit wartender Bestellung
  alle 2 s neu senden und (Fully) WLAN neu verbinden
- **Offline-fest:** App startet ohne Server (Service Worker), Bestellungen gehen in einen lokalen
  Ausgang und werden gesendet, bis der Server bestätigt – ohne doppelte Bons
- Verlauf der eigenen Bestellungen, Kasse jederzeit wieder öffnen (global abschaltbar); der Verlauf fragt den Pi nach
  dem Stand (storniert, Tisch abgerechnet, abgeschlossen, gelöscht) – Abgeschlossenes und Gelöschtes nur noch ansehen,
  gelöschte Übungsbestellungen verschwinden gleich, echte nach einem Tag; offline bleibt alles
- **Nur Bestell-Handhelds mit Gerätezertifikat** kommen auf die App (mTLS auf Port 443), kein Passwort im Browser.
  Ein **gesperrtes** Zertifikat weist der Server an einer Stelle für alle Routen der App ab (T3), sofort auf allen Pi.
  **Erneuern:** Handhelds mit Fully erneuert der Pi vor Ablauf selbst (siehe unten); ohne Fully zeigt die App den Hinweis
  mit «Jetzt erneuern», mit Fully nur, wenn die Automatik nicht geht (dann über die Festleitung)
- **PIN-Sperre** (optional, orange mit Schloss – nicht mit der Tischwahl zu verwechseln; ● getippt, ○ fehlt noch): nach Inaktivität und/oder wenn
  der Bildschirm aus war, funktioniert offline; pro Gerät
  eine eigene PIN möglich (Verwaltung › Handhelds, T14), sonst gilt die gemeinsame
- **Testmodus** zum Üben (Test-PIN wählbar): überall gelb-schwarz gekennzeichnet, Übungsbestellungen zählen nirgends,
  Druck wählbar und dann gross als TEST markiert; beim Start der App immer aus, beenden auch aus der Statusanzeige
  und der Verwaltung, Beginn und Ende im Protokoll
- **Einführung** (Tutorial) mit Beispieldaten, startet auf Wunsch nach dem Eingeben des Gerätenamens; erklärt nur, was
  eingeschaltet ist (Ausverkauft, Lieferzeit, Quittung, PIN-Sperre …) und fragt am Ende «Testmodus einschalten?»
- Hell/Dunkel, Bildschirm-anlassen, Android-Zurück-Taste, keine Downloads aus dem Internet; in Fully regeln die
  **Lautstärketasten die Helligkeit**; Knopf **Neu laden (App und Preisliste)** unter Einstellungen › Erweiterte Einstellungen;
  dort in Fully auch **Fully Kiosk**: Helligkeit − / +, Bildschirm aus, Cache leeren und neu laden, Fully neu starten,
  **Handheld neu starten** (Rückfrage; ist noch etwas offen – Ausgang, Zahlungen, abgelehnt, Warenkorb – Warnung mit
  Liste und ein zweites Mal bestätigen; tut Fully nichts, sagt es die App; ausschalten nur über die Verwaltung) –
  mit der Rolle „Festleitung“ dazu Kiosk-Modus aus/ein und Android-Einstellungen (entsperrt den Kiosk; danach wieder «ein»)
- Tischwahl zeigt unten einen **noch offenen Tisch** (ungesendete Artikel) – antippen macht dort weiter
- **Zahlart** Bar / Karte / TWINT in der Kasse – nur wenn eingeschaltet (aus / freiwillig / Pflicht), auch offline
  (geht mit der Bestellung mit oder wird nachgereicht)
- **Rolle „Nur Statusanzeige“**: das Gerät zeigt statt der Bestell-App die Statusanzeige und darf nicht bestellen.
- **Storno am Handheld** nur mit der Rolle „Bedienung + Storno“ oder „Festleitung“ (pro Gerätezertifikat): Menge, Grund,
  Storno-Bon (Positionen durchgestrichen, als Rasterbild gedruckt); der Bon in der Kasse zeigt danach pro Position, was
  storniert ist, und «Einzeln» nur noch, was gilt. Auch beim Abrechnen eines offenen Tisches (über alle Bestellungen)
- **Nachbuchen ohne Bon** für schon Geliefertes: Link im Warenkorb (jeder Tisch) und beim Abrechnen eines offenen
  Tisches; oranger Modus nur für diesen Vorgang, keine Bons, sonst wie jede Bestellung (Umsatz, MWST, Restmenge).
  Wer darf: Kasse (alle / nur mit Storno-Recht / aus); in den Bestellungen als «ohne Bon» markiert
- **Ausverkauft und Restmenge** pro Produkt: ausverkaufte Kacheln werden auf allen Handhelds grau (innert 10 Sekunden) und
  lassen sich nicht mehr antippen; mit Restmenge zählt der Server bei jeder Bestellung herunter, ab der Warnschwelle
  steht „noch 8“ auf der Kachel und die Statusanzeige warnt. Der Server lehnt Bestellungen für Ausverkauftes ab
  (keine Bons); zwei Handhelds können die letzten Stück nicht doppelt verkaufen (CHECK in der Datenbank, ein Batch).
  Ausverkauft melden auch am Handheld mit der Rolle „Festleitung“ (Einstellungen › Ausverkauft melden): antippen =
  sofort ausverkauft bzw. wieder frei; **lange drücken oder «Stück»** = Restmenge eingeben (− / + / Zahl / 5 10 20 50,
  grün «Noch N verfügbar», 0 = ausverkauft, «Ohne Restmenge» = frei verkaufen).
  **Vor dem Kassieren auf den Server warten** (Kasse: immer / nur bei Restmenge / aus, höchstens 2 oder
  5 Sekunden): abgelehnt bleibt die Bedienung im Warenkorb, alle betroffenen Positionen markiert, „Automatisch
  anpassen“; ohne Antwort geht es trotzdem zur Kasse (Ausgang, gleiche Nummer). Später abgelehnt: die Kasse zeigt
  „Nicht angenommen“ statt Total und Tastatur. CSV-Import/-Export mit `bestand`, `warnen_ab`, `ausverkauft`
- Auf Bestellungen steht das **ausgerollte Gerät und die Bedienung**: „XCover101 (Anna)“ (Name im Gerätezertifikat,
  in Klammern der in der App eingetragene Name)
- Links zu **Verwaltung** und **Statusanzeige** (gleicher Server und Port, auch in Fully Kiosk ohne Adressfeld)
- Tippen neben die **Optionsauswahl**: warnen / nichts tun / schliessen (Einstellung in der Verwaltung)
- Meldet den **Akkustand** an die Statusanzeige (Chrome-Schnittstelle oder Fully); in **Fully Kiosk** (PLUS)
  holt der Server zusätzlich Details ab – nur per HTTPS mit eigenem Zertifikat pro Handheld, sonst nie mit Passwort;
  auf demselben Weg richtet die Verwaltung CA und Gerätezertifikat ein und erneuert sie (ohne Handarbeit am Handheld).
  **Automatisch erneuern** (T3, Geräte & Netz › Einstellungen › Gültigkeit, Standard 2 Jahre vor Ablauf, höchstens die halbe
  Laufzeit): der Pi, mit dem die Handhelds reden, spielt nach jeder Fully-Runde einem fälligen Handheld ein neues Zertifikat
  ein – eines nach dem anderen und nur, wenn keine Bestellung wartet, es 10 Minuten nichts bestellt hat und (Bildschirm
  aus oder 10 Minuten keine Bestellung im ganzen Fest). Das alte gilt, bis das Handheld das neue benutzt; meldet es sich
  nicht innert 10 Minuten damit, kommt gleich ein zweites. Nach zwei Fehlschlägen an einem Tag: «Erneuern gescheitert»
  in der Verwaltung, die Übersicht zählt es zu «Zertifikate laufen ab», am nächsten Tag versucht es der Pi wieder

## Verwaltung (static/admin.html)

Menü in drei Gruppen, grosse Seiten mit Reitern. Die Seitenleiste bleibt beim Scrollen stehen,
die Gruppen sind klappbar (bei wenig Höhe automatisch bis auf die aktive); am Handheld eine Kopfzeile mit Menü-Knopf
(T5). Oben der Knopf **Statusanzeige** mit Ampel (live alle 5 s, dieselben Störungen und Hinweise zu Druckern und Pi
wie die Statusanzeige, `GET /api/admin/lamp`), unten die **Bestell-App** (am Handheld-Port oder in Fully) und der eigene
Name – ein Klick darauf öffnet Sprache, Passwort ändern, Überall abmelden, Abmelden und die Version:

| Gruppe | Seiten |
|---|---|
| – | **Übersicht**: Zahlen, Drucker, Pi erreichbar/Dienste, Hinweise (Zertifikate) |
| Betrieb | **Offene Tische** (ab Festleitung: übergeben, abrechnen, «nicht bezahlt» ausbuchen) · **Bestellungen** (suchen, Positionen, Zahlart ändern, stornieren, alle als CSV) · **Druckaufträge** (nachdrucken / erledigt; ab Festleitung «Offene Bons umleiten» von einem Drucker auf einen anderen, T34) · **Tagesabschluss** (Auswertung, Kassensturz, PDF/CSV, Prüfung der Kette) · **Protokoll** (wer hat wann was geändert – Geräte, Einstellungen, Uhrzeit, Sicherung, Kasse; ab Festleitung, T20) |
| Vorbereiten | **Produkte** (Reiter Produkte · Kategorien · Lieferzeiten · Import & Export, Reihenfolge per Drag & Drop oder einmal «A–Z sortieren», Sofort-Speichern, Bilder, Optionen, Lieferzeit, MWST) · **Tische** (Tischplan mit Bereichen und Grundriss: Tische ziehen, grösser, drehen, Festbank/eckig/rund, Reihe einfügen, Beschriftungen, Rückgängig, Prüfung «am Handheld übereinander»; inkl. Tischkarten als PDF oder Bon – ist «Tisch per QR-Code scannen» aus, fragt die Verwaltung vorher, ob es jetzt eingeschaltet werden soll) · **Kasse & App** (T34, Reiter **Veranstalter**: Name, Währung, Sprache, Tisch/Platz, Logo · **Kasse**: aufklappbare Karten mit Zustand im Titel – Abrechnen, Zahlarten & Trinkgeld, MWST, Karte/TWINT, QR-Bestellung – und ein Speichern-Balken «Geändert – Verwerfen · Speichern» · **Bestell-App**: Verhalten, PIN-Sperre, Testmodus (Festleitung sieht nur den Testmodus) · **Bons & Quittung**: Bon-Baukasten) |
| System | **Geräte & Netz** (T34, ein Menüpunkt mit Reitern) – Reiter **Pi**: Pi mit Uhrzeit als Liste, Pi antippen → Unterseite mit Zustand, Bildschirm, neu starten/herunterfahren nur dieses Pi; jeder Pi mit Diensten, neu starten/herunterfahren auf Wunsch mit den Handhelds – vorher 30 s Meldung mit Countdown auf allen Handys, der Statusanzeige und in der Verwaltung, dort abbrechbar; nur ein Pi von mehreren: Meldung «Pi B …» nur auf Statusanzeige und Verwaltung, Bildschirm am Pi, Uhrzeit, Zertifikate, **Auf 3 Pi erweitern** / **Ersatzkarte vorbereiten** (Kartenpaket) / **SD-Karte schreiben** (USB-Kartenleser, ohne PC) · Reiter **Handhelds & PC** (ab Festleitung: jedes Gerät einmal mit fester IP, Zertifikat, Rolle und «Im Einsatz» (in Liste und Geräteansicht direkt änderbar); «Bearbeiten» öffnet die Geräteansicht mit Zertifikat samt Verlauf, Sperren, Fully-Zustand und -Aktionen, «Jetzt erneuern» bzw. wie erneuert wird, Löschen nur Admin (ein Dialog, «Zertifikat sperren» vorgewählt); gesperrte Geräte zugeklappt unter «Gesperrt», auch mit fester IP (bleibt fürs Entsperren, fehlt aber auf der Statusanzeige und im DHCP); neue WLAN-Adresse (MAC) bei bekanntem Zertifikat: feste IP geht nach 10 Minuten Ruhe der alten von selbst mit, zwei Geräte mit demselben Zertifikat geben einen Alarm mit «Neue MAC übernehmen» / «Alte MAC behalten» / «Sperren»; Fully-Version pro Handheld, Hinweis bei abweichender Version mit «Erneut verteilen»; **Gerät hinzufügen** in drei Arten: Werksreset per Einrichtungs-QR, Fully auf vorhandenem Handheld mit Einmal-Passwort, Nur Zugang – die beiden letzten mit WLAN-QR) · Reiter **Einstellungen** (nur Admin: Fully-APK (auf alle Pi kopiert, auf die Handhelds verteilen), App «FestPOS Tap», Einrichtungs-QR, FestPOS-WLAN, Fully-Passwort, Vorlage, Gültigkeit der Zertifikate, Feste IP bei neuer MAC) · Reiter **Drucker** (Testdruck, Papierstatus, Umleiten, Standard-Drucker, Drucker-Netz im eigenen VLAN mit Stand pro Pi) · Reiter **Netz & Internet** (oben **Gateway, DNS & Zeitserver**: automatisch, fester Router z.B. im Firmennetz oder ohne Internet, Zeitserver in jeder Art – die Karten darunter richten sich danach; Schalter Internet für Updates, für die Handys beim Einrichten und **dauerhaft verbunden** für Payrexx/QR-Bestellung – über Router, WLAN oder USB-A-Netzwerkadapter, ohne Handy-Tethering, Weitergabe an die anderen Pi und nur an die eingetragenen Handhelds, Gateway der Handhelds automatisch; Verbindung des Pi, Mobilfunk-Router mit «Im Einsatz», Netzwerkgeräte auf der Statusanzeige mit «Im Einsatz») · **Benutzer** (feste Rollen Anzeige, Festleitung, Admin und **eigene Rollen aus einzelnen Rechten** – Bestellungen ansehen/ändern, Druckaufträge, offene Tische, Tagesabschluss, Protokoll, Produkte, Ausverkauft/Restmenge, Tische, Drucker, Handhelds & Fully, Pi & Uhrzeit, Testmodus; Einstellungen, Benutzer, Sicherung und Update nur Admin. Die Rolle Admin lässt sich weder ändern noch löschen, der letzte aktive Admin nicht sperren, herabstufen oder löschen. Karte **Zugriff**: «Nur mit Gerätezertifikat», siehe unten; Notfall-Login `admin`) · **Wartung** (Reiter Update · Sicherung: Sicherungs-Passwort drucken, verschlüsselt herunterladen/einspielen, Prüfliste nach dem Einspielen, automatisch auf USB-Stick, Bestellungen löschen) |

«Im Einsatz» aus: das Gerät bleibt eingetragen (beim Router mit Zugang und Zertifikat), wird aber weder geprüft
noch auf der Statusanzeige gezeigt – z.B. kein Mobilfunk an diesem Anlass. Ausgeschaltete Drucker erscheinen dort
ebenfalls nicht. Die Adressen in der Verwaltung sind `#<Seite>/<Reiter>`, z.B. `#devices/printers` oder `#pos/app`;
eine unbekannte Adresse führt auf die Übersicht.

## Updates

Versionen: `MAJOR.MINOR.PATCH` (`1.0.0`), Testversionen `X.Y.Z-rc.N` (Pre-release, älter als `X.Y.Z`); was neu ist,
steht in `CHANGELOG.md`. Eine Version kommt als `festpos.zip` mit Signatur `festpos.zip.sig` (`ssh-keygen -Y sign`).
Die offiziellen Releases (github.com/T-0-N/FestPOS › Releases) sind mit dem **Projekt-Schlüssel** signiert; dessen
öffentlicher Teil `deploy/festpos-update-projekt.pub` liegt im Image (und kommt mit `setup.sh`). Eigene Versionen
signiert man mit einem **eigenen Schlüssel** (`festpos-update.pub`, bei der Ersteinrichtung oder von Hand). Die Pi
spielen nur gültig signierte, **neuere** Versionen ein. Ein Schlüssel kommt nur mit dem Image, von Hand, bei der
Ersteinrichtung oder mit einer vom Pi geschriebenen Karte auf die Boot-Partition – nie über die Verwaltung (auch nicht
mit «Schlüssel aus der Sicherung übernehmen»): ein gestohlenes Admin-Passwort genügt nicht für eigenen Code auf den Pi.
- **Verwaltung › Update:** beide Dateien hochladen → Prüfung → „Auf alle Pi verteilen“. Jeder Pi kommt einzeln
  dran, der druckende zuletzt, nur wenn alle anderen erreichbar sind; jeder holt das Paket beim anderen Pi
  (mit `CLUSTER_SECRET`). Fehler: dieser Pi schaltet selbst zurück (`update.sh`), der Rest wartet.
  „Alle Pi zurück“ wechselt zur Sicherung `/opt/festpos.alt`.
- **Nachziehen:** Ein Pi, der fehlte (aus, neue SD-Karte), holt die Version selbst, sobald er wieder da ist.
- **USB-Stick:** `festpos.zip` + `festpos.zip.sig` auf einen Stick, an einen Pi stecken – wird nur lesend
  eingebunden, geprüft und wie oben verteilt. Alternativ `sudo festpos-update --ordner ~` nach `scp`.
- **Aus dem Internet (GitHub):** Verwaltung › Update › „Aus dem Internet“ (Verbindung und Schalter unter Verwaltung ›
  Internet) holt `festpos.zip` + `festpos.zip.sig`
  aus dem neusten Release (ohne Testversionen) von `T-0-N/FestPOS` (vorbelegt) oder einem eigenen Repo (privat:
  Token mit Leserecht). Der Pi der
  Verwaltung ist nur dafür kurz online – über einen Router im FestPOS-Netz (Gateway + DNS), sein WLAN oder einen
  USB-Netzwerkadapter (DHCP) – und danach wieder offline (spätestens nach 30 Minuten per Zeitgeber). Geprüft und
  verteilt wird wie beim Hochladen; ohne gültige Signatur geschieht nichts. Der Funk des WLAN ist aus (bei jedem
  Start) und nur während des Updates an.
- Die App installiert nichts selbst: Sie gibt einen Auftrag an den root-Helfer `festpos-update`, der das Paket
  in seinen eigenen Ordner kopiert, die Signatur selbst prüft und das `update.sh` aus dem Paket ausführt.

## Statusanzeige (static/status.html)

Am Pi-Bildschirm ohne Anmeldung, sonst aus der Verwaltung. Ansichten für 3,5"-Display, Pi-Bildschirm/TV, Tablet
(hoch/quer) und PC – automatisch erkannt, übersteuerbar mit `?ansicht=klein|pi|tablet|pc|auto`. Drucker,
Bestellungen mit Druckstatus (eine Bestellung antippen: gleich ihre Details, «‹ Alle Bestellungen» bzw. die Überschrift:
alle seit dem Tagesabschluss; einzeilig, wo Platz ist, und so viele, wie ganz hineinpassen – breit quer stehen die
Drucker als Reihe über den Bestellungen), Handhelds (Akku, Name aus der App, orange bei Kiosk-Modus aus), Netzwerk (nur Geräte im
Einsatz), **Server** (eine Zeile pro Pi: druckt, Sicherung; Temperatur und Speicher nur, wenn warm bzw. knapp; ein Dienst, der
nicht läuft, steht beim Pi), **Internet** (mit «Dauerhaft verbunden»: Weg, erreichbar, Tunnel; rot nur, wenn Karte/TWINT
oder QR-Bestellung es brauchen, nie doppelt mit dem Router), Gesamtlampe,
**Empfang des Mobilfunk-Routers** in Balken (antippen: RSRP, SINR, Anbieter, Band, SIM, Datenmenge, Verlauf der letzten
30 Minuten – zum Ausrichten der Antenne; gelb bei schwachem Empfang, rot ohne Verbindung).
Handheld oder Pi antippen: Details (Temperatur, Android-/OS-Version, Speicher …); ab Festleitung mit Knöpfen für
Seite neu laden, Neustart, Ausschalten und Kiosk-Modus aus/ein. Handhelds im Testmodus tragen die Marke TEST; **Testmodus beenden** (pro
Handheld, ab zwei Handhelds auch «Alle aus dem Testmodus») geht ab Festleitung und am Bildschirm des Pi ohne Anmeldung. Bestell-Handhelds mit Gerätezertifikat sehen sie ohne Anmeldung; unten
Links zur Bestell-App und Verwaltung (nie am Bildschirm des Pi).

**Einfache Statusanzeige** (`/status-einfach`, `app/status_simple.py`) für alte Browser wie Safari 9 (iPad mini,
iOS 9.3.5): der Server baut die Seite fertig als HTML aus denselben Daten wie `/api/status` (Gesamtlampe, Drucker,
Bestellungen, Handhelds, Netzwerk, Server), schlichtes CSS (`static/status-einfach.css`: kein Grid, `gap`, `min()`,
`color-mix()`). Ein kleines Skript in ES5 (`static/status-einfach.js`: XMLHttpRequest, kein `fetch`, keine Promises)
holt alle 10 s nur den Inhalt (`/status-einfach?teil=1`, Version in `X-FestPOS-Version`) und tauscht ihn aus – ohne
Neuladen, also ohne weisses Aufblitzen. Nach zwei Fehlschlägen erscheint «Keine Verbindung zum Server» und der Inhalt
wird abgedunkelt; versucht wird immer weiter. Bei neuer Version (Update) oder abgelaufener Anmeldung (401) lädt die
Seite ganz neu. Ohne JavaScript lädt sie wie bisher alle 10 s neu (`<noscript><meta http-equiv="refresh">`).
Gleicher Zugang wie `/api/status` (Anmeldung mit Basic Auth genügt, Rolle Anzeige). Nur anschauen.

## Übersetzung (i18n)

Alle Texte für Menschen stehen in **`app/i18n/<sprache>.json`** – derselbe Katalog für Server (Fehlermeldungen,
Bons, PDF) und Browser (`static/i18n.js`). Mitgeliefert: Deutsch (`de`, Ausgangssprache), Englisch (`en`),
Französisch (`fr`), Italienisch (`it`), Rumantsch Vallader (`rm-vallader`, BCP-47-Variante; `rm` und `rm-CH`
finden sie auch). Die Übersetzungen sind maschinell – vor dem Einsatz von Muttersprachigen gegenlesen lassen.
- Schlüssel flach, mindestens drei Teile: `bereich.thema.text` (Bereiche `app`, `admin`, `status`, `bon`, `err`,
  `srv`, `common`, `ledger`). Platzhalter `{name}`, Mehrzahl über CLDR-Kategorien `{"one": …, "other": …}` und `count`.
- Im Code: Python `t("err.order.empty")`, JavaScript `t("app.order.send")` bzw. `th(…)` in HTML (escaped die
  Platzhalter), feste HTML-Texte mit `data-i18n="…"`. Programmlogik hängt nie an Texten: Druckerfehler und
  Zustände haben Codes (`error_code`, `reason_code`), übersetzt wird erst bei der Anzeige.
- Sprache: pro Handheld/Browser wählbar, Vorgabe in der Verwaltung (`language`), Bons pro Drucker (`printers.language`);
  Anfragen schicken `X-Festpos-Lang`. Fehlende Texte einer Sprache: Deutsch. Datum/Zeit über `Intl`;
  Beträge bleiben in Schweizer Schreibweise (CHF).
- Prüfen: `tools/i18n_check.py` (fehlende, unbenutzte Schlüssel, Platzhalter, HTML-Tags, Mehrzahl, Bon-Texte im
  Zeichensatz PC858). Die Tests prüfen mit einer **Pseudo-Sprache** (`⟦Táxt⟧`) auf allen Seiten, Bons und
  Meldungen, dass kein Text fest im Code steht, und jede mitgelieferte Sprache im Browser (360 px breit: nichts
  abgeschnitten, Umschalter einzeilig) sowie auf Bons mit 32, 42 und 48 Zeichen.
- In Sätzen stehen Tisch/Platz klein (`app.word.table.one` = «table»: «Choisir d’abord la table»); Überschriften
  machen den ersten Buchstaben gross. Artikel gehören in den Satz, nicht in den Platzhalter.

## Architektur

```
 XCover 7 (Chrome/PWA) ── WLAN ── EAP225 ── TL-SG108PE ──┬── Pi A ─┐
   https://192.168.55.10 (VIP)                            ├── Pi B ─┼─ Raft (rqlite): jede Bestellung
                                                          ├── Pi C ─┘   liegt auf ≥2 Pis, bevor OK kommt
                                                          ├── TM-T20III Getränkeausgabe (:9100)
                                                          └── TM-T20III Küche (:9100)

 Jeder Pi:  festpos-node (Rolle aus node.conf) → rqlited → FastAPI (:443, TLS) + PrintSpooler
            keepalived hängt die VIP an den Raft-Leader; nur dort druckt der Spooler.
```

**Drucker-Netz (optional):** Die Drucker hängen in einem eigenen VLAN (z.B. 56, Netz 192.168.56.x), das nur die Pi
erreichen – aus dem WLAN kommt niemand an Port 9100. Eingestellt unter Verwaltung › Geräte & Netz › Drucker (nur Admin), gespeichert in
der Datenbank; jeder Pi schreibt es nach `/var/lib/festpos/druckernetz/` (`app/printer_net.py`), und
`festpos-druckernetz` (root) legt die VLAN-Schnittstelle über NetworkManager an (`eth0.56`, Adresse
`<Netz>.<letzte Stelle der Pi-IP>`). Weitergeleitet wird zwischen den Netzen nichts; die Firewall lässt aus dem
Drucker-Netz nur Antworten herein. Am Switch: Pi-Ports im VLAN mit Tag, Drucker-Ports ohne Tag (PVID).

**Garantien**
- **Keine verlorene Bestellung:** Bestellung + Druckjobs werden in einem atomaren Raft-Commit
  gespeichert (Mehrheit 2 von 3), bevor das Handheld „OK“ bekommt.
- **Kein doppelter Bon:** Ein Job wird per Compare-and-Set reserviert (`pending → printing`),
  ebenfalls als Raft-Commit. Ein Knoten ohne Mehrheit kann gar nicht drucken. Jobs, die bei
  einem Ausfall genau „im Druck“ waren, werden `uncertain` statt blind wiederholt – ebenso Bons, deren
  Verbindung mitten im Senden abriss (vorher abgewiesene Verbindungen werden weiter automatisch wiederholt).
- **Kein doppelter Bon bei Funkloch:** Das Handheld sendet eine `client_uuid`; Wiederholungen
  werden erkannt.
- **Kein doppelter Schreibzugriff (T36):** Geht die Antwort von rqlite auf einen Schreibbefehl verloren, wiederholt
  FestPOS ihn nicht selbst (er kann schon ausgeführt sein), sondern meldet «unklar»; nochmals gesendet wird nur mit
  demselben Schlüssel. Nach kurzer Ruhe bekommt ein Schreibbefehl eine neue Verbindung.
- **SD-Karten schonen:** Jeder Schreibzugriff geht über Raft auf die Karten **aller** Pi. Im Leerlauf schreibt
  FestPOS darum nichts regelmässig: Pi-Infos nur bei einer Änderung (Version, Dienste, Sicherung), Druckerstand und
  Temperatur holen sich die Pi direkt beim anderen (`/api/internal/node`); die Aufräumrunde des Spoolers liest
  zuerst. Messen ohne Pi: `python3 tools/schreibzugriffe.py` (zählt die Raft-Einträge in einem echten 3er-Cluster).

**Gemessen im Test (3 echte rqlite-Knoten, Leader hart beendet mitten im Drucken, 5 Läufe):**
Failover 0,8–1,5 s · 0 doppelte Bons · Bestellen läuft nach dem Ausfall weiter ·
Knoten mit **leerer** Karte ist nach < 1 s wieder synchron.

## Betriebsarten

| | Einzelbetrieb (`CLUSTER_SIZE=1`) | Cluster (`CLUSTER_SIZE=3`) |
|---|---|---|
| Hardware | 1 Pi | 3 Pis |
| Adresse .10 | fest auf Pi A | wandert per keepalived zum Leader |
| SD-Karte defekt | Ersatzkarte (Geräte & Netz › Pi › SD-Karte schreiben, oder Raspberry Pi OS + `setup.sh`) + Sicherung einspielen (bringt Schlüssel mit) | Ersatzkarte von einem anderen Pi schreiben lassen (oder Kartenpaket), synchronisiert sich selbst |
| Wechsel | Geräte & Netz › Pi › Auf 3 Pi erweitern: Karten B und C am Pi schreiben (oder Kartenpaket), Pi A umstellen, B und C einstecken – Daten bleiben | – |

## Störfälle

| Ausfall | Verhalten |
|---|---|
| 1 Pi / 1 SD-Karte | Betrieb läuft weiter. Karte tauschen, Pi holt sich die Daten selbst. |
| 2 Pis | Schreiben gesperrt (Schutz vor Split-Brain). Notfall: `festpos-notfall einzelbetrieb`. |
| Handheld ohne WLAN | Bestellen und Kassieren geht weiter, Versand automatisch bei Wiederverbindung. |
| Drucker aus / Papier | Bons warten, Reihenfolge bleibt; andere Drucker laufen weiter. |
| Drucker defekt | Schlange auf anderen Drucker umleiten (Bon wird für dessen Papierbreite neu gerendert). |
| SD-Karte defekt (1 Pi) | Ersatzkarte + neuste Sicherung vom USB-Stick (alle 15 min automatisch, verschlüsselt, Ordner `festpos-sicherung`) mit dem ausgedruckten Sicherungs-Passwort; danach Prüfliste. Siehe `docs/backup-restore.md`. |
| Uhrzeit falsch | Nach der Anmeldung fragt die Verwaltung (Festleitung, Admin) nach: «Zeit stimmt» oder «Uhrzeit dieses Geräts übernehmen» stellt alle Pi (und ihr RTC). Die Handhelds stellen den Pi nicht (T2). Details: `deploy/ANLEITUNG.md`, «Uhrzeit». |

## Projektstruktur

```
app/
  schema.sql        Datenmodell (SQLite = rqlite, keine nicht-deterministischen Defaults)
  store.py          Datenbank-Layer: SQLiteStore (Einzelbetrieb/Tests) | RqliteStore (Cluster)
  escpos.py         ESC/POS-Builder, Raw-TCP-Versand, Statusabfrage (DLE EOT); schweigt ein Drucker, der früher
                    geantwortet hat (printers.status_seen), wird nicht blind gesendet (T15)
  print_service.py  Bon-Layouts (Zeilenabstand, Luft nach jeder Position, Optionen fett), Einzelbon-Logik,
                    cluster-sicherer Spooler, Nachdruck/Umleiten
  orders.py         Bestellung anlegen (ein atomarer Batch, idempotent, mit Prüfsumme und MWST-Satz)
  ledger_base.py    Kassenbuch-Grundlagen: Prüfsummen (HMAC), MWST-Sätze und -Rechnung, Protokoll, Rollen der Handhelds
                    (die Prüfsummen schützen vor Änderungen an der Datenbank, nicht vor dem Admin: er kann
                    CLUSTER_SECRET über eine Sicherung oder das Kartenpaket erhalten)
  stock.py          Ausverkauft und Restmenge: Prüfung beim Bestellen, Stand für Handhelds und Statusanzeige
  ledger.py         Kassenbuch: Zahlart, Storno (Gegenbuchung, Storno-Bon), Tagesabschluss (Kassensturz, Kette,
                    PDF/CSV), Protokoll, MWST-Verwaltung
  tips.py           Trinkgeld: pro Bestellung (bar/Karte/TWINT, Zahlart der Bestellung) und im Payrexx-Betrag;
                    Auswertung für den Tagesabschluss (pro Zahlart, pro Handheld), kein Umsatz
  csv_import.py     Preisliste CSV Import (Vorschau → Übernehmen) / Export, Bestell-Export
  backup.py         Sicherung (.festpos): verschlüsselt mit allem drin, Sicherungs-Passwort, automatische Sicherung
                    auf USB-Stick (Status, Hinweise); auch von den root-Helfern benutzt
  restore_check.py  Prüfliste nach dem Einspielen einer Sicherung (Zertifikate, Drucker, Handhelds, Zeit, Zugangsdaten …)
  cards.py          Kartenpaket für weitere Pi, Pi A auf 3 Pi umstellen, SD-Karte schreiben (T10), Aufträge an
                    festpos-wiederherstellen
  kartenboot.py     Boot-Dateien einer Karte für Pi x (festpos.conf, node.conf, festpos-tls) – Kartenpaket und Karte
  timesync.py       Uhrzeit: Zeitstand pro Pi (RTC, chrony, gestellt), Bestätigung und Fristen, auf alle Knoten verteilen
  time_admin.py     Uhrzeit in der Verwaltung und zwischen den Pi; Zeitserver per USB (Ergebnisse von festpos-handy-usb)
  core.py           Konfiguration, Laufzeit-Objekte, Admin-Anmeldung (Sitzungs-Token), Sperre gegen Raten,
                    Schranke vor dem Lesen von Anfragen (Anmeldung, Grösse)
  auth.py           Rollen (Anzeige < Festleitung < Admin), Regeln Pfad → Mindestrolle, Passwort-Hash (scrypt)
  users.py          Benutzerverwaltung (Admin) und eigenes Passwort
  system.py         Infos pro Pi, Neu starten / Herunterfahren (einzeln oder alle, auf Wunsch mit Handhelds)
  devices.py        Gerätezertifikate der Handhelds: prüfen, sperren, erneuern, ausstellen
  device_ids.py     Geräte-ID statt Name (Zertifikats-Kette, Handhelds), eindeutige Gerätenamen, Migration
  display.py        Bildschirm am Pi (Einstellungen, Abgleich mit festpos-display)
  fully.py          Fully Kiosk: Zertifikat pro Handheld, Abfrage und Befehle nur per HTTPS mit genau diesem Zertifikat;
                    CA und Gerätezertifikat darüber einrichten
  fully_apk.py      Fully-APK (T6): Version aus der APK, Kopie auf alle Pi, auf die Handhelds verteilen und nachliefern,
                    Fully-Version pro Handheld
  uplink.py         Mobilfunk-Router: Treiber pro Modell (heute MikroTik RouterOS 7, REST), einheitliche Werte,
                    Bewertung in Balken, Verlauf; nur HTTPS mit übernommenem Zertifikat, nur lesen
  uplink_admin.py   Verwaltung › Mobilfunk-Router: einrichten, prüfen, Zertifikat übernehmen
  dhcp.py           DHCP für die Handhelds: feste Zuteilungen (MAC -> IP) für dnsmasq, MAC aus der ARP-Tabelle
  mac_change.py     Neue MAC bei bekanntem Zertifikat: feste IP umschreiben, Alarm bei zwei Geräten (T4)
  rawhttp.py        kleiner HTTPS-Baustein für Fully und Router (Fingerabdruck statt Zertifizierungsstelle)
  release.py        Update-Pakete: Signatur (OpenSSH «SSHSIG», Ed25519, auch mehrere in einer .sig), Versionen, ZIP
                    sicher prüfen/entpacken; zurückgezogene Schlüssel (festpos-update-zurueckgezogen.txt)
  update.py         Verwaltung › Update, Plan im Cluster (wer ist dran, Sperre, Mehrheit), Nachziehen, USB-Stick
  update_net.py     Update aus dem Internet: GitHub-Release suchen/herunterladen, Internet kurz ein (festpos-update);
                    «dauerhaft verbunden»: Verbindung nach einem Neustart übernehmen, Prüfung DNS + HTTPS mit Grund,
                    neu verbinden erst nach 3 Fehlversuchen mit wachsender Pause
  status.py         Statusanzeige (ein Aufruf mit allem)
  status_simple.py  Einfache Statusanzeige als fertiges HTML für alte Browser (/status-einfach, iOS 9; ?teil=1 nur Inhalt)
  ledger.py (T45)   Korrekturen früherer Tage (Tabelle corrections): Rückerstattung einer schon abgeschlossenen
                    Bestellung bucht der nächste Abschluss; offene Kartenzahlung sperrt den Abschluss; T46: spät
                    bestätigte Kartenzahlung – extern bezahlt = zu viel, bar = Verwaltung entscheidet (zurück/nachbuchen)
  receipts.py       Quittung für den Gast: Nummer, festgehaltener Inhalt mit Prüfsumme, Kopien, Einzeln pro Person
                    (Prüfsumme Version 2, T41: ganzer gedruckter Inhalt inkl. Empfänger, Zahlart, MWST-Nummer)
  cluster.py        Kurzauskunft über den Raft-Cluster für die anderen Pi
  version.py        liest die Versionsnummer aus der Datei VERSION
  i18n.py + i18n/   Übersetzungen: t(), Sprachwahl pro Anfrage, Standardsprache; i18n/de.json = alle Texte,
                    en/fr/it/rm-vallader.json = Übersetzungen
  catalog.py        Verwaltung › Preisliste: Kategorien, Produkte, Bilder, Logo, Import/Export (CSV, Bilder-ZIP)
  printers.py       Verwaltung › Drucker und Druckaufträge (prüfen, umleiten, nachdrucken, erledigen)
  printer_net.py    Drucker-Netz: Drucker im eigenen VLAN (Einstellung, Datei für festpos-druckernetz, Stand pro Pi)
  tables.py         Verwaltung › Tische und Tischkarten (QR auf dem Bondrucker oder als PDF)
  tabs.py           Offene Tische: Tisch pro Tischnummer, Zahlungen auf dem Server, Rechte, Übergeben, «nicht bezahlt»;
                    Tagesabschluss gesperrt, solange einer offen ist (Zahlart nach den Zahlungen aufgeteilt)
  bon_design.py     Bons & Quittung: Bon-Baukasten (Bausteine, Schema, Vorlagen, alte Form übernehmen), Bon-Logo,
                    Bilder, Vorschau, Probedruck (/api/admin/bon-design)
  bon_preview.py    Vorschau: ESC/POS-Bytes wie ein TM-T20III als PNG zeichnen (Schrift A, Grösse, Raster, QR)
  plan.py           Tischplan: Bereiche mit Grundriss (Bild in images), Lage/Grösse/Drehung/Form der Tische, Beschriftungen;
                    plan_for_phone für /api/bootstrap
  settings_admin.py Einstellungen der Verwaltung (PIN und Test-PIN nur als Hash, Protokolleintrag)
  maintenance.py    Verwaltung › Wartung: Sicherung (herunterladen, einspielen: gleiches System / neuer Pi),
                    Sicherungs-Passwort, Bestellungen zurücksetzen, Übungen löschen
  main.py           FastAPI: Start, Middleware, Kellner-API (Bootstrap, Bestellungen), Anmeldung, Health für keepalived
  setup_mode.py     Ersteinrichtung im Browser (nur ohne festpos.conf, festpos-einrichtung.service, https 443
                    mit vorläufigem Zertifikat über setup_serve.py, Port 80 leitet um):
                    Einrichtungscode, neu / Sicherung / Kartenpaket, schreibt über festpos-wiederherstellen (T11);
                    ca.key nur verschlüsselt (Download oder USB-Stick am Pi)
  zertifikate.py    Server-CA (mit Namensbeschränkung), Geräte-CA und Server-Zertifikat wie make-certs.sh;
                    privaten Schlüssel mit Passwort verschlüsseln (PKCS#8, PBES2, 600 000 Runden)
  usbstick.py       USB-Stick finden und kurz einbinden (root-Helfer, z.B. ca.key auf den Stick)
  emergency.py      Notfall-Passwort als Prüfwert (festpos.conf ADMIN_PASSWORD_HASH, Salz aus CLUSTER_SECRET),
                    Umwandeln beim Start (festpos-node), Mindestlänge 16, nur aus dem FestPOS-Netz
  saison.py         Saison abschliessen: verschlüsseltes Archiv (Abschlüsse, Quittungen, PDF), bestätigen (USB-Stick
                    oder wieder hochladen), danach Quittungen samt Gastdaten löschen
  geheim.py         Daten verschlüsselt: geheime Werte aus festpos.conf («im-container») und Schlüssel im Container,
                    Wiederherstellungs-Schlüssel (Format), gemeinsam für festpos-krypt, festpos-node und Kartenpaket
  krypt.py          Daten verschlüsselt in der Verwaltung: Stand pro Pi, Umstellen, Schlüssel sichern (an den eigenen
                    Helfer oder den betroffenen Pi über /api/internal/krypt)
  umzug.py          Netz umziehen im Betrieb: Plan und Prüfung, neue CA, Ablauf pro Pi (Monitor), Adressen in der
                    Datenbank umrechnen und zurück, Bestätigen nur über die neue Adresse, Handhelds über Fully
  netz.py           FestPOS-Netz: Prüfung und Vorschläge bei der Ersteinrichtung, Beispieladressen für die Verwaltung
  ausgehend.py      Firewall ins Internet: erlaubte Ziele (eine Liste) und nft-Regeln pro Modus (root und App)
  firewall.py       Modus der Firewall in der Verwaltung, Wunsch an festpos-firewall, Stand pro Pi
  ereignisse.py     Sicherheitsereignisse sammeln (root-Teil: Journal, Neustart, Prüfsummen der Boot-Partition)
  sicherheit.py     Sicherheitsereignisse ins Protokoll (alle 10 s), Anmeldesperre, Übersicht bis «Gesehen»
  totp.py           Codes der Authenticator-App (TOTP, RFC 6238) und Wiederherstellungs-Codes
  twofa.py          Zweiter Faktor der Verwaltung (Einrichten, Anmeldung in zwei Schritten, Pflicht pro Rolle) und
                    kritische Aktionen bestätigen (Passwort und Code, 5 Minuten, im Token)
  secretbox.py      Geheimnisse der Einstellungen verschlüsselt (AES-GCM, Schlüssel pro Zweck aus CLUSTER_SECRET);
                    Store.setting entschlüsselt selbst, Klartext älterer Versionen wird umgeschrieben, sobald alle Pi
                    die neue Version haben
  gast.py           QR-Bestellung am Tisch (T22): signierte Tische, Speisekarte, Zahlung bei Payrexx, Bestellung erst
                    nach Bestätigung, automatische Rückerstattung, Storno erstattet zurück, Verwaltung, QR-PDF;
                    Rückerstattungen als Aufträge (gast_refunds, T37): im Batch des Stornos, Timeout = «unklar»,
                    nie von selbst wiederholt; nie auf den offenen Tisch, Preise wie bezahlt, Abbruch
                    erst nach Antwort von Payrexx, bezahlt nach Abbruch = zurück (T38)
  gast_proxy.py     Gästedienst hinter dem Cloudflare Tunnel (eigener Benutzer, Sandbox): Gästeseite und nur die
                    Gäste-Anfragen an FestPOS, Bremse, Sicherheits-Header
  web.py            Webseite & Rechtliches zur QR-Bestellung: Startseite ohne QR-Code, Impressum, AGB, Datenschutz,
                    Rückerstattung (Text oder Verweis), eigenes CSS (streng geprüft, nur diese Seiten), ZIP für Cloudflare Pages
  tunnel.py         Cloudflare Tunnel aus der Verwaltung: Token verschlüsselt für alle Pi, jeder Pi richtet ihn über
                    festpos-update ein (Auftrag «tunnel»); tunnel_token.py prüft den Token (auch für den Helfer)
  payrexx_api.py    Payrexx-REST-API (Gateway, Stand, Rückerstattung), Schlüssel verschlüsselt
  payrexx.py        Karte/TWINT mit NFC über Payrexx (T13): Zahlung mit Einmal-Schlüssel, Startadresse für «FestPOS Tap»,
                    Ergebnis der Payrexx-App prüfen (Schlüssel, Betrag, Bezug), von Hand bestätigen, Übung simuliert,
                    APK «FestPOS Tap» über Fully verteilen; Tagesabschluss mit eigener Zeile;
                    T39: Bestätigen/Abbrechen nur startendes Handy, Tischberechtigte, Festleitung; Bestätigung und
                    Tischzahlung in einem Batch, nie mehr als offen; eine offene Zahlung pro Bestellung/Tisch und Teil
VERSION             die einzige Stelle mit der Versionsnummer (Server, app.js und sw.js lesen sie)
requirements.in     direkte Abhängigkeiten; requirements.txt = alle mit exakter Version und Prüfsumme
deploy/
  ANLEITUNG.md      Schritt für Schritt: Zertifikat, Master-Karte, Klonen, Betrieb
  setup.sh          frisches Raspberry Pi OS Lite 64-bit → FestPOS (ohne festpos.conf: Ersteinrichtung im Browser);
                    im Image-Bau mit FESTPOS_CHROOT=1
  pi-gen/           fertiges Image (T12): config, stage-festpos (ruft setup.sh im chroot), clean-image.sh
                    (karte-ohne.txt), build.sh (lokal und CI: .github/workflows/image.yml), os_list.py (Imager-Liste)
  update.sh         neue Version ohne Internet, mit Probelauf und automatischem Rückfall (--zurueck); liegt
                    auch in /opt/festpos/deploy (Zurück ohne Projektordner)
  install-lib.sh    gemeinsame Schritte von setup.sh und update.sh
  rqlite.env        rqlite für den Pi: Version und Prüfsumme (Python-Pakete und rqlite holt tools/pakete.py
                    beim Bauen ins Update-Paket und Image, nicht im Git)
  festpos-node.sh   bei jedem Boot: Rolle a/b/c anwenden (IP, keepalived, chrony, Firewall); ohne festpos.conf:
                    Ersteinrichtung (Einrichtungscode, DHCP + 192.168.55.10, notfalls eigener DHCP, festpos.local)
  festpos-rqlited   startet die Datenbank: Daten da -> normal, leer -> gründen (nur Einzelbetrieb) oder beitreten
  festpos-notfall   status | einzelbetrieb | neu-beitreten | gruenden
  festpos-redirect  http:// -> https://
  make-certs.sh     eigene CA (mit Namensbeschränkung, ca.key verschlüsselt ausserhalb des Projekts) + Serverzertifikat
  festpos-zertifikate  neue Zertifikate auf einem Pi prüfen und einspielen
  festpos-settime   root-Helfer: Uhrzeit nach Auftrag der App setzen, auch ins RTC (über festpos-settime.path, kein sudo)
  festpos-haertung  root-Helfer (T23, bei jedem Start und Update): SSH nur mit Schlüssel, Schlüssel von der
                    Boot-Partition (festpos-ssh.pub), sudo nur mit Passwort; Bootloader nur von der SD-Karte
                    (EEPROM BOOT_ORDER=0xf1), Bluetooth aus, keine Anmeldung an der Konsole; «--stand» zeigt den
                    Zustand, /run/festpos-haertung.json für die Verwaltung (Geräte & Netz › Pi)
  festpos-firewall  root-Helfer (festpos-firewall.timer jede Minute, festpos-firewall.path bei Änderung): Firewall
                    ins Internet nach dem Modus der Verwaltung (Tabelle inet festpos_out), Namen alle 10 min neu
                    auflösen, Zähler und verworfene Ziele nach /run/festpos/firewall-stand/stand.json
  festpos-os-update root-Helfer: Sicherheitsupdates des Betriebssystems aus dem Update-Paket einspielen (update.sh,
                    ohne Internet), mit Internet nur aus den Sicherheitsquellen (--online), unterbrochenes apt beim
                    Start reparieren (festpos-node); Stand in /var/lib/festpos-os/stand.json
  festpos-krypt     root-Helfer: Daten verschlüsselt (festpos-krypt.service beim Start vor festpos-node, rqlite und
                    FestPOS): Container /var/lib/festpos-krypt/daten.luks öffnen und nach /var/lib/festpos einhängen,
                    Schlüssel aus dem Chip (rpi-otp-private-key), neuen Pi verschlüsseln, Umstellen mit Rückfall
                    (festpos-krypt-pruefen.service), Geheimnisse von der Boot-Partition in den Container; Stand in
                    /run/festpos-krypt/stand.json
  festpos-netz      root-Helfer: Netz umziehen – neue festpos.conf und Zertifikate bereitlegen, zur gemeinsamen Zeit
                    umstellen (rqlite raft/peers.json, Neustart), ohne Bestätigung zurück (festpos-netz.timer)
  festpos-ereignisse root-Helfer (festpos-ereignisse.timer, alle 20 s): SSH-Anmeldung und sudo aus dem Journal,
                    Neustart, geänderte Boot-Partition -> /var/lib/festpos-ereignisse/ereignisse.json (app/ereignisse.py)
  festpos-handy-usb root-Helfer: Handheld mit USB-Debugging am Pi -> Pi als Zeitserver des Handhelds (adb, udev-Regel)
  festpos-dhcp-reload.py  root-Helfer: geänderte feste DHCP-Zuteilungen und das Gateway der Handhelds übernehmen (über festpos-dhcp-reload.path)
  festpos-druckernetz.py  root-Helfer: VLAN-Schnittstelle der Drucker anlegen/löschen (nmcli; bei jedem Start und über
                    festpos-druckernetz.path)
  festpos-display   root-Helfer: Bildschirm-Treiber und Statusanzeige nach der Verwaltung einrichten
  festpos-update    root-Helfer: signierte Updates prüfen und einspielen (Aufträge der App, USB-Stick, Ordner),
                    Internet kurz ein/aus für «Update aus dem Internet» (--netz-aus); Internet-Aufträge über einen
                    eigenen Dienst (festpos-update-netz.path, --anfrage-netz), Stand in update-status/internet.json
  festpos-usb.rules udev: USB-Stick eingesteckt -> festpos-usb@<gerät>.service -> festpos-update --usb
  festpos-sicherung root-Helfer: alle 15 min (festpos-sicherung.timer) verschlüsselt auf einen USB-Stick mit dem
                    Ordner «festpos-sicherung» sichern, die letzten 20 behalten, Fully-APK einmal daneben
  festpos-wiederherstellen  root-Helfer (festpos-wiederherstellen.path): Schlüssel aus einer Sicherung auf die
                    Boot-Partition, Kartenpaket, Pi A auf 3 Pi, Fully-APK vom Stick, Aufträge für festpos-karte
  festpos-karte     root-Programm (festpos-karte.service): neue SD-Karte im USB-Kartenleser schreiben – System
                    kopieren ohne das, was in karte-ohne.txt steht (Datenbank, Leases, Logs, machine-id, SSH-Schlüssel)
  systemd/          festpos-node, rqlited, festpos, festpos-settime, festpos-display, festpos-update …
static/             index.html + app.js und app/ (Bestell-App), admin.html + admin/ (Verwaltung), gast/ (Gästeseite der
                    QR-Bestellung, T22), sw.js (offline), i18n.js (Texte),
                    status.html + status.js (Statusanzeige), fonts/ und vendor/ lokal – funktioniert ohne Internet.
                    Keine Skripte direkt in den Seiten (Content-Security-Policy).
  app/              Bestell-App als klassische Skripte (gemeinsame globale Namen), geladen in der Reihenfolge von
                    index.html: zuerst app.js (Version, Hilfen, Zustand), dann device.js, net.js, view.js, order.js,
                    send.js, pay.js, payment.js, storno.js, lock.js, settings.js, tour.js, sheets.js, scanner.js,
                    input.js, zuletzt start.js. Jede Datei steht auch in sw.js (SHELL) – geprüft von test_app_files.py,
                    offline aus dem Service Worker von test_browser_offline.py.
  admin/            Verwaltung als ES-Module ohne Build-Schritt: main.js (Start, Klick-Handler), core.js (api, Anmeldung,
                    Hilfen), shell.js (Menü, Seiten, Reiter; RENDER), ledger.js (Begriffe des Kassenbuchs) und eine Datei
                    pro Seite oder Bereich (products.js, orders.js, settings.js, update.js, devices.js mit
                    device_add.js «Gerät hinzufügen», internet.js …). Was ein Modul von einem
                    anderen braucht, importiert es; Variablen anderer Module setzt es nur über deren setXyz().
                    tools/js_module_check.py prüft Imports und Exports (Teil von check.sh).
tests/              Unit-, API- und Cluster-Failover-Tests, simulierter Epson-Drucker, update.sh im Testverzeichnis,
                    Browser-Rauchtest mit Playwright (übersprungen, wenn Chromium fehlt – in der CI ein Fehler),
                    Kernpfade der API auch gegen echtes rqlite (test_rqlite_api.py)
tools/              check.sh (alle Prüfungen wie die CI), i18n_check.py (Sprachdateien), sicherung_lesen.py (Sicherung am PC
                    entschlüsseln), js_module_check.py
                    (Imports/Exports der ES-Module),
                    pin-requirements.py (Versionen und Prüfsummen neu erzeugen), pakete.py (Python-Pakete und
                    rqlite für den Pi holen und prüfen), build_zip.py (Update-Paket),
                    schreibzugriffe.py (Raft-Einträge im Leerlauf/Betrieb zählen, echter 3er-Cluster, T9),
                    sd-messung.py (auf dem Pi: wer schreibt wie viel auf die SD-Karte, liest nur),
                    release_notes.py (Release-Text aus CHANGELOG.md),
                    sbom.py (Paketliste CycloneDX für festpos.zip und das Image)
.github/workflows/  ci.yml: ruff, shellcheck, JS-Syntax, Sprachdateien, pytest mit echtem rqlite und Chromium;
                    release.yml (Tag vX.Y.Z: prüfen, Update-Paket, Image, App, SBOM, Herkunftsnachweis,
                    Release-Entwurf); image.yml
                    (SD-Karten-Image, T12), android.yml (Begleit-App festpos-tap.apk, T13)
android/festpos-tap/  Begleit-App «FestPOS Tap» (T13, Java, ohne Bibliotheken): startet die Payrexx-App über deren
                    App-zu-App-Schnittstelle und meldet das Ergebnis an den Pi (POST /api/tap/result)
```

## API (Auszug)

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/api/bootstrap` | Alles fürs Handheld in einem Request: Produkte, Kategorien, Tische |
| POST | `/api/orders` | Bestellung `{client_uuid, table, device, lines:[{product_id, qty}], test?, payment?, no_print?}`; `no_print`: nachgebucht ohne Bon (Einstellung `nachbuchen`, sonst 422); ausverkauft / nicht genug: 422 mit `detail`, `stock_problems:[{product_id, name, left}]` (alle) und `stock` (neuer Stand) |
| GET | `/i18n/{sprache}.json` | Sprachdatei (`auto` = Standardsprache), fehlende Texte aus `de` ergänzt |
| POST | `/api/admin/test-orders/delete?confirm=JA` | Übungsbestellungen aus dem Testmodus löschen |
| POST | `/api/admin/devices/test-exit` | Testmodus beenden (ab Festleitung): `{key, name}` für ein Handheld (Schlüssel aus `/api/status` bzw. `/api/admin/devices` › `test`) oder `{all: true}` |
| POST | `/api/status/test-exit` | Dasselbe am Bildschirm des Pi, nur vom Pi selbst und ohne Anmeldung |
| GET | `/api/status/orders?before=&limit=50`, `/api/status/orders/{id}` | Statusanzeige (T29): Bestellungen seit dem Tagesabschluss, neueste zuerst, seitenweise (`orders, more, count, total_cents`); eine Bestellung mit Positionen, Stornos, Bons pro Drucker, Zahlart, Trinkgeld. Rechte wie `/api/status` |
| POST | `/api/phone/test` | Handheld meldet Beginn/Ende des Testmodus fürs Protokoll: `{on, how}` (`app`, `tour`, `start`, `remote`, `admin`) |
| POST | `/api/time/sync` | Handheld meldet seine Uhr – nur zur Anzeige der Abweichung unter Geräte, stellt den Pi nie (T2) |
| GET | `/api/admin/time` | Uhrzeit (ab Festleitung): Zeitstand aller Pi (Zeit, Zeitzone, RTC-Batterie, Quelle), Bestätigung, Gründe für den Hinweis (`never`, `restart`, `old`, `unsure`, `cluster`), Uhren der Handhelds, Zeitserver per USB |
| POST | `/api/admin/time`, `/time/confirm` | Uhrzeit auf allen Pi setzen: `{epoch_ms}` (dieses Gerät) oder `{local: "2026-10-28T15:49:12"}` (Zeitzone des Pi); «Zeit stimmt» setzt alle Pi auf die Uhr dieses Pi – beides zählt als Bestätigung |
| GET | `/api/admin/time/usb?since=` | Ergebnisse von «Handheld per USB» (Zeitserver gesetzt, Modell, WLAN-Adresse) |
| POST | `/api/admin/fully/adb` | `{ip, on}`: ADB über Fully ein-/ausschalten (Werksreset-Einrichtung, Schritt Zeitserver per USB) |
| GET | `/api/internal/time` | Zeitstand dieses Pi für die anderen (mit `CLUSTER_SECRET`) |
| GET | `/api/internal/node` | Infos dieses Pi (Version, Dienste, Temperatur …) und, wenn er druckt, der Stand der Drucker – für die anderen Pi alle 10 s, statt über die Datenbank (mit `CLUSTER_SECRET`; T9) |
| GET | `/api/health/leader` | 200 nur auf dem druckenden Leader (für keepalived) |
| POST | `/api/admin/import/products?commit=` | CSV hochladen: ohne `commit` nur Vorschau |
| POST | `/api/admin/import/images` | ZIP mit Bildern, Dateiname = Produktname; meldet `matched`, `unmatched`, `failed` (unlesbar) |
| GET | `/api/admin/export/products.csv`, `/orders.csv` | Excel-taugliche Exporte |
| GET/POST/PUT/DELETE | `/api/admin/printers`, `/products`, `/categories`, `/tables` | Verwaltung |
| GET/PUT · POST/DELETE · POST · POST · POST | `/api/admin/bon-design` · `/bon-design/logo` · `/bon-design/image` · `/bon-design/preview` · `/bon-design/test` | Bons & Quittung (nur Admin): `{receipt:{blocks:[{type, on, …}], template}, ticket:{…}, receipt_printer_id}` (GET zusätzlich `schema`, `templates`, Drucker, Lieferzeitpunkte; Kopf/Fusszeile als `text` der Bausteine `header`/`footer`; die alte Form mit festen Schaltern wird noch angenommen), Bon-Logo bzw. Bild (Datei → `{id, url}`), Vorschau `{kind, design:{blocks}, width, delivery}` → PNG mit Kopfzeile `X-Blocks` (wo jeder Baustein steht), Probedruck `{… , printer_id}` direkt auf den Drucker (mit «MUSTER», keine Quittung) |
| GET · POST/PUT/DELETE | `/api/admin/plan` · `/plan/areas`, `/plan/areas/{id}`, `/plan/areas/{id}/image`, `PUT /plan/settings` | Tischplan (ab Festleitung): Bereiche `{name}`, Lage `{tables:[{id, x, y, w, h, rot, shape}], remove, labels, opacity}`, Grundriss (Datei), Startansicht `{start: plan\|number\|off}`; fürs Handheld in `/api/bootstrap` als `plan` |
| PUT | `/api/admin/products/order`, `/api/admin/categories/order` | Reihenfolge per Drag & Drop `{ids: [...]}` (sort = 10, 20, 30 …, im Protokoll) |
| POST | `/api/admin/printers/{id}/test`, `/reroute?to=` | Testdruck, Notfall-Umleitung |
| GET, PUT | `/api/admin/printer-net` | Drucker-Netz (nur Admin): `{vlan, net}` (`vlan` 0 = aus, 2–4094; `net` z.B. `192.168.56`); Antwort mit Adressen der Pi, Stand pro Pi (`pnet`) und Warnungen zu Drucker-Adressen |
| GET/POST | `/api/admin/jobs`, `/jobs/{id}/reprint`, `/resolve` | Druckjobs überwachen |
| GET | `/api/admin/cluster` | Zustand aller Knoten |
| GET | `/api/admin/fully` | Letzte Fully-Abfrage pro Handheld (Akku, Temperatur, Bildschirm, Kiosk, WLAN, Speicher) |
| POST | `/api/admin/fully/cert`, `/check`, `/forget` | `{ip}`: Zertifikat für Fully erstellen (.p12), sofort prüfen, Abfrage abschalten |
| POST | `/api/admin/fully/ca` | `{ip}`: CA des Servers über Fully installieren (Fully holt sie als ZIP mit `loadZipFile`, dann `installUserCa` mit `file://`; ab Festleitung) |
| POST | `/api/admin/fully/device-cert` | `{ip, name}`: Gerätezertifikat ausstellen und über Fully einrichten (als ZIP abholen lassen, `clientCaUrl`/`clientCaPassword` setzen, App neu starten); gleicher Name = erneuern (nur Admin) |
| GET, PUT | `/api/admin/fully/template` | Vorlage der Fully-Einstellungen lesen / als JSON setzen (ohne Geheimes und Gerätebezogenes; nur Admin) |
| PUT | `/api/admin/fully/template/pin` | `{pin, clear?}`: Kiosk-PIN der Vorlage (nie zurückgegeben; Abgleich über `kioskPinEnc`) |
| POST | `/api/admin/fully/template/from` | `{ip}`: Vorlage von diesem Handheld übernehmen (`listSettings`, gefiltert) |
| POST | `/api/admin/fully/settings` | `{ip, apply?}`: Handheld mit der Vorlage vergleichen; mit `apply` die Abweichungen setzen |
| GET, PUT | `/api/admin/fully/provision` | Einstellungen für den Einrichtungs-QR (APK, Prüfsumme, Fully-Code, WLAN; Passwort nur setzen) |
| GET | `/api/admin/fully/provision/qr` | Einrichtungs-QR (Android Enterprise) als SVG |
| GET, POST | `/api/admin/fully/apk` | Fully-APK: Stand (diese Pi, massgebliche `meta`, Stand pro Pi `pis`, Version pro Handheld `phones`, `outdated`; ab Festleitung) / hochladen (nur Admin; Version und Paket aus der APK, Play-Store-APK abgewiesen, Prüfsumme rechnet der Pi, gilt für alle Pi); ausgeliefert als `http://<Pi>/fully/fully.apk` |
| POST | `/api/admin/fully/apk/distribute` | `{phones?}`: APK an alle abweichenden (oder die genannten) Handhelds mit Fully (`loadApkFile`); nicht erreichbare und beschäftigte bleiben offen und werden nachgeliefert (nur Admin) |
| POST | `/api/admin/fully/update-app` | `{ip}`: Fully auf diesem Handheld mit der APK von einem Pi aktualisieren, der sie hat (`loadApkFile`) |
| GET | `/api/internal/fully/apk` | Die Fully-APK dieses Pi für die anderen (mit `CLUSTER_SECRET`; T6) |
| POST | `/api/admin/fully/rollout` | `{name, ip, cert?}`: Einrichtungs-QR für genau dieses Handheld (30 Minuten; Einstellungsdatei `http://<Pi>/fully/<Code>.json`) |
| GET | `/api/admin/fully/rollout/{code}` | Stand: wartet / Einstellungen abgeholt (Adresse, MAC) / abgelaufen |
| GET | `/api/device-cert/fully/settings/{code}` | Einstellungsdatei für Fully beim Einrichten – nur für den Pi selbst (`festpos-redirect.py`) und nur, solange das Einmal-Passwort gilt; merkt die MAC, trägt die feste DHCP-Zuteilung ein |
| POST | `/api/admin/fully/internet` | `{on}`: Internet beim Einrichten über diesen Pi an die Handhelds weitergeben – nur wenn unter «Netz & Internet» erlaubt (`net_phones`; Verbindung von «Update aus dem Internet», Weiterleitung + NAT über `festpos-update`, höchstens 30 Minuten); nur auf dem Pi, der DHCP-Gateway ist |
| POST | `/api/admin/fully/license` | `{ip, volume?}`: Fully-Lizenz prüfen (lizenziert, Geräte-ID); mit `volume` zuerst die hinterlegte Volumenlizenz setzen |
| POST | `/api/admin/fully/bootstrap` | `{ip, name, plain_ok?}`: frisch eingerichtetes Handheld eintragen und ihm das Zertifikat für die Fernverwaltung geben (nur wenn es noch kein HTTPS spricht); mit QR-Code ausgerollt mit dem Einmal-Passwort und danach über HTTPS auf das echte umgestellt, sonst einmal unverschlüsselt nur mit `plain_ok` |
| GET | `/api/device-cert/fully/{code}` | Einmal-ZIP für Fully – nur für den Pi selbst; `festpos-redirect.py` reicht sie als `http://<Pi>/fully/<code>.zip` weiter |
| POST | `/api/admin/fully/whitelist` | `{ip}`: URL Whitelist von Fully auf die Adresse der Bestell-App setzen (aus der Start-URL; ersetzt die bisherige), danach nachlesen. Geprüft wird sie alle 15 Minuten und mit `/check` (`web` in den Ergebnissen: `ok`, `off`, `missing`, `wide`, `unknown`) |
| POST | `/api/admin/fully/command` | `{ip, cmd, value?}`: `restartApp`, `rebootDevice`, `shutdownDevice`, `screenOff`, `screenOn`, `lockKiosk` (ist der Kiosk-Modus ganz aus, zuerst `kioskMode` ein), `unlockKiosk`, `loadStartURL`, `clearCache` (danach neu laden), `toForeground`, `brightness` mit `value` 0–255 an ein Handheld (Recht «Handhelds und Fully») |
| GET/PUT | `/api/admin/uplink` | Mobilfunk-Router: Einstellung (ohne Passwort), Modelle, Einrichtungsbefehle, letzte Abfrage; ändern nur Admin |
| POST | `/api/admin/uplink/check`, `/trust` | Router jetzt abfragen (ab Festleitung); `{pin}`: gezeigtes Zertifikat übernehmen (Admin) |
| POST | `/api/admin/uplink/active` | `{active}`: Router im Einsatz ein/aus (Admin) – aus: nicht abgefragt, nicht auf der Statusanzeige |
| GET | `/api/admin/system` | Alle Pi mit Modell, OS, Temperatur, Speicher, Laufzeit, Version; Anzahl Handhelds mit Fully |
| GET | `/api/admin/update` | Update: Pi mit Version/Sicherung/Fortschritt, Schlüssel, letztes Paket, Plan, Warnungen (Admin) |
| POST | `/api/admin/update/upload` | `bundle` + `sig` (festpos.zip, festpos.zip.sig): prüfen und ablegen (Admin) |
| POST | `/api/admin/update/start`, `/rollback`, `/cancel`, `/retry` | `{sha, confirm}` verteilen; alle zurück `{confirm}`; abbrechen; erneut versuchen |
| GET, PUT | `/api/admin/update/net` | Aus dem Internet: Einstellungen (Repo, Token gekürzt, Verbindung), Geräte, letzter Vorgang, `allowed` (Schalter `net_updates`; Admin) |
| POST | `/api/admin/update/net/check`, `/download` | Neusten Release suchen; herunterladen und prüfen (läuft im Hintergrund) |
| GET | `/api/internal/update/{sha}.zip`, `.sig` | Für die anderen Pi: Paket abholen (nur mit `CLUSTER_SECRET`) |
| POST | `/api/internal/revoked` | Von einem anderen Pi nach Sperren/Entsperren: Sperrliste sofort neu lesen (nur mit `CLUSTER_SECRET`; T3) |
| POST | `/api/admin/backup` | `{password}` (mind. 12 Zeichen): Sicherung `.festpos` herunterladen – verschlüsselt, mit allem drin (Admin) |
| GET | `/api/admin/backup/password` | Sicherungs-Passwort: Anfang, erzeugt, ausgedruckt; `?show=1` ganz mit QR-Code (Protokoll) |
| POST | `/api/admin/backup/password/printed`, `/new`, `/bon` | Als ausgedruckt bestätigen; neues erzeugen (`?confirm=JA`); `{printer_id}`: als Bon direkt an den Drucker |
| POST | `/api/admin/restore?confirm=JA` | `file` (+ `password`): einspielen; ohne Passwort zuerst das eigene – 428, wenn keines passt. Antwort mit `case` (same/new), `keys`, `restart` |
| POST | `/api/admin/restore/keys` | `file` + `password`: Schlüssel aus der Sicherung übernehmen (Einzel-Pi, Pi startet neu) |
| GET | `/api/admin/restore-check` | Prüfliste nach dem Einspielen; POST `/done`, `/receipt-next {next}`, `/apk` |
| GET | `/api/admin/cluster/expand` | Prüfungen für «Auf 3 Pi erweitern»; POST `?confirm=JA`: Pi A auf 3 Pi umstellen (startet neu) |
| POST | `/api/admin/cluster/kartenpaket` | `{letters}`: Kartenpaket (ZIP, unverschlüsselt) für weitere Karten |
| GET | `/api/admin/cluster/karten` | Kartenleser und Karten (erlaubt / Grund), erlaubte Buchstaben, Stand des Schreibens, geschriebene Karten |
| GET/POST | `/api/admin/cluster/karte` | Stand des Schreibens; POST `{device, serial, size, letter, confirm}` startet das Schreiben (confirm = Buchstabe) |
| POST | `/api/admin/cluster/karte/abbrechen` | Schreiben abbrechen (Karte wird unbrauchbar gemacht) |
| POST | `/api/admin/system/power` | `{action: reboot\|poweroff, node: all\|a\|b\|c, phones: none\|restart_app\|reboot\|shutdown\|screen_off}` |
| GET/POST/PUT/DELETE | `/api/admin/users`, `/users/{id}` | Benutzer anlegen, Rolle/aktiv/Passwort ändern, löschen (nur Admin); `GET` mit allen Rollen und Rechten; der letzte aktive Admin bleibt (422) |
| POST/PUT/DELETE | `/api/admin/roles`, `/roles/{id}` | Eigene Rolle `{name, perms}` anlegen, ändern, löschen (nur Admin; feste Rollen 422, Rolle mit Benutzern 409) – gespeichert in der Einstellung `user_roles` |
| GET/POST | `/api/admin/me`, `/me/password`, `/me/logout-all`, `/me/refresh` | Eigener Name und Rolle, eigenes Passwort ändern, überall abmelden, Sitzung nach Bedienung verlängern |
| GET | `/api/admin/lamp` | Ampel der Seitenleiste (jede Rolle): `{level: ok\|wait\|bad, bad: [...], wait: [...]}`, Einträge `{key, name}` bzw. `{key, pi}` (Schlüssel `status.*` aus der Sprachdatei) |
| GET | `/api/logo` | Öffentlich: `{logo: "/media/…" oder null, logo_plate}` (auch in `/api/bootstrap` und `/api/status`) |
| GET | `/api/nav` | Öffentlich: `{phone, phone_port}` – für die Links zwischen Bestell-App, Verwaltung und Statusanzeige |
| GET/POST | `/api/tabs`, `/api/tabs/{id}`, `/{id}/pay`, `/{id}/undo`, `/{id}/close` | Handheld: offene Tische (Stand, darf ich abrechnen), Zahlung `{key, amount_cents, payment, tip_cents, part, items}`, rückgängig `{key}`, schliessen |
| POST | `/api/tabs/{id}/storno` | Handheld mit Storno-Rolle und Recht am Tisch: `{client_uuid, lines:[{id, qty}], reason, note}` – `id` = zusammengefasste Position aus `/api/tabs/{id}` (ganz Storniertes bleibt mit `qty` 0 und `cancelled`, die Nummern ändern sich nie); gebucht auf die Bestellungen dahinter, neueste zuerst; nicht über «Einzeln» Bezahltes oder mehr als offen |
| GET/POST | `/api/admin/tabs`, `/tabs/{id}`, `/{id}/settle`, `/{id}/transfer`, `/{id}/unpaid` | Verwaltung (ab Festleitung): offene Tische, Rest abrechnen `{payment}`, übergeben `{device_id}`, nicht bezahlt `{note}` |
| POST | `/api/orders/status` | Handheld: `{uuids: [Kennung …]}` (höchstens 60) → `{states: {Kennung: open\|storno\|tab_done\|closed\|gone}}` – Stand für den Verlauf, nie der Inhalt |
| POST | `/api/orders/payment` | Handheld: `{client_uuid, payment: cash\|card\|twint\|other}` – Zahlart nachreichen |
| POST | `/api/orders/tip` | Handheld: `{client_uuid, tip_cents}` – Trinkgeld der Bestellung (Summe ohne Payrexx, ersetzt den alten Wert) |
| POST/GET | `/api/phone/payrexx`, `/payrexx/{id}` | Handheld (T13): `{client_uuid, amount_cents, tip_cents, part: all\|pN\|sN, key}` → Zahlung mit `launch` (intent:// für «FestPOS Tap»); Stand abfragen; `/cancel`, `/manual` (Payrexx zeigte «bezahlt») |
| GET/POST | `/api/gast/menu`, `/api/gast/order`, `/order/{beleg}`, `/order/{beleg}/cancel`, `/webhook` | Gästedienst (T22, eigener Schlüssel): Speisekarte eines signierten Tisches, Bestellung `{t, s, key, lang, lines, tip_cents}` → Payrexx-Link, Stand |
| GET/PUT | `/api/admin/gast` | Admin: QR-Bestellung `{open, domain, instance, key?, max_cents, refund_min}`; `POST /gast/test`, `GET /gast/qr.pdf`, `GET /gast/orders`, `POST /gast/orders/{id}/refund` |
| POST | `/api/tap/result` | «FestPOS Tap» (auch Verwaltungs-Port, ohne Gerätezertifikat): `{id, token, ok, result, message}` – Ergebnis der Payrexx-App |
| GET/PUT | `/api/admin/payrexx` | Admin: `{enabled, method: ""\|CONTACTLESS\|TWINT, show_result}`; `POST /payrexx/apk` (festpos-tap.apk), `POST /payrexx/apk/install` (über Fully, im Hintergrund: Installationssperren kurz aus, Ergebnis per `getInstallApkState`; `{ip}` = nur dieses Handheld; Stand pro Handheld in GET › `sent`: installing, dann installed/sent/failed), `GET /payrexx/payments`; `POST /payrexx/payments/{id}/cancel` (offene Kartenzahlung abbrechen, T45) und `POST /payrexx/payments/{id}/late/{refund\|book}` (nach dem Abschluss bestätigt, Bestellung bar: zurückerstattet oder nachbuchen, T46) – beide ab Festleitung |
| GET | `/api/orders/lines?uuid=` | Handheld mit Storno-Rolle: Positionen einer Bestellung mit schon Storniertem |
| POST | `/api/orders/storno` | Handheld mit Storno-Rolle: `{client_uuid, order_uuid, lines:[{line_no, qty}] oder null, reason, note}` |
| GET | `/api/phone/status` | Status-Punkt der Bestell-App: Drucker (`ok`/`rerouted`/`down`, Grund, Ziel, wartend, `paper_low` – nur Festleitung zeigt es), Quittungen eingerichtet, `stock: {soldout:[ids], stock:{id: n}, warn:{id: n}}`, `test_exit: true`, wenn der Testmodus von aussen beendet wurde |
| GET/POST | `/api/device/cert`, `/api/device/renew` | Handheld: Zustand des eigenen Zertifikats (`renew_due`, `revoked`, `auto` = der Pi erneuert selbst, `fully`; auch gesperrt erreichbar) bzw. neues holen (Download, Einmal-Kennwort) |
| POST | `/api/phone/products/{id}/soldout` | Handheld mit Rolle Festleitung: `{soldout}` – freigeben setzt Restmenge 0 auf „ohne Bestand“ |
| POST | `/api/orders/payments` | Handheld: `{client_uuid, payments: [{key, amount_cents, payment, tip_cents, person, items}]}` – alle Teilzahlungen einer sofort kassierten Bestellung; fehlende gelten als zurückgenommen (bleiben mit `undone_at`), zusammen höchstens das Total, abgeschlossen 409 |
| POST | `/api/phone/products/{id}/stock` | Handheld mit Rolle Festleitung: `{stock}` – Restmenge (0 = ausverkauft, `null` = ohne Bestand) |
| POST | `/api/orders/receipt` | Handheld: Quittung `{order_uuid, request_id, items?:[{product_id, qty, options}], part?, copy_of?}` |
| GET | `/api/admin/orders?scope=open\|all&q=`, `/orders/{id}` | Bestellungen mit Storno und Zahlart (ab Anzeige) |
| POST/PUT | `/api/admin/orders/{id}/storno`, `/orders/{id}/payment` | Stornieren, Zahlart ändern (ab Festleitung) |
| POST | `/api/admin/orders/{id}/receipt` | Quittung `{request_id, copy_of?, recipient?}` – noch nicht Quittiertes, sonst Kopien (ab Festleitung) |
| GET/POST | `/api/admin/closings/open`, `/closings` | Offener Zeitraum, Tagesabschluss erstellen `{float_cents, counted_cents, card_cents, twint_cents, other_cents, tip_out_cents, note}` |
| GET | `/api/admin/closings/{id}/pdf`, `/csv`, `/verify` | Abschluss als PDF/CSV, Prüfsumme und Kette prüfen |
| GET | `/api/admin/audit?q=` | Protokoll (ab Festleitung) |
| GET/PUT/POST/DELETE | `/api/admin/vat`, `/vat/rates`, `/vat/rates/{id}` | MWST ein/aus, MWST-Nr., Sätze mit „gültig ab“ (ändern nur Admin) |
| PUT | `/api/admin/products/{id}/vat`, `/api/admin/devices/role` | MWST eines Produkts (Admin); Rolle eines Geräts `{device, role: service\|storno\|lead}` – `device` = Geräte-ID (Zertifikat) oder ID des Handhelds; alte Form `{name, role}` (ab Festleitung) |
| GET | `/api/admin/devices` | Reiter «Handhelds & PC»: Gerätezertifikate mit `device_id`, Rollen pro Geräte-ID, letzte Meldung pro Gerät (`last_seen`) und pro IP (`last_seen_ip`, Sekunden), `renew_days` und `renew` pro Geräte-ID (`mode`: `auto`/`manual`/`failed`, `state`: `running`/`ok`/`retry`/`failed`, `step`, `detail`; T3) (ab Festleitung) |
| POST | `/api/admin/devices/cert` | `{name, renew?, device?, phone?, role?}`: Gerätezertifikat (QR + Einmal-Kennwort). Name unter den gültigen Geräten eindeutig (sonst 409). `renew`/`device`: neues Zertifikat für dieses Gerät (auch mit neuem Namen = umbenennen), das alte sperrt der Server beim ersten Gebrauch des neuen; `phone`: gehört zu diesem Handheld (ab Festleitung) |
| POST | `/api/admin/devices/{id}/revoke?undo=` | Sperren ab Festleitung, Entsperren (`undo=true`) nur Admin |
| POST | `/api/admin/devices/{id}/remove` | Gesperrtes Gerät endgültig löschen (nur Admin; 409, solange ein Zertifikat gilt): verschwindet aus allen Listen samt Handheld-Eintrag (feste IP, MAC), die Sperre bleibt (`removed_at`) |
| POST | `/api/admin/devices/mac` | `{device, action: take\|keep, mac}`: MAC-Wechsel von Hand entscheiden (T4) – neue MAC übernehmen (feste IP umschreiben) oder alte behalten (die andere 24 Stunden lang nicht mehr melden); 409, wenn schon entschieden oder die MAC einem anderen Handheld gehört; ab Festleitung |
| PUT | `/api/admin/devices/phone` | `{id?, name, ip, active, cert?, role?}` (alte Form `old` = bisheriger Name): Handheld auf der Statusanzeige eintragen/ändern (Name unter allen gültigen Geräten, IP unter den Handhelds eindeutig; Fully-Zertifikat und MAC bleiben, neue IP: Fully neu einrichten; `ip` leer: von der Statusanzeige nehmen; ab Festleitung) |
| GET | `/api/admin/devices/wifi-qr` | WLAN-QR (SVG) für das FestPOS-WLAN aus Geräte & Netz › Einstellungen – Passwort nur im Bild (ab Festleitung) |
| POST | `/api/admin/fully/prepare` | `{name}`: «Fully auf vorhandenem Handheld» – Einmal-Passwort für die Fernverwaltung; `bootstrap` mit `token` stellt danach über HTTPS auf das echte um (ab Festleitung) |
| PUT | `/api/admin/products/{id}/stock` | Bestand `{soldout, stock (null = ohne Bestand), stock_warn}` (ab Festleitung) |
| POST/DELETE | `/api/admin/logo` | Logo hochladen (Datei, wird verkleinert) / entfernen – nur Admin |

Anmeldung der Verwaltung: `POST /api/admin/login` mit `{"name": …, "password": …}` liefert ein Sitzungs-Token
(höchstens 12 Stunden) mit Name und Rolle, danach `Authorization: Bearer <token>`. Ohne Bedienung endet die Sitzung
nach `admin_idle_minutes` (Einstellung, 10–120, Standard 30; Antwort: `idle_in` Sekunden); `POST /api/admin/me/refresh`
gibt nach Bedienung ein neues Token mit neuer Frist und gleichem Ablauf. Rolle Anzeige ohne diese Frist (Statusanzeige).
Eine abgelaufene Frist zählt nicht als Fehlversuch. Name leer oder `admin` = Notfall-Login mit
dem Passwort aus festpos.conf (gesperrt, solange dort der Platzhalter steht). `POST /api/admin/me/logout-all` beendet
alle Sitzungen des eigenen Kontos. Fehlversuche zählen pro Adresse und Name: eine richtige Anmeldung löscht nur die
eigenen; nach 30 Fehlversuchen für einen Namen (alle Adressen zusammen, 5 Minuten) geht dieser Name nur noch von
Adressen, die sich damit in den letzten 12 Stunden richtig angemeldet haben. Mit `ADMIN_FROM` (festpos.conf) geht die
Verwaltung auf Port 8443 nur von diesen Adressen. Basic Auth (Name + Passwort) geht weiterhin, z.B. für curl; eine
schreibende Anfrage mit Basic Auth, die ein Browser von einer fremden Seite aus schickt (`Sec-Fetch-Site`/`Origin`),
wird abgewiesen (CSRF). `GET /api/admin/settings` (jede Rolle) gibt nie ein Geheimnis zurück (`backup.SECRET_KEYS`).
Welche Rechte ein Pfad braucht, steht an einer Stelle in `app/auth.py` (nicht aufgeführte Pfade: nur Admin);
`/api/admin/me` und die Anmeldung liefern dazu `perms` und den Namen einer eigenen Rolle.

**Zweiter Faktor und kritische Aktionen** (`app/twofa.py`, `app/totp.py`): Hat ein Benutzer einen zweiten Faktor
(TOTP, RFC 6238, 6 Ziffern, 30 s, ±1 Schritt, jeder Code einmal), antwortet `POST /api/admin/login` mit
`{"second_step": true, "pending": …}` statt eines Tokens; `POST /api/admin/login/totp` `{pending, code}` (Code aus der
App oder Wiederherstellungs-Code; `pending` gilt 5 Minuten, danach 409) liefert das Token. Einrichten:
`POST /api/admin/me/totp/start` (QR als SVG, Schlüssel), `/me/totp/confirm` `{code}` (gibt 10
Wiederherstellungs-Codes zurück, gespeichert nur als HMAC), `/me/totp/later` (Pflicht: dreimal), `/me/totp/off` und
`/me/totp/codes` `{password, code}`; `GET /api/admin/me` enthält `totp` (`on`, `required`, `later`, `blocked`, `codes`,
`role_required`). Pflicht pro Rolle: Einstellung `totp_required_roles` (JSON-Liste, Standard `["admin"]`); ist die
Pflicht offen und kein «Später» mehr übrig, antwortet jede andere Anfrage 403 `{"code": "totp_setup"}`. Das Geheimnis
liegt verschlüsselt in `users.totp` (secretbox). Ein Admin setzt es mit `PUT /api/admin/users/{id}` `{"totp_reset": true}`
zurück. Kritische Aktionen (`auth.CRITICAL`, Einstellungen `auth.CRITICAL_SETTINGS`) brauchen ein bestätigtes Token:
ohne antwortet der Server 428 `{"code": "confirm_needed"}`; `POST /api/admin/me/confirm` `{password, code}` gibt ein
neues Token, das 5 Minuten als bestätigt gilt (im signierten Token, auf allen Pi gleich, bleibt beim Verlängern).
Basic Auth gilt als bestätigt, ausser der Benutzer hat einen zweiten Faktor – dann nur lesen.

**Sicherheitsereignisse** (`app/sicherheit.py`, ab Recht «Protokoll»): `GET /api/admin/security` – Ereignisse seit dem
letzten «Gesehen» (`count`, `events`, `last`); `POST /api/admin/security/seen?upto=<id>` blendet sie für alle aus.
Im Protokoll stehen sie als `sec_ssh`, `sec_sudo`, `sec_sudo_failed`, `sec_reboot`, `sec_boot_changed`,
`sec_update_foreign`, `sec_lockout` (dazu `emergency_login`); `GET /api/admin/audit?sec=true` zeigt nur diese.

**Saison abschliessen** (`app/saison.py`, nur Admin): `GET /api/admin/season` (offene Abschlüsse, Archiv in Arbeit,
letzte Saison, Erinnerung), `POST /api/admin/season/archive` `{password}`, `GET /api/admin/season/archive` (Download),
`POST /api/admin/season/usb` (Stick, zurückgelesen), `POST /api/admin/season/verify` (Datei und Passwort: prüfen,
bestätigt bei gleicher SHA-256), `POST /api/admin/season/close` `{sha}` (kritisch: löscht die Quittungen der
archivierten Abschlüsse), `DELETE /api/admin/season/archive`. Am PC: `tools/archiv.py`.

**Betriebssystem** (T-0-N/FestPOS#5): Beim Release holt `tools/os_updates.py` in einer leeren apt-Umgebung (arm64,
nur Debian bookworm, bookworm-updates, bookworm-security und Raspberry Pi, signed-by) die Updates für die Pakete aus
`deploy/os-pakete.txt` (Paketliste des Images, `festpos-pakete.txt` vom Image-Bau; `release.cmd` übernimmt sie nach jedem Release) und `build_zip.py --os-updates`
legt sie als `os-updates/` ins Paket. Ohne `deploy/os-pakete.txt` entsteht das Paket wie bisher. Stand pro Pi:
`/api/admin/system` › `info.osup` (Datum, Ergebnis, Kernel, Neustart nötig); `POST /api/admin/os/online` (kritisch)
holt die Sicherheitsupdates mit Internet.

**Netz** (T-0-N/FestPOS#2, `app/netz.py`): `festpos.conf` hat `NET=Adresse/Grösse` (z.B. `10.20.0.0/22`, bei der
Ersteinrichtung /16 bis /27); ältere Dateien mit `NET_PREFIX`/`NET_CIDR` gehen weiter (`festpos_net_from_conf` in
`deploy/festpos-conf.sh`). Firewall, DHCP (`DHCP_POOL` mit ganzen Adressen oder Abstand zur Netzadresse), chrony,
`make-certs.sh` und die Zertifizierungsstelle rechnen mit `NET`. `GET /api/admin/netinfo` gibt Netz, VIP und
Beispieladressen für die Verwaltung (statt fester 192.168.55.x).

**Netz umziehen** (T-0-N/FestPOS#2, `app/umzug.py`, nur Admin): `GET /api/admin/umzug` (Zustand, Stand pro Pi),
`POST /api/admin/umzug/pruefen` `{net, vip, a, b, c, dhcp_from, dhcp_to, gw?}` (Fehler, Hindernisse, Checkliste;
`gw` = Gateway, DNS, Zeitserver im neuen Netz, sonst die jetzigen umgerechnet),
`POST /api/admin/umzug/start` (kritisch), `POST /api/admin/umzug/bestaetigen` (kritisch, nur über eine Adresse im neuen
Netz), `POST /api/admin/umzug/zurueck` (kritisch), `POST /api/admin/umzug/ca-key` `{password}` (kritisch, neuer ca.key
verschlüsselt, danach gelöscht), `GET /api/admin/umzug/ca.crt`. Übergangszeit: `festpos.conf` hat `NET_ALT`, `VIP_ALT`,
`NODE_x_IP_ALT`, `NET_ALT_BIS`; `app/serve.py` lauscht dann pro Adresse (`FESTPOS_LISTEN`, `FESTPOS_LISTEN_ALT`,
`FESTPOS_TLS_ALT_CERT`).

**Gateway, DNS & Zeitserver** (`app/gateway.py`, nur Admin): `GET /api/admin/gateway` (was gilt, Stand pro Pi),
`POST /api/admin/gateway/pruefen` `{mode, gateway, dns, ntp}` (Fehler; antworten Gateway, DNS, Zeitserver?),
`PUT /api/admin/gateway` (kritisch). `mode`: `auto` (wie bisher), `fest` (Router für Pi und Handhelds), `ohne`
(geschlossenes Netz). Massgebend ist `festpos.conf` (`GATEWAY`, `DNS`, `NTP`) – gilt beim Start; die Datenbank hält
den gewünschten Stand mit Nummer, jeder Pi lässt ihn von `festpos-netz --gateway` (root) schreiben und ohne Neustart
übernehmen (NetworkManager, DHCP, chrony, Firewall).

**Daten verschlüsselt** (T-0-N/FestPOS#3, `app/krypt.py`, nur Admin): Stand pro Pi in `/api/admin/system` ›
`info.krypt` (`state`: `aus`, `vorlaeufig`, `aktiv`, `kein_chip`, `umstellen`, `gesperrt`; `chip`, `offen`,
`bestaetigt`, `error`). `POST /api/admin/krypt/schluessel` gibt einen neuen Wiederherstellungs-Schlüssel;
`POST /api/admin/krypt/{pi}/umstellen` `{recovery, printed}`, `GET /api/admin/krypt/{pi}/offen` (Schlüssel eines neuen
Pi) und `POST /api/admin/krypt/{pi}/bestaetigen` `{printed}` sind kritisch. Für einen anderen Pi geht der Auftrag über
`POST /api/internal/krypt` (CLUSTER_SECRET, nur an Adressen aus `FESTPOS_PEERS`).

**Firewall ins Internet** (`app/firewall.py`, nur Admin): `GET /api/admin/firewall` – Modus (`strict`, `watch`,
`open`), `until`, erlaubte Ziele; `PUT /api/admin/firewall` `{mode}` (kritisch). `watch`/`open` gelten 24 Stunden.
Der Stand pro Pi (angewendeter Modus, Zähler `unknown`, zuletzt verworfene Ziele) steht in `/api/admin/system` unter
`info.fw`.

**Verwaltung nur mit Gerätezertifikat** (Benutzer › Zugriff, Einstellung `admin_cert_only`): Port 8443 fragt beim
TLS-Aufbau nach einem Gerätezertifikat (freiwillig – die Pi untereinander haben keines). Eingeschaltet weist der Server
`/api/admin/…` (auch die Anmeldung) ohne gültiges, nicht gesperrtes Zertifikat ab; das Passwort braucht es weiterhin.
Am PC: Zertifikat aus Geräte & Netz › Handhelds & PC (Art «Nur Zugang») im Browser installieren. Einschalten geht nur
von einer Verbindung mit Zertifikat aus; der Pi selbst (localhost, z.B. SSH-Tunnel) bleibt der Notweg.

Vollständige API-Beschreibung nur auf Wunsch (REV-041): `FESTPOS_API_DOCS=1` in der Umgebung, dann
`https://192.168.55.10:8443/api/docs`.

## CSV-Preisliste

```
name;preis;kategorie;drucker;einzelbon;sort;aktiv
Bier 5dl;6.50;Getränke;Getränkeausgabe;0;1;1
Bratwurst mit Bürli;8,00;Essen;Küche;ja;1;1
```
Excel-tauglich (Semikolon oder Komma, UTF-8 oder Windows-Umlaute, `6.50` / `6,50` / `6.–`).
Abgleich über den Namen; Option „fehlende Produkte deaktivieren“ für eine komplett neue Liste.
Vorlage: `deploy/preisliste-vorlage.csv`

## Entwicklung

```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements.txt     # dieselben Versionen wie auf dem Pi
pip install -r requirements-dev.txt                  # pytest, httpx2, ruff
tools/check.sh                           # ruff, shellcheck, Sprachdateien, JS-Syntax und -Module, pytest (wie die CI)
tools/check.sh --schnell                 # ohne Cluster-/rqlite-Tests (die brauchen rqlited im PATH oder RQLITED=…)
python -m tests.fake_printer 9100        # simulierter Drucker, zeigt Bons als Text
FESTPOS_DB=sqlite:///./dev.db FESTPOS_ADMIN_PASSWORD=entwicklung-lokal uvicorn app.main:app --reload
```

Einrichten in einem Schritt: `tools/setup-dev.sh` (Python-Pakete, shellcheck, rqlited, Chromium für die
Browser-Tests). Texte für alle Sprachen eintragen: `python3 tools/i18n_add.py texte.json`. Update-Paket bauen:
`python3 tools/build_zip.py` → `dist/festpos.zip` (bricht bei Windows-Zeilenenden ab; unter Windows mit
`git clone -c core.autocrlf=false …` klonen, siehe `.gitattributes`).

**Release:** Ein Tag `vX.Y.Z` startet `.github/workflows/release.yml`: alle Prüfungen, dann Update-Paket (mit
Python-Paketen und rqlite), Image (ARM-Runner) und App «FestPOS Tap»; daraus wird ein Release-**Entwurf** mit
`SHA256SUMS` und dem Text aus `CHANGELOG.md` (`tools/release_notes.py`). Veröffentlicht wird von Hand, nachdem
`festpos.zip` signiert und `festpos.zip.sig` angehängt ist – die CI hat keinen Update-Schlüssel.

**Neue Version:** die Datei `VERSION` und `CHANGELOG.md`. **Pakete aktualisieren:** `requirements.in` anpassen,
`python3 tools/pin-requirements.py` (braucht Internet), dann `tools/check.sh`; die Pakete für den Pi holt
`tools/pakete.py` beim Bauen. **rqlite:** Version und Prüfsumme in `deploy/rqlite.env` (gleiche Hauptversion). **cloudflared** (Tunnel zur
QR-Bestellung) kommt nicht mit FestPOS: jeder Pi installiert es selbst aus der Paketquelle von Cloudflare, sobald
der Tunnel an ist (`festpos-update`, Auftrag «cloudflared»), und aktualisiert es einmal pro Woche.
