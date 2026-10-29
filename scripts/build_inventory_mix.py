"""Build `inventory-mix-v1` (PROD-004 data plan v2, D-012) in three steps.

  select    pick COCO train2017 photos with clean cutouts (reads the annotations zip)
  download  fetch the selected photos from images.cocodataset.org (approved 29 Oct 2026)
  build     FSC-147 count samples + cutout composites into the dataset, plus the review
            page (not frozen; voice with synthesize_voicecount.py, then finalize)
  sheets    contact sheets of the cutouts for the eye review (rejects go in
            inventory_mix_rejects.json)
  review    rewrite the review page

Run from main_LX with PYTHONPATH=src.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

from lx.inventory import expected_answer
from lx.inventory_cutouts import (CUTOUT_KINDS, MIN_VERTICES, add_composite_samples, cutout_candidates,
                                  prepare_cutouts, select_photos)
from lx.inventory_data import add_fsc_samples

LX = Path(".lx")
COCO = LX / "downloads" / "coco"
POOL = COCO / "cutout_pool_train.json"
TRAIN_DIR = COCO / "train2017"
URL = "http://images.cocodataset.org/train2017/"
N_IMAGES = 380
DATASET = LX / "datasets" / "inventory-mix-v1"
VIEW = LX / "views" / "inventory-mix-v1.html"
# Per request type: user bundle 15 = stream 6 + check 9; hidden held-out 24; calibration 6 (D-012).
SPLIT_COUNTS = {"stream": 2, "check": 3, "heldout": 8, "calibration": 2}
REJECTS = Path(__file__).with_name("inventory_mix_rejects.json")  # cutouts rejected by eye on the contact sheets
SHEETS = LX / "views" / "cutout-sheets"


def select(args):
    with zipfile.ZipFile(COCO / "annotations_trainval2017.zip") as archive:
        annotations = json.loads(archive.read("annotations/instances_train2017.json"))
    found = cutout_candidates(annotations)
    files = set(select_photos(found, args.images))
    pool = [c for c in found if c["file_name"] in files]
    POOL.write_text(json.dumps({"images": sorted(files), "cutouts": pool}) + "\n", encoding="utf-8")
    kinds = {}
    for c in pool:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    print(f"{len(found)} clean cutouts in train2017; selected {len(files)} photos with {len(pool)} cutouts")
    print(json.dumps(dict(sorted(kinds.items())), indent=None))


def download(args):
    files = json.loads(POOL.read_text(encoding="utf-8"))["images"]
    TRAIN_DIR.mkdir(parents=True, exist_ok=True)
    total = fetched = 0
    for name in files:
        target = TRAIN_DIR / name
        if target.exists():
            continue
        if not name.endswith(".jpg") or "/" in name or ".." in name:
            raise ValueError(f"unexpected file name {name!r}")
        with urllib.request.urlopen(URL + name, timeout=60) as reply:
            data = reply.read(5_000_000)
        if not data.startswith(b"\xff\xd8"):
            raise ValueError(f"{name} is not a JPEG")
        target.write_bytes(data)
        total += len(data)
        fetched += 1
        time.sleep(0.05)
    print(f"fetched {fetched} photos, {total / 1e6:.1f} MB; {len(files)} listed")


def build(args):
    if DATASET.exists():
        raise SystemExit(f"{DATASET} exists; remove it to rebuild")
    voicecount = json.loads((LX / "datasets" / "voicecount-v1" / "controller_truth" / "dataset.json").read_text())
    exclude = {source["image"] for source in voicecount["sources"].values()}
    for split, count in SPLIT_COUNTS.items():
        add_fsc_samples(DATASET, COCO.parent / "fsc147", count, split=split, exclude=exclude)
    cutouts = load_cutouts()
    coloured = sum(1 for c in cutouts if c["colour"])
    print(f"{len(cutouts)} cutouts, {coloured} with a dominant colour")
    # Only about a third of the cutouts have a clear colour, too few to keep the user bundle and the
    # hidden sets apart; a cutout may appear in up to three samples (never twice in one), and every
    # composite photo is new.
    for split in ("heldout", "calibration", "stream", "check"):
        for request_type in ("colour_count", "inventory"):
            add_composite_samples(DATASET, cutouts, SPLIT_COUNTS[split], request_type=request_type, split=split,
                                  max_uses=3, separate_groups=False)
    review()


def load_cutouts():
    """Prepared cutouts from the downloaded train2017 selection plus val2017, under the current rules."""
    pool = json.loads(POOL.read_text(encoding="utf-8"))["cutouts"]
    with zipfile.ZipFile(COCO / "annotations_trainval2017.zip") as archive:
        val = json.loads(archive.read("annotations/instances_val2017.json"))
    have = {p.name for p in (COCO / "val2017").iterdir()}
    pool += [c for c in cutout_candidates(val) if c["file_name"] in have]
    rejected = set(json.loads(REJECTS.read_text(encoding="utf-8"))["ann_ids"]) if REJECTS.exists() else set()
    kinds = set(CUTOUT_KINDS.values())
    pool = [c for c in pool if c["kind"] in kinds and len(c["polygon"]) >= 2 * MIN_VERTICES
            and c["ann_id"] not in rejected]

    def load(name):
        return (TRAIN_DIR / name if (TRAIN_DIR / name).exists() else COCO / "val2017" / name).read_bytes()

    return prepare_cutouts(pool, load)


def sheets(args):
    """Contact sheets (6x5 cutouts each, labelled `ann_id kind colour`) for the eye review."""
    from PIL import Image, ImageDraw

    cutouts = sorted(load_cutouts(), key=lambda c: (c["kind"], c["ann_id"]))
    SHEETS.mkdir(parents=True, exist_ok=True)
    cell, cols, rows = 200, 6, 5
    for start in range(0, len(cutouts), cols * rows):
        sheet = Image.new("RGB", (cols * cell, rows * cell), (235, 235, 235))
        draw = ImageDraw.Draw(sheet)
        for index, c in enumerate(cutouts[start:start + cols * rows]):
            x, y = index % cols * cell, index // cols * cell
            image = c["image"].copy()
            image.thumbnail((cell - 10, cell - 30))
            sheet.paste(image, (x + 5, y + 5), image)
            draw.text((x + 5, y + cell - 22), f"{c['ann_id']} {c['kind']} {c['colour'] or '-'}", fill=(0, 0, 0))
            draw.rectangle((x, y, x + cell - 1, y + cell - 1), outline=(180, 180, 180))
        sheet.save(SHEETS / f"sheet-{start // (cols * rows) + 1:02d}.png")
    print(f"{len(cutouts)} cutouts on {-(-len(cutouts) // (cols * rows))} sheets in {SHEETS}")


def review():
    import html
    manifest = json.loads((DATASET / "worker_inputs" / "manifest.json").read_text(encoding="utf-8"))
    truth = json.loads((DATASET / "controller_truth" / "ground_truth.json").read_text(encoding="utf-8"))
    requests = json.loads((DATASET / "controller_truth" / "requests.json").read_text(encoding="utf-8"))
    parts = ["<!doctype html><meta charset=utf-8><title>Inventory mix review</title><style>body{font:14px system-ui;"
             "margin:16px;background:#111;color:#eee}.row{display:flex;gap:8px;overflow-x:auto}figure{margin:0}"
             "img{height:230px}figcaption{text-align:center;max-width:320px}</style>"
             "<h2>inventory-mix-v1: built samples (FSC-147 dots; COCO cutout composites)</h2>"]
    for sample in manifest["samples"]:
        sid, t = sample["sample_id"], truth[sample["sample_id"]]
        answer = expected_answer(t)
        if isinstance(answer, dict):
            answer = ", ".join(f"{k} {v}" for k, v in sorted(answer.items()))
        parts.append(f"<section><h3>{html.escape(sid)} ({sample['split']}): &quot;{html.escape(requests[sid]['text'])}"
                     f"&quot; &rarr; {html.escape(str(answer))}</h3><div class=row>")
        for n, path in enumerate(sample["photos"], 1):
            items = "; ".join(f"{i['count']} {(i.get('colour') or '') + ' ' if i.get('colour') else ''}{i['kind']}"
                              for i in t["photos"][str(n)]) or "none"
            parts.append(f'<figure><img src="../datasets/inventory-mix-v1/worker_inputs/{path}">'
                         f"<figcaption>photo {n}: {html.escape(items)}</figcaption></figure>")
        parts.append("</div></section>")
    VIEW.write_text("".join(parts), encoding="utf-8")
    print(f"{len(manifest['samples'])} samples; review page {VIEW}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=("select", "download", "sheets", "build", "review"))
    parser.add_argument("--images", type=int, default=N_IMAGES)
    args = parser.parse_args(argv)
    if args.step == "review":
        review()
    else:
        {"select": select, "download": download, "sheets": sheets, "build": build}[args.step](args)


if __name__ == "__main__":
    sys.exit(main())
