"""Sicherheitsupdates des Betriebssystems für das Update-Paket holen (T-0-N/FestPOS#5, läuft in der CI beim Release).

  python3 tools/os_updates.py --pakete deploy/os-pakete.txt --ziel dist/os-updates \\
      --debian-keyring /usr/share/keyrings/debian-archive-keyring.gpg --rpi-keyring raspberrypi.gpg

Die Pi bekommen nie Internet, um ihr System zu erneuern – darum holt die CI die Updates: apt in einer eigenen,
leeren Umgebung (nichts vom Rechner der CI), Architektur arm64, nur die offiziellen Quellen von Debian (bookworm,
bookworm-updates, bookworm-security) und Raspberry Pi. apt prüft die signierten Paketlisten selbst (signed-by mit den
angegebenen Schlüsseln). Als «installiert» gilt die Paketliste des Images (deploy/os-pakete.txt: Name<TAB>Version
<TAB>Architektur, wie dpkg-query -W sie schreibt; der Image-Bau legt sie als festpos-pakete.txt ab). apt rechnet
aus, was neuer ist (dist-upgrade, auch neu nötige Pakete, z.B. ein neuer Kernel) und lädt genau diese .deb. Dazu
die Pakete, die FestPOS neu braucht und ältere Images noch nicht haben (EXTRA, z.B. cryptsetup für #3).

Ergebnis im Ziel: debs/*.deb, liste.json (Name, Version, Architektur, Datei, SHA-256, vorher, neu, Kernel) und
stand.json (Datum, Quellen). build_zip.py legt den Ordner als os-updates/ ins Paket; auf dem Pi spielt
deploy/festpos-os-update sie ein (update.sh), ohne Internet.
Tests: FESTPOS_APT_GET (Ersatz für apt-get), FESTPOS_DPKG_DEB (Ersatz für dpkg-deb).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SUITE = "bookworm"
DEBIAN = ("http://deb.debian.org/debian", (SUITE, f"{SUITE}-updates"), "main contrib non-free non-free-firmware")
SECURITY = ("http://security.debian.org/debian-security", (f"{SUITE}-security",), "main contrib non-free non-free-firmware")
RPI = ("http://archive.raspberrypi.com/debian", (SUITE,), "main")
KERNEL_RE = re.compile(r"^(linux-image-|linux-headers-|raspi-firmware|raspberrypi-kernel|raspberrypi-bootloader|"
                       r"rpi-eeprom)")
INST_RE = re.compile(r"^Inst (\S+)(?: \[([^\]]+)\])? \((\S+) ")
APT = os.environ.get("FESTPOS_APT_GET", "apt-get")
DPKG_DEB = os.environ.get("FESTPOS_DPKG_DEB", "dpkg-deb")
EXTRA = ("cryptsetup-bin",)          # Daten verschlüsseln (T-0-N/FestPOS#3, festpos-krypt)


def read_packages(path: Path) -> list[tuple[str, str, str]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        p = line.strip().split("\t")
        if len(p) == 3 and p[0] and not line.startswith("#"):
            out.append((p[0], p[1], p[2]))
    return out


def status_file(pkgs: list[tuple[str, str, str]]) -> str:
    """dpkg-Status, als wären genau diese Pakete installiert (für apt in der leeren Umgebung)."""
    return "".join(f"Package: {n}\nStatus: install ok installed\nPriority: optional\nArchitecture: {a}\n"
                   f"Version: {v}\nMaintainer: x\nDescription: x\n\n" for n, v, a in pkgs)


def sandbox(root: Path, pkgs, debian_key: str, rpi_key: str) -> Path:
    for d in ("etc/apt/apt.conf.d", "etc/apt/preferences.d", "etc/apt/trusted.gpg.d", "var/lib/apt/lists/partial",
              "var/cache/apt/archives/partial", "var/lib/dpkg", "var/log/apt"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "var/lib/dpkg/status").write_text(status_file(pkgs), encoding="utf-8")
    archs = sorted({a for _, _, a in pkgs if a not in ("all", "")} or {"arm64"})
    lines = []
    for url, suites, comps in (DEBIAN, SECURITY, RPI):
        key = rpi_key if url == RPI[0] else debian_key
        lines += [f"deb [arch={','.join(archs)} signed-by={key}] {url} {s} {comps}" for s in suites]
    (root / "etc/apt/sources.list").write_text("\n".join(lines) + "\n", encoding="utf-8")
    conf = root / "apt.conf"
    conf.write_text(f'''Dir "{root}/";
Dir::State::status "{root}/var/lib/dpkg/status";
APT::Architecture "{archs[0]}";
APT::Architectures {{ {" ".join(f'"{a}";' for a in archs)} }};
APT::Get::AllowUnauthenticated "false";
Acquire::AllowInsecureRepositories "false";
Acquire::Languages "none";
Debug::NoLocking "true";
Dir::Etc::Parts "{root}/etc/apt/apt.conf.d";
''', encoding="utf-8")
    return conf


def apt(conf: Path, *args: str, timeout: int = 1800) -> str:
    env = {**os.environ, "APT_CONFIG": str(conf), "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C"}
    p = subprocess.run([APT, *args], env=env, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise SystemExit(f"apt-get {' '.join(args)} gescheitert:\n{p.stdout[-2000:]}\n{p.stderr[-2000:]}")
    return p.stdout


def plan(sim: str) -> list[dict]:
    """«apt-get -s dist-upgrade» -> [{name, before, version}] (before None = neu nötig)."""
    out = []
    for line in sim.splitlines():
        m = INST_RE.match(line)
        if m:
            out.append({"name": m[1].split(":")[0], "before": m[2], "version": m[3]})
    return out


def deb_fields(path: Path) -> tuple[str, str, str]:
    p = subprocess.run([DPKG_DEB, "-f", str(path), "Package", "Version", "Architecture"], capture_output=True,
                       text=True, timeout=60)
    f = dict(line.split(": ", 1) for line in p.stdout.splitlines() if ": " in line)
    return f.get("Package", ""), f.get("Version", ""), f.get("Architecture", "")


def collect(pkgs, ziel: Path, debian_key: str, rpi_key: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="festpos-apt-") as tmp:
        root = Path(tmp)
        conf = sandbox(root, pkgs, debian_key, rpi_key)
        apt(conf, "update", "--error-on=any")              # jede Quelle muss aktuell sein (sonst alte Listen)
        todo = plan(apt(conf, "-s", "dist-upgrade"))
        if todo:
            apt(conf, "--download-only", "-y", "dist-upgrade")
        extra = [t for t in plan(apt(conf, "-s", "install", "--no-install-recommends", *EXTRA))
                 if t["name"] not in {x["name"] for x in todo}]
        if extra:
            apt(conf, "--download-only", "-y", "install", "--no-install-recommends", *EXTRA)
            todo += extra
        debs = ziel / "debs"
        shutil.rmtree(ziel, ignore_errors=True)
        debs.mkdir(parents=True)
        by_name = {t["name"]: t for t in todo}
        items = []
        for f in sorted((root / "var/cache/apt/archives").glob("*.deb")):
            name, version, arch = deb_fields(f)
            t = by_name.get(name)
            if not t or t["version"] != version:
                continue                                   # nur, was apt für das Upgrade wollte
            dest = debs / f.name
            shutil.copyfile(f, dest)
            items.append({"name": name, "version": version, "arch": arch, "file": f"debs/{f.name}",
                          "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(), "before": t["before"],
                          "new": t["before"] is None, "kernel": bool(KERNEL_RE.match(name))})
        missing = sorted(set(by_name) - {i["name"] for i in items})
        if missing:
            raise SystemExit(f"apt hat nicht alle Pakete geladen: {', '.join(missing)}")
    stand = {"date": time.strftime("%Y-%m-%d"), "suite": SUITE, "count": len(items),
             "kernel": any(i["kernel"] for i in items), "sources": [DEBIAN[0], SECURITY[0], RPI[0]]}
    (ziel / "liste.json").write_text(json.dumps(items, indent=1), encoding="utf-8")
    (ziel / "stand.json").write_text(json.dumps(stand, indent=1), encoding="utf-8")
    return stand


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pakete", required=True, help="Paketliste des Images (Name, Version, Architektur)")
    ap.add_argument("--ziel", required=True)
    ap.add_argument("--debian-keyring", default="/usr/share/keyrings/debian-archive-keyring.gpg")
    ap.add_argument("--rpi-keyring", required=True)
    a = ap.parse_args(argv)
    pkgs = read_packages(Path(a.pakete))
    if not pkgs:
        print("Paketliste leer – keine Updates des Betriebssystems im Paket.", file=sys.stderr)
        return 1
    stand = collect(pkgs, Path(a.ziel), str(Path(a.debian_keyring).resolve()), str(Path(a.rpi_keyring).resolve()))
    print(f"{stand['count']} Pakete für das Betriebssystem ({stand['date']}){' – mit Kernel' if stand['kernel'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
