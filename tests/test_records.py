import json
import tempfile
import unittest
from pathlib import Path

from lx.records import (
    FIELDS,
    MAX_SPLIT_COUNT,
    build_records_dataset,
    verify_record,
)


class RecordsDomainTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.dataset = build_records_dataset(Path(self.temp_dir.name) / "records", 2, 3, seed=91)
        self.sample_id = self.dataset.worker_inputs[0]["sample_id"]
        self.truth = self.dataset.controller_truth[self.sample_id]

    def tearDown(self):
        self.temp_dir.cleanup()

    def good_record(self):
        return {field: self.truth[field] for field in FIELDS}

    def test_accepts_known_good_record(self):
        result = verify_record(self.truth, self.good_record())
        self.assertTrue(result.accepted, result.errors)
        self.assertEqual(1.0, result.score)
        self.assertEqual(7, result.n_passed)
        self.assertEqual(7, result.n_criteria)
        self.assertEqual((), result.errors)

    def test_rejects_missing_field_and_scores_partial_credit(self):
        candidate = self.good_record()
        del candidate["phone"]
        result = verify_record(self.truth, candidate)
        self.assertFalse(result.accepted)
        self.assertIn("phone: required field is missing", result.errors)
        self.assertEqual((6, 7), (result.n_passed, result.n_criteria))
        self.assertAlmostEqual(6 / 7, result.score)

    def test_rejects_extra_field_and_reduces_score(self):
        candidate = self.good_record()
        candidate["confidence"] = 1.0
        result = verify_record(self.truth, candidate)
        self.assertFalse(result.accepted)
        self.assertTrue(any("unknown field" in error for error in result.errors))
        self.assertEqual((7, 8), (result.n_passed, result.n_criteria))
        self.assertAlmostEqual(7 / 8, result.score)

    def test_rejects_wrong_types_without_python_coercion(self):
        for field, wrong_value in (("amount_minor", True), ("customer", 12), ("tags", "priority")):
            with self.subTest(field=field):
                candidate = self.good_record()
                candidate[field] = wrong_value
                result = verify_record(self.truth, candidate)
                self.assertFalse(result.accepted)
                self.assertLess(result.score, 1.0)
                self.assertTrue(any(error.startswith(f"{field}:") for error in result.errors))

    def test_rejects_noncanonical_and_invalid_dates(self):
        for value in ("2025-2-03", "2025-02-30", "03 February 2025"):
            with self.subTest(value=value):
                candidate = self.good_record()
                candidate["date"] = value
                result = verify_record(self.truth, candidate)
                self.assertFalse(result.accepted)
                self.assertTrue(any(error.startswith("date:") for error in result.errors))

    def test_rejects_swapped_fields_and_wrong_values(self):
        candidate = self.good_record()
        candidate["order_id"], candidate["customer"] = candidate["customer"], candidate["order_id"]
        result = verify_record(self.truth, candidate)
        self.assertFalse(result.accepted)
        self.assertTrue(any(error.startswith("order_id:") for error in result.errors))
        self.assertTrue(any(error.startswith("customer:") for error in result.errors))
        self.assertLess(result.score, 1.0)

    def test_malformed_non_object_gets_zero(self):
        for value in (None, [], "{}"):
            result = verify_record(self.truth, value)
            self.assertFalse(result.accepted)
            self.assertEqual(0.0, result.score)
            self.assertEqual((0, 7), (result.n_passed, result.n_criteria))

    def test_manifest_contains_only_worker_fields_and_truth_is_separate(self):
        worker_dir = self.dataset.root / "worker_inputs"
        truth_dir = self.dataset.root / "controller_truth"
        manifest_path = worker_dir / "manifest.json"
        truth_path = truth_dir / "ground_truth.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        truth_file = json.loads(truth_path.read_text(encoding="utf-8"))
        records = manifest["samples"]
        self.assertEqual({"sample_id", "source_id", "split", "text"}, set(records[0]))
        self.assertTrue(all(set(record) == {"sample_id", "source_id", "split", "text"} for record in records))
        self.assertFalse(truth_path.is_relative_to(worker_dir))
        self.assertEqual({record["sample_id"] for record in records}, set(truth_file))
        self.assertTrue(all(set(record) == set(FIELDS) for record in truth_file.values()))

    def test_split_source_and_rendering_isolation(self):
        dev = [record for record in self.dataset.worker_inputs if record["split"] == "dev"]
        heldout = [record for record in self.dataset.worker_inputs if record["split"] == "heldout"]
        self.assertEqual(2, len(dev))
        self.assertEqual(3, len(heldout))
        self.assertFalse({item["source_id"] for item in dev} & {item["source_id"] for item in heldout})
        self.assertTrue(all(item["source_id"].startswith("dev-") for item in dev))
        self.assertTrue(all(item["source_id"].startswith("heldout-") for item in heldout))
        dev_text = "\n".join(item["text"] for item in dev)
        heldout_text = "\n".join(item["text"] for item in heldout)
        self.assertNotIn("Fulfillment slip", dev_text)
        self.assertNotIn("Order ", heldout_text)

    def test_generation_is_deterministic_and_seed_sensitive(self):
        root = Path(self.temp_dir.name)
        same = build_records_dataset(root / "same", 2, 3, seed=91)
        other = build_records_dataset(root / "other", 2, 3, seed=92)
        self.assertEqual(self.dataset.worker_inputs, same.worker_inputs)
        self.assertEqual(self.dataset.controller_truth, same.controller_truth)
        self.assertEqual(self.dataset.worker_inputs[0]["text"], same.worker_inputs[0]["text"])
        self.assertNotEqual(self.dataset.worker_inputs, other.worker_inputs)

    def test_increasing_dev_count_does_not_change_heldout_inputs(self):
        root = Path(self.temp_dir.name)
        smaller_dev = build_records_dataset(root / "few-dev", 1, 4, seed=772)
        larger_dev = build_records_dataset(root / "many-dev", 12, 4, seed=772)
        heldout_small = tuple(item for item in smaller_dev.worker_inputs if item["split"] == "heldout")
        heldout_large = tuple(item for item in larger_dev.worker_inputs if item["split"] == "heldout")
        self.assertEqual(heldout_small, heldout_large)
        for item in heldout_small:
            self.assertEqual(
                smaller_dev.controller_truth[item["sample_id"]],
                larger_dev.controller_truth[item["sample_id"]],
            )

    def test_configurable_counts_and_bounds(self):
        root = Path(self.temp_dir.name)
        small = build_records_dataset(root / "small", 1, 0, seed=4)
        self.assertEqual(1, len(small.worker_inputs))
        self.assertEqual("dev", small.worker_inputs[0]["split"])
        empty = build_records_dataset(root / "empty", 0, 0, seed=4)
        self.assertEqual((), empty.worker_inputs)
        for args in ((-1, 0), (0, MAX_SPLIT_COUNT + 1), (True, 1), (1.5, 1)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                build_records_dataset(root / f"invalid-{str(args[0])}", *args)


if __name__ == "__main__":
    unittest.main()
