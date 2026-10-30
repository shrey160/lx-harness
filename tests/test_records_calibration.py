"""Offline guards for the fixed live records-calibration entry point."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lx.records import build_records_dataset
from lx.records_calibration import APPROVED_DATASET, APPROVED_EXPERIMENT, main
from lx.trials import dataset_hash, experiment_hash
from lx.usage import summarize


class RecordsCalibrationGuardTests(unittest.TestCase):
    def arguments(self, approval=APPROVED_DATASET):
        return ["records-calibration", "--approve-dataset", approval]

    def test_wrong_dataset_approval_fails_before_application_or_transport(self):
        with patch.object(sys, "argv", self.arguments("wrong-dataset")), \
             patch("lx.engine.Engine") as application, \
             patch("lx.engine.LiveChatTransport") as transport:
            with self.assertRaises(SystemExit) as raised:
                main()

        self.assertEqual(2, raised.exception.code)
        application.assert_not_called()
        transport.assert_not_called()

    def test_missing_historical_ledger_fails_before_application_or_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(sys, "argv", self.arguments()), \
                 patch("lx.engine.ROOT", root), \
                 patch("lx.engine.Engine") as application, \
                 patch("lx.engine.LiveChatTransport") as transport:
                with self.assertRaisesRegex(ValueError, "existing shared pilot ledger"):
                    main()

        application.assert_not_called()
        transport.assert_not_called()

    def test_old_spend_or_call_checkpoint_fails_before_transport_construction(self):
        checkpoints = (
            {"spent_usd": 0.036492925, "settled_calls": 154},
            {"spent_usd": 0.036492926, "settled_calls": 153},
        )
        for state in checkpoints:
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                ledger_path = root / ".lx" / "live-spike" / "pilot-budget.sqlite3"
                ledger_path.parent.mkdir(parents=True)
                ledger_path.write_bytes(b"temporary test marker")
                ledger = SimpleNamespace(summary=lambda state=state: state)
                make_live_transport = Mock()
                app = SimpleNamespace(
                    pilot_ledger=ledger, root=root, make_live_transport=make_live_transport
                )

                with patch.object(sys, "argv", self.arguments()), \
                     patch("lx.engine.ROOT", root), \
                     patch("lx.engine.Engine", return_value=app) as application, \
                     patch("lx.engine.LiveChatTransport") as transport:
                    with self.assertRaisesRegex(ValueError, "older than the recorded checkpoint"):
                        main()

                application.assert_called_once_with()
                make_live_transport.assert_not_called()
                transport.assert_not_called()

    def test_changed_experiment_hash_fails_when_worker_input_hash_still_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = build_records_dataset(root / "dataset", 20, 40, seed=20261028)
            self.assertEqual(APPROVED_DATASET, dataset_hash(dataset))
            self.assertEqual(APPROVED_EXPERIMENT, experiment_hash(dataset))
            sample_id = dataset.worker_inputs[0]["sample_id"]
            dataset.controller_truth[sample_id]["currency"] = "CAD"
            self.assertEqual(APPROVED_DATASET, dataset_hash(dataset))
            self.assertNotEqual(APPROVED_EXPERIMENT, experiment_hash(dataset))

            ledger_path = root / ".lx" / "live-spike" / "pilot-budget.sqlite3"
            ledger_path.parent.mkdir(parents=True)
            ledger_path.write_bytes(b"temporary test marker")
            state = {"spent_usd": 0.036492926, "settled_calls": 154}
            ledger = SimpleNamespace(summary=lambda: state)
            make_live_transport = Mock()
            app = SimpleNamespace(
                pilot_ledger=ledger, root=root, make_live_transport=make_live_transport
            )

            with patch.object(sys, "argv", self.arguments()), \
                 patch("lx.engine.ROOT", root), \
                 patch("lx.engine.Engine", return_value=app) as application, \
                 patch("lx.records_calibration.build_records_dataset", return_value=dataset), \
                 patch("lx.records_calibration.usage_rows", return_value=[]), \
                 patch("lx.records_calibration.calibrate") as calibrate, \
                 patch("lx.engine.LiveChatTransport") as transport:
                with self.assertRaisesRegex(
                    ValueError, "Contract, controller truth or verifier differs"
                ):
                    main()

            application.assert_called_once_with()
            make_live_transport.assert_not_called()
            calibrate.assert_not_called()
            transport.assert_not_called()


class UsageSummaryPhaseTests(unittest.TestCase):
    def test_per_phase_status_totals_reconcile_with_global_call_totals(self):
        rows = [
            {"role": "improver", "phase": "optimize", "status": "settled",
             "actual_nanos": 123_000, "input_tokens": 10, "output_tokens": 3},
            {"role": "worker", "phase": "execute", "status": "reserved",
             "actual_nanos": None, "input_tokens": None, "output_tokens": None},
            {"role": "orchestrator", "phase": "discussion", "status": "unknown",
             "actual_nanos": None, "input_tokens": None, "output_tokens": None},
            {"role": "worker", "phase": "execute", "status": "settled",
             "actual_nanos": 77_000, "input_tokens": 4, "output_tokens": 2},
        ]

        result = summarize(rows)

        self.assertEqual(2, result["settled_calls"])
        self.assertEqual(1, result["reserved_calls"])
        self.assertEqual(1, result["unknown_calls"])
        self.assertEqual(2, sum(phase["settled_calls"] for phase in result["per_phase"].values()))
        self.assertEqual(1, sum(phase["reserved_calls"] for phase in result["per_phase"].values()))
        self.assertEqual(1, sum(phase["unknown_calls"] for phase in result["per_phase"].values()))
        self.assertEqual(4, sum(
            phase["settled_calls"] + phase["reserved_calls"] + phase["unknown_calls"]
            for phase in result["per_phase"].values()
        ))
        self.assertAlmostEqual(0.0002, result["spent_usd"])
        self.assertAlmostEqual(0.0002, sum(phase["spent_usd"] for phase in result["per_phase"].values()))


if __name__ == "__main__":
    unittest.main()
