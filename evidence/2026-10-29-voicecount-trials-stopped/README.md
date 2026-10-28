# S7 voicecount trials — stopped during trial 1 (29 Oct 2026)

Approved ("Approve, run S7") per
[voicecount-trials-review.md](../../docs/voicecount-trials-review.md), then
**stopped by the user** ("Stop it now") about 40 minutes in, during trial 1's
development evaluation (28 of 40 loop items scored for base and candidate).
No trial completed, so there is **no criterion result** and no capability
claim. The run was interrupted with SIGINT; the one in-flight Spark call was
recorded as `unknown` (its $0.1056768 reservation stays held, per ledger
policy).

## What happened in adaptation

The improver ran its full core loop on the 3 adaptation items (loop-0001
tomatoes, loop-0002 cranes, loop-0003 alcohol bottles): 119 improvement and
30 reflection calls. For every item the chosen harness was **identical to
base** (`chosen_tree_is_base: true`); the other proposals (`inv1`, `dom1`)
were also unchanged. The improver's notes explain why, for example:

> kept -> base harness unchanged (KEEP_BASE) -> base 1/1 rollout delivered
> correct JSON via transcribe then segment then count_boxes with region right
> and count 31, no repeated failure to fix

All three base rollouts on those items were in fact correct, and because the
improver is grade-blind (D-008(5)), a wrong count that looks like a clean run
is invisible to it. The trial was therefore heading for "candidate = base".

## Partial development scores (27 items scored by both arms)

| Arm | Mean | Used min_confidence/dedupe | Stopped by caps |
|---|---|---|---|
| base | 0.556 | 5 | 0 |
| candidate (identical harness) | 0.593 | 2 | 0 |

26 of 27 items scored the same; the one difference is run-to-run variance.
Base failures on loop items include oranges (right) 32 vs 18, cupcake tray
1 vs 12, oranges 26 vs 15, cows 35 vs 50, candles 14 vs 28, kidney beans 65
vs 55: **the loop split is much harder for the base harness (0.56) than the
calibration split was (0.80)**, so real headroom exists; the adaptation
items just did not expose it.

## Spend

Scope `voicecount-trials-v1/`: 502 settled calls, **$0.200876334** (Spark
agent 276 calls $0.034747, improver 149 calls $0.037253, SAM 50 images
$0.125, Transcribe 27 clips $0.003876), plus 1 unknown call holding
$0.1056768. Shared ledger: **$0.347744732 spent, $0.1056768 held,
$19.546578468 remaining, 903 settled, 1 unknown**.

Files: [partial.json](partial.json) (selections, per-item scores, improver
notes), [usage.json](usage.json).
