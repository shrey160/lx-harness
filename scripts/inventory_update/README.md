# Inventory and cash updates: two samples

Two small photo/audio transactions for the local LFM3-VL-2B worker. A stock
clerk receives an inventory with unit prices and a cash balance, two photos of
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

Photo 3 renders the starting inventory and account as legible JSON. The same
data is in `inventory.json`; LX currently gives photo workers images and audio,
not arbitrary JSON files, so the sheet is the operative input.

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

The default output is `.lx/inventory-update-local/`. `--root` chooses another
fresh folder. Build refuses to overwrite samples. The existing COCO downloads,
Pillow, local Kokoro model/voices and `.lx/tts-venv` are prerequisites. No
downloads or provider calls occur. Speech is synthesized offline with Kokoro
and phonemizer; its temporary files stay under the generated review folder.

`samples/sample-*/` holds `photos/1.jpg` to `3.jpg`, `request.wav`,
`inventory.json` and controller-only `truth.json`. The root has `task.json`,
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
Unsloth worker and Spark improver, with SAM and Transcribe enabled. It is
unapproved; existing default budgets and limits remain. `ready` means the files
can run, not that the model server is currently reachable or capable.

Source: existing COCO train2017/val2017 licensed cutouts, rendered onto generated
backgrounds by the established dataset builder. `attribution.json` records
source-image URLs, license names, image and annotation IDs. Existing rejects
apply; annotation 88783 is additionally excluded from sample 02 because its
bottle is incomplete. Original image assets and generated media are not Git
tracked. `pack` exports only the dataset/reviewer files, excluding LX state,
approvals, runs and temporary speech-library files.
