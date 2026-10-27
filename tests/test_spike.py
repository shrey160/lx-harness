"""Run the actual engine and controller-backed fixture in the supported WSL runtime."""
import os
import tempfile
import unittest
from pathlib import Path

from lx.budget import BudgetLedger
from lx.spark import SparkClient
from lx.proposer import SparkProposer
from lx.spike import FixtureTransport, run_spike


@unittest.skipUnless(os.name == "posix", "requires the supported WSL/Linux controller")
class SpikeIntegration(unittest.TestCase):
    def test_real_engine_harvest_select_and_resume_without_new_usage(self):
        core = Path(__file__).resolve().parents[2]/"harness2_portable_core"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = BudgetLedger(root/"budget.sqlite3")
            provider = FixtureTransport()
            first = run_spike(core, root, SparkClient(provider, ledger))
            self.assertTrue(first["independent_check"])
            self.assertTrue(first["candidate_edited_and_harvested"])
            self.assertGreater(provider.calls, 0)
            calls, before = provider.calls, ledger.summary()
            second = run_spike(core, root, SparkClient(provider, ledger))
            self.assertEqual(provider.calls, calls)
            self.assertEqual(ledger.summary(), before)
            self.assertEqual(second["execution_count"], 0)
            self.assertEqual(second["selected_run_id"], first["selected_run_id"])

    def test_individual_proposer_cache_preserves_harvest_without_transport(self):
        from lx.spike import fixture_module
        core = Path(__file__).resolve().parents[2]/"harness2_portable_core"
        fixture = fixture_module(core)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = root/"input"
            (context/"harness"/"scripts").mkdir(parents=True)
            (context/"harness"/"scripts"/"normalize.py").write_text(fixture.BASE_SCRIPT)
            ledger = BudgetLedger(root/"budget.sqlite3")
            transport = FixtureTransport()
            proposer = SparkProposer(SparkClient(transport, ledger), root/"audit")
            first = proposer.call("Repair signed totals", context, "repair", harvest="context/harness")
            calls, before = transport.calls, ledger.summary()
            again = SparkProposer(SparkClient(transport, ledger), root/"audit")
            second = again.call("Repair signed totals", context, "repair", harvest="context/harness")
            self.assertTrue(second.cached)
            self.assertEqual(first.workspace, second.workspace)
            self.assertEqual(transport.calls, calls)
            self.assertEqual(ledger.summary(), before)
