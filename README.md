# LX harness product

Both first product gates passed. The images + supplied-transcripts verifier is
implemented. The synthetic-only live Spark spike edited, harvested and selected
a correct candidate through the actual engine in Docker: 35 calls, $0.008337338
computed token cost, then zero-call/zero-execution resume. This establishes the
small JSON integration, not broader multimodal capability. The local app uses
the user-approved React + Python/SQLite architecture. The
application defaults to a **controlled preview**: its discussion/worker/improver responses
are predetermined and never load credentials or spend credits. Application
live flow (O-11) was approved 28 Oct 2026 and executed: a spec saved with mode `live` ran real Spark calls after spec/data/budget/Contributor approval under the shared $20 ledger (the app now uses the workspace budget described below; that ledger stays for experiments). First live result was honestly negative (no pixel-accurate boxes; base retained). See `evidence/2026-10-28-live-app/live-report.json`.

## Get the code (two repos, side by side)

This app depends on the sibling core repository; clone both under these
exact folder names so the relative dependency path resolves:

```bash
git clone https://github.com/shrey160/lx-harness.git main_LX
git clone https://github.com/shrey160/harness2-portable-core.git harness2_portable_core
```

Third-party disclosure: the core repo vendors a pinned copy of the
harness2-cli engine (Apache-2.0; provenance in its `UPSTREAM.md`). That
engine is pre-existing third-party code, not part of this project's own
implementation.

## Open the app

Inside WSL, from this repository:

```bash
uv sync --locked --python 3.12
```

Build the React assets from Windows or WSL:

```bash
npm --prefix web ci
npm --prefix web run build
```

Then inside WSL:

```bash
.venv/bin/python -m lx.server --port 8765
```

Or from PowerShell after setup: `./scripts/serve.ps1`.
Open [the local workspace](http://127.0.0.1:8765). The service binds to loopback,
uses a per-session change token and rejects foreign origins. Close the serving
terminal to stop it. No hosting/account or public service is configured.

Every project is a workspace: a folder you choose. LX keeps its files in `.lx/`
inside it (`project.json`, immutable `spec/vN.json`, `runs/<id>/`, `packages/<id>/`,
`snapshots/`, simulated usage) and adds a `.gitignore` so they stay out of your
repository. Samples stay in your folder, in `samples/` if that exists; a folder
holding code needs that subfolder, because dotfiles such as `.git` are refused
as samples. Approval freezes the samples as they are at that moment, and each
run uses that frozen copy. Later edits need a new approval to take effect.

`lx.engine` is the single service layer behind the app. Approval records stay
in user-level application state rather than `.lx/`, so writing to the folder
cannot approve a run, and any change to a spec file voids its approval. An agent
with full access to this machine is not stopped by this; the human confirmation
step comes with the CLI and MCP server.

A task is data plus a check file, not code. The spec's `task` holds the contract,
base prompt and loop wording; `.lx/checks/vN/check.py` scores answers, and the
spec records its hash, so editing the file stops the run. The first template is
`photo-request`: each sample is a folder `sample-*/` with `photos/1.jpg` ...
(3-7 photos), `request.wav` (or `request.txt` in a preview) and `truth.json`.
Inventory is the first task in it; a coding agent can supply another with its
own check. Truth reaches only the check, never the worker or the improver.

A check is untrusted code, so it runs only in the script sandbox; without Docker
or the Linux sandbox it does not run at all. Its protocol is in `lx/checks.py`:
JSON in, a list of named pass/fail checks out, and the score is the fraction
passed. Approval re-runs a self-test on the frozen samples: a correct answer
must score 1.0, malformed answers must be graded without crashing and without
full marks, grading must repeat exactly, another sample's answer must not pass,
and errors must not reveal expected values. A live task also needs `request.wav`
in every sample, and SAM and Transcribe spend from the workspace budget.

A live run first shows who receives what, per role and tool. Accepting covers
that workspace until a new host would receive data. This replaces the old
"synthetic or public" confirmation. Previews send nothing out and need no
notice, and a task preview runs only on the synthetic demo samples.

Any chat provider with an OpenAI-compatible endpoint can fill any role
(orchestrator, improver, worker). Add it under Providers with its base URL,
model id, key, prices and context limit, then assign it in the spec. The spec
records each role's endpoint and prices, so changing an entry after approval
stops the run until it is approved again. Live runs need prices on every entry
so the budget can be enforced. Each call reserves its context limit at the input
price, so set that limit for paid models or a small budget has no room.

Run limits are yours to set, on the spec page under Run limits: agent turns and
tool calls per photo, improver turns and session time, time per model call and
per run, reply length, and call counts per run. Each has a default and a hard
outer bound (`lx.limits`). Spark keeps its original numbers and other providers
get longer ones; a provider entry can set its own default for time per call and
reply length. A field tied to a role takes its default from that role's
provider; the others take the longest default among the run's providers. The
spec holds every value, so a change is a new version that needs approval. A run
keeps the limits it was approved with, so a raised limit reaches a new run, not
a resumed one. Sandbox rules, check and script run times, the self-test, human
approval, request size bounds and no-redirects are not settable.

A provider entry can also state requests per minute. Calls to it are then spaced
evenly within a run; two runs at once do not share the pacing. The rate is part
of what the spec binds, so changing it after approval needs a new approval.

Each workspace has its own budget and real-spend ledger, kept in user-level
state so they do not depend on files in the folder. The default is $5 and the
most is $20. Preview usage is simulated and stays in `.lx/`.

Keys go to the operating system's keychain (Windows Credential Manager on
Windows) and never enter a workspace. Keys saved earlier as files move there on
the next start. Where there is no keychain, as in WSL and headless Linux, keys
use native local-share storage with owner-only files, and the service refuses
key writes on filesystems that cannot enforce private POSIX permissions. On
Windows, a key is refused rather than written to a file if the Credential
Manager is unavailable.

The list of open workspaces, the model registry and approvals use the user's
native local-state directory.

The workflow supports model create/update/delete metadata and write-only key
replacement, folder/ZIP intake and inventory, metadata-first discussion,
versioned spec editing/approval, one managed run at a time, role/phase/candidate
usage, stop/resume, package download and fresh constructed-scene reuse. A changed
spec clears approval; active runs must stop before editing. The worker rechecks
approval and frozen input hashes before any request.

This first optimization template accepts the constructed demo dataset or an
identical imported snapshot. Other supported public/synthetic files can be
inventoried and discussed; they are not silently treated as independently
verifiable tasks. The app offers the demo as `data.zip` for testing both paths.
Known-negative candidates retain the starting harness and show failed checks.

See [application live review](docs/application-live-review.md) for the
approved O-11 flow. The rejected generic application transport was not added.

## Offline setup and checks

PROD-002 adds a text-only synthetic order-normalization domain, strict graded
field checks and a five-trial offline runner. See [records contract](docs/records-contract.md).
The truth-blind no-op preview checks the plumbing and records zero gains; it is
not capability evidence. The approved [live base-only calibration](docs/records-calibration-review.md)
scored 100% on all 40 held-out records ($0.004790572). The task has no measured
headroom, so five live trials remain disabled pending task redesign.

Use Python 3.12 or 3.13 inside WSL/Linux with the existing Docker setup.
Keep this repository beside `harness2_portable_core/`.

```bash
uv sync --locked
.venv/bin/python scripts/validate_product.py --evidence evidence/local-validation
.venv/bin/python -m lx.spike --core-source ../harness2_portable_core --output .lx/controlled-spike
```

Natively on Windows (D-016), use a separate uv-managed environment so the WSL
`.venv` stays untouched. Helper scripts run in a Docker sandbox there
(`lx-sandbox:pillow-11.3.0`: no network, read-only root, no capabilities,
unprivileged user). Build it once with Docker Desktop running; it uses the
digest-pinned base image and the Pillow wheel pinned in `uv.lock`. Without
Docker or the image, scripts are turned off: the agent gets no `run_script` and
the improver keeps procedures in skills.

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"; uv sync --locked --managed-python --python 3.12
.venv-win\Scripts\python.exe scripts\build_sandbox_image.py
.venv-win\Scripts\python.exe -m unittest discover -s tests
```

Repeat the last command with the same output directory: completed task work is
reused without fresh provider-double calls. These calls simulate token usage;
they do not spend credits. The test suite separately verifies the proposer
session cache and harvested workspace, not only whole-task skipping.

The core is a declared local library dependency; the engine remains its pinned
vendored dependency. `uv.lock` uses relative paths. No core source changes are
required beyond the recorded Windows port patches (`UPSTREAM.md` in the core).
Tests use the existing synthetic core fixture from the sibling source
repository. The user confirmed library consumption for S3 on 28 October 2026.

## Implemented contracts

- [Synthetic dataset and output verifier](docs/demo-contract.md): four PNGs,
  scene-separated development/held-out splits, independent truth, exact text
  and object matching with one-pixel box-edge tolerance.
- `lx.budget`: SQLite reservations under one persistent cap (at most $20),
  atomic concurrent admission, role/phase/candidate records, nanodollar
  accounting, and retained reservations for unknown usage. A reported overrun
  is recorded and blocks new spending. New live jobs must share one ledger;
  creating another database is not a mechanism for resetting the pilot cap.
- `lx.spark`: injected transport, normalized Meta function calls, complete
  assistant/tool history, protocol-owned 429 retries, usage validation,
  pre-call reservation of the entire context-window cost ceiling, cancellation
  and time bounds. A synchronous in-flight transport may run until its timeout;
  a late cancelled response is accounted for and never returned for use.
- `lx.proposer`: preserves upstream `Proposer.call` audit/cache/harvest and
  forces one upstream transport attempt. Tools read allowed session-context
  text files and write only inside `harness/`. Unexpected context filenames
  and links are rejected before upstream fingerprinting/copying.

Contributor prices were verified on 28 October 2026:
[pricing](https://dev.meta.ai/docs/pricing-rate-limits),
[chat protocol](https://dev.meta.ai/docs/protocols/chat-completions),
[tool protocol](https://dev.meta.ai/docs/tool-calling).
Input $0.10/M, cached input $0.002/M, output $0.20/M. Computed list-price
cost is distinct from provider billing. The old pilot's recorded $0.001 is
retained as conservative prior spend; its notes used Standard-mode prices.

App evidence: [validation](evidence/2026-10-28-app-preview/validation.json),
[core regression](evidence/2026-10-28-app-preview/core-regression.json),
[browser result](evidence/2026-10-28-app-preview/ui-results.png).
The preview uses a separate, explicitly simulated usage ledger; the actual live
pilot ledger is unchanged. Package exports include spec version, instructions,
check results, simulated usage/cost and limitations; keys/secret references and
machine paths are excluded.

## Live S1 evidence

See [the approved payload](docs/live-spike-review.md) and
[live result](evidence/2026-10-28-live-gate/live-report.json).
The explicit user approval superseded the earlier automatic-review hold for
this fixed synthetic experiment. `python -m lx.live` accepts no custom input
path, loads the existing key only at runtime, uses the fixed Meta endpoint,
requires the approved fixture checksum, and enforces 40 shared calls/$20 cap.
Generated code runs in Docker under the core's managed job lifecycle. The
transport refuses redirects and withholds provider error bodies. Candidate
input copies and selected Docker artifacts are checked. No managed containers
remained after the run. The adjacent offline suite passed 36 tests.
