# Three-sample Spark experiment: 0f64c549d33c

Completed 3 November 2026, **3/3 samples in819.34 seconds** (13m39s).
Muse Spark1.3 Contributor worker/improver/judge, Muse Voice Transcribe and
SAM3.1, fresh inventory-update-three workspace. Dataset built in **560cbd2**
from the user's four photos plus the original four composite photos.

**Baseline and final answers each pass24/24 objective checks. All30 model
judge criteria pass** on the six baseline/final answers. One intermediate
candidate fails ending cash arithmetic: overall **95/96 objective checks**
across12 executions. The loop drops that candidate; the exported package is
the unchanged base harness. Scores are reports, not selection inputs.

## Dataset and spoken requests

| Source sample | Photos | Intended transaction | Final stock / cash |
|---|---:|---|---|
| sample-01 |3; user31132 added | Buy7 cups | Cup11 /32.50 USD |
| sample-02 |3; user31133 added | Sell5 bottles | Bottle5 /41.25 USD |
| sample-03 |2; user31130/31131 | Cancel pencil restock; sell2 forks | Fork4 /44.50 USD |

Each sample has actual starting input.json with quantities, unit prices and
cash. New scissors/chocolate keys and the third sample's pencils, glue,
earphones and spoons remain unchanged. The third note is a designed harder
case: its initial pencil action is cancelled, the final sale targets forks,
and nearby spoons are explicitly excluded. The photos have several unrelated
kinds; no note gives an object count or mechanical JSON/tool instruction.
The model still passed this case at baseline; no measured difficulty claim
beyond this one run.

All three audio files were remade offline with Kokoro as conversational
synthetic speech (11.24/10.33/14.59s). Actual tool transcriptions are saved in
[summary.json](summary.json). They preserve the transaction meanings. The
third transcript repeats "photo" and "cash" around pauses but retains the
cancellation and final fork sale. Independent listening was not performed;
the reviewer HTML/audio controls let the user assess voice naturalness.

User images were rotated upright, resized to768x1024, stripped of metadata,
manually annotated and visually checked, with no crop/content synthesis.
Original photographs, previous10 sample files and earlier runs are preserved.
The other four photos remain1024x768. Controller-only photo_sizes allows
portrait/photo3 bounds, with legacy truth compatibility. Starting inventory
data matches the frozen JSON in **all12 openings**, with correct3/3/2 photos.
No answer scripts or truth are supplied as worker user input.

## All executions

| Source sample | Base | General candidate | Domain candidate | Combined/final |
|---|---:|---:|---:|---:|
| sample-01 cups |8/8 |8/8 |8/8 |8/8 |
| sample-02 bottles |8/8 |8/8 |8/8 |8/8 |
| sample-03 corrected fork sale |8/8 |8/8 |**7/8** |8/8 |

The fork domain candidate loaded its skill and correctly identified fork,
change-2, ending quantity4, cash_change+4.50 and two matching boxes. It wrote
**account.balance44.00**, which contradicts starting40.00 plus4.50. All other
seven objective checks pass. The skill's cash formula itself is correct;
the arithmetic slip in its rollout does not establish that the skill caused it.

Compose explicitly cites the observed44.00/44.50 mismatch and drops the skill
and its trigger. The final base rerun returns44.50 correctly. Cup and bottle
compose stages likewise retain base because the added domain skills show no
demonstrated benefit. No helper script was executed in any rollout. General
proposals leave unchanged-base probes. Decision notes and candidate harnesses
are retained in the handoff rather than treating perfect final scores as the
whole experiment.

Every worker execution calls Transcribe and SAM. The harder sample locates
forks in photo2 and segments that photo; photo1's unrelated stationery need
not be segmented. All final evidence matches the annotated objects. There
are **no worker cutoff events, tool-handler errors or script failures**. Maximum
worker reply836 output tokens; no separate reasoning fields were returned by
this Spark provider. Full returned reasoning remains unclipped in the runtime.

Stream source order cups, bottles, forks. Internal mapping:
sample-01 ->source sample-02; sample-02 ->source sample-03;
sample-03 ->source sample-01. The final package comes from internal sample-02,
the last source/fork sample. It is base; no learned procedure was manually
edited into it. All three inputs belong to the learning stream, not a held-out
test. One experiment does not establish repeatable gain or harm.

## Consent, cost and handoff

Specv3: worker32768-token allowance, paid improver/judge8192,1800s calls,
four-hour run; **$1 loop plus separate$0.25 judge**. Same five judge criteria,
concise improver hint and worker identity note. User explicitly requested the
new Spark/Transcribe run with a harder sample; that human chat authorization
was recorded with unchanged disclosed caps. No autonomous person consent or
GUI click claimed. Windows denied one snapshot publish before live job
creation; retry succeeded. Job created once/launched once through a hidden
native worker. No extra run or retry of a live execution.

Recorded **$0.067979478**: improver0.038171182, worker0.009615658,
perception0.019316, judge0.000876638. **176 settled calls**, no held/reserved/
unknown calls. Three paid transcriptions and seven paid segmentations serve
repeated executions through cache; four new judge calls, saved verdicts reused
where answers match. Provider billing not independently verified. Judges see
answers/truth, not trajectories or harness edits, and do not prove harness
benefit. Strict checks independently verify all twelve saved answers.

[summary.json](summary.json) contains scores, calls, transcripts and usage;
[final-answers.json](final-answers.json) contains all three updated inventories.
[Dataset preparation evidence](../2026-11-03-three-inventory-samples/README.md)
records63 negative cases,33 LX cases, seven malformed-dimension refusals,
eight legacy-answer comparisons and image/audio/ZIP/worker-input validation.

Local artifacts relative to repository:
`.lx/inventory-update-three/.lx/runs/0f64c549d33c/`.
Dataset review: `.lx/inventory-update-three/review.html` (photos/audio/answers).
Dataset export: `.lx/inventory-update-three/inventory-update-three.zip`.
One-off full experiment handoff:
`.lx/inventory-update-three/experiment-handoff-0f64c549d33c.zip`.
The latter includes frozen inputs/truth, all12 worker openings/traces/answers,
candidate harnesses/library experiences, decision notes, final package and
this review. Product-wide evidence packaging/UI remains pending under D-041.
