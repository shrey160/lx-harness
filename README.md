# LX harness product

Both first product gates passed. The images + supplied-transcripts verifier is
implemented. The synthetic-only live Spark spike edited, harvested and selected
a correct candidate through the actual engine in Docker: 35 calls, $0.008337338
computed token cost, then zero-call/zero-execution resume. This establishes the
small JSON integration, not broader multimodal capability. The local app uses
the user-approved React + Python/SQLite architecture. The
application is a **controlled preview**: its discussion/worker/improver responses
are predetermined and never load credentials or spend credits. Application
live activation is pending explicit approval of the reviewed data flow below.

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

Application runtime records use the WSL user's native local-state directory;
private credentials use native local-share storage with owner-only files.
The service refuses key writes on filesystems that cannot enforce private
POSIX permissions. Its secret directory is separate from samples and exports.

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

See [application live review](docs/application-live-review.md) for the specific
pending permission. The rejected generic application transport was not added.

## Offline setup and checks

Use Python 3.12 or 3.13 inside WSL/Linux with the existing Docker setup.
Keep this repository beside `harness2_portable_core/`.

```bash
uv sync --locked
.venv/bin/python scripts/validate_product.py --evidence evidence/local-validation
.venv/bin/python -m lx.spike --core-source ../harness2_portable_core --output .lx/controlled-spike
```

Repeat the last command with the same output directory: completed task work is
reused without fresh provider-double calls. These calls simulate token usage;
they do not spend credits. The test suite separately verifies the proposer
session cache and harvested workspace, not only whole-task skipping.

The core is a declared local library dependency; the engine remains its pinned
vendored dependency. `uv.lock` uses relative paths. No core source changes are
required. Tests use the existing synthetic core fixture from the sibling source
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
