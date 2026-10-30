"""Real BigQuery check. Skipped unless you ask for it.

Run it (PowerShell, venv active, after setenv.ps1):
    $env:RUN_BQ_TESTS = "1"
    pytest tests/test_store_bigquery_integration.py -q

It reads the six seed claims you created in BigQuery. It costs a tiny amount
(a few kilobytes scanned) but it is a real call, so it is off by default.

NOTE: the expected values below match the seed data. Once later milestones
change a claim's status on purpose, update the expected value here too.
"""

import os

import pytest
from claims_agent.store import BigQueryClaimStore
from google.cloud import bigquery

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BQ_TESTS") != "1",
    reason="set RUN_BQ_TESTS=1 to run tests against real BigQuery",
)


@pytest.fixture(scope="module")
def store():
    project = os.environ["GCP_PROJECT"]
    return BigQueryClaimStore(bigquery.Client(project=project), project=project)


@pytest.mark.parametrize(
    ("claim_id", "expected"),
    [
        ("TP20260101", "Pending"),
        ("TP20260615", "Approved"),
        ("TP20260610", "Denied"),
        ("TP20250101", None),  # not in the table
    ],
)
def test_seed_claims(store, claim_id, expected):
    assert store.get_status(claim_id) == expected
