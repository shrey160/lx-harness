# Project history

This is the earlier product history, moved out of the README on 30 October 2026.
For how to use the product today, read the [README](../README.md). Statements
below were true when written; later work may have changed them.

## Product gates

The synthetic-only live Spark spike edited, harvested and selected a correct
candidate through the actual engine in Docker: 35 calls, $0.008337338 computed
token cost, then a zero-call, zero-execution resume. This established the small
JSON integration, not broader multimodal capability.

Application live flow (O-11) was approved on 28 October 2026 and executed: a spec
saved with mode `live` ran real Spark calls after spec, data, budget and
Contributor approval under the shared $20 ledger. The first live result was
honestly negative (no pixel-accurate boxes; base retained). See
`evidence/2026-10-28-live-app/live-report.json` and the
[application live review](application-live-review.md). The rejected generic
application transport was not added then; D-015 and D-016 later replaced that
flow with any provider in any role.

The first optimization template accepted only the constructed demo dataset or an
identical imported snapshot. The Image annotation demo still works this way.

## Offline records domain (PROD-002)

PROD-002 added a text-only synthetic order-normalization domain, strict graded
field checks and a five-trial offline runner. See the
[records contract](records-contract.md). The truth-blind no-op preview checks the
plumbing and records zero gains; it is not capability evidence. The approved
[live base-only calibration](records-calibration-review.md) scored 100% on all 40
held-out records ($0.004790572). The task has no measured headroom, so the five
live trials stayed disabled.

## Early contracts

- [Synthetic dataset and output verifier](demo-contract.md): four PNGs,
  scene-separated development and held-out splits, independent truth, exact text
  and object matching with one-pixel box-edge tolerance.
- `lx.budget`: SQLite reservations under one persistent cap (at most $20), atomic
  concurrent admission, role, phase and candidate records, nanodollar accounting,
  and retained reservations for unknown usage. A reported overrun is recorded and
  blocks new spending.
- `lx.spark`: injected transport, normalized Meta function calls, complete
  assistant and tool history, protocol-owned 429 retries, usage validation,
  pre-call reservation of the entire context-window cost ceiling, cancellation
  and time bounds. A synchronous in-flight transport may run until its timeout; a
  late cancelled response is accounted for and never returned for use.
- `lx.proposer`: preserves upstream `Proposer.call` audit, cache and harvest, and
  forces one upstream transport attempt. Tools read allowed session-context text
  files and write only inside `harness/`. Unexpected context filenames and links
  are rejected before upstream fingerprinting and copying.

Contributor prices were verified on 28 October 2026:
[pricing](https://dev.meta.ai/docs/pricing-rate-limits),
[chat protocol](https://dev.meta.ai/docs/protocols/chat-completions),
[tool protocol](https://dev.meta.ai/docs/tool-calling).
Input $0.10/M, cached input $0.002/M, output $0.20/M. Computed list-price cost is
distinct from provider billing. The old pilot's recorded $0.001 is retained as
conservative prior spend; its notes used Standard-mode prices.

## Early evidence

App evidence: [validation](../evidence/2026-10-28-app-preview/validation.json),
[core regression](../evidence/2026-10-28-app-preview/core-regression.json),
[browser result](../evidence/2026-10-28-app-preview/ui-results.png).

Live S1: see [the approved payload](live-spike-review.md) and
[live result](../evidence/2026-10-28-live-gate/live-report.json). The explicit
user approval superseded the earlier automatic-review hold for this fixed
synthetic experiment. `python -m lx.live` accepts no custom input path, loads the
existing key only at runtime, uses the fixed Meta endpoint, requires the approved
fixture checksum, and enforces 40 shared calls and the $20 cap. Generated code ran
in Docker under the core's managed job lifecycle. The transport refuses redirects
and withholds provider error bodies. No managed containers remained after the run.

The user confirmed library consumption of the core for S3 on 28 October 2026.
