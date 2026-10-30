"""T6 probe logic offline: doubles only, no credentials or network."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lx import inventory_probe as probe
from lx import sandbox
from lx.budget import BudgetLedger
from lx.inventory import FakeShapeSegmentation
from lx.inventory_data import build_synthetic_dataset, inventory_dataset_hash
from lx.inventory_workflow import InventoryPreviewTransport, preview_transcription
from lx.perception import PerceptionCache, PerceptionClient
from lx.spark import SparkClient
from lx.spike import wire_reply


class ViewThenDescribe:
    """Agent double for the view check: calls view on photo 1, then describes it."""

    def __init__(self):
        self.bodies = []

    def __call__(self, body, timeout):
        self.bodies.append(body)
        if not any(m["role"] == "tool" for m in body["messages"]):
            return wire_reply(calls=[{"id": "v1", "type": "function",
                                      "function": {"name": "view", "arguments": json.dumps({"photo": 1})}}])
        return wire_reply("Coloured shapes on a light background.")


class ProbeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        scenes, requests = build_synthetic_dataset(self.root / "ds", 2)
        self.ledger = BudgetLedger(self.root / "ledger.sqlite3", initial_spend_usd=0)
        self.recorders = {"segment": probe.Recording(FakeShapeSegmentation(scenes)),
                          "transcribe": probe.Recording(preview_transcription(self.root / "ds", requests))}
        self.perception = PerceptionClient(self.recorders["segment"], self.recorders["transcribe"], self.ledger,
                                           PerceptionCache(self.root / "cache"))
        self.probe = probe.Probe(self.root / "ds")
        self.items = patch.multiple(probe, SAMPLE_A="synth-01", SAMPLE_B="synth-02")
        self.items.start()
        self.addCleanup(self.items.stop)

    def test_view_check_sends_the_image_after_the_tool_message(self):
        transport = ViewThenDescribe()
        result = self.probe.view_check(SparkClient(transport, self.ledger), self.perception, "synth-01")
        self.assertTrue(result["view_called"])
        self.assertEqual(result["reply"], "Coloured shapes on a light background.")
        last = transport.bodies[-1]["messages"]
        self.assertEqual([m["role"] for m in last[-3:]], ["assistant", "tool", "user"])
        self.assertEqual(last[-1]["content"][1]["type"], "image_url")

    def test_transcribe_and_segment_reports(self):
        heard = self.probe.transcribe(self.perception, "synth-01")
        self.assertEqual(heard["wer"], 0.0)
        seen = self.probe.segment(self.perception, "synth-01")
        self.assertEqual(sorted(seen), [str(i) for i in range(1, len(seen) + 1)])
        self.assertTrue(all(v["box_sizes_match_image"] for v in seen.values()))
        self.assertTrue(all("truth" in v and "confident_0_5" in v for v in seen.values()))

    @unittest.skipUnless(sandbox.available(), "sample B runs the helper script in the sandbox")
    def test_meta_run_covers_base_and_skill_harness(self):
        client = SparkClient(InventoryPreviewTransport(), self.ledger)
        report = probe.run_meta(self.probe, client, self.perception, self.recorders)
        self.assertEqual(sorted(report["agent"]), ["synth-01", "synth-02"])
        base, skilled = report["agent"]["synth-01"], report["agent"]["synth-02"]
        self.assertEqual(base["harness"], "base")
        self.assertNotIn("run_script", [c["name"] for c in base["tool_calls"]])
        script_calls = [c for c in skilled["tool_calls"] if c["name"] == "run_script"]
        self.assertEqual([c["exit_code"] for c in script_calls], [0])
        self.assertIn("load_skill", [c["name"] for c in skilled["tool_calls"]])
        self.assertEqual(report["raw_shapes"]["segment"][0]["keys"], ["output_text"])
        self.assertIn("view_called", report["view_check"])

    def test_probe_prompt_lists_the_skill_only_for_sample_b(self):
        from lx.components import compose_prompt
        self.assertIn("AVAILABLE SKILLS", compose_prompt(probe.SURFACE, probe.probe_harness()))
        self.assertNotIn("AVAILABLE SKILLS", compose_prompt(probe.SURFACE, probe.base_harness()))

    def test_main_refuses_a_wrong_approval(self):
        with patch.object(sys, "argv", ["inventory_probe", "--part", "meta", "--approve-dataset", "0" * 64]):
            with self.assertRaises(SystemExit):
                probe.main()

    def test_dataset_hash_is_stable_and_detects_changes(self):
        first = inventory_dataset_hash(self.root / "ds")
        self.assertEqual(first, inventory_dataset_hash(self.root / "ds"))
        truth = self.root / "ds" / "controller_truth" / "ground_truth.json"
        data = json.loads(truth.read_text(encoding="utf-8"))
        data["synth-01"]["photos"]["1"].append({"kind": "circle", "count": 1})
        truth.write_text(json.dumps(data), encoding="utf-8")
        self.assertNotEqual(first, inventory_dataset_hash(self.root / "ds"))

    def test_guard_refuses_old_ledger_and_reused_scope(self):
        ledger_path = self.root / "pilot.sqlite3"
        ledger = BudgetLedger(ledger_path)
        with patch.object(probe, "LEDGER", ledger_path), patch.object(probe, "CHECKPOINT_SPENT", 0),                 self.assertRaisesRegex(ValueError, "older than"):
            probe.guarded_ledger("inventory-probe-v1")
        with patch.multiple(probe, LEDGER=ledger_path, CHECKPOINT_SPENT=0, CHECKPOINT_CALLS=0):
            probe.guarded_ledger("inventory-probe-v1")
            reservation = ledger.reserve("0", "tool", "segment", "inventory-probe-v1/segment/x")
            ledger.settle(reservation, cost_usd=0, input_tokens=0, output_tokens=0)
            with self.assertRaisesRegex(ValueError, "already dispatched"):
                probe.guarded_ledger("inventory-probe-v1")
            probe.guarded_ledger("inventory-probe-local-v1")

    def test_fixed_request_constants(self):
        self.assertEqual(probe.PARTS["meta"]["cap_usd"], "0.40")
        self.assertEqual(probe.PARTS["local"]["cap_usd"], "0.15")
        self.assertEqual(probe.KEEP_IMAGES, 2)
        self.assertEqual(probe.LOCAL.model, "gemma-4-E2B-it-GGUF")
        self.assertEqual(len(probe.APPROVED_DATASET), 64)

if __name__ == "__main__":
    unittest.main()
