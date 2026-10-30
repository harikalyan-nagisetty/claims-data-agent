"""Test doubles used by the tests. No network, no cloud, no cost.

InMemoryClaimStore
    A stand-in for the real store. Later we will give it to the agent in tests
    so the agent can be exercised without BigQuery.

FakeBigQueryClient
    Looks just enough like the real BigQuery client for BigQueryClaimStore to
    use it. It records every query it receives, and it answers by reading the
    *query parameter* named claim_id. If your store builds the SQL by pasting
    text instead of using a parameter, this fake finds no rows and the tests fail.
"""

from claims_agent.rules import normalize_claim_id, parse_status
from claims_agent.store import DuplicateClaimError, UnexpectedStatusError


class InMemoryClaimStore:
    def __init__(self, rows=()):
        # rows: list of (claim_id, status). Duplicates are allowed on purpose.
        self._rows = list(rows)

    def get_status(self, claim_id):
        if normalize_claim_id(claim_id) != claim_id:
            raise ValueError(f"claim id must be in canonical form: {claim_id!r}")
        statuses = [status for cid, status in self._rows if cid == claim_id]
        if not statuses:
            return None
        if len(statuses) > 1:
            raise DuplicateClaimError(claim_id)
        if parse_status(statuses[0]) != statuses[0]:
            raise UnexpectedStatusError(statuses[0])
        return statuses[0]


class FakeJob:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return iter(self._rows)


class FakeBigQueryClient:
    def __init__(self, rows=()):
        self.rows = list(rows)       # list of (claim_id, status)
        self.calls = []              # list of (sql, job_config)

    def query(self, query, job_config=None, **kwargs):
        self.calls.append((query, job_config))
        parameters = {}
        if job_config is not None:
            parameters = {p.name: p.value for p in job_config.query_parameters}
        wanted = parameters.get("claim_id")
        matching = [{"status": status} for cid, status in self.rows if cid == wanted]
        return FakeJob(matching)
