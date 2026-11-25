"""Paketliste (SBOM, CycloneDX 1.5 JSON) für das Update-Paket und das Image – als Release-Datei (T-0-N/FestPOS#14).

  python3 tools/sbom.py zip > festpos.zip.cdx.json
  python3 tools/sbom.py image festpos-pakete.txt > festpos-VERSION.img.cdx.json

zip:   FestPOS selbst, die Python-Pakete (requirements.txt, feste Versionen mit Prüfsumme) und rqlite
       (deploy/rqlite.env, mit SHA256).
image: dasselbe und dazu alle Debian-Pakete des Images (festpos-pakete.txt: «Name<TAB>Version<TAB>Architektur»,
       schreibt der Image-Bau mit dpkg-query, deploy/pi-gen/stage-festpos).
Nur Standardbibliothek; die Ausgabe hängt nur von den Eingaben ab (gleiche Eingaben -> gleiche Datei, ausser Zeit).
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
REQ_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")
HASH_RE = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def _version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def python_components(req: Path = ROOT / "requirements.txt") -> list[dict]:
    """Python-Pakete aus requirements.txt (pip-compile mit --hash): Name, Version, alle Prüfsummen."""
    out, cur = [], None
    for line in req.read_text(encoding="utf-8").splitlines():
        m = REQ_RE.match(line.strip())
        if m:
            name, ver = m.group(1).lower().replace("_", "-"), m.group(2)
            cur = {"type": "library", "name": name, "version": ver, "purl": f"pkg:pypi/{name}@{ver}",
                   "bom-ref": f"pypi:{name}@{ver}"}
            out.append(cur)
        h = HASH_RE.search(line)
        if h and cur is not None:                           # alle Prüfsummen (Wheels je Plattform, sdist)
            cur.setdefault("hashes", []).append({"alg": "SHA-256", "content": h.group(1)})
    return sorted(out, key=lambda c: c["name"])


def rqlite_component(env: Path = ROOT / "deploy" / "rqlite.env") -> dict:
    vals = dict(re.findall(r"^(RQLITE_VERSION|RQLITE_SHA256)=(\S+)$", env.read_text(encoding="utf-8"), re.M))
    v = vals["RQLITE_VERSION"].lstrip("v")
    return {"type": "application", "name": "rqlite", "version": v, "bom-ref": f"rqlite@{v}",
            "purl": f"pkg:github/rqlite/rqlite@v{v}",
            "hashes": [{"alg": "SHA-256", "content": vals["RQLITE_SHA256"]}],
            "externalReferences": [{"type": "distribution",
                                    "url": f"https://github.com/rqlite/rqlite/releases/download/v{v}/"
                                           f"rqlite-v{v}-linux-arm64.tar.gz"}]}


def debian_components(listing: Path) -> list[dict]:
    """Debian-Pakete aus «Name<TAB>Version<TAB>Architektur» (dpkg-query -W)."""
    out = []
    for line in listing.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or not parts[0] or not parts[1]:
            continue
        name, ver, arch = parts[0], parts[1], parts[2]
        out.append({"type": "library", "name": name, "version": ver, "bom-ref": f"deb:{name}@{ver}:{arch}",
                    "purl": f"pkg:deb/debian/{quote(name)}@{quote(ver, safe='')}?arch={quote(arch)}"})
    return sorted(out, key=lambda c: c["name"])


def bom(kind: str, components: list[dict]) -> dict:
    v = _version()
    name = "festpos.zip" if kind == "zip" else f"festpos-{v}.img.xz"
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{uuid.uuid4()}", "version": 1,
            "metadata": {"timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "tools": {"components": [{"type": "application", "name": "festpos tools/sbom.py"}]},
                         "component": {"type": "application" if kind == "zip" else "operating-system",
                                       "name": name, "version": v, "bom-ref": f"festpos@{v}",
                                       "purl": f"pkg:github/T-0-N/FestPOS@v{v}"}},
            "components": components}


def main(argv: list[str]) -> int:
    if argv[1:2] == ["zip"] and len(argv) == 2:
        comps = [*python_components(), rqlite_component()]
    elif argv[1:2] == ["image"] and len(argv) == 3:
        deb = debian_components(Path(argv[2]))
        if not deb:
            print(f"Keine Debian-Pakete in {argv[2]} – Liste leer oder falsches Format.", file=sys.stderr)
            return 1
        comps = [*python_components(), rqlite_component(), *deb]
    else:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    json.dump(bom(argv[1], comps), sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
