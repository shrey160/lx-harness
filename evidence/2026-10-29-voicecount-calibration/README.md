# S6 voicecount calibration (29 Oct 2026)

Approved by the user ("Approve, run S6") per
[voicecount-calibration-review.md](../../docs/voicecount-calibration-review.md).
Ran once; exit 0; no model errors. Base harness only: no improver, no
candidate.

## Result (20 calibration items, primary metric)

| Arm | Mean | Passed | Whole (15) | Half-image (5) |
|---|---|---|---|---|
| A0 Spark alone (request as text, no tools) | **0.45** | 9/20 | 7/15 | 2/5 |
| A1 base harness (audio only via tools) | **0.80** | 16/20 | 12/15 | 4/5 |

- **Gate:** A1 = 0.80 is inside the pre-registered 0.30-0.80 band (at its
  upper edge), so the pre-registered decision is **prepare S7**.
- **Tools beat Spark's own eyes:** +0.35 on the same items. A0 undercounts
  dense scenes (apples 41 vs 66, marbles 75 vs 108, grapes 44 vs 60).
- **A1 behaviour:** every item went transcribe → segment → count_boxes
  (twice a second segment), 4-5 turns, no stops, no tool errors. The agent
  never set `min_confidence` or `dedupe_iou`; it used the defaults.

## A1 failures (the headroom)

| Item | Request | Truth | A1 | Likely cause |
|---|---|---|---|---|
| calibration-0006 | apples, whole | 66 ±3 | 62 | SAM misses |
| calibration-0007 | milk cartons, whole | 59 ±2 | 80 | over-segmentation; no threshold or dedupe used |
| calibration-0009 | deers, whole | 21 ±1 | 16 | SAM misses |
| calibration-0013 | books, top half | 89 ±4 | 14 | noun phrase or segmentation failure on dense shelves |

Two of four failures look fixable by harness behaviour (filtering, concept
wording); two look SAM-limited. The headroom is therefore thin: the
pre-registered S7 criterion (+0.10 on the final test in 4 of 5 trials) needs
the improved harness to fix about half of the remaining errors on unseen
items.

## Spend

144 settled calls, 0 unknown, **$0.07627006** (SAM 22 images $0.055;
Transcribe 20 clips $0.00302; Spark 102 calls $0.01825006). Shared ledger
now **$0.146868398 spent, $19.853131602 remaining, 401 settled**.

Files: [calibration.json](calibration.json) (per-item answers, scores, tools,
stops), [usage.json](usage.json). Traces stay under
`main_LX/.lx/voicecount-calibration-v1/A1/` (local).
