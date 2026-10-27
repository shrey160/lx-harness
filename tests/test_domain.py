import json
import math
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from lx.domain import (
    BOX_EDGE_TOLERANCE_PX,
    IMAGE_HEIGHT,
    IMAGE_WIDTH,
    build_demo_dataset,
    verify_output,
)


class DemoDomainTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.dataset = build_demo_dataset(Path(self.temp_dir.name) / "demo")
        self.sample_id = self.dataset.worker_inputs[0]["sample_id"]
        self.truth = self.dataset.controller_truth[self.sample_id]

    def tearDown(self):
        self.temp_dir.cleanup()

    def good_output(self):
        return {
            "objects": [
                {"label": obj["label"], "box": list(obj["box"])}
                for obj in self.truth["objects"]
            ],
            "text": self.truth["text"],
        }

    def test_accepts_known_good_and_order_independent_output(self):
        result = verify_output(self.truth, self.good_output())
        self.assertTrue(result.accepted, result.errors)
        reordered = self.good_output()
        reordered["objects"].reverse()
        self.assertTrue(verify_output(self.truth, reordered).accepted)

    def test_accepts_box_edges_within_documented_one_pixel_tolerance(self):
        output = self.good_output()
        output["objects"][0]["box"][0] += BOX_EDGE_TOLERANCE_PX
        self.assertTrue(verify_output(self.truth, output).accepted)

    def test_rejects_missing_object(self):
        output = self.good_output()
        output["objects"].pop()
        self.assertFalse(verify_output(self.truth, output).accepted)

    def test_rejects_box_shift_beyond_tolerance(self):
        output = self.good_output()
        output["objects"][0]["box"][0] += BOX_EDGE_TOLERANCE_PX + 1
        self.assertFalse(verify_output(self.truth, output).accepted)

    def test_rejects_wrong_transcript(self):
        output = self.good_output()
        output["text"] = "A red square sits beside a green rectangle."
        self.assertFalse(verify_output(self.truth, output).accepted)

    def test_rejects_malformed_extra_and_non_finite_fields(self):
        malformed = self.good_output()
        malformed["objects"][0]["box"] = [8, 10, 31]
        self.assertFalse(verify_output(self.truth, malformed).accepted)

        extra = self.good_output()
        extra["confidence"] = 1.0
        self.assertFalse(verify_output(self.truth, extra).accepted)

        not_finite = self.good_output()
        not_finite["objects"][0]["box"][0] = math.nan
        self.assertFalse(verify_output(self.truth, not_finite).accepted)

        bool_coordinate = self.good_output()
        bool_coordinate["objects"][0]["box"][0] = True
        self.assertFalse(verify_output(self.truth, bool_coordinate).accepted)

        extra_object_field = self.good_output()
        extra_object_field["objects"][0]["confidence"] = 0.99
        self.assertFalse(verify_output(self.truth, extra_object_field).accepted)

    def test_split_is_by_whole_source_scene_and_truth_is_separate(self):
        manifest_path = self.dataset.root / "worker_inputs" / "manifest.json"
        truth_path = self.dataset.root / "controller_truth" / "ground_truth.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        truth_file = json.loads(truth_path.read_text(encoding="utf-8"))
        self.assertEqual({"dev", "heldout"}, {item["split"] for item in manifest["samples"]})
        self.assertEqual(2, sum(item["split"] == "dev" for item in manifest["samples"]))
        self.assertEqual(2, sum(item["split"] == "heldout" for item in manifest["samples"]))
        by_scene = {}
        for item in manifest["samples"]:
            by_scene.setdefault(item["source_scene_id"], set()).add(item["split"])
            self.assertNotIn("objects", item)
            self.assertNotIn("box", item)
        self.assertTrue(all(len(splits) == 1 for splits in by_scene.values()))
        self.assertFalse(truth_path.is_relative_to(self.dataset.root / "worker_inputs"))
        self.assertEqual(set(item["sample_id"] for item in manifest["samples"]), set(truth_file))

    def test_generated_images_are_valid_pngs_of_expected_dimensions(self):
        for record in self.dataset.worker_inputs:
            image_path = self.dataset.root / "worker_inputs" / record["image"]
            data = image_path.read_bytes()
            self.assertEqual(b"\x89PNG\r\n\x1a\n", data[:8])
            offset = 8
            chunk_kinds = []
            while offset < len(data):
                length = struct.unpack(">I", data[offset : offset + 4])[0]
                kind = data[offset + 4 : offset + 8]
                content = data[offset + 8 : offset + 8 + length]
                crc = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])[0]
                self.assertEqual(zlib.crc32(kind + content) & 0xFFFFFFFF, crc)
                chunk_kinds.append(kind)
                if kind == b"IHDR":
                    width, height = struct.unpack(">II", content[:8])
                    self.assertEqual((IMAGE_WIDTH, IMAGE_HEIGHT), (width, height))
                offset += 12 + length
            self.assertEqual([b"IHDR", b"IDAT", b"IEND"], chunk_kinds)
            self.assertEqual(len(data), offset)


if __name__ == "__main__":
    unittest.main()
