#!/usr/bin/env python3
"""T9 – Schreibzugriffe auf die SD-Karte messen. Liest nur, ändert nichts.
Auf dem Pi:  sudo python3 /opt/festpos/tools/sd-messung.py [MINUTEN]      (Standard 60, Zwischenstand alle 10 Minuten; T9)"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 60
RQ = os.environ.get("RQ", "http://127.0.0.1:4001")
DIENSTE = ["rqlited", "festpos", "festpos-dhcp", "systemd-journald", "chrony"]
LEASES = "/var/lib/festpos/dhcp.leases"
KEYS = "key='printer_state' OR key LIKE 'node_info.%' OR key LIKE 'display_status.%'"


def sh(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


DISK = sh("lsblk", "-no", "PKNAME", sh("findmnt", "-no", "SOURCE", "/")) or "mmcblk0"


def sektoren():
    with open("/proc/diskstats") as f:
        for line in f:
            x = line.split()
            if x[2] == DISK:
                return int(x[9])                      # geschriebene Sektoren à 512 Byte
    return 0


def rq(path, **q):
    try:
        with urllib.request.urlopen(f"{RQ}{path}?{urllib.parse.urlencode(q)}", timeout=5) as r:
            return json.load(r)
    except (OSError, ValueError):
        return {}


def sql(q):                                           # level=none: nur lokal lesen, kein Raft-Eintrag
    try:
        return rq("/db/query", level="none", q=q)["results"][0].get("values") or []
    except (KeyError, IndexError):
        return []


def raft_index():
    try:
        return int(rq("/status")["store"]["raft"]["last_log_index"])
    except (KeyError, TypeError, ValueError):
        return 0


def pids(dienst):
    try:
        with open(f"/sys/fs/cgroup/system.slice/{dienst}.service/cgroup.procs") as f:
            return [p.strip() for p in f if p.strip()]
    except OSError:
        return []


def wbytes(pid):
    try:
        with open(f"/proc/{pid}/io") as f:
            return next(int(x.split()[1]) for x in f if x.startswith("write_bytes"))
    except (OSError, StopIteration, ValueError):
        return None


def zahl(q):
    v = sql(q)
    return v[0][0] if v else None


def mtime(p):
    try:
        return os.stat(p).st_mtime
    except OSError:
        return None


def mb(b):
    return f"{b / 1e6:8.2f} MB"


print(f"Karte: /dev/{DISK}  /tmp: {sh('findmnt', '-no', 'FSTYPE', '/tmp') or '?'}  "
      f"Journal: {[x for x in sh('systemd-analyze', 'cat-config', 'systemd/journald.conf').splitlines() if x.startswith('Storage=')][-1:] or '?'}  "
      f"Swap: {sh('swapon', '--show', '--noheadings') or 'aus'}")
start, s0, i0 = time.time(), sektoren(), raft_index()
o0, j0 = zahl("SELECT count(*) FROM orders"), zahl("SELECT count(*) FROM print_jobs")
last = {d: {p: wbytes(p) for p in pids(d)} for d in DIENSTE}
proc = dict.fromkeys(DIENSTE, 0)
werte, wechsel = {}, {}
lease_m, lease_n = mtime(LEASES), 0
naechster = start + 600


def bericht():
    h = (time.time() - start) / 3600 or 1
    total = (sektoren() - s0) * 512
    print(f"\n--- nach {(time.time() - start) / 60:.0f} Minuten, hochgerechnet pro Stunde ---")
    print(f"Karte gesamt          {mb(total / h)}")
    for d in DIENSTE:
        print(f"  {d:20s}{mb(proc[d] / h)}")
    print(f"  {'Rest (ext4 …)':20s}{mb((total - sum(proc.values())) / h)}")
    print(f"Raft-Einträge         {(raft_index() - i0) / h:8.0f} pro Stunde (alle Schreibzugriffe im Cluster)")
    for k in sorted(wechsel):
        print(f"  {k:20s}{wechsel[k] / h:8.0f}")
    o1, j1 = zahl("SELECT count(*) FROM orders"), zahl("SELECT count(*) FROM print_jobs")
    if None not in (o0, o1, j0, j1):
        print(f"  neue Bestellungen {o1 - o0}, neue Druckaufträge {j1 - j0} (insgesamt, nicht pro Stunde)")
    print(f"dnsmasq-Leases        {lease_n / h:8.0f} mal neu geschrieben (gezählt alle 10 s)")
    sys.stdout.flush()


try:
    while time.time() - start < MIN * 60:
        time.sleep(10)
        for d in DIENSTE:
            now = {p: wbytes(p) for p in pids(d)}
            for p, v in now.items():
                if v is not None and last[d].get(p) is not None:
                    proc[d] += max(0, v - last[d][p])
            last[d] = {**last[d], **now}
        for k, v in sql(f"SELECT key, value FROM settings WHERE {KEYS}"):
            if k in werte and werte[k] != v:
                wechsel[k] = wechsel.get(k, 0) + 1
            werte[k] = v
        m = mtime(LEASES)
        if m is not None and lease_m is not None and m != lease_m:
            lease_n += 1
        lease_m = m
        if time.time() >= naechster:
            bericht()
            naechster += 600
except KeyboardInterrupt:
    pass
bericht()
