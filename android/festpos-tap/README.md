# FestPOS Tap – Begleit-App für die NFC-Zahlung (T13)

Payrexx «Tap to Pay» läuft in der Payrexx-App auf dem XCover und wird von anderen Apps über einen Android-Intent
angesprochen (App-zu-App-Schnittstelle, `com.payrexx.taptopay.SOFTPOS`, siehe github.com/payrexx/TapToPaySDK). Das
Ergebnis kommt als Activity-Result zurück – das kann die Bestell-App im Browser (Fully) nicht empfangen. Diese kleine
App (eine Activity, ohne Fremdbibliotheken) macht die Brücke:

1. Bestell-App startet sie: `intent://pay#Intent;scheme=festpostap;package=ch.festpos.tap;S.id=…;S.token=…;S.amount=…;S.ref=…;S.server=…;end`
2. sie ruft die Payrexx-App auf (Karte oder Handy an das Gerät halten),
3. meldet das Ergebnis mit dem Einmal-Schlüssel an den Pi (`POST {server}/api/tap/result`, Port 8443, vertraut der
   FestPOS-CA, die auf jedem Handy installiert ist) und schliesst sich.

Bauen: CI (`.github/workflows/android.yml`) oder lokal mit Android-SDK: `gradle -p android/festpos-tap assembleRelease`.
Installieren: Verwaltung › Einstellungen › Kasse › NFC-Zahlung › APK hochladen › «Auf die Handys installieren» (Fully,
ohne Rückfrage). Die Payrexx-App «Payrexx Tap to Pay» kommt aus dem Play Store.
