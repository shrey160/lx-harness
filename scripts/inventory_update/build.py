"""Build/voice/review/verify two stock-update samples offline. Run from main_LX.

Uses the existing COCO downloads, approved cutout rejects and Kokoro install.
Generated media, controller notes and reviewer answers are kept under .lx/.
No model calls, downloads, credentials, changes to the loop or spending.
"""
import argparse
import base64
import copy
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import wave
import zipfile

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import build_inventory_mix
from receiving_audit.build import compose

ROOT = Path(".lx/inventory-update-json")
WORK = ROOT / "review"
PLANS = [
    {"name": "add-cups", "item": "cup", "before": {"cup": 4, "bottle": 6, "bowl": 8},
     "prices": {"cup": 2.50, "bottle": 4.00, "bowl": 6.00}, "balance": 50.00,
     "counts": [2, 3], "sign": 1, "distractors": ["bottle", "bowl"], "voice": "af_sarah",
     "note": "Please update the inventory and cash account supplied in the JSON input. We purchased all the cups in photos one and two at the listed unit price and paid for them from this account. Add those cups to stock and record the payment. These are two separate groups of objects. Leave the other stock and all unit prices unchanged."},
    {"name": "remove-bottles", "item": "bottle", "before": {"cup": 7, "bottle": 10, "bowl": 5},
     "prices": {"cup": 2.00, "bottle": 3.25, "bowl": 5.00}, "balance": 25.00,
     "counts": [1, 2], "sign": -1, "distractors": ["cup", "bowl"], "voice": "am_adam", "avoid": [88783],
     "note": "Please update the inventory and cash account supplied in the JSON input. We sold all the bottles in photos one and two at the listed unit price and received the money in this account. Remove those bottles from stock and record the cash received. These are two separate groups of objects. Leave the other stock and all unit prices unchanged."},
]


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_check():
    spec = importlib.util.spec_from_file_location("inventory_update_check", HERE / "check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def starting(plan):
    return {"inventory": {k: {"quantity": v, "unit_price": plan["prices"][k]} for k, v in plan["before"].items()},
            "account": {"currency": "USD", "balance": plan["balance"]}}


def build():
    if (ROOT / "samples").exists():
        raise SystemExit("Samples already exist; use a new root rather than overwrite a workspace.")
    rejected = set(json.loads((HERE.parent / "receiving_audit/rejects.json").read_text())["ann_ids"])
    pool = [c for c in build_inventory_mix.load_cutouts() if c["kind"] in {"cup", "bottle", "bowl"} and c["ann_id"] not in rejected]
    rng, used, layouts, scripts, sources = random.Random(20261103), set(), {}, {}, []
    for number, plan in enumerate(PLANS, 1):
        name = f"sample-{number:02d}"
        folder = ROOT / "samples" / name
        (folder / "photos").mkdir(parents=True)
        evidence, layout = [], []
        for photo, count in enumerate(plan["counts"], 1):
            kinds = [plan["item"]] * count + [plan["distractors"][photo - 1]]
            objects = []
            for kind in kinds:
                choices = [c for c in pool if c["kind"] == kind and c["ann_id"] not in used and c["ann_id"] not in plan.get("avoid", [])]
                if not choices:
                    raise ValueError("Not enough distinct cutouts for " + kind)
                c = rng.choice(choices)
                used.add(c["ann_id"])
                objects.append(c)
            rng.shuffle(objects)
            built = None
            for _ in range(100):
                built = compose(objects, rng, 0, rng.randrange(1_000_000))
                if built:
                    break
            if built is None:
                raise ValueError("Could not place objects without overlap")
            data, boxes = built
            (folder / "photos" / f"{photo}.jpg").write_bytes(data)
            entries = []
            for c, box in zip(objects, boxes):
                target = c["kind"] == plan["item"]
                entries.append({"kind": c["kind"], "box": box, "target": target, "ann_id": c["ann_id"]})
                if target:
                    evidence.append({"photo": photo, "box": box})
                sources.append({"sample": name, "photo": photo, **{k: c[k] for k in ("ann_id", "image_id", "file_name", "kind", "license", "flickr_url", "coco_url")}})
            layout.append(entries)
        save(folder / "input.json", starting(plan))
        truth = {"before": plan["before"], "prices": plan["prices"], "balance": plan["balance"], "item": plan["item"], "change": plan["sign"] * sum(plan["counts"]), "evidence": evidence}
        load_check().example_answer(truth)
        save(folder / "truth.json", truth)
        layouts[name] = {"name": plan["name"], "photos": layout}
        scripts[name] = {"text": plan["note"], "voice": plan["voice"]}
    save(WORK / "controller_truth/scripts.json", scripts)
    save(WORK / "layouts.json", layouts)
    save(ROOT / "attribution.json", {"source": "Existing COCO train2017/val2017 cutouts; no new downloads", "sources": sources})
    (ROOT / "eval").mkdir(exist_ok=True)
    shutil.copyfile(HERE / "check.py", ROOT / "eval/check.py")
    shutil.copyfile(HERE / "task.json", ROOT / "task.json")
    shutil.copyfile(HERE / "README.md", ROOT / "README.md")
    print("Built two samples, four photos, direct JSON inputs and controller truth.")


def voice():
    (WORK / "worker_inputs/media").mkdir(parents=True, exist_ok=True)
    temp = WORK / "tts-temp"
    temp.mkdir(exist_ok=True)
    model = Path(".lx/downloads/kokoro")
    subprocess.run([str(Path(".lx/tts-venv/Scripts/python.exe")), str(HERE.parent / "synthesize_voicecount.py"), str(WORK),
                    str(model / "kokoro-v1.0.int8.onnx"), str(model / "voices-v1.0.bin")], check=True,
                   env={**os.environ, "TMP": str(temp.resolve()), "TEMP": str(temp.resolve())})
    for name in json.loads((WORK / "controller_truth/scripts.json").read_text()):
        shutil.copyfile(WORK / "worker_inputs/media" / (name + ".wav"), ROOT / "samples" / name / "request.wav")


def review():
    layouts = json.loads((WORK / "layouts.json").read_text())
    scripts = json.loads((WORK / "controller_truth/scripts.json").read_text())
    overview = Image.new("RGB", (1200, 660), "#e9edf2")
    out = ['<!doctype html><meta charset="utf-8"><title>Inventory update review</title>',
           '<style>body{font:18px system-ui;margin:32px;background:#eef2f6;color:#17283a}section{background:white;padding:24px;margin:24px 0;border-radius:16px}.photos{display:flex;gap:12px;align-items:start}.photos img{width:32%;object-fit:contain}.photos pre{width:32%;font-size:14px;overflow-wrap:anywhere}pre{white-space:pre-wrap}audio{width:100%}</style>',
           '<h1>Two inventory and cash updates / direct JSON</h1><p>Reviewer material: includes answers. Worker receives two object photos, audio and input.json as text data; truth is controller-only. Synthetic composites, existing licensed COCO objects, offline Kokoro speech. Baseline difficulty and actual tool use are unmeasured.</p>']
    for row, (name, layout) in enumerate(layouts.items()):
        folder = ROOT / "samples" / name
        out.append(f'<section><h2>{html.escape(name + " / " + layout["name"])}</h2><div class="photos">')
        for photo in (1, 2):
            image = Image.open(folder / "photos" / f"{photo}.jpg").convert("RGB")
            overview.paste(image.resize((384, 288)), (12 + (photo - 1) * 400, 35 + row * 330))
            ImageDraw.Draw(overview).text((12 + (photo - 1) * 400, 12 + row * 330), f"{name} / photo {photo}", fill="#17283a", font=ImageFont.load_default(size=17))
            if photo < 3:
                draw = ImageDraw.Draw(image)
                for e in layout["photos"][photo - 1]:
                    colour = "#058b43" if e["target"] else "#ae4a30"
                    draw.rectangle(e["box"], outline=colour, width=4)
                    draw.text((e["box"][0], max(0, e["box"][1] - 25)), e["kind"], fill=colour, font=ImageFont.load_default(size=23))
            import io
            buf = io.BytesIO()
            image.save(buf, "JPEG", quality=90)
            out.append('<img alt="Photo ' + str(photo) + '" src="data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode() + '">')
        input_text = (folder / "input.json").read_text(encoding="utf-8")
        out.append('<pre><strong>input.json / direct worker data</strong>\n' + html.escape(input_text) + '</pre></div>')
        draw = ImageDraw.Draw(overview)
        draw.text((812, 12 + row * 330), name + ' / input.json (text)', fill="#17283a", font=ImageFont.load_default(size=17))
        draw.multiline_text((812, 40 + row * 330), json.dumps(json.loads(input_text), indent=1), fill="#17283a", font=ImageFont.load_default(size=11), spacing=0)
        out.append('<p>' + html.escape(scripts[name]["text"]) + '</p>')
        clip = base64.b64encode((folder / "request.wav").read_bytes()).decode()
        out.append('<audio controls src="data:audio/wav;base64,' + clip + '"></audio>')
        truth = json.loads((folder / "truth.json").read_text())
        out.append('<pre>' + html.escape(json.dumps(load_check().example_answer(truth), indent=2)) + '</pre></section>')
    (ROOT / "review.html").write_text("\n".join(out), encoding="utf-8")
    overview.save(ROOT / "overview.jpg", quality=95)
    print("Wrote review.html (answers and audio) and overview.jpg (unannotated inputs).")


def verify():
    check, summary, cases = load_check(), {}, 0
    layouts = json.loads((WORK / "layouts.json").read_text())
    sources = json.loads((ROOT / "attribution.json").read_text())["sources"]
    assert len(sources) == 12 and len({s["ann_id"] for s in sources}) == 12
    assert all(s["license"] and (s["flickr_url"] or s["coco_url"]) for s in sources)
    truths = [json.loads(p.read_text()) for p in sorted((ROOT / "samples").glob("*/truth.json"))]
    assert len(truths) == 2
    for index, (folder, truth) in enumerate(zip(sorted((ROOT / "samples").iterdir()), truths)):
        answer = check.example_answer(truth)
        flags = lambda a: [c["passed"] for c in check.evaluate(truth, a)["checks"]]
        assert all(flags(answer)) and not any(flags({})) and not any(flags(None))
        tests = []
        def changed(field, value):
            a = copy.deepcopy(answer)
            a[field] = value
            return a
        tests += [(changed("change", -answer["change"]), 1), (changed("change", True), None),
                  (changed("item", "other"), None), (changed("evidence", []), 7),
                  (changed("evidence", answer["evidence"][:-1]), 7),
                  (changed("evidence", [answer["evidence"][0]] * len(answer["evidence"])), 7)]
        inv = copy.deepcopy(answer["inventory"])
        inv[truth["item"]]["quantity"] += 1
        tests.append((changed("inventory", inv), 2))
        inv = copy.deepcopy(answer["inventory"])
        inv[next(k for k in inv if k != truth["item"])]["quantity"] += 1
        tests.append((changed("inventory", inv), 3))
        inv = copy.deepcopy(answer["inventory"])
        inv[truth["item"]]["unit_price"] += 1
        tests.append((changed("inventory", inv), 4))
        tests += [(changed("cash_change", -answer["cash_change"]), 5), (changed("cash_change", 0), 5),
                  (changed("cash_change", True), None), (changed("cash_change", 0.001), None),
                  (changed("account", {"currency": "USD", "balance": truth["balance"]}), 6),
                  (changed("account", {"currency": "EUR", "balance": answer["account"]["balance"]}), 6)]
        boxes = copy.deepcopy(answer["evidence"])
        boxes[0]["photo"] = 3
        tests.append((changed("evidence", boxes), None))
        boxes = copy.deepcopy(answer["evidence"])
        boxes[0]["photo"] = 3 - boxes[0]["photo"]
        tests.append((changed("evidence", boxes), 7))
        boxes = copy.deepcopy(answer["evidence"])
        boxes[0]["box"] = [0, 0, 1, 1]
        tests.append((changed("evidence", boxes), 7))
        extra = dict(answer, extra="refuse")
        tests += [(extra, None), (check.example_answer(truths[1 - index]), None)]
        for candidate, fail in tests:
            result = flags(candidate)
            assert not any(result) if fail is None else not result[fail]
            cases += 1
        reversed_boxes = changed("evidence", list(reversed(answer["evidence"])))
        assert all(flags(reversed_boxes))
        assert json.loads((folder / "input.json").read_text()) == starting(truth)
        assert sorted(p.name for p in folder.joinpath("photos").iterdir()) == ["1.jpg", "2.jpg"]
        expected_evidence = [{"photo": photo, "box": entry["box"]}
                             for photo, entries in enumerate(layouts[folder.name]["photos"], 1)
                             for entry in entries if entry["target"]]
        assert expected_evidence == truth["evidence"]
        for photo, entries in enumerate(layouts[folder.name]["photos"], 1):
            assert {e["ann_id"] for e in entries} == {s["ann_id"] for s in sources if s["sample"] == folder.name and s["photo"] == photo}
        for photo in folder.joinpath("photos").iterdir():
            with Image.open(photo) as image:
                assert image.size == (1024, 768) and image.mode == "RGB" and not image.getexif()
                image.verify()
        with wave.open(str(folder / "request.wav")) as wav:
            assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 24000)
            seconds = wav.getnframes() / wav.getframerate()
            assert 5 < seconds < 45
            import array
            pcm = array.array("h", wav.readframes(wav.getnframes()))
            assert sum(abs(v) for v in pcm) / len(pcm) > 100
        summary[folder.name] = {"audio_seconds": round(seconds, 2), "photos": 2, "direct_json": True, "objects_changed": abs(truth["change"])}
    hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sample_files()}
    save(ROOT / "validation.json", {"samples": summary, "negative_cases": cases, "good_and_reordered_evidence_pass": True,
         "blank_scores_zero": True, "sha256": hashes, "live_calls": 0, "baseline_measured": False})
    print(json.dumps({"samples": summary, "negative_cases": cases, "live_calls": 0}))


def pack():
    verify()
    files = sample_files()
    files += [ROOT / name for name in ("README.md", "task.json", "eval/check.py", "attribution.json", "validation.json", "overview.jpg", "review.html", "review/layouts.json", "review/controller_truth/scripts.json")]
    with zipfile.ZipFile(ROOT / "inventory-update-dataset.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(ROOT).as_posix())
    print("Packed inventory-update-dataset.zip without LX runtime state.")


def sample_files():
    return [ROOT / "samples" / name / filename for name in ("sample-01", "sample-02")
            for filename in ("photos/1.jpg", "photos/2.jpg", "request.wav", "input.json", "truth.json")]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=("build", "voice", "review", "verify", "pack"))
    parser.add_argument("--root", type=Path, default=ROOT, help="Fresh output folder; existing samples are never overwritten")
    args = parser.parse_args()
    ROOT = args.root
    WORK = ROOT / "review"
    {"build": build, "voice": voice, "review": review, "verify": verify, "pack": pack}[args.step]()
