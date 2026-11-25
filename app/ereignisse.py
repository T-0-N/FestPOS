"""Sicherheitsereignisse eines Pi sammeln (T-0-N/FestPOS#11) – Teil für root.

deploy/festpos-ereignisse ruft collect() alle 20 Sekunden (festpos-ereignisse.timer) als root auf; festpos-update
meldet abgewiesene fremd signierte Pakete mit append(). Die App (app/sicherheit.py) liest die Datei OUT und trägt neue
Ereignisse ins Protokoll ein – über die Datenbank sehen sie alle Pi.

Gesammelt wird:
- SSH-Anmeldung (sshd «Accepted … for BENUTZER from ADRESSE») und sudo (Befehl, Fehlversuche) aus dem Journal,
- Neustart (andere boot_id als beim letzten Lauf),
- geänderte Dateien auf der Boot-Partition: beim ersten Lauf nach dem Start gegen den Stand vor dem Herunterfahren
  verglichen (Prüfsumme) – so fällt auf, wer die SD-Karte an einem anderen Rechner geändert hat. Änderungen während
  des Betriebs (FestPOS selbst, Updates) übernimmt jeder Lauf still; wer dafür root braucht, steht ohnehin als sudo da.

Datei OUT (für die App lesbar, nur root schreibt): {"run": Kennung, "seq": letzte Nummer, "events": [{seq, at, kind,
…}]} – höchstens KEEP Ereignisse. «run» ändert sich nur, wenn die Datei neu entsteht (dann zählt die App von vorne).
Zustand (nur root): STATE/stand.json mit Journal-Cursor, boot_id und Liste der Boot-Dateien.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

R = os.environ.get("FESTPOS_TEST_ROOT", "")
DIR = Path(os.environ.get("FESTPOS_EREIGNISSE_DIR", f"{R}/var/lib/festpos-ereignisse"))
OUT = DIR / "ereignisse.json"
STATE = DIR / "stand" / "stand.json"
KEEP = 300
JOURNAL = os.environ.get("FESTPOS_JOURNALCTL", "journalctl")
IDENTS = ("sshd", "sshd-session", "sudo")
FIRST_SINCE = "-10min"                             # erster Lauf überhaupt: so weit zurück ins Journal

SSH_RE = re.compile(r"^Accepted (\S+) for (\S+) from (\S+) port \d+")
SUDO_RE = re.compile(r"^\s*(\S+) : .*?USER=(\S+) ; COMMAND=(.*)$")
SUDO_FAIL_RE = re.compile(r"^\s*(\S+) : (\d+) incorrect password attempts?")
KINDS = ("ssh", "sudo", "sudo_failed", "reboot", "boot_changed", "update_foreign")


def boot_dir() -> Path:
    b = Path(os.environ.get("FESTPOS_BOOT", f"{R}/boot/firmware"))
    return b if b.is_dir() else Path(f"{R}/boot")


# -- Datei für die App --------------------------------------------------------------------------------------------
@contextlib.contextmanager
def _locked():
    DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DIR, 0o755)
    with open(DIR / ".lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def read(path: Path | None = None) -> dict:
    try:
        d = json.loads((path or OUT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"run": "", "seq": 0, "events": []}
    return d if isinstance(d, dict) and isinstance(d.get("events"), list) else {"run": "", "seq": 0, "events": []}


def _write(d: dict) -> None:
    tmp = OUT.with_name(".ereignisse.neu")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    os.fchmod(fd, 0o644)
    with open(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, OUT)


def append(*events: dict) -> None:
    """Ereignisse anhängen (Nummer und Zeit kommen dazu). Auch für festpos-update (fremd signiert)."""
    if not events:
        return
    with _locked():
        d = read()
        if not d.get("run"):
            d = {"run": secrets.token_hex(6), "seq": 0, "events": []}
        for e in events:
            d["seq"] = int(d.get("seq") or 0) + 1
            d["events"].append({"seq": d["seq"], "at": int(e.pop("at", None) or time.time()), **e})
        d["events"] = d["events"][-KEEP:]
        _write(d)


# -- Sammeln ------------------------------------------------------------------------------------------------------
def _state() -> dict:
    try:
        d = json.loads(STATE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE.parent, 0o700)
    tmp = STATE.with_suffix(".neu")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, STATE)


def journal(cursor: str | None) -> tuple[list[dict], str | None]:
    """Neue Einträge von sshd und sudo -> (Ereignisse, neuer Cursor)."""
    cmd = [JOURNAL, "-o", "json", "--no-pager", "-q"] + [f"SYSLOG_IDENTIFIER={i}" for i in IDENTS]
    cmd[5:5] = ["--after-cursor", cursor] if cursor else ["--since", FIRST_SINCE]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return [], cursor
    events = []
    for line in out.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        cursor = e.get("__CURSOR") or cursor
        msg, ident = str(e.get("MESSAGE") or ""), e.get("SYSLOG_IDENTIFIER")
        try:
            at = int(int(e.get("__REALTIME_TIMESTAMP")) / 1_000_000)
        except (TypeError, ValueError):
            at = int(time.time())
        if ident in ("sshd", "sshd-session") and (m := SSH_RE.match(msg)):
            events.append({"kind": "ssh", "at": at, "user": m[2][:40], "ip": m[3][:60], "how": m[1][:30]})
        elif ident == "sudo" and (m := SUDO_FAIL_RE.match(msg)):          # zuerst: steht auch mit COMMAND=
            events.append({"kind": "sudo_failed", "at": at, "user": m[1][:40], "count": int(m[2])})
        elif ident == "sudo" and (m := SUDO_RE.match(msg)):
            events.append({"kind": "sudo", "at": at, "user": m[1][:40], "as": m[2][:40], "cmd": m[3].strip()[:200]})
    return events, cursor


def boot_id() -> str:
    try:
        return Path(f"{R}/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def boot_files(old: dict) -> dict:
    """{Pfad: [Grösse, mtime_ns, sha256]} der Boot-Partition – Prüfsumme nur neu, wenn sich Grösse/Zeit änderten.
    old leer: alles neu rechnen (nach einem Neustart – am PC lassen sich Grösse und Zeit gleich halten)."""
    base, out = boot_dir(), {}
    for p in sorted(base.rglob("*")):
        try:
            st = p.lstat()
        except OSError:
            continue
        if not p.is_file() or p.is_symlink():
            continue
        rel = str(p.relative_to(base))
        prev = old.get(rel)
        if prev and prev[0] == st.st_size and prev[1] == st.st_mtime_ns:
            out[rel] = prev
            continue
        try:
            out[rel] = [st.st_size, st.st_mtime_ns, _sha(p)]
        except OSError:
            continue
    return out


def boot_diff(old: dict, new: dict) -> list[str]:
    changed = [k for k in new if k in old and old[k][2] != new[k][2]]
    added = [k for k in new if k not in old]
    removed = [k for k in old if k not in new]
    return sorted(changed) + [f"+{k}" for k in sorted(added)] + [f"-{k}" for k in sorted(removed)]


def collect() -> list[dict]:
    """Ein Lauf: Journal, Neustart, Boot-Partition -> neue Ereignisse (auch angehängt)."""
    st = _state()
    events, cursor = journal(st.get("cursor"))
    bid, files_before = boot_id(), st.get("boot") or {}
    new_boot = bool(st.get("boot_id")) and bid != st.get("boot_id")
    files = boot_files({} if new_boot else files_before)    # nach einem Neustart jede Prüfsumme neu
    if new_boot:
        events.append({"kind": "reboot"})
        diff = boot_diff(files_before, files) if files_before else []
        if diff:
            events.append({"kind": "boot_changed", "files": diff[:30], "count": len(diff)})
    append(*events)
    _save_state({"cursor": cursor, "boot_id": bid, "boot": files})
    return events


if __name__ == "__main__":
    collect()
