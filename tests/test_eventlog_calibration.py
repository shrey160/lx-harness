"""Offline guards and scoring for the fixed live event-log difficulty ladder."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lx.eventlog_calibration import (
    APPROVED_DATASET,
    APPROVED_EXPERIMENT,
    CALL_LIMIT,
    LEVELS,
    PER_LEVEL,
    build_ladder,
    ladder_hashes,
    main,
    run_ladder,
)
from lx.spark import ModelError


CORE_AVAILABLE = os.name == "posix"


class _ScriptedClient:
    """Answers with controller truth for chosen levels, else an empty object."""

    def __init__(self, ladder, correct_levels=(), fail_tasks=()):
        # Sample IDs repeat across levels, so key cases by (level, sample ID) via their text.
        self.case = {item["text"]: (level, item["sample_id"])
                     for level, dataset in ladder.items() for item in dataset.worker_inputs}
        self.truth = {(level, task): answer for level, dataset in ladder.items()
                      for task, answer in dataset.controller_truth.items()}
        self.correct_levels, self.fail_tasks = correct_levels, fail_tasks
        self.calls = []

    def chat(self, messages, **kwargs):
        import json
        case = self.case[messages[1]["content"].removeprefix("Source:\n")]
        self.calls.append((case, kwargs))
        if case in self.fail_tasks:
            raise ModelError("provider returned an incomplete response")
        answer = self.truth[case] if case[0] in self.correct_levels else {}
        return {"content": json.dumps(answer)}, {"cost_usd": 0, "input_tokens": 1, "output_tokens": 2}


class LadderHashTests(unittest.TestCase):
    def test_pinned_hashes_match_regenerated_ladder(self):
        with tempfile.TemporaryDirectory() as directory:
            ladder = build_ladder(directory)
            self.assertEqual((APPROVED_DATASET, APPROVED_EXPERIMENT), ladder_hashes(ladder))
            self.assertEqual(CALL_LIMIT, sum(len(d.worker_inputs) for d in ladder.values()))
            self.assertTrue(all(i["split"] == "calibration" for d in ladder.values() for i in d.worker_inputs))

    def test_truth_change_alters_experiment_but_not_dataset_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            ladder = build_ladder(directory)
            task = ladder[2].worker_inputs[0]["sample_id"]
            ladder[2].controller_truth[task]["total_minor"] += 1
            dataset_sha, experiment_sha = ladder_hashes(ladder)
            self.assertEqual(APPROVED_DATASET, dataset_sha)
            self.assertNotEqual(APPROVED_EXPERIMENT, experiment_sha)


class CalibrationGuardTests(unittest.TestCase):
    def arguments(self, approval=APPROVED_DATASET):
        return ["eventlog-calibration", "--approve-dataset", approval]

    def test_wrong_approval_fails_before_application_or_transport(self):
        with patch.object(sys, "argv", self.arguments("wrong")), \
             patch("lx.app.Application") as application, \
             patch("lx.app.LiveMetaTransport") as transport:
            with self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(2, raised.exception.code)
        application.assert_not_called()
        transport.assert_not_called()

    def test_missing_ledger_fails_before_application_or_transport(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(sys, "argv", self.arguments()), \
             patch("lx.app.ROOT", Path(directory)), \
             patch("lx.app.Application") as application, \
             patch("lx.app.LiveMetaTransport") as transport:
            with self.assertRaisesRegex(ValueError, "existing shared pilot ledger"):
                main()
        application.assert_not_called()
        transport.assert_not_called()

    def guarded_app(self, root, state):
        ledger_path = root / ".lx" / "live-spike" / "pilot-budget.sqlite3"
        ledger_path.parent.mkdir(parents=True)
        ledger_path.write_bytes(b"temporary test marker")
        return SimpleNamespace(live_ledger=SimpleNamespace(summary=lambda: state), root=root,
                               make_live_transport=Mock())

    def test_ledger_older_than_records_checkpoint_fails_before_transport(self):
        for state in ({"spent_usd": 0.041283497, "settled_calls": 194},
                      {"spent_usd": 0.041283498, "settled_calls": 193}):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                app = self.guarded_app(Path(directory), state)
                with patch.object(sys, "argv", self.arguments()), \
                     patch("lx.app.ROOT", Path(directory)), \
                     patch("lx.app.Application", return_value=app), \
                     patch("lx.app.LiveMetaTransport") as transport:
                    with self.assertRaisesRegex(ValueError, "older than the recorded checkpoint"):
                        main()
                app.make_live_transport.assert_not_called()
                transport.assert_not_called()

    def test_prior_dispatch_or_marker_blocks_a_repeat(self):
        state = {"spent_usd": 0.041283498, "settled_calls": 194}
        with tempfile.TemporaryDirectory() as directory:
            app = self.guarded_app(Path(directory), state)
            with patch.object(sys, "argv", self.arguments()), \
                 patch("lx.app.ROOT", Path(directory)), \
                 patch("lx.app.Application", return_value=app), \
                 patch("lx.eventlog_calibration.usage_rows", return_value=[{"status": "settled"}]):
                with self.assertRaisesRegex(ValueError, "already dispatched"):
                    main()
            marker = Path(directory) / "eventlog-calibration-v1" / "started.json"
            marker.parent.mkdir(parents=True)
            marker.write_text("{}", encoding="utf-8")
            with patch.object(sys, "argv", self.arguments()), \
                 patch("lx.app.ROOT", Path(directory)), \
                 patch("lx.app.Application", return_value=app), \
                 patch("lx.eventlog_calibration.usage_rows", return_value=[]):
                with self.assertRaises(FileExistsError):
                    main()
            app.make_live_transport.assert_not_called()

    def test_changed_experiment_fails_before_transport(self):
        state = {"spent_usd": 0.041283498, "settled_calls": 194}
        with tempfile.TemporaryDirectory() as directory:
            app = self.guarded_app(Path(directory), state)
            ladder = build_ladder(Path(directory) / "ladder")
            task = ladder[4].worker_inputs[0]["sample_id"]
            ladder[4].controller_truth[task]["status"] = "open?"
            with patch.object(sys, "argv", self.arguments()), \
                 patch("lx.app.ROOT", Path(directory)), \
                 patch("lx.app.Application", return_value=app), \
                 patch("lx.eventlog_calibration.build_ladder", return_value=ladder), \
                 patch("lx.eventlog_calibration.usage_rows", return_value=[]), \
                 patch("lx.eventlog_calibration.run_ladder") as run:
                with self.assertRaisesRegex(ValueError, "differs from the reviewed experiment"):
                    main()
            run.assert_not_called()
            app.make_live_transport.assert_not_called()


@unittest.skipUnless(CORE_AVAILABLE, "requires the POSIX WSL environment and vendored core")
class LadderScoringTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.ladder = build_ladder(self.root / "ladder")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_base_only_ladder_scores_each_level_and_picks_lowest_in_band(self):
        # Levels 1-2 perfect, 3-4 empty: no level has mean in [0.30, 0.80].
        client = _ScriptedClient(self.ladder, correct_levels=(1, 2))
        report = run_ladder(self.ladder, self.root / "out", client)
        self.assertEqual(CALL_LIMIT, len(client.calls))
        self.assertTrue(all(kwargs["role"] == "worker" for _, kwargs in client.calls))
        self.assertEqual([1.0, 1.0, 0.0, 0.0], [report["levels"][level]["mean"] for level in LEVELS])
        self.assertEqual(PER_LEVEL, report["levels"][1]["accepted"])
        self.assertIsNone(report["chosen_level"])
        self.assertIn("revise generator", report["decision"])
        self.assertFalse(report["capability_evidence"])
        self.assertTrue((self.root / "out" / "calibration.json").is_file())

    def test_level_in_band_is_chosen(self):
        client = _ScriptedClient(self.ladder, correct_levels=(1, 2))
        # Make half of level 3 correct so its mean lands at 0.5.
        level3 = [(3, item["sample_id"]) for item in self.ladder[3].worker_inputs]
        original_chat = client.chat

        def chat(messages, **kwargs):
            message, usage = original_chat(messages, **kwargs)
            case = client.calls[-1][0]
            if case in level3[:PER_LEVEL // 2]:
                import json
                message = {"content": json.dumps(client.truth[case])}
            return message, usage

        client.chat = chat
        report = run_ladder(self.ladder, self.root / "band", client)
        self.assertAlmostEqual(0.5, report["levels"][3]["mean"])
        self.assertEqual(3, report["chosen_level"])

    def test_model_errors_score_zero_and_three_errors_stop_the_run(self):
        level4 = [(4, item["sample_id"]) for item in self.ladder[4].worker_inputs]
        report = run_ladder(self.ladder, self.root / "one-error",
                            _ScriptedClient(self.ladder, correct_levels=LEVELS, fail_tasks=level4[:1]))
        self.assertEqual(0.0, report["levels"][4]["scores"][level4[0][1]])
        self.assertEqual(1, len(report["errors"]))
        self.assertAlmostEqual(0.9, report["levels"][4]["mean"])
        client = _ScriptedClient(self.ladder, correct_levels=LEVELS, fail_tasks=level4[:3])
        with self.assertRaisesRegex(ModelError, "stopped after 3 model errors"):
            run_ladder(self.ladder, self.root / "three-errors", client)
        self.assertEqual(3 * PER_LEVEL + 3, len(client.calls))


if __name__ == "__main__":
    unittest.main()
