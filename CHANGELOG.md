# Änderungen

Alle wichtigen Änderungen an FestPOS. Versionen nach [Semantic Versioning](https://semver.org/lang/de/):
MAJOR.MINOR.PATCH – PATCH für Fehlerbehebungen, MINOR für neue Funktionen, MAJOR, wenn ein Update eine Neuinstallation
oder Umstellung braucht. Testversionen heissen `X.Y.Z-rc.N` und sind älter als `X.Y.Z`.

Neue Einträge zuerst unter «Unveröffentlicht» sammeln; beim Release wird daraus die neue Version.

## [Unveröffentlicht]

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
