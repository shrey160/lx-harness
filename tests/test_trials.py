"""Focused offline tests for the records trial and calibration path."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lx.budget import BudgetLedger
from lx.records import build_records_dataset, verify_record
from lx.records_workflow import RecordsAdapter, RecordsDomain, RecordsPreviewTransport
from lx.spark import SparkClient
from lx.trials import calibrate, criterion_result, run_trials
from lx.usage import BudgetScope


CORE_AVAILABLE = os.name in ("posix", "nt")  # the core runs natively on Windows too (D-016)


class _RecordingClient:
    def __init__(self, answer="{}"):
        self.answer = answer
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return {"content": self.answer}, {"cost_usd": 0, "input_tokens": 1, "output_tokens": 1}


class TrialCriterionTests(unittest.TestCase):
    def test_five_trial_success_boundary_includes_exact_thresholds(self):
        result = criterion_result([0.10, 0.10, 0.10, 0.10, -0.05])
        self.assertEqual(4, result["wins"])
        self.assertTrue(result["passed"])

    def test_fails_with_too_few_trials_even_if_each_trial_wins(self):
        result = criterion_result([0.2, 0.2])
        self.assertEqual(2, result["wins"])
        self.assertFalse(result["passed"])

    def test_fails_when_win_count_is_below_four(self):
        result = criterion_result([0.10, 0.10, 0.10, 0.099, 0.0])
        self.assertEqual(3, result["wins"])
        self.assertFalse(result["passed"])

    def test_fails_when_any_regression_exceeds_five_points(self):
        result = criterion_result([0.10, 0.10, 0.10, 0.10, -0.05001])
        self.assertEqual(4, result["wins"])
        self.assertFalse(result["passed"])


class RecordsWorkflowUnitTests(unittest.TestCase):
    def test_live_trials_fail_before_factory_is_called(self):
        called = []
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            root = Path(temp)
            dataset = build_records_dataset(root / "dataset", 1, 1, seed=120)

            def factory(identifier):
                called.append(identifier)
                raise AssertionError("live mode must stop before client creation")

            with self.assertRaisesRegex(ValueError, "Live trial activation"):
                run_trials(dataset, root / "experiment", factory, trials=5, live=True)

        self.assertEqual([], called)

    def test_more_than_five_trials_rejected_before_factory_is_called(self):
        called = []
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            root = Path(temp)
            dataset = build_records_dataset(root / "dataset", 1, 1, seed=121)

            def factory(identifier):
                called.append(identifier)
                raise AssertionError("invalid trial count must stop before client creation")

            with self.assertRaisesRegex(ValueError, "five trials"):
                run_trials(dataset, root / "experiment", factory, trials=6)

        self.assertEqual([], called)

    def test_malformed_json_answer_receives_zero_field_credit(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            expected = {
                "order_id": "LX-123456", "customer": "Mira Solis", "date": "2025-01-02",
                "currency": "USD", "amount_minor": 1234, "phone": None, "tags": ["gift"],
            }
            adapter = object.__new__(RecordsAdapter)
            adapter.truth = {"sample": expected}
            adapter.domain = RecordsDomain()
            run = Path(temp) / "run"
            run.mkdir()
            (run / "answer.json").write_text(json.dumps("{not valid json"), encoding="utf-8")

            grade = adapter.score(run, "sample")

        self.assertEqual({"n_passed": 0, "n_criteria": 7, "all_pass": False}, grade)
        self.assertEqual(0.0, verify_record(expected, "{not valid json").score)


@unittest.skipUnless(CORE_AVAILABLE, "requires the POSIX WSL environment and vendored core")
class RecordsCoreIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.dataset = build_records_dataset(self.root / "dataset", 2, 2, seed=331)

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_client(self):
        return _RecordingClient()

    def test_two_trial_real_engine_preview_is_no_gain_and_uses_fresh_ids(self):
        ledger = BudgetLedger(self.root / "usage.sqlite3", cap_usd=20, initial_spend_usd=0)
        created = []

        def factory(identifier):
            created.append(identifier)
            return SparkClient(RecordsPreviewTransport(), BudgetScope(ledger, identifier, 1))

        output = self.root / "experiment"
        result = run_trials(
            self.dataset, output, factory, trials=2, adaptation_count=1, live=False
        )

        self.assertEqual(2, len(result["trials"]))
        trial_ids = [trial["trial_id"] for trial in result["trials"]]
        self.assertEqual(2, len(set(trial_ids)))
        self.assertEqual(trial_ids, created)
        self.assertTrue(all(not trial["improved"] for trial in result["trials"]))
        self.assertTrue(all(trial["retained"] == "base" for trial in result["trials"]))
        self.assertFalse(result["criterion"]["passed"], "two trials cannot satisfy the five-trial criterion")
        self.assertFalse(result["live_success"])

        dev_ids = {item["sample_id"] for item in self.dataset.worker_inputs if item["split"] == "dev"}
        heldout_ids = {item["sample_id"] for item in self.dataset.worker_inputs if item["split"] == "heldout"}
        for trial_id in trial_ids:
            source_ids = {path.name for path in (output / trial_id / "sources").iterdir()}
            self.assertEqual(dev_ids, source_ids)
            self.assertFalse(source_ids & heldout_ids)

    def test_calibration_scores_heldout_only_without_improver_calls(self):
        client = self.make_client()
        output = self.root / "calibration"

        result = calibrate(self.dataset, output, client)

        expected_heldout = {
            item["sample_id"] for item in self.dataset.worker_inputs if item["split"] == "heldout"
        }
        self.assertEqual(expected_heldout, set(result["scores"]))
        self.assertEqual(2, len(result["scores"]))
        self.assertEqual(2, len(client.calls))
        self.assertTrue(all(call_kwargs["role"] == "worker" for _, call_kwargs in client.calls))
        self.assertTrue(all(call_kwargs["phase"] == "execute" for _, call_kwargs in client.calls))
        self.assertTrue((output / "calibration.json").is_file())

    def test_every_editable_harness_file_invalidates_cache_and_reaches_worker_prompt(self):
        client = self.make_client()
        output = self.root / "cache-check"
        adapter = RecordsAdapter(
            self.dataset.root / "worker_inputs", output, client,
            self.dataset.controller_truth, mode="offline_preview",
        )
        from harness2.core import tree

        surface = adapter.substrate.surface()
        editable = tuple(surface.always_on)
        self.assertTrue(editable)
        harness = adapter.base_harness()
        base_content = tree.read_tree(surface, harness)
        sample_id = adapter.tasks("dev")[0]
        run_id = "same-run-id"

        adapter.execute(sample_id, None, run_id)
        self.assertEqual(1, len(client.calls))
        for index, filename in enumerate(editable):
            marker = f"cache-probe-{index}-{filename}"
            changed = dict(base_content)
            changed[filename] = f"{changed.get(filename, '')}\n{marker}\n"
            tree.write_tree(surface, changed, harness, stubs=False)
            adapter.execute(sample_id, None, run_id)
            self.assertEqual(index + 2, len(client.calls), f"{filename} did not invalidate the cache")
            prompt = client.calls[-1][0][0]["content"]
            self.assertIn(marker, prompt, f"{filename} did not reach the worker prompt")

    def test_task_contract_change_invalidates_cache_and_reaches_worker_prompt(self):
        client = self.make_client()
        output = self.root / "contract-cache-check"
        adapter = RecordsAdapter(
            self.dataset.root / "worker_inputs", output, client,
            self.dataset.controller_truth, mode="offline_preview",
        )
        sample_id = adapter.tasks("dev")[0]
        run_id = "same-contract-run"
        adapter.execute(sample_id, None, run_id)
        self.assertEqual(1, len(client.calls))

        marker = "contract-cache-probe"
        adapter.domain.task_prompt += f"\n{marker}"
        adapter.execute(sample_id, None, run_id)

        self.assertEqual(2, len(client.calls))
        prompt = client.calls[-1][0][0]["content"]
        self.assertIn(marker, prompt)

    def test_controller_truth_change_invalidates_execution_cache(self):
        client = self.make_client()
        output = self.root / "truth-cache-check"
        adapter = RecordsAdapter(
            self.dataset.root / "worker_inputs", output, client,
            self.dataset.controller_truth, mode="offline_preview",
        )
        sample_id = adapter.tasks("dev")[0]
        run_id = "same-truth-run"
        adapter.execute(sample_id, None, run_id)
        self.assertEqual(1, len(client.calls))

        adapter.truth[sample_id]["amount_minor"] += 1
        adapter.execute(sample_id, None, run_id)

        self.assertEqual(2, len(client.calls))

    def test_nonfinite_json_worker_reply_is_stored_and_scores_zero(self):
        client = _RecordingClient('{"amount_minor": NaN}')
        adapter = RecordsAdapter(
            self.dataset.root / "worker_inputs", self.root / "nonfinite-reply", client,
            self.dataset.controller_truth, mode="offline_preview",
        )
        sample_id = adapter.tasks("dev")[0]

        run = adapter.execute(sample_id, None, "nonfinite-json")
        grade = adapter.score(run, sample_id)

        self.assertEqual({"n_passed": 0, "n_criteria": 7, "all_pass": False}, grade)
        self.assertEqual("\"{\\\"amount_minor\\\": NaN}\"", adapter.deliverable_text(run))

    def test_mean_dev_retention_can_choose_partial_gain_without_any_pass(self):
        from harness2.core import tree
        from harness2.core.schedule import Namespace

        client = self.make_client()
        output = self.root / "partial-gain"
        adapter = RecordsAdapter(
            self.dataset.root / "worker_inputs", output, client,
            self.dataset.controller_truth, mode="offline_preview",
        )
        selected_label = "mock-partial-candidate"

        def fake_run_domain(domain, practice, k, *, adapter, prop, runs_root, library_root, modes, reflector_prop):
            for task in practice:
                agg = Namespace(adapter.spec.name, adapter.substrate.name, "agg", 1).task_dir(runs_root, task)
                agg.mkdir(parents=True, exist_ok=True)
                (agg / "summary.json").write_text(
                    json.dumps({"selections": {"seq": {"chosen_label": selected_label}}}), encoding="utf-8"
                )
                harness = Namespace(adapter.spec.name, adapter.substrate.name, "seq", 1).task_dir(runs_root, task)
                tree.write_tree(
                    adapter.substrate.surface(),
                    tree.read_tree(adapter.substrate.surface(), adapter.base_harness()),
                    harness / "harnesses" / selected_label,
                    stubs=False,
                )
            return {"done": len(practice)}

        source = {"kind": "base"}

        def fake_execute(task, harness_dir, run_id, **kwargs):
            source["kind"] = "candidate" if harness_dir is not None else "base"
            return Path(run_id)

        adapter.execute = fake_execute
        adapter.deliverable_text = lambda run: "{}"
        adapter.verify = lambda task, answer: SimpleNamespace(
            accepted=False,
            score=(4 / 7 if source["kind"] == "candidate" else 3 / 7),
        )
        spec = {"test": "partial-score retention"}

        with patch("lx.workflow.SparkProposer", side_effect=lambda *args, **kwargs: object()), \
             patch("harness2.core.schedule.run_domain", side_effect=fake_run_domain):
            from lx.workflow import optimize_adapter
            report = optimize_adapter(adapter, output, client, spec, adaptation_count=1)

        self.assertTrue(report["improved"])
        self.assertEqual("candidate", report["retained"])
        self.assertFalse(any(report["checks"]["base"].values()))
        self.assertFalse(any(report["checks"]["selected_dev"].values()))
        self.assertAlmostEqual(3 / 7, report["means"]["base_dev"])
        self.assertAlmostEqual(4 / 7, report["means"]["candidate_dev"])


if __name__ == "__main__":
    unittest.main()
