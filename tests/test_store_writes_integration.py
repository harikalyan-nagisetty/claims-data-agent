"""Real BigQuery check of the write side. Skipped unless you ask for it.

Run it (PowerShell, venv active, after setenv.ps1):
    $env:RUN_BQ_TESTS = "1"
    pytest tests/test_store_writes_integration.py -q

It creates one throwaway claim (TP20260707), changes it through the store,
reads the audit trail back, and then deletes everything it created. It costs
a tiny amount, but it is a real call, so it is off by default.
"""

import os

import pytest
from claims_agent.store import BigQueryClaimStore
from google.cloud import bigquery

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BQ_TESTS") != "1",
    reason="set RUN_BQ_TESTS=1 to run tests against real BigQuery",
)

CLAIM = "TP20260707"


@pytest.fixture()
def world():
    project = os.environ["GCP_PROJECT"]
    client = bigquery.Client(project=project)
    status_table = f"`{project}.claims_agent.claim_status`"
    audit_table = f"`{project}.claims_agent.claim_audit`"

    def run(sql):
        config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("claim_id", "STRING", CLAIM)]
        )
        return list(client.query(sql, job_config=config).result())

    def clean_up():
        run(f"DELETE FROM {status_table} WHERE claim_id = @claim_id")
        run(f"DELETE FROM {audit_table} WHERE claim_id = @claim_id")

    clean_up()
    run(
        f"INSERT INTO {status_table} (claim_id, status, updated_at, updated_by) "
        "VALUES (@claim_id, 'Pending', CURRENT_TIMESTAMP(), 'integration-test')"
    )
    store = BigQueryClaimStore(client, project=project)

    def audit():
        rows = run(
            f"SELECT old_status, new_status, reason, changed_by FROM {audit_table} "
            "WHERE claim_id = @claim_id ORDER BY changed_at"
        )
        return [(r["old_status"], r["new_status"], r["reason"], r["changed_by"]) for r in rows]

    yield store, audit
    clean_up()


def apply(store, requested, *, reason=None, confirmed=False, role="editor"):
    return store.apply_change(
        role=role, claim_id=CLAIM, requested_status=requested, reason=reason,
        confirmed=confirmed, changed_by="it-editor",
    )


def test_full_lifecycle_against_real_bigquery(world):
    store, audit = world

    assert apply(store, "Approved") == "changed"
    assert store.get_status(CLAIM) == "Approved"
    assert audit() == [("Pending", "Approved", None, "it-editor")]

    assert apply(store, "Approved") == "no_change_same_status"
    assert len(audit()) == 1

    assert apply(store, "Denied") == "asked_for_reason"
    assert apply(store, "Denied", reason="documents incomplete") == "asked_for_confirmation"
    assert store.get_status(CLAIM) == "Approved"

    assert apply(store, "Denied", reason="documents incomplete", confirmed=True) == "changed"
    assert store.get_status(CLAIM) == "Denied"
    assert audit()[-1] == ("Approved", "Denied", "documents incomplete", "it-editor")
    assert len(audit()) == 2

    assert apply(store, "Pending", confirmed=True) == "refused_transition"
    assert apply(store, "Approved", role="viewer") == "refused_permission"
    assert len(audit()) == 2
