#!/usr/bin/env python3
"""FestPOS – vor dem Start von rqlited: gehören die Daten dieses Knotens noch zum laufenden Cluster?

Wird von festpos-rqlited aufgerufen, wenn der Knoten schon Mitglied ist und 3 Pi konfiguriert sind.
Fragt die anderen Pi (GET /api/health/cluster) und gibt EINE Zeile aus: "<urteil> <erklärung>".

  ok        normal starten
  veraltet  ein anderer Pi lief im Notfall-Einzelbetrieb, oder ein laufender Cluster führt diesen Knoten nicht
            mehr als Mitglied: alte Daten beiseite legen und neu beitreten
  fremd     dieser Knoten war im Einzelbetrieb, inzwischen bilden die anderen mit ihrem ALTEN Stand eine
            Mehrheit: nicht starten, sonst würden die Daten aus dem Einzelbetrieb überschrieben
  konflikt  widersprüchliche Antworten: nicht starten, ein Mensch muss entscheiden

Gewartet wird, bis alle anderen Pi geantwortet haben, einer einen Leader kennt oder die Zeit um ist.
Antwortet keiner (z.B. die anderen sind aus), ist das Urteil "ok".
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

PATH = "/api/health/cluster"


def ask(base: str, ctx: ssl.SSLContext | None, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(base.rstrip("/") + PATH, timeout=timeout, context=ctx) as r:
            data = json.load(r)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 – Pi aus, App noch nicht bereit, alte Version ohne diese Adresse …
        return None


def decide(me: str, answers: dict[str, dict], einzelbetrieb: bool) -> tuple[str, str]:
    leaders: dict[str, dict] = {}
    for a in answers.values():
        if a.get("state") in ("Leader", "Follower") and a.get("leader"):
            leaders.setdefault(str(a["leader"]), a)
    # Ein anderer Pi meldet «Einzelbetrieb»: er hat die aktuellen Daten – solange kein ANDERER Pi einen
    # laufenden Cluster führt. (Die App antwortet schon, bevor ihre Datenbank läuft; so ist auch
    # gleichzeitiges Einschalten aller Pi sicher.) Führt ein anderer einen Cluster, entscheidet unten die
    # Mitgliedschaft; der Pi im Einzelbetrieb verweigert dann selbst den Start («fremd»).
    flagged = sorted(str(a.get("node")) for a in answers.values() if a.get("einzelbetrieb") and str(a.get("node")) != me)
    if flagged and (not leaders or set(leaders) <= set(flagged)):
        if einzelbetrieb:
            return "konflikt", f"Pi {', '.join(flagged)} und dieser Pi waren beide im Einzelbetrieb"
        return "veraltet", f"Pi {flagged[0]} lief im Notfall-Einzelbetrieb und hat die aktuellen Daten"
    if not leaders:
        return "ok", "kein anderer Pi kennt gerade einen Leader"
    if len(leaders) > 1:
        return "konflikt", "die anderen Pi melden verschiedene Leader (" + ", ".join(sorted(leaders)) + ")"
    leader, a = next(iter(leaders.items()))
    members = [str(m) for m in a.get("members") or []]
    if me not in members:
        if einzelbetrieb:
            return "konflikt", (f"Pi {leader} führt einen Cluster ohne diesen Pi ({', '.join(members)}), "
                                "dieser Pi war aber selbst im Einzelbetrieb")
        return "veraltet", f"der laufende Cluster (Leader {leader}, Mitglieder {', '.join(members)}) führt diesen Pi nicht mehr"
    if einzelbetrieb and leader != me:
        return "fremd", (f"Pi {leader} führt einen Cluster mit ÄLTEREM Stand ({', '.join(members)}); "
                         "die Daten aus dem Einzelbetrieb liegen auf diesem Pi")
    return "ok", f"Mitglied im Cluster mit Leader {leader}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--node", required=True)
    p.add_argument("--peers", default="", help="Basis-URLs, mit Komma getrennt (z.B. https://192.168.55.12:8443)")
    p.add_argument("--self-ip", default="", help="eigene IP – diese Adresse wird nicht gefragt")
    p.add_argument("--cafile", default="")
    p.add_argument("--wait", type=float, default=15.0, help="so lange auf Antworten warten (Sekunden)")
    p.add_argument("--einzelbetrieb", action="store_true")
    a = p.parse_args(argv)

    peers = [u.strip() for u in a.peers.split(",") if u.strip()]
    if a.self_ip:
        peers = [u for u in peers if urlsplit(u).hostname != a.self_ip]
    if not peers:
        print("ok keine anderen Pi konfiguriert")
        return 0
    ctx = None
    if any(u.startswith("https:") for u in peers):
        try:
            ctx = ssl.create_default_context(cafile=a.cafile) if a.cafile else ssl._create_unverified_context()
        except OSError:
            ctx = ssl._create_unverified_context()      # CA nicht lesbar: lieber fragen als blind starten

    deadline = time.monotonic() + a.wait
    answers: dict[str, dict] = {}
    with ThreadPoolExecutor(len(peers)) as pool:
        while True:
            for base, res in zip(peers, pool.map(lambda u: ask(u, ctx), peers), strict=True):
                if res is not None:
                    answers[base] = res
            knows_leader = any(r.get("leader") for r in answers.values())
            if knows_leader or len(answers) == len(peers) or time.monotonic() >= deadline:
                break
            time.sleep(1)
    verdict, why = decide(a.node, answers, a.einzelbetrieb)
    if not answers:
        why = f"kein anderer Pi hat geantwortet ({len(peers)} gefragt)"
    print(f"{verdict} {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
