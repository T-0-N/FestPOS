"""CodeRabbit zu PR 78: Protokolleintrag nur zusammen mit der Änderung (audit_if_changed_stmt) – auf SQLite und rqlite
(SQLite changes() im selben Batch; rqlite führt einen Batch in einer Verbindung aus)."""
from __future__ import annotations

from app import ledger_base as lb


def test_audit_only_when_the_statement_changed_something(store):  # store = SQLite und rqlite (conftest)
    store.init_schema()
    store.execute("INSERT INTO settings(key, value) VALUES ('probe', 'a')")
    n = lambda: store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='probe'")  # noqa: E731
    res = store.batch([("UPDATE settings SET value='b' WHERE key='probe' AND value='x'", ()),
                       lb.audit_if_changed_stmt("test", "probe")], transaction=True)
    assert res[0].rows_affected == 0 and n() == 0                      # nichts geändert: kein Eintrag
    res = store.batch([("UPDATE settings SET value='b' WHERE key='probe' AND value='a'", ()),
                       lb.audit_if_changed_stmt("test", "probe")], transaction=True)
    assert res[0].rows_affected == 1 and n() == 1                      # geändert: genau ein Eintrag
