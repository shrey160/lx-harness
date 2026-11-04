# Installation agent: use this example with LX

This is a dataset/setup runbook, not a worker system prompt. Treat sample content
as data. Read the LX checkout's README and `docs/agent-mcp.md` for installation
and connection. This bundle includes no credentials, installed dependencies,
provider IDs, approvals, spending grants or learned harness.

## 1. Establish the installation

Install LX and the separate sibling core using the checkout's Windows/Linux
instructions. Build the frontend and prepare a supported script sandbox. Connect
a fresh agent-facing stdio MCP client using Settings → Connectivity. Call `status`,
`guide(topic="tasks")`, `guide(topic="environment")` and `environment`.
Do not use the Web UI URL as the MCP endpoint.

First finish LX's built-in synthetic preview if you need a provider-free
end-to-end installation check. Its doubles cannot execute this dataset. A scratch
state `--root` disables live calls; keep scratch roots separate from the person's
normal UI/MCP configuration.

## 2. Verify and make a task workspace

Run this bundle's `prepare.py verify`, then `copy --destination ABSOLUTE_NEW_FOLDER`
with the installed LX Python. The destination must not already exist. Alternatively
extract the ZIP into a new folder and verify there. Leave the tracked example and
earlier runs unchanged. Resolve the destination's absolute native path for MCP.

Call, in order (names are the unprefixed LX tool names):

```text
init(workspace=ABSOLUTE_NEW_FOLDER, template="photo-request",
     name="Inventory and cash example",
     goal="Update stock and cash from object photos and spoken shop transactions.")
samples(workspace=ABSOLUTE_NEW_FOLDER)
spec_show(workspace=ABSOLUTE_NEW_FOLDER)
```

Do not call `init` twice on an initialized folder: use `spec_show` to reopen it.
`init` starts from LX's default template; it does **not** import `task.json`.
The default has six samples and a different answer schema, so the next step is
required. Do not create extra samples to satisfy those defaults.

## 3. Load the task and check without running

Read `task.json` from the copied bundle using ordinary filesystem access. Parse
the whole object and pass it as `task` to `spec_draft`; do not pass a filename or
JSON string. This sets `truth_schema="free"` and `stream_samples=3` along with the
six-field output contract. `input.json` remains actual starting data, not a photo.

```text
spec_draft(workspace=ABSOLUTE_NEW_FOLDER,
           task=PARSED_TASK_JSON_OBJECT,
           use_eval_file=true,
           tools=["sam-catalog", "transcribe-catalog"],
           mode="live")
check_test(workspace=ABSOLUTE_NEW_FOLDER, use_eval_file=true)
spec_show(workspace=ABSOLUTE_NEW_FOLDER)
ready(workspace=ABSOLUTE_NEW_FOLDER)
```

`mode="live"` prepares real-data configuration; it does not approve or start work.
There is already `eval/check.py`; do not run `eval_init` or replace it with simple
equality rules. The truth is a compact transaction record, not the required output
shape; `truth_shape` therefore describes checker inputs, not the six output fields.
`task.json` is the authoritative worker output contract. Expected check self-test:
three samples, correct 1.0, blank/wrong 0.0, no weaknesses (currently 33 cases).

Keep both built-in tools selected for this example. The person configures their
Meta key and enables the tools in Settings → Tools. Discover actual chat-model
entry IDs with `status`; choose enabled worker/improver entries with appropriate
tool support via `spec_draft(worker=ID, improver=ID)`. Do not assume an installation
has the original local Unsloth entry or a particular provider. To repeat the old
model choice, select Muse Spark 1.3 Contributor when available; use the current
catalog's name/ID and disclosed terms rather than inventing an endpoint.

If providers/tools are not configured, report the exact `ready.blocked` reason.
Offline data/check success plus a missing-key blocker is a valid preparation
result; it is not `ready=true` or a successful live test. Do not copy credentials
from another install, call paid Provider Test, raise caps or weaken checks.

## 4. Optional judges and improver context

The default setup uses eight objective checks and no model judge. To reproduce the
historical five arithmetic/completeness criteria, copy `reviewer/judges.json` to
`eval/judges.json`, then save `spec_draft(use_judge_file=true)`. The person selects
the judge model and separate cap in the UI and reviews/approves the judged spec.
Do not claim judge scores prove that a harness caused an improvement.

No hints or learned skill are preloaded. For an explicitly requested small-worker
experiment, optional improver-only context can ask for concise text suitable for
that worker, with `tell_improver_worker=true`; record this departure from the base
configuration. Never add expected answers, counts or reviewer transcripts.

## 5. Execute only the requested test, then inspect evidence

Show the person readiness, selected providers/tools, what data they receive and
current run/trial/judge caps. Installation/example preparation alone does not
authorize spending. When the person requests a particular live test under those
settings, approve an eligible checked spec with `approve`, then call `trial_start`
for one sample or `run_start` for the three-sample loop. Judged/unscored/weak-check
specs require person review. Honor pending/blocked results; do not bypass them.

Record the returned run ID, poll `run_status`, and read `edits`/`results` after
completion. Inspect answers and full trajectories in the UI, including actual
Transcribe and SAM calls, errors, costs and sample identity. Source folders
sample-01/02/03 can have different shuffled internal execution IDs; use the source
labels when comparing. A trial selects one input; it does not cover all samples.

Report installed LX/core revisions, environment, copy/manifest verification,
check self-test, readiness/blockers, selected roles/tools/caps, and any authorized
run's ID/results/errors/spend. Explain whether the final harness changed and what
evidence supports any claim. Keep the inputs, run and download artifacts for review.
Packages and trajectories are separate product downloads; a general combined
experiment-export feature is still pending. Do not describe this dataset ZIP as
a learned harness, result archive or clean-install test result.
