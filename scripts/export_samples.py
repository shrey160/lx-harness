"""Copy a frozen inventory dataset's split into a workspace's samples folder.

The result is the photo-request layout the app reads: `sample-NN/` with
photos/1.jpg ..., request.wav and truth.json. Nothing is sent anywhere.
"""
import argparse
import json
from pathlib import Path
import shutil

from lx.inventory_data import load_inventory_dataset


def export(dataset, target, split="stream"):
    dataset, target = Path(dataset), Path(target)
    samples, truth, _ = load_inventory_dataset(dataset)
    chosen = [sample for sample in samples if sample["split"] == split]
    if not chosen:
        raise SystemExit(f"No samples in split {split!r}")
    if target.exists():
        raise SystemExit(f"{target} already exists")
    source = dataset/"worker_inputs"
    for index, sample in enumerate(chosen, 1):
        folder = target/f"sample-{index:02d}"
        (folder/"photos").mkdir(parents=True)
        for number, photo in enumerate(sample["photos"], 1):
            shutil.copyfile(source/photo, folder/"photos"/f"{number}.jpg")
        shutil.copyfile(source/sample["audio"], folder/"request.wav")
        (folder/"truth.json").write_text(json.dumps(truth[sample["sample_id"]], indent=2), encoding="utf-8")
    return len(chosen)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="a frozen dataset folder, such as .lx/datasets/inventory-mix-v1")
    parser.add_argument("target", type=Path, help="the new samples folder, such as <workspace>/samples")
    parser.add_argument("--split", default="stream")
    args = parser.parse_args()
    print(f"Exported {export(args.dataset, args.target, args.split)} samples to {args.target}")


if __name__ == "__main__":
    main()
