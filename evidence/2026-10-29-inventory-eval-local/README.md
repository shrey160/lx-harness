# E-C local-agent inventory evaluation — live run, stopped (29–30 Oct 2026)

Approved by the user ("Approve, run E-C"); request reviewed in
[docs/inventory-local-eval-review.md](../../docs/inventory-local-eval-review.md).
Before the run, the server reported `gemma-4-E4B-it-GGUF` (Q4_K_M, 131k
context) as loaded. Windows controller, 34 minutes, exit 0.

**Outcome: stopped on 5 model errors after 8 of 33 samples, so there is no
criterion result.** Spend: **$0** (146 local chat calls: 141 settled and 5
unknown at $0; all perception came from the E-B cache, with no new SAM or
Transcribe calls). Ledger after: $1.744275772 spent, $18.150047428
remaining, 2725 settled, 6 unknown (the old T6 one plus these 5, none
holding cost).

## Cause of the errors: a 60-second client timeout, not the model or server

All 5 errors were "provider connection/JSON failed", all in the frozen
arm. In the Unsloth server log, each matches a `/v1/chat/completions`
request closed by the client (status 499) after 60.0 s, with llama-server
cancelling the task.

`SparkClient.chat` caps every transport call at `min(60, …)` seconds. So the
600-second local timeout in `LiveMetaTransport` (and the "600 s per call"
in the review) never took effect.

Gemma generated at about 15–30 tokens/s on this machine, so a reply near the
1,536-token cap needs about 50–100 s. The frozen arm loads the skill and
writes longer replies, so it hit the limit. This is a controller defect;
the frozen arm's errors say nothing about the harness.

## What the 8 samples show (all FSC count samples; descriptive only)

| | Base | Frozen |
|---|---|---|
| Runs finished | 8 / 8 | 3 / 8 (5 timed out) |
| Mean checklist score | 0.395 | 0.000 |
| Exact answers | 0 | 0 |

- **Base:** Gemma used the tools (transcribe, view, segment) and answered in
  JSON on 7 of 8; one answer was malformed JSON. Scores were 0–0.62.
- **Frozen, 3 finished runs, all scored 0:**
  - Two ended with Gemma's native tool-call syntax as the reply text, for
    example `<|channel>call:segment{concept:<|"|>peach<|"|>,photo:3}<tool_call|>`.
    The server did not turn it into a tool call, so the agent read it as
    the final answer.
  - One ended with prose instead of the JSON answer.

  Each did load the skill (`load_skill`) and then view and segment per
  photo.
- The tool-call leak is a separate local-model and server issue that the
  harness could address, for example with a guardrail on the answer format.
  It is not measured here.

## Next

1. Fix the client timeout so a local provider gets its transport's bound,
   with a test.
2. Run E-C again as a new reviewed retry under a new scope, with the same
   criterion.

Files: `eval.json` (8 rows, errors, summaries), `usage.json`, `started.json`.
