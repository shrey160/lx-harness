# Documentation

Start with the [main README](../README.md) for the complete first-run path: clone
inside your project, configure the Web UI, connect an agent and inspect a task run.
The guides below describe current behavior. Contracts and experiment reviews are
dated records of what was proposed/approved then; preserve them even where newer
behavior supersedes their assumptions.

## Current guides

Ready-to-copy test tasks are in [Example datasets](../examples/README.md), starting
with the [three-sample inventory/cash example](../examples/inventory-cash-three/README.md)
and its installing-agent runbook.

[Fresh-clone installation evidence](../evidence/2026-11-04-fresh-install/README.md)
records Windows/Ubuntu WSL2 setup, UI/MCP, synthetic previews and restart checks.

| File | Purpose |
|---|---|
| [installation.md](installation.md) | Windows/Linux prerequisites, nested clones, separate core, launch, updates and troubleshooting |
| [configuration.md](configuration.md) | Web UI keys/providers/tools, budgets, persistence, access and workspace lifecycle |
| [agent-mcp.md](agent-mcp.md) | Connect an external agent to LX; stdio setup, current tools, permissions and errors |
| [agent-workflow.md](agent-workflow.md) | Person/agent responsibilities, first preview, task preparation, execution and result review |
| [tasks.md](tasks.md) | Writing a task: samples, the spec, the scoring check, trying and reading a run |
| [environment.md](environment.md) | Preparing an environment: keys, Python libraries and system tools for a task |
| [mcp.md](mcp.md) | Give LX workers tools from other MCP servers; separate from connecting an agent to LX |
| [architecture.md](architecture.md) | Host-project/LX/core boundaries, execution/state and development map |
| [documentation-validation.md](documentation-validation.md) | Dated source revisions, documentation checks, runtime evidence and unverified setup cases |
| [history.md](history.md) | Earlier product history, moved out of the main README |

## Contracts

| File | Task |
|---|---|
| [demo-contract.md](demo-contract.md) | Image and transcript demo |
| [records-contract.md](records-contract.md) | Noisy order normalization |
| [eventlog-contract.md](eventlog-contract.md) | Event-log reconciliation |

## Live experiment reviews

| File | Experiment |
|---|---|
| [live-spike-review.md](live-spike-review.md) | S1 live JSON spike |
| [application-live-review.md](application-live-review.md) | O-11 application flow; superseded by D-015 and D-016 |
| [records-calibration-review.md](records-calibration-review.md) | Records calibration |
| [records-accounting-audit.md](records-accounting-audit.md) | Audit of old call counts and ledger opening |
| [eventlog-calibration-review.md](eventlog-calibration-review.md) | Event-log calibration |
| [voicecount-probe-review.md](voicecount-probe-review.md) | S5 probe |
| [voicecount-calibration-review.md](voicecount-calibration-review.md) | S6 calibration |
| [voicecount-trials-review.md](voicecount-trials-review.md) | S7 trials |
| [inventory-probe-review.md](inventory-probe-review.md) | T6 probe |
| [inventory-calibration-review.md](inventory-calibration-review.md) | T7 calibration |
| [inventory-stream-review.md](inventory-stream-review.md) | T8 E-A stream |
| [spark-calibration-review.md](spark-calibration-review.md) | Spark calibration on the new agent loop; run 31 Oct |
| [inventory-eval-review.md](inventory-eval-review.md) | E-B evaluation |
| [inventory-local-eval-review.md](inventory-local-eval-review.md) | E-C evaluation and its retry |
| [inventory-stream-local-review.md](inventory-stream-local-review.md) | G1b probe and G2 stream |
| [inventory-rerun-review.md](inventory-rerun-review.md) | Graded inventory rerun on OpenRouter; prepared, not run |
