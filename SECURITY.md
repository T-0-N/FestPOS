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
  seiner Boot-Partition liegt. Ein Schlüssel kommt nie über die Verwaltung dorthin.
- **Handhelds** brauchen ein Gerätezertifikat der eigenen Zertifizierungsstelle (pro Installation erzeugt).
- **Verwaltung** mit Benutzern und Rollen; auf Wunsch nur mit Gerätezertifikat.
- **Kassenbuch** mit Prüfsummen (HMAC) und verketteten Tagesabschlüssen.
- **QR-Bestellung** (optional): von aussen erreichbar ist nur ein abgeschotteter Gästedienst über einen Cloudflare
  Tunnel; kein Port ist offen.
- **SSH** nur mit Schlüssel, `sudo` nur mit Passwort.

Einzelheiten: `deploy/ANLEITUNG.md` (Kapitel «Neue Version einspielen», «QR-Bestellung am Tisch», «SSH-Zugang»).
