"""Inventory adapter, synthetic dataset and the offline E-A preview (real core loop and sandbox)."""

import json
import tempfile
import unittest
from pathlib import Path

from lx import sandbox
from lx.inventory import WORDING, synthetic_sample
from lx.inventory_data import build_synthetic_dataset, load_inventory_dataset
from lx.inventory_workflow import InventoryDomain, parse_request, preview_stream


class DomainTests(unittest.TestCase):
    def test_parse_request_covers_every_wording(self):
        for request_type, templates in WORDING.items():
            for template in templates:
                text = template.format(kinds="paper clips", colour="green")
                expected = (request_type, None if request_type == "inventory" else "paper clip",
                            "green" if request_type == "colour_count" else None)
                with self.subTest(text=text):
                    self.assertEqual(parse_request(text), expected)
        self.assertEqual(parse_request("Something else entirely."), (None, None, None))

    def test_manifest_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_synthetic_dataset(Path(tmp) / "ds", 2, stream_count=1)
            root = Path(tmp) / "ds" / "worker_inputs"
            _, samples = InventoryDomain.samples_from(root)
            self.assertEqual([s["split"] for s in samples], ["dev", "heldout"])
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            bad = [dict(manifest["samples"][0], extra=1), dict(manifest["samples"][0], photos=["../x.jpg"] * 3),
                   dict(manifest["samples"][0], photos=manifest["samples"][0]["photos"][:2]),
                   dict(manifest["samples"][0], split="test")]
            for sample in bad:
                with self.subTest(sample=sorted(sample)):
                    (root / "manifest.json").write_text(json.dumps({"samples": [sample]}), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        InventoryDomain.samples_from(root)

    def test_synthetic_dataset_is_frozen_and_skips_inventory_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            scenes, requests = build_synthetic_dataset(Path(tmp) / "ds", 4)
            samples, truth, stored = load_inventory_dataset(Path(tmp) / "ds")
            self.assertEqual(len(samples), 4)
            self.assertTrue(all(t["request_type"] in ("count", "colour_count") for t in truth.values()))
            self.assertEqual(len(scenes), sum(len(s["photos"]) for s in samples))
            self.assertEqual(stored, requests)
            self.assertEqual(len({(Path(tmp) / "ds" / "worker_inputs" / s["audio"]).read_bytes() for s in samples}), 4)


@unittest.skipUnless(sandbox.available(), "preview runs helper scripts in the sandbox")
class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.temp.name) / "preview"
        cls.report = preview_stream(cls.out, count=2)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_loop_adds_skill_and_script_and_the_agent_uses_them(self):
        report = self.report
        self.assertEqual(report["run"]["done"], 2)
        for task, row in report["per_sample"].items():
            with self.subTest(task=task):
                self.assertEqual(row["status"], "ok")
                self.assertFalse(row["chosen_is_base"])
                self.assertEqual(row["files_added"], ["scripts/sum_counts.py", "skills/inventory-check/SKILL.md"])
                self.assertLess(row["base"]["score"], 1.0)
                self.assertGreater(row["harness2"]["score"], row["base"]["score"])
        traces = [json.loads(p.read_text(encoding="utf-8")) for p in (self.out / "run" / "runs").rglob("trace.json")]
        used = [step for t in traces for step in t["trace"] if step["type"] == "tool" and step["name"] == "run_script"]
        self.assertTrue(used)
        self.assertTrue(all(step["result"]["exit_code"] == 0 for step in used))
        self.assertEqual(report["run"]["tasks"][1]["library_entries_before"], {"seq": 1})

    def test_improver_context_holds_no_truth_or_scores(self):
        truth = json.loads((self.out / "dataset" / "controller_truth" / "ground_truth.json").read_text(encoding="utf-8"))
        staged = list((self.out / "run" / "sources").rglob("*"))
        self.assertTrue(staged)
        for path in staged:
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("request_type", text)
                self.assertNotIn(json.dumps(next(iter(truth.values()))["photos"]), text)
        audits = [p.read_text(encoding="utf-8") for p in (self.out / "run" / "proposer_audit").rglob("*") if p.is_file()]
        self.assertTrue(audits)
        self.assertFalse(any('"score"' in a or "ground_truth" in a for a in audits))


if __name__ == "__main__":
    unittest.main()
