"""E-B evaluation logic offline: doubles only, no credentials or network."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lx import inventory_eval as evaluation
from lx import sandbox
from lx.budget import BudgetLedger
from lx.inventory import FakeShapeSegmentation
from lx.inventory_data import build_synthetic_dataset
from lx.inventory_probe import Probe, probe_harness
from lx.inventory_workflow import InventoryPreviewTransport, preview_transcription
from lx.perception import PerceptionCache, PerceptionClient
from lx.spark import ModelError, ChatClient


class Failing:
    def __call__(self, body, timeout):
        raise ModelError("server said no")


class EvalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        scenes, requests = build_synthetic_dataset(self.root / "ds", 4, stream_count=0)
        self.ledger = BudgetLedger(self.root / "ledger.sqlite3", initial_spend_usd=0)
        self.perception = PerceptionClient(FakeShapeSegmentation(scenes), preview_transcription(self.root / "ds", requests),
                                           self.ledger, PerceptionCache(self.root / "cache"))
        self.probe = Probe(self.root / "ds")

    @unittest.skipUnless(sandbox.available(), "the preview skill runs its script in the sandbox")
    def test_pairs_both_arms_on_unseen_samples(self):
        client = ChatClient(InventoryPreviewTransport(), self.ledger)
        report = evaluation.run_eval(self.probe, self.root / "out", client, self.perception, probe_harness())
        self.assertEqual(sorted(report["rows"]), ["synth-01", "synth-02", "synth-03", "synth-04"])  # all held-out
        held = report["by_split"]["heldout"]
        self.assertEqual(held["items"], 4)
        self.assertEqual(report["by_split"]["check"]["items"], 0)
        for row in report["rows"].values():
            self.assertIn("run_script", [c["name"] for c in row["frozen"]["tool_calls"]])
            self.assertNotIn("run_script", [c["name"] for c in row["base"]["tool_calls"]])
        gains = [r["frozen"]["score"] - r["base"]["score"] for r in report["rows"].values()]
        self.assertAlmostEqual(held["mean_gain"], sum(gains) / 4)
        self.assertEqual(held["wins"] + held["ties"] + held["losses"], 4)
        self.assertEqual(report["criterion_met"], held["mean_gain"] >= 0.10 and held["interval"][0] > 0)
        self.assertTrue(report["complete"])

    def test_repeated_errors_stop_without_a_criterion_result(self):
        report = evaluation.run_eval(self.probe, self.root / "out", ChatClient(Failing(), self.ledger, retries=0),
                                     self.perception, probe_harness())
        self.assertGreaterEqual(len(report["errors"]), evaluation.MAX_ERRORS)
        self.assertFalse(report["complete"])
        self.assertFalse(report["criterion_met"])
        self.assertIn("no criterion result", report["decision"])

    def test_paired_interval(self):
        self.assertEqual(evaluation.paired_interval([0.5] * 10), (0.5, 0.5))
        low, high = evaluation.paired_interval([0.0, 1.0] * 12)
        self.assertLess(low, 0.5)
        self.assertGreater(high, 0.5)
        self.assertEqual(evaluation.paired_interval([0.0, 1.0] * 12), (low, high))  # seeded
        self.assertEqual(evaluation.paired_interval([]), (None, None))

    def test_frozen_harness_is_the_reviewed_one(self):
        files = evaluation.read_harness()
        self.assertEqual(evaluation.harness_hash(files), evaluation.APPROVED_HARNESS)
        self.assertIn("skills/inventory/SKILL.md", files)
        self.assertIn("inventory", files["guardrails.md"])

    def test_main_refuses_wrong_approvals(self):
        for argv in (["--approve-dataset", "0" * 64, "--approve-harness", evaluation.APPROVED_HARNESS],
                     ["--approve-dataset", evaluation.APPROVED_DATASET, "--approve-harness", "0" * 64]):
            with patch.object(sys, "argv", ["inventory_eval", *argv]), self.assertRaises(SystemExit):
                evaluation.main()

    def test_guard_refuses_old_ledger_and_reused_scope(self):
        path = self.root / "pilot.sqlite3"
        ledger = BudgetLedger(path)
        with patch.object(evaluation, "LEDGER", path), self.assertRaisesRegex(ValueError, "older than"):
            evaluation.guarded_ledger()
        with patch.multiple(evaluation, LEDGER=path, CHECKPOINT_SPENT=0, CHECKPOINT_CALLS=0):
            evaluation.guarded_ledger()
            reservation = ledger.reserve("0", "tool", "segment", evaluation.SCOPE + "/segment/x")
            ledger.settle(reservation, cost_usd=0, input_tokens=0, output_tokens=0)
            with self.assertRaisesRegex(ValueError, "already dispatched"):
                evaluation.guarded_ledger()

    def test_fixed_request_constants(self):
        self.assertEqual(evaluation.RUN_CAP_USD, "2.50")
        self.assertEqual(evaluation.LIMITS, {"chat": 1300, "segment": 600, "transcribe": 40})
        self.assertEqual(evaluation.CRITERION, {"split": "heldout", "minimum_gain": 0.10, "interval": 0.95,
                                                "resamples": 10000, "seed": 20261029})


if __name__ == "__main__":
    unittest.main()
