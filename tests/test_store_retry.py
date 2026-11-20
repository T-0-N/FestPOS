"""T36: Schreibbefehle an rqlite nie doppelt – auch wenn die Antwort verloren geht.

Kleiner nachgebauter rqlite-Server: zählt, wie oft ein Schreibbefehl ausgeführt wurde, und kann die Verbindung nach
dem Ausführen ohne Antwort schliessen (Antwort verloren) oder nach einer Antwort still schliessen (ruhende
Keep-Alive-Verbindung, die rqlite inzwischen zugemacht hat).
"""
from __future__ import annotations

import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.store import RqliteStore, StoreUnavailable, StoreUnclear


class FakeRqlite:
    def __init__(self):
        self.executed = 0            # ausgeführte Schreibbefehle
        self.queries = 0
        self.connections = 0
        self.drop_next_write = False  # ausführen, dann ohne Antwort schliessen
        self.close_after_query = False  # nach der Antwort still schliessen
        fake = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                fake.connections += 1

            def log_message(self, *a):
                pass

            def _answer(self, obj):
                raw = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                stmts = json.loads(body or b"[]")
                if self.path.startswith("/db/execute"):
                    fake.executed += 1
                    if fake.drop_next_write:
                        fake.drop_next_write = False
                        self.close_connection = True
                        return                               # ausgeführt, aber keine Antwort
                    self._answer({"results": [{"rows_affected": 1} for _ in stmts]})
                else:
                    fake.queries += 1
                    self._answer({"results": [{"columns": ["x"], "values": [[1]]}]})
                    if fake.close_after_query:
                        fake.close_after_query = False
                        self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    f = FakeRqlite()
    yield f
    f.stop()


def test_lost_answer_never_runs_write_twice(fake):
    st = RqliteStore("127.0.0.1", fake.port)
    st.execute("UPDATE t SET a=a+1")
    assert fake.executed == 1
    fake.drop_next_write = True
    with pytest.raises(StoreUnclear):
        st.execute("UPDATE gast_orders SET refunded_cents=refunded_cents+500 WHERE id=1")
    assert fake.executed == 2                                # genau einmal mehr, nicht zweimal
    assert isinstance(StoreUnclear("x"), StoreUnavailable)   # Handy: 503, sendet mit demselben Schlüssel nochmals


def test_read_is_retried_after_closed_keepalive(fake):
    st = RqliteStore("127.0.0.1", fake.port)
    fake.close_after_query = True
    assert st.query("SELECT 1") == [{"x": 1}]
    assert st.query("SELECT 1") == [{"x": 1}]               # alte Verbindung zu: einmal neu, Ergebnis stimmt
    assert fake.connections == 2


def test_write_after_idle_uses_new_connection(fake):
    st = RqliteStore("127.0.0.1", fake.port)
    st.execute("UPDATE t SET a=1")
    st.execute("UPDATE t SET a=2")                           # gleich danach: dieselbe Verbindung
    assert fake.connections == 1
    st._local.used -= RqliteStore.WRITE_IDLE_S + 1           # länger geruht
    st.execute("UPDATE t SET a=3")
    assert fake.connections == 2 and fake.executed == 3


def test_refused_connection_is_retried_once(fake, monkeypatch):
    st = RqliteStore("127.0.0.1", fake.port)
    real = http.client.HTTPConnection.connect
    calls = {"n": 0}

    def flaky(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionRefusedError("noch nicht bereit")
        return real(self)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", flaky)
    st.execute("UPDATE t SET a=1")                           # nichts gesendet -> zweiter Versuch ist sicher
    assert fake.executed == 1 and calls["n"] == 2
