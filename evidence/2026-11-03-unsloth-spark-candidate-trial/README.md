# Unsloth trial of the saved Spark cup harness

Trial **bd85de129c17** completed on 3 November 2026 in **883.99 seconds**
(14 minutes44 seconds). **13/13 checks pass**: all eight objective checks,
independently rechecked, and all five Spark judge criteria.

User requested one local Unsloth trial and explicitly selected the **Spark cup
candidate** after clarification that Spark's final exported package was base.
Selected run386bafba041f, source sample-01, internal sample-02, adopted combined
harness c1. The candidate was not edited to repair its known cash wording.

## Outcome

LX's normal trial input was source **sample-02, sell three bottles**. Selecting
from_sample selects a harness, not an input. The cup-derived candidate was
therefore exercised on the bottle transaction.

- Starting bottles10, final bottles7, signed change-3.
- Unit price3.25, cash received9.75, cash25 ->34.75 USD.
- Cup/bowl quantities and all unit prices preserved.
- Three evidence boxes match the pictured bottles, one in photo1 and two in
  photo2. Final JSON is complete and valid.

See the exact [answer.json](answer.json) and [summary.json](summary.json).

## What actually ran

Local worker: **LiquidAI/LFM3-VL-2B-GGUF** through Unsloth Studio. Specv6,
worker_reply_tokens0 (no LX generation cap), full returned reasoning retained,
same task/check/five judge criteria/SAM/Transcribe. Trial cap0.25 USD plus the
existing separate judge cap0.25 USD; neither increased. No improver or further
trial. Human authorization is the explicit trial request plus subsequent
selection of the Spark cup candidate; it is recorded as chat consent, not a
GUI click or autonomous agent approval.

Copied and executed candidate fingerprint:
`fad0d047e65a9b89129e2737893667a36708384e3055ea4e5335695937ad47f7`.
It matches the original source candidate exactly. Opening inventory input
matches the correct frozen bottle JSON. Generic from_sample trial support was
built in **f46fdda**, verified by **53 trial/CLI +7 MCP tests** before use.
See [implementation evidence](../2026-11-03-sample-harness-trials/README.md).

Worker tool sequence:
`transcribe -> view(photo1) -> view(photo2) -> segment(bottle,photo1) -> segment(bottle,photo2)`.
All succeed, with no stopped/cutoff events, handler errors or script calls.

**The worker never called load_skill.** Its opening lists inventory-update,
and its copied guardrails say to load it, but the learned skill body was not
loaded. The changed always-on trigger was present; this observation is not a
test proving the learned procedure helped. It also does not validate or exercise
the contradictory cash formula in that unread skill. No paired baseline was
authorized or run. This is a successful one-sample smoke test, not a measured
harness improvement or general reliability result. The source Spark experiment
saw both input samples, as the trial result notes.

## Reasoning retention and usage

Six worker execution replies: **17,934 output tokens** in total, maximum6,748
in one reply. Returned reasoning lengths are **29,075;1,541;1,459;24,772;718;
15,919 characters**, total73,484. These full fields are saved in the trace,
providing live evidence that the former1,500-character cutoff is removed.
This does not establish that retaining reasoning caused success; this single
trial changes worker/harness configuration relative to previous experiments.

Recorded total **$0.0062169**: perception $0.005976, Spark judge including
preflight $0.0002409, local worker $0, improver $0. **12 settled calls**, no
held/reserved/unknown calls. Seven worker calls include one preflight; one
transcription, two SAM calls, judge preflight and one grading call. Provider
billing not independently verified. No extra model calls during review.

Local artifacts relative to the repository:
`.lx/inventory-update-json/.lx/runs/bd85de129c17/`.
The copied harness is in harness/; full opening, answer and trace are in
outputs/runs/executions/trial/. Outputs/judged.json holds verdict reasons.
No second trial or candidate edits were made after inspecting the outcome.
