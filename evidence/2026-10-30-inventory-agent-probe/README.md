# G1b agent probe — LFM3-VL-2B, live (30 Oct 2026)

Approved by the user ("go ahead"); request reviewed in
[docs/inventory-stream-local-review.md](../../docs/inventory-stream-local-review.md)
(section G1b, Windows controller as amended). Agent
`LiquidAI/LFM3-VL-2B-GGUF` (Q8_0 file loaded) on Unsloth Studio at
`http://127.0.0.1:8888/v1`. The key was read at runtime from the core `.env`
via `UNSLOTH_API_KEY`, never written to any artifact. About 28 s, exit 0.

**Spend $0**: 3 settled agent calls; 3,243 input and 3,879 output tokens; no
SAM or Transcribe calls. Ledger unchanged at $2.054275772 spent, 3579
settled.

## Result: **gate failed** — LFM3 never called a tool

| Check | Outcome |
|---|---|
| View check (`fsc-calibration-02`: call `view`, then describe photo 1) | no tool call; the reply ran to the 1,536-token cap and was cut off |
| Base run `fsc-calibration-02` (count) | turn 1: no tool call. The reply was the sample's own user message (sample id, photo sizes, "use view", "use transcribe") echoed back as JSON. Score 0 |
| Base run `mix-calibration-colourcount-01` (colour_count) | turn 1: no tool call; the reply ran to the 1,536-token cap and was cut off. Score 0 |

- The report's `answered: 1` counts the echoed JSON object. It is not an
  answer: every check fails, and `tools_used` is empty.
- Generation is fast, about 120–300 tokens/s by the server log (requests took
  4.5–12.8 s). So the cut-offs are long runaway replies, not slowness.
- Across 3 requests, with the same function tools that Gemma E4B used
  correctly in S5, T6 and E-C, LFM3-VL-2B produced no `tool_calls` at all.

## Reading

As served by Unsloth Studio with standard OpenAI-format tools, LFM3-VL-2B
cannot act as the agent for this task. Per the review's gate, stop before
G2.

Possible causes, not tested:
- the model or its chat template does not support OpenAI function calling
  through this server;
- it needs a different tool-call prompt format;
- default thinking or sampling settings lead to runaway text.

This is a wire and behaviour check, not a capability measurement.

Next options (the user's choice):
- return to Gemma 4 E4B, which is known to call the tools;
- try another local vision model with function calling;
- investigate LFM3's tool-call support in Unsloth first.

Files: `probe.json`, `usage.json`, `started.json`.
