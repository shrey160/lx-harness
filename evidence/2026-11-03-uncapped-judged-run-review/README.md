# Uncapped judged run review: 5a2e37184043

Reviewed 3 November 2026 at the user's request. The run **completed 2/2** with
no run-level error. Removing the worker output cap prevented the previous
cutoffs, but did not produce reliable tool use, valid output or a safe learned
package. This review makes no runtime changes and starts no additional run.

## Evidence and naming

Inspected all eight saved worker openings, traces and answers; base/candidate
harnesses, improver notes and final package; four judge verdict sets; saved
report, health and settled usage. Independently reran the packaged eight-rule
check on every saved answer, parsed the malformed final text, and compared all
eight opening inventory inputs and both audio files with the frozen snapshot.
Machine-readable results: [summary.json](summary.json).

Local artifacts relative to the repository:
`.lx/inventory-update-json/.lx/runs/5a2e37184043/`.
Internal `sample-02` = source `sample-01` (buy cups); internal `sample-01` =
source `sample-02` (sell bottles). Source stream order: cups, then bottles.
The final package comes from bottles, the last sample; it is not selected by
score. Scores below use source folders.

## All eight executions

| Source sample | Base | General candidate (inv1) | Domain candidate (dom1) | Combined/final (c1) |
|---|---:|---:|---:|---:|
| sample-01: buy 5 cups | 8/8 | 0/8 | 6/8 | 0/8, base retained |
| sample-02: sell 3 bottles | 7/8 | 8/8, unchanged base | 8/8 | 8/8 |

The deterministic mean is **0.9375 -> 0.50**. With five additional model-judge
criteria per answer, the displayed mean is **0.9231 -> 0.6538** (24/26 -> 17/26).
The previous unjudged run had only eight checks; compare its deterministic
scores, not its total directly against this run's thirteen-check total.

- Cup base correctly counted five, updated stock 4 -> 9 and cash 50 -> 37.50.
  The general candidate is the user's quoted failure, described below.
  The domain candidate counted five correctly but **added** 12.50 to cash,
  producing 62.50 instead of 37.50. Compose discarded both candidates and
  reran the unchanged base. That rerun transcribed and viewed only photo 1,
  never used SAM or photo 2, counted two instead of five, and supplied one
  unsupported broad evidence box. It also emitted malformed JSON. Thus the
  cup regression cannot be attributed solely to learned instructions: even
  the identical base harness gave radically different executions.
- Bottle base found all three and computed cash correctly, but set ending
  stock to zero rather than seven. The general-stage harness was unchanged:
  proposed underscore-named skill directories failed component validation.
  Its successful rerun therefore also demonstrates execution variance.
  Domain and combined replies correctly returned stock seven and cash 34.75.
  However, the final learned skill contains an incorrect cash rule.

## The quoted missing-audio passage

It is in `outputs/runs/executions/lx_inventory_update-oc-seq1/sample-02/inv1/trace.json`:
the cup general candidate, not the final packaged bottle execution. The worker
made **zero tool calls**, including no load_skill or transcribe. Its final text
was `{"photo": 1, "box": [0, 0, 1024, 768]}`: view arguments emitted as an
answer rather than a function call. This is a model/tool-use failure, not an
observed missing-file or Transcribe provider failure.

Both WAVs exist, are 19.11/19.46 seconds long, and match the approved snapshot
byte for byte. Transcribe succeeds in seven of eight executions, including
other executions on the very same cup input. All eight opening inventory JSON
objects match their correct snapshot input.json.

The worker receives photo metadata and `spoken_request: "audio only; use
transcribe"`. Audio is held by the controller and bound to the zero-argument
transcribe tool; the worker does not need an audio attachment or filename.
See [inventory_workflow.py](../../src/lx/inventory_workflow.py), messages and
_conversation, and [agent.py](../../src/lx/agent.py), photo_tools/PhotoSetTools.
The placeholder under a request-like field plausibly confused the small
worker into treating it as the request itself. That interpretation is an
inference; the saved actions establish only that it never tried the tool.

The passage ends mid-word because `NOTE_CHARS = 1500` in agent.py and the trace
stores only the first 1,500 reasoning characters. This is separate from an
output-token cutoff. The same shortened note is also replayed as assistant
content on subsequent tool turns when ordinary assistant content is empty.
It is therefore more than a display limit. Tool results remain in history;
whether shortening reasoning causes these failures is untested. The quoted
execution had no subsequent tool turn, so replay cannot explain that specific
execution's failure.

## Wrong formula in the final package

`outputs/package/harness/skills/inventory-update/SKILL.md`, line 16, says:

`cash_change = change * unit_price`

Under the task's stock sign convention the correct relationship is
`cash_change = -change * unit_price`: purchases add stock and spend cash,
sales remove stock and receive cash. The final bottle answer happens to use
the correct cash sign despite the skill. A passing answer does not validate
the packaged instructions. Do not treat this package as a reliable inventory
and accounting procedure. No learned rule was manually patched in this review.

## Judge false positive and health-report limits

The cup combined answer is stored as a string because its raw final text is
invalid JSON: `Expecting ',' delimiter: line 11 column 55 (char 328)`. Its evidence
entry has mismatched closing brackets/braces. The objective check correctly
fails all eight criteria because the answer is not a valid six-field object.

The model judge nevertheless passes four of five criteria, including
**complete usable answer**, saying the fields have correct types and structure.
That is a concrete false positive. Its arithmetic reasons describe intended
numbers (4 + 2 = 6; 50 - 5 = 45), but do not establish a usable serialized answer
or the correct observed count. The quantity criterion correctly fails.
Judge verdicts alone cannot establish harness benefit; this judge saw answers
and controller truth, not trajectories or harness edits. The bottle stock
correction is real in this execution, but repeatability is not established.

`health.json` says answered 8/no_answer 0 and improver failed 0. Here “answered”
means text returned, not correct tool use or valid JSON. Those counters do not
contradict the two failed cup outputs or the rejected intermediate edits.

## Output limits and spending

No execution has a cutoff/stopped event. Replies of **9,280 and 14,014 output
tokens** succeeded, exceeding the former 8,192 cap. The uncapped setting worked;
remaining failures involve tool use, counting, arithmetic and serialization.

Recorded total **$0.04979184**: improver $0.03698164, perception tools $0.011932,
judge $0.0008782, local worker $0. There are **133 settled calls**, no held,
reserved or unknown calls. Only two paid transcriptions and four paid SAM calls
were needed because repeated inputs reuse perception results. Provider billing
was not independently verified. No new model calls were made for this review.

## Suggested next investigation, not implemented

Clarify generic media availability in the input interface rather than placing
an instruction placeholder in spoken_request. Review reasoning-history
shortening against the accepted loop contract. Make answer usability rely on
strict parsing/schema checks and review the judge's treatment of malformed
answers. Keep task-specific counting/accounting procedures learned by the loop;
do not hardcode this package's corrections into baseline guidance. Any causal
comparison needs repeated matched executions because identical harnesses vary.
