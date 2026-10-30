"""T7 calibration logic offline: doubles only, no credentials or network."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lx import inventory_calibration as calibration
from lx.budget import BudgetLedger
from lx.inventory import FakeShapeSegmentation
from lx.inventory_data import build_synthetic_dataset
from lx.inventory_probe import Probe
from lx.inventory_workflow import InventoryPreviewTransport, preview_transcription
from lx.perception import PerceptionCache, PerceptionClient
from lx.spark import ModelError, SparkClient


class Failing:
    def __init__(self):
        self.calls = 0

    def __call__(self, body, timeout):
        self.calls += 1
        raise ModelError("server said no")


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        scenes, requests = build_synthetic_dataset(self.root / "ds", 4, stream_count=0)
        self.ledger = BudgetLedger(self.root / "ledger.sqlite3", initial_spend_usd=0)
        self.perception = PerceptionClient(FakeShapeSegmentation(scenes), preview_transcription(self.root / "ds", requests),
                                           self.ledger, PerceptionCache(self.root / "cache"))
        self.probe = Probe(self.root / "ds")
        self.items = ["synth-01", "synth-02", "synth-03", "synth-04"]

    def test_scores_every_sample_with_the_base_harness(self):
        client = SparkClient(InventoryPreviewTransport(), self.ledger)
        report = calibration.run_calibration(self.probe, self.root / "out", client, self.perception, items=self.items)
        self.assertEqual(sorted(report["results"]), self.items)
        for result in report["results"].values():
            self.assertNotIn("run_script", [c["name"] for c in result["tool_calls"]])
            self.assertIn(result["request_type"], ("count", "colour_count"))
            self.assertTrue(0 <= result["score"] <= 1)
        summary = report["summary"]
        self.assertEqual(summary["items"], 4)
        self.assertAlmostEqual(summary["mean"], sum(r["score"] for r in report["results"].values()) / 4)
        self.assertEqual(report["headroom"], 0.3 <= summary["mean"] <= 0.8)
        self.assertFalse(report["live"])
        saved = json.loads((self.root / "out" / "calibration.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["summary"], json.loads(json.dumps(summary)))

    def test_repeated_model_errors_stop_the_run(self):
        transport = Failing()
        report = calibration.run_calibration(self.probe, self.root / "out", SparkClient(transport, self.ledger, retries=0),
                                             self.perception, items=self.items)
        self.assertEqual(len(report["errors"]), calibration.MAX_ERRORS)
        self.assertEqual(len(report["results"]), calibration.MAX_ERRORS)
        self.assertFalse(report["headroom"])
        self.assertIn("stopped on model errors", report["decision"])

    def test_defaults_to_the_calibration_split(self):
        self.probe.samples["synth-03"] = dict(self.probe.samples["synth-03"], split="calibration")
        client = SparkClient(InventoryPreviewTransport(), self.ledger)
        report = calibration.run_calibration(self.probe, self.root / "out", client, self.perception)
        self.assertEqual(report["items"], ["synth-03"])

    def test_main_refuses_a_wrong_approval(self):
        with patch.object(sys, "argv", ["inventory_calibration", "--approve-dataset", "0" * 64]):
            with self.assertRaises(SystemExit):
                calibration.main()

    def test_guard_refuses_old_ledger_and_reused_scope(self):
        path = self.root / "pilot.sqlite3"
        ledger = BudgetLedger(path)
        with patch.object(calibration, "LEDGER", path), self.assertRaisesRegex(ValueError, "older than"):
            calibration.guarded_ledger()
        with patch.multiple(calibration, LEDGER=path, CHECKPOINT_SPENT=0, CHECKPOINT_CALLS=0):
            calibration.guarded_ledger()
            reservation = ledger.reserve("0", "tool", "segment", calibration.SCOPE + "/segment/x")
            ledger.settle(reservation, cost_usd=0, input_tokens=0, output_tokens=0)
            with self.assertRaisesRegex(ValueError, "already dispatched"):
                calibration.guarded_ledger()

    def test_fixed_request_constants(self):
        self.assertEqual(calibration.RUN_CAP_USD, "0.60")
        self.assertEqual(calibration.HEADROOM, (0.30, 0.80))
        self.assertEqual(calibration.LIMITS, {"chat": 120, "segment": 120, "transcribe": 8})
        self.assertEqual(len(calibration.APPROVED_DATASET), 64)
        self.assertTrue(str(calibration.DATASET).endswith("inventory-mix-v1"))


if __name__ == "__main__":
    unittest.main()
