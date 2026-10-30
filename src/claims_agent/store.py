import re

from claims_agent.rules import normalize_claim_id, parse_status
from google.cloud import bigquery

# Names that go into the table path may only contain these characters.
_SAFE_NAME = re.compile(r"[A-Za-z0-9_-]+")


class DuplicateClaimError(Exception):
    """The table has more than one row for the same claim."""


class UnexpectedStatusError(Exception):
    """The table holds a status that is not Pending, Approved or Denied."""


class BigQueryClaimStore:
    """The only place that reads claim status from BigQuery."""

    def __init__(self, client, project, dataset="claims_agent"):
        # Project and dataset names become part of the SQL text, so they
        # must be plain names. Anything else is rejected.
        for name in (project, dataset):
            if not _SAFE_NAME.fullmatch(name):
                raise ValueError(f"unsafe name: {name!r}")

        self._client = client
        self._table = f"`{project}.{dataset}.claim_status`"

    def get_status(self, claim_id):
        """Return the claim's status, or None if the claim is not in the table."""
        # Only accept IDs the caller has already cleaned (see rules.py).
        if normalize_claim_id(claim_id) != claim_id:
            raise ValueError(f"claim id must be in canonical form: {claim_id!r}")

        # The claim id is sent as a query PARAMETER, never pasted into the SQL.
        # LIMIT 2 is enough to notice duplicates without reading more.
        sql = f"SELECT status FROM {self._table} WHERE claim_id = @claim_id LIMIT 2"
        config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("claim_id", "STRING", claim_id)]
        )
        rows = list(self._client.query(sql, job_config=config).result())

        if not rows:
            return None
        if len(rows) > 1:
            raise DuplicateClaimError(claim_id)

        status = rows[0]["status"]
        # Never pass along a status we do not recognise. The match is exact.
        if parse_status(status) != status:
            raise UnexpectedStatusError(status)
        return status