# LX harness

LX improves the instructions around a model for a task you define. You give it a
folder of sample data, a goal and a check that scores answers. A larger model
then edits the harness (prompts, guardrails, skills, helper scripts) while the
model's weights stay fixed, and the check decides whether the edits helped. You
keep the result as a package, so a smaller model can do the task later.

It runs on your own machine as a local web app. You choose the providers, and
nothing runs live until you approve the spec, the data and the budget.

## What has been shown so far

| Test | Agent | Result |
|---|---|---|
| Held-out photo inventory, learned harness against base | Muse Spark | 0.47 to 0.70, gain +0.23, 95% range 0.11 to 0.37 |
| Same harness moved to a small local model | Gemma 4 E4B | 0.40 to 0.30, gain -0.09: it did not transfer |
| Full loop on 6 inventory samples on the new agent loop | Muse Spark 1.3 Contributor | 0.82 to 0.82, $0.37: all 6 base runs answered, and these samples left the loop little room |
| Full loop on 6 inventory samples through OpenRouter | Muse Glimmer | 0.00 to 0.00, $1.27: the agent never answered within the turn limits of the time |

The Glimmer run came before the raised limits and the new agent loop, and Glimmer has
not been rerun on them.
Previews use scripted responses, so they prove the plumbing and say nothing about
a model. Earlier results are in [docs/history.md](docs/history.md).

## Install

Clone both repositories side by side under these exact folder names, so the
relative dependency path resolves:

```bash
git clone https://github.com/shrey160/lx-harness.git main_LX
git clone https://github.com/shrey160/harness2-portable-core.git harness2_portable_core
```

The core repository vendors a pinned copy of the harness2-cli engine (Apache-2.0;
provenance and the recorded Windows patches are in its `UPSTREAM.md`). That engine
is third-party code, not part of this project's own implementation.

You need [uv](https://docs.astral.sh/uv/), Node.js and Python 3.12 or 3.13. uv can
supply Python on Windows. You also need a script sandbox to run scoring checks and
helper scripts. That is Docker Desktop on Windows, or Docker or `unshare` on Linux.
Without one, checks do not run, so graded tasks cannot start, and helper scripts
are turned off.

### Windows

Run these from `main_LX` in PowerShell. A separate environment keeps the WSL
`.venv` untouched:

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"; uv sync --locked --managed-python --python 3.12
npm --prefix web ci
npm --prefix web run build
.venv-win\Scripts\python.exe scripts\build_sandbox_image.py
```

The last line needs Docker Desktop running, and builds the `lx-sandbox:pillow-11.3.0`
image once. The sandbox has no network, a read-only root, no capabilities and an
unprivileged user.

### WSL or Linux

```bash
uv sync --locked --python 3.12
npm --prefix web ci
npm --prefix web run build
```

In WSL `uv` may not be on the PATH. If you ran the core's setup, it installed a
copy at `~/projects/harness2_portable_core/.tools/bin/uv`. Sync again whenever
`uv.lock` changes, because an out-of-date `.venv` fails at import time.

## Start the app

On Windows:

```powershell
.\scripts\serve.ps1
```

This uses `.venv-win` if it exists and otherwise starts the server inside WSL. Add
`-Wsl` to choose WSL, `-Port` to change the port and `-Distro` to name your distro.

In WSL or Linux:

```bash
.venv/bin/python -m lx.server --port 8765
```

Open <http://127.0.0.1:8765>. The server binds to loopback only, uses a per-session
change token and rejects foreign origins. Close the terminal to stop it.

The Windows and WSL servers keep separate state: open workspaces, provider entries,
keys, budgets and approvals. A provider added in one does not appear in the other.

## Try it in preview

A preview makes no live calls and spends nothing. Its responses are scripted, and
its usage is simulated.

1. Choose New workspace and enter a folder. Browse opens a folder dialog on native
   Windows; elsewhere, type the path.
2. Keep Task on Photo inventory, set Samples to Add the synthetic samples, and create
   the workspace.
3. On the Spec tab, choose Review and approve, then Approve.
4. On the Runs tab, start the preview. The run's page updates as it goes.

The other task, Image annotation demo, is the older pixel-box demo. It accepts only
its constructed samples and is a poor test of the loop.

## Set up a provider

In Settings, under Providers, choose Add provider, then OpenRouter, NVIDIA, Local
server or Other. The app reads that provider's model list, so you pick a model and
add a key if the provider needs one. You do not type prices or capabilities. Other
asks for the server's address, because the app cannot find that. Any chat provider
with an OpenAI-compatible endpoint can fill any role: orchestrator, improver or
worker.

What the list gives depends on the provider. Checked on 31 October 2026:

| Provider | Base URL | What its list gives |
|---|---|---|
| OpenRouter | `https://openrouter.ai/api/v1` | Names, prices with the cached rate, context size, image and tool support |
| NVIDIA | `https://integrate.api.nvidia.com/v1` | Names only. The app paces it at 40 requests a minute |
| Local server | `http://127.0.0.1:8888/v1` | Usually names only. Counted as free |

For example, `meta/muse-spark-1.3-contributor` on OpenRouter lists $0.10 and $0.20
per million tokens, and $0.002 for cached input.

Where a list does not say, the app assumes a model accepts images and tool calls,
and a run tells you if it does not. A listed context above 262,144 tokens is held
at that number, because each call reserves its whole context at the input price, and
a large one would otherwise use up a small budget on every call.

If a provider publishes no price, a live run still works, but the budget cannot count
it, so only the call limits bound the run, and the spec says so. A local server is
counted at $0 and works the same way. A provider that states each call's cost, as
OpenRouter does, gets a comparison with the listed prices on the run's page, and a
warning above 5%. To correct anything the provider gave, use Edit, under Advanced.

Plain HTTP works only for loopback or private-network hosts, such as a local model
server. Other hosts need HTTPS.

Test on a provider's entry sends one short live message to check the key, endpoint
and model. It does not test images or tool calls, and it spends from a separate $1
ledger. Then assign providers to roles on a workspace's Spec tab.

### Meta models

The built-in entries are Muse Spark Contributor, SAM 3.1 and Muse Voice Transcribe.
They are ready once you have a Meta key, and they share that one key. Save it in
Settings, under Tools, which puts it in the keychain. Otherwise the app reads the
`MODEL_API_KEY` or `META_API_KEY` environment variable, or else the core's `.env`.
A saved key is used first. Settings shows whether a key was found, without showing
the key or calling Meta, so use Test on the Spark provider to check that Meta
accepts it.

SAM and Transcribe are the worker's tools in a live inventory task, so that task
needs the Meta key even when another provider fills all three roles. A live spec
that needs the key and has none cannot be approved or started, and it says why.
Previews need no key. Spark Contributor may use approved content for training, and
the app asks you to accept that when Spark has a role.

SAM and Transcribe start on. Two switches control them. In Settings, under Tools,
Off stops every workspace from using a tool, and any approved run that includes it
is blocked with the reason, not silently changed. On a workspace's Spec tab, Model
tools chooses what this task gets; a change is a new spec version to approve. A tool
that is off is gone from the run: the agent is not told about it, `count_boxes` goes
with SAM, its call limit is hidden, and it leaves the notice of who receives what.

A photo task can leave out SAM, because the agent can still `view` the photos. It
cannot leave out Transcribe, because its requests are audio only. Scores from runs
with different tools are not comparable.

### Where keys live

Keys go to the operating system's keychain (Windows Credential Manager on Windows)
and never enter a workspace. On Windows a key is refused, not written to a file, if
Credential Manager is unavailable. WSL and headless Linux have no keychain, so keys
use owner-only files in local-share storage, and a filesystem that cannot enforce
private permissions refuses them. Keys saved earlier as files move to the keychain
on the next start.

## Prepare your samples

A workspace is a folder you choose. Put samples in a `samples/` subfolder; a folder
holding code needs this, because dotfiles such as `.git` are refused as samples.
Approval freezes the samples as they are at that moment, and each run uses the
frozen copy, so later edits need a new approval.

The `text-request` template takes a folder per sample with `request.txt` and
`truth.json`, and needs no photos, audio, SAM or Transcribe. Its first task is Order
records; `src/lx/records_check.py` is its check. The agent has only the harness's own
skills and scripts, and its worker provider needs tool calls but not images.

The `photo-request` template expects a folder per sample:

```text
samples/
  sample-01/
    photos/1.jpg  2.jpg  3.jpg
    request.wav
    truth.json
  sample-02/
    ...
```

- **Samples:** 6 by default, in folders named `sample-*`. With more, a fixed shuffle
  picks which 6 run in the loop, so folder order cannot bias the choice.
- **Photos:** 3 to 7, numbered from 1, as `.jpg`, `.jpeg` or `.png`. Intake applies
  the EXIF orientation, drops all metadata, and resizes the longest side to 1024.
- **Request:** `request.wav`, mono 16-bit PCM at 16 or 24 kHz. A live run needs it in
  every sample; `request.txt` works only in a preview. To convert a recording:
  `ffmpeg -i in.m4a -ac 1 -ar 16000 -sample_fmt s16 request.wav`
- **Size:** at most 100 files and 200 MiB for a task. A ZIP upload is limited to 20 MiB.
- **From a frozen dataset:** `scripts/export_samples.py` writes this layout.
- **Truth:** `truth.json` reaches only the check, never the worker or the improver.

For the inventory task, `truth.json` holds what is really in each photo. Each photo
number lists its items. Include every item a correct answer should count.

```json
{"request_type": "colour_count", "kind": "pen", "colour": "green",
 "photos": {"1": [{"kind": "pen", "colour": "green", "count": 2}],
            "2": [{"kind": "pen", "colour": "blue", "count": 1}],
            "3": []}}
```

`request_type` is `count`, `colour_count` or `inventory`. An `inventory` request has
no `kind` or `colour`. An optional `aliases` maps a kind to other accepted names.
Counts are exact below 20, then within 5%. Photos must show different items, because
the task does not de-duplicate across photos.

Everything in the samples goes to the providers you assign. Use photos with no
people, faces, screens, documents or addresses unless you are content to send them.
Before a live run the app shows who receives what, per role and tool. Accepting
covers that workspace until a new host would receive data.

## Write a scoring check

A task is data plus a check, not code. The spec's `task` holds the contract, the
base prompt and the wording the loop uses. The check file is stored as
`.lx/checks/vN/check.py`, and the spec records its hash, so editing the file stops
the run. There is no form for this yet: send it with `Engine.set_task(id, task,
check)` or `POST /api/projects/<id>/task`, which needs the session token. Each change
is a new spec version that you still approve.

A check is one Python file, treated as untrusted code, so it runs only in the
sandbox: no network, a 60-second limit per batch and at most 20,000 characters of
source. It reads one JSON object on stdin and prints one on stdout:

- `{"items": [{"truth": ..., "answer": ...}]}` prints
  `{"results": [{"checks": [{"name": str, "passed": bool, "error": str or null}]}]}`
- `{"examples": [truth, ...]}` prints `{"answers": [a correct answer per truth]}`

The score is the fraction of named checks passed, with at most 200 checks per item.
This minimal check passes the self-test:

```python
import json
import sys


def grade(truth, answer):
    right = type(answer) is dict and answer.get("total") == truth["total"]
    return {"checks": [{"name": "total", "passed": right, "error": None if right else "total: wrong"}]}


payload = json.load(sys.stdin)
if "examples" in payload:
    print(json.dumps({"answers": [{"total": truth["total"]} for truth in payload["examples"]]}))
else:
    print(json.dumps({"results": [grade(item["truth"], item["answer"]) for item in payload["items"]]}))
```

Approval re-runs a self-test on the frozen samples. A correct answer must score 1.0,
malformed answers must be graded without crashing and without full marks, grading
must repeat exactly, another sample's answer must not pass, and errors must not
reveal expected values. `Engine.test_check(id)` or
`POST /api/projects/<id>/check-test` runs it sooner. `src/lx/inventory_check.py` is a
full example.

## Run limits

Set these on the Spec tab under Advanced. Each has a default and a hard outer
bound (`lx.limits`).

| Limit | Default |
|---|---|
| Agent turns per photo or text sample (plus 4) | 12 |
| Agent tool calls per photo or text sample (plus 6) | 12 |
| Stop after this many agent runs in a row with no answer | 8 |
| Improver turns per session | 12 for Spark, 30 otherwise |
| Improver session | 180 seconds for Spark, 900 otherwise |
| One model call with its retries | 90 seconds for Spark, 600 otherwise on a task, 300 on the demo |
| Whole run | 4 hours on a task; 15 minutes on the demo with Spark, 1 hour otherwise |
| Reply length | 4,096 tokens for Spark, 8,192 otherwise |
| Model calls per run | 1,400 on a task, 200 on the demo |
| SAM calls and Transcribe calls per run (photo tasks only) | 600 and 60 |

A field tied to a role takes its default from that role's provider. The others take
the longest default among the run's providers, and a provider entry can set its own
time per call and reply length. The spec holds every value, so a change is a new
version that needs approval. A run keeps the limits it was approved with, so a
raised limit reaches a new run, not a resumed one. Spec versions saved before these
limits existed need approving again, because the spec now binds them.

Sandbox rules, check and script run times, the self-test, human approval, request
size bounds and no-redirects are not settable.

## Budgets and run health

Each workspace has its own budget and real-spend ledger: $5 by default, at most $20.
Each spec also has a run budget. Calls are reserved before they are sent, and the
actual cost comes from reported usage at the prices you entered, which can differ
from your provider's bill.

The Runs tab lists every run. A run's page shows samples done, agent runs answered,
improver sessions finished, why any failed, and samples left ungraded. From there:

- **Stop and Resume:** a failed, cancelled or interrupted run can resume.
- **Grade saved answers:** a failed task run whose loop finished can be graded from
  its saved answers, with no calls.
- **Held calls:** a call that ends without a known cost stays held against the budget,
  with its reason shown. After checking your provider's bill, release a stopped run's
  held calls as not billed, or count them as spent.

## What lives where

Your workspace holds `.lx/`, which LX adds to a `.gitignore`:

```text
.lx/
  project.json
  spec/vN.json          immutable spec versions
  checks/vN/check.py    the approved scoring check
  snapshots/            frozen samples
  runs/<id>/            run record, event log, snapshot, outputs, health.json
  packages/<id>/        the reusable harness
  preview-usage.sqlite3 simulated usage
```

Settings shows spending across workspaces, the script sandbox's status and the tools
an agent can call.

Provider entries, approvals, budgets and real-spend ledgers stay in user-level state
at `~/.local/state/lx-harness/`, so writing to the folder cannot approve a run. Any
change to a spec file voids its approval. An agent with full access to this machine
is not stopped by this; the human confirmation step comes with the CLI and MCP server.

## Repository map

Each folder has its own README:

| Folder | Holds |
|---|---|
| [src/lx](src/lx/README.md) | The product package; `lx.engine` is the single service layer |
| [web](web/README.md) | The React UI |
| [scripts](scripts/README.md) | Start, build and validation scripts |
| [docker](docker/README.md) | The sandbox image definition |
| [tests](tests/README.md) | The offline test suite |
| [docs](docs/README.md) | Task contracts, experiment reviews and history |
| [evidence](evidence/README.md) | Saved results of tests and live runs |
| [harnesses](harnesses/README.md) | Frozen harnesses from experiments |

[overview.md](overview.md) is the original project vision.

## Not built yet

- Two templates exist: `photo-request` and `text-request`. A task that fits neither
  needs a new one.
- The discussion step is not yet a real orchestrator: it does not remember the
  conversation or edit the spec. That is P5.
- There is no CLI or MCP server yet (P6), and the GUI redesign is P7.
- Reuse on a fresh sample works only for the demo. A task package holds the harness, its
  check, results and limitations; running it on new samples comes with `lx use` (P6).
- The Test button has not yet made a real provider call, and request pacing has been
  tested only with a mocked clock.

## Tests

Run the suite from `main_LX`. With no script sandbox, sandbox tests skip.

```powershell
.venv-win\Scripts\python.exe -m unittest discover -s tests
```

```bash
.venv/bin/python -m unittest discover -s tests
```

`scripts/validate_product.py --evidence <folder>` writes a validation record. The
core is a declared local library dependency and the engine stays its pinned vendored
dependency. `uv.lock` uses relative paths. Tests use the synthetic core fixture from
the sibling repository.
