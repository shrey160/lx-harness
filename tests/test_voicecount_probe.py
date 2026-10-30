"""S5 probe logic offline: fake transports only, no credentials or network."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lx import voicecount_probe as probe
from lx.budget import BudgetLedger
from lx.perception import PerceptionCache, PerceptionClient
from lx.spark import ChatClient
from lx.voicecount import build_voicecount_dataset, finalize_voicecount, load_voicecount_dataset
from lx.voicecount_workflow import VoiceCountPreviewTransport, preview_perception

from test_voicecount import fsc_fixture, silence


class ProbeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        fsc, images = fsc_fixture(self.root/"fsc")
        built = build_voicecount_dataset(self.root/"data", fsc, images, 3, 1, 2, 1)
        for index, sample in enumerate(built.worker_inputs):
            (built.root/"worker_inputs"/sample["audio"]).write_bytes(silence(1.0 + index / 100))
        finalize_voicecount(built.root)
        self.dataset = load_voicecount_dataset(built.root)
        self.ledger = BudgetLedger(self.root/"ledger.sqlite3", initial_spend_usd=0)

    def test_meta_probe_reports_shapes_transcripts_boxes_and_agent(self):
        segmenter, transcriber = preview_perception(self.dataset)
        recorders = {"segment": probe.Recording(segmenter), "transcribe": probe.Recording(transcriber)}
        perception = PerceptionClient(recorders["segment"], recorders["transcribe"], self.ledger,
                                      PerceptionCache(self.root/"cache"))
        client = ChatClient(VoiceCountPreviewTransport(), self.ledger)
        items = ("loop-0001", "loop-0002")
        truth = self.dataset.controller_truth
        with patch.object(probe, "ITEMS", items), patch.object(probe, "NEGATIVE", ("loop-0001", "giraffes")):
            report = probe.run_meta(self.dataset, perception, client, recorders)
        for item in items:
            self.assertEqual(report["transcribe"][item]["wer"], 0.0)
            seen = report["segment"][f"{item}:{truth[item]['object']}"]
            self.assertGreater(seen["instances"], 0)
            self.assertTrue(seen["box_sizes_match_image"])
            self.assertIn(report["agent"][item]["score"], (0.0, 1.0))
            self.assertEqual(report["agent"][item]["tool_calls"][0]["name"], "transcribe")
        self.assertEqual(report["segment"]["loop-0001:giraffes"]["truth_count"], 0)
        self.assertEqual(report["raw_shapes"]["segment"][0]["keys"], ["output_text"])
        self.assertIn("audioDurationMs", report["raw_shapes"]["transcribe"][0]["keys"])
        # The agent's repeated perception calls were served from the cache.
        self.assertGreater(perception.cache.hits, 0)

    def test_word_error_rate(self):
        self.assertEqual(probe.word_error_rate("How many geese are there?", "how many geese are there"), 0.0)
        self.assertAlmostEqual(probe.word_error_rate("count the cans", "count the cats"), 1/3)
        self.assertEqual(probe.word_error_rate("a b", ""), 1.0)

    def test_guards_refuse_wrong_approval_and_reused_scope(self):
        with patch.object(sys, "argv", ["probe", "--part", "meta", "--approve-dataset", "0"*64]), \
                self.assertRaises(SystemExit):
            probe.main()
        ledger_path = self.root/"pilot.sqlite3"
        ledger = BudgetLedger(ledger_path)
        with patch.object(probe, "LEDGER", ledger_path), self.assertRaisesRegex(ValueError, "older than"):
            probe.guarded_ledger("voicecount-probe-v1")
        for _ in range(probe.CHECKPOINT_CALLS):
            ledger.settle(ledger.reserve("0.001", "worker", "x", "old/x"), cost_usd="0.001", input_tokens=1, output_tokens=1)
        reservation = ledger.reserve("0", "tool", "segment", "voicecount-probe-v1/segment/x")
        ledger.settle(reservation, cost_usd=0, input_tokens=0, output_tokens=0)
        with patch.object(probe, "LEDGER", ledger_path), self.assertRaisesRegex(ValueError, "already dispatched"):
            probe.guarded_ledger("voicecount-probe-v1")
        with patch.object(probe, "LEDGER", ledger_path):
            probe.guarded_ledger("voicecount-probe-local-v1")

    def test_fixed_request_constants(self):
        self.assertEqual(probe.ITEMS, ("loop-0005", "loop-0036"))
        self.assertEqual(probe.LOCAL.endpoint, "http://127.0.0.1:8888/v1/chat/completions")
        self.assertEqual(probe.PARTS["meta"]["cap_usd"], "0.50")
        self.assertTrue(all(item.startswith("loop-") for item in probe.ITEMS + (probe.LOCAL_ITEM,)))


if __name__ == "__main__":
    unittest.main()
