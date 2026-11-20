"""Hilfen für Tests mit signierten Update-Paketen (Version 2026.10.10-26): Schlüssel, Paket bauen, signieren."""
from __future__ import annotations

import os
import stat
import zipfile
from pathlib import Path

from app import release

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".git", "wheels", "tests", "docs"}


def keypair(comment: str = "Test Update"):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    pk = Ed25519PrivateKey.generate()
    return pk, release.public_line(pk, comment)


def build_bundle(dst: Path, version: str, prefix: str = "festpos/", extra: dict[str, bytes] | None = None) -> Path:
    """Projekt wie festpos.zip (ohne Python-Pakete, Tests, Doku), VERSION ersetzt."""
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, names in os.walk(ROOT):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for n in sorted(names):
                p = Path(root) / n
                rel = p.relative_to(ROOT).as_posix()
                if n.endswith((".pyc", ".db")) or rel in ("VERSION", "deploy/festpos.conf"):
                    continue
                info = zipfile.ZipInfo.from_file(p, prefix + rel)
                info.external_attr = ((0o100755 if p.stat().st_mode & stat.S_IXUSR else 0o100644) & 0xFFFF) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, p.read_bytes())
        z.writestr(prefix + "VERSION", version + "\n")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return dst


def sign(zip_path: Path, pk, sig_path: Path | None = None, **kw) -> Path:
    sig_path = sig_path or zip_path.with_name(zip_path.name + ".sig")
    sig_path.write_bytes(release.sign(zip_path, pk, **kw))
    return sig_path
