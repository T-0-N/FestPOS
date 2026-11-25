"""festpos.zip für das Update bauen (Verwaltung › Update oder USB-Stick).

  python3 tools/build_zip.py                      -> dist/festpos.zip
  python3 tools/build_zip.py ziel.zip
  python3 tools/build_zip.py --pakete-aus alt.zip  Python-Pakete und rqlite aus einer früheren festpos.zip (ohne
                                                   Internet, wenn sich die Pakete nicht geändert haben)
  python3 tools/build_zip.py --os-updates DIR      Sicherheitsupdates des Betriebssystems (tools/os_updates.py) als
                                                   os-updates/ ins Paket (T-0-N/FestPOS#5)

Im Git-Repo kommen alle Dateien hinein, die Git kennt oder kennen würde (also ohne alles aus .gitignore:
Caches, lokale Konfiguration, Datenbanken). Ohne Git: alles ausser Caches, *.db und deploy/festpos.conf.
Dazu kommen die Python-Pakete für den Pi und rqlite (tools/pakete.py: mit Prüfsumme, aus dem Zwischenspeicher
dist/pakete/, einer früheren festpos.zip oder dem Internet) – sie liegen nicht im Git. Ausführungsrechte bleiben.
Bricht ab, wenn eine Textdatei Windows-Zeilenenden (CRLF) hat – damit liefe das Paket auf dem Pi nicht
(Abhilfe: Klon mit «git clone -c core.autocrlf=false …», siehe .gitattributes).
Die Signatur (festpos.zip.sig) entsteht separat, siehe deploy/ANLEITUNG.md.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.release import windows_line_endings  # noqa: E402  (nur Standardbibliothek; dieselbe Regel wie auf dem Pi)
sys.path.insert(0, str(ROOT / "tools"))
import pakete  # noqa: E402
SKIP_DIRS = {"__pycache__", ".ruff_cache", ".pytest_cache", ".git", "dist", "scratch"}
SKIP_FILES = {"deploy/festpos.conf"}
# Was nur ins Entwicklungs-Repo gehört, steht dort in .oeffentlich/ohne.txt – das Paket entspricht dann dem
# öffentlichen Stand. Im öffentlichen Repo gibt es die Datei nicht (und nichts davon).
OHNE = ROOT / ".oeffentlich" / "ohne.txt"


def _ohne() -> list[str]:
    if not OHNE.is_file():
        return []
    return [x.strip() for x in OHNE.read_text(encoding="utf-8").splitlines() if x.strip() and not x.startswith("#")]


def _excluded(rel: str, rules: list[str]) -> bool:
    return any(rel == r or (r.endswith("/") and rel.startswith(r)) for r in rules)


def _files() -> list[str]:
    if (ROOT / ".git").exists():
        out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             cwd=ROOT, check=True, capture_output=True).stdout.decode()
        names = [n for n in out.split("\0") if n and (ROOT / n).is_file()]
        # Git kennt sie, im Ordner fehlen sie (gelöscht, nicht ausgecheckt): nie ein unvollständiges Paket bauen
        weg = subprocess.run(["git", "ls-files", "-z", "--deleted"], cwd=ROOT, check=True, capture_output=True).stdout.decode()
        weg = sorted(n for n in weg.split("\0") if n and not _excluded(n, _ohne()))
        if weg:
            sys.exit(f"FEHLER: {len(weg)} Dateien fehlen im Ordner, z.B. {', '.join(weg[:5])}\n"
                     "Zurückholen mit «git restore .» oder neu klonen:\n"
                     "  git clone -c core.autocrlf=false https://github.com/T-0-N/FestPOS.git")
    else:
        names = []
        for root, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            names += [(Path(root) / f).relative_to(ROOT).as_posix() for f in files]
    rules = _ohne()
    return sorted(n for n in set(names)
                  if n not in SKIP_FILES and not n.endswith((".pyc", ".db")) and n.split("/")[0] not in SKIP_DIRS
                  and not _excluded(n, rules))


def _exec_in_git() -> set[str]:
    """Dateien, die Git als ausführbar kennt (100755) – unter Windows kennt das Dateisystem kein «ausführbar»."""
    if not (ROOT / ".git").exists():
        return set()
    out = subprocess.run(["git", "ls-files", "-s", "-z"], cwd=ROOT, check=True, capture_output=True).stdout.decode()
    return {e.split("\t", 1)[1] for e in out.split("\0") if e.startswith("100755 ")}


def _add(z: zipfile.ZipFile, path: Path, rel: str, exe: set[str] | None = None) -> None:
    info = zipfile.ZipInfo.from_file(path, "festpos/" + rel)
    x = rel in exe if exe else bool(path.stat().st_mode & stat.S_IXUSR)
    info.external_attr = ((0o100755 if x else 0o100644) & 0xFFFF) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    z.writestr(info, path.read_bytes())


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    alt, osdir = None, None
    if "--os-updates" in argv:
        i = argv.index("--os-updates")
        osdir = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
        if not osdir.is_dir():
            sys.exit(f"--os-updates: Ordner {osdir} fehlt")
    if "--pakete-aus" in argv:
        i = argv.index("--pakete-aus")
        alt = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    out = Path(argv[0]) if argv else ROOT / "dist" / "festpos.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    names = [n for n in _files() if not n.startswith("deploy/wheels/") and not pakete.RQ_FILE_RE.fullmatch(n)]
    crlf = [n for n in names if windows_line_endings(n, (ROOT / n).read_bytes())]
    if crlf:
        sys.exit(f"FEHLER: {len(crlf)} Dateien mit Windows-Zeilenenden (CRLF), z.B. {', '.join(crlf[:5])}\n"
                 "Auf dem Pi liefen damit die Skripte nicht. Neu klonen ohne Umwandlung:\n"
                 "  git clone -c core.autocrlf=false https://github.com/T-0-N/FestPOS.git")
    tmp = out.with_suffix(".tmp")
    with tempfile.TemporaryDirectory() as d:
        try:
            extra = pakete.fetch(Path(d), alt)
        except (pakete.PaketFehler, OSError, subprocess.CalledProcessError, zipfile.BadZipFile) as e:
            sys.exit(f"FEHLER: Pakete für den Pi: {e}")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            exe = _exec_in_git()
            for rel in names:
                _add(z, ROOT / rel, rel, exe)
            for rel in extra:
                _add(z, Path(d) / rel, rel)
            if osdir:                                  # Sicherheitsupdates des Betriebssystems (#5)
                for f in sorted(osdir.rglob("*")):
                    if f.is_file() and not f.is_symlink():
                        rel = "os-updates/" + f.relative_to(osdir).as_posix()
                        _add(z, f, rel)
                        extra.append(rel)
    tmp.replace(out)
    version = (ROOT / "VERSION").read_text().strip()
    print(f"{out}: Version {version}, {len(names) + len(extra)} Dateien, {out.stat().st_size} Bytes")


if __name__ == "__main__":
    main()
