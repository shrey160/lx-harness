# S5 live probe, part `meta` (29 Oct 2026)

Approved by the user ("Approve, run meta") per
[voicecount-probe-review.md](../../docs/voicecount-probe-review.md). Ran once;
exit 0. Wire-format check only, not capability evidence (2 loop items).

## Spend

| | Calls | USD |
|---|---|---|
| Transcribe | 2 | 0.000212 |
| SAM 3.1 | 8 images | 0.020000 |
| Spark agent | 9 chat calls (16,764 in / 1,765 out tokens) | 0.001321742 |
| **Total** | 19 settled, 0 unknown | **0.021533742** |

Shared ledger: $0.049064596 → **$0.070598338** spent, $19.929401662
remaining, 253 settled, 0 unknown. 3 perception cache hits.

## Findings

1. **SAM reply shape (was undocumented):** a full Responses object
   (`object: "response"`, `status: "completed"`); the segmentation text is at
   `output[0].content[0]` with `type: "output_text"`. `lx.perception.sam_text`
   already handled it. `usage` carries `images_processed: 1` and zero tokens.
   Box tokens carry `c` when `include_confidence` is set, and `w`/`h` equal
   the source image size. The reply shows `"store": true` (Meta keeps the
   response); consider sending `store: false` after checking it is accepted.
2. **Transcribe:** the request `{"model", "audioEncoding": "WAV", "mode":
   "PUSH_TO_TALK"}` is accepted. Both Kokoro clips transcribed exactly
   (word error rate 0.0); `turns` is empty in this mode, as documented.
3. **SAM accuracy differs by scene:**
   - `geese` on loop-0005: 10 instances vs 30 true; confidences 0.50-0.54
     (small distant birds).
   - `cans` on loop-0036: 66 instances vs 36 true (whole image; the request
     asks for the bottom half); confidences 0.63-0.92.
   - absent `giraffes`: 0 instances, empty text, handled.
   So raw SAM output is neither a ready answer nor useless: concept wording,
   thresholds, deduplication and the region filter all matter — the levers
   the harness is meant to learn.
4. **Spark as agent:** accepts a JPEG plus the generated tool set and calls
   tools in a sensible order.
   - loop-0036: transcribe → segment("can") → count_boxes(region bottom) →
     `{"object":"cans","region":"bottom","count":36}`: correct.
   - loop-0005: transcribe → segment with five different phrasings ("geese",
     "bird", "flying goose", ...) → stopped by the probe's 6-tool-call cap
     with no answer (scored 0). A missing "stop and answer" rule is exactly
     the kind of process failure the improver can see in traces.
5. **Cost model update:** Spark worker calls cost about $0.00015 each here
   (about 1,900 input tokens); SAM dominates live cost at $0.0025 per distinct
   (image, concept) pair, and the agent explores several phrasings.

Files: [probe.json](probe.json) (results and raw reply shapes; mask excerpts
are image data, no credentials), [usage.json](usage.json).
