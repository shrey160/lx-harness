"""T8 E-A stream logic offline: doubles only, no credentials or network."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lx import inventory_stream as stream
from lx import sandbox
from lx.budget import BudgetLedger
from lx.inventory import FakeShapeSegmentation
from lx.inventory_data import build_synthetic_dataset
from lx.inventory_workflow import InventoryPreviewTransport, preview_transcription
from lx.perception import PerceptionCache, PerceptionClient
from lx.spark import SparkClient
from lx.usage import BudgetScope


class StreamTests(unittest.TestCase):
    @unittest.skipUnless(sandbox.available(), "the preview skill runs its script in the sandbox")
    def test_runs_the_loop_over_the_stream_and_grades_both_arms(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenes, requests = build_synthetic_dataset(root / "ds", 2)
            ledger = BudgetLedger(root / "ledger.sqlite3", initial_spend_usd=0)
            transport = InventoryPreviewTransport()
            client = SparkClient(transport, BudgetScope(ledger, "t8-test", "5"))
            perception = PerceptionClient(FakeShapeSegmentation(scenes), preview_transcription(root / "ds", requests),
                                          ledger, PerceptionCache(root / "cache"))
            report = stream.run_ea(root / "ds", root / "out", client, perception)
            order, truth = stream.stream_samples(root / "ds")
            self.assertEqual(report["stream"], order)  # interleaved by request type: count first
            self.assertEqual(sorted(order), ["synth-01", "synth-02"])
            self.assertEqual(truth[order[0]]["request_type"], "count")
            self.assertEqual(report["run"]["done"], 2)
            self.assertEqual(sorted(report["gains"]), ["synth-01", "synth-02"])
            for task, row in report["per_sample"].items():
                self.assertAlmostEqual(report["gains"][task], row["harness2"]["score"] - row["base"]["score"])
                self.assertIn("scripts/sum_counts.py", row["files_added"])
            self.assertEqual(report["improved"], sorted(t for t, g in report["gains"].items() if g > 0))
            self.assertFalse(report["live"])
            self.assertGreater(transport.calls["improver"], 0)
            saved = json.loads((root / "out" / "ea_report.json").read_text())
            self.assertEqual(saved["gains"], report["gains"])

    def test_scripts_off_controller_runs_the_loop_with_skills_only(self):
        # Native Windows without Docker (D-016): no run_script tool, scripts refused, skills still learned.
        with tempfile.TemporaryDirectory() as tmp, patch.object(sandbox, "available", return_value=False):
            root = Path(tmp)
            scenes, requests = build_synthetic_dataset(root / "ds", 2)
            ledger = BudgetLedger(root / "ledger.sqlite3", initial_spend_usd=0)
            transport = InventoryPreviewTransport(scripts=False)
            client = SparkClient(transport, BudgetScope(ledger, "t8-test", "5"))
            perception = PerceptionClient(FakeShapeSegmentation(scenes), preview_transcription(root / "ds", requests),
                                          ledger, PerceptionCache(root / "cache"))
            report = stream.run_ea(root / "ds", root / "out", client, perception)
            self.assertEqual(report["run"]["done"], 2)
            for row in report["per_sample"].values():
                self.assertIn("skills/inventory-check/SKILL.md", row["files_added"])
                self.assertFalse(any(f.startswith("scripts/") for f in row["files_added"]))
            self.assertTrue(report["improved"])
            from lx.inventory_workflow import InventoryAdapter, SCRIPTS_OFF
            adapter = InventoryAdapter(root / "ds" / "worker_inputs", root / "check", client, {},
                                       perception=perception, catalog=stream.CATALOG)
            self.assertNotIn("run_script", [t["function"]["name"] for t in adapter.tools])
            with self.assertRaisesRegex(ValueError, SCRIPTS_OFF[:30]):
                adapter.validate_tree({"systemprompt.md": "x", "scripts/sum.py": "print(1)"})

    def test_main_refuses_a_wrong_approval(self):
        with patch.object(sys, "argv", ["inventory_stream", "--approve-dataset", "0" * 64]):
            with self.assertRaises(SystemExit):
                stream.main()

    def test_guard_refuses_old_ledger_low_budget_and_reused_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pilot.sqlite3"
            ledger = BudgetLedger(path)
            with patch.object(stream, "LEDGER", path), self.assertRaisesRegex(ValueError, "older than"):
                stream.guarded_ledger()
            with patch.multiple(stream, LEDGER=path, CHECKPOINT_SPENT=0, CHECKPOINT_CALLS=0, RUN_CAP_USD="25"), \
                    self.assertRaisesRegex(ValueError, "Not enough"):
                stream.guarded_ledger()
            with patch.multiple(stream, LEDGER=path, CHECKPOINT_SPENT=0, CHECKPOINT_CALLS=0):
                stream.guarded_ledger()
                reservation = ledger.reserve("0", "tool", "segment", stream.SCOPE + "/segment/x")
                ledger.settle(reservation, cost_usd=0, input_tokens=0, output_tokens=0)
                with self.assertRaisesRegex(ValueError, "already dispatched"):
                    stream.guarded_ledger()

    def test_fixed_request_constants(self):
        self.assertEqual(stream.RUN_CAP_USD, "1.50")
        self.assertEqual(stream.LIMITS, {"chat": 900, "segment": 300, "transcribe": 40})
        self.assertEqual(stream.APPROVED_DATASET, "db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322")
        self.assertEqual(stream.SCOPE, "inventory-ea-v1")


if __name__ == "__main__":
    unittest.main()
