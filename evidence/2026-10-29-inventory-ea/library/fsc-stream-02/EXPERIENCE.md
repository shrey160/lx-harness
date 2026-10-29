# fsc-stream-02

- adopted: `c1`  (run `lx_inventory-oc-seq1/fsc-stream-02/c1`)
- components: 1 skills; 139 always-on chars

## Why this was chosen
trivial pool: single final at k=1; delivered without a selector session

## What each edit did
component: guardrails.md
candidates: c1, inv1, dom1
change: Added one trigger line naming the inventory skill ("When answering a spoken request about photos, load the `inventory` skill."). Identical wording in all three candidates; base had no trigger.
observed: Base rollout base1.md never called load_skill and, after identical transcribe plus view x3 plus segment x3 (11/53/14), entered an unbounded verify loop (count_boxes, re-views of photos 1/2/3, zoom crops, one out-of-bounds error) and hit [STOPPED] no answer within 13 turns with empty deliverable. All three skill candidates each called load_skill once and then delivered JSON. No rollout isolates trigger from skill content.
verdict: confounded
component: skills/inventory/SKILL.md
candidates: c1, inv1, dom1
change: Added new on-demand skill in three forms. c1 form: transcribe-first classify, view-once, segment-once with most-specific concept, view-controls-over-segment with explicit reflections/mirror-images/furniture exclusion, one bounded recheck then trust eye count and never loop, exact request/photos/answer contract plus reconcile check. inv1 form: same skeleton but narrowed exclusion to furniture/surfaces/walls only, view as authority on colour only, fallback only if segment returns 0. dom1 form: same reflection exclusion as c1 but restructured into ANALYTIC MOVE / ARTIFACT CONTRACT / DECISION RULE with reworded description.
observed: c1 rollout finished immediately after view x3 plus segment x3 with 11/36/14 answer 61 with no re-views. inv1 rollout needed extra steps after the same segment result — re-view photo 1, count_boxes x3, re-view photo 2, count_boxes with min_confidence 0.8 to filter 53 to 36 — then finished with identical 11/36/14 answer 61. dom1 rollout did re-view photo 1 + photo 2 then photo 3 and finished with 11/36/12 answer 59 (photo 3 differs by 2). Base with no skill failed as above. Form-level differences each rest on a single rollout.
verdict: confounded

## Transferable lesson
- **cue**: When an agent must reconcile a noisy detector against direct observation and risks unbounded re-viewing/re-counting instead of finishing
- **lesson**: Add the procedure as an on-demand skills/<name>/SKILL.md with an explicit view-controls-over-tool-boxes rule plus one-bounded-recheck-then-trust-eye-count-and-reconcile stop rule, and mention it by name in guardrails.md so it is actually loaded
- **evidence**: Base with neither component looped 8+ view/count_boxes calls and delivered nothing (rollouts/base1.md), while all three candidates carrying trigger plus bounded-skill variants each stopped and delivered JSON (c1 minimal finish 11/36/14; inv1 filtered finish 11/36/14; dom1 eye-count finish 11/36/12) — package contrast only, components never isolated
- **confidence**: medium, and why: contrast repeats across three skill variants versus one base failure on the same photos/transcript, but each form has a single rollout and trigger versus skill content remain confounded
