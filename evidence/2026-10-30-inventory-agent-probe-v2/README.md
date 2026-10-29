# G1b v2 agent probe — LFM3-VL-2B, thinking off vs on (30 Oct 2026)

Approved by the user ("Probe both", then "check and resume" after the machine
shut down). Reviewed in
[docs/inventory-stream-local-review.md](../../docs/inventory-stream-local-review.md)
(section G1b v2).
- Agent: `LiquidAI/LFM3-VL-2B-GGUF` (Q8_0) on Unsloth Studio at
  `http://127.0.0.1:8888/v1`, Windows controller.
- Key: read at runtime from the core `.env` via `UNSLOTH_API_KEY`.
- Same probe as v1: a view check on `fsc-calibration-02`, then base-harness
  runs on `fsc-calibration-02` (count) and `mix-calibration-colourcount-01`
  (colour_count).

**Total spend $0.0025** (one new SAM pair in the thinking-off view check).
Ledger after: $2.056775772 spent, 3598 settled.

## Results

| | Thinking off (cap 1,536) | Thinking on (cap 8,192) |
|---|---|---|
| Wall time | 10 s | 10.5 min |
| Agent calls / output tokens | 5 / 568 | 10 / 14,643 (~1.5k per call, ~60 s per call) |
| View check | called `view` (then `segment`), described photo 1 | called `view`, described photo 1 ("cupcakes … 16 instances") |
| Count run (`fsc-calibration-02`) | **no tool calls**; invented malformed JSON at once; 0.00 | `transcribe`, `view` 1, `view` 2, then a contract-shaped answer; **0.25** |
| Colour run (`mix-calibration-colourcount-01`) | **no tool calls**; invented malformed JSON at once; 0.00 | `transcribe`, `view` 1, then a contract-shaped answer (no orange items found); **0.375** |
| Cut-offs | none | none |
| Tools used in runs | none | `transcribe`, `view` (no `segment`) |

`answered` (contract-shaped answers): thinking off 0 of 2, thinking on 2 of 2.

**Failures that remain with thinking on:**
- the `answer` field is written as an object (`{"count": 41, …}`) instead of
  a number, so the schema, consistency and answer checks fail. Gemma made
  the same error in E-C;
- not every photo was viewed (count run: 2 of 3; colour run: 1 of 3);
- the colour request was recorded as type `count`;
- `segment` was never called in a run.

All of these are the kind of thing a learned skill addresses.

## Interrupted attempt

The first thinking-on run (scope `inventory-agent-probe-v2-thinking-on`)
stopped when the machine shut down. It had 3 settled view-check calls (1,783,
116 and 2,237 output tokens) and 1 call still reserved at $0 for the first
base-run turn. No report was written; only `started.json` is kept, under
`thinking-on-interrupted/`. The rerun used `--attempt 2` (`28c7143`), which
changes only the scope name.

## Reading

- **Thinking off:** LFM3 follows an explicit instruction (the view check) but
  skips the tools entirely under the bare base prompt. Base scores would be
  about 0 everywhere. That is maximal headroom, but it is unclear whether a
  2B model without thinking can follow a learned skill.
- **Thinking on:** LFM3 works the task unaided (transcribe, view, a
  structured answer) and its failures are format and coverage, the
  T8/E-C pattern. It is slow, about 60 s per call. G2's roughly 255 agent
  calls would take about 4–5 hours.

Either mode is mechanically viable; the choice for G2 is the user's.

Files: `thinking-off/`, `thinking-on-attempt2/` (each `probe.json`,
`usage.json`, `started.json`), and `thinking-on-interrupted/started.json`.
