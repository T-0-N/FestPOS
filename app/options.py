"""Produkt-Optionen ("2 Zucker", "ohne Zwiebeln", Freitext).

Gespeichert pro Produkt als JSON in products.options:
    {"groups": [{"name": "Zucker", "choices": ["ohne", "1 Zucker", "2 Zucker"],
                 "required": true, "multi": false}, ...],
     "free": true}                       # Freitext-Bemerkung erlaubt

Textform (CSV-Spalte "optionen"): Gruppen mit ";" getrennt, Auswahl mit "|".
    Zucker*: ohne|1 Zucker|2 Zucker; Milch+: Hafer|laktosefrei
    *  = Pflicht (muss gewählt werden)
    +  = Mehrfachauswahl
"""
from __future__ import annotations

import json
import re

from . import i18n

MAX_GROUPS = 8
MAX_CHOICES = 16
MAX_LEN = 30          # Länge eines Gruppennamens / einer Auswahl
NOTE_LEN = 60         # Freitext pro Position


class OptionsError(ValueError):
    pass


def _clean(s: str, n: int = MAX_LEN) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def normalize(data: dict | None) -> dict | None:
    """Prüft/bereinigt eine Optionsdefinition. None = Produkt hat keine Optionen."""
    if not data:
        return None
    groups = []
    for g in (data.get("groups") or [])[:MAX_GROUPS]:
        name = _clean(g.get("name", ""))
        seen, choices = set(), []
        for c in (g.get("choices") or [])[:MAX_CHOICES]:
            c = _clean(c)
            if c and c.lower() not in seen:
                seen.add(c.lower())
                choices.append(c)
        if not choices:
            if name:
                raise OptionsError(i18n.t("err.options.group_empty", name=name))
            continue
        groups.append({"name": name, "choices": choices,
                       "required": bool(g.get("required")), "multi": bool(g.get("multi"))})
    free = bool(data.get("free"))
    if not groups and not free:
        return None
    return {"groups": groups, "free": free}


def dumps(data: dict | None) -> str | None:
    n = normalize(data)
    return json.dumps(n, ensure_ascii=False, separators=(",", ":")) if n else None


def loads(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return normalize(json.loads(raw))
    except (ValueError, TypeError, AttributeError):
        return None


def parse_text(text: str, free: bool = False) -> dict | None:
    """'Zucker*: ohne|1 Zucker; Milch+: Hafer' -> Definition."""
    groups = []
    for part in re.split(r"[;\n]", text or ""):
        part = part.strip()
        if not part:
            continue
        name, sep, rest = part.partition(":")
        if not sep:
            name, rest = "", part
        name = name.strip()
        flags = ""
        while name and name[-1] in "*+":
            flags += name[-1]
            name = name[:-1].rstrip()
        groups.append({"name": name, "choices": [c for c in rest.split("|")],
                       "required": "*" in flags, "multi": "+" in flags})
    return normalize({"groups": groups, "free": free})


def to_text(data: dict | None) -> str:
    if not data:
        return ""
    out = []
    for g in data.get("groups", []):
        flags = ("*" if g.get("required") else "") + ("+" if g.get("multi") else "")
        head = f"{g['name']}{flags}: " if (g["name"] or flags) else ""
        out.append(head + "|".join(g["choices"]))
    return "; ".join(out)


CHOICE_LEN = MAX_LEN * 2 + 2      # «Gruppe: Auswahl»


def clean_choice_list(values: list | None, dedupe: bool = True) -> list[str]:
    """Gewählte Optionen einer Bestellposition (vom Handy). Absichtlich tolerant: eine Bestellung
    aus dem Offline-Puffer soll nicht scheitern, nur weil die Optionen inzwischen geändert wurden."""
    out = []
    for v in (values or [])[:MAX_GROUPS * MAX_CHOICES]:
        v = _clean(v, CHOICE_LEN)
        if v and (not dedupe or v not in out):
            out.append(v)
    return out


def label_choices(defn: dict | None, values: list[str]) -> list[str]:
    """Gleiche Auswahl in mehreren Gruppen («ohne» bei Zucker UND bei Milch) bekommt den Gruppennamen
    («Zucker: ohne», «Milch: ohne») – sonst weiss die Küche nicht, was gemeint ist (REV-020).

    Die App schickt das schon so. Ältere Apps (Offline-Puffer nach einem Update) schickten nur «ohne»,
    in Gruppen-Reihenfolge: kommt es so oft vor, wie es Gruppen damit gibt, ist die Zuordnung eindeutig.
    Sonst bleibt es unverändert stehen – lieber zweimal «ohne» als eine falsche Gruppe."""
    groups = (defn or {}).get("groups") or []

    def where(v: str) -> list[int]:
        return [i for i, g in enumerate(groups) if any(c.lower() == v.lower() for c in g["choices"])]

    seen: dict[str, int] = {}
    out: list[str] = []
    for v in values:
        idx = where(v)
        if len(idx) > 1:
            k = v.lower()
            n = seen.get(k, 0)
            seen[k] = n + 1
            if sum(1 for x in values if x.lower() == k) == len(idx) and groups[idx[n]]["name"]:
                out.append(f"{groups[idx[n]]['name']}: {v}")
            else:
                out.append(v)                                  # nicht eindeutig: so lassen, nicht zusammenlegen
        elif v not in out:
            out.append(v)
    return out


def group_label(defn: dict | None, gi: int, choice: str) -> str:
    """Auswahl `choice` der Gruppe Nummer `gi`, mit Gruppennamen, wenn eine andere Gruppe gleich heisst
    («Beilage: Pommes» neben «Extra: Pommes») – wie die Bestell-App (static/app/order.js, optLabel)."""
    groups = (defn or {}).get("groups") or []
    g, lc = groups[gi], choice.lower()
    dup = g["name"] and any(hi != gi and any(c.lower() == lc for c in h["choices"]) for hi, h in enumerate(groups))
    return f"{g['name']}: {choice}" if dup else choice


def clean_note(s: str | None) -> str:
    return _clean(s or "", NOTE_LEN)
