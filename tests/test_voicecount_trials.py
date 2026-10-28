"""S7 trial controller path offline: preview doubles only, no credentials or network."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lx import voicecount_trials as s7
from lx.budget import BudgetLedger
from lx.trials import run_trials
from lx.usage import usage_rows
from lx.voicecount import build_voicecount_dataset, finalize_voicecount, load_voicecount_dataset
from lx.voicecount_workflow import VoiceCountPreviewTransport, preview_perception

from test_voicecount import fsc_fixture, silence


class TrialControllerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        fsc, images = fsc_fixture(self.root/"fsc")
        built = build_voicecount_dataset(self.root/"data", fsc, images, 3, 1, 2, 0)
        for index, sample in enumerate(built.worker_inputs):
            (built.root/"worker_inputs"/sample["audio"]).write_bytes(silence(1.0 + index / 100))
        finalize_voicecount(built.root)
        self.dataset = load_voicecount_dataset(built.root)

    def test_controller_path_runs_five_trials_and_final_test_in_scoped_budgets(self):
        ledger = BudgetLedger(self.root/"ledger.sqlite3", initial_spend_usd=0)
        segmenter, transcriber = preview_perception(self.dataset)
        result = s7.run_live(self.dataset, self.root/"run", lambda kind: (VoiceCountPreviewTransport(), segmenter, transcriber),
                             ledger, live=False, cache_root=self.root/"cache")
        self.assertEqual(len(result["trials"]), 5)
        self.assertEqual(result["spec"]["adaptation_count"], 3)
        self.assertEqual(result["spec"]["experiment_cap_usd"], 5.5)
        self.assertEqual(set(result["final_test"]["gains"]), {t["trial_id"] for t in result["trials"]})
        rows = usage_rows(ledger)
        self.assertTrue(rows and all(r["candidate"].startswith(s7.SCOPE+"/") for r in rows))
        self.assertTrue(any(r["role"] == "tool" for r in rows))
        self.assertFalse(list((self.root/"run").glob("*")) == [])

    def test_live_is_refused_outside_the_reviewed_controller(self):
        for kwargs in ({"task": "eventlog"}, {"task": "voicecount"}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, "Live trial activation"):
                run_trials(self.dataset, self.root/"x", lambda i: None, live=True, **kwargs)

    def test_fixed_request(self):
        self.assertEqual((s7.TRIALS, s7.ADAPTATION_COUNT), (5, 3))
        self.assertEqual(s7.EXPERIMENT_CAP_USD, "5.50")
        self.assertAlmostEqual(5*float(s7.LIMITS["trial"]["cap_usd"]) + float(s7.LIMITS["final"]["cap_usd"]), 5.5)
        with patch.object(sys, "argv", ["s7", "--approve-dataset", "0"*64]), self.assertRaises(SystemExit):
            s7.main()


if __name__ == "__main__":
    unittest.main()
