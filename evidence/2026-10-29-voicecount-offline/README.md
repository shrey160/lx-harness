# Voicecount dataset and offline preview measurement (29 Oct 2026)

Offline only: no Spark, SAM or Transcribe calls and no spend. The agent,
improver and tools were deterministic wire-format doubles, so the preview
"gain" below is plumbing evidence, not capability evidence.

## Dataset `voicecount-v1`

- Source: FSC-147 (MIT repo, cvlab-stonybrook/LearningToCountEverything):
  annotations from GitHub, 384-px images from the official Google Drive
  archive (1,533,803,725 bytes). Speech: Kokoro-82M int8 via kokoro-onnx
  0.6.1, six English voices, 24 kHz mono 16-bit, deterministic (same hash on
  repeat), 304 s in total.
- Splits: loop 40 (FSC train), verify 5, test 40, calibration 20 (disjoint
  FSC val/test images). Each split takes a third of its items from each count
  band (10-24, 25-49, 50-120); about 30% of requests name a half of the image.
- Truth: dots in the named region; at most 3 dots within 2% of the dividing
  line, each widening that item's tolerance by one (tolerance range 1-6).
- One pair of items has identical request text and voice, so identical audio;
  harmless (the same transcript is correct for both).
- Media, truth and request text stay under `main_LX/.lx/` (gitignored);
  [dataset-summary.json](dataset-summary.json) pins every file and download by
  SHA-256. `dataset_sha256` b306c42f…, `experiment_sha256` 129449c2….

Rebuild: `build_voicecount_dataset(root, fsc, fsc/images, 40, 5, 40, 20)`,
then `scripts/synthesize_voicecount.py` in the Kokoro tools environment, then
`finalize_voicecount(root)`.

## Preview measurement ([call-measurement.json](call-measurement.json))

5 trials, adaptation on 3 loop items, plus the final test (base + 5
packages x 40 items = 240 executions):

- Agent chat calls: 2,720 in total (about 3-4 turns per execution).
- Improver sessions: 90; reflections: 15.
- Distinct tool calls after caching: 85 SAM, 84 Transcribe (repeats were
  served from the perception cache at no cost).

Planning consequences for the live steps:

- The existing 200-call run limit is far too low: a live trial needs roughly
  350-550 agent calls and the final test about 900; each live request must
  state measured ceilings.
- The SAM cost is driven by distinct (image, concept) pairs, not executions;
  a live agent that varies its noun phrases will make more distinct calls
  than this double.
- Token costs here are simulated (fixed 120/32 tokens per reply); live image
  and tool-result tokens will be much larger and are measured in S5/S6.
