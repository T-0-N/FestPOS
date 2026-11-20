"""Übersetzungen: alle Texte für Bedienung, Verwaltung, Statusanzeige, Bons und Server-Meldungen stehen in
app/i18n/<sprache>.json – nicht im Code. Die Browser-Seiten laden dieselben Dateien (static/i18n.js).

Aufbau einer Sprachdatei (flach, ein Schlüssel pro Text, immer mindestens drei Teile «bereich.thema.text» –
sonst findet tools/i18n_check.py die Verwendung im Code nicht):
    "_meta": {"name": "Deutsch", "locale": "de-CH"}
    "err.order.empty": "Bestellung ist leer"
    "app.cart.items": {"one": "{count} Artikel", "other": "{count} Artikel"}     # Mehrzahl (CLDR-Kategorien)
Platzhalter: {name}. Ausgangssprache ist Deutsch (de): fehlt ein Text in einer anderen Sprache, gilt der deutsche.

Neue Sprache: de.json kopieren, z.B. als fr.json, "_meta" anpassen und die Werte übersetzen (Schlüssel und
{Platzhalter} unverändert lassen). tools/i18n_check.py prüft fehlende/überzählige Schlüssel und Platzhalter.

Welche Sprache gilt:
  - Anfrage vom Handy/aus der Verwaltung: Kopfzeile X-Festpos-Lang (Wahl auf dem Gerät), sonst Standard
  - Bons: Sprache des Druckers (Verwaltung › Drucker), sonst Standard
  - Standard: Einstellung «language» (Verwaltung › Einstellungen), sonst de
Protokolle (journalctl) bleiben bewusst deutsch – sie sind für den Betrieb, nicht für die Bedienung.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("festpos.i18n")

SOURCE = "de"                                        # Ausgangssprache: vollständig, Rückfall für alle anderen
CODE_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
_HERE = Path(__file__).resolve().parent / "i18n"

current: contextvars.ContextVar[str | None] = contextvars.ContextVar("festpos_lang", default=None)


_dirs_cache: dict[str, tuple[Path, ...]] = {}


def _dirs() -> tuple[Path, ...]:
    """Zusätzliche Ordner (FESTPOS_I18N_DIR, mit : getrennt) vor dem eingebauten – z.B. für Tests."""
    raw = os.environ.get("FESTPOS_I18N_DIR", "")
    hit = _dirs_cache.get(raw)
    if hit is None:
        hit = _dirs_cache[raw] = (*(Path(p) for p in raw.split(os.pathsep) if p.strip()), _HERE)
    return hit


_lock = threading.Lock()
_cache: dict[Path, tuple[float, dict, float]] = {}      # Datei -> (mtime, Inhalt, nächste Prüfung)
RECHECK_S = 2.0                                        # t() wird oft aufgerufen: Dateien höchstens alle 2 s prüfen


def _read(path: Path) -> dict:
    """Sprachdatei lesen (neu, sobald sie sich ändert). Kaputte Datei: leer statt Absturz."""
    now = time.monotonic()
    with _lock:
        hit = _cache.get(path)
        if hit and now < hit[2]:
            return hit[1]
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    with _lock:
        hit = _cache.get(path)
        if hit and hit[0] == mtime:
            _cache[path] = (mtime, hit[1], now + RECHECK_S)
            return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("kein Objekt")
    except (OSError, ValueError) as e:
        log.error("Sprachdatei %s nicht lesbar: %s", path, e)
        data = {}
    with _lock:
        _cache[path] = (mtime, data, now + RECHECK_S)
    return data


_files_cache: dict[tuple, tuple[float, dict[str, Path]]] = {}


def _files() -> dict[str, Path]:
    """Vorhandene Sprachdateien (Code -> Pfad), für einige Sekunden gemerkt."""
    dirs = _dirs()
    now = time.monotonic()
    hit = _files_cache.get(dirs)
    if hit and now < hit[0]:
        return hit[1]
    out: dict[str, Path] = {}
    for d in dirs:
        try:
            for p in sorted(d.glob("*.json")):
                if CODE_RE.match(p.stem) and p.stem not in out:
                    out[p.stem] = p
        except OSError:
            continue
    _files_cache[dirs] = (now + RECHECK_S, out)
    return out


def available() -> list[dict]:
    """[{"code": "de", "name": "Deutsch", "locale": "de-CH"}, …] – Ausgangssprache zuerst."""
    out = []
    for code, path in _files().items():
        meta = _read(path).get("_meta") or {}
        out.append({"code": code, "name": str(meta.get("name") or code), "locale": str(meta.get("locale") or code)})
    out.sort(key=lambda x: (x["code"] != SOURCE, x["name"].lower()))
    return out


def normalize(lang: str | None, files: dict[str, Path] | None = None) -> str | None:
    """"de-CH", "de_CH", "DE" -> "de", "rm" -> "rm-vallader", wenn vorhanden; sonst None."""
    if not lang:
        return None
    if files is None:
        files = _files()
    if lang in files:
        return lang
    raw = str(lang).strip().replace("_", "-")
    base = raw.split("-")[0].lower()
    for cand in (raw, raw.lower(), base):
        if cand in files:
            return cand
    for code in sorted(files):                       # "rm", "rm-CH" -> "rm-vallader" (Variante derselben Sprache)
        if code.split("-")[0] == base:
            return code
    return None


def catalog(lang: str | None) -> dict:
    """Alle Texte einer Sprache, fehlende aus der Ausgangssprache ergänzt (für die Browser-Seiten)."""
    code = normalize(lang) or default_lang()
    files = _files()
    base = _read(files[SOURCE]) if SOURCE in files else {}
    if code == SOURCE or code not in files:
        merged = dict(base)
        code = SOURCE if SOURCE in files else code
    else:
        merged = {**base, **{k: v for k, v in _read(files[code]).items() if v not in (None, "")}}
    meta = merged.get("_meta") or {}
    merged["_meta"] = {**meta, "code": code}
    return merged


# -- Standardsprache (Einstellung in der Verwaltung) ---------------------------------------------------
_default = {"value": SOURCE, "until": 0.0}
_loader: Callable[[], str] | None = None


def set_default_loader(fn: Callable[[], str] | None) -> None:
    """Woher die Standardsprache kommt (Einstellung «language» in der Datenbank)."""
    global _loader
    _loader = fn
    _default.update(value=SOURCE, until=0.0)             # neue Quelle: bisheriger Wert gilt nicht mehr


def _reload_default(now: float) -> None:
    try:
        _default["value"] = normalize(_loader()) or SOURCE if _loader else SOURCE
    except Exception:  # noqa: BLE001 – Datenbank noch nicht bereit: bisherigen Wert behalten
        _default["until"] = now + 2


def default_lang() -> str:
    """Standardsprache, höchstens 30 s alt (auch Änderungen von einem anderen Pi im Cluster kommen an).
    Im Event-Loop wird nie auf die Datenbank gewartet: dann gilt der bisherige Wert, neu gelesen wird nebenher
    (ein hängender Pi im Cluster bremst sonst jede Anfrage)."""
    now = time.monotonic()
    if _loader is not None and now >= _default["until"]:
        _default["until"] = now + 30
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            _reload_default(now)
        else:
            loop.run_in_executor(None, _reload_default, now)
    return _default["value"] if normalize(_default["value"]) else SOURCE


def forget_default() -> None:
    """Nach dem Speichern der Einstellung sofort neu lesen (die Datenbank hat eben geantwortet)."""
    now = time.monotonic()
    _default["until"] = now + 30
    _reload_default(now)


# -- Übersetzen ------------------------------------------------------------------------------------------
# CLDR-Mehrzahlregeln (vereinfacht, ganze Zahlen) für die gängigen Sprachen; sonst gilt "other"
_ONE_IS_0_1 = {"fr", "pt", "hi", "fa", "am"}
_NO_PLURAL = {"ja", "zh", "ko", "th", "vi", "id", "tr"}


def plural_category(lang: str, n: Any) -> str:
    try:
        n = abs(int(n))
    except (TypeError, ValueError):
        return "other"
    base = lang.split("-")[0]
    if base in _NO_PLURAL:
        return "other"
    if base in _ONE_IS_0_1:
        return "one" if n in (0, 1) else "other"
    if base in ("ru", "uk", "pl", "cs", "sk", "hr", "sr", "bs", "sl", "lt", "lv"):
        if base in ("cs", "sk"):
            return "one" if n == 1 else "few" if 2 <= n <= 4 else "other"
        if n % 10 == 1 and n % 100 != 11:
            return "one"
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return "few"
        return "many"
    return "one" if n == 1 else "other"


_PH = re.compile(r"\{(\w+)\}")


def _fill(text: str, params: dict) -> str:
    return _PH.sub(lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), text)


def _code(lang: str | None, files: dict[str, Path]) -> str:
    return normalize(lang, files) or normalize(current.get(), files) or default_lang()


def lookup(key: str, lang: str | None = None) -> Any:
    files = _files()
    code = _code(lang, files)
    for c in (code, SOURCE):
        if c in files:
            v = _read(files[c]).get(key)
            if v not in (None, ""):
                return v
    return None


def t(key: str, lang: str | None = None, /, **params: Any) -> str:
    """Text zum Schlüssel in der gewünschten (sonst aktuellen) Sprache. Mehrzahl über count=…"""
    v = lookup(key, lang)
    if v is None:
        log.warning("Text fehlt: %s", key)
        return key
    if isinstance(v, dict):
        cat = plural_category(_code(lang, _files()), params.get("count", 0))
        v = v.get(cat) or v.get("other") or next(iter(v.values()), key)
    return _fill(str(v), params)


def request_lang(header: str | None, accept: str | None = None) -> str | None:
    """Sprache einer Anfrage: Wahl auf dem Gerät (X-Festpos-Lang), sonst Browser-Sprache, sonst None (=Standard)."""
    if header:
        code = normalize(header)
        if code:
            return code
    if accept:
        for part in accept.split(","):
            code = normalize(part.split(";")[0].strip())
            if code:
                return code
    return None
