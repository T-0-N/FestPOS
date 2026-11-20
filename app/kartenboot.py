"""Boot-Dateien einer Karte für den Pi mit Buchstabe x (T8 Kartenpaket, T10 «SD-Karte schreiben»).

Die einzige Stelle, die festpos.conf, node.conf, festpos-tls/ und die Update-Schlüssel für eine neue Karte
zusammenstellt – das Kartenpaket (ZIP zum Herunterladen) und die Karte, die der Pi selbst schreibt, bekommen damit
genau denselben Inhalt. Läuft als root (festpos-wiederherstellen, festpos-karte), braucht nur die Boot-Partition.

Fälle (Plan T10, 2.1):
  - Einzelbetrieb, Buchstabe b/c: CLUSTER_SIZE=3, CLUSTER_SECRET ausgeschrieben (Pi A wird danach umgestellt).
  - Einzelbetrieb, eigener Buchstabe (Ersatzkarte): festpos.conf unverändert (CLUSTER_SIZE=1).
  - Cluster: CLUSTER_SIZE=3 wie bisher, CLUSTER_SECRET ausgeschrieben.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import backup

LETTERS = ("a", "b", "c")


class KartenFehler(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def conf_text(boot: Path) -> str:
    try:
        return (boot / "festpos.conf").read_text(encoding="utf-8", errors="surrogateescape")
    except OSError:
        raise KartenFehler("conf") from None


def single(text: str) -> bool:
    return (backup.conf_value(text, "CLUSTER_SIZE") or "1").strip() == "1"


def own_letter(boot: Path) -> str:
    """Buchstabe dieses Pi (node.conf auf der Boot-Partition, sonst a)."""
    try:
        text = (boot / "node.conf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "a"
    v = (backup.conf_value(text, "NODE") or "a").strip().lower()
    return v if v in LETTERS else "a"


def effective_secret(text: str, boot: Path, etc: Path) -> str:
    """CLUSTER_SECRET, wie die App es benutzt: aus festpos.conf – oder abgeleitet aus dem Server-Schlüssel."""
    s = backup.conf_value(text, "CLUSTER_SECRET") or ""
    if s.strip() not in backup.PLACEHOLDER_SECRETS:
        return s
    for p in (boot / "festpos-tls" / "server.key", etc / "tls" / "server.key"):
        try:
            return backup.derived_secret(p.read_bytes())
        except OSError:
            continue
    raise KartenFehler("secret")


def boot_files(boot: Path, etc: Path, letter: str) -> dict[str, bytes]:
    """{relativer Pfad: Inhalt} für die Boot-Partition der Karte mit diesem Buchstaben."""
    if letter not in LETTERS:
        raise KartenFehler("letters")
    text = conf_text(boot)
    if single(text) and letter == own_letter(boot):
        conf = text                                           # Ersatzkarte des Einzel-Pi: gleich wie Pi A
    else:
        secret = effective_secret(text, boot, etc)
        conf = backup.conf_set(backup.conf_set(text, "CLUSTER_SIZE", "3"), "CLUSTER_SECRET", secret)
    out = {"festpos.conf": conf.encode("utf-8", errors="surrogateescape"), "node.conf": f"NODE={letter}\n".encode()}
    tls = {}
    for name in backup.TLS_FILES:
        p = boot / "festpos-tls" / name
        if p.is_file():
            tls[name] = p.read_bytes()
    if not {"ca.crt", "server.crt", "server.key"} <= set(tls):
        raise KartenFehler("tls_files")
    out.update({f"festpos-tls/{n}": v for n, v in tls.items()})
    for e in os.scandir(boot):
        if backup.UPDATE_KEY_RE.match(e.name) and e.is_file(follow_symlinks=False):
            out[e.name] = Path(e.path).read_bytes()
    return out
