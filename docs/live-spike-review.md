# Proposed bounded live spike — approval pending

Destination: `https://api.meta.ai/v1/chat/completions`.
Model: `muse-spark-1.3-contributor` only, acting as improver/selector/reflector.
Contributor content may be used by Meta for training (already accepted for this
synthetic/public product slice). No client or personal samples are proposed.

The next live experiment is one task (`records-1`), probe width `k=1`, through
the real engine. It sends these synthetic records:

```json
{"records":[{"id":"A-1","amount":"14.25"},{"id":"A-2","amount":"-20.10"},{"id":"A-3","amount":"3.85"}]}
```

It also sends the existing fixture's starting harness: the Python Decimal-based
JSON normalizer in `../harness2_portable_core/fixtures/custom_workflow.py`
(`BASE_SCRIPT`), its short harness instructions, the engine-generated task and
improvement/selection/reflection prompts, synthetic execution outputs and
trajectories, and candidate harness files/diffs generated during that run.
The known-bad starting script filters negative values out of its total; the
independent final answer is retained by the controller. Images/transcripts and
held-out truth from S2 are not part of this first live experiment.

The proposed runtime must generate fresh task context from that fixture only,
never accept an arbitrary data directory, validate context before copying or
hashing, and expose only the restricted context file tools. No shell or network
tool is exposed to the model. Generated task code runs in the existing Docker
worker with no provider credentials or network access. Only the WSL controller
calls Meta. API authentication is loaded by the application at runtime from the
existing local credential configuration; no key value is printed, stored in
the ledger, or included in prompts/audits.

The shared pilot cap remains $20, with at least $0.001 counted as prior spend.
Each call reserves the full declared input-context ceiling plus its maximum
output cost before dispatch. Unknown outcomes retain their reservation. A
small call-count limit and per-session deadline must also bound the experiment.
One repeat of the completed run must demonstrate cache reuse without spending.

Success requires a genuinely model-edited, harvested candidate, selected output
passing the independent check, and reconciled usage. Controlled-response
success is not accepted as live success. S3/frontend construction stays gated.

Approval requested: add and run this synthetic-only integration to the specified
Meta endpoint under the existing $20 cap, including runtime credential loading.
