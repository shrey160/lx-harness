# T8 live E-A stream request — PROD-004 inventory (prepared 29 Oct 2026)

Status: **PREPARED, NOT RUN.** Needs the user's explicit approval.
Controller: `lx.inventory_stream`. It is offline-tested in
`tests/test_inventory_stream.py`, with the real core loop and sandbox and
stand-in models. Authority: D-008, D-010 (E-A first, then E-B and E-C) and
D-012.

## Why

E-A runs the paper's loop exactly as published (Harness², Propose-Probe-
Compose, k=1, sequential mode, the experience library carried between tasks)
on what a user would hand over: the 6 stream samples of the bundle. It
answers whether Spark, acting as improver, can edit its own harness so that
it does better on the sample in front of it. That per-task gain is the
paper's metric.

T7 showed where the room is. The base harness scored a mean of 0.39, and 3
of 6 samples used up their tool calls on repeated `view` calls without
answering.

This is not a held-out claim. E-B, which freezes a harness and scores it on
the 9 check and 24 held-out samples, is a separate request after this one.

## Fixed inputs

Frozen `inventory-mix-v1`, approval hash
`db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322`. The
stream runs in this fixed order, interleaved by request type so the library
sees every type early:

1. `fsc-stream-01` (count, FSC-147)
2. `mix-stream-colourcount-01` (colour)
3. `mix-stream-inventory-01` (inventory)
4. `fsc-stream-02` (count)
5. `mix-stream-colourcount-02` (colour)
6. `mix-stream-inventory-02` (inventory)

The check, held-out and calibration samples are not used. The data sent is
the same kind as in T7: public photos, composites and synthetic speech, with
no personal data.

## What runs per sample (vendored core, unchanged)

1. **Base run:** the base harness, the same arm as T7.
2. **Propose:** the improver (Spark) reads the trajectory, harness and
   library, then writes an invariant edit and a domain edit. It can edit
   any of the allowed components:
   - `systemprompt.md`, `guardrails.md`, `LongTermMEMORY.md`, tool guidance;
   - up to 8 skills and 8 scripts (Python with Pillow, run in the WSL
     sandbox with no network and no host files).

   Sub-agents and plugins are refused.
3. **Probe:** each edit is run on the same sample.
4. **Compose:** a blind selection keeps the merged harness, one edit or the
   base.
5. **Reflect:** the reflector writes one experience entry into the library
   for the next samples.

The improver and selector are **grade-blind**: they see trajectories and
answers, never checklist scores or the truth. After the loop, the controller
grades each sample's base run and chosen run with the T7 checklist.

**Reported:**
- per-sample base score, Harness² score and their difference;
- the means;
- which components were added;
- the library entries.

## Limits and budget

- Agent caps per run as in T7: `caps(n)`, 13–19 turns. At most 2 view images
  per request, each at most 400 KB.
- Improver and reflector sessions are capped at 12 turns each.
- Call limits:

  | | Limit |
  |---|---|
  | Spark chat calls | 900 (about 4 agent runs and 3–4 improver sessions per sample; worst case about 750) |
  | SAM calls | 300 |
  | Transcribe calls | 40 |

  Identical SAM and Transcribe calls reuse T7's cache
  (`.lx/perception-cache/inventory-mix-v1`).
- **Budget:** scope `inventory-ea-v1`, cap **$1.50**. Expected spend:
  **about $0.40–0.90**.
  - Spark cost about $0.0003 per call in T7 and S7.
  - SAM costs $0.0025 per new photo-phrase pair, so its worst case at the
    limit is $0.75.
- **Wall time:** about 1–2 hours. T7 averaged 5.6 s per call.
- **Guards:**
  - exact approval hash;
  - the WSL sandbox must be available;
  - shared ledger at or after the checkpoint ($0.50603337 spent, 1058
    settled);
  - at least $1.50 left in the ledger;
  - no earlier rows in the scope;
  - an exclusive `started.json` marker;
  - fixed endpoints and models; retries off;
  - error bodies withheld; key loaded at runtime only.
- **Stopping:** if the budget or a call limit is hit, the remaining samples
  fail cleanly and the report is still written. Stopping by hand (SIGINT)
  keeps `usage.json`. A restart needs a new reviewed retry.
- **Command** (WSL, after approval):
  `wsl -e bash -lc 'cd /mnt/c/Shrey_Projs/hackathons/main_LX && PYTHONPATH=src .venv/bin/python -m lx.inventory_stream --approve-dataset db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322'`

## Outputs

The run writes to `main_LX/.lx/inventory-ea-v1/`:
- `summary.json` and `usage.json`;
- `run/ea_report.json`, with the per-sample rows, gains, and the improved and
  regressed samples;
- `run/library/` (the experience entries);
- `run/runs/` (every execution's answer and trace, plus the chosen
  harnesses);
- the improver, selector and reflector audits.

Evidence is committed after the run: the report, the library, the chosen
harness files and the usage, but not the photos.

## How to read the result

- **Gain on several samples, with skills or scripts that fix the T7 failure
  modes** (answering before the cap, segmenting before viewing, tiling dense
  photos): the loop works on this task. Next, prepare E-B with the last
  chosen harness, or one composed from the library.
- **Base kept everywhere** (as in S7): the improver sees no failure it can
  name. D-009, giving the improver the names of failed checks, becomes the
  next decision.
- **Regressions:** read the chosen harnesses before E-B.
- With 6 samples and one run each, this is descriptive, not a statistical
  claim.
