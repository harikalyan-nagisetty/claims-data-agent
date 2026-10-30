"""Test doubles used by the tests. No network, no cloud, no cost.

InMemoryClaimStore
    A stand-in for the real store. It follows the same rules, so tests can run
    the same checks against it and against BigQueryClaimStore. Later the agent
    will be tested against it too.

FakeBigQueryClient
    Looks just enough like the real BigQuery client for BigQueryClaimStore to
    use it, and it behaves like a tiny database:

      * A query WITHOUT "BEGIN TRANSACTION" is treated as a read. It answers by
        reading the query parameter named claim_id.
      * A query WITH "BEGIN TRANSACTION" is treated as the write script. It
        needs exactly these parameters: claim_id, expected_status, new_status,
        changed_by, reason, audit_id. It changes the row only if the claim still
        has expected_status (compare-and-set), writes one audit record, and
        answers with one row: {"rows_updated": 1} or {"rows_updated": 0}.

    Both doubles keep their data in two plain attributes you can inspect:
        .rows   list of (claim_id, status)
        .audit  list of dicts (audit_id, claim_id, old_status, new_status,
                reason, changed_by)
    and both have a before_write hook (a function called just before the write
    happens) so a test can simulate someone else changing the claim.
"""

import uuid

from claims_agent.rules import decide_change, normalize_claim_id, parse_status
from claims_agent.store import (
    ConcurrentChangeError,
    DuplicateClaimError,
    UnexpectedStatusError,
)

ROLES = ("viewer", "editor")
STATUSES = ("Pending", "Approved", "Denied")
REQUIRED_WRITE_PARAMETERS = {
    "claim_id", "expected_status", "new_status", "changed_by", "reason", "audit_id",
}


def _validate_change_inputs(role, claim_id, requested_status, changed_by):
    if normalize_claim_id(claim_id) != claim_id:
        raise ValueError(f"claim id must be in canonical form: {claim_id!r}")
    if requested_status not in STATUSES:
        raise ValueError(f"unknown status: {requested_status!r}")
    if role not in ROLES:
        raise ValueError(f"unknown role: {role!r}")
    if not isinstance(changed_by, str) or not changed_by.strip():
        raise ValueError("changed_by must be a non-empty string")


def _clean_reason(reason):
    return reason.strip() if reason and reason.strip() else None


class InMemoryClaimStore:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.audit = []
        self.before_write = None

    def get_status(self, claim_id):
        if normalize_claim_id(claim_id) != claim_id:
            raise ValueError(f"claim id must be in canonical form: {claim_id!r}")
        statuses = [status for cid, status in self.rows if cid == claim_id]
        if not statuses:
            return None
        if len(statuses) > 1:
            raise DuplicateClaimError(claim_id)
        if parse_status(statuses[0]) != statuses[0]:
            raise UnexpectedStatusError(statuses[0])
        return statuses[0]

    def apply_change(self, role, claim_id, requested_status, reason, confirmed, changed_by):
        _validate_change_inputs(role, claim_id, requested_status, changed_by)
        if role != "editor":
            return "refused_permission"

        current = self.get_status(claim_id)
        if current is None:
            return "not_found"

        decision = decide_change(role, current, requested_status, reason, confirmed)
        if decision != "allowed":
            return decision

        if self.before_write is not None:
            self.before_write()
        matches = [i for i, (cid, s) in enumerate(self.rows) if cid == claim_id and s == current]
        if len(matches) != 1:
            raise ConcurrentChangeError(claim_id)

        self.rows[matches[0]] = (claim_id, requested_status)
        self.audit.append(
            {
                "audit_id": str(uuid.uuid4()),
                "claim_id": claim_id,
                "old_status": current,
                "new_status": requested_status,
                "reason": _clean_reason(reason),
                "changed_by": changed_by,
            }
        )
        return "changed"


class FakeJob:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return iter(self._rows)


class FakeBigQueryClient:
    def __init__(self, rows=()):
        self.rows = list(rows)       # list of (claim_id, status)
        self.audit = []              # list of audit dicts
        self.calls = []              # list of (sql, job_config)
        self.before_write = None

    def query(self, query, job_config=None, **kwargs):
        self.calls.append((query, job_config))
        parameters = {}
        if job_config is not None:
            parameters = {p.name: p.value for p in job_config.query_parameters}

        if "BEGIN TRANSACTION" in query.upper():
            return FakeJob([{"rows_updated": self._run_write_script(parameters)}])

        wanted = parameters.get("claim_id")
        matching = [{"status": status} for cid, status in self.rows if cid == wanted]
        return FakeJob(matching)

    def _run_write_script(self, p):
        assert set(p) == REQUIRED_WRITE_PARAMETERS, (
            f"the write script must use exactly these parameters "
            f"{sorted(REQUIRED_WRITE_PARAMETERS)}, but got {sorted(p)}"
        )
        if self.before_write is not None:
            self.before_write()
        matches = [
            i for i, (cid, s) in enumerate(self.rows)
            if cid == p["claim_id"] and s == p["expected_status"]
        ]
        if len(matches) != 1:
            return 0
        self.rows[matches[0]] = (p["claim_id"], p["new_status"])
        self.audit.append(
            {
                "audit_id": p["audit_id"],
                "claim_id": p["claim_id"],
                "old_status": p["expected_status"],
                "new_status": p["new_status"],
                "reason": p["reason"],
                "changed_by": p["changed_by"],
            }
        )
        return 1
