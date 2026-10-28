# Application live data flow — O-11 approved and executed 28 Oct 2026

Status: APPROVED by the user's go-ahead to the reviewed continuation plan and
EXECUTED. The constrained transport (`lx.live_transport`) implements exactly
the flow below: fixed endpoint and model, approved payload shapes only, live
mode recorded per approved spec, shared $20 ledger, new job identities.
Result: the live run completed with an honest negative outcome — neither base
nor improved harness produced pixel-accurate boxes, the base was retained,
and held-out/fresh checks failed visibly. See
`evidence/2026-10-28-live-app/live-report.json`. Specs still default to
controlled preview; live requires an approved live spec.

Historical context: automatic approval review had rejected adding a generic
application transport (the fixed JSON-spike permission did not cover app
messages or multimodal inputs). That generic transport was never added; this
constrained flow was approved instead.

## Specific requested activation

Enable only `muse-spark-1.3-contributor` at
`https://api.meta.ai/v1/chat/completions`. No other models or arbitrary provider
URLs may receive live roles in this slice.

Only these application payloads are proposed:

1. Discussion: the goal and discussion messages explicitly entered/approved in
   the UI, plus sample inventory names/types/sizes/hashes. Start with metadata;
   do not silently attach file contents to discussion.
2. Worker: one approved constructed 96x64 PNG at a time, its supplied transcript,
   task/sample identity, and the frozen harness instructions. Images are inline
   PNG data, not user-chosen external URLs. The first domain remains restricted
   to the four known demo scenes (or an identical imported snapshot).
3. Improver: task instructions, development-scene metadata, candidate harness
   instructions, instruction diffs and actual development worker outputs/traces.
   Controller truth and held-out answers never enter the improver's context.
4. Reuse: a newly constructed scene from the same supported domain plus its
   supplied transcript, through the saved harness, after an explicit reuse
   action. The independent answer remains controller-only.

The snapshot, spec version, role assignments and budget must be approved in the
UI before a run. Editing a spec clears approval. Other imported folders can be
inventoried/discussed but cannot silently become a live optimization task.
Data is synthetic/public only; Contributor may use submitted content for
training. Sensitive/client/personal data remains outside this slice.

Use the registered Spark credential reference, or the existing runtime Spark
configuration if no key was registered. Only the WSL controller loads it.
No tool reads/displays the credential value, no response/log/spec/package
returns it, and workers receive no credential. The key store uses private native
WSL files; unsafe filesystem permissions refuse credential writes.

All real application calls must use the existing shared $20 pilot ledger,
which currently records $0.009337338 including conservative prior spend.
Remaining: $19.990662662. Do not reset the ledger. Reserve the declared full
context ceiling + bounded output before each call, reconcile known usage,
retain ambiguous reservations, and enforce each approved run cap atomically.
Add a bounded call count/deadline per run; protocol owns retries. Unknown live
cost must remain unknown until reported; preview usage stays visibly separate.
Live jobs must use new identities and must never reuse controlled-preview
answers, caches or claimed gains as live evidence.

## Verification after approval

Run one bounded built-in synthetic demo through discussion, approval, preflight,
improvement, held-out verification and saved-harness reuse. Record the actual
outcome even if the candidate does not improve. Preserve the starting harness,
failed candidates, per-role usage and limitations. Stop/resume must retain
budget and completed work. No additional live model or general-data support is
part of this request.

Approval requested: enable and test this specific application data flow to Meta
with runtime credential loading, using the existing shared $20 cap and the UI's
versioned spec/data/budget approval gate.
