"""Verify this trusted example offline, copy it fresh, or create a standalone ZIP.

Use LX's Python (Pillow is installed with LX). No LX state or providers are touched.
Only files explicitly listed in manifest.json are read/copied/packed.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import wave
import zipfile


ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def payload_files(root):
    manifest = read(root / "manifest.json")
    if manifest.get("id") != "inventory-cash-three" or manifest.get("version") != 1:
        raise ValueError("Unknown example/version")
    entries = manifest["files"]
    if not isinstance(entries, dict) or not entries:
        raise ValueError("Missing file manifest")
    for name, entry in entries.items():
        path = PurePosixPath(name)
        if (not path.parts or path.is_absolute() or "\\" in name or ":" in name
                or any(part.startswith(".") for part in path.parts)
                or path.suffix not in {".md", ".txt", ".json", ".py", ".jpg", ".wav"}
                or name == "manifest.json"):
            raise ValueError("Unsafe manifest path")
        target = root.joinpath(*path.parts)
        if any(parent.is_symlink() or parent.is_junction()
               for parent in (target, *target.parents) if parent != root.parent):
            raise ValueError("Linked example files are not supported")
        if not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
            raise ValueError("Missing or escaped example file: " + name)
        data = target.read_bytes()
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("File integrity failed: " + name)
    return manifest, ["manifest.json", *sorted(entries)]


def verify(root=ROOT):
    from PIL import Image

    manifest, files = payload_files(root)
    spec = importlib.util.spec_from_file_location("example_inventory_check", root / "eval/check.py")
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    task = read(root / "task.json")
    if (task["template"], task["truth_schema"], task["stream_samples"]) != ("photo-request", "free", 3):
        raise ValueError("Unexpected task template/schema/sample count")
    accepted = rejected = photos = 0
    answers = []
    truths = []
    for number, count in enumerate((3, 3, 2), 1):
        folder = root / "samples" / f"sample-{number:02d}"
        truth = read(folder / "truth.json")
        expected_input = {"inventory": {key: {"quantity": value, "unit_price": truth["prices"][key]}
                                        for key, value in truth["before"].items()},
                          "account": {"currency": "USD", "balance": truth["balance"]}}
        if read(folder / "input.json") != expected_input:
            raise ValueError("Starting JSON disagrees with controller truth")
        if len(truth["photo_sizes"]) != count:
            raise ValueError("Unexpected photo count")
        for index, size in enumerate(truth["photo_sizes"], 1):
            with Image.open(folder / "photos" / f"{index}.jpg") as image:
                if list(image.size) != size or image.format != "JPEG":
                    raise ValueError("Incorrect image dimensions/format")
                image.load()
            photos += 1
        with wave.open(str(folder / "request.wav"), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 24000, "NONE"):
                raise ValueError("Unexpected WAV format")
            if not 5 < audio.getnframes() / audio.getframerate() < 30:
                raise ValueError("Unexpected WAV duration")
            if len(audio.readframes(audio.getnframes())) != audio.getnframes() * 2:
                raise ValueError("Truncated WAV")
        answer = check.example_answer(truth)
        reordered = copy.deepcopy(answer)
        reordered["evidence"].reverse()
        for good in (answer, reordered):
            if not all(flag["passed"] for flag in check.evaluate(truth, good)["checks"]):
                raise ValueError("Correct answer failed checker")
            accepted += 1
        bad_answers = [None, {}, [], "", "not json", 7, {"answer": None}, [{}]]
        for field in ("item", "change", "inventory", "cash_change", "account", "evidence"):
            bad = copy.deepcopy(answer)
            if field == "item":
                bad[field] = "unlisted_item"
            elif field == "change":
                bad[field] = -bad[field]
            elif field == "inventory":
                unrelated = next(key for key in bad[field] if key != bad["item"])
                bad[field][unrelated]["quantity"] += 1
            elif field == "cash_change":
                bad[field] = -bad[field]
            elif field == "account":
                bad[field]["balance"] += 0.5
            else:
                bad[field] = bad[field][:-1]
            bad_answers.append(bad)
        for bad in bad_answers:
            if all(flag["passed"] for flag in check.evaluate(truth, bad)["checks"]):
                raise ValueError("Incorrect answer received full marks")
            rejected += 1
        truths.append(truth)
        answers.append(answer)
    for index, truth in enumerate(truths):
        if any(flag["passed"] for flag in check.evaluate(truth, answers[(index + 1) % 3])["checks"]):
            raise ValueError("Another sample's answer received credit")
        rejected += 1
    return {"example": manifest["id"], "version": manifest["version"], "files": len(files),
            "samples": 3, "photos": photos, "audio_clips": 3,
            "correct_cases": accepted, "negative_cases": rejected, "provider_calls": 0}, files


def export(root, destination, *, zipped=False):
    summary, files = verify(root)
    destination = destination.resolve()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Destination already exists; choose a fresh path")
    if destination.is_relative_to(root.resolve()):
        raise ValueError("Destination must be outside this example")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if zipped:
        with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in files:
                archive.write(root / name, name)
        with zipfile.ZipFile(destination) as archive:
            if archive.testzip() is not None:
                raise ValueError("ZIP integrity failed")
    else:
        destination.mkdir()
        for name in files:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, target)
    return {**summary, "output": str(destination), "kind": "zip" if zipped else "folder"}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("verify", "copy", "pack"))
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    if args.command == "verify" and args.destination is not None:
        parser.error("verify does not take --destination")
    if args.command != "verify" and args.destination is None:
        parser.error("copy/pack require --destination")
    try:
        result = (verify()[0] if args.command == "verify" else
                  export(ROOT, args.destination, zipped=args.command == "pack"))
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
