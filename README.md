# LX harness product

The first product gates are in progress. The images + supplied-transcripts
verifier is implemented. A Spark-shaped, injected transport passes controlled
tests through the actual pinned Harness² engine. Live Spark improvement has
not been tested; there is no live network transport or credential loader here.
The frontend and backend application remain gated on that live spike.

## Offline setup and checks

Use Python 3.12 or 3.13 inside WSL/Linux with the existing Docker setup.
Keep this repository beside `harness2_portable_core/`.

```bash
uv sync --locked
.venv/bin/python scripts/validate_product.py --evidence evidence/local-validation
.venv/bin/python -m lx.spike --core-source ../harness2_portable_core --output .lx/controlled-spike
```

Repeat the last command with the same output directory: completed task work is
reused without fresh provider-double calls. These calls simulate token usage;
they do not spend credits. The test suite separately verifies the proposer
session cache and harvested workspace, not only whole-task skipping.

The core is a declared local library dependency; the engine remains its pinned
vendored dependency. `uv.lock` uses relative paths. No core source changes are
required. Tests use the existing synthetic core fixture from the sibling source
repository. S3's final application/core-consumption decision remains open.

## Implemented contracts

- [Synthetic dataset and output verifier](docs/demo-contract.md): four PNGs,
  scene-separated development/held-out splits, independent truth, exact text
  and object matching with one-pixel box-edge tolerance.
- `lx.budget`: SQLite reservations under one persistent cap (at most $20),
  atomic concurrent admission, role/phase/candidate records, nanodollar
  accounting, and retained reservations for unknown usage. A reported overrun
  is recorded and blocks new spending. New live jobs must share one ledger;
  creating another database is not a mechanism for resetting the pilot cap.
- `lx.spark`: injected transport, normalized Meta function calls, complete
  assistant/tool history, protocol-owned 429 retries, usage validation,
  pre-call reservation of the entire context-window cost ceiling, cancellation
  and time bounds. A synchronous in-flight transport may run until its timeout;
  a late cancelled response is accounted for and never returned for use.
- `lx.proposer`: preserves upstream `Proposer.call` audit/cache/harvest and
  forces one upstream transport attempt. Tools read allowed session-context
  text files and write only inside `harness/`. Unexpected context filenames
  and links are rejected before upstream fingerprinting/copying.

Contributor prices were verified on 28 October 2026:
[pricing](https://dev.meta.ai/docs/pricing-rate-limits),
[chat protocol](https://dev.meta.ai/docs/protocols/chat-completions),
[tool protocol](https://dev.meta.ai/docs/tool-calling).
Input $0.10/M, cached input $0.002/M, output $0.20/M. Computed list-price
cost is distinct from provider billing. The old pilot's recorded $0.001 is
retained as conservative prior spend; its notes used Standard-mode prices.

## Remaining gate

See [the proposed live spike payload](docs/live-spike-review.md).
Automatic approval review rejected the earlier generic live transport because
it could send potentially sensitive context and load caller-selected credential
files. No such transport was added. Explicit approval of the bounded synthetic
payload/destination is pending before adding and running live integration.
