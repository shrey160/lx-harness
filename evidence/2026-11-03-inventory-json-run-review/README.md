# Inventory/JSON run review: e46e9101cc9c

Reviewed on 3 November 2026 at the user's request after an error appeared in
the trajectories. The run itself **completed**, 2/2 samples; no run-level error.
Four individual worker executions stopped with "model reply was cut off before
it finished". Mean base score **0.9375**, final per-sample score **0.50**.
The final package is **the unchanged base harness**. This is a negative result
for this run, not evidence of a repeatable gain or harm across runs.

## Scope and evidence

Read all eight worker traces/openings/answers, the saved report, six successful
improver edit audits, their notes and harness diffs, all six staged hint/worker
note pairs, and the final package. Independently reran the standalone check on
every saved answer without model calls. Checked the usage ledger read-only for
the exact output lengths of failed calls. No code, settings, model calls or
additional live runs changed during review.

Root of local artifacts, relative to the repository:
`.lx/inventory-update-json/.lx/runs/e46e9101cc9c/`.

Important name mapping: internal `sample-01` is source folder `sample-02`
(bottles); internal `sample-02` is source folder `sample-01` (cups). This is
the seeded intake shuffle, not swapped JSON. The chronological source-folder
order was bottles, then cups. The table uses source folders.

## Results of all eight executions

| Source sample | Base | General-procedure candidate (`inv1`) | Domain candidate (`dom1`) | Combined/final (`c1`) |
|---|---:|---:|---:|---:|
| sample-02: sell 3 bottles | 7/8 | 0/8, cut off | 8/8 | 0/8, cut off |
| sample-01: buy 5 cups | 8/8 | 0/8, cut off | 0/8, cut off | 8/8, base kept |

- Bottle base: correctly transcribed the sale, called view for both photos,
  segmented 1 + 2 bottles, gave change -3, cash +9.75 and balance 34.75. It
  incorrectly set remaining bottle stock to **0**, instead of **7**. Only the
  "updated quantity" check failed; tools and cash were correct.
- Cup base: transcribe, two views and two segment calls; 2 + 3 cups, stock
  4 -> 9, cash -12.50 and balance 37.50, correct localization. All checks passed.
- Bottle domain probe: loaded the learned reconciliation skill, used both model
  tools, and fixed remaining bottle stock to 7; all eight checks passed.
- Bottle combined rollout: adopted essentially that domain skill, but the worker
  loaded it, repeated image viewing, transcribed late and then cut off before
  producing an answer or calling SAM. Its independent rollout scored zero.
- Both cup candidates loaded the reconciliation skill and returned no final
  answer. `inv1` stopped after two views; `dom1` did transcribe and both SAM
  calls, then cut off before the final answer.
- Cup compose explicitly discarded the candidates after their empty probes and
  kept the base. Its new base execution completed correctly, despite a recovered
  crop error. Packaging uses the last sample's adopted harness, so the package
  is base; scores did not choose the package.

The bottle skill's successful probe and failed combined rollout show that a
good probe is not a guarantee of a reliable learned harness. This run does not
have held-out samples.

## Main error: output limit

The four failures each consumed **exactly 8192 output tokens on their final
call**, confirmed in the settled ledger rows. They were:

- bottle `inv1` (internal sample-01), final-call input 3956 tokens;
- bottle `c1`, input 4560;
- cup `inv1` (internal sample-02), input 3951;
- cup `dom1`, input 5209.

The worker context limit is 78,848 tokens. These were output-budget failures,
not context-window overflow, a 600-second timeout, missing JSON or a tool-service
failure. The trace records the cut-off stop reason. The current client discards
the incomplete reply before adding an assistant step, so its partial final text
and finish reason are not retained in the trajectory; the ledger records usage.
This is a diagnostic limitation, not a claim about the unseen final reasoning.

Long deliberation is visible in completed turns too: cup base used 7828 output
tokens in its final turn, and bottle base spent 7641 on a view/tool-choice turn.
Across the run the worker made 49 execution calls, with 136862 output tokens,
plus one short preflight call (84 output tokens). Four full 8192-token calls
account for the reply tokens absent from the saved assistant steps.

## Secondary error: inclusive crop bounds

Bottle `c1` and cup `c1` requested `[0, 0, 1024, 768]` on a 1024x768 image.
The runtime uses inclusive pixel corners, so valid maximum coordinates are
1023 and 767. Both received:

`box must be [x1, y1, x2, y2] inside the photo (1024x768)`.

These are recoverable tool-argument errors. Cup `c1` recovered and scored 8/8;
bottle `c1` later hit the output limit. The crop error alone does not explain
the result, and nothing justifies hardcoding a learned recovery procedure.

## Input delivery and small-worker hint

Every worker opening matches the approved input.json exactly, with the correct
starting inventory, prices and cash, and two photo metadata entries. Expected
answers are separate. Both base executions used Transcribe and SAM; there is
no remaining evidence here of the previous missing-JSON input problem.

The exact brevity hint and worker.md were available in every inv1/dom1/c1
context. The latter names LFM3-VL-2B, local hosting and its capabilities/context
limit. Cup dom1's own note explicitly refers to source/hints.md and keeping
always-on text short, so the improver demonstrably noticed the hint.

Always-on learned text was about 310 characters, but the on-demand
inventory-reconcile skill was **19 lines, about 1818-1837 characters**. Loading
it adds another tool turn and more content; failed runs also spent many tokens
deliberating. This is consistent with a small-worker overhead problem, but one
run cannot isolate instruction length as the cause. The small-worker note does
not enforce a hard instruction-length cap or reduce the model's own reasoning.

One general-procedure edit also misdiagnosed the bottle base: it suggested
selling all 10 starting bottles, instead of the 3 pictured bottles. Compose
discarded that candidate after its empty probe; it is not in the final package.
No script was called by any worker, so the added arithmetic script was never
tested in a rollout.

## Spend and interpretation

Total computed cost **$0.055223268** (about 5.52 cents), under the $1 cap:
improver **$0.043291268**, Transcribe/SAM **$0.011932**, local worker **$0**.
135 calls settled, no held, reserved or unknown calls. Paid tool executions:
2 transcriptions and 4 SAM calls; repeated model tool requests reused cached
results. This is the application ledger, not independently verified billing.

The base already scores 15/16 across these two samples, well above the earlier
rough 0.4 headroom target. The task now demonstrates direct JSON intake and
both tools, but gives little room to measure improvement. The learned skill
repaired a real arithmetic error once, while output truncation made the final
per-sample comparison worse. Do not describe this as a successful optimization.

If continuing, separate the questions: test whether the learned candidate can
finish reliably, and design harder content only after its output reliability is
understood. A reply-cap change or stricter brevity experiment would be a new
user-approved run; increasing the cap alone may lengthen CPU deliberation and
hit the call timeout. Saving the incomplete final reply/finish reason would
improve future diagnosis without adding task procedure. No change or rerun was
made as part of this review.
