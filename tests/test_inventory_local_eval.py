"""E-C controller offline: doubles only, no credentials, network or local server."""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lx import inventory_eval
from lx import inventory_local_eval as evaluation
from lx.budget import BudgetLedger
from lx.spark import ModelError, SparkClient


class Capture:
    def __init__(self):
        self.bodies = []

    def __call__(self, body, timeout):
        self.bodies.append(body)
        raise ModelError("stop here")


class LocalEvalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_fixed_request_constants(self):
        self.assertEqual(evaluation.LOCAL.model, "gemma-4-E4B-it-GGUF")
        self.assertEqual(evaluation.LOCAL.endpoint, "http://127.0.0.1:8888/v1/chat/completions")
        self.assertEqual(evaluation.MAX_OUTPUT_TOKENS, 1536)
        self.assertEqual(evaluation.RUN_CAP_USD, "1.50")
        self.assertEqual(evaluation.LIMITS, {"chat": 1300, "segment": 600, "transcribe": 40})
        self.assertEqual(evaluation.CRITERION, inventory_eval.CRITERION)  # same as E-B
        self.assertEqual(evaluation.SCOPE, "inventory-eval-local-v2")  # the stopped first run used v1
        self.assertEqual(evaluation.CHECKPOINT_CALLS, 2725)

    def test_client_sends_the_lower_output_cap_to_the_local_model(self):
        transport = Capture()
        client = SparkClient(transport, BudgetLedger(self.root / "l.sqlite3", initial_spend_usd=0),
                             max_tokens=evaluation.MAX_OUTPUT_TOKENS, retries=0, profile=evaluation.LOCAL)
        with self.assertRaises(ModelError):
            client.chat([{"role": "user", "content": "hi"}], role="worker", phase="execute", candidate="x")
        self.assertEqual(transport.bodies[0]["model"], "gemma-4-E4B-it-GGUF")
        self.assertEqual(transport.bodies[0]["max_tokens"], 1536)

    def test_served_models_reads_the_listing(self):
        def opener(url, timeout):
            self.assertEqual(url, evaluation.MODELS_URL)
            return io.BytesIO(json.dumps({"data": [{"id": "gemma-4-E4B-it-GGUF", "loaded": True}, {"id": "other"},
                                                   {"id": "gemma-4-E2B-it-GGUF", "loaded": False}]}).encode())
        self.assertEqual(evaluation.served_models(opener), ["gemma-4-E4B-it-GGUF", "other"])  # unloaded left out

    def test_main_refuses_wrong_approvals_and_wrong_or_absent_model(self):
        good = ["--approve-dataset", evaluation.APPROVED_DATASET, "--approve-harness", evaluation.APPROVED_HARNESS]
        for argv in (["--approve-dataset", "0" * 64, "--approve-harness", evaluation.APPROVED_HARNESS],
                     ["--approve-dataset", evaluation.APPROVED_DATASET, "--approve-harness", "0" * 64]):
            with patch.object(sys, "argv", ["e", *argv]), self.assertRaises(SystemExit):
                evaluation.main()
        with patch.object(sys, "argv", ["e", *good]), patch("lx.inventory_data.inventory_dataset_hash",
                                                           return_value=evaluation.APPROVED_DATASET):
            for served in (lambda: ["gemma-4-E2B-it-GGUF"], lambda: (_ for _ in ()).throw(OSError("down"))):
                with patch.object(evaluation, "served_models", served), \
                        patch.object(evaluation, "checked_probe", side_effect=AssertionError("must stop first")), \
                        self.assertRaises(SystemExit):
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

    def test_decision_and_spark_comparison(self):
        self.assertIn("no criterion result", evaluation.decision({"complete": False, "criterion_met": False}))
        self.assertIn("local agent", evaluation.decision({"complete": True, "criterion_met": True}))
        self.assertIn("not met", evaluation.decision({"complete": True, "criterion_met": False}))
        spark = evaluation.spark_comparison()
        self.assertEqual(spark["by_split"]["heldout"]["items"], 24)
        self.assertEqual(set(spark["by_type"]), {"count", "colour_count", "inventory"})


if __name__ == "__main__":
    unittest.main()
