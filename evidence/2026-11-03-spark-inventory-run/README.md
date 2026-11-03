# Spark inventory/cash experiment: 386bafba041f

Live run completed 3 November 2026 in **586.44 seconds**. Muse Spark 1.3
Contributor was worker, improver and judge. **All eight worker executions pass
all 64 objective checks**, independently rechecked from saved answers. All
20 judge criteria pass on the four baseline/final answers. Base and final means
are both **1.00**, with either the eight objective checks or all thirteen checks.

The experiment produced useful failure evidence despite already-correct
baseline answers: a generated helper failed six times, Spark recovered, and
the loop discarded the helper after inspecting the failed trajectory.

## Outcomes

| Source sample | Base | General candidate | Domain candidate | Combined/final |
|---|---:|---:|---:|---:|
| sample-01: buy 5 cups | 8/8 | 8/8 | 8/8 | 8/8 |
| sample-02: sell 3 bottles | 8/8 | 8/8, base unchanged | 8/8 | 8/8, base unchanged |

- Cups: change +5, inventory 4 -> 9, cash_change -12.50, cash 50 -> 37.50.
- Bottles: change -3, inventory 10 -> 7, cash_change +9.75, cash 25 -> 34.75.
- Every answer is a valid six-field JSON object, preserving unrelated stock
  and unit prices, with matching object evidence. Every execution uses
  Transcribe and SAM on both photos. No stopped/cutoff events or tool-handler
  errors. Maximum individual worker reply: 1,469 output tokens.

Source order was cups, then bottles. Internal IDs are shuffled: internal
sample-02 = source sample-01/cups; internal sample-01 = source sample-02/bottles.
The final package is **the unchanged base harness** from the last sample. Cup
combined retained a skill, but the bottle compose stage dropped its domain
skill: it observed identical correct answers with an additional load_skill call,
and found no demonstrated failure that justified the added instructions. This
is the loop's trajectory-based choice; controller scores did not select it.

## What failed, and what the loop did

Cup general candidate added scripts/compute_update.py. The script accepts an
input wrapper inconsistently: it unwraps inventory/account but reads item,
count and purchase from the outer JSON. LX passes agent fields under input
alongside controller-provided photos and segments. As a result, item is absent
where the script looks for it. The worker tried six payload variations; every
run_script result had exit_code 1 and `item not found in inventory keys`.

Spark then bypassed the helper and returned the correct answer itself. The
compose notes explicitly cite those six failures and drop the script. The cup
combined rollout succeeds without it. The later bottle stage reads the library
experience and avoids adding a script. This is direct evidence that the loop
used execution failures to revise a candidate, even though answer scores
remained perfect. It does not establish a repeatable performance gain.

Passing answers also do not validate every learned instruction. Both domain
skills include contradictory cash wording: negative for purchases/positive
for sales, but also "equal to change times unit price". Given the task's stock
sign convention, the formula needs **minus** change times unit price. Spark's
answers use the correct cash sign despite that text. The final base package
does not contain this contradictory skill. Do not promote the intermediate
skill on the strength of these passing outputs alone.

## Configuration, consent and comparability

Inventory and cash JSON, spec **v5**, run **386bafba041f**, code **61ca81b**.
Same two samples, exact task/check/judge criteria, SAM/Transcribe and input
interface as v4. All ten source files (four photos, two WAVs, two input JSONs
and two truths) match the prior frozen run byte for byte. All eight opening
inventory objects match their correct snapshots. Baseline starts from the
stock harness, without the previous run's wrong learned skill.

Changed worker to Spark; improver and judge stay Spark. Retained concise
improver guidance and the worker identity note, removing the obsolete claim
that the worker is small/local. Worker generation allowance is **32,768 tokens**:
the free-local-only uncapped setting cannot apply to paid Spark. Paid
improver/judge allowance remains 8,192. Calls 1,800 s, run 14,400 s; other limits
unchanged. No spending cap was increased: **$1 loop + $0.25 judge**, separate.

User explicitly replied **Approve and start** to the prepared v5/settings/caps
after LX returned pending under its model-judge person-review rule. Recorded
that explicit human chat approval, not a GUI click or autonomous agent consent.
Windows denied snapshot publishing twice before live job creation; retry
succeeded. Created the job once and launched it once as a hidden native worker.

The user also requested removal of the 1,500-character reasoning cutoff.
**61ca81b** preserves full returned reasoning in spec-driven worker traces and
history; 41 focused Windows tests pass, including exact preservation beyond
9,000 characters. This Spark provider returned no separate reasoning field in
these saved worker replies, so this live run does not itself demonstrate long
reasoning capture. Existing older clipped traces cannot be reconstructed.
See [the offline change evidence](../2026-11-03-full-reasoning/README.md).

This is one two-sample experiment. It shows Spark succeeded here, not a
general reliability guarantee or a causal comparison isolating model choice
from all configuration changes. Judges are the same model family and are
supplementary; strict saved-answer checks independently passed.

## Cost and handoff

Recorded **$0.045040716** total: improver $0.025304358, worker $0.007573758,
perception $0.011932, judge $0.0002306. **118 settled calls**, no held, reserved
or unknown calls. Only one new paid judge call was needed; saved verdicts were
reused for identical answers. Two paid transcriptions and four paid SAM calls
served repeated executions through caching. Provider billing not independently
verified. No additional live model calls during review.

Evidence: [summary.json](summary.json), including all execution scores, tool
sequences, script failures, output lengths, usage and limits.

Local run artifacts, relative to the repository:
`.lx/inventory-update-json/.lx/runs/386bafba041f/`.
Baseline/probes/final traces are under outputs/runs/executions; learned
candidate harnesses and library experiences are under outputs/runs; improver
decisions are outputs/proposer_audit/*.response.md. The standard LX package
contains the final harness, check/judges, spec, scores, usage and limitations;
it does not yet bundle all intermediate trajectories/library evidence.

A separate portable review ZIP is prepared locally at
`.lx/inventory-update-json/spark-experiment-handoff-386bafba041f.zip`, with the
frozen samples, every worker opening/trace/answer, candidate harnesses, library
experiences, improver decision notes, final package and this review. This is a
one-off experiment handoff; broader product packaging/UI work remains pending
under the newly accepted evidence-platform direction (D-041). No product loop
or scoring policy was changed to force a gain or retain a particular candidate.
