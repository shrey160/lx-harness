# LX harness

LX is a local experimentation platform for agents building task-specific harnesses.
Give it sample inputs, a goal and an output contract. A worker attempts the task;
an improver edits prompts, guardrails, skills and helper scripts while model weights
stay fixed. LX records the executions, changes, checks, errors and usage so you can
understand what happened and decide what to reuse. Scores can improve or decline;
a finished run is evidence, not a guarantee of a better harness.

[![Demo](https://img.shields.io/badge/Demo-FF0000?style=for-the-badge&logo=youtube&logoColor=white)](https://youtu.be/YcyyPwPcKYc)

The usual workflow is:

**Clone inside your project → configure LX in the Web UI → connect your agent
through MCP → prepare a task → run within your budget → inspect results.**

You can also work directly in the Web UI or CLI. Your external agent uses its own
model as orchestrator; LX supplies the configured worker, improver and task tools.

## How LX helps

Building reliable agents requires testing how they work, understanding failures
and deciding which changes are worth keeping. LX gives agents a platform for
that experimentation.

Provide sample inputs, a goal and an output contract. LX runs an automatic harness
loop and records the attempts, prompt and skill changes, tool calls, checks,
errors and usage. Agents and developers can inspect this evidence to understand
what failed, assess proposed changes and build better harnesses while model
weights stay fixed.

Your agent can also use LX to generate task-specific trajectories for model
fine-tuning. The run page's **Download trajectories** option exports recorded
agent interactions and tool use. Review, filter and convert these raw traces
into your chosen training format, then fine-tune with your training framework.

A useful experiment can reveal a failure, an ineffective change or a successful
approach. Improvement is something to investigate and validate—not something
every run guarantees.

## 1. Install inside your project

Install [Git](https://git-scm.com/downloads),
[uv](https://docs.astral.sh/uv/getting-started/installation/) and
[Node.js with npm](https://nodejs.org/en/download). Use Python 3.12 for the commands
below; uv supplies it. The package allows Python 3.12 and 3.13. A script sandbox is
needed for scoring checks and learned helper scripts: start
[Docker Desktop with Linux containers on Windows](https://docs.docker.com/desktop/setup/install/windows-install/),
or use Docker/a working Linux namespace sandbox on Linux.

From the **existing project repository where you want to use LX**, clone both
repositories. You can do this yourself or ask your agent to follow these steps:

```text
your-project/
  main_LX/                    # LX app, Web UI, CLI and MCP server; own Git repo
  harness2_portable_core/      # separate execution core; own Git repo
    vendor/harness2-cli/      # pinned third-party engine, already included
  lx-task/                    # example location for your sample-data workspace
    samples/
```

```bash
git clone https://github.com/shrey160/lx-harness.git main_LX
git clone https://github.com/shrey160/harness2-portable-core.git harness2_portable_core
cd main_LX
```

Keep these folder names and the sibling relationship: the dependency in
[pyproject.toml](pyproject.toml) resolves `../harness2_portable_core`, whose vendored
engine is installed automatically. No separate core server or Google Harness²
installation is needed. The repositories have independent Git histories; decide
whether to ignore these folders in your parent project or manage them explicitly.
Do not accidentally stage nested repositories as ordinary project files.

### Windows — PowerShell

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"
uv sync --locked --managed-python --python 3.12 --extra mcp
npm --prefix web ci
npm --prefix web run build
.venv-win\Scripts\python.exe scripts\build_sandbox_image.py
.venv-win\Scripts\python.exe -m lx.cli --help
.venv-win\Scripts\python.exe -m lx.server --port 8765
```

Keep Docker Desktop running for checks and scripts. Using the Python command
directly also works when PowerShell policy prevents running `scripts/serve.ps1`.

### Linux or WSL — shell

```bash
UV_PROJECT_ENVIRONMENT=.venv uv sync --locked --managed-python --python 3.12 --extra mcp
npm --prefix web ci
npm --prefix web run build
.venv/bin/python scripts/build_sandbox_image.py
.venv/bin/python -m lx.cli --help
.venv/bin/python -m lx.server --port 8765
```

The image-build command requires a running Linux Docker engine. If using the Linux
namespace backend instead, omit that command and confirm a working sandbox under
Settings → Tools. Native Windows and Ubuntu WSL2 have previous execution evidence;
these instructions do not establish support for every Linux distribution or ARM.
See [installation and troubleshooting](docs/installation.md) for prerequisites,
updates, path handling and verification limits.

## 2. Configure in the Web UI

Open [LX on this machine](http://127.0.0.1:8765).

1. **Settings → Providers:** add/select your chat models. Choose a provider, load
   its model list and enter its key if required. Assign worker and improver roles
   on the workspace's Spec tab. A connected agent provides the orchestrator role.
2. **Settings → Tools:** save a Meta key if using built-in Spark, SAM or Voice
   Transcribe. They share that key. Toggle task tools and check sandbox readiness.
   A photo/audio task needs Transcribe even when chat roles use another provider.
3. **Spec → Budget and limits:** review the workspace/run caps and task limits
   before approving live execution. Provider Test sends a real request and can
   cost money; a key being found does not prove it works.
4. **Settings → Connectivity:** keep Agent access Started to allow MCP calls.
   Leave workspace deletion permission off unless you want to grant it separately.

Keys are saved through the UI, with OS-keychain storage where available and
restricted local files on Linux without a keychain. They are not copied into MCP
configuration. See [configuration, persistence and spending](docs/configuration.md).

## 3. Connect your agent through MCP

In **Settings → Connectivity → Connect an agent**, copy this installation's
generated setup for your client. It includes the actual Python executable and
arguments. Merge it into existing client settings without discarding other servers,
then restart the client's LX connection or session.

LX's agent-facing server uses **stdio**. The client launches the installed Python
with `-m lx.mcp_server`; the Web UI address is not an MCP endpoint. The UI and MCP
process share settings when they use the same installation, OS user and state root.
The Web UI need not stay open for MCP operations.

Ask the agent: **“Call LX's status tool. Do not approve or start a run.”** A
successful tool response verifies the connection; a saved configuration alone does
not. Then ask for `guide` and `environment` to discover the current workflow,
templates and tools. [Connection details and all MCP tools](docs/agent-mcp.md).

Settings → **MCP servers** serves a different purpose: it connects other servers'
tools for LX's workers to use. [Worker-tool MCP guide](docs/mcp.md).

## 4. Run a task and inspect the evidence

Create a separate task folder such as `your-project/lx-task`, with `samples/`
inside it. Keep your host project's code, the LX checkout and the core outside the
sample-data tree. MCP workspace arguments take an absolute path to the task folder.

- `text-request`: each `samples/sample-*/` has `request.txt` and, for a checked task,
  `truth.json`.
- `photo-request`: each sample has 2–7 numbered photos, `request.wav`, optional
  starting data in `input.json`, and checked-task answers in `truth.json`.
  [Exact input and scoring contracts](docs/tasks.md).

An agent first calls `init`, drafts the goal/task/check with `spec_draft`, tests the
check and calls `ready`. The task describes what to achieve and the required answer;
the loop learns the procedure. For a first installation check, create a synthetic
workspace in the UI and run a **preview**: it uses doubles with no provider calls.
Preview does not work on arbitrary user samples.

When you request live execution, an agent can approve a checked spec and start a
trial or full run within the caps you set. Judged runs, unscored runs and checks
with weak self-tests require person review. Editing a spec clears its approval.
Live execution sends sample data to the selected providers and can spend money.

Start actions return a run ID promptly. The agent polls `run_status`, reads `edits`
and, on completion, `results`. Open the run in the UI for trajectories, answers,
usage, package and trajectory downloads. A run continues after its initiating
client disconnects; closing the UI is not a way to stop active work.

Read the changes and failures alongside scores. Checks report outcomes; they do
not select the harness by score. Each full loop starts from stock and carries
learned changes between samples within that run. A trial can explicitly use a
finished run's package or a saved sample's adopted harness. One trial is a smoke
test, not proof of transfer or improvement. [Complete user/agent workflow](docs/agent-workflow.md).

## Example datasets

[Example datasets](examples/README.md) contains ready-to-copy tasks for testing an
installation. Start with [Inventory and cash: three samples](examples/inventory-cash-three/README.md):
eight photos, conversational voice notes, starting inventory JSON and an independent
checker. The third request corrects an earlier instruction. Its
[agent runbook](examples/inventory-cash-three/AGENT.md) covers offline verification,
a fresh workspace, MCP setup and an explicitly requested live test.

Use the included helper to copy the example or make a standalone ZIP; no original
media collection or speech generator is needed. Offline checks make no provider
calls. Real photo/audio execution needs configured providers/tools and a requested
run; the built-in synthetic preview remains the free end-to-end wiring check.

## Development and roadmap

LX is actively evolving. Future development will expand the kinds of agents,
tasks and environments it can support. Planned directions include:

- **Subagent support:** delegation and coordination across multiple agents.
- **Extended connectivity:** more integrations with agent clients, providers and
  development workflows.
- **Better sandboxing:** stronger isolation and more flexible, reproducible
  execution environments.
- **Broader tooling and task support:** tools and environments for testing a wider
  range of tasks.
- **Richer experiment exports:** portable packages bringing together harnesses,
  inputs, outcomes, trajectories and findings.

These are development directions; availability and scope will evolve as features
are implemented and tested.

## Current limits and evidence

- Two general templates ship; tasks outside their input/runtime contracts need
  implementation work. Helper scripts have constrained runtime and no network.
- Model judgments can be wrong; score gains and learned rules need independent
  validation. Full returned reasoning is retained when the provider supplies it.
- Packages and trajectories are separate downloads. A standardized combined
  evidence/library handoff is planned, not shipped. There is no general deployment
  command for arbitrary new inputs.
- Agent deletion is off by default. When enabled, it removes LX-managed workspace
  state, preserves original files and spend records, and refuses active jobs.
  Unsettled calls remain visible for later person-only billing resolution.
- This README describes the local source revision checked in
  [documentation validation](docs/documentation-validation.md), including the
  published product/core revisions. The later
  [fresh-clone rehearsal](evidence/2026-11-04-fresh-install/README.md) passes on
  Windows and Ubuntu WSL2; clean-OS prerequisites, bare Linux and real-provider
  validation remain unverified by that rehearsal.
  Historical experiment results are indexed in [evidence](evidence/README.md);
  immutable reviews retain the assumptions of their date.

## License

Original LX code and documentation are licensed under the
[Apache License 2.0](LICENSE); see [NOTICE](NOTICE). This permits commercial use,
modification and redistribution subject to the license terms.

Third-party code, datasets and media retain their own terms. In particular, the
separate core's optional WorkBuddy benchmark material has non-Apache conditions,
including an EU-use restriction. See [third-party notices](THIRD_PARTY_NOTICES.md).

## Find your way around

| Need | Read |
| --- | --- |
| Install, restart, update or diagnose setup | [Installation](docs/installation.md) |
| Keys, providers, tools, budgets and saved state | [Configuration](docs/configuration.md) |
| Connect an agent and use the MCP API | [Agent-facing MCP](docs/agent-mcp.md) |
| Human/agent responsibilities and a worked workflow | [Agent workflow](docs/agent-workflow.md) |
| Sample formats, checks and model judges | [Writing a task](docs/tasks.md) |
| Ready-to-copy test data and installing-agent instructions | [Example datasets](examples/README.md) |
| Give workers tools from another MCP server | [Worker-tool MCP](docs/mcp.md) |
| Libraries, programs and sandbox limits | [Environment](docs/environment.md) |
| LX/core boundary, source map and development | [Architecture](docs/architecture.md) |
| All current guides and historical reviews | [Documentation index](docs/README.md) |
