# mix-stream-colourcount-01

- adopted: `c1`  (run `lx_inventory-oc-seq1/mix-stream-colourcount-01/c1`)
- components: 1 skills; 139 always-on chars

## Why this was chosen
trivial pool: single final at k=1; delivered without a selector session

## What each edit did
component: skills/inventory/SKILL.md
candidates: c1, inv1
change: added new skill (absent in base): transcribe-first classification, single ordered view pass, segment with most-specific concept with colour-qualified first for colour_count (e.g. `red vase`), one bounded retry then stop, strict request/photos/answer shape plus reconcile. inv1 form adds rewording plus a run_script verify step and Script helper section; c1 form keeps reconcile as mental check.
observed: c1 rollout and inv1 rollout both segmented `red vase` in first segment round and got 0/1/1 at once (photo1 0, photo2 1, photo3 1); base1 rollout segmented bare `vase` first and got 1/0/0 (photo1 white-vase box) then had to re-view photo1, re-view photos2-3, and re-segment `red vase` to reach the same 0/1/1. Same final JSON in all cases; divergence is tool-call path at the first segment block.
verdict: confounded
component: skills/photo-inventory/SKILL.md
candidates: dom1
change: added renamed skill `photo-inventory` in place of `inventory` with opposite segmentation rule: segment bare kind first then filter by colour with view, explicitly distrust colour-qualified segment alone, plus classify/compare/reconcile sections.
observed: dom1 rollout reproduced the base1 waste: first segments `vase` 1/0/0, then extra crop views and 4 additional `red vase` segments plus repeated full-photo views before reaching the same 0/1/1 and identical final JSON as c1/inv1. Compared to c1/inv1 colour-first forms on the same transcript and photos, it used more segment and view calls for the same answer.
verdict: confounded
component: guardrails.md
candidates: c1, dom1, inv1
change: base guardrails empty; three trigger forms tried: c1 `When answering a spoken request about photos, load the inventory skill.`; dom1 same sentence with `photo-inventory`; inv1 `Skills: when counting items across photos, load the inventory skill. It carries its helper scripts/check_inventory.py...`
observed: base1 never calls load_skill; c1, dom1, inv1 each call load_skill once for their named skill and then follow it. No rollout isolates the trigger sentence from the skill content it loads; c1 vs inv1 vs dom1 differences in path track the loaded skill content, not the sentence wording.
verdict: confounded
component: systemprompt.md
candidates: inv1
change: appended `When counting items across photos, load the inventory skill.` to the base prompt `Answer the spoken request about the photos and return JSON.`; c1 and dom1 left systemprompt at base text.
observed: inv1 loaded `inventory` once like c1 and showed the same transcribe-then-view-then-colour-qualified-segment path as c1; no second load_skill and no prompt-driven behaviour change visible versus c1 rollout.
verdict: no observed effect
component: scripts/check_inventory.py
candidates: inv1
change: added executable verifier checking exact top keys, request-type/kind/colour rules, photo numbering, item keys, and answer-equals-per-photo-sum / per-kind totals, invoked via run_script with `{"draft": {...}}` on stdin.
observed: only inv1 rollout calls run_script, 3 times after assembling the correct draft, and all 3 return `ok:false / draft must be an object` (two with nested `draft`, one without); agent then abandons verification and delivers the same correct JSON as c1 with no fix from the script. c1 rollout with identical perception but no script makes zero run_script calls and delivers the same answer.
verdict: hurt

## Transferable lesson
- **cue**: when a harness pairs a procedural skill with a new executable verifier script the agent must call through a wrapper tool
- **lesson**: in scripts/<name>.py and its skill step, specify and test the exact stdin envelope as the run_script tool delivers it and show one literal call shape, so a contract mismatch cannot burn calls without ever verifying
- **evidence**: scripts/check_inventory.py row (inv1-only): 3 failed run_script calls with `draft must be an object` versus c1 with no script and zero such calls, same final answer; c1/inv1 vs dom1/base colour-first vs bare-first path contrast stays confounded and is not the basis
- **confidence**: medium, and why: the waste is directly isolated to the script component by the c1-vs-inv1 contrast on the same task, but it is still one spoken-request task with one verifier contract
