import re
from datetime import datetime

STATUSES = ("Pending", "Approved", "Denied")
ROLES = ("viewer", "editor")
REVERSALS = {("Approved", "Denied"), ("Denied", "Approved")}


def normalize_claim_id(raw):
    """Return the cleaned claim ID (like TP20260621), or None if it is invalid."""
    text = raw.strip().upper()

    # "TP" followed by exactly 8 digits. [0-9] means plain 0-9 only.
    match = re.fullmatch(r"TP([0-9]{8})", text)
    if match is None:
        return None

    # The 8 digits must be a real date (no month 13, no Feb 30).
    try:
        datetime.strptime(match.group(1), "%Y%m%d")
    except ValueError:
        return None

    return text


def parse_status(raw):
    """Return 'Pending', 'Approved' or 'Denied', or None for anything else."""
    cleaned = raw.strip().capitalize()
    if cleaned in STATUSES:
        return cleaned
    return None


def decide_change(role, current, requested, reason, confirmed):
    """Decide what should happen. The ORDER of the checks matters."""
    # Step 0: unknown inputs are errors. We never guess.
    if role not in ROLES:
        raise ValueError(f"unknown role: {role!r}")
    if current not in STATUSES or requested not in STATUSES:
        raise ValueError(f"unknown status: {current!r} / {requested!r}")

    # 1. Only editors may change anything.
    if role != "editor":
        return "refused_permission"

    # 2. Already that status: not an error, there is just nothing to do.
    if current == requested:
        return "no_change_same_status"

    # 3. Never move a claim back to Pending.
    if requested == "Pending":
        return "refused_transition"

    # 4. Reversals need a reason first, then an explicit yes.
    if (current, requested) in REVERSALS:
        if not reason or not reason.strip():
            return "asked_for_reason"
        if not confirmed:
            return "asked_for_confirmation"

    # 5. Everything else that reached this point is allowed.
    return "allowed"