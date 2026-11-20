"""Die Kernpfade der API (Bestellung, Storno, Zahlart, Tagesabschluss, Quittung, Nachdruck, Sicherung) auch gegen ein
ECHTES rqlite – nicht nur gegen SQLite. rqlite liefert Ergebnisse und Fehler anders (JSON über HTTP, Fehler pro
Anweisung im Batch); genau dort fiele ein Unterschied erst am Pi auf.

Die Tests selbst stehen in den anderen Dateien; hier wird nur die Fixture «client» durch eine mit rqlite ersetzt.
Läuft nicht mit «tools/check.sh --schnell» (Name enthält «rqlite»)."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import RQLITED, load_app, start_cluster
from tests.test_api import test_csv_import_preview_update_and_errors, test_order_flow_and_jobs  # noqa: F401
from tests.test_kassenbuch_review import (test_closing_chain_is_keyed_and_old_ones_still_verify,  # noqa: F401
                                          test_concurrent_receipt_only_once_original,
                                          test_concurrent_storno_never_exceeds_order)
from tests.test_ledger import (shop, test_closing_cash_check_vat_and_integrity,  # noqa: F401
                               test_payment_at_order_phone_and_admin, test_reset_only_after_closing_and_closings_stay,
                               test_storno_partial_full_and_bons)
from tests.test_receipts import (test_numbering_copy_and_content, test_part_receipts_per_person_and_storno,  # noqa: F401
                                 test_reprint_in_jobs_is_original_until_printed_then_copy, test_vat_receipt_and_recipient)
from tests.test_robust import test_duplicate_names_have_readable_messages  # noqa: F401
from tests.test_sicherung import (test_receipt_numbers_jump_on_new_pi,  # noqa: F401
                                  test_revocations_and_new_certs_survive_older_backup,
                                  test_same_system_keeps_running_secrets_and_password)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Wie conftest.client, aber die App spricht mit einem echten rqlite (ein Knoten)."""
    if not Path(RQLITED).exists():
        pytest.skip("rqlited nicht gefunden")
    from fastapi.testclient import TestClient
    nodes = start_cluster(tmp_path, 1)
    monkeypatch.setenv("FESTPOS_DB", f"rqlite://127.0.0.1:{nodes[0].http_port}")
    monkeypatch.setenv("FESTPOS_ADMIN_PASSWORD", "geheim")
    monkeypatch.setenv("FESTPOS_STATIC", str(tmp_path / "nostatic"))
    main = load_app()
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        nodes[0].stop()
