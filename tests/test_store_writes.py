"""Tests for the write side of the claim store: store.apply_change(...).

apply_change(role, claim_id, requested_status, reason, confirmed, changed_by)
returns one of these words:
    changed, not_found, refused_permission, refused_transition,
    no_change_same_status, asked_for_reason, asked_for_confirmation
and raises ValueError for bad input, ConcurrentChangeError if someone else
changed the claim in the meantime.

Part 1 is a contract that runs against the in-memory store AND
BigQueryClaimStore (with a fake BigQuery client that behaves like a tiny
database). Part 2 checks the safety of the BigQuery script itself.

Nothing here touches the internet or costs money.
"""

import re

import pytest
from claims_agent.store import BigQueryClaimStore, ConcurrentChangeError, DuplicateClaimError
from fakes import FakeBigQueryClient, InMemoryClaimStore

CLAIM = "TP20260621"
OTHER = "TP20260101"


@pytest.fixture(params=["in_memory", "bigquery"])
def build(request):
    """Returns build(rows) -> (store, world). world has .rows, .audit, .before_write."""

    def _build(rows):
        if request.param == "in_memory":
            store = InMemoryClaimStore(rows)
            return store, store
        client = FakeBigQueryClient(rows)
        store = BigQueryClaimStore(client, project="test-project", dataset="claims_agent")
        return store, client

    return _build


def change(store, claim_id=CLAIM, requested="Approved", *, role="editor", reason=None,
           confirmed=False, changed_by="editor-7"):
    return store.apply_change(
        role=role,
        claim_id=claim_id,
        requested_status=requested,
        reason=reason,
        confirmed=confirmed,
        changed_by=changed_by,
    )


# ------------------------------------------------ Part 1: the contract
@pytest.mark.parametrize(
    ("current", "requested", "reason", "confirmed"),
    [
        ("Pending", "Approved", None, False),
        ("Pending", "Denied", None, False),
        ("Approved", "Denied", "documents were incomplete", True),   # reversal
        ("Denied", "Approved", "new documents verified", True),      # reversal
    ],
)
def test_allowed_changes_update_the_status_and_write_one_audit_record(
    build, current, requested, reason, confirmed
):
    store, world = build([(CLAIM, current), (OTHER, "Pending")])

    outcome = change(store, requested=requested, reason=reason, confirmed=confirmed,
                     changed_by="editor-7")

    assert outcome == "changed"
    assert (CLAIM, requested) in world.rows
    assert (OTHER, "Pending") in world.rows, "another claim must not be touched"
    assert len(world.rows) == 2
    assert len(world.audit) == 1
    record = world.audit[0]
    assert record["claim_id"] == CLAIM
    assert record["old_status"] == current
    assert record["new_status"] == requested
    assert record["changed_by"] == "editor-7"
    assert record["reason"] == reason
    assert record["audit_id"]


def test_the_reason_is_stored_without_surrounding_spaces(build):
    store, world = build([(CLAIM, "Approved")])
    change(store, requested="Denied", reason="  documents incomplete  ", confirmed=True)
    assert world.audit[0]["reason"] == "documents incomplete"


def test_a_blank_reason_is_stored_as_nothing_on_a_normal_change(build):
    store, world = build([(CLAIM, "Pending")])
    change(store, requested="Approved", reason="   ")
    assert world.audit[0]["reason"] is None


NO_WRITE_CASES = [
    ("editor", "Approved", "Denied", None, False, "asked_for_reason"),
    ("editor", "Approved", "Denied", "   ", True, "asked_for_reason"),
    ("editor", "Denied", "Approved", "new docs", False, "asked_for_confirmation"),
    ("editor", "Approved", "Pending", None, False, "refused_transition"),
    ("editor", "Denied", "Pending", "any reason", True, "refused_transition"),
    ("editor", "Approved", "Approved", None, False, "no_change_same_status"),
    ("editor", "Pending", "Pending", None, False, "no_change_same_status"),
    ("viewer", "Pending", "Approved", None, False, "refused_permission"),
    ("viewer", "Approved", "Denied", "reason", True, "refused_permission"),
]


@pytest.mark.parametrize(("role", "current", "requested", "reason", "confirmed", "expected"),
                         NO_WRITE_CASES)
def test_when_the_rules_say_no_nothing_changes(build, role, current, requested, reason,
                                               confirmed, expected):
    store, world = build([(CLAIM, current)])

    outcome = change(store, requested=requested, role=role, reason=reason, confirmed=confirmed)

    assert outcome == expected
    assert world.rows == [(CLAIM, current)]
    assert world.audit == []


def test_a_claim_that_does_not_exist_is_reported_not_created(build):
    store, world = build([(OTHER, "Pending")])
    assert change(store, claim_id=CLAIM, requested="Approved") == "not_found"
    assert world.rows == [(OTHER, "Pending")]
    assert world.audit == []


def test_a_viewer_is_refused_even_for_a_claim_that_does_not_exist(build):
    store, world = build([(OTHER, "Pending")])
    assert change(store, claim_id=CLAIM, role="viewer") == "refused_permission"
    assert world.audit == []


def test_asking_for_the_same_change_twice_changes_it_once(build):
    store, world = build([(CLAIM, "Pending")])
    assert change(store, requested="Approved") == "changed"
    assert change(store, requested="Approved") == "no_change_same_status"
    assert len(world.audit) == 1


def test_a_change_made_by_someone_else_in_the_meantime_is_detected(build):
    store, world = build([(CLAIM, "Pending")])
    world.before_write = lambda: world.rows.__setitem__(0, (CLAIM, "Denied"))

    with pytest.raises(ConcurrentChangeError):
        change(store, requested="Approved")

    assert world.rows == [(CLAIM, "Denied")], "the other person's change must survive"
    assert world.audit == []


def test_duplicate_rows_stop_the_change(build):
    store, world = build([(CLAIM, "Pending"), (CLAIM, "Approved")])
    with pytest.raises(DuplicateClaimError):
        change(store, requested="Denied")
    assert world.audit == []


BAD_INPUTS = [
    {"claim_id": "tp20260621"},
    {"claim_id": "TP12"},
    {"claim_id": "x' OR 1=1 --"},
    {"requested": "Maybe"},
    {"requested": "approved"},
    {"requested": ""},
    {"role": "admin"},
    {"role": ""},
    {"role": "EDITOR "},
    {"changed_by": ""},
    {"changed_by": "   "},
    {"changed_by": None},
]


@pytest.mark.parametrize("bad", BAD_INPUTS)
def test_bad_input_is_an_error_and_changes_nothing(build, bad):
    store, world = build([(CLAIM, "Pending")])
    with pytest.raises(ValueError):
        change(store, **bad)
    assert world.rows == [(CLAIM, "Pending")]
    assert world.audit == []


# ------------------------------- Part 2: safety of the BigQuery script
def make_bq(rows=()):
    client = FakeBigQueryClient(rows)
    return BigQueryClaimStore(client, project="test-project", dataset="claims_agent"), client


def write_calls(client):
    return [(sql, cfg) for sql, cfg in client.calls if "BEGIN TRANSACTION" in sql.upper()]


def position(pattern, sql):
    match = re.search(pattern, sql, re.IGNORECASE)
    assert match, f"{pattern!r} not found in the write script"
    return match.start()


def test_a_change_is_one_script_in_the_right_order():
    store, client = make_bq([(CLAIM, "Pending")])
    change(store, requested="Approved")

    calls = write_calls(client)
    assert len(calls) == 1, "status update and audit record must travel in ONE script"
    sql, _ = calls[0]
    assert (
        position(r"\bBEGIN\s+TRANSACTION\b", sql)
        < position(r"\bUPDATE\b", sql)
        < position(r"\bINSERT\s+INTO\b", sql)
        < position(r"\bCOMMIT\s+TRANSACTION\b", sql)
    )
    assert "test-project.claims_agent.claim_status" in sql
    assert "test-project.claims_agent.claim_audit" in sql


def test_the_update_only_applies_if_the_status_is_still_what_we_read():
    store, client = make_bq([(CLAIM, "Pending")])
    change(store, requested="Approved")
    sql, _ = write_calls(client)[0]
    assert re.search(r"\bstatus\s*=\s*@expected_status\b", sql, re.IGNORECASE)


def test_every_value_is_a_parameter_never_pasted_into_the_sql():
    nasty_reason = "x'; DROP TABLE claim_status; --"
    nasty_user = "ed'; DELETE FROM claim_audit; --"
    store, client = make_bq([(CLAIM, "Approved")])

    outcome = change(store, requested="Denied", reason=nasty_reason, confirmed=True,
                     changed_by=nasty_user)

    assert outcome == "changed"
    sql, config = write_calls(client)[0]
    for text in (nasty_reason, nasty_user, CLAIM, "Approved", "Denied"):
        assert text not in sql, f"{text!r} was pasted into the SQL"
    names = {p.name for p in config.query_parameters}
    assert names == {"claim_id", "expected_status", "new_status", "changed_by",
                     "reason", "audit_id"}
    assert client.audit[0]["reason"] == nasty_reason
    assert client.audit[0]["changed_by"] == nasty_user


def test_the_script_touches_only_the_two_tables_and_nothing_dangerous():
    store, client = make_bq([(CLAIM, "Pending")])
    change(store, requested="Approved")
    sql, _ = write_calls(client)[0]

    tables = set(re.findall(r"`([^`]+)`", sql))
    assert tables == {"test-project.claims_agent.claim_status",
                      "test-project.claims_agent.claim_audit"}
    assert re.search(r"\b(DROP|DELETE|ALTER|CREATE|TRUNCATE|MERGE)\b", sql, re.IGNORECASE) is None


@pytest.mark.parametrize(("role", "current", "requested", "reason", "confirmed", "expected"),
                         NO_WRITE_CASES)
def test_no_write_script_is_sent_when_the_rules_say_no(role, current, requested, reason,
                                                       confirmed, expected):
    store, client = make_bq([(CLAIM, current)])
    assert change(store, requested=requested, role=role, reason=reason,
                  confirmed=confirmed) == expected
    assert write_calls(client) == []


@pytest.mark.parametrize("claim_id", [CLAIM, "TP20250101"])
def test_a_viewer_causes_no_bigquery_call_at_all(claim_id):
    store, client = make_bq([(CLAIM, "Pending")])
    assert change(store, claim_id=claim_id, role="viewer") == "refused_permission"
    assert client.calls == []


@pytest.mark.parametrize("bad", BAD_INPUTS)
def test_bad_input_causes_no_bigquery_call_at_all(bad):
    store, client = make_bq([(CLAIM, "Pending")])
    with pytest.raises(ValueError):
        change(store, **bad)
    assert client.calls == []


def test_each_audit_record_gets_its_own_id():
    store, client = make_bq([(CLAIM, "Pending"), (OTHER, "Pending")])
    change(store, claim_id=CLAIM, requested="Approved")
    change(store, claim_id=OTHER, requested="Denied")
    ids = [record["audit_id"] for record in client.audit]
    assert len(ids) == 2 and len(set(ids)) == 2 and all(ids)
