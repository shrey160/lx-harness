# Documentation verification — 3 November 2026

This records checks for the installation/Web UI/MCP documentation rewrite.
It does not certify a fresh installation on every platform or establish model
quality. No real sample workspace was deleted and no paid experiment was started.

## Source baseline

| Component | Reviewed revision |
| --- | --- |
| LX before the documentation change | `2b593845b324ab309fdb20314a75d7227467ec0d` |
| Separate portable core | `53b9b6b1eebf696b143df8dd90f803db79d78c56` |
| Core's vendored independent CLI engine | `0c811344a4edfe34960b2fc8f286369d7e81fb27`, plus core-recorded Windows patches |

The working product was clean before this task. The core had a pre-existing
`.env.example` modification, which was left untouched. At inspection the local
product and core were respectively 229 and 6 commits ahead of their cached remote
tracking branches. No remote fetch, push or public clone verification was performed.
Consequently the cloning instructions express the required layout, but newly
documented behavior is not established as available from the public remote yet.
Publish/reconcile matching source revisions before advertising a tested release.

## Checks performed

- Read the actual dependency declarations/lock layout, Web UI labels, keychain and
  runtime precedence, state-root selection, launcher, CLI parser, MCP server and
  orchestration implementations. Reconciled current lifecycle and permission
  behavior with the latest local evidence; historical experiment reviews were
  preserved.
- CLI and MCP `--help` both succeeded using the existing Windows environment.
- Existing **12 agent-setup tests passed**, including generated configuration,
  install-specific state paths, Windows environment forwarding, access controls
  and key-free setup. Existing **8 MCP server tests passed**, including the SDK
  stdio client taking disposable synthetic samples to a completed preview.
  No skips in these two runs. One test intentionally logs an exception to verify
  private details are withheld from the tool response; that case passed.
- Initial restricted-process test execution failed on temporary-directory access,
  before exercising product behavior. Re-running with an accessible disposable
  temporary directory and approved local execution passed. The initial uv attempt
  similarly failed on cache permissions; an accessible cache resolved it.
- The Windows `uv sync --locked --managed-python --python 3.12 --extra mcp`
  command was checked with `--offline --dry-run`: 42 packages resolved, 39 checked,
  no changes needed. This uses the existing environment; it is not a clean install.
- Documentation links, JSON/CLI examples and MCP reference coverage were checked
  against local files/parser/source: **11 documents, 84 local links, 9 parsed
  examples, no issues**. The reference covers all **23** currently registered
  server tools. Four PowerShell and six Bash blocks pass syntax parsing; Bash
  parsing used Ubuntu WSL without executing the commands. Final whitespace/Git
  checks accompany this commit.
- Upstream prerequisite references were reviewed: [uv installation](https://docs.astral.sh/uv/getting-started/installation/),
  [Node.js downloads](https://nodejs.org/en/download) and
  [Docker Desktop Windows installation](https://docs.docker.com/desktop/setup/install/windows-install/).

Toolchain observed: Windows x86_64, Node 24.20.0, npm 11.10.1, uv 0.13.5;
the existing Python 3.12 environment and installed MCP extra were used. Earlier
Windows/Ubuntu WSL execution evidence remains historical and is linked through
the [evidence index](../evidence/README.md).

## Not performed in this documentation task

- Fresh clone/install on a clean Windows machine, WSL or bare Linux; ARM support.
- New credential entry, provider Test calls, live worker/improver/judge runs or a
  paid end-to-end rehearsal. The Web UI configuration flow was checked against
  source and existing evidence; real keys and secret files were not inspected.
- Full application/core test suites, a new UI build or new browser walkthrough.
  Product code and UI assets were unchanged.
- Publication, remote-revision synchronization or migration of existing state.

Use the [quick start](../README.md), check the actual `environment`/readiness
results on a new machine, and report any platform-specific gap without presenting
these offline tests as evidence of model capability or universal compatibility.
