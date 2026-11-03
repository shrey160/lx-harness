# Configure LX through the Web UI

Open the [local Web UI](http://127.0.0.1:8765) after following
[installation](installation.md). Configure secrets and spending here before
delegating a task. A connected agent reuses LX's configuration; it does not need
your provider keys in its MCP settings.

## Settings and task controls

| Location | What to configure |
| --- | --- |
| Settings → Providers | Chat endpoints, model entries, keys, provider/model facts and advanced overrides |
| Settings → Tools | Shared Meta key; global SAM/Voice Transcribe switches; sandbox availability |
| Settings → MCP servers | Other servers' tools available to workers; connection, secrets and person-confirmed read-only tools |
| Settings → Connectivity | Agent access; install-specific client setup; separate permission for agent workspace deletion |
| Settings → Usage | Real usage, held calls and deleted-workspace billing records |
| Settings → General | Installation/state and environment information |
| Workspace → Spec | Goal, task/output contract, worker/improver roles, tools, checks/judges, mode, budget, limits and approval |

The external agent becomes orchestrator when it drafts through MCP. It uses the
worker/improver model entry IDs from `status`, not arbitrary guessed provider IDs.
The built-in Discuss interface instead uses the configured orchestrator model.

## Providers and keys

Add a provider in Settings → Providers, choose a model from its list and save its
key if required. The app offers OpenRouter, NVIDIA, Unsloth Studio, a local server
and a custom endpoint. Models expose different amounts of pricing/capability data;
review what the app found. Advanced fields allow corrections. A listed model is
not proof that it accepts this task's images or tool calls.

For built-in Meta models/tools, save the shared Meta key under Settings → Tools.
Its precedence is: saved registered key first; otherwise `MODEL_API_KEY`, then
`META_API_KEY` in the runtime environment, then the core's `.env` fallback. The
recommended install path is the Web UI: an agent must not read secret files or
request key values in chat. A found-key indicator checks availability only;
the provider Test button makes a real request and can spend money.

Keys use the OS keychain when available. On Windows, key saving refuses if the
keychain is unavailable. Linux without a usable keychain falls back to owner-only
files and refuses filesystems that cannot enforce private permissions. Never put
keys in task folders, committed files, MCP launch examples or screenshots.

Photo requests need Voice Transcribe and its Meta key even if another chat provider
is used. SAM may be omitted where the task permits direct photo viewing. Global
tool switches and per-spec selection both apply; turning a required tool off blocks
affected approved work rather than silently changing it.

## Budgets, approval and agents

Set the workspace and run budgets in the UI. Review trial caps, call/token/time
limits and any separate model-judge budget before approving. Missing provider
prices make spending estimates incomplete; free-local accounting does not guarantee
that a separately operated server has no cost.

An MCP agent can draft specs, test checks, approve eligible checked live specs and
start work within the limits you set. It must have your instruction to approve
and run: connecting alone is not a request to spend. Agents cannot raise budgets,
add providers/keys, create grants or rule on held billing. A weak self-test, no
scoring check or configured model judges requires person review. Saved spec edits
clear approval; approval freezes sample inputs and binds the configuration.

A person can optionally issue a grant with scope, cap, expiry and run count. An
agent able to approve does not need a grant; say explicitly if it must operate only
under one. Stopping Agent access blocks new MCP actions, including open sessions;
it does not stop a controller already running. Stop those runs explicitly.

## Where configuration and results live

- The **host project** contains the two independent clones and any task folders.
- The **task workspace** keeps original `samples/` and LX-managed `.lx/` state:
  specs, check versions, frozen inputs, runs, packages and core-job records.
  `eval/` can hold check/judge files. Ordinary unrelated files are not sample data.
- **User-level state** remembers workspaces, providers, approvals, grants and real
  spending. The default state root is beneath the user's
  `.local/state/lx-harness/`, with an install-path-derived identifier. Read the
  actual location in Settings → General rather than guessing it.
- Where keychain storage is unavailable on POSIX, default secret storage lives
  beneath `.local/share/lx-harness/` for that installation, with restricted access.
  This is runtime storage, not a file an agent should inspect.

UI and MCP share configuration only when they use the same installation, OS
environment/user and state root. Connectivity generates the matching command;
custom state roots are included there. An arbitrary `--root` is a test/private
state, and live calls are disabled for scratch roots. Switching native Windows to
WSL or moving the checkout does not migrate configuration automatically.

## Workspace lifecycle

Archive/restore controls are in Workspace options. Archived workspaces remain
readable and cannot start new work; archive/restore retain their own active/held
call guards. User deletion is one click. Deletion removes LX-managed workspace
settings, runs and harnesses while preserving original samples/other files and
user-level spend records. Active jobs block deletion. Unlike archive, deletion
can proceed with unsettled billing; those records remain under Usage for later
person-only billed/not-billed resolution.

Agent deletion requires the separate **Allow agents to delete workspaces** setting
in Connectivity. It is off by default, checked for each call, and agents cannot
enable it. An agent with that permission still needs a request to delete the
specific workspace. Deletion cannot be undone through an LX restore command.

For worker-side MCP secrets and connections, read [MCP tools](mcp.md).
