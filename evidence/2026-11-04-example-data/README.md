# Reusable three-sample example — 4 November 2026

User requested an example-data section and a package that an agent installing LX
can understand and use. Added the versioned
[example catalog](../../examples/README.md), starting with
[inventory/cash](../../examples/inventory-cash-three/README.md) and its
[agent runbook](../../examples/inventory-cash-three/AGENT.md).

## Delivered

- The exact reviewed dataset from builder revision `560cbd2`: eight JPEGs, three
  conversational synthetic WAVs, three starting JSON objects and three controller
  truths. The 17 sample files plus task/check (19 files) match the old local
  dataset byte for byte. No originals or saved runs were rewritten.
- Task/check, optional judge criteria and reviewer voice scripts, source
  attribution/asset terms, Apache code license/notice, manifest and standalone
  verification/copy/ZIP helper. 29 package files, about 3.13 MB uncompressed.
- Instructions distinguish the `photo-request` input, required custom task import,
  controller truth versus six-field output, objective checks versus optional
  judges, offline verification versus preview versus real execution, provider
  discovery, person-controlled caps/approval and outcome inspection.
- `examples/.gitattributes` disables text conversion within this hashed bundle,
  preserving exact payload bytes across Git checkouts. The helper exports only
  listed files, excluding any later workspace/runtime/reviewer-cache additions.
- Links from the root README, docs index, installation and agent workflow.

The third sample retains the spoken correction: cancel the pencil restock and
sell the forks while leaving the spoons. Historical results are labeled as prior
observations, not a guarantee for a new install/model. No learned harness or old
approval is shipped. Media terms remain separate from the code license.

## Verification

- Offline verifier: all hashes; image decode/dimensions; WAV format, duration and
  completeness; direct input/truth consistency; six positive/reordered-evidence
  cases and **45 negative cases** pass. Zero provider calls.
- **Five package tests pass**, covering independent fresh copies, paths with spaces,
  existing-destination refusal, ZIP re-extraction/verification, exclusion of
  unlisted/runtime files, corrupt-input refusal and unsafe manifest paths.
- **Fresh SDK stdio MCP connection** using the existing Windows Python installation
  and an isolated scratch state: documented init → task/check draft → check_test →
  ready works. Docker executes the real checker; **33 cases, 3 samples, 8 checks
  each, blank/wrong scores 0, no weaknesses**. Direct input JSON matches; truth
  reads are refused. No hint/eval drift. `ready=true`, no data/provider blockers
  reported on this already-configured host. This does not validate real provider
  credentials with requests; scratch-root live execution is disabled.
- No approvals, grants or runs; zero spend. Existing personal state is untouched.
  [validation.json](validation.json) stores the non-sensitive MCP/offline result.
  [validate.py](validate.py) repeats this preparation with a caller-provided scratch
  folder and optional JSON output; it does not execute a model experiment.
- A local standalone ZIP was produced, CRC checked, and re-extraction verified by
  the package tests. It is reproducible with the documented `pack` command, not a
  checked-in duplicate binary archive. [package.json](package.json) records hashes
  and sizes. All 84 local links across ten touched/current example guides resolve;
  Git whitespace and manifest-listed text encoding checks pass.

Initial restricted Windows test execution failed on temporary-directory permissions;
the five tests pass with an accessible temporary folder under normal local
execution. Two initial validation-helper errors (argument name collision and a
zero-spend string-format assertion) were corrected; final stdio validation passes.
Neither was a product/runtime failure.

Reproduce from `main_LX` using its Python environment:

```text
python examples/inventory-cash-three/prepare.py verify
python -m unittest discover -s tests -p test_example_data.py -v
python evidence/2026-11-04-example-data/validate.py --scratch .lx/example-data-qa
```

## Limits and next task

This uses the existing native Windows installation, Docker and installed MCP SDK.
It is not a fresh clone, clean OS, Linux execution, Web UI retest or an autonomous
external installation-agent trial. No new worker/improver/judge/Transcribe/SAM
provider call was made; their older run evidence remains separate. No full suite,
frontend rebuild, real workspace deletion/billing decision, core change or push.

Next remains the separately planned fresh-clone Windows/available-Linux installation
rehearsal and synthetic preview. The example can then be verified and drafted from
that fresh checkout; live example execution needs a separately requested run.
D-041 general experiment export/library UI remains separate pending product work.
