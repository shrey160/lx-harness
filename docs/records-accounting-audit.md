# PROD-002 accounting audit — 28 October 2026

Read-only review of committed sanitized evidence and the ledger/application code;
no runtime ledger or credentials were inspected and no live requests were made.

The O-11 report's job subtotal is 117 settled calls / $0.026845888. The shared
ledger moves from 35 to 154 calls and $0.009337338 to $0.036492926: 119 calls /
$0.027155588. Therefore **two settled calls / $0.000309700** are outside the job
subtotal. Job usage filters the candidate prefix `<job-id>/`; live discussion
uses a separate `discussion-<project-id>/` prefix. Preflight is job-scoped and
is already included. Two discussion requests fit the difference, but the
sanitized report lacks reservation rows to prove that attribution. There is
no evidence here of missing settlements; exact historical phase attribution
remains unavailable. New usage reports include phase counts and charges plus
reserved/unknown counts so future reports can reconcile these explicitly.

`BudgetLedger`'s initial $0.001 is an opening amount, not the current total.
Existing databases enforce an exact match of cap/opening configuration and
sum that opening amount with all settled charges. The report is consistent
with $0.001 plus the previous 35 S1 calls; reopening did not reset spending.
Deleting/replacing a ledger would lose its history. The fixed calibration
controller additionally requires the existing ledger file and at least the
recorded 154 calls / $0.036492926 before dispatch.

Code reviewed: `src/lx/budget.py`, `spark.py`, `app.py`, `worker.py`, `usage.py`,
and `live_transport.py`. Evidence: `evidence/2026-10-28-live-app/live-report.json`.
The transport's 200-call ceiling is per instance. A future five-trial E4b
controller must enforce both fresh bounded trial transports and one shared
ledger, with at most $0.40 per trial ($2 total). It is not activated by the
offline runner. Full default trials require about 124 worker requests each
before improver/reflection traffic, so limits must be checked before approval.
