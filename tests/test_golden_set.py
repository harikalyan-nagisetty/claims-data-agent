"""Checks that the golden set is well formed AND agrees with the spec rules.

These tests do not call any AI. They protect the golden set itself: if a case
contradicts the rules table, the permissions, or the reversal flow, a test
fails here before the case can mislead an evaluation of the agent.
"""

import os
import re
from datetime import datetime
from pathlib import Path

import pytest
import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "evals" / "golden_set.yaml"
GOLDEN_PATH = Path(os.environ.get("GOLDEN_SET", DEFAULT_PATH))

OUTCOMES = {
    "status_reported", "changed", "not_found", "invalid_id", "invalid_status",
    "asked_for_claim_id", "asked_for_reason", "asked_for_confirmation",
    "asked_one_claim_at_a_time", "cancelled", "refused_transition",
    "refused_permission", "no_change_same_status", "declined_off_topic",
}
STATUSES = {"Pending", "Approved", "Denied"}
ROLES = {"viewer", "editor"}
ALLOWED_CHANGES = {
    ("Pending", "Approved"), ("Pending", "Denied"),
    ("Approved", "Denied"), ("Denied", "Approved"),
}
REVERSALS = {("Approved", "Denied"), ("Denied", "Approved")}
CLAIM_ID = re.compile(r"^TP(\d{8})$")
SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")

CASES = yaml.safe_load(GOLDEN_PATH.read_text(encoding="utf-8"))
BY_ID = [pytest.param(case, id=str(case.get("id"))) for case in CASES]


def is_valid_claim_id(claim_id: str) -> bool:
    match = CLAIM_ID.match(claim_id)
    if not match:
        return False
    try:
        datetime.strptime(match.group(1), "%Y%m%d")
    except ValueError:
        return False
    return True


def changed_claims(case):
    before, after = case["claim_before"], case["expect"]["status_after"]
    return {k for k in set(before) | set(after) if before.get(k) != after.get(k)}


def test_there_are_enough_cases():
    assert len(CASES) >= 30


def test_ids_are_unique_and_snake_case():
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    assert all(SNAKE_CASE.match(i) for i in ids)


def test_every_outcome_is_covered_at_least_once():
    used = {turn["outcome"] for case in CASES for turn in case["turns"]}
    assert used == OUTCOMES, f"missing coverage for: {sorted(OUTCOMES - used)}"


@pytest.mark.parametrize("case", BY_ID)
def test_case_is_well_formed(case):
    assert set(case) == {"id", "role", "claim_before", "turns", "expect"}
    assert case["role"] in ROLES
    for claim_id, status in case["claim_before"].items():
        assert is_valid_claim_id(claim_id), f"bad claim id in claim_before: {claim_id}"
        assert status in STATUSES
    assert case["turns"], "a case needs at least one turn"
    for turn in case["turns"]:
        assert isinstance(turn["say"], str) and turn["say"].strip()
        assert turn["outcome"] in OUTCOMES
    expect = case["expect"]
    assert isinstance(expect["write"], bool)
    assert isinstance(expect["audit_record"], bool)
    for claim_id, status in expect["status_after"].items():
        assert is_valid_claim_id(claim_id)
        assert status in STATUSES


@pytest.mark.parametrize("case", BY_ID)
def test_write_flag_matches_the_state_change(case):
    expect = case["expect"]
    if expect["write"]:
        assert len(changed_claims(case)) == 1, "a write must change exactly one claim"
        assert expect["audit_record"] is True, "every write needs an audit record"
    else:
        assert changed_claims(case) == set(), "no write, but the table changed"
        assert expect["audit_record"] is False


@pytest.mark.parametrize("case", BY_ID)
def test_changed_outcome_if_and_only_if_write(case):
    outcomes = [turn["outcome"] for turn in case["turns"]]
    assert ("changed" in outcomes) == case["expect"]["write"]
    if "changed" in outcomes:
        assert outcomes[-1] == "changed", "a change should be the last thing that happens"


@pytest.mark.parametrize("case", BY_ID)
def test_writes_follow_the_rules_table(case):
    if not case["expect"]["write"]:
        return
    (claim_id,) = changed_claims(case)
    before = case["claim_before"][claim_id]
    after = case["expect"]["status_after"][claim_id]
    assert (before, after) in ALLOWED_CHANGES, f"{before} -> {after} is never allowed"
    assert case["role"] == "editor", "only editors may change a status"
    if (before, after) in REVERSALS:
        outcomes = [turn["outcome"] for turn in case["turns"]]
        assert "asked_for_confirmation" in outcomes, "a reversal needs confirmation first"
        assert outcomes.index("asked_for_confirmation") < outcomes.index("changed")


@pytest.mark.parametrize("case", BY_ID)
def test_viewers_never_get_a_change(case):
    if case["role"] == "viewer":
        outcomes = {turn["outcome"] for turn in case["turns"]}
        assert not outcomes & {"changed", "asked_for_reason", "asked_for_confirmation"}
