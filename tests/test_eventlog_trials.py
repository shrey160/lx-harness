"""Offline tests for event-log wiring into the shared trial runner."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from lx.budget import BudgetLedger
from lx.eventlog import build_eventlog_dataset
from lx.eventlog_workflow import EventLogAdapter, EventLogDomain, EventLogPreviewTransport, WORKER_MARKER
from lx.spark import SparkClient
from lx.trials import experiment_hash, final_test, run_trials
from lx.usage import BudgetScope


CORE_AVAILABLE = os.name in ("posix", "nt")  # the core runs natively on Windows too (D-016)


class _RecordingClient:
    def __init__(self, answer="{}"):
        self.answer = answer
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return {"content": self.answer}, {"cost_usd": 0, "input_tokens": 1, "output_tokens": 1}


def _ids(dataset, split):
    return {item["sample_id"] for item in dataset.worker_inputs if item["split"] == split}


class EventLogDomainTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp_dir.name)
        self.dataset = build_eventlog_dataset(self.root / "dataset", 3, 2, 3, 2, difficulty=2, seed=12)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_reader_maps_loop_to_dev_and_verify_to_heldout(self):
        _, samples = EventLogDomain.samples_from(self.dataset.root / "worker_inputs")
        mapped = {sample["sample_id"]: sample["split"] for sample in samples}
        for split, adapter_split in (("loop", "dev"), ("verify", "heldout"), ("test", "test"),
                                     ("calibration", "calibration")):
            self.assertTrue(all(mapped[sample_id] == adapter_split for sample_id in _ids(self.dataset, split)))

    def test_reader_rejects_unknown_splits_and_extra_fields(self):
        manifest = self.dataset.root / "worker_inputs" / "manifest.json"
        original = json.loads(manifest.read_text(encoding="utf-8"))
        for change in ({"split": "dev"}, {"truth": "x"}):
            with self.subTest(change=change):
                broken = {"samples": [dict(original["samples"][0], **change)]}
                manifest.write_text(json.dumps(broken), encoding="utf-8")
                with self.assertRaises(ValueError):
                    EventLogDomain.samples_from(manifest.parent)

    def test_worker_message_carries_marker_contract_and_source_only(self):
        sample = self.dataset.worker_inputs[0]
        developer, user = EventLogDomain().messages(sample, "PROMPT")
        self.assertTrue(developer["content"].startswith(WORKER_MARKER))
        self.assertIn("refunded_minor", developer["content"])
        self.assertEqual("Source:\n" + sample["text"], user["content"])

    def test_experiment_hash_binds_difficulty(self):
        harder = build_eventlog_dataset(self.root / "harder", 3, 2, 3, 2, difficulty=3, seed=12)
        self.assertNotEqual(experiment_hash(self.dataset, "eventlog"), experiment_hash(harder, "eventlog"))
        self.assertEqual(experiment_hash(self.dataset, "eventlog"), experiment_hash(self.dataset, "eventlog"))

    def test_unknown_task_is_rejected(self):
        with self.assertRaises(ValueError):
            run_trials(self.dataset, self.root / "x", lambda identifier: None, trials=1, task="images")


@unittest.skipUnless(CORE_AVAILABLE, "requires the POSIX WSL environment and vendored core")
class EventLogCoreIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.dataset = build_eventlog_dataset(self.root / "dataset", 3, 2, 3, 1, difficulty=3, seed=44)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_adapter_stages_loop_items_only(self):
        adapter = EventLogAdapter(self.dataset.root / "worker_inputs", self.root / "stage", _RecordingClient(),
                                  self.dataset.controller_truth)
        staged = {path.name for path in (self.root / "stage" / "sources").iterdir()}
        self.assertEqual(_ids(self.dataset, "loop"), staged)
        self.assertEqual(_ids(self.dataset, "verify"), set(adapter.tasks("heldout")))
        self.assertEqual(_ids(self.dataset, "test"), set(adapter.tasks("test")))

    def test_two_trial_preview_judges_criterion_on_final_test(self):
        ledger = BudgetLedger(self.root / "usage.sqlite3", cap_usd=20, initial_spend_usd=0)
        transports = {}

        def factory(identifier):
            transports[identifier] = EventLogPreviewTransport()
            return SparkClient(transports[identifier], BudgetScope(ledger, identifier, 1))

        output = self.root / "experiment"
        result = run_trials(self.dataset, output, factory, trials=2, adaptation_count=2, task="eventlog")

        self.assertEqual("order_event_log_reconciliation", result["spec"]["task"])
        self.assertEqual("final test split, retained package vs base", result["spec"]["criterion_basis"])
        test = result["final_test"]
        self.assertEqual(_ids(self.dataset, "test"), set(test["base"]))
        # Both trials retained base, so the packages reuse base scores: one test pass only.
        self.assertEqual(3, test["executions"])
        self.assertEqual({0.0}, set(test["gains"].values()))
        self.assertFalse(result["criterion"]["passed"])
        for trial in result["trials"]:
            self.assertEqual("base", trial["retained"])
            self.assertEqual(_ids(self.dataset, "verify"), set(trial["scores"]["base_heldout"]))
            self.assertEqual(_ids(self.dataset, "loop"), set(trial["scores"]["base_dev"]))
            calls = transports[trial["trial_id"]].calls
            # Per adaptation item the double sees 4 rollouts, 3 improver sessions, 1 reflection.
            self.assertEqual({"worker": 2 * (3 + 2) + 2 * 4, "improver": 2 * 3, "reflection": 2}, calls)
        self.assertEqual({"worker": 3, "improver": 0, "reflection": 0},
                         transports[next(k for k in transports if k.endswith("final-test"))].calls)

    def test_final_test_scores_a_changed_package_separately(self):
        from harness2.core import tree

        client = _RecordingClient()
        base_adapter = EventLogAdapter(self.dataset.root / "worker_inputs", self.root / "pkg", client,
                                       self.dataset.controller_truth)
        surface = base_adapter.substrate.surface()
        changed = tree.read_tree(surface, base_adapter.base_harness())
        changed["systemprompt.md"] += "\nSort events by timestamp first."
        package = self.root / "changed-package"
        tree.write_tree(surface, changed, package, stubs=False)

        result = final_test(self.dataset, self.root / "final", client, {"trial": package})

        self.assertEqual(6, result["executions"])
        self.assertTrue(any("Sort events by timestamp first." in messages[0]["content"]
                            for messages, _ in client.calls))
        self.assertEqual(0.0, result["gains"]["trial"])


if __name__ == "__main__":
    unittest.main()
