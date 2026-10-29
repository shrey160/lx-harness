# E-C local-agent inventory evaluation — retry, live run (30 Oct 2026)

Approved by the user ("Approve, run retry"); request reviewed in
[docs/inventory-local-eval-review.md](../../docs/inventory-local-eval-review.md)
(Retry section). The first attempt stopped on a 60 s client timeout
([../2026-10-29-inventory-eval-local/](../2026-10-29-inventory-eval-local/)),
fixed in `ae150e5`.

Base harness vs the frozen T8 harness (`c98c636f...caf6`). The agent was
`gemma-4-E4B-it-GGUF` (Q4_K_M), served by Unsloth Studio at 127.0.0.1:8888,
with a 1,536-token cap per reply. Frozen `inventory-mix-v1`, 9 check + 24
held-out samples. 3 h 29 min, 33 of 33 samples, 0 model errors, run complete.

**Spend $0.31**: 124 new SAM calls. Gemma's 727 calls cost $0, and there
were 258 perception cache hits. Ledger after: $2.054275772 spent,
$17.840047428 remaining, 3576 settled, 6 unknown (none holding cost).

## Pre-registered criterion: **not met**

Held-out (24): mean paired gain **−0.091**, 95% paired bootstrap interval
**[−0.171, −0.018]**. With Gemma as the agent, the frozen harness did
slightly *worse* than the base harness.

| Split | Base | Frozen | Gain | 95% interval | W / T / L | Exact |
|---|---|---|---|---|---|---|
| **held-out (24, decides)** | 0.395 | 0.304 | **−0.091** | [−0.171, −0.018] | 4 / 10 / 10 | 0 → 0 |
| check (9, reported) | 0.248 | 0.367 | +0.119 | [−0.038, 0.303] | 4 / 3 / 2 | 0 → 0 |

By type (both splits, 11 each), with E-B's Spark gains for comparison
(descriptive only):

| Type | Gemma base | Gemma frozen | Gemma gain [95%] | Spark gain (E-B) |
|---|---|---|---|---|
| count | 0.452 | 0.368 | −0.084 [−0.208, 0.002] | +0.036 |
| colour_count | 0.426 | 0.527 | +0.101 [−0.042, 0.257] | +0.244 |
| inventory | 0.186 | 0.069 | −0.117 [−0.234, −0.005] | +0.258 |

## Why (from the rows)

1. **The skill fixes a failure Gemma does not have.** Spark's base runs
   often ran out of tool calls (13/33 gave no answer). Gemma's base runs
   used about 10 tool calls on average and rarely hit the caps (3 turn-cap
   stops). The frozen skill (view each photo once, segment once, bounded
   retry) therefore had little to fix. Gemma loaded it in all 33 frozen runs.
2. **The frozen arm cut off and leaked more.** In the frozen arm:
   - 6 replies hit the 1,536-token cap (base: 3);
   - 2 replies were Gemma's raw tool-call text (`<|channel>call:segment{...}<tool_call|>`),
     which the server did not parse into a tool call (base: 0);
   - 9 of 33 answers were not JSON (base: 6).

   Most of these are on inventory, the longest answers, which is where the
   loss is largest.
3. **Per-photo counting barely changed.** Per-photo checks passed: Gemma
   base 52/133 (0.39), Gemma frozen 53/133 (0.40). For comparison, Spark
   base 59/133 (0.44) and Spark frozen 70/133 (0.53).
4. **No exact answers at all.** Gemma writes the final `answer` field as an
   object, for example `{"count": 79, "colour_count": null}`, instead of the
   number (or the inventory list) the contract asks for. This happened in
   24 of 27 JSON answers in the base arm and all 24 in the frozen arm. The
   schema, consistency and answer checks fail every time, so every Gemma
   score is capped well below 1, even when the per-photo counts and the
   total are right. This is a harness-addressable format error that the
   Spark-learned skill does not mention, because Spark never made it.

## Reading

The harness Spark learned does not transfer to Gemma 4 E4B on this task.
Its gains came from fixing Spark-specific failures (tool-call budget).
Gemma's failures are different: the answer-field format, cut-off long
replies, and tool-call text the server does not parse.

This is the swap result: a learned harness is partly agent-specific. The
next natural step, a later option in the plan, is to re-adapt with Gemma as
the agent (the Harness² loop with Gemma's trajectories), or at least to test
whether a Gemma-run E-A finds the format fix.

Limits: one run per arm per sample; one quantisation and one output cap; the
Gemma-vs-Spark comparison is not paired.

Files: `eval.json` (every row, both arms, summaries, E-B Spark summaries),
`usage.json`, `started.json`.
