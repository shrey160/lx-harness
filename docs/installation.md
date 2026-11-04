# Install and maintain LX

Start with the complete [README quick start](../README.md). Commands here run
from `main_LX` unless stated otherwise. The Python environment installs both LX
and the separate sibling core; you do not start a second core service.

## Prerequisites

| Requirement | Setup/check |
| --- | --- |
| Git | Install from [Git](https://git-scm.com/downloads); `git --version` |
| uv | Follow [uv installation](https://docs.astral.sh/uv/getting-started/installation/); `uv --version` |
| Python | Commands select managed Python 3.12. Packages allow `>=3.12,<3.14` |
| Node.js and npm | Install a supported LTS from [Node.js](https://nodejs.org/en/download); `node --version`, `npm --version` |
| Windows sandbox | [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/), running with Linux containers; `docker info` |
| Linux/WSL sandbox | Docker engine and image, or functioning `unshare`/`chroot` namespaces; see [environment](environment.md) |

The supplied Docker image builder targets Linux x86_64 and Python 3.12. Other
architectures have not been established by the recorded checks. Building requires
internet access for pinned dependencies. Running checks/scripts uses an isolated,
offline sandbox; hosted model/tool calls are made by the controller separately.

## Clone inside the host project

From the existing repository in which you want to use LX:

```bash
git clone https://github.com/shrey160/lx-harness.git main_LX
git clone https://github.com/shrey160/harness2-portable-core.git harness2_portable_core
cd main_LX
```

`main_LX` and `harness2_portable_core` must be siblings. The checked-in lockfile
uses relative local dependencies. The core already contains `vendor/harness2-cli`;
do not install a competing distribution sharing the `harness2` import namespace.

An agent can perform these same steps. It should first inspect the host project's
instructions and existing folders, preserve existing checkouts, and report setup
results without reading credentials. It should leave key entry and spending limits
to the user in the Web UI. It must not automatically run a paid example.

Both clones keep independent `.git` directories. For a machine-local installation,
you may exclude `/main_LX/` and `/harness2_portable_core/` through the host project's
local `.git/info/exclude`; use its `.gitignore` instead only if the team should
share that policy. Those are user choices, not installation commands that silently
change the parent repository. A submodule layout is not configured by this guide.

## Windows

Run in PowerShell, inside `main_LX`:

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"
uv sync --locked --managed-python --python 3.12 --extra mcp
npm --prefix web ci
npm --prefix web run build
.venv-win\Scripts\python.exe scripts\build_sandbox_image.py
.venv-win\Scripts\python.exe -m lx.server --port 8765
```

The environment variable applies to this PowerShell session; set it again in a
new session before syncing. No environment activation is required. Keep Windows
and WSL environments separate: `.venv-win` and `.venv` contain different binaries.

The optional `scripts/serve.ps1` launcher uses `.venv-win` if present; `-Wsl`
selects WSL, `-Distro` selects a distro, and `-Port` changes the port. If policy
blocks scripts, use the Python server command above without changing machine policy.

## Linux or WSL

Run in a Linux shell inside `main_LX`:

```bash
UV_PROJECT_ENVIRONMENT=.venv uv sync --locked --managed-python --python 3.12 --extra mcp
npm --prefix web ci
npm --prefix web run build
.venv/bin/python scripts/build_sandbox_image.py
.venv/bin/python -m lx.server --port 8765
```

The image command requires Docker. On a host with working Linux namespaces, it
may be omitted: LX prefers that backend. Inspect Settings → Tools, or the MCP
`environment` response, rather than assuming namespaces work merely because the
executables exist. Without a sandbox, graded tasks cannot start and helper
scripts are unavailable. Bare Linux distribution coverage remains unverified;
previous Linux execution evidence comes from Ubuntu under WSL2.

Windows and WSL use separate user state and credentials. Keep UI and MCP in the
same OS environment for a shared setup. A headless Linux install without a usable
keychain needs a filesystem that can enforce owner-only secret-file permissions.

## Verify and connect

1. Open [the local UI](http://127.0.0.1:8765). A 503 usually means `web/dist` has
   not been built; repeat the npm steps.
2. Check Settings → Tools for sandbox availability. A provider key is unnecessary
   for synthetic previews; Docker/namespace isolation still matters for checks.
3. Create a new disposable workspace using synthetic samples and complete a preview.
   This checks wiring with doubles and makes no claim about real-model quality.
4. Configure keys/providers in the UI and copy the generated MCP setup from
   Connectivity. Verify the agent can call `status` before preparing a live run.

CLI smoke checks, using the same interpreter as the UI:

```powershell
.venv-win\Scripts\python.exe -m lx.cli --help
.venv-win\Scripts\python.exe -m lx.mcp_server --help
```

```bash
.venv/bin/python -m lx.cli --help
.venv/bin/python -m lx.mcp_server --help
```

For installed command names, use `.venv-win\Scripts\lx.exe` on Windows or
`.venv/bin/lx` on Linux. No global `lx` installation is required.

For a concrete test after setup, use the packaged
[three-sample inventory/cash dataset](../examples/inventory-cash-three/README.md).
Its [agent runbook](../examples/inventory-cash-three/AGENT.md) explains offline
verification, copying into a separate task folder, loading the custom task/check
and testing LX readiness. Live photo/audio execution requires configured providers
and a requested run; it cannot use the built-in synthetic preview doubles.

## Stop, restart, update and move

- **Stop a run:** use its UI Stop control or MCP `run_stop`. Spend already incurred
  stays recorded. Detached run controllers can outlive the UI and initiating agent.
- **Stop the UI:** press Ctrl+C in its terminal. Restart with the same Python command.
- **Update:** stop active jobs first, inspect changes in both repositories with
  `git status`, and use the matching product/core revisions for the version being
  installed. For clean tracking branches, `git pull --ff-only` in each repository
  updates without creating a merge. If it refuses, inspect local work rather than
  resetting it. Re-run the matching uv sync, npm install/build and, when sandbox
  inputs changed, image build. Restart UI and agent MCP processes.
- **Record revisions:** run `git rev-parse HEAD` in each checkout. Relative paths
  fix layout, not Git commit compatibility; use [validation](documentation-validation.md)
  to see the pair reviewed for these docs. Local unpushed changes need publication
  before another machine can clone them; these instructions do not push anything.
- **Move:** virtual environments and generated MCP paths should be recreated at
  the new location. Default user state is keyed to the LX install path, so moving
  it can appear to create a fresh configuration. Do not delete the old state or
  copy secret files through an agent to troubleshoot it.
- **Remove:** stop jobs and disconnect the client's LX entry first. Removing
  clones is separate from deleting task workspaces or user-level state; preserve
  samples, packages and accounting records you need. There is no all-data uninstall
  command in this guide.

## Common failures

| Symptom | Check |
| --- | --- |
| Missing `h2portable` or local dependency | Verify sibling folder name, then sync the correct environment |
| Missing `harness2` distribution | Verify the core includes its vendored engine and sync from LX; do not copy just Python source |
| Missing `mcp` module | Re-sync with `--extra mcp` and use that environment in the client |
| Old code after an update | Re-sync and restart both UI and MCP; rebuild the UI if it changed |
| No sandbox | Check Docker engine, image build and Settings; inspect Linux namespace errors |
| Keys/settings differ between UI and agent | Match installation, interpreter, OS user and state root |
| No tools or stale tools in the client | Re-copy Connectivity setup and reconnect; verify an actual `status` call |
| Port 8765 is occupied | Use another `--port` and open that URL; do not kill an unrelated process |
| Paths contain spaces | Quote shell paths; keep client command and argument array separate. Prefer the UI-generated setup |

See [configuration](configuration.md), [agent MCP](agent-mcp.md) and the dated
[validation record](documentation-validation.md) for current limits and evidence.
