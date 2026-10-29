# E-B inventory evaluation — live run (29 Oct 2026)

Approved by the user ("Approve, run E-B"); request reviewed in
[docs/inventory-eval-review.md](../../docs/inventory-eval-review.md).
Base harness vs the frozen T8 harness (`harnesses/inventory-ea-v1`, hash
`c98c636f...caf6`), Spark as agent, no improver; frozen `inventory-mix-v1`
(hash `db6c70f6...c322`), 9 check + 24 held-out samples, one run per arm per
sample, arm order alternating. 85 minutes, 0 model errors, run complete.

**Spend $0.922790764** (Spark 663 calls $0.1891, SAM 292 calls $0.7300,
Transcribe 31 calls $0.0037; 143 perception cache hits). Ledger after:
$1.744275772 spent, $18.150047428 remaining, 2584 settled, 1 unknown (the
old T6 one).

## Pre-registered criterion: **met**

Held-out (24): mean paired gain **+0.231** (≥ 0.10 required), 95% paired
bootstrap interval **[0.106, 0.366]** (lower end > 0 required).

| Split | Base | Frozen | Gain | 95% interval | W / T / L | Exact (base → frozen) |
|---|---|---|---|---|---|---|
| **held-out (24, decides)** | 0.470 | 0.701 | **+0.231** | [0.106, 0.366] | 11 / 11 / 2 | 9 → 11 |
| check (9, reported) | 0.605 | 0.647 | +0.041 | [-0.078, 0.183] | 2 / 5 / 2 | 2 → 2 |

By request type (both splits, 11 each; descriptive):

| Type | Base | Frozen | Gain | 95% interval | W / T / L | Exact |
|---|---|---|---|---|---|---|
| count | 0.700 | 0.737 | +0.036 | [-0.036, 0.155] | 1 / 8 / 2 | 5 → 6 |
| colour_count | 0.638 | 0.883 | +0.244 | [0.043, 0.473] | 6 / 4 / 1 | 4 → 6 |
| inventory | 0.182 | 0.439 | +0.258 | [0.083, 0.423] | 6 / 4 / 1 | 2 → 1 |

## What changed

The gain comes mostly from answering. Base runs stopped without an answer on
13 of 33 samples (10 at the tool-call cap, 3 at the turn cap); frozen runs on
3 of 33 (the same 3 also failed in the base arm). Frozen runs also used fewer
tool calls on most samples. This is the failure T7 found and T8's skill
targets (view each photo once, segment once, one bounded retry, then answer).

## Losses and limits

- **Inventory** improved the score through partial credit (answered with
  partly correct per-photo counts) but not exact answers (2 → 1). Three
  inventory samples fail in both arms without an answer (heldout-05,
  heldout-08, check-03). The one loss, heldout-inventory-04, was exact in
  base and missed one photo in the frozen arm.
- **Count** is flat: the dense FSC photos are counted by eye in both arms.
  fsc-heldout-08's frozen answer is graded exact on the total while three
  per-photo counts are wrong (offsetting errors).
- **Check split** shows no clear gain (2 wins, 2 losses); it has 9 samples
  and does not decide.
- One run per arm per sample: the interval covers sample variation, not
  run-to-run variance. Composite photos look pasted; colour answers are
  small.

Files: `eval.json` (every row with both arms' answers, scores, failed
checks, tool calls and stop reasons; summaries; decision), `usage.json`,
`started.json`.
