# PROD-002 R3 difficulty-ladder calibration request — 29 October 2026

**Status: APPROVED by the user ("Approve, run it") and EXECUTED ONCE on
29 October 2026. Do not repeat.**

**Result:** the base scored 1.000 at all four levels, 40/40 accepted, with no
errors. No level fell in the 0.30-0.80 band, so R4 is not eligible; the next
step is to revise the generator.

**Cost:** $0.007781098 for 40 settled calls. The shared ledger stands at
$0.049064596 spent and $19.950935404 remaining.

See the [evidence](../evidence/2026-10-29-eventlog-calibration/README.md).

## Purpose

The records task saturated: its base score was 1.0. Before asking for live
trials (R4), this run measures how the **base harness alone** scores on the
new event-log task at each difficulty level. The result picks the level used
for R4. No improver, no candidate harness and no trial runs here, so this
cannot show whether the harness improves.

## What would be sent

- **Items:** 10 fictional event logs at each of difficulty levels 1-4, so
  40 items. They are all from the `calibration` split (seed 20261029), which
  is never used for loop, verify or test items.
- **Where to read them:** every input is in
  `evidence/2026-10-29-eventlog-calibration-request/level-N/manifest.json`.
  Inputs are 321-1,792 characters each, about 37 kB in total.
- **Rules:** the task rules are in [eventlog-contract.md](eventlog-contract.md).
- **Payload per call:** the base instruction files, the ten-rule task
  contract, and one generated log. Answer keys and verifier results stay
  local.
- **Model and endpoint:** `muse-spark-1.3-contributor` at the fixed Meta
  endpoint `https://api.meta.ai/v1/chat/completions`, through the existing
  live transport. It sends text only, with no tools, images, discussion,
  private folders or other models.
- **Data exposure:** the existing Contributor disclosure covers these
  synthetic inputs.

## Bounds

- **Calls:** at most 40, enforced by the transport. No retries.
- **Cost cap:** $0.50 for this run, inside the existing shared $20 ledger.
  Last recorded remaining: $19.958716502.
  - Each call reserves $0.1056768 before dispatch: the full context ceiling
    plus 4,096 output tokens. A lower cap could not dispatch a call.
  - Expected actual cost is a few cents. The records calibration cost
    $0.0048 for 40 shorter calls. This is an estimate, not a quote.
- **Errors:**
  - A reply that is cut off or malformed is still charged. That item scores
    0 and is recorded as an error.
  - After 3 such errors the run stops.
  - A cut-off reply at level 4 would itself be a useful signal about
    difficulty.
- **Credentials:** the existing registered Spark credential is loaded only at
  runtime by the WSL application. No values enter files, reports or logs.

## Decision rule (fixed before running)

- For each level, record the base mean field score.
- **Choose the lowest level whose mean is between 0.30 and 0.80.** That level
  is proposed for R4.
- If no level falls in the band, return to R1 and adjust the generator.
  Examples: if L4 scores above 0.80, add harder levels; if every level is
  below 0.30, lower the difficulty.
- The report also records:
  - per-field accuracy for each level, to show which rules fail
  - output tokens per item
  - accepted counts

## Pinned hashes

Worker-input SHA256 over the four levels:
`04455ea074864b87f614c2cb595080ce464c8c6181d2b59603e95354a7502822`

Experiment SHA256 over the four levels. It covers the inputs, controller
truth, difficulty, contract, base prompt, message builder and verifier source:
`4399af14c327250bb8f5b32f633f9566742dd4bf50964393b3e83be57da246ef`

## Command (only after approval)

```bash
.venv/bin/python -m lx.eventlog_calibration --approve-dataset 04455ea074864b87f614c2cb595080ce464c8c6181d2b59603e95354a7502822
```

Before constructing any transport, the controller
(`src/lx/eventlog_calibration.py`):
- regenerates the ladder and checks both hashes
- requires the existing shared ledger to be at or past the 28 Oct checkpoint
  ($0.041283498 spent and 194 settled calls)
- refuses to run if its scope `eventlog-calibration-v1` has already
  dispatched calls
- creates an exclusive one-run marker

It writes `calibration.json` and `usage.json` under `.lx/eventlog-calibration-v1/`.

Offline guard tests are in `tests/test_eventlog_calibration.py`. They cover:
- wrong approval
- missing ledger
- an old checkpoint
- a repeat dispatch and an existing marker
- a changed answer key
- ladder scoring and level choice
- error handling

None of these tests makes a network call.

## Not covered by this request

- R4 live trials: 5 trials plus the final test. They need their own request,
  which must fix the level, k, the per-trial call limit (200 at k=1, or about
  350 at k=4) and the total cap.
- Any change to the improver's grade-blind design.
