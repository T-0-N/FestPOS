# FestPOS – Probelauf vor einer Version (Release-Kandidat)

Für jede neue MINOR- oder MAJOR-Version, zuerst für 1.0: mit der Testversion (`vX.Y.Z-rc.N`, Pre-release) auf
leeren Karten, so wie jemand FestPOS zum ersten Mal einrichtet. Erst wenn alles grün ist, die fertige Version.
Payrexx hat einen eigenen Testplan: `docs/testplan-payrexx.md`.

**Material:** 3 Pi 5 mit Netzteil, Bildschirm (HDMI) an Pi A, Switch, Access Point, Laptop, 2 Handhelds mit Fully,
ein Drucker, USB-Kartenleser, 4 leere SD-Karten (32 GB), USB-Stick (FAT32), Internet (Router oder Handy-Hotspot).

## A – Release
- [ ] **A1 Entwurf:** Tag `vX.Y.Z-rc.N` gepusht → Workflow «Release» grün; Entwurf als *Pre-release* mit
      `festpos.zip`, `festpos-X.Y.Z-rc.N.img.xz`, `.sha256`, `festpos-os-list.json`, `festpos-tap.apk`, `SHA256SUMS`.
      Dauer Image: ______ min.
- [ ] **A2 Signieren:** `festpos.zip` aus dem Entwurf signiert, `festpos.zip.sig` und Kurzanleitung (PDF) angehängt,
      veröffentlicht (bleibt Pre-release). Text des Release: «Testversion», Bekannte Einschränkungen (Payrexx).
- [ ] **A3 Öffentliches Repo:** ein Commit «FestPOS X.Y.Z-rc.N», keine Branches ausser `main`, keine privaten Dateien
      (keine Pläne, keine Arbeitsnotizen).

## B – Neue Karte aus dem Image
- [ ] **B1 Imager:** `festpos-…img.xz` mit dem Raspberry Pi Imager auf eine leere Karte (Liste
      `festpos-os-list.json` als benutzerdefiniertes Repository: FestPOS erscheint mit Version und Grösse).
- [ ] **B2 Ersteinrichtung:** Pi am Heimrouter ohne Internet-Bedarf: Bildschirm zeigt Adresse und Code,
      `https://festpos.local/` (Warnung einmal, Fingerabdruck wie am Bildschirm), «Weiter, Weiter, Fertig», `ca.key`
      sichern, Neustart. In der Zusammenfassung: «Updates: vom FestPOS-Projekt».
- [ ] **B3 Anmelden am Festplatz:** `https://192.168.55.10:8443/admin.html`, `admin` mit dem Notfall-Passwort; Version
      = Testversion; Wartung › Update zeigt den **Projekt-Schlüssel** (Fingerabdruck wie `deploy/festpos-update-projekt.pub`).
- [ ] **B4 Betrieb:** Drucker einrichten, Produkte importieren (Vorlage), ein Handheld einrichten, bestellen, Bon kommt,
      Tagesabschluss.
- [ ] **B5 Eigener Schlüssel:** zweite Karte aus dem Image, bei der Ersteinrichtung eine eigene `festpos-update.pub`
      wählen (falsche Datei → Meldung) → Wartung › Update zeigt beide Schlüssel.
- [ ] **B6 Zwei Karten aus dem Image:** `machine-id` und SSH-Schlüssel verschieden; ohne Imager-Einstellungen SSH aus.

## C – Weitere Karten vom Pi selbst
- [ ] **C1 Auf 3 Pi erweitern:** Geräte & Netz › Pi › «Auf 3 Pi erweitern», Karten für B und C am USB-Kartenleser
      schreiben (Dauer: ______ min), Pi A umstellen, B und C einstecken → treten ohne Handgriff bei, Übersicht 3/3.
- [ ] **C2 Pi B:** andere `machine-id`, SSH-Schlüssel und PARTUUID als A; `hostname` festpos-b; Projekt-Schlüssel da
      (Wartung › Update auf B).
- [ ] **C3 Ersatzkarte im Cluster:** Karte für Pi C schreiben, C tauschen → tritt bei, holt die Daten.
- [ ] **C4 Abbrechen / Leser ziehen:** «Abgebrochen – die Karte ist leer»; Leser gezogen → «nicht geschrieben», neuer
      Versuch geht.
- [ ] **C5 Ersatz-Pi aus Sicherung:** frische Karte aus dem Image, «Sicherung einspielen» → Daten da, Handys ohne
      Neueinrichtung, Prüfliste grün.

## D – Updates
- [ ] **D1 Hochladen:** nächste Testversion (`-rc.N+1`) als `festpos.zip` + `.sig` hochladen → «Auf alle Pi
      verteilen», Pi für Pi, der druckende zuletzt; Bestellen läuft weiter.
- [ ] **D2 USB-Stick:** dieselben Dateien auf einem Stick an einen Pi → «übernommen», verteilt.
- [ ] **D3 Aus dem Internet:** nach dem Veröffentlichen der fertigen Version: Geräte & Netz › Netz & Internet,
      Verbindung wählen; Wartung › Update › «Aus dem Internet»: Repo `T-0-N/FestPOS` steht schon da, «Nach neuer
      Version suchen» findet die fertige Version (keine Testversion), herunterladen, verteilen.
- [ ] **D4 Zurück:** «Alle Pi zurück auf …» und wieder vor.
- [ ] **D5 Fremd signiert:** ein mit einem anderen Schlüssel signiertes Paket → abgewiesen («unbekannter Schlüssel»).
- [ ] **D6 rqlite:** Test-Paket mit neuerer rqlite-Version derselben Hauptversion (`deploy/rqlite.env`) → jeder Pi meldet
      «rqlite … installiert», Cluster danach 3/3; «Alle Pi zurück» nimmt auch rqlite zurück.

## E – Zum Schluss
- [ ] **E1** Alle Sprachen kurz durchklicken (Verwaltung, Bestell-App, Ersteinrichtung) – keine fehlenden Texte.
- [ ] **E2** Handheld 360 px breit: alles passt.
- [ ] **E3** Fehler als Issue im öffentlichen Repo mit Nummer (z.B. «C1 – Kartenleser»).
