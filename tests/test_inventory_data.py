"""Inventory dataset intake (user folders), COCO held-out builder, freezing (offline)."""

import io
import json
import random
import tempfile
import unittest
import wave
from pathlib import Path

from PIL import Image

from lx.inventory import validate_truth
from lx.inventory_data import (COCO_KINDS, FSC_KINDS, add_coco_samples, add_fsc_samples, add_user_samples,
                               check_sample_folder, finalize_inventory, load_inventory_dataset, normalize_photo)

from test_voicecount import silence

TRUTH = {"request_type": "count", "kind": "pen", "colour": None,
         "photos": {"1": [{"kind": "pen", "colour": "blue", "count": 2}], "2": [], "3": [{"kind": "mug", "count": 1}]}}


def photo(size=(300, 200), colour=(10, 120, 200), fmt="JPEG", exif=None):
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, fmt, **({"exif": exif} if exif else {}))
    return out.getvalue()


def stereo_wav():
    out = io.BytesIO()
    with wave.open(out, "wb") as clip:
        clip.setnchannels(2)
        clip.setsampwidth(2)
        clip.setframerate(44100)
        clip.writeframes(b"\0\0\0\0" * 4410)
    return out.getvalue()


def make_sample(root, name, truth=TRUTH, n=3, wav=None, text="How many pens are there?"):
    folder = Path(root) / name
    (folder / "photos").mkdir(parents=True)
    for k in range(1, n + 1):
        (folder / "photos" / f"{k}.jpg").write_bytes(photo(colour=(k * 40, 50, 60)))
    (folder / "truth.json").write_text(json.dumps(truth))
    if wav is not None:
        (folder / "request.wav").write_bytes(wav)
    if text is not None:
        (folder / "request.txt").write_text(text)
    return folder


class PhotoTests(unittest.TestCase):
    def test_orientation_applied_and_metadata_dropped(self):
        exif = Image.Exif()
        exif[0x0112] = 6  # rotate 90 degrees on display
        exif[0x010F] = "PhoneMaker"
        data = normalize_photo(photo((300, 200), exif=exif.tobytes()))
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual((image.format, image.size), ("JPEG", (200, 300)))
            self.assertEqual(dict(image.getexif()), {})

    def test_downscale_png_and_reject_non_images(self):
        with Image.open(io.BytesIO(normalize_photo(photo((3000, 1500), fmt="PNG")))) as image:
            self.assertEqual(image.size, (1024, 512))
        for data in (b"not an image", photo(fmt="GIF")):
            with self.assertRaises(ValueError):
                normalize_photo(data)


class FolderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_valid_folder(self):
        paths, truth, wav, text = check_sample_folder(make_sample(self.root, "sample-01", wav=silence()))
        self.assertEqual([p.name for p in paths], ["1.jpg", "2.jpg", "3.jpg"])
        self.assertEqual((truth, wav.name, text), (TRUTH, "request.wav", "How many pens are there?"))

    def test_invalid_folders(self):
        cases = {"count mismatch": dict(n=4), "no request": dict(text=None),
                 "bad wav": dict(wav=stereo_wav()), "bad truth": dict(truth=dict(TRUTH, kind=None))}
        for index, (name, kwargs) in enumerate(cases.items()):
            with self.subTest(case=name):
                with self.assertRaises(ValueError) as error:
                    check_sample_folder(make_sample(self.root, f"sample-{index}", **kwargs))
                if name == "bad wav":
                    self.assertIn("ffmpeg", str(error.exception))
        folder = make_sample(self.root, "sample-x")
        (folder / "photos" / "notes.txt").write_text("x")
        with self.assertRaisesRegex(ValueError, "unexpected file"):
            check_sample_folder(folder)

    def test_user_intake_splits_and_freezes(self):
        source, dataset = self.root / "src", self.root / "ds"
        make_sample(source, "sample-a", wav=silence())
        make_sample(source, "sample-b")
        make_sample(source, "sample-c", text="Count the pens.")
        (source / "notes").mkdir()
        with self.assertRaisesRegex(ValueError, "only 3 sample folders"):
            add_user_samples(dataset, source, stream_count=3, calibration_count=1)
        self.assertEqual(add_user_samples(dataset, source, stream_count=1, calibration_count=1),
                         ["user-01", "user-02", "user-03"])
        manifest = json.loads((dataset / "worker_inputs" / "manifest.json").read_text())
        self.assertEqual([s["split"] for s in manifest["samples"]], ["stream", "calibration", "heldout"])
        sources = json.loads((dataset / "controller_truth" / "sources.json").read_text())
        self.assertEqual(sorted(s["folder"] for s in sources.values()), ["sample-a", "sample-b", "sample-c"])
        recorded = next(i for i, s in sources.items() if s["folder"] == "sample-a")
        scripts = json.loads((dataset / "controller_truth" / "scripts.json").read_text())
        self.assertEqual(sorted(scripts), sorted(set(sources) - {recorded}))
        requests = json.loads((dataset / "controller_truth" / "requests.json").read_text())
        self.assertEqual(requests[recorded]["audio"], "recorded")
        with self.assertRaisesRegex(ValueError, "audio missing"):
            finalize_inventory(dataset)
        for sample_id in scripts:  # stands in for the Kokoro synthesis step
            (dataset / "worker_inputs" / "media" / f"{sample_id}.wav").write_bytes(silence(0.5))
        hashes = finalize_inventory(dataset)
        self.assertEqual(len(hashes), 3 * 4)
        samples, truth, _ = load_inventory_dataset(dataset)
        self.assertEqual(truth[recorded], TRUTH)
        with self.assertRaisesRegex(ValueError, "frozen"):
            add_user_samples(dataset, source, stream_count=1)
        (dataset / "worker_inputs" / samples[0]["photos"][0]).write_bytes(photo(colour=(0, 0, 0)))
        with self.assertRaisesRegex(ValueError, "changed"):
            load_inventory_dataset(dataset)


def coco_fixture(n_images=120, seed=3):
    rng = random.Random(seed)
    names = sorted(COCO_KINDS)[:4]
    categories = [{"id": i + 1, "name": n} for i, n in enumerate(names + ["person"])]
    images, annotations, aid = [], [], 0
    for image_id in range(1, n_images + 1):
        images.append({"id": image_id, "file_name": f"{image_id:012d}.jpg", "width": 640, "height": 480,
                       "license": 4 if image_id % 5 else 2, "coco_url": None, "flickr_url": f"http://f/{image_id}"})
        for category in rng.sample(categories, 2):
            for _ in range(rng.randint(1, 4)):
                aid += 1
                annotations.append({"id": aid, "image_id": image_id, "category_id": category["id"],
                                    "area": 2000 if image_id % 7 else 900, "iscrowd": 1 if image_id == 3 else 0})
    licenses = [{"id": 2, "name": "BY-NC"}, {"id": 4, "name": "CC BY 2.0"}]
    return {"images": images, "annotations": annotations, "categories": categories, "licenses": licenses}


class CocoTests(unittest.TestCase):
    def test_builds_count_samples_with_licence_and_quality_filters(self):
        annotations = coco_fixture()
        loaded = []
        with tempfile.TemporaryDirectory() as tmp:
            def load(name):
                loaded.append(name)
                return photo((640, 480), colour=(len(loaded) % 255, 0, 0))
            added = add_coco_samples(Path(tmp) / "ds", annotations, load, 4, seed=5)
            self.assertEqual(added, [f"coco-heldout-{i:02d}" for i in range(1, 5)])
            truth = json.loads((Path(tmp) / "ds" / "controller_truth" / "ground_truth.json").read_text())
            sources = json.loads((Path(tmp) / "ds" / "controller_truth" / "sources.json").read_text())
            used = [p["coco_id"] for s in sources.values() for p in s["photos"]]
            self.assertEqual(len(used), len(set(used)))
            by_id = {i["id"]: i for i in annotations["images"]}
            categories = {c["id"]: c["name"] for c in annotations["categories"]}
            for sample_id, t in truth.items():
                validate_truth(t)
                self.assertEqual((t["request_type"], sources[sample_id]["truth_scope"]), ("count", "target"))
                for k, entry in enumerate(sources[sample_id]["photos"], 1):
                    image_id = entry["coco_id"]
                    self.assertEqual(by_id[image_id]["license"], 4)
                    self.assertEqual(entry["license"], "CC BY 2.0")
                    labelled = [a for a in annotations["annotations"] if a["image_id"] == image_id
                                and COCO_KINDS.get(categories[a["category_id"]]) == t["kind"]]
                    self.assertEqual(sum(i["count"] for i in t["photos"][str(k)]), len(labelled))
                    self.assertTrue(all(a["area"] >= 1600 and not a["iscrowd"] for a in labelled))
                    self.assertFalse(any(categories[a["category_id"]] == "person" for a in annotations["annotations"]
                                         if a["image_id"] == image_id))
                self.assertLessEqual(sum(1 for items in t["photos"].values() if not items), 1)
            scripts = json.loads((Path(tmp) / "ds" / "controller_truth" / "scripts.json").read_text())
            self.assertEqual(sorted(scripts), added)
            self.assertTrue(all(s["text"] for s in scripts.values()))
            more = add_coco_samples(Path(tmp) / "ds", annotations, load, 1, split="calibration", seed=6)
            sources = json.loads((Path(tmp) / "ds" / "controller_truth" / "sources.json").read_text())
            again = [p["coco_id"] for p in sources[more[0]]["photos"]]
            self.assertFalse(set(again) & set(used))

    def test_too_few_images_is_an_error_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "usable COCO samples"):
                add_coco_samples(Path(tmp) / "ds", coco_fixture(n_images=12), lambda n: photo(), 5)
            media = Path(tmp) / "ds" / "worker_inputs" / "media"
            self.assertFalse(media.exists() and any(media.iterdir()))


def fsc_fixture(root, per_class=8, seed=4):
    """A tiny FSC-147 layout: varied random-block photos, one exact copy among the pens."""
    from PIL import ImageDraw

    rng = random.Random(seed)
    root = Path(root)
    (root / "images").mkdir(parents=True)
    annotations, lines, number = {}, [], 0
    for name in ("pens", "pencils", "apples", "geese"):
        for k in range(per_class):
            number += 1
            image = Image.new("RGB", (480, 384), (rng.randint(0, 255),) * 3)
            draw = ImageDraw.Draw(image)
            for _ in range(12):
                x, y = rng.randint(0, 440), rng.randint(0, 340)
                draw.rectangle((x, y, x + 40, y + 40), fill=tuple(rng.randint(0, 255) for _ in range(3)))
            out = io.BytesIO()
            image.save(out, "JPEG")
            (root / "images" / f"{number}.jpg").write_bytes(out.getvalue())
            dots = 50 if k == per_class - 1 else 3 + k
            annotations[f"{number}.jpg"] = {"points": [[1.0, 2.0]] * dots}
            lines.append(f"{number}.jpg\t{name}")
    (root / "images" / "2.jpg").write_bytes((root / "images" / "1.jpg").read_bytes())
    (root / "annotation_FSC147_384.json").write_text(json.dumps(annotations))
    (root / "ImageClasses_FSC147.txt").write_text("\n".join(lines) + "\n")
    return annotations


class FscTests(unittest.TestCase):
    def test_builds_multi_photo_count_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            fsc, dataset = Path(tmp) / "fsc", Path(tmp) / "ds"
            annotations = fsc_fixture(fsc, per_class=12)
            added = add_fsc_samples(dataset, fsc, 4, seed=8, exclude={"3.jpg"})
            self.assertEqual(added, [f"fsc-heldout-{i:02d}" for i in range(1, 5)])
            truth = json.loads((dataset / "controller_truth" / "ground_truth.json").read_text())
            sources = json.loads((dataset / "controller_truth" / "sources.json").read_text())
            used = [p["fsc_image"] for s in sources.values() for p in s["photos"]]
            self.assertEqual(len(used), len(set(used)))
            self.assertNotIn("3.jpg", used)
            for s in sources.values():  # an exact copy never shares a sample with its original
                self.assertFalse({"1.jpg", "2.jpg"} <= {p["fsc_image"] for p in s["photos"]})
            self.assertEqual(len({t["kind"] for t in truth.values()}), 3)  # unused kinds come first
            for sample_id, t in truth.items():
                validate_truth(t)
                self.assertIn(t["kind"], FSC_KINDS.values())
                for k, entry in enumerate(sources[sample_id]["photos"], 1):
                    items = t["photos"][str(k)]
                    if FSC_KINDS.get(entry["class"]) == t["kind"]:
                        self.assertEqual(items, [{"kind": t["kind"], "count": len(annotations[entry["fsc_image"]]["points"])}])
                        self.assertLessEqual(items[0]["count"], 40)
                    else:
                        self.assertEqual(items, [])
                        self.assertNotEqual({t["kind"], FSC_KINDS[entry["class"]]}, {"pen", "pencil"})
            more = add_fsc_samples(dataset, fsc, 1, split="calibration", seed=9)
            sources = json.loads((dataset / "controller_truth" / "sources.json").read_text())
            self.assertFalse({p["fsc_image"] for p in sources[more[0]]["photos"]} & set(used))

    def test_shortfall_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            fsc_fixture(Path(tmp) / "fsc", per_class=4)
            with self.assertRaisesRegex(ValueError, "usable FSC-147 samples"):
                add_fsc_samples(Path(tmp) / "ds", Path(tmp) / "fsc", 6)
            media = Path(tmp) / "ds" / "worker_inputs" / "media"
            self.assertFalse(media.exists() and any(media.iterdir()))


if __name__ == "__main__":
    unittest.main()
