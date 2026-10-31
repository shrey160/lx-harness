# Spark calibration on the new agent loop (prepared 31 Oct 2026)

Status: **DONE.** Run `4d32acd19c7f` ran 31 Oct at the user's direction, and the agent
clicked approve and start. Result: `evidence/2026-10-31-spark-calibration`.

## Why

The agent loop changed in `3dafd38`, so earlier scores no longer describe the product.
This run sets the new baseline, and it answers three questions:

1. With every photo in view, does Muse Spark 1.3 Contributor answer the inventory task
   without cycling `view`?
2. Does the improvement loop still add to its base?
3. Do the parts that have never run live work on the real API: the shared Meta key, up
   to 6 images in a request, and the guided messages?

It is not a held-out claim. Every score comes from samples the loop adapts on.

## Comparison

T8 is the closest earlier run: the same kind of stream, Spark in every role, 6 samples,
one run per arm. T8 scored 0.315 at base and 0.657 after the loop, and base answered on
2 of 6 samples.

Results compare with T8 only in outline. This run has a different loop, higher limits
and the new controller message, and one run per arm cannot separate a small gain from
noise.

## Fixed inputs

- **Workspace:** `.lx/workspaces/inventory-spark`, "Inventory on Spark", spec version 2,
  live, unapproved.
- **Samples:** 6 samples of 3 to 4 photos, each with `request.wav`, 2.0 MB in all. They
  are a byte-for-byte copy of the `inventory-openrouter` samples (`9650d7f7...`): public
  photos, composites and synthetic speech, with no personal data.
- **Roles:** the built-in Muse Spark 1.3 Contributor on the Meta API for all three, at
  $0.10 and $0.20 per million. Spark Contributor may use approved content for training,
  and approval asks you to accept that.
- **Tools:** SAM 3.1 and Muse Voice Transcribe, both on, on the Meta API.
- **Scoring:** check file `51be3b91...4325`, unchanged, after the run. The improver
  never sees scores.
- **Key:** found in the environment or the core `.env`. Found does not mean Meta accepts
  it, so step 2 below tests it first.

## Limits

All defaults, except the early stop.

| Limit | Value |
|---|---|
| Agent turns per photo | 12, plus 4 (40 to 52 a run) |
| Agent tool calls per photo | 12, plus 6 (42 to 54 a run) |
| **Stop after agent runs in a row with no answer** | **4** (default 8) |
| Images held in a request | Photos plus 2 (5 to 6 here) |
| Improver turns and session | 12 and 180 seconds |
| One model call, whole run | 90 seconds, 4 hours |
| Model, SAM and Transcribe calls | 1,400, 600 and 60 |
| **Run budget, workspace budget** | **$1.50, $2.00** |

Each call first reserves $0.1057, because Spark's whole context is held at the input
price. The run therefore stops at about $1.39 of real spend.

## Expected cost and time

From T8's $0.315 in 56 minutes: Spark $0.18, SAM $0.13, Transcribe under $0.01. Agent
requests now carry more images and more text, and that effect has not been measured, so
these are estimates.

| Outcome | Spend | Time |
|---|---|---|
| Early stop fires | $0.05 to $0.20 | 10 to 20 minutes |
| Run finishes | $0.40 to $0.90 | 1 to 2 hours |
| Spend reaches the budget | About $1.39 | Up to 4 hours |

SAM results are cached per run, so a repeated call inside the run costs nothing.

## How to read the result

Decided before the run:

- **The loop fix works:** at least 5 of 6 base runs give a valid answer (T8: 2 of 6), and
  no agent run stops at a limit while cycling `view`.
- **New baseline:** the base mean from this run replaces T7 and T8 for later work. If it
  is above 0.80, the loop has little room to show a gain on these samples.
- **The loop still adds:** the mean after the loop is above base on at least 3 samples,
  with none lower by more than 0.25. One run per arm makes this a signal, not a claim.
- **Still looping:** the early stop fires. Read the traces before any rerun.
- **Spark refuses the images:** the run fails on its first agent run with an HTTP error,
  at very little cost. No limit is published, so this is possible. The next step is a
  smaller window, not a retry.

We also record what cannot be known offline: whether Spark returns reasoning in a field
we read, how many exact repeat views still happen, and how many replies needed a second
turn.

**Risk:** a run that hits its budget or stops early has no graded score, only health
and traces.

## Steps

1. Start the app with Docker Desktop running: `.\scripts\serve.ps1`.
2. In Settings, under Providers, choose Test on Muse Spark 1.3 Contributor. It sends one
   short message and spends a fraction of a cent from the separate test ledger.
3. Open Inventory on Spark, Spec tab: version 2 is saved. Choose Review and approve.
   Approval freezes the samples, and the review shows who receives what.
4. Start the run on the Runs tab.
5. Results go to a new evidence folder, as for earlier runs.
