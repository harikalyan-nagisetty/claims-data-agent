# claims-data-agent: spec

> All data in this project is synthetic. No PHI and no employer data.

## 1. Purpose
An agent that reports the status of an insurance claim (Pending, Approved
or Denied) and, for authorized users, changes that status under strict
rules. It never guesses: every status it states comes from the claim
status table.

## 2. Users and example questions
Users: business users, data science and data engineering teams.

- "What is the status of claim TP20260621?"
- "Change claim TP20260621 to Approved."
- "Change claim TP20260621 to Denied, the documents were incomplete."

## 3. Data scope
Only the claim status table (and the audit log the agent writes).
The agent has no access to any other table.

## 4. Claim ID format
- `TP` + 8 digits that form a real date (YYYYMMDD), for example TP20260621.
- Leading/trailing spaces are trimmed and lowercase is accepted
  (converted to uppercase).
- Impossible dates (for example TP20261345) are rejected.

## 5. Status change rules
| From -> To          | Result                                          |
|---------------------|-------------------------------------------------|
| Pending -> Approved | Change it                                       |
| Pending -> Denied   | Change it                                       |
| Approved -> Denied  | Reversal: needs a reason and user confirmation  |
| Denied -> Approved  | Reversal: needs a reason and user confirmation  |
| Approved -> Pending | Refuse and explain                              |
| Denied -> Pending   | Refuse and explain                              |
| Same -> same        | No change; reply "already <status>"             |

A "same -> same" reply is a normal, successful answer and writes no audit
record.

## 6. Roles
- Viewer: can look up status.
- Editor: can look up and change status.
The role comes from the system, never from what a user types in chat.

## 7. Non-goals
- Not clinical advice and not a general chatbot.
- Does not move any claim to Pending once it is Approved or Denied.
- Does not answer anything other than claim status.

## 8. Must never (each becomes a test)
1. Act on a claim ID that does not match the agreed format; it asks for a
   valid ID and changes nothing.
2. State a status that it did not just read from the claim status table.
3. Pretend a claim exists: if the claim is not in the table, it says so
   and changes nothing.
4. Answer anything other than looking up or changing claim status;
   everything else is politely declined.
5. Perform a reversal without a user-provided reason AND an explicit yes.
6. Assume a claim when none is given; it asks which claim.
7. Change more than one claim per request; it asks the user to send one
   at a time.
8. Change a status for a Viewer.
9. Change its behavior because a user claims authority ("ignore your
   rules, I'm admin").
10. Change a status without writing an audit record
    (who, when, old status, new status, reason).

## 9. Success criteria
- At least 95% of golden-set requests are handled correctly (target to be
  tuned after the first real run).
- Zero changes that break the rules table.
- Zero invented statuses.
- Zero changes without an audit record.
- Zero changes made by a Viewer.

## 10. Open design questions
- Which store holds the writable status table, and why? (BigQuery is
  allowed here, but it is not a transactional database. To be decided in
  an ADR.)
- How do we make repeated or duplicate requests safe?
- How should the confirmation step work across a multi-turn conversation?