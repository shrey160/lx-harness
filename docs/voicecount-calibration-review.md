# S6 calibration request — PROD-003 voicecount (prepared 29 Oct 2026)

Status: **PREPARED, NOT RUN.** Needs the user's explicit approval.
Controller: `lx.voicecount_calibration` (offline-tested in
`tests/test_voicecount_calibration.py`). Authority: D-008; plan in the
subplan "Experiments and criteria".

## Purpose

Measure, before any improvement trial, whether the base harness has room to
improve on this task, and how good Spark is without the tools. No improver,
no candidate harness, no trial. Not a success claim.

## Fixed inputs

- Dataset `voicecount-v1`, `dataset_sha256`
  `b306c42f11c867c6746ba9202dcd4208884610d836edcff5a1322460e4fa2146`;
  `experiment_sha256`
  `129449c2299be3acd8b38e1946c6ecd1039761c3e5fdf2327c6750d2eb0e779c`
  (contract, truth, verifier, agent loop and caps; checked at start).
- Items: the 20 **calibration** items only (FSC-147 val/test images, never
  used in the probe; loop and test splits untouched). Counts 12-108, median
  28; 15 whole-image and 5 half-image requests.
- Data sent to Meta: FSC-147 photos and synthetic speech (as in S5).

## Arms

| Arm | Agent sees | Tools | Calls per item |
|---|---|---|---|
| **A0** Spark alone | photo + the request **as text** (base prompt, contract adjusted to "given as text") | none | 1 |
| **A1** base harness | photo; the request only as audio | SAM 3.1, Muse Voice Transcribe, `count_boxes` (generated from catalog descriptions) | up to 8 turns and 12 tool calls |

Both arms: `muse-spark-1.3-contributor`, base harness ("Answer the spoken
request about the photo and return JSON.", empty guidance), reasoning effort
low, 4,096 output tokens, retries off. Scoring: region right **and** count
within the pre-registered tolerance (max(1, 5%) plus one per near-line dot).

## Gate and decision (pre-registered)

- A1 mean in **0.30-0.80** → prepare the S7 trial request.
- A1 above 0.80 → no headroom; raise difficulty offline (higher counts,
  harder classes, `count_boxes` off) with a fresh calibration set.
- A1 below 0.30 → inspect traces for a broken tool binding before changing
  anything.
- A0 is reported for context (does the tool route beat Spark's own eyes?);
  it does not gate.

## Limits and budget

- Call limits: **180 Spark** chat calls (20 A0 + up to 160 A1), **150 SAM**
  images, **25 Transcribe** clips. Perception results are cached by image,
  audio and noun phrase, so repeats are free.
- Scope `voicecount-calibration-v1`, cap **$1.00**. The cap covers the
  $0.1056768 reservation held by each in-flight Spark call plus the
  worst-case SAM bill (150 x $0.0025 = $0.375).
- Expected actual spend **about $0.15-0.30**, from the S5 probe rates: SAM
  ~$0.10-0.20 (2-4 distinct noun phrases per item), Spark ~$0.03 (about 100
  calls at ~2-3k tokens), Transcribe ~$0.003 (20 clips, about 60 s).
- Error policy: a model or transport error scores the item 0; three errors
  stop the run (partial results and usage are still written).

## Guards

Exact dataset approval hash; experiment hash; existing shared ledger at or
after the S5 checkpoint ($0.070598338, 257 settled); no earlier rows in the
scope; exclusive `started.json` marker (no repeats without a new reviewed
retry); fixed endpoints and models; key loaded at runtime only; error bodies
withheld.

## Command (after approval)

`wsl -e bash -lc 'cd /mnt/c/Shrey_Projs/hackathons/main_LX && PYTHONPATH=src .venv/bin/python -m lx.voicecount_calibration --approve-dataset b306c42f11c867c6746ba9202dcd4208884610d836edcff5a1322460e4fa2146'`

## Outputs

`main_LX/.lx/voicecount-calibration-v1/calibration.json` (per-item answers,
scores, tools used, stops; per-arm means overall and by region; decision)
and `usage.json`; A1 traces under `A1/runs/executions/`. Evidence committed
after the run.
