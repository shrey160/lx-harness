# PROD-002 E4a calibration request — 28 October 2026

**Prepared for user review; not yet approved or executed.**

Run the base harness once on the 40 held-out fictional orders in
`evidence/2026-10-28-records-offline/worker_inputs/manifest.json`. All 60 worker
inputs are reviewable there; only the 40 held-out texts are sent in calibration.
The schema and scoring rules are in [records-contract.md](records-contract.md).
No optimization, discussion, tools, images, private input folder or other model.

- Model: `muse-spark-1.3-contributor` at the fixed Meta
  `https://api.meta.ai/v1/chat/completions` endpoint.
- Payload: task instructions and the generated fictional order text, plus the
  base instruction files. Controller truth and verifier results remain local.
- Bound: at most **40 calls**, no transport retries; **$0.50 calibration cap**
  inside the existing shared $20 ledger. Last recorded remaining: $19.963507074.
  The existing Contributor data-exposure disclosure applies to these synthetic inputs.
- Credentials: the existing registered Spark credential is loaded only by the
  WSL application runtime; no values enter task files, reports or logs.
- Gate: base mean field accuracy from 0.10 through 0.90 permits proposing E4b.
  Outside that range, revise task difficulty before requesting five live trials.
  Calibration itself cannot establish harness improvement.

Worker-input SHA256:
`c4c1917a69aa154fa304a936ec8bf9c1a5a6c596b64b2e95abeb047306ada47a`

Experiment SHA256 (inputs + controller truth + task contract + base prompt +
worker message builder + verifier source):
`d8bd6759014d6e767c62c951b676975fee0d0f7264c38f1f2219c3e70b229aa2`

After explicit user approval, the fixed controller command is:

```bash
.venv/bin/python -m lx.records_calibration --approve-dataset c4c1917a69aa154fa304a936ec8bf9c1a5a6c596b64b2e95abeb047306ada47a
```

The controller regenerates and checks both pinned hashes, requires the existing
historical ledger/checkpoint, and creates an exclusive one-run marker. It
refuses an empty replacement ledger or a repeated dispatch. It reuses the
existing transport without broadening its endpoint/model/tool capabilities.
E4b's proposed $2 total budget is separate and remains unapproved.

The original $0.10 proposal was infeasible: a default Spark request reserves
$0.1056768 (full input context ceiling plus 4,096 output tokens) before dispatch.
The $0.50 cap accommodates that reservation while keeping this request bounded;
actual costs are computed from reported tokens, not claimed as provider billing.
