# PROD-002 offline evidence — 28 October 2026

- `validation.json` / `product-tests.log`: **113/113 product tests pass** in the
  installed WSL Python 3.12.3 environment; includes existing application flows,
  new domain/verifier, grading/retention, cache, calibration approval guards and
  phase accounting. All provider transports are doubles/mocks; no live calls.
- `offline-trials.json`: five fresh real-core trials, each with 20 dev/40 held-out
  examples, one adaptation item, 124 worker executions and four no-op improver/
  reflection calls (640 simulated calls in total). Every worker emits `{}`;
  every trial retains base with zero gain. Criterion fails as expected.
- `worker_inputs/manifest.json`: the 60 reviewable fictional source texts.
- `controller_truth/ground_truth.json`: separately stored synthetic answer key;
  never staged for worker/improver requests.

These are offline wiring/negative-control results, not model capability evidence.
Live calls/spend: **0 / $0**. No core/vendor edits; the separate historical core
57-test/demo/crash campaign was not rerun for this product-only change.

Worker-input SHA256:
`c4c1917a69aa154fa304a936ec8bf9c1a5a6c596b64b2e95abeb047306ada47a`

Experiment SHA256 (including task/worker contract and verifier source):
`d8bd6759014d6e767c62c951b676975fee0d0f7264c38f1f2219c3e70b229aa2`

The first full trial attempt on NTFS was interrupted for slow file handling;
the completed recorded campaign used native temporary storage and exported
only these path-free summaries and synthetic data. One added contract-cache
test initially failed, exposing a real divergence between task metadata and
worker instructions. The contract now has one definition and all final tests
and the five-trial evidence were rerun successfully after that fix.
