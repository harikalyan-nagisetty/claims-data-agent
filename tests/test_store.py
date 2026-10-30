"""Tests for the claim store (src/claims_agent/store.py).

Part 1 is a "contract": rules that EVERY store must follow. The same tests
run against the in-memory fake and against BigQueryClaimStore (talking to a
fake BigQuery client), so the two cannot drift apart.

Part 2 checks BigQueryClaimStore's safety rules: no user text inside SQL,
read-only queries, and no query at all for a bad claim ID.

No test here touches the internet or costs money.
"""

import re

import pytest
from claims_agent.store import BigQueryClaimStore, DuplicateClaimError, UnexpectedStatusError
from fakes import FakeBigQueryClient, InMemoryClaimStore

BAD_IDS = [
    "tp20260621",                              # must already be normalized
    " TP20260621",                             # spaces
    "TP12",                                    # too short
    "TP20261345",                              # month 13
    "",                                        # empty
    "x' OR 1=1 --",                            # SQL injection attempt
    "TP20260621' OR '1'='1",                   # injection hiding behind a valid start
    "TP20260621; DROP TABLE claim_status",     # a second statement
]


@pytest.fixture(params=["in_memory", "bigquery"])
def build(request):
    def _build(rows=()):
        if request.param == "in_memory":
            return InMemoryClaimStore(rows)
        client = FakeBigQueryClient(rows)
        return BigQueryClaimStore(client, project="test-project", dataset="claims_agent")

    return _build


# ----------------------------------------------- Part 1: the contract
def test_returns_the_status_of_an_existing_claim(build):
    store = build([("TP20260621", "Pending")])
    assert store.get_status("TP20260621") == "Pending"


def test_returns_none_for_a_claim_that_is_not_there(build):
    store = build([("TP20260101", "Approved")])
    assert store.get_status("TP20260621") is None


def test_returns_none_when_the_table_is_empty(build):
    assert build([]).get_status("TP20260621") is None


def test_returns_the_right_claim_when_there_are_several(build):
    store = build([("TP20260101", "Approved"), ("TP20260621", "Denied"), ("TP20260315", "Pending")])
    assert store.get_status("TP20260621") == "Denied"
    assert store.get_status("TP20260101") == "Approved"


def test_two_rows_for_one_claim_is_an_error_not_a_guess(build):
    store = build([("TP20260621", "Pending"), ("TP20260621", "Approved")])
    with pytest.raises(DuplicateClaimError):
        store.get_status("TP20260621")


def test_a_status_outside_the_three_known_ones_is_an_error(build):
    store = build([("TP20260621", "Maybe")])
    with pytest.raises(UnexpectedStatusError):
        store.get_status("TP20260621")


def test_status_must_match_exactly_not_just_loosely(build):
    # The table should contain "Approved". "approved" means the data is wrong.
    store = build([("TP20260621", "approved")])
    with pytest.raises(UnexpectedStatusError):
        store.get_status("TP20260621")


@pytest.mark.parametrize("bad_id", BAD_IDS)
def test_ids_that_are_not_in_canonical_form_are_rejected(build, bad_id):
    store = build([("TP20260621", "Pending")])
    with pytest.raises(ValueError):
        store.get_status(bad_id)


# ----------------------------------- Part 2: BigQueryClaimStore safety
def make_bq(rows=()):
    client = FakeBigQueryClient(rows)
    return BigQueryClaimStore(client, project="test-project", dataset="claims_agent"), client


def test_claim_id_travels_as_a_query_parameter_never_inside_the_sql():
    store, client = make_bq([("TP20260621", "Pending")])
    store.get_status("TP20260621")

    assert len(client.calls) == 1
    sql, config = client.calls[0]
    assert "@claim_id" in sql
    assert "TP20260621" not in sql
    assert {p.name: p.value for p in config.query_parameters} == {"claim_id": "TP20260621"}


def test_the_query_only_reads_the_claim_status_table():
    store, client = make_bq([("TP20260621", "Pending")])
    store.get_status("TP20260621")

    sql, _ = client.calls[0]
    assert sql.lstrip().upper().startswith("SELECT")
    assert "test-project.claims_agent.claim_status" in sql
    assert re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE)\b", sql.upper()) is None


@pytest.mark.parametrize("bad_id", BAD_IDS)
def test_a_bad_id_never_reaches_bigquery(bad_id):
    store, client = make_bq([("TP20260621", "Pending")])
    with pytest.raises(ValueError):
        store.get_status(bad_id)
    assert client.calls == []


@pytest.mark.parametrize(
    ("project", "dataset"),
    [
        ("test-project", "claims_agent; DROP TABLE x"),
        ("test-project", "claims.agent"),
        ("test-project", ""),
        ("test`project", "claims_agent"),
        ("test project", "claims_agent"),
        ("", "claims_agent"),
    ],
)
def test_unsafe_project_or_dataset_names_are_rejected(project, dataset):
    with pytest.raises(ValueError):
        BigQueryClaimStore(FakeBigQueryClient(), project=project, dataset=dataset)
