"""Tests for the rules engine (src/claims_agent/rules.py).

You write rules.py. These tests tell you when it is right.
Run them with:  pytest tests/test_rules.py -q

The rules engine is plain Python. No AI, no database, no network. It only
answers one question: "given who is asking and what the claim looks like,
what should happen?"
"""

import itertools

import pytest
from claims_agent.rules import decide_change, normalize_claim_id, parse_status

STATUSES = ["Pending", "Approved", "Denied"]
ROLES = ["viewer", "editor"]
LEGAL_CHANGES = {
    ("Pending", "Approved"), ("Pending", "Denied"),
    ("Approved", "Denied"), ("Denied", "Approved"),
}
REVERSALS = {("Approved", "Denied"), ("Denied", "Approved")}


# ------------------------------------------------------------- claim IDs
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("TP20260621", "TP20260621"),
        ("tp20260621", "TP20260621"),          # lowercase is accepted
        ("  TP20260621  ", "TP20260621"),      # spaces are trimmed
        ("Tp20260621", "TP20260621"),
        ("TP20240229", "TP20240229"),          # 2024 is a leap year
    ],
)
def test_valid_claim_ids_are_normalized(raw, expected):
    assert normalize_claim_id(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "TP12",              # too short
        "20260621",          # no prefix
        "AB20260621",        # wrong prefix
        "TP20261345",        # month 13
        "TP20260230",        # Feb 30
        "TP20250229",        # 2025 is not a leap year
        "TP202602182345",    # the old, longer format
        "TP2026062",         # 7 digits
        "TP2026062a",        # a letter where a digit should be
        "TP 20260621",       # space inside the ID
        "",
        "   ",
    ],
)
def test_invalid_claim_ids_return_none(raw):
    assert normalize_claim_id(raw) is None


# ---------------------------------------------------------------- statuses
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Pending", "Pending"),
        ("approved", "Approved"),
        ("  DENIED ", "Denied"),
    ],
)
def test_parse_status_accepts_the_three_statuses(raw, expected):
    assert parse_status(raw) == expected


@pytest.mark.parametrize("raw", ["Maybe", "", "approve", "Cancelled", "   "])
def test_parse_status_rejects_anything_else(raw):
    assert parse_status(raw) is None


# ------------------------------------------------ decisions, case by case
# (role, current, requested, reason, confirmed) -> expected decision
CASES = [
    # viewers can never change anything
    ("viewer", "Pending", "Approved", None, False, "refused_permission"),
    ("viewer", "Approved", "Denied", "documents incomplete", True, "refused_permission"),
    ("viewer", "Approved", "Approved", None, False, "refused_permission"),
    # normal changes: no reason, no confirmation needed
    ("editor", "Pending", "Approved", None, False, "allowed"),
    ("editor", "Pending", "Denied", None, False, "allowed"),
    # reversals: reason first, then confirmation
    ("editor", "Approved", "Denied", None, False, "asked_for_reason"),
    ("editor", "Denied", "Approved", None, False, "asked_for_reason"),
    ("editor", "Approved", "Denied", "   ", False, "asked_for_reason"),
    ("editor", "Approved", "Denied", None, True, "asked_for_reason"),
    ("editor", "Approved", "Denied", "documents incomplete", False, "asked_for_confirmation"),
    ("editor", "Denied", "Approved", "documents verified", False, "asked_for_confirmation"),
    ("editor", "Approved", "Denied", "documents incomplete", True, "allowed"),
    ("editor", "Denied", "Approved", "documents verified", True, "allowed"),
    # never move to Pending
    ("editor", "Approved", "Pending", None, False, "refused_transition"),
    ("editor", "Denied", "Pending", "any reason", True, "refused_transition"),
    # same status: not an error, just nothing to do
    ("editor", "Approved", "Approved", None, False, "no_change_same_status"),
    ("editor", "Denied", "Denied", None, False, "no_change_same_status"),
    ("editor", "Pending", "Pending", None, False, "no_change_same_status"),
]


@pytest.mark.parametrize(
    ("role", "current", "requested", "reason", "confirmed", "expected"), CASES
)
def test_decision(role, current, requested, reason, confirmed, expected):
    assert decide_change(role, current, requested, reason, confirmed) == expected


# ---------------------------- properties that must hold for EVERY input
ALL_INPUTS = list(
    itertools.product(
        ROLES, STATUSES, STATUSES, [None, "", "   ", "a real reason"], [False, True]
    )
)


@pytest.mark.parametrize(("role", "current", "requested", "reason", "confirmed"), ALL_INPUTS)
def test_safety_properties(role, current, requested, reason, confirmed):
    decision = decide_change(role, current, requested, reason, confirmed)
    has_reason = bool(reason and reason.strip())

    if role == "viewer":
        assert decision == "refused_permission"
    if decision == "allowed":
        assert role == "editor"
        assert (current, requested) in LEGAL_CHANGES
        assert requested != "Pending"
        if (current, requested) in REVERSALS:
            assert has_reason and confirmed
    if requested == "Pending" and current != "Pending" and role == "editor":
        assert decision == "refused_transition"


# ------------------------------------------------------- fail closed
@pytest.mark.parametrize("role", ["admin", "", "EDITOR ", None])
def test_unknown_role_is_an_error_not_a_permission(role):
    with pytest.raises(ValueError):
        decide_change(role, "Pending", "Approved", None, False)


@pytest.mark.parametrize(("current", "requested"), [("Maybe", "Approved"), ("Pending", "Maybe")])
def test_unknown_status_is_an_error(current, requested):
    with pytest.raises(ValueError):
        decide_change("editor", current, requested, None, False)
