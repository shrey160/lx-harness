# LX, the separate core and the host project

LX is installed inside the repository where it will be used. Its two source
repositories stay separate from that host project's code and task samples:

```text
host-project/
  main_LX/                         LX application repository
    src/lx/                        UI API, CLI, MCP, orchestration and task logic
    web/                           React UI; build output served locally
    docs/                          Current guides and historical reviews
  harness2_portable_core/           Separate h2portable repository
    src/h2portable/                 Managed jobs, execution and recovery
    vendor/harness2-cli/            Pinned, attributed third-party engine
  lx-task/                         Example user task workspace
    samples/                       Original task inputs and expected answers
    eval/                          Optional check/judge source files
    .lx/                           LX-managed versions, snapshots and results
```

## Execution path

The Web UI, CLI and external-agent MCP server call the shared LX engine. External
agent actions go through `orchestration.py`, which constrains available operations.
The engine persists configuration and starts a detached managed-run controller.
The controller uses the core and its vendored Harness² CLI engine to execute the
loop. Worker/improver model calls and hosted/MCP tools are handled through LX;
learned helper scripts and scoring checks execute in isolated sandboxes.

The core is a **Python dependency**, not another Web UI or always-running server.
Syncing the product environment installs `h2portable` and `harness2-cli` from the
sibling repository. Only that vendored engine belongs on the runtime import path.
The independent CLI fork is not Google's official method-reference repository;
provenance, licenses and patch history are recorded in the core's `UPSTREAM.md`
and `upstream.lock.json`. See the
[core repository](https://github.com/shrey160/harness2-portable-core).

## Two MCP directions

| Direction | Setup | Role |
| --- | --- | --- |
| External agent → LX | Connectivity; local stdio server | Agent prepares/operates experiments on the user's behalf |
| LX worker → another tool server | MCP servers; command, Docker or streamable HTTP | Worker gets person-approved read-only tools; loop learns their use |

Neither direction turns the local Web UI URL into a public MCP endpoint.
See [agent MCP](agent-mcp.md) and [worker tools](mcp.md).

## State and boundaries

Original samples stay in the chosen task folder. Approval freezes a snapshot and
binds the spec/data/configuration. Specs/checks/results live in its `.lx/`; real
spend, registrations and approvals also have user-level records. Keys belong in
runtime credential storage, not task packages or MCP client settings. The
[configuration guide](configuration.md) explains persistence and permissions.

A detached controller can survive closing its initiating client. Stop through LX
so job state and accounting remain consistent. A full loop starts from stock;
learned changes carry between samples within that run. Checks report outcomes
without choosing the adopted harness by score. Keep model capability claims
separate from offline wiring tests and observed experiment results.

## Source map and development

- [src/lx](../src/lx/README.md): shared engine, orchestration, MCP server/client,
  tasks, judges, budgets and providers.
- [web](../web/README.md): UI build and source map. Rebuild after UI changes;
  `lx.server` serves `web/dist`, not a separate development server.
- [scripts](../scripts/README.md): launcher, sandbox builder and validation tools.
- [docker](../docker/README.md): pinned sandbox build.
- [tests](../tests/README.md): offline suites, fixture isolation and skip limits.
- [evidence](../evidence/README.md): historical live/offline/preview results.

Run the existing tests with the installed interpreter:

```powershell
.venv-win\Scripts\python.exe -m unittest discover -s tests
```

```bash
.venv/bin/python -m unittest discover -s tests
```

Use discovery because test modules import shared fixtures. Missing sandbox or MCP
dependencies can cause skips; report them. Source sync/build steps belong in the
[installation guide](installation.md). Do not run old fixed-experiment scripts as
an installation check: some encode historical scopes and require their own paid
execution request. Preserve immutable reviews and add dated evidence for new work.
