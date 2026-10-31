# lx: the product package

Everything LX runs is here. The [main README](../../README.md) covers how to use it;
this page says where the code is.

`engine.py` is the single service layer. `server.py` is a thin HTTP layer over it, and
the CLI and MCP server planned for later phases will sit beside it. A run starts as a
separate `worker.py` process, so credentials load there and never in task workers.

## Application

| File | Purpose |
|---|---|
| `engine.py` | The engine API: workspaces, specs, approval, runs, results, provider tests |
| `orchestration.py` | What an orchestrator may do; the built-in one, the CLI and the MCP server share it (D-024) |
| `orchestrator.py` | The built-in orchestrator: a bounded conversation over those actions |
| `server.py` | Loopback-only HTTP API and the built React UI |
| `worker.py` | Controller subprocess for one managed run |
| `workspace.py` | A user folder and the `.lx/` directory inside it |
| `store.py` | Project and job records in user-level state |
| `intake.py` | Safe, hashed snapshots of sample folders and ZIP files |
| `limits.py` | User-set run limits: defaults and hard bounds (D-017) |
| `health.py` | Run health, early stop and failure reasons |

## Tasks and scoring

| File | Purpose |
|---|---|
| `tasks.py` | General task spec: a run is data in the spec plus a check file |
| `taskrun.py` | One spec-driven run, graded by its check |
| `text_task.py` | The `text-request` template: its samples, dataset, adapter and preview double |
| `records_check.py` | The order-records check: a standalone file, standard library only |
| `checks.py` | Runs a check in the sandbox and self-tests it before approval |
| `inventory_check.py` | The inventory check: a standalone file, standard library only |
| `sandbox.py` | Throwaway sandbox for checks and helper scripts: Docker or Linux `unshare` |

## Models and spending

| File | Purpose |
|---|---|
| `registry.py` | Provider entries, with keys held in the keychain |
| `keychain.py` | The operating system's keychain |
| `providers.py` | Where a provider is called and what it costs; endpoint rules |
| `model_list.py` | Reads a provider's model list, so its details need no typing |
| `spark.py` | The chat protocol; the transport is injected, so it is offline-testable |
| `live_transport.py` | The constrained chat transport for live calls |
| `perception.py` | Model tools: one transport and client, and a format per wire shape. SAM 3.1 and Muse Voice Transcribe are the first two; each can be switched off (`tasks.py`) |
| `budget.py` | Persistent, concurrency-safe spending ledger |
| `usage.py` | Scopes a ledger to an approved run budget and summarizes usage |

## The loop

| File | Purpose |
|---|---|
| `agent.py` | Agent tool loop: the acting model calls registered models as tools |
| `components.py` | Skills and helper scripts, the harness parts beyond the always-on files |
| `proposer.py` | Session hook that keeps the pinned engine's cache, audit and harvest |
| `workflow.py` | Image demo adapter and the controlled preview |

## Domains

Each domain has a contract and verifier, and a workflow file that plugs it into the loop.

| Files | Domain |
|---|---|
| `inventory.py`, `inventory_workflow.py`, `inventory_data.py`, `inventory_cutouts.py`, `cutout_art.py` | Photo inventory: the current task and its dataset builders |
| `domain.py` | Image and transcript demo |
| `records.py`, `records_workflow.py` | Noisy order normalization (PROD-002) |
| `eventlog.py`, `eventlog_workflow.py` | Order event-log reconciliation |
| `voicecount.py`, `voicecount_workflow.py` | Spoken counting (PROD-003) |

## Fixed experiments

Each of these is one reviewed experiment with fixed inputs and limits. Those that make
live calls need the user's explicit approval. Do not reuse them as general entry
points, and do not change them to rerun: a rerun needs a new scope. Their reviews are
in [docs](../../docs/README.md) and their results in [evidence](../../evidence/README.md).

| Files | Experiment |
|---|---|
| `live.py` | S1 live JSON spike; also holds the runtime key loader |
| `spike.py` | S1 controlled-response integration, no network |
| `trials.py` | Offline trial and calibration mechanics |
| `records_calibration.py` | PROD-002 calibration |
| `eventlog_calibration.py` | R3 difficulty ladder |
| `voicecount_probe.py`, `voicecount_calibration.py`, `voicecount_trials.py` | S5, S6 and S7 |
| `inventory_probe.py`, `inventory_calibration.py`, `inventory_stream.py` | T6, T7 and T8 |
| `inventory_eval.py`, `inventory_local_eval.py` | E-B and E-C |
| `inventory_stream_local.py`, `inventory_agent_probe.py` | G1, G2 and G1b |
| `provider_probe.py` | Three-call compatibility probe of a hosted endpoint |

## Rules for changes

- Text I/O uses UTF-8 explicitly, because Windows has another default.
- Truth reaches only the check, never the worker or the improver.
- Nothing prints or logs a credential. Error messages withhold provider bodies.
- Anything settable belongs in `limits.py` with a hard bound. Sandbox rules, approval
  and request size bounds are not settable.
