# S5 live probe, part `local` (29 Oct 2026)

Approved by the user ("Unsloth ready, run") per
[voicecount-probe-review.md](../../docs/voicecount-probe-review.md). Ran once
from the Windows controller; exit 0. Wire-format check only (1 loop item).

- **Agent:** `gemma-4-E4B-it-GGUF` served by Unsloth at
  `http://127.0.0.1:8888/v1`, described like any other provider (no key, $0
  per token), same generated tools and base harness as Spark.
- **Result on loop-0005** (geese, whole, truth 30 ±1): transcribe →
  segment("geese") → count_boxes("geese") → `{"object":"geese","region":
  "whole","count":10}`. 4 chat calls (3,408 in / 537 out tokens), tool calls
  in the right order with valid arguments, valid answer JSON, scored 0.0
  because SAM itself found only 10 geese (see the meta probe).
- **Cloud tools:** both perception calls were served from the cache written
  by part `meta` (same audio, same concept), so no new SAM/Transcribe calls
  and **$0 spent**; ledger unchanged at $0.070598338.
- **Conclusion:** the swap path works end to end: a local OpenAI-compatible
  model accepted the image and the function tools, and drove the cloud
  tools through the unchanged harness. Its accuracy is not measured here.

Files: [probe.json](probe.json), [usage.json](usage.json).
