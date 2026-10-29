# E-C live evaluation request — PROD-004 inventory, local agent (prepared 29 Oct 2026)

Status: **PREPARED, NOT RUN.** Needs the user's explicit approval, with the
local server running Gemma 4 E4B.
Controller: `lx.inventory_local_eval`, offline-tested in
`tests/test_inventory_local_eval.py`. Authority: D-008 (a local model is a
provider like any other), D-010 (E-A, E-B, then E-C) and D-012 (E-C uses
Gemma 4 E4B).

## Why

E-B showed that the harness Spark learned in T8 transfers to unseen samples
on Spark: held-out 0.47 → 0.70, gain +0.23 [0.11, 0.37]. E-C asks the swap
question of the project: **with only the agent swapped for a small local
model, does the same frozen harness still help?** SAM and Transcribe stay
the same cloud tools.

## What is the same as E-B

Everything except the agent, its output cap and its scope:
- arms: base harness vs frozen `harnesses/inventory-ea-v1/` (hash
  `c98c636f83a3fdabf4e3a7ed4acabb3b104de19ed6714822b7ef6283024ecaf6`,
  validated against the core surface);
- samples: frozen `inventory-mix-v1` (hash
  `db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322`),
  9 check + 24 held-out, each once per arm, arm order alternating;
- agent caps `caps(n)` (13–19 turns), at most 2 view images per request,
  each at most 400 KB;
- the same evaluation code (`inventory_eval.run_eval`) and scoring;
- the perception cache shared with E-B, so a photo-phrase pair Spark already
  asked SAM about is free.

## What differs

| | E-B | E-C |
|---|---|---|
| Agent | Spark (cloud) | `gemma-4-E4B-it-GGUF` at `http://127.0.0.1:8888/v1`, no key, $0 per token |
| Output cap per reply | 4,096 tokens | **1,536 tokens** |
| Controller host | WSL | Windows (where the local server is reachable) |
| Per-call timeout | 60 s | 600 s |

Why 1,536: in T6, E2B ran away to the 4,096-token limit on its sixth reply.
E-B's longest final answer was about 1,000 JSON characters, which fits well
within 1,536 tokens. A reply that hits the cap ends that run as stopped and
scores what it has; it is not a model error.

## Pre-registered criterion (fixed before any E-C call; the same as E-B)

On the **24 held-out** samples, two conditions must both hold:
- the mean paired checklist gain (frozen − base) is **at least 0.10**;
- the lower end of the **95% paired bootstrap interval** (10,000 resamples,
  seed 20261029) is **above 0**.

The 9 check samples are reported but do not decide. If 5 model errors
occur, for example server timeouts or a context overflow, the run stops with
no criterion result.

**Also reported:**
- per split and per type: means, gain, interval, wins, ties, losses, exact
  answers;
- E-B's Spark summaries side by side, descriptive only (Gemma vs Spark is
  not a paired claim).

## Limits and budget

- Call limits:

  | | Limit |
  |---|---|
  | Gemma chat calls | 1,300 (worst case at the caps about 1,250) |
  | SAM calls | 600 |
  | Transcribe calls | 40 |
- **Budget:**
  - Scope `inventory-eval-local-v1`, cap **$1.50**.
  - Gemma is $0. Spend is SAM only, at $0.0025 per new photo-phrase pair.
    E-B paid for 292, and the cache now holds 435 entries.
  - Expected spend: **about $0.10–0.60**. Gemma may phrase concepts
    differently from Spark; the cap binds before SAM's call limit.
  - Transcribe should be fully cached.
- **Wall time:** unknown, because it depends on the GPU. At about
  10–30 s per local reply, and about 800 replies, expect about **2–7 hours**.
  The PC must stay awake with the server running.
- **Guards:**
  - exact dataset and harness hashes, each passed on the command line;
  - harness validation;
  - the local server must list `gemma-4-E4B-it-GGUF` (a GET of
    `/v1/models`, no generation) or the run refuses to start;
  - shared ledger at or after the checkpoint ($1.744275772 spent, 2584
    settled);
  - at least $1.50 left in the ledger;
  - no earlier rows in the scope;
  - an exclusive `started.json` marker;
  - fixed endpoints and models; retries off; the SAM/Transcribe key is loaded
    at runtime only.

## Before approving

- Start Unsloth Studio with **`gemma-4-E4B-it-GGUF`** loaded, not E2B, at
  `127.0.0.1:8888`.
- Set a context window of at least about **16k tokens**. Spark's prompts in
  E-B averaged about 4.2k tokens and grow over a run.
- Keep the machine awake for the run.

## Command (Windows PowerShell, from `main_LX`, after approval)

```
$env:PYTHONPATH="src;..\harness2_portable_core\vendor\harness2-cli"; & .\.lx\tts-venv\Scripts\python.exe -m lx.inventory_local_eval --approve-dataset db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322 --approve-harness c98c636f83a3fdabf4e3a7ed4acabb3b104de19ed6714822b7ef6283024ecaf6
```

## Outputs

The run writes `main_LX/.lx/inventory-eval-local-v1/eval.json` and
`usage.json`:
- every row with both arms' answers, scores, failed checks, tool calls and
  stop reasons;
- the summaries;
- the criterion result and decision;
- E-B's Spark summaries for comparison.

Evidence is committed after the run.

## How to read the result

- **Criterion met:** the harness Spark learned helps a small local agent
  too. That is the swap claim, for this task and these samples.
- **Not met:** report the observed effect. Read the stop reasons: cut-off
  replies, turn caps and malformed tool calls point to local-model limits
  that the harness could address. Re-adapting with Gemma as the agent is a
  later option.
- **Limits:**
  - one run per arm per sample;
  - one local model, at one quantisation and output cap;
  - the Gemma-vs-Spark comparison is descriptive only.
