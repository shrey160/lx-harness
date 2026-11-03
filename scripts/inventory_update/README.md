# Inventory and cash datasets

The latest three-sample variant is built by `expand.py`. It preserves the
original two-sample dataset and creates `.lx/inventory-update-three/`:

| Sample | Photos | Transaction | Expected stock / cash |
|---|---:|---|---|
| 01 | Original two + user31132 (scissors/two cups) | Buy all7 cups | 11 cups /32.50 USD |
| 02 | Original two + user31133 (chocolate/two bottled drinks) | Sell all5 bottles | 5 bottles /41.25 USD |
| 03 | User31130 (stationery),31131 (cutlery) | Cancel pencil restock; sell2 forks | 4 forks /44.50 USD |

The third conversational note changes its mind about the item, so an executor
must preserve the cancelled pencils and unrelated spoons. No audio names a
count, JSON field or tool. These are constructed transactions and USD prices,
not statements about the real photographs' purchase history or printed prices.
Every inventory key has a price. Earphones are one set. User photos are rotated
upright, resized to768x1024 and stripped of metadata; source pixels are not
cropped or synthesized. Original files and prior LX runs remain unchanged.

Rebuild from the repository root, supplying the four original photos:

```powershell
python scripts/inventory_update/expand.py build --photos-dir PATH_TO_PHOTOS
python scripts/inventory_update/expand.py voice
python scripts/inventory_update/expand.py review
python scripts/inventory_update/expand.py verify
python scripts/inventory_update/expand.py pack
```

Use `--root` for a fresh output folder and `--source` for the original JSON
dataset. The builder refuses existing samples. Voice uses the same offline
Kokoro installation as the original builder. Review HTML includes audio,
annotated photos and expected answers; the ZIP excludes runtime state.
`truth.json` may now carry controller-only `photo_sizes` so the standalone
check validates actual portrait dimensions and photo3. The six-field answer
stays the same; legacy truths default to two1024x768 photos. Old approved
snapshots keep their own check files and are not rewritten.

## Original two-sample variant

Two small photo/audio transactions for the local LFM3-VL-2B worker. A stock
clerk receives an inventory with unit prices and a cash balance as direct JSON, two photos of
distinct objects, and a spoken transaction request. The answer updates stock
and cash, preserves unrelated stock and prices, and gives a box per changed
object. These are constructed test scenes, not photographs of real transactions.

| Sample | Audio request | Changed stock | Cash |
|---|---|---|---|
| 01 | Purchase the cups shown and pay from the account | 4 + (2 + 3) = 9 cups | $50.00 - 5 x $2.50 = $37.50 |
| 02 | Sell the bottles shown and receive money in the account | 10 - (1 + 2) = 7 bottles | $25.00 + 3 x $3.25 = $34.75 |

The audio names the transaction and target, never the object count. Each scene
has one unrelated object. All objects are separate and there is no overlap
between photos. Unit prices are both purchase/sale transaction prices here;
there is no margin calculation, tax, fee, discount or other cash movement.

`input.json` contains the starting inventory and account. LX supplies this
object directly as `input` in the worker's user message. There are exactly two
object photos, with no inventory-sheet photo and no visual text-reading step.
The expected updated answer is separate in controller-only `truth.json`.

Transcribe provides the request and SAM provides instance boxes. A vision
model could count without SAM: the output check tests localization, not which
tool was called. Actual tool use, time and base score remain unmeasured until
a person authorizes a trial. No 0.4 baseline or improvement is claimed.

## Rebuild offline

From the repository root, using the product's Python environment:

```powershell
python scripts/inventory_update/build.py build
python scripts/inventory_update/build.py voice
python scripts/inventory_update/build.py review
python scripts/inventory_update/build.py verify
python scripts/inventory_update/build.py pack
```

The default output is `.lx/inventory-update-json/`. `--root` chooses another
fresh folder. Build refuses to overwrite samples. The existing COCO downloads,
Pillow, local Kokoro model/voices and `.lx/tts-venv` are prerequisites. No
downloads or provider calls occur. Speech is synthesized offline with Kokoro
and phonemizer; its temporary files stay under the generated review folder.

`samples/sample-*/` holds `photos/1.jpg` and `2.jpg`, `request.wav`,
`input.json` and controller-only `truth.json`. The root has `task.json`,
`eval/check.py`, `attribution.json`, `validation.json`, `overview.jpg` and an
annotated `review.html` with audio controls and answers. `review/` holds voice
scripts and source layouts, outside worker inputs. Keep truth and reviewer
files away from any hand-built worker prompt. LX separates truth itself.

The eight equally weighted checks cover item, signed quantity, target stock,
unchanged stock, prices, cash movement, ending account and one-to-one boxes
with IoU >= 0.5. Monetary comparison uses exact cents. Strict schema gates all
checks, and blank answers score zero. Evidence order does not matter.

Both samples belong to the learning stream; there is no independent held-out
set and no generalization claim. The saved workspace draft selects the existing
Unsloth worker and Spark improver, with SAM and Transcribe enabled. It retains
the user's concise-small-worker improver hint and worker identity note. It is
unapproved; existing default budgets and limits remain. `ready` means the files
can run, not that the model server is currently reachable or capable.

Source: existing COCO train2017/val2017 licensed cutouts, rendered onto generated
backgrounds by the established dataset builder. `attribution.json` records
source-image URLs, license names, image and annotation IDs. Existing rejects
apply; annotation 88783 is additionally excluded from sample 02 because its
bottle is incomplete. Original image assets and generated media are not Git
tracked. `pack` exports only the dataset/reviewer files, excluding LX state,
approvals, runs and temporary speech-library files.
