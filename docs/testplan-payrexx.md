# FestPOS – Testplan Payrexx (Karte/TWINT)

**Stand Version 1.0: ungetestet.** Die Zahlung über Payrexx ist fertig umgesetzt und durch automatische Tests
abgedeckt, lief aber noch nie gegen ein echtes Payrexx-Konto. Betroffen sind:

- Karte/TWINT mit NFC am Handheld («Payrexx Tap to Pay» und die Begleit-App «FestPOS Tap», `android/festpos-tap`)
- QR-Bestellung am Tisch (Gäste zahlen auf dem eigenen Handy, Cloudflare Tunnel)
- Rückerstattungen, Trinkgeld mit Karte/TWINT und die Payrexx-Zeilen im Tagesabschluss

Im Code sind alle Stellen mit `PAYREXX-UNGETESTET` markiert. Solange `PAYREXX_GEPRUEFT` in `app/payrexx_api.py`
`False` ist, zeigt die Verwaltung auf den Payrexx-Karten einen Hinweis und fragt beim Einschalten nach.

**Wenn alle Punkte bestanden sind:** `PAYREXX_GEPRUEFT = True` setzen, die Markierungen `PAYREXX-UNGETESTET` entfernen,
den Abschnitt «Bekannte Einschränkungen» in `README.md` und `CHANGELOG.md` nachführen, neue Version (MINOR).

## Material
- Payrexx-Konto im **Testmodus** mit API-Schlüssel (Payrexx › Integrationen › API & Plugins), Testkarte bzw. Handy mit
  TWINT-Test.
- Zwei Handhelds (XCover 1 und 2) mit «Payrexx Tap to Pay» aus dem Play Store, mit dem Testkonto angemeldet, NFC an,
  Internet am Handheld (SIM oder Mobilfunk-Router).
- `festpos-tap.apk` vom Release (oder aus der CI, Workflow «Release», Artefakt).
- Für die QR-Bestellung: Cloudflare-Konto mit eigener Domain, Tunnel wie in `deploy/ANLEITUNG.md`
  («QR-Bestellung am Tisch»), Internet für den Pi (Verwaltung › Geräte & Netz › Netz & Internet, Schalter c).

## A – Karte/TWINT mit NFC am Handheld
- [ ] **A1 App verteilen:** Verwaltung › Kasse › Karte/TWINT mit NFC: `festpos-tap.apk` hochladen → «Version …»;
      eine andere APK (z.B. Fully) → abgewiesen. «Auf die Handys installieren» → pro Handy «Auftrag geschickt», nach
      einer Minute ist «FestPOS Tap» installiert. Protokoll: hochgeladen / an die Handys geschickt.
- [ ] **A2 Hinweis:** Auf der Karte steht «Noch nicht mit einem echten Payrexx-Konto getestet»; beim Einschalten kommt
      die Rückfrage, «Abbrechen» lässt den Schalter aus.
- [ ] **A3 Ganze Bestellung:** «In der Bestell-App anbieten» ein, Zahlmittel leer. Kasse › Karte/TWINT (NFC) → die
      Payrexx-App öffnet sich mit dem richtigen Betrag; Testkarte an die Rückseite → zurück in der Bestell-App,
      «Bezahlt ✓», Zeile «Karte/TWINT (NFC) bezahlt ✓»; im Payrexx-Konto mit Referenz «FestPOS-…».
- [ ] **A4 Einzeln:** Person 1 mit Karte, Person 2 bar. **Teilen:** 3 Personen, zwei mit Karte.
- [ ] **A5 Trinkgeld:** «+ Trinkgeld» mit Karte → Payrexx-Betrag mit Trinkgeld; im Tagesabschluss eigene Zeile.
- [ ] **A6 Abgelehnt / abgebrochen:** In der Payrexx-App abbrechen → «nicht durchgegangen», grüner Knopf «Nochmals an
      das Handy halten» startet dieselbe Zahlung; «Abbrechen» schliesst, bar kassieren.
- [ ] **A7 Ergebnis kommt nicht an:** Während der Zahlung das WLAN zum Pi trennen (Handy nur mit SIM) → nach 15 s
      «Payrexx zeigte «bezahlt»» → bestätigen → bezahlt; Protokoll mit Gerätenamen.
- [ ] **A8 Zwei Handhelds:** XCover 1 startet Karte/TWINT für eine Bestellung; auf XCover 2 dieselbe Bestellung öffnen
      → «Payrexx zeigte «bezahlt»» bzw. Abbrechen geht nicht («anderes Handy»). Am offenen Tisch: XCover 1 startet
      Karte für den ganzen Betrag, XCover 2 kassiert inzwischen bar → Protokoll «Kartenzahlung höher als am Tisch
      offen», am Tisch nichts doppelt.
- [ ] **A9 Übung:** Testmodus, Kasse › Karte/TWINT → «Übung», die Payrexx-App öffnet sich nicht, nach 3 s «bezahlt».
- [ ] **A10 Zahlmittel TWINT** bzw. «Ergebnis zuerst in der Payrexx-App zeigen» → die Payrexx-App verhält sich so.
- [ ] **A11 Aus / Handy ohne «FestPOS Tap»:** keine Zahlart Karte/TWINT bzw. Hinweis «App fehlt – bar kassieren».
- [ ] **A12 Tagesabschluss:** Zeile «Davon Karte/TWINT (NFC) über Payrexx, n Zahlungen», erwartetes Bargeld ohne
      diesen Betrag, PDF mit der Zeile; mit der Abrechnung von Payrexx vergleichen.
- [ ] **A13 Offen beim Abschluss:** Kartenzahlung starten, nicht abschliessen → Tagesabschluss meldet «Kartenzahlung ist
      noch offen», **Abbrechen** → Abschluss geht.
- [ ] **A14 Späte Zahlung:** Kartenzahlung starten, in der Verwaltung **Abbrechen**, Bestellung bar, Tagesabschluss;
      danach «bezahlt» melden lassen → Seite Tagesabschluss zeigt «kam erst nach dem Abschluss» mit zwei Knöpfen.
      **Nachbuchen** → nächster Abschluss mit «nachträglich bestätigt», erwartete Kasse kleiner.

## B – QR-Bestellung am Tisch
- [ ] **B1 Einrichten:** Verwaltung › Kasse › QR-Bestellung: Hinweis «ungetestet» sichtbar; Adresse, Instanz,
      Schlüssel, Tunnel-Token, «Verbindung testen» ok; Schlüssel und Token danach nicht mehr sichtbar. Pro Pi steht
      «verbunden» (falls `cloudflared` fehlte: «wird installiert», danach «verbunden»). QR-PDF drucken.
- [ ] **B2 Bestellen:** QR mit dem eigenen Handy (Mobilfunk, nicht WLAN) scannen → Speisekarte, Tisch stimmt;
      2 Produkte, Bezahlen → Payrexx (Testkarte/TWINT-Test) → zurück, «Bezahlt», Nummer; Bons drucken mit Tisch,
      Gerät «Online». Bestellung in der Verwaltung mit Zahlart Karte/TWINT.
- [ ] **B3 Fremder Tisch:** Im Link die Tischnummer ändern → «QR-Code ungültig».
- [ ] **B4 Ausverkauft dazwischen:** Warenkorb füllen, in der Bestell-App das Produkt ausverkauft melden, dann
      bezahlen → «Inzwischen ausverkauft», Geld bei Payrexx zurück.
- [ ] **B5 Drucker aus** (Kabel ziehen), bezahlen → nach der eingestellten Zeit zurückerstattet, Bon nicht gedruckt.
- [ ] **B6 Preis geändert:** bis zur Payrexx-Seite, in der Verwaltung den Preis ändern, dann bezahlen → Bestellung und
      Bon mit dem alten Preis.
- [ ] **B7 Abbrechen:** bis zur Payrexx-Seite, zurück, «Abbrechen» → «abgebrochen». Nochmals, aber vorher am Pi das
      Internet trennen → «Abbruch wird geprüft …»; Internet wieder an → nach spätestens einer Minute «abgebrochen».
      Mit TWINT bezahlen und **gleichzeitig** abbrechen → «Zahlung kam nach dem Abbruch an», Geld automatisch zurück,
      kein Bon.
- [ ] **B8 Offener Tisch:** Kasse › «Tisch offen lassen» an. Per QR an Tisch 5 bestellen und bezahlen → Bon kommt, am
      Handheld erscheint Tisch 5 **nicht** als offen.
- [ ] **B9 Storno und Rückerstattung:** eine Position einer Online-Bestellung stornieren → genau dieser Betrag zurück;
      «Zurückerstatten» in der Liste → Rest zurück, Storno im Kassenbuch. Internet am Pi trennen, «Zurückerstatten»
      → rot «Rückerstattung offen», Zeile «wird gesendet»; Internet an → innert einer Minute zurück.
- [ ] **B10 Geschlossen:** Schalter aus → Gäste sehen «geschlossen». **Tunnel falsch** (URL auf 8443) → FestPOS
      antwortet nicht, das Journal meldet es. Wieder auf `127.0.0.1:8090`.
- [ ] **B11 Tagesabschluss:** Online-Zahlungen in der Payrexx-Zeile, abzüglich Rückerstattungen. Rückerstattung nach
      dem Abschluss → nächster Abschluss zeigt «Rückerstattungen früherer Tage», erwartete Kasse unverändert.
- [ ] **B12 Webhook** (optional, Payrexx › Webhooks `https://<adresse>/api/gast/webhook`): Zahlung ist sofort da.
