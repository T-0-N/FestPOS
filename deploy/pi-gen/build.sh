#!/bin/bash
# FestPOS – fertiges Image mit pi-gen bauen (T12). Lokal (Docker nötig) und in der CI (.github/workflows/image.yml).
#
#   bash deploy/pi-gen/build.sh            -> deploy/pi-gen/out/festpos-<VERSION>.img.xz und .sha256
#
# PI_GEN_REF: Zweig/Tag von pi-gen (Standard «arm64» = Raspberry Pi OS 64-bit). Auf einem x86-Rechner (auch die CI,
# mit x86-Runner) baut Docker mit QEMU (qemu-user-static, 1,5–3 h); auf einem arm64-Rechner (ARM-Runner, Pi 5)
# 30–60 min.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PROJ=$(cd "$HERE/../.." && pwd)
VERSION=$(tr -d '[:space:]' < "$PROJ/VERSION")
PI_GEN_REF=${PI_GEN_REF:-arm64}
# Fester Stand von pi-gen (Review 2026-11, D4): der Zweig «arm64» bewegt sich, das Image soll aus einem bekannten
# Stand entstehen. Gilt nur für den Standard-Zweig; anderer Zweig/Tag (PI_GEN_REF) oder PI_GEN_COMMIT= (leer) nimmt
# dessen Spitze. Erneuern: git ls-remote https://github.com/RPi-Distro/pi-gen refs/heads/arm64 – danach ein Image
# bauen und auf einem Pi prüfen.
PI_GEN_PINNED=c0d2c9f2cffc08bc56850b0a3eb23178f4226e8a
if [ "$PI_GEN_REF" = arm64 ]; then PI_GEN_COMMIT=${PI_GEN_COMMIT-$PI_GEN_PINNED}; else PI_GEN_COMMIT=${PI_GEN_COMMIT-}; fi
WORK=${PI_GEN_WORK:-$HERE/work}
OUT=$HERE/out
rm -rf "$WORK"
git clone --depth 1 --branch "$PI_GEN_REF" https://github.com/RPi-Distro/pi-gen.git "$WORK"
if [ -n "$PI_GEN_COMMIT" ] && [ "$(git -C "$WORK" rev-parse HEAD)" != "$PI_GEN_COMMIT" ]; then
  git -C "$WORK" fetch --depth 1 origin "$PI_GEN_COMMIT"
  git -C "$WORK" checkout -q --detach FETCH_HEAD
fi
echo "pi-gen: $(git -C "$WORK" rev-parse HEAD)"
cp "$HERE/config" "$WORK/config"
cp -a "$HERE/stage-festpos" "$WORK/stage-festpos"
mkdir -p "$WORK/stage-festpos/festpos"
# nur, was im Git steht (keine festpos.conf, keine Zertifikate, kein dist/) – dazu die Python-Pakete und rqlite,
# mit Prüfsumme geholt wie fürs Update-Paket (tools/pakete.py)
git -C "$PROJ" archive HEAD | tar -x -C "$WORK/stage-festpos/festpos"
python3 "$PROJ/tools/pakete.py" "$WORK/stage-festpos/festpos"
touch "$WORK/stage2/SKIP_IMAGES"                       # nur das FestPOS-Image ausgeben, nicht das leere Lite
# Benutzer gesperrt (passwd -l im Image); pi-gen verlangt trotzdem ein Passwort beim Bauen
echo "FIRST_USER_PASS=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')" >> "$WORK/config"
(cd "$WORK" && ./build-docker.sh)
mkdir -p "$OUT"
IMG=$(find "$WORK/deploy" -name '*festpos*.img.xz' | head -1)
[ -n "$IMG" ] || { echo "kein Image gebaut"; exit 1; }
cp "$IMG" "$OUT/festpos-$VERSION.img.xz"
(cd "$OUT" && sha256sum "festpos-$VERSION.img.xz" > "festpos-$VERSION.img.xz.sha256")
PAKETE=$(find "$WORK/deploy" -name 'festpos-pakete.txt' | head -1)    # Debian-Pakete des Images (für die SBOM)
[ -n "$PAKETE" ] && cp "$PAKETE" "$OUT/festpos-pakete.txt"
python3 "$HERE/os_list.py" "$OUT/festpos-$VERSION.img.xz" "$VERSION" "" "${FESTPOS_TAG:-v$VERSION}" > "$OUT/festpos-os-list.json"
echo "Fertig: $OUT/festpos-$VERSION.img.xz"
