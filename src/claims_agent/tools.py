"""The dispatcher: the layer between Gemini and the claim store.

Gemini asks for a tool ("change_claim_status with these arguments"). This code
cleans the input, adds what Gemini must never control (the role, who is asking,
and whether the user confirmed), calls the store, and returns a small dict.
"""

import re

from claims_agent.rules import normalize_claim_id, parse_status
from claims_agent.store import (
    ConcurrentChangeError,
    DuplicateClaimError,
    UnexpectedStatusError,
)

_ROLES = ("viewer", "editor")

# A message counts as yes/no only if the WHOLE message (lowercased, without
# punctuation) is one of these. "yes but make it Pending" is neither.
_YES = {"yes", "y", "yep", "yeah", "ok", "okay", "sure", "confirm", "do it",
        "go ahead", "yes go ahead", "yes please", "yes confirm", "yes do it"}
_NO = {"no", "n", "nope", "cancel", "stop", "never mind", "nevermind", "dont",
       "dont do it", "no thanks", "no dont", "no cancel"}

# The menu shown to Gemini. Note what is NOT here: role, user, confirmed.
TOOL_SPECS = [
    {
        "name": "get_claim_status",
        "description": "Look up the current status of one claim.",
        "parameters": {"claim_id": "string"},
        "required": ["claim_id"],
    },
    {
        "name": "change_claim_status",
        "description": "Ask to change the status of one claim.",
        "parameters": {"claim_id": "string", "new_status": "string", "reason": "string"},
        "required": ["claim_id", "new_status"],
    },
]


def _words(text):
    """Lowercase, drop apostrophes and punctuation, squeeze spaces."""
    if not isinstance(text, str):
        return ""
    lowered = text.lower().replace("'", "").replace("’", "")
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", lowered).split())


class AgentTools:
    def __init__(self, store, role, user_id):
        # Fail closed: an unknown role or a missing user stops us at the start.
        if role not in _ROLES:
            raise ValueError(f"unknown role: {role!r}")
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id must be a non-empty string")

        self._store = store
        self._role = role
        self._user_id = user_id
        self._pending = None      # the reversal we are waiting for a yes on
        self._yes_ready = False   # True only right after a plain yes

    def receive_user_message(self, text):
        """Call once per user message, before Gemini runs.

        Returns "cancelled" if the user declined a pending confirmation,
        otherwise None.
        """
        words = _words(text)
        self._yes_ready = False          # a yes never outlives its message

        if self._pending is None:
            return None                  # nothing to confirm, nothing to cancel
        if words in _YES:
            self._yes_ready = True       # keep the pending note
            return None

        self._pending = None             # any other message throws it away
        if words in _NO:
            return "cancelled"
        return None

    def get_claim_status(self, claim_id):
        claim, problem = self._clean_claim_id(claim_id)
        if problem:
            return problem
        try:
            status = self._store.get_status(claim)
        except (DuplicateClaimError, UnexpectedStatusError):
            return {"outcome": "system_error", "claim_id": claim}
        if status is None:
            return {"outcome": "not_found", "claim_id": claim}
        return {"outcome": "status_reported", "claim_id": claim, "status": status}

    def change_claim_status(self, claim_id, new_status, reason=None):
        claim, problem = self._clean_claim_id(claim_id)
        if problem:
            return problem

        status = parse_status(new_status) if isinstance(new_status, str) else None
        if status is None:
            return {"outcome": "invalid_status", "claim_id": claim}
        if reason is not None and not isinstance(reason, str):
            reason = None

        # Confirmed only if the user said a plain yes to THIS claim and status.
        confirmed = False
        if (
            self._yes_ready
            and self._pending is not None
            and self._pending["claim_id"] == claim
            and self._pending["new_status"] == status
        ):
            confirmed = True
            if not (reason and reason.strip()):
                reason = self._pending["reason"]   # reuse the reason from before
        self._yes_ready = False                    # every call uses up the yes

        try:
            outcome = self._store.apply_change(
                role=self._role,              # from the session, never from Gemini
                claim_id=claim,
                requested_status=status,
                reason=reason,
                confirmed=confirmed,
                changed_by=self._user_id,     # from the session, never from Gemini
            )
        except (DuplicateClaimError, UnexpectedStatusError, ConcurrentChangeError):
            self._pending = None
            return {"outcome": "system_error", "claim_id": claim}

        if outcome == "asked_for_confirmation":
            self._pending = {
                "claim_id": claim,
                "new_status": status,
                "reason": reason.strip() if reason else None,
            }
        else:
            self._pending = None

        result = {"outcome": outcome, "claim_id": claim}
        if outcome == "changed":
            result["new_status"] = status
        return result

    @staticmethod
    def _clean_claim_id(claim_id):
        """Return (clean_id, None) or (None, result_dict_to_send_back)."""
        if not isinstance(claim_id, str) or not claim_id.strip():
            return None, {"outcome": "asked_for_claim_id"}
        claim = normalize_claim_id(claim_id)
        if claim is None:
            return None, {"outcome": "invalid_id"}
        return claim, None
