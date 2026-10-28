# R3 event-log difficulty ladder — live result, 29 October 2026

The user approved the reviewed request by saying "Approve, run it". It ran
once:

```bash
.venv/bin/python -m lx.eventlog_calibration --approve-dataset 04455ea074864b87f614c2cb595080ce464c8c6181d2b59603e95354a7502822
```

This was the base harness only: `muse-spark-1.3-contributor`, 10
calibration-split cases at each level, no retries.

## Result

| Level | Mean field score | Accepted | Max output tokens |
|---|---|---|---|
| 1 | 1.000 | 10/10 | 476 |
| 2 | 1.000 | 10/10 | 579 |
| 3 | 1.000 | 10/10 | 844 |
| 4 | 1.000 | 10/10 | 1,188 |

All seven fields were 100% correct at every level. No level reached the
0.30-0.80 band, so the fixed decision rule applies: **no level for R4; revise
the generator (R1)**. There were no errors and no cut-off replies.

## Verification

- **Rescoring:** all 40 saved answers were rescored independently against
  freshly regenerated controller truth, with the same pinned hashes. The
  scores match the report exactly.
- **Usage:** 40 worker calls settled, with 0 reserved and 0 unknown. Tokens:
  34,031 input and 25,687 output. Cost: $0.007781098, computed from
  reported tokens at list price; this is not provider billing.
- **Shared ledger:** it went from $0.041283498 spent and 194 settled calls to
  $0.049064596 and 234. The deltas, $0.007781098 and 40 calls, match the run
  exactly. Remaining: $19.950935404.

## Files

- `calibration.json`: the full report, with per-item scores, per-field
  accuracy and output tokens.
- `usage.json`: the scope usage, plus the ledger before and after.
- `started.json`: the one-run marker.
- `answers/level-N/*.json`: each saved model answer with its usage.
- The inputs are in `../2026-10-29-eventlog-calibration-request/`.

## What it means

- With all ten rules stated explicitly, the base model replays shuffled
  logs perfectly. That holds even at level 4: about 19 lines, duplicates,
  near-identical distractor orders, cancel/reinstate, currency conversion,
  half-even rounding and refund caps.
- Interacting procedural rules at this length leave no headroom.
- This measures the base model only. It is not evidence about harness
  improvement either way.
