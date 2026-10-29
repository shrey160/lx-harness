# E-B live evaluation request — PROD-004 inventory (prepared 29 Oct 2026)

Status: **RUN 29 Oct 2026 — criterion met** (held-out gain +0.231, 95% interval [0.106, 0.366]); evidence in `evidence/2026-10-29-inventory-eval/`.
Controller: `lx.inventory_eval`, offline-tested in
`tests/test_inventory_eval.py`. Authority: D-008, D-010 (E-A, then E-B and
E-C) and D-012.

## Why

T8 (E-A) raised the per-sample checklist mean from 0.31 to 0.66 on the 6
stream samples. That is improvement on the samples the loop worked on. E-B
asks the question a user cares about: **does the harness the loop wrote also
help on samples it never saw?**

## Arms

| Arm | Harness | Agent |
|---|---|---|
| base | the base harness (as T7 and the T8 base runs) | Spark |
| frozen | `main_LX/harnesses/inventory-ea-v1/`: the latest T8 harness (the `inventory` skill plus one guardrail trigger line; no scripts) | Spark |

The frozen harness is a byte copy of
`evidence/2026-10-29-inventory-ea/library/fsc-stream-02/harness/`.
- Approval hash: `c98c636f83a3fdabf4e3a7ed4acabb3b104de19ed6714822b7ef6283024ecaf6`
  (computed over the file texts).
- It passes the slice's component validation.
- The prompt is composed with the same core surface the T8 loop used.

There is no improver, and nothing is edited during E-B.

## Samples

Frozen `inventory-mix-v1`, hash
`db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322`:
- 9 **check** samples, from the user bundle;
- 24 **held-out** samples, hidden.

That is 33 samples, 11 of each request type. Neither split was seen by T7 or
T8. Each sample runs once per arm, and the arm order alternates from one
sample to the next.

## Pre-registered criterion (fixed before any E-B call)

On the **24 held-out** samples, two conditions must both hold:
- the mean paired checklist gain (frozen − base) is **at least 0.10**;
- the lower end of the **95% paired bootstrap interval** (10,000 resamples,
  seed 20261029) is **above 0**.

The 9 check samples are reported next to it as the user's own view, but they
do not decide. If 5 model errors occur, the run stops with no criterion
result.

**Also reported:**
- per split and per request type: base mean, frozen mean, gain, interval,
  wins, ties and losses;
- exact-answer counts.

## Limits and budget

- Agent caps per run as before: `caps(n)`, 13–19 turns. At most 2 view
  images per request, each at most 400 KB.
- Call limits:

  | | Limit |
  |---|---|
  | Spark chat calls | 1,300 (worst case at the caps about 1,250) |
  | SAM calls | 600 |
  | Transcribe calls | 40 |

  Both arms share the perception cache, so a SAM call for a photo and
  phrase is paid once.
- **Budget:**
  - Scope `inventory-eval-v1`, cap **$2.50**.
  - Expected spend: **about $0.80–1.30**. Spark is about $0.0003 per call;
    SAM is $0.0025 per new photo-phrase pair, about 10 per sample.
  - SAM's worst case at the limit is $1.50.
- **Wall time:** about 1.5–2 hours.
- **Guards:**
  - exact dataset and harness hashes, each passed on the command line;
  - harness validation;
  - shared ledger at or after the checkpoint ($0.821485008 spent, 1598
    settled);
  - at least $2.50 left in the ledger;
  - no earlier rows in the scope;
  - an exclusive `started.json` marker;
  - fixed endpoints and models; retries off;
  - error bodies withheld; key loaded at runtime only.
- **Command** (WSL, after approval):
  `wsl -e bash -lc 'cd /mnt/c/Shrey_Projs/hackathons/main_LX && PYTHONPATH=src .venv/bin/python -m lx.inventory_eval --approve-dataset db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322 --approve-harness c98c636f83a3fdabf4e3a7ed4acabb3b104de19ed6714822b7ef6283024ecaf6'`

## Outputs

The run writes `main_LX/.lx/inventory-eval-v1/eval.json` and `usage.json`:
- every row, with both arms' answers, scores, failed checks, tool calls and
  stop reasons;
- the summaries by split and by type;
- whether the criterion was met, and the decision.

Evidence is committed after the run.

## How to read the result

- **Criterion met:** the learned skill transfers to unseen samples on Spark.
  Prepare E-C: the same two arms with Gemma 4 E4B as the agent and a lower
  per-turn output cap.
- **Gain below 0.10, or an interval that crosses 0:** report the observed
  effect with no transfer claim. Read the losses, especially inventory, where
  T8 did not improve.
- **Limits:** one run per arm per sample, so the interval covers sample
  variation, not run-to-run variance. Colour answers are small. The
  composites look pasted.
