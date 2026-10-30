"""S6 calibration logic offline: preview doubles only, no credentials or network."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lx import voicecount_calibration as calibration
from lx.budget import BudgetLedger
from lx.perception import PerceptionCache, PerceptionClient
from lx.spark import ModelError, ChatClient
from lx.spike import wire_reply
from lx.voicecount import CONTRACT, build_voicecount_dataset, finalize_voicecount, load_voicecount_dataset
from lx.voicecount_workflow import VoiceCountPreviewTransport, preview_perception

from test_voicecount import fsc_fixture, silence


class AloneThenAgent:
    """A0 replies with a fixed guess; A1 requests go to the voicecount preview double."""

    def __init__(self, fail_alone=False):
        self.agent, self.fail_alone, self.alone_bodies = VoiceCountPreviewTransport(), fail_alone, []

    def __call__(self, body, timeout):
        if body["messages"][0]["content"].startswith(calibration.ALONE_MARKER):
            self.alone_bodies.append(body)
            if self.fail_alone:
                raise ModelError("offline failure")
            return wire_reply(json.dumps({"object": "things", "region": "whole", "count": 20}))
        return self.agent(body, timeout)


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        fsc, images = fsc_fixture(self.root/"fsc")
        built = build_voicecount_dataset(self.root/"data", fsc, images, 1, 1, 1, 3)
        for index, sample in enumerate(built.worker_inputs):
            (built.root/"worker_inputs"/sample["audio"]).write_bytes(silence(1.0 + index / 100))
        finalize_voicecount(built.root)
        self.dataset = load_voicecount_dataset(built.root)
        self.ledger = BudgetLedger(self.root/"ledger.sqlite3", initial_spend_usd=0)
        segmenter, transcriber = preview_perception(self.dataset)
        self.perception = PerceptionClient(segmenter, transcriber, self.ledger, PerceptionCache(self.root/"cache"))

    def test_both_arms_score_calibration_items_only(self):
        transport = AloneThenAgent()
        report = calibration.run_calibration(self.dataset, self.root/"out", ChatClient(transport, self.ledger),
                                             self.perception)
        self.assertEqual(len(report["items"]), 3)
        self.assertTrue(all(item.startswith("calibration-") for item in report["items"]))
        for arm in ("A0", "A1"):
            self.assertEqual(report["summary"][arm]["items"], 3)
        first = report["arms"]["A1"][report["items"][0]]
        self.assertEqual(first["tools"][:2], ["transcribe", "segment"])
        # A0 sees the request as text and the image, but never any tools.
        alone = transport.alone_bodies[0]
        self.assertNotIn("tools", alone)
        self.assertIn("request", alone["messages"][1]["content"][0]["text"])
        self.assertEqual(alone["messages"][1]["content"][1]["type"], "image_url")
        self.assertIn("decision", report)
        self.assertTrue((self.root/"out"/"calibration.json").is_file())

    def test_repeated_errors_stop_the_run(self):
        with self.assertRaisesRegex(ModelError, "stopped after 3"):
            calibration.run_calibration(self.dataset, self.root/"out", ChatClient(AloneThenAgent(fail_alone=True), self.ledger),
                                        self.perception)

    def test_fixed_request(self):
        self.assertNotEqual(calibration.ALONE_CONTRACT, CONTRACT)
        self.assertIn("given as text", calibration.ALONE_CONTRACT)
        self.assertEqual(calibration.RUN_CAP_USD, "1.00")
        self.assertEqual(calibration.HEADROOM, (0.30, 0.80))
        with patch.object(sys, "argv", ["calibration", "--approve-dataset", "0"*64]), self.assertRaises(SystemExit):
            calibration.main()


if __name__ == "__main__":
    unittest.main()
