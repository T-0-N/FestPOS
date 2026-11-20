"""Fremde Pakete für den Pi beim Bauen holen – nicht im Git: die Python-Pakete (aarch64, Python 3.13) aus
requirements.txt und rqlite aus deploy/rqlite.env. Jede Datei wird mit ihrer Prüfsumme geprüft (requirements.txt
bzw. RQLITE_SHA256); eine andere Datei kommt nie ins Paket. Danach liegen sie dort, wo setup.sh und update.sh sie
ohne Internet finden:

  ZIEL/deploy/wheels/*.whl
  ZIEL/deploy/rqlite-<version>-linux-arm64.tar.gz

  python3 tools/pakete.py ZIEL                  holen (zuerst aus dem Zwischenspeicher dist/pakete/, sonst Internet)
  python3 tools/pakete.py ZIEL --aus ALT.zip    zuerst aus einer früheren festpos.zip nehmen (ohne Internet, z.B.
                                                für eine Fehlerbehebung am Anlass, wenn sich die Pakete nicht änderten)

tools/build_zip.py und das Image (deploy/pi-gen/build.sh) rufen es selbst auf.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "dist" / "pakete"
PY = "3.13"
PLATFORMS = ["manylinux2014_aarch64", "manylinux_2_17_aarch64", "manylinux_2_28_aarch64", "manylinux_2_34_aarch64"]
RQ_URL = "https://github.com/rqlite/rqlite/releases/download/{v}/rqlite-{v}-linux-arm64.tar.gz"
MAX = 100 * 1024 * 1024
RQ_FILE_RE = re.compile(r"deploy/rqlite-v[\d.]+-linux-arm64\.tar\.gz")


class PaketFehler(Exception):
    pass


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirements(path: Path = ROOT / "requirements.txt") -> dict[str, tuple[str, set[str]]]:
    """requirements.txt -> {name: (version, {sha256, …})}."""
    out: dict[str, tuple[str, set[str]]] = {}
    cur = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s\\]+)", line)
        if m:
            cur = _norm(m[1])
            out[cur] = (m[2], set())
        for h in re.findall(r"--hash=sha256:([0-9a-f]{64})", line):
            if cur is None:
                raise PaketFehler("requirements.txt: Prüfsumme ohne Paket")
            out[cur][1].add(h)
    return out


def _wheel_name(fname: str) -> str | None:
    """Paketname einer .whl, nur wenn sie auf den Pi passt (aarch64 oder für alle)."""
    m = re.match(r"^(?P<n>[^-]+)-[^-]+-.+-(?P<plat>[^-]+)\.whl$", fname)
    if not m or not (m["plat"] == "any" or "aarch64" in m["plat"]):
        return None
    return _norm(m["n"])


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rqlite_spec(path: Path = ROOT / "deploy" / "rqlite.env") -> tuple[str, str, str]:
    vals = dict(re.findall(r"^(RQLITE_VERSION|RQLITE_SHA256)=(\S+)$", path.read_text(encoding="utf-8"), re.M))
    v, sha = vals.get("RQLITE_VERSION", ""), vals.get("RQLITE_SHA256", "")
    if not re.fullmatch(r"v\d+\.\d+\.\d+", v) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise PaketFehler("deploy/rqlite.env: RQLITE_VERSION oder RQLITE_SHA256 fehlt")
    return v, sha, f"rqlite-{v}-linux-arm64.tar.gz"


def _from_zip(alt: Path | None) -> dict[str, bytes]:
    """Dateien aus deploy/wheels und deploy/rqlite-*.tar.gz einer früheren festpos.zip (Name -> Inhalt)."""
    if not alt:
        return {}
    out = {}
    with zipfile.ZipFile(alt) as z:
        for info in z.infolist():
            parts = info.filename.split("/")
            if info.is_dir() or info.file_size > MAX:
                continue
            if (len(parts) >= 3 and parts[-2] == "wheels" and parts[-3] == "deploy" and parts[-1].endswith(".whl")) or \
                    (len(parts) >= 2 and parts[-2] == "deploy" and re.fullmatch(r"rqlite-v[\d.]+-linux-arm64\.tar\.gz", parts[-1])):
                out[parts[-1]] = z.read(info)
    return out


def _pip_download(dest: Path) -> None:
    cmd = [sys.executable, "-m", "pip", "download", "-q", "--require-hashes", "--no-deps", "--only-binary=:all:",
           "--python-version", PY, "--implementation", "cp", "--abi", "cp313", "--abi", "abi3", "--abi", "none",
           "-r", str(ROOT / "requirements.txt"), "-d", str(dest)]
    for p in PLATFORMS:
        cmd += ["--platform", p]
    subprocess.run(cmd + ["--platform", "any"], check=True)


def wheels(target: Path, old: dict[str, bytes], log=print) -> int:
    """Für jedes Paket aus requirements.txt genau die passende .whl nach target/deploy/wheels legen."""
    req = requirements()
    out = target / "deploy" / "wheels"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    found: dict[str, tuple[str, bytes]] = {}

    def take(fname: str, data: bytes) -> None:
        name = _wheel_name(fname)
        if name in req and name not in found and _sha(data) in req[name][1]:
            found[name] = (fname, data)
    for fname, data in old.items():
        take(fname, data)
    cache = CACHE / "wheels"
    if len(found) < len(req) and cache.is_dir():
        for f in sorted(cache.glob("*.whl")):
            take(f.name, f.read_bytes())
    if len(found) < len(req):
        log(f"Python-Pakete: {len(req) - len(found)} fehlen – lade von PyPI (Prüfsummen aus requirements.txt)")
        cache.mkdir(parents=True, exist_ok=True)
        _pip_download(cache)
        for f in sorted(cache.glob("*.whl")):
            take(f.name, f.read_bytes())
    missing = sorted(set(req) - set(found))
    if missing:
        raise PaketFehler(f"Python-Pakete fehlen oder haben eine falsche Prüfsumme: {', '.join(missing)}")
    for fname, data in found.values():
        (out / fname).write_bytes(data)
    return len(found)


def rqlite(target: Path, old: dict[str, bytes], log=print) -> str:
    v, sha, fname = rqlite_spec()
    data = old.get(fname)
    cached = CACHE / fname
    if (data is None or _sha(data) != sha) and cached.is_file():
        data = cached.read_bytes()
    if data is None or _sha(data) != sha:
        log(f"rqlite {v}: lade von GitHub (Prüfsumme aus deploy/rqlite.env)")
        with urllib.request.urlopen(RQ_URL.format(v=v), timeout=120) as r:
            data = r.read(MAX + 1)
        if _sha(data) != sha:
            raise PaketFehler(f"rqlite {v}: falsche Prüfsumme – nicht übernommen")
        CACHE.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    (target / "deploy").mkdir(parents=True, exist_ok=True)
    for f in (target / "deploy").glob("rqlite-v*-linux-arm64.tar.gz"):
        f.unlink()
    (target / "deploy" / fname).write_bytes(data)
    return fname


def fetch(target: Path, alt: Path | None = None, log=print) -> list[str]:
    """Alles holen -> Liste der Pfade relativ zu target (für das ZIP)."""
    old = _from_zip(alt)
    n = wheels(target, old, log)
    rq = rqlite(target, old, log)
    log(f"Pakete: {n} Python-Pakete, {rq}")
    return sorted([f"deploy/wheels/{f.name}" for f in (target / "deploy" / "wheels").iterdir()] + [f"deploy/{rq}"])


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    alt = None
    if "--aus" in argv:
        i = argv.index("--aus")
        if i + 1 >= len(argv):
            print(__doc__)
            return 2
        alt = Path(argv[i + 1])
        args = [a for a in args if a != argv[i + 1]]
    if len(args) != 1:
        print(__doc__)
        return 2
    try:
        fetch(Path(args[0]), alt)
    except (PaketFehler, OSError, subprocess.CalledProcessError, zipfile.BadZipFile) as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

