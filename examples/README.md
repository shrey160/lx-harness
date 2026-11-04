# Example datasets

Ready-to-copy inputs for testing LX after [installation](../docs/installation.md).
Each example owns its samples, task, scoring check, media attribution and agent
instructions. Copy it to a fresh task workspace outside this checkout before
using LX; keep the versioned example unchanged for repeatable comparisons.

| Example | What it exercises | Test options |
| --- | --- | --- |
| [Inventory and cash: three samples](inventory-cash-three/README.md) | Photos, conversational audio, starting inventory JSON, purchases/sales and a corrected request | Offline integrity/checker verification; LX draft/check/readiness; requested live trial or full loop |

For a provider-free end-to-end installation check, use the built-in
[synthetic preview](../docs/agent-workflow.md#first-check-synthetic-preview).
The photo/audio example needs real providers for execution; copying or verifying
it makes no provider calls and is not permission to start a live run.

An installation agent should start with the example's `AGENT.md`. Future examples
should include a stable ID/version, input/output contract, fresh-copy instructions,
independent controller truth/check, file hashes, media provenance, validation and
explicit offline versus live expectations. Never ship keys, approvals, user-state
databases, cached tool responses or run folders as part of a dataset.
