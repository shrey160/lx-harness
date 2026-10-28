# S7 Stage-A trials request — PROD-003 voicecount (prepared 29 Oct 2026)

Status: **PREPARED, NOT RUN.** Needs the user's explicit approval.
Controller: `lx.voicecount_trials` (offline-tested in
`tests/test_voicecount_trials.py`). Authority: D-008; calibration S6 passed
its pre-registered gate (A1 0.80, at the upper edge).

## Question

Can Spark, as improver, edit the harness so that Spark as agent uses the
registered model tools better, measured on items it never saw?

## Design (pre-registered in the subplan, unchanged)

- **Five fresh trials**, each from the same base harness ("Answer the spoken
  request about the photo and return JSON.", empty tool guidance).
- **Roles:** `muse-spark-1.3-contributor` is agent, improver, selector and
  reflector. SAM 3.1 and Muse Voice Transcribe are fixed tools;
  `count_boxes` is a fixed deterministic helper.
- **Adaptation:** the core loop on the first **3 loop items**. The improver
  edits only the four always-on Markdown files (in practice
  `tool_descriptions/tool_guidance.md`) and sees the agent's full traces
  (tool calls, arguments, results, final answers), never grades (core check).
- **Retention:** candidate kept only on a strict mean gain over all **40 loop
  items** (controller grades); **5 verify** items checked per trial.
- **Final test** after all trials: base and each retained package once on the
  frozen **40 test items** (never used before; FSC-147 val/test images).
- **Success criterion** (`CRITERION`, checked at start): retained package
  beats base on the final test by **>= 0.10 in at least 4 of 5 trials**, and
  no trial below **-0.05**. Primary metric as in S6.

Fixed inputs: `dataset_sha256` `b306c42f…a2146`, `experiment_sha256`
`129449c2…779c` (same as S6).

## Limits and budget

| Transport | Spark chat | SAM | Transcribe | Budget scope cap |
|---|---|---|---|---|
| each trial (x5) | 1,000 | 300 | 100 | $0.80 |
| final test | 2,000 | 600 | 100 | $1.50 |

- **Experiment cap $5.50** (sum of the scopes), inside the shared $20 ledger
  ($19.853 remaining). The controller refuses to start if less than $5.50
  remains.
- **Expected spend about $2-3**, from S5/S6 rates: an agent execution is
  about 4 Spark calls (~$0.0005) plus 1-2 SAM images ($0.0025 each, free when
  cached); per trial about 100 executions plus improver sessions (large
  contexts, about $0.002-0.005 per call); the final test about 240
  executions. One perception cache is shared across the experiment, so a
  repeated (image, noun phrase) is paid once.
- **Duration:** roughly 2-4 hours (sequential calls). Started detached with
  a log; partial results are written after every trial.

## Guards

Exact dataset hash approval; experiment hash; criterion check; shared ledger
at or after the S6 checkpoint ($0.146868398, 401 settled); at least $5.50
remaining; no earlier rows under `voicecount-trials-v1/`; exclusive
`started.json` marker; fixed endpoints/models; retries off; key loaded at
runtime only; error bodies withheld. Live trial mode is refused for any other
task or caller.

## Command (after approval)

`wsl -e bash -lc 'cd /mnt/c/Shrey_Projs/hackathons/main_LX && nohup env PYTHONPATH=src .venv/bin/python -m lx.voicecount_trials --approve-dataset b306c42f11c867c6746ba9202dcd4208884610d836edcff5a1322460e4fa2146 > .lx/voicecount-trials-v1.log 2>&1 &'`

## Outputs

`main_LX/.lx/voicecount-trials-v1/`: `run/trials.json` (per-trial dev,
verify and test scores, retained or not, usage), `run/partial-trials.json`,
`summary.json` (criterion, gains), `usage.json`; each trial's harness
package and the improver audit. Evidence (scores, packages, criterion,
usage; no media) committed after the run.

## Honest expectations

The base harness already scores 0.80 on calibration; of its 4 errors, about
2 looked fixable by harness behaviour (thresholds, deduplication, noun
phrases) and 2 were SAM misses. A null result is plausible and would still
be a valid answer; the pre-registered next step would then be a harder
variant (for example `count_boxes` removed) with a fresh calibration.
