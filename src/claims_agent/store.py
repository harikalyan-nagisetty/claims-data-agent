import re
import uuid
from claims_agent.rules import decide_change, normalize_claim_id, parse_status

from google.cloud import bigquery

# Names that go into the table path may only contain these characters.
_SAFE_NAME = re.compile(r"[A-Za-z0-9_-]+")
_ROLES = ("viewer", "editor")
_STATUSES = ("Pending", "Approved", "Denied")

_WRITE_SQL = """
DECLARE rows_updated INT64 DEFAULT 0;

BEGIN TRANSACTION;

UPDATE {status_table}
SET status = @new_status, updated_at = CURRENT_TIMESTAMP(), updated_by = @changed_by
WHERE claim_id = @claim_id AND status = @expected_status;

SET rows_updated = @@row_count;

IF rows_updated = 1 THEN
  INSERT INTO {audit_table}
    (audit_id, claim_id, old_status, new_status, reason, changed_by, changed_at)
  VALUES
    (@audit_id, @claim_id, @expected_status, @new_status, @reason, @changed_by,
     CURRENT_TIMESTAMP());
  COMMIT TRANSACTION;
ELSE
  ROLLBACK TRANSACTION;
END IF;

SELECT rows_updated;
"""

class DuplicateClaimError(Exception):
    """The table has more than one row for the same claim."""


class UnexpectedStatusError(Exception):
    """The table holds a status that is not Pending, Approved or Denied."""

class ConcurrentChangeError(Exception):
    """Someone else changed the claim between our read and our write."""


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
        self._write_sql = _WRITE_SQL.format(
            status_table=self._table,
            audit_table=f"`{project}.{dataset}.claim_audit`",
        )

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

    def apply_change(self, role, claim_id, requested_status, reason, confirmed, changed_by):
        if normalize_claim_id(claim_id) != claim_id:
            raise ValueError(f"claim id must be in canonical form: {claim_id!r}")
        if requested_status not in _STATUSES:
            raise ValueError(f"unknown status: {requested_status!r}")
        if role not in _ROLES:
            raise ValueError(f"unknown role: {role!r}")
        if not isinstance(changed_by, str) or not changed_by.strip():
            raise ValueError("changed_by must be a non-empty string")

        if role != "editor":
            return "refused_permission"

        current = self.get_status(claim_id)
        if current is None:
            return "not_found"

        decision = decide_change(role, current, requested_status, reason, confirmed)
        if decision != "allowed":
            return decision
                # 5. Keep the reason trimmed, or None if there is none.
        clean_reason = reason.strip() if reason and reason.strip() else None

        # 6. Every value travels as a parameter. expected_status is the status
        #    we read in step 3 (compare-and-set).
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("claim_id", "STRING", claim_id),
                bigquery.ScalarQueryParameter("expected_status", "STRING", current),
                bigquery.ScalarQueryParameter("new_status", "STRING", requested_status),
                bigquery.ScalarQueryParameter("changed_by", "STRING", changed_by),
                bigquery.ScalarQueryParameter("reason", "STRING", clean_reason),
                bigquery.ScalarQueryParameter("audit_id", "STRING", str(uuid.uuid4())),
            ]
        )

        # 7. Run the script. If no row was updated, someone changed the claim
        #    after we read it, so we stop instead of overwriting their change.
        rows = list(self._client.query(self._write_sql, job_config=config).result())
        if rows[0]["rows_updated"] != 1:
            raise ConcurrentChangeError(claim_id)
        return "changed"

        raise NotImplementedError