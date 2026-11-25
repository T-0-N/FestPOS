# Sicherheit

## Lücken melden

Bitte **nicht** als öffentliches Issue. Stattdessen über GitHub vertraulich melden: im Repo unter **Security ›
Report a vulnerability** (Private vulnerability reporting). Hilfreich sind: betroffene Version (`VERSION`), was man
tun muss, was dann passiert, und wenn möglich ein Vorschlag.

Eine Antwort kommt in der Regel innert einer Woche. Behoben wird in einer neuen PATCH-Version; im `CHANGELOG.md`
steht dann, was betroffen war.

## Unterstützte Versionen

Sicherheitskorrekturen gibt es für die jeweils neuste Version (`MAJOR.MINOR`). Ältere Versionen bitte zuerst
aktualisieren (Verwaltung › Wartung › Update).

## Wie FestPOS geschützt ist (Kurzfassung)

- **Updates** spielt ein Pi nur ein, wenn sie mit einem Update-Schlüssel signiert sind, dessen öffentlicher Teil auf
  seiner Boot-Partition liegt. Ein Schlüssel kommt nie über die Verwaltung dorthin – nur von der Karte oder mit einem
  schon vertrauenswürdig signierten Paket (Schlüsselwechsel, siehe unten); zurückgezogene Schlüssel gelten nie mehr.
- **Handhelds** brauchen ein Gerätezertifikat der eigenen Zertifizierungsstelle (pro Installation erzeugt).
- **Verwaltung** mit Benutzern und Rollen; auf Wunsch nur mit Gerätezertifikat. Zweiter Faktor (TOTP) für Admins
  Pflicht (einstellbar pro Rolle); kritische Aktionen wie Update, Benutzer, Netz und Sicherung einspielen verlangen
  Passwort und Code nochmals und stehen im Protokoll.
- **Kassenbuch** mit Prüfsummen (HMAC) und verketteten Tagesabschlüssen.
- **QR-Bestellung** (optional): von aussen erreichbar ist nur ein abgeschotteter Gästedienst über einen Cloudflare
  Tunnel; kein Port ist offen.
- **Betriebssystem:** Update-Pakete bringen die Sicherheitsupdates von Debian und Raspberry Pi mit (aus den
  offiziellen, signierten Quellen); die Pi spielen sie ohne Internet ein. Die Verwaltung zeigt den Stand pro Pi.
- **Gastdaten:** Quittungen über CHF 400 mit MWST tragen nur Name und Ort. «Saison abschliessen» verschiebt die
  Quittungen in ein verschlüsseltes Archiv und löscht sie danach auf dem Pi.
- **Internet:** Die Pi verbinden sich nur mit Payrexx, Cloudflare, GitHub, den Paketquellen, Zeitservern und DNS;
  alles andere verwirft und zählt die Firewall (Modus in der Verwaltung, «Beobachten»/«Offen» höchstens 24 Stunden).
- **SSH** nur mit Schlüssel, `sudo` nur mit Passwort. SSH-Anmeldungen, `sudo`, Neustarts, Änderungen an der
  Boot-Partition, Anmeldesperren und abgewiesene fremd signierte Updates erscheinen im Protokoll und in der Übersicht
  der Verwaltung (alle Pi, innert einer Minute).
- **Daten auf der Karte** (Datenbank, Gastdaten, Schlüssel, Geheimnisse) liegen verschlüsselt (LUKS2); der Schlüssel
  liegt einmalig im Chip des Pi (OTP). Eine gestohlene SD-Karte ist ohne genau diesen Pi unlesbar; für einen defekten
  Pi gibt es pro Pi einen Wiederherstellungs-Schlüssel zum Ausdrucken. Grenzen: Wer Karte **und** Pi hat und das
  System auf der Karte verändern kann, kommt an den Schlüssel (dagegen hilft erst Secure Boot); root auf dem laufenden
  Pi hat Zugriff. Nicht im Container: Betriebssystem, SSH-Hostschlüssel und das WLAN-Passwort für «Update aus dem
  Internet» (NetworkManager).
- **Notfall-Passwort** (Benutzer `admin`): auf der Karte nur als Prüfwert (scrypt), mindestens 16 Zeichen, nur aus dem
  FestPOS-Netz; jede Anmeldung im Protokoll.
- **Geheimnisse in der Datenbank** (Sicherungs-Passwort, Fully-, WLAN- und Router-Passwort, Tokens, Payrexx-Schlüssel)
  liegen mit AES-GCM verschlüsselt; der Schlüssel ist pro Zweck ein eigener und stammt aus `CLUSTER_SECRET` (nicht aus
  der Datenbank). Das gilt, sobald alle Pi die Version ab 1.1.0 haben **und** FestPOS die bisherigen Werte umgeschrieben
  hat (im Hintergrund, alle 10 Minuten; auch nach dem Einspielen einer älteren Sicherung). Bis dahin stehen sie im
  Klartext bzw. im bisherigen Verfahren. Danach verrät eine Sicherung der Datenbank allein sie nicht. Zurück auf eine
  Version vor 1.1.0 liest die umgeschriebenen Werte nicht mehr: dann Passwörter und Schlüssel in der Verwaltung neu
  eintragen.

Einzelheiten: `deploy/ANLEITUNG.md` (Kapitel «Neue Version einspielen», «QR-Bestellung am Tisch», «SSH-Zugang»,
«Daten verschlüsselt»).

## Update-Schlüssel zurückziehen und ersetzen

Gerät ein Update-Schlüssel (der **private** Teil) in falsche Hände, nehmen die Pi damit signierte Pakete weiter an –
bis der Schlüssel zurückgezogen ist. Ein Pi verwirft danach jedes Paket mit diesem Schlüssel, auch vom USB-Stick.
Das funktioniert ab Version 1.1.0; ältere Pi zuerst auf eine Version ab 1.1.0 aktualisieren.

**Projekt-Schlüssel (Releases von T-0-N/FestPOS):**

1. Neuen Schlüssel erzeugen (offline, am besten auf einem Rechner ohne Netz):
   `ssh-keygen -t ed25519 -C "FestPOS Update JAHR" -f festpos-update-neu`
2. Im Projekt `deploy/update-keys/` anlegen: `festpos-update-neu.pub` (öffentlicher Teil) und – wenn der alte
   Schlüssel kompromittiert ist – `zurueckgezogen.txt` mit seinem Fingerabdruck (`ssh-keygen -lf alt.pub`, eine
   Zeile `SHA256:…  Kommentar`). `deploy/festpos-update-projekt.pub` durch den neuen ersetzen (für neue Images).
3. Das Paket mit **beiden** Schlüsseln signieren und die Signaturen in eine Datei schreiben:
   `ssh-keygen -Y sign -f alt -n festpos-update festpos.zip && mv festpos.zip.sig alt.sig`,
   dasselbe mit dem neuen Schlüssel, dann `cat alt.sig neu.sig > festpos.zip.sig`.
   Pi mit dem alten Schlüssel nehmen es dank der alten Signatur an, Pi mit dem neuen dank der neuen.
4. Nach dem Einspielen übernimmt jeder Pi den neuen Schlüssel (Boot-Partition, `festpos-update-….pub`) und trägt
   die zurückgezogenen in `festpos-update-zurueckgezogen.txt` ein; eine Datei, die nur noch zurückgezogene Schlüssel
   enthält, heisst danach `….pub.zurueckgezogen`. Verwaltung › Wartung › Update zeigt die Schlüssel jedes Pi.
5. Ist der alte Schlüssel kompromittiert: im Release-Text darauf hinweisen und Pi, die länger aus waren, zuerst mit
   genau diesem Paket aktualisieren. Wird der Schlüssel nur routinemässig gewechselt, `zurueckgezogen.txt` erst in
   einer späteren Version mitgeben, wenn alle Pi den neuen haben.

**Eigener Schlüssel eines Betreibers:** gleich, mit dem eigenen Paket. Ohne Paket (z.B. Schlüssel verloren): auf
jeder Karte am PC die alte `festpos-update….pub` löschen, die neue auf die Boot-Partition legen und die Zeile mit dem
alten Fingerabdruck in `festpos-update-zurueckgezogen.txt` eintragen. Ist diese Datei da, aber nicht lesbar (zu
gross, eine Verknüpfung, Lesefehler), gilt **kein** Update-Schlüssel, bis sie wieder in Ordnung ist – nie wird
dadurch ein zurückgezogener wieder gültig. Bei einem Schlüsselwechsel legt der Pi zuerst die neuen Schlüssel ab und
zieht erst danach zurück. Die Verwaltung bringt nie einen Schlüssel
auf die Pi – nur ein schon vertrauenswürdig signiertes Paket oder der Zugriff auf die Karte.
