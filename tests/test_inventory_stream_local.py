"""G1 swapped-agent E-A stream offline: doubles only, no credentials, network or provider server."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lx import inventory_stream as stream
from lx import inventory_stream_local as local
from lx import sandbox
from lx.budget import BudgetLedger
from lx.inventory import FakeShapeSegmentation
from lx.inventory_data import build_synthetic_dataset
from lx.inventory_probe import TOOLS as AGENT_TOOLS
from lx.inventory_workflow import InventoryPreviewTransport, preview_transcription
from lx.live_transport import validate_body
from lx.perception import PerceptionCache, PerceptionClient
from lx.proposer import TOOLS as FILE_TOOLS
from lx.providers import SPARK
from lx.spark import SparkClient
from lx.usage import BudgetScope

URL = "http://172.20.0.1:8888/v1"


class Checked:
    """A preview double behind the live transport's body contract."""

    def __init__(self, **contract):
        self.inner, self.contract, self.bodies = InventoryPreviewTransport(), contract, []

    def __call__(self, body, timeout):
        validate_body(body, image_types=("jpeg",), max_images=2, **self.contract)
        self.bodies.append(body)
        return self.inner(body, timeout)


class LocalStreamTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    @unittest.skipUnless(sandbox.available(), "the preview skill runs its script in the sandbox")
    def test_agent_and_improver_use_separate_providers(self):
        scenes, requests = build_synthetic_dataset(self.root / "ds", 2)
        ledger = BudgetLedger(self.root / "ledger.sqlite3", initial_spend_usd=0)
        perception = PerceptionClient(FakeShapeSegmentation(scenes), preview_transcription(self.root / "ds", requests),
                                      ledger, PerceptionCache(self.root / "cache"))
        scope = BudgetScope(ledger, "g1-test", "5")
        profile = local.agent_profile(URL)
        agent_side = Checked(model=profile.model, tool_sets=(FILE_TOOLS, AGENT_TOOLS))
        improver_side = Checked(model=SPARK.model, tool_sets=(FILE_TOOLS,))
        agent = SparkClient(agent_side, scope, max_tokens=local.MAX_OUTPUT_TOKENS, profile=profile)
        report = stream.run_ea(self.root / "ds", self.root / "out", agent, perception,
                               improver=SparkClient(improver_side, scope))
        self.assertEqual(report["run"]["done"], 2)
        # The swapped provider only ran the agent; Spark only improved and reflected.
        self.assertGreater(agent_side.inner.calls["worker"], 0)
        self.assertEqual(agent_side.inner.calls["improver"] + agent_side.inner.calls["reflection"], 0)
        self.assertEqual(improver_side.inner.calls["worker"], 0)
        self.assertGreater(improver_side.inner.calls["improver"], 0)
        self.assertTrue(all(b["model"] == "gemma-4-E4B-it-GGUF" and b["max_tokens"] == 1536 for b in agent_side.bodies))
        for row in report["per_sample"].values():
            self.assertIn("scripts/sum_counts.py", row["files_added"])  # scripts stay available (WSL sandbox)

    def test_agent_profile_is_the_e_c_entry_at_the_stated_url(self):
        profile = local.agent_profile(URL + "/")
        self.assertEqual(profile.endpoint, URL + "/chat/completions")
        self.assertEqual((profile.model, profile.token_param), (local.LOCAL.model, local.LOCAL.token_param))
        self.assertEqual(profile.input_price + profile.output_price, 0)
        for bad in ("http://example.com:8888/v1", "ftp://127.0.0.1/v1", "http://user:pw@127.0.0.1:8888/v1"):
            with self.assertRaises(ValueError):
                local.agent_profile(bad)

    def test_main_refuses_bad_inputs_before_the_ledger(self):
        with patch.object(sys, "argv", ["g", "--approve-dataset", "0" * 64, "--agent-base-url", URL]), \
                self.assertRaises(SystemExit):
            local.main()
        seen = []
        def unloaded(url):
            seen.append(url)
            return ["gemma-4-E2B-it-GGUF"]
        cases = [("http://example.com/v1", lambda url: self.fail("no GET for a refused URL")),
                 (URL, unloaded), (URL, lambda url: (_ for _ in ()).throw(OSError("down")))]
        with patch("lx.inventory_data.inventory_dataset_hash", return_value=local.APPROVED_DATASET), \
                patch.object(sandbox, "available", return_value=True), \
                patch.object(local, "guarded_ledger", side_effect=AssertionError("must stop first")):
            for url, served in cases:
                with patch.object(sys, "argv", ["g", "--approve-dataset", local.APPROVED_DATASET, "--agent-base-url", url]), \
                        patch.object(local, "served_models", lambda url, f=served: f(url)), \
                        self.assertRaises(SystemExit):
                    local.main()
        self.assertEqual(seen, [URL + "/models"])

    def test_guard_refuses_old_ledger_and_reused_scope(self):
        path = self.root / "pilot.sqlite3"
        ledger = BudgetLedger(path)
        with patch.object(local, "LEDGER", path), self.assertRaisesRegex(ValueError, "older than"):
            local.guarded_ledger()
        with patch.multiple(local, LEDGER=path, CHECKPOINT_SPENT=0, CHECKPOINT_CALLS=0):
            local.guarded_ledger()
            reservation = ledger.reserve("0", "tool", "segment", local.SCOPE + "/segment/x")
            ledger.settle(reservation, cost_usd=0, input_tokens=0, output_tokens=0)
            with self.assertRaisesRegex(ValueError, "already dispatched"):
                local.guarded_ledger()

    def test_fixed_request_constants(self):
        self.assertEqual(local.SCOPE, "inventory-ea-local-v1")
        self.assertNotEqual(local.SCOPE, stream.SCOPE)
        self.assertEqual(local.RUN_CAP_USD, "1.00")
        self.assertEqual(local.LIMITS, {"agent_chat": 600, "improver_chat": 500, "segment": 300, "transcribe": 40})
        self.assertEqual((local.CHECKPOINT_SPENT, local.CHECKPOINT_CALLS), (local.Decimal("2.054275772"), 3576))
        self.assertEqual(local.APPROVED_DATASET, "db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322")


if __name__ == "__main__":
    unittest.main()
