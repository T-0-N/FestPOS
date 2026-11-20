# Fremdsoftware in FestPOS

FestPOS selbst steht unter der GNU General Public License Version 3 (Datei `LICENSE`). Dieses Repo und das
Update-Paket `festpos.zip` enthalten zusätzlich die folgende Software anderer Autoren, jeweils unverändert und unter
ihrer eigenen Lizenz. Die Lizenztexte liegen in den Paketen selbst (in jeder `.whl`-Datei unter `*.dist-info/`, im
`.deb` unter `usr/share/doc/`) bzw. neben den Dateien.

Python-Pakete und rqlite liegen nicht im Repo: `tools/pakete.py` holt sie beim Bauen von Update-Paket und Image
(feste Versionen mit Prüfsumme aus `requirements.txt` bzw. `deploy/rqlite.env`).

Diese Liste gehört zum Release: `tests/test_review_2026_11.py` prüft, dass jedes Paket aus `requirements.txt` mit
seiner Version hier steht. Nach `python3 tools/pin-requirements.py` nachführen.

## Python-Pakete (im Update-Paket und Image unter `deploy/wheels/`, auf dem Pi in `/opt/festpos/venv`)

| Paket | Version | Lizenz | Quelle |
|---|---|---|---|
| annotated-doc | 0.0.5 | MIT | https://github.com/fastapi/annotated-doc |
| annotated-types | 0.8.0 | MIT | https://github.com/annotated-types/annotated-types |
| anyio | 4.15.1 | MIT | https://github.com/agronholm/anyio |
| cffi | 2.1.1 | MIT-0 | https://github.com/python-cffi/cffi |
| charset-normalizer | 3.5.2 | MIT |  |
| click | 8.5.0 | BSD-3-Clause | https://github.com/pallets/click/ |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography |
| fastapi | 0.142.4 | MIT | https://github.com/fastapi/fastapi |
| h11 | 0.16.0 | MIT | https://github.com/python-hyper/h11 |
| httptools | 0.9.0 | MIT | https://github.com/MagicStack/httptools |
| idna | 3.20 | BSD-3-Clause | https://github.com/kjd/idna |
| opentelemetry-api | 1.45.1 | Apache-2.0 | https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-api |
| pillow | 12.3.0 | MIT-CMU | https://python-pillow.github.io |
| pycparser | 3.11 | BSD-3-Clause | https://github.com/eliben/pycparser |
| pydantic | 2.14.0 | MIT | https://github.com/pydantic/pydantic |
| pydantic_core | 2.50.0 | MIT | https://github.com/pydantic/pydantic |
| python-dotenv | 1.2.4 | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| python-multipart | 0.0.32 | Apache-2.0 | https://github.com/Kludex/python-multipart |
| PyYAML | 6.0.3 | MIT | https://pyyaml.org/ |
| reportlab | 5.0.1 | BSD-3-Clause | https://www.reportlab.com/ |
| starlette | 1.7.0 | BSD-3-Clause | https://github.com/Kludex/starlette |
| typing_extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| typing-inspection | 0.4.4 | MIT | https://github.com/pydantic/typing-inspection |
| uvicorn | 0.53.0 | BSD-3-Clause | https://uvicorn.dev/ |
| uvloop | 0.23.0 | MIT OR Apache-2.0 | https://github.com/MagicStack/uvloop |
| watchfiles | 1.3.0 | MIT | https://github.com/samuelcolvin/watchfiles |
| websockets | 17.2 | BSD-3-Clause | https://github.com/python-websockets/websockets |

reportlab bringt die Schriften Bitstream Vera mit (Bitstream Vera Fonts Copyright); FestPOS benutzt sie für
durchgestrichene Positionen auf dem Storno-Bon.

## Weitere mitgelieferte Dateien

| Was | Wo | Lizenz | Quelle |
|---|---|---|---|
| jsQR (QR-Scanner im Browser, Rückfall ohne BarcodeDetector) | `static/vendor/jsQR.js`, Lizenz daneben | Apache-2.0 | https://github.com/cozmo/jsQR |
| Barlow Condensed (Schrift der Oberflächen) | `static/fonts/`, Lizenz `OFL.txt` | SIL Open Font License 1.1 | https://github.com/jpt/barlow |

## Wird beim Bauen oder bei der Installation geholt, liegt nicht im Repo

| Was | Lizenz | Quelle |
|---|---|---|
| rqlite 10.5.3 (replizierte Datenbank; im Update-Paket und Image, feste Version mit Prüfsumme aus `deploy/rqlite.env`) | MIT | https://github.com/rqlite/rqlite |
| cloudflared (Cloudflare Tunnel, nur für die QR-Bestellung; installiert jeder Pi selbst aus der Paketquelle von Cloudflare, sobald der Tunnel an ist) | Apache-2.0 | https://github.com/cloudflare/cloudflared |
| Raspberry Pi OS mit keepalived, chrony, nftables, dnsmasq, Chromium, cage … (Pakete der Distribution) | je Paket (meist GPL/BSD) | https://www.raspberrypi.com/software/ |

## Nicht enthalten

- **Fully Kiosk Browser** (proprietär, eigene Lizenz pro Gerät): die APK lädt der Betreiber selbst hoch.
- **Payrexx** «Tap to Pay» und die Payrexx-API: Dienste von Payrexx mit eigenem Vertrag.
- Werkzeuge nur für Entwicklung und CI (pytest, httpx2, ruff, Playwright): `requirements-dev.txt`, nicht auf dem Pi.
