# PROD-002 E4a live calibration — 28 October 2026

User approved the exact fixed request with "go ahead". One live base-only
calibration ran against Muse Spark 1.3 Contributor at the fixed Meta endpoint,
40 synthetic text records, no retries, $0.50 scoped cap, historical shared ledger.
Input and experiment hash checks passed. No optimization or other model calls.

**40/40 records accepted; 280/280 fields correct; mean/min/max field accuracy 1.0.**
The base exceeds the pre-registered 0.90 calibration ceiling: `headroom=false`.
Therefore E4b improvement trials are not eligible for this dataset and were not
started. This proves the base succeeds on this narrow synthetic normalization
task; it supplies no harness-improvement evidence and no Stage B readiness claim.

Usage: 40 settled worker/execute calls, 7,680 input / 21,331 output tokens,
computed Contributor cost **$0.004790572**. Ledger reconciles exactly:

- Before: 154 settled, $0.036492926 spent, $19.963507074 remaining.
- After: 194 settled, $0.041283498 spent, **$19.958716502 remaining**.
- Zero reserved or unknown calls before and after; no error state.

Computed token cost is distinct from provider billing/account balance.

Files:

- `live-calibration.json`: original path-free controller report with per-item
  scores, scoped role/phase usage, fixed hashes and before/after ledger summaries.
- `answers.json`: the 40 actual synthetic normalized answers, keyed by sample ID.
- `independent-checks.json`: re-verification from saved answers against the
  separately committed controller truth, with exact integer field counts.

The independent export asserted the 40 IDs match, every answer passes all seven
criteria, both pinned hashes match, exactly 40 calls settled with no unknowns,
and the ledger cost delta equals scoped usage. It made no model calls. Runtime
execution records/paths, credentials and provider raw envelopes were not copied.

The completed one-run marker and ledger scope prevent accidental repeat
dispatch. Next: offline task redesign, fresh held-out examples, and a newly
reviewed calibration; do not reset the ledger or silently activate E4b.
