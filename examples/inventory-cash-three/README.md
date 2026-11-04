# Inventory and cash: three samples

A ready-to-use `photo-request` dataset for LX. The worker receives numbered
photos, an audio-only shop transaction and actual starting inventory/account
JSON. It returns updated stock and cash plus one box per changed object.
The [agent runbook](AGENT.md) explains the exact LX setup and test sequence.

Eight photos and three conversational WAV clips are included. No downloads,
original photo folder, speech model or dataset generator are needed to use them.
The audio is **Kokoro synthetic speech**, not a human recording. Transactions and
USD prices are constructed test data; printed package prices do not set the price.

| Sample | Photos | Scenario | Challenge |
| --- | ---: | --- | --- |
| sample-01 | 3 | Purchase cups for stock, paid from the till | Count the target across mixed scenes; preserve scissors and other stock |
| sample-02 | 3 | Sell bottles, customer pays cash | Include drink bottles; preserve chocolates and other stock |
| sample-03 | 2 | Proposed pencil restock is cancelled; forks are sold instead | Follow the final spoken correction; preserve pencils and nearby spoons |

The third sample is designed to be harder. It was already correct at baseline
in the historical Spark run; that single result does not establish benchmark
difficulty or generalization. All three samples are learning-stream inputs,
not a held-out evaluation set.

## Verify, copy or make a ZIP

Run from `main_LX` with its installed Python environment. Verification is offline:
it checks file hashes, decodes images/audio, compares starting JSON with controller
truth and tests the included trusted checker on correct and incorrect answers.
It does not initialize LX, approve work or call a provider.

Windows PowerShell:

```powershell
.venv-win\Scripts\python.exe examples\inventory-cash-three\prepare.py verify
.venv-win\Scripts\python.exe examples\inventory-cash-three\prepare.py copy --destination ..\lx-inventory-test
```

Linux/WSL:

```bash
.venv/bin/python examples/inventory-cash-three/prepare.py verify
.venv/bin/python examples/inventory-cash-three/prepare.py copy --destination ../lx-inventory-test
```

Both platforms accept `pack --destination PATH_TO_NEW_ZIP` in place of `copy`.
For example, from `main_LX`:

```powershell
.venv-win\Scripts\python.exe examples\inventory-cash-three\prepare.py pack --destination ..\inventory-cash-three.zip
```

Copy/pack always verify first, refuse existing destinations and include only
manifest-listed files. ZIP contents start at this example's root: extract into a
new `lx-inventory-test` folder so `samples/` is directly inside it. Use that whole
folder as the LX workspace, not its `samples/` subfolder. The extracted bundle
also includes this runbook and `prepare.py`; it works independently of the source
checkout when run with LX's Python (which supplies Pillow).

## Package layout and input separation

```text
inventory-cash-three/
  AGENT.md                  agent setup and acceptance steps
  task.json                 task fields for spec_draft; not auto-imported by init
  eval/check.py             eight objective checks; load with use_eval_file
  samples/sample-01/        same layout for sample-02 and sample-03
    photos/1.jpg ...        3 / 3 / 2 photos respectively
    request.wav            the only spoken request supplied to the worker
    input.json             starting inventory and cash; LX passes this directly
    truth.json             controller-only expected transaction/boxes
  reviewer/                 voice scripts and optional judge criteria
  attribution.json          image sources, transformations and voice provenance
  MEDIA.md                  asset terms and attribution
  manifest.json             versioned file sizes and SHA-256 hashes
  prepare.py                offline verify / fresh copy / ZIP
```

`truth.json`, reviewer material and these instructions are for setup/verification,
not worker prompts. LX's normal template freezes worker inputs separately from
truth. Do not attach this entire directory to a model, insert transcripts into
`input.json`, or expose expected quantities/answers in task text or improver hints.
The optional judge file is deliberately outside `eval/` until explicitly enabled,
so the default objective-only setup has no unused-judge-file drift.

## What a test proves

- `prepare.py verify`: packaged files and checker are internally consistent.
- LX `check_test`: the checker works through LX's configured script sandbox.
- LX `ready`: validates data/spec and reports missing provider/tool configuration;
  it does not verify credentials with paid requests or prove model quality.
- A requested live trial: one sample's worker/tool path works. A full run exercises
  all three learning samples and the automatic harness loop.

The objective check covers item, signed change, updated quantity, unchanged stock,
unit prices, cash movement, ending account and one-to-one object boxes (IoU at
least 0.5, inclusive coordinates, actual photo bounds). Scores verify the answer;
they do **not** prove Transcribe or SAM was called. Inspect trajectories for tool
use. Vision can sometimes count without segmentation.

Historical reference: the 3 November 2026 Spark experiment `0f64c549d33c` passed
24/24 baseline and final objective checks and 30/30 judge criteria. Across twelve
executions it passed 95/96 objective checks: one discarded intermediate candidate
had a cash arithmetic error. The final package was unchanged base. These are saved
observations, not expected results guaranteed for a new model or installation.
Full review in the repository: `evidence/2026-11-03-three-sample-spark-run/`.

See [MEDIA.md](MEDIA.md) before redistributing the media. Original LX code/docs
retain Apache-2.0; third-party photos retain their asset-specific terms.
