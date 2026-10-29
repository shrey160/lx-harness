"""COCO cutout selection, extraction and composite samples (offline)."""

import io
import json
import random
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from lx.inventory import expected_answer, validate_truth
from lx.inventory_cutouts import (MAX_USES, add_composite_samples, compose_photo, cutout_candidates,
                                  extract_cutout, prepare_cutouts, select_photos)

COLOURS_RGB = {"red": (220, 30, 30), "blue": (30, 70, 220), "green": (40, 170, 70), "yellow": (240, 210, 40)}


def square(x, y, side):
    return [x, y, x + side, y, x + side, y + side, x, y + side]


def cutout_fixture(n_images=60, seed=2):
    """Photos with solid-colour squares (cups, balls, books) plus edge, overlap, tiny and person cases."""
    rng = random.Random(seed)
    categories = [{"id": 1, "name": "cup"}, {"id": 2, "name": "sports ball"}, {"id": 3, "name": "book"},
                  {"id": 4, "name": "person"}, {"id": 5, "name": "airplane"}]
    images, annotations, pixels, aid = [], [], {}, 0
    for image_id in range(1, n_images + 1):
        name = f"{image_id:012d}.jpg"
        images.append({"id": image_id, "file_name": name, "width": 640, "height": 480,
                       "license": 4 if image_id % 6 else 5, "flickr_url": f"http://f/{image_id}", "coco_url": None})
        photo = Image.new("RGB", (640, 480), (128, 128, 128))
        draw = ImageDraw.Draw(photo)
        for slot in range(2):
            aid += 1
            colour = rng.choice(sorted(COLOURS_RGB))
            x, y, side = 40 + slot * 300, 100, 120
            draw.polygon(square(x, y, side), fill=COLOURS_RGB[colour])
            annotations.append({"id": aid, "image_id": image_id, "category_id": rng.choice((1, 2, 3)),
                                "segmentation": [square(x, y, side)], "area": side * side, "iscrowd": 0,
                                "bbox": [x, y, side, side], "colour": colour})
        if image_id % 10 == 1:  # touches the edge
            aid += 1
            annotations.append({"id": aid, "image_id": image_id, "category_id": 1, "segmentation": [square(0, 300, 90)],
                                "area": 8100, "iscrowd": 0, "bbox": [0, 300, 90, 90]})
        if image_id % 10 == 2:  # overlapped by an airplane label
            annotations.append({"id": aid + 1, "image_id": image_id, "category_id": 5, "segmentation": [square(80, 140, 60)],
                                "area": 3600, "iscrowd": 0, "bbox": [80, 140, 60, 60]})
            aid += 1
        if image_id % 10 == 3:  # a person in the photo
            annotations.append({"id": aid + 1, "image_id": image_id, "category_id": 4, "segmentation": [square(500, 400, 40)],
                                "area": 1600, "iscrowd": 0, "bbox": [500, 400, 40, 40]})
            aid += 1
        if image_id % 10 == 4:  # too small
            annotations.append({"id": aid + 1, "image_id": image_id, "category_id": 2, "segmentation": [square(250, 350, 30)],
                                "area": 900, "iscrowd": 0, "bbox": [250, 350, 30, 30]})
            aid += 1
        out = io.BytesIO()
        photo.save(out, "JPEG", quality=95)
        pixels[name] = out.getvalue()
    licenses = [{"id": 4, "name": "CC BY 2.0"}, {"id": 5, "name": "CC BY-SA 2.0"}]
    return {"images": images, "annotations": annotations, "categories": categories, "licenses": licenses}, pixels


class CandidateTests(unittest.TestCase):
    def test_filters(self):
        annotations, _ = cutout_fixture()
        found = cutout_candidates(annotations)
        by_image = {i["id"]: i for i in annotations["images"]}
        self.assertTrue(found)
        for c in found:
            self.assertEqual(by_image[c["image_id"]]["license"], 4)
            self.assertNotEqual(c["image_id"] % 10, 3)
            self.assertGreaterEqual(min(c["bbox"][2:]), 64)
            self.assertGreater(c["bbox"][0], 0)
            self.assertIn(c["kind"], {"cup", "ball", "book"})
        overlapped = {c["image_id"] for c in found if c["image_id"] % 10 == 2 and c["bbox"][0] == 40}
        self.assertFalse(overlapped)

    def test_extract_keeps_the_polygon_only(self):
        annotations, pixels = cutout_fixture(n_images=5)
        candidate = cutout_candidates(annotations)[0]
        cutout = extract_cutout(pixels[candidate["file_name"]], candidate)
        self.assertEqual(cutout.mode, "RGBA")
        self.assertEqual(cutout.getpixel((cutout.width // 2, cutout.height // 2))[3], 255)
        self.assertEqual(cutout.getpixel((0, 0))[3] < 255, True)


    def test_select_photos_covers_kinds(self):
        candidates = [{"kind": k, "file_name": f"{k}-{i}.jpg"} for k, n in (("cup", 20), ("ball", 2), ("book", 5))
                      for i in range(n)]
        chosen = select_photos(candidates, 9, seed=1)
        self.assertEqual(len(chosen), 9)
        self.assertEqual(sum(f.startswith("ball") for f in chosen), 2)
        self.assertGreaterEqual(sum(f.startswith("book") for f in chosen), 3)
        self.assertEqual(chosen, select_photos(candidates, 9, seed=1))


class CompositeTests(unittest.TestCase):
    def setUp(self):
        annotations, pixels = cutout_fixture()
        truth_colour = {a["id"]: a.get("colour") for a in annotations["annotations"]}
        self.cutouts = prepare_cutouts(cutout_candidates(annotations), pixels.__getitem__)
        self.truth_colour = truth_colour

    def test_colour_rule_matches_the_painted_colour(self):
        self.assertTrue(all(c["colour"] == self.truth_colour[c["ann_id"]] for c in self.cutouts))

    def test_compose_is_repeatable_and_none_when_crowded(self):
        first = compose_photo(self.cutouts[:3], random.Random(4))
        self.assertEqual(first, compose_photo(self.cutouts[:3], random.Random(4)))
        with Image.open(io.BytesIO(first)) as image:
            self.assertEqual(image.size, (1024, 768))
        self.assertIsNone(compose_photo(self.cutouts[:3], random.Random(4), size=(200, 150)))

    def test_samples_have_exact_truth_and_respect_use_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset = Path(tmp) / "ds"
            made = {}
            for request_type in ("colour_count", "inventory", "count"):
                made[request_type] = add_composite_samples(dataset, self.cutouts, 2, request_type=request_type,
                                                           split="stream", seed=3)
            hidden = add_composite_samples(dataset, self.cutouts, 2, request_type="inventory", split="heldout", seed=3)
            truth = json.loads((dataset / "controller_truth" / "ground_truth.json").read_text())
            sources = json.loads((dataset / "controller_truth" / "sources.json").read_text())
            uses, groups = {}, {}
            for sample_id, source in sources.items():
                t = truth[sample_id]
                validate_truth(t)
                seen = [a for p in source["photos"] for a in p["ann_ids"]]
                self.assertEqual(len(seen), len(set(seen)))  # never twice in one sample
                for a in seen:
                    uses[a] = uses.get(a, 0) + 1
                    groups.setdefault(a, set()).add("hidden" if sample_id in hidden else "bundle")
                for n, photo in enumerate(source["photos"], 1):
                    pasted = sum(i["count"] for i in t["photos"][str(n)])
                    self.assertEqual(pasted, len(photo["objects"]))  # truth lists every pasted object
                if t["request_type"] == "colour_count":
                    asked = sum(1 for p in source["photos"] for o in p["objects"]
                                if o["kind"] == t["kind"] and o["colour"] == t["colour"])
                    self.assertEqual(expected_answer(t), asked)
                    self.assertTrue(all(o["colour"] for p in source["photos"] for o in p["objects"] if o["kind"] == t["kind"]))
                if t["request_type"] == "count":
                    self.assertGreater(expected_answer(t), 0)
            self.assertLessEqual(max(uses.values()), MAX_USES)
            self.assertTrue(all(len(g) == 1 for g in groups.values()))
            self.assertEqual(len(made["colour_count"]) + len(hidden), 4)

    def test_shortfall_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "usable inventory composites"):
                add_composite_samples(Path(tmp) / "ds", self.cutouts[:4], 3, request_type="inventory")
            media = Path(tmp) / "ds" / "worker_inputs" / "media"
            self.assertFalse(media.exists() and any(media.iterdir()))


if __name__ == "__main__":
    unittest.main()
