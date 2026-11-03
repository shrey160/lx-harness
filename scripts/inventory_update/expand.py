"""Expand the two-sample dataset with four user photos and conversational audio.

Offline preparation only. Uses existing local Kokoro assets; never overwrites
samples, reads credentials or starts a run. Paths are supplied at the command line.
"""
import argparse
import copy
import hashlib
import html
import importlib.util
import json
from pathlib import Path
import shutil
import wave
import zipfile

from PIL import Image, ImageDraw, ImageFont, ImageOps


HERE = Path(__file__).resolve().parent
ROOT = Path(".lx/inventory-update-three")
SOURCE = Path(".lx/inventory-update-json")
PHOTOS = None
SCRIPTS = {
    "sample-01": {"voice": "af_sarah", "text": "Hey, I picked up the cups in these pictures for the shop, and I paid cash from the till. Could you add them to stock and take the payment out? The scissors and the other bits are just things we already had."},
    "sample-02": {"voice": "am_adam", "text": "Hey, those bottles in the photos have just sold. The customer paid cash, so that money's in the till now. Can you mark them off? The chocolates are still here, so leave those as they are."},
    "sample-03": {"voice": "af_sarah", "text": "Hey, I was going to restock the pencils in that first photo... actually, leave those alone for now. Someone bought the forks in the other photo and paid cash. Take those off the shelf count and put the payment in the till. The spoons weren't part of the sale."},
}
# Human annotations in the upright, resized 768x1024 photographs, excluding shadows.
SCENES = {
    "31130.jpg": [("earphones", [82, 417, 333, 884]), ("glue_stick", [213, 130, 513, 252]),
                  ("pencil", [439, 382, 492, 953]), ("pencil", [506, 397, 567, 968]),
                  ("pencil", [563, 396, 630, 971]), ("pencil", [626, 383, 697, 957])],
    "31131.jpg": [("fork", [253, 70, 503, 410]), ("fork", [348, 88, 625, 400]),
                  ("spoon", [153, 354, 246, 740]), ("spoon", [231, 515, 328, 887]),
                  ("spoon", [313, 690, 415, 1023])],
    "31132.jpg": [("scissors", [61, 120, 601, 344]), ("cup", [344, 423, 655, 672]),
                  ("cup", [72, 625, 392, 951])],
    "31133.jpg": [("chocolate", [348, 116, 666, 349]), ("chocolate", [296, 279, 613, 531]),
                  ("bottle", [121, 570, 541, 762]), ("bottle", [114, 746, 538, 953])],
}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def checker():
    spec = importlib.util.spec_from_file_location("expanded_inventory_check", HERE / "check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def starting(truth):
    return {"inventory": {k: {"quantity": q, "unit_price": truth["prices"][k]} for k, q in truth["before"].items()},
            "account": {"currency": "USD", "balance": truth["balance"]}}


def build():
    if (ROOT / "samples").exists():
        raise SystemExit("Samples already exist; choose a fresh --root.")
    if PHOTOS is None:
        raise SystemExit("build requires --photos-dir containing 31130.jpg through 31133.jpg")
    layouts, provenance = {}, []
    seed_layouts = read(SOURCE / "review/layouts.json")
    for sample, filename, extra, price in [("sample-01", "31132.jpg", "scissors", 4.25),
                                           ("sample-02", "31133.jpg", "chocolate", 1.50)]:
        folder = ROOT / "samples" / sample
        (folder / "photos").mkdir(parents=True)
        for number in (1, 2):
            shutil.copyfile(SOURCE / "samples" / sample / "photos" / f"{number}.jpg", folder / "photos" / f"{number}.jpg")
        truth = read(SOURCE / "samples" / sample / "truth.json")
        truth["before"][extra], truth["prices"][extra] = 3 if extra == "scissors" else 6, price
        entries = add_photo(folder, 3, filename, truth["item"], provenance)
        truth["evidence"] += [{"photo": 3, "box": e["box"]} for e in entries if e["target"]]
        truth["change"] = (1 if truth["change"] > 0 else -1) * len(truth["evidence"])
        layouts[sample] = {"name": "buy-cups" if sample == "sample-01" else "sell-bottles",
                           "photos": seed_layouts[sample]["photos"] + [entries]}
        finish_sample(folder, truth)
    folder = ROOT / "samples/sample-03"
    (folder / "photos").mkdir(parents=True)
    entries = [add_photo(folder, n, name, "fork", provenance) for n, name in enumerate(("31130.jpg", "31131.jpg"), 1)]
    truth = {"before": {"earphones": 3, "glue_stick": 5, "pencil": 8, "spoon": 9, "fork": 6},
             "prices": {"earphones": 12.0, "glue_stick": 1.25, "pencil": 0.75, "spoon": 1.5, "fork": 2.25},
             "balance": 40.0, "item": "fork", "change": -2,
             "evidence": [{"photo": n, "box": e["box"]} for n, scene in enumerate(entries, 1) for e in scene if e["target"]]}
    finish_sample(folder, truth)
    layouts[folder.name] = {"name": "corrected-request-sell-forks", "harder": True,
                            "difficulty": "Cancel a proposed pencil restock; sell forks, preserving pencils and similar spoons.", "photos": entries}
    save(ROOT / "review/layouts.json", layouts)
    save(ROOT / "review/controller_truth/scripts.json", SCRIPTS)
    old = read(SOURCE / "attribution.json")
    save(ROOT / "attribution.json", {"source": "Four existing synthetic COCO scenes plus four user-supplied photographs",
                                     "existing_coco_sources": old["sources"], "user_photos": provenance,
                                     "prices": "Constructed USD transaction prices from input.json; packaging labels are unrelated."})
    (ROOT / "eval").mkdir(exist_ok=True)
    shutil.copyfile(HERE / "check.py", ROOT / "eval/check.py")
    shutil.copyfile(SOURCE / "eval/judges.json", ROOT / "eval/judges.json")
    task = read(HERE / "task.json")
    task["stream_samples"] = 3
    task["contract"] = task["contract"].replace("metadata for two photos", "metadata for the supplied photos").replace(
        "The two photos show", "The photos show").replace('1 or 2,', 'a supplied photo number,')
    task["selection_checks"] = task["selection_checks"].replace("inventory sheet", "starting inventory JSON")
    save(ROOT / "task.json", task)
    (ROOT / "README.md").write_text("# Three inventory/cash samples\n\n3/3/2 photos, direct input.json and conversational offline speech. "
        "Sample01 buys7 cups: quantity11, cash32.50. Sample02 sells5 bottles: quantity5, cash41.25. "
        "Sample03 cancels pencil restocking and sells2 forks: quantity4, cash44.50. "
        "Counts and outcomes here are reviewer-only; truth/scripts stay outside worker inputs. "
        "Prices are constructed USD transaction prices. Prior datasets/runs are unchanged.\n", encoding="utf-8")
    print("Built three samples with 3/3/2 photos; conversational scripts and private truth prepared.")


def add_photo(folder, number, filename, target, provenance):
    original = PHOTOS / filename
    with Image.open(original) as im:
        image = ImageOps.exif_transpose(im).convert("RGB")
        image.thumbnail((768, 1024), Image.Resampling.LANCZOS)
        assert image.size == (768, 1024), "Unexpected photograph dimensions; annotations need review"
        image.save(folder / "photos" / f"{number}.jpg", quality=92)
    provenance.append({"sample": folder.name, "photo": number, "filename": filename,
                       "source_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
                       "processing": "EXIF orientation applied, resized to768x1024, metadata removed; no crop or content synthesis",
                       "annotation": "Manual visible-object boxes on normalized image; shadows excluded"})
    return [{"kind": kind, "box": box, "target": kind == target, "user_photo": filename} for kind, box in SCENES[filename]]


def finish_sample(folder, truth):
    truth["photo_sizes"] = [list(Image.open(p).size) for p in sorted((folder / "photos").glob("*.jpg"))]
    checker().example_answer(truth)
    save(folder / "input.json", starting(truth))
    save(folder / "truth.json", truth)


def voice():
    spec = importlib.util.spec_from_file_location("seed_inventory_builder", HERE / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT, mod.WORK = ROOT, ROOT / "review"
    mod.voice()


def review():
    layouts, scripts = read(ROOT / "review/layouts.json"), read(ROOT / "review/controller_truth/scripts.json")
    annotated = ROOT / "review/annotated"
    annotated.mkdir(exist_ok=True)
    overview = Image.new("RGB", (1600, 1500), "#eef2f6")
    font = ImageFont.load_default(size=19)
    out = ['<!doctype html><meta charset="utf-8"><title>Three inventory samples</title>',
           '<style>body{font:18px system-ui;margin:30px;background:#eef2f6;color:#17283a}section{background:white;padding:24px;margin:24px 0;border-radius:16px}.photos{display:flex;gap:12px}.photos img{width:30%;max-height:600px;object-fit:contain}pre{white-space:pre-wrap}audio{width:100%}</style>',
           '<h1>Three inventory and cash samples</h1><p>Reviewer material includes answers. Worker inputs are photos, actual starting JSON and audio. Third voice note contains a correction; the cancelled pencil action must not change stock or cash.</p>']
    for row, (sample, layout) in enumerate(layouts.items()):
        folder = ROOT / "samples" / sample
        out.append(f'<section><h2>{html.escape(sample + " / " + layout["name"])}</h2><div class="photos">')
        for number, entries in enumerate(layout["photos"], 1):
            with Image.open(folder / "photos" / f"{number}.jpg") as im:
                image = im.convert("RGB")
            thumb = ImageOps.contain(image, (370, 430))
            x, y = (number - 1) * 395 + 12, row * 500 + 40
            overview.paste(thumb, (x, y))
            ImageDraw.Draw(overview).text((x, row * 500 + 10), f"{sample} / photo{number}", fill="#17283a", font=font)
            draw = ImageDraw.Draw(image)
            for e in entries:
                colour = "#00b050" if e["target"] else "#e26528"
                draw.rectangle(e["box"], outline=colour, width=4)
                draw.text((e["box"][0], max(0, e["box"][1] - 24)), e["kind"], fill=colour, font=font)
            name = f"{sample}-{number}.jpg"
            image.save(annotated / name, quality=92)
            out.append(f'<img alt="{sample} photo{number}" src="review/annotated/{name}">')
        truth = read(folder / "truth.json")
        answer = checker().example_answer(truth)
        ImageDraw.Draw(overview).multiline_text((1200, row * 500 + 35), f"{layout['name']}\nChange: {answer['change']} {answer['item']}\nStock: {answer['inventory'][answer['item']]['quantity']}\nCash: {answer['account']['balance']:.2f} USD", fill="#17283a", font=font, spacing=8)
        out.append('</div><h3>Starting JSON</h3><pre>' + html.escape(json.dumps(starting(truth), indent=2)) + '</pre>')
        out.append('<p>' + html.escape(scripts[sample]["text"]) + f'</p><audio controls src="samples/{sample}/request.wav"></audio>')
        out.append('<h3>Expected answer</h3><pre>' + html.escape(json.dumps(answer, indent=2)) + '</pre></section>')
    (ROOT / "review.html").write_text("\n".join(out), encoding="utf-8")
    overview.save(ROOT / "overview.jpg", quality=94)
    print("Wrote reviewer page, annotated images and overview.")


def verify():
    check, summaries, negative = checker(), {}, 0
    layouts = read(ROOT / "review/layouts.json")
    folders = sorted((ROOT / "samples").iterdir())
    assert [p.name for p in folders] == ["sample-01", "sample-02", "sample-03"]
    for folder, expected_count in zip(folders, [3, 3, 2]):
        truth = read(folder / "truth.json")
        answer = check.example_answer(truth)
        flags = lambda value: [x["passed"] for x in check.evaluate(truth, value)["checks"]]
        assert all(flags(answer)) and not any(flags({})) and not any(flags(None))
        assert read(folder / "input.json") == starting(truth)
        assert sorted(p.name for p in (folder / "photos").iterdir()) == [f"{i}.jpg" for i in range(1, expected_count + 1)]
        for p, size in zip(sorted((folder / "photos").iterdir()), truth["photo_sizes"]):
            with Image.open(p) as im:
                assert list(im.size) == size and im.mode == "RGB" and not im.getexif()
                im.verify()
        assert truth["evidence"] == [{"photo": n, "box": e["box"]} for n, scene in enumerate(layouts[folder.name]["photos"], 1) for e in scene if e["target"]]
        cases = [({}, None), (None, None), (dict(answer, extra=1), None)]
        for field, value, failed in [("item", "wrong", None), ("change", -answer["change"], 1), ("change", True, None),
                                     ("cash_change", -answer["cash_change"], 5), ("cash_change", 0.001, None),
                                     ("cash_change", True, None), ("cash_change", float("nan"), None), ("evidence", [], 7),
                                     ("account", {"currency": "EUR", "balance": answer["account"]["balance"]}, 6)]:
            a = copy.deepcopy(answer); a[field] = value; cases.append((a, failed))
        for field, failed in [("quantity", 2), ("unit_price", 4)]:
            a = copy.deepcopy(answer); a["inventory"][truth["item"]][field] += 1; cases.append((a, failed))
        a = copy.deepcopy(answer); other = next(k for k in truth["before"] if k != truth["item"])
        a["inventory"][other]["quantity"] += 1; cases.append((a, 3))
        for photo in [0, expected_count + 1, True]:
            a = copy.deepcopy(answer); a["evidence"][0]["photo"] = photo; cases.append((a, None))
        a = copy.deepcopy(answer); a["evidence"][0]["box"] = [0, 0, 1, 1]; cases.append((a, 7))
        a = copy.deepcopy(answer); a["evidence"][0]["box"][2] = truth["photo_sizes"][a["evidence"][0]["photo"] - 1][0]; cases.append((a, None))
        a = copy.deepcopy(answer); a["evidence"] = [a["evidence"][0]] * len(a["evidence"]); cases.append((a, 7))
        for candidate, failed in cases:
            result = flags(candidate)
            assert not any(result) if failed is None else not result[failed]
            negative += 1
        a = copy.deepcopy(answer); a["evidence"].reverse(); assert all(flags(a))
        with wave.open(str(folder / "request.wav")) as audio:
            assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 24000)
            seconds = audio.getnframes() / audio.getframerate()
            assert 5 < seconds < 45
            import array
            pcm = array.array("h", audio.readframes(audio.getnframes()))
            assert sum(abs(v) for v in pcm) / len(pcm) > 100
        summaries[folder.name] = {"photos": expected_count, "changed_item": truth["item"], "change": truth["change"],
                                  "ending_quantity": answer["inventory"][truth["item"]]["quantity"], "ending_cash": answer["account"]["balance"], "audio_seconds": round(seconds, 2)}
    save(ROOT / "validation.json", {"samples": summaries, "negative_cases": negative,
         "sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sample_files()}, "baseline_measured": False})
    print(json.dumps({"samples": summaries, "negative_cases": negative}))


def sample_files():
    return sorted(p for folder in sorted((ROOT / "samples").iterdir())
                  for p in [*sorted((folder / "photos").glob("*.jpg")), folder / "input.json", folder / "truth.json", folder / "request.wav"])


def pack():
    verify()
    files = sample_files() + [ROOT / name for name in ["README.md", "task.json", "eval/check.py", "eval/judges.json", "attribution.json", "validation.json", "overview.jpg", "review.html", "review/layouts.json", "review/controller_truth/scripts.json"]]
    files += sorted((ROOT / "review/annotated").glob("*.jpg"))
    with zipfile.ZipFile(ROOT / "inventory-update-three.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(ROOT / "inventory-update-three.zip") as archive:
        assert archive.testzip() is None
    print("Packed inventory-update-three.zip without runtime state.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=["build", "voice", "review", "verify", "pack"])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--photos-dir", type=Path)
    args = parser.parse_args()
    ROOT, SOURCE, PHOTOS = args.root, args.source, args.photos_dir
    {"build": build, "voice": voice, "review": review, "verify": verify, "pack": pack}[args.step]()
