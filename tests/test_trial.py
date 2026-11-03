"""`trial`: one real sample on the base harness (D-025), run here on doubles of the live providers."""
import json
from pathlib import Path
import shutil
import unittest

from lx import sandbox, tasks
from lx.inventory import FakeShapeSegmentation, write_synthetic
from lx.inventory_data import _tone
from lx.inventory_workflow import InventoryPreviewTransport
from lx.orchestration import Orchestration
from lx.perception import FakeTranscription, media_hash
from lx.spike import wire_reply
from lx.taskrun import _adapter, _folders
from lx.worker import execute
from test_tasks import Base

ACCEPTED = {"data_approved": True, "contributor_accepted": True, "notice_accepted": True}


class Trials(Base):
    def setUp(self):
        super().setUp()
        self.lx = Orchestration(self.app, launch=False)
        self.path = self.folder()
        samples = Path(self.path)/"samples"
        write_synthetic(samples, 6)
        scripts = {}
        for index, folder in enumerate(sorted(samples.iterdir()), 1):
            audio = _tone(index)
            (folder/"request.wav").write_bytes(audio)
            scripts[media_hash(audio)] = (folder/"request.txt").read_text(encoding="utf-8").strip()
        self.transport = InventoryPreviewTransport()
        self.app._live_transport_factory = lambda hint=None: self.transport
        self.app._live_perception_factory = lambda: (FakeShapeSegmentation(tasks.preview_scenes(6)), FakeTranscription(scripts))
        self.lx.init(self.path, "photo-request")
        self.lx.spec_draft(self.path, mode="live")
        self.id = self.app.open_workspace(self.path)["id"]
        self.workspace = self.app.workspace(self.id)

    def approve_it(self):
        self.approve(self.app.project(self.id), notice_accepted=True)

    def finish(self, run_id):
        return execute(self.app, self.workspace, run_id, self.workspace.run_dir(run_id)/"snapshot")

    def trial(self):
        self.approve_it()
        run_id = self.lx.trial_start(self.path)["run_id"]
        return run_id, self.finish(run_id)


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class WhatItDoes(Trials):
    def test_one_sample_runs_on_the_base_harness_with_no_improver(self):
        run_id, result = self.trial()
        self.assertEqual((result["kind"], result["live"]), ("trial", True))
        self.assertEqual(self.transport.calls["worker"] > 0, True)
        self.assertEqual((self.transport.calls["improver"], self.transport.calls["reflection"]), (0, 0))
        executions = self.workspace.run_dir(run_id)/"outputs"/"runs"/"executions"
        self.assertEqual([path.name for path in executions.iterdir()], ["trial"])

    def test_it_writes_no_package(self):
        run_id, _ = self.trial()
        self.assertFalse(self.workspace.package_dir(run_id).exists())
        self.assertEqual(self.workspace.run(run_id)["status"], "completed")

    def test_it_says_when_grading_starts(self):
        phases = []
        update = self.workspace.update_run
        self.workspace.update_run = lambda identifier, **changes: (phases.append(changes.get("phase")), update(identifier, **changes))[1]
        self.trial()
        self.assertEqual([phase for phase in phases if phase], ["preflight", "trying", "grading", "complete"])

    def test_it_shows_no_loop_progress(self):
        self.trial()
        self.assertIsNone(self.lx.run_status(self.path)["runs"][0]["progress"])

    def test_it_uses_a_stream_sample(self):
        _, result = self.trial()
        manifest = json.loads((self.workspace.run_dir(self.lx.run_status(self.path)["runs"][0]["id"])/"dataset"/"worker_inputs"/"manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(next(s for s in manifest["samples"] if s["sample_id"] == result["sample"])["split"], "stream")

    def test_it_spends_from_the_workspace_within_its_cap(self):
        run_id, _ = self.trial()
        self.assertEqual(self.workspace.run(run_id)["cap_usd"], 0.25)
        usage = self.app.spend(self.id)
        self.assertGreater(usage["per_role"]["worker"]["settled_calls"], 0)
        self.assertLessEqual(usage["spent_usd"], 0.25)

    def test_its_cap_binds_the_model_and_the_tools_not_the_run_budget(self):
        self.approve_it()
        run_id = self.lx.trial_start(self.path)["run_id"]
        self.workspace.update_run(run_id, cap_usd=0.000001)
        before = self.transport.calls["worker"]
        with self.assertRaisesRegex(ValueError, "no room"):
            self.finish(run_id)
        self.assertEqual(self.transport.calls["worker"], before)

    def test_an_agent_sees_scores_and_counts_but_no_trace_or_answer(self):
        run_id, _ = self.trial()
        seen = self.lx.results(self.path, run_id)
        self.assertEqual(set(seen) - {"usage"}, {"kind", "task", "live", "sample", "scored", "score", "base_sha256", "checks", "stopped", "tool_calls", "note", "learned"})
        self.assertIsNone(seen["learned"])
        self.assertEqual({tuple(sorted(item)) for item in seen["checks"]}, {("name", "passed")})
        self.assertGreater(seen["tool_calls"]["transcribe"], 0)
        self.assertTrue(0 <= seen["score"] <= 1)
        text = json.dumps(seen)
        answer = (self.workspace.run_dir(run_id)/"outputs"/"runs"/"executions"/"trial"/"answer.json").read_text(encoding="utf-8")
        self.assertNotIn(answer, text)
        self.assertNotIn("final_text", text)

    def test_a_spec_with_no_check_gets_a_trial_with_no_score(self):
        self.lx.spec_draft(self.path, no_check=True)
        _, result = self.trial()
        self.assertEqual((result["scored"], result["score"], result["checks"]), (False, None, []))
        self.assertTrue(result["tool_calls"])

    def test_a_trial_that_stops_says_so(self):
        self.transport = lambda body, timeout: (wire_reply("PREFLIGHT_OK") if body["messages"][0]["content"].startswith("LX_PREFLIGHT") else
                                                wire_reply(calls=[{"id": "x", "type": "function", "function": {"name": "transcribe", "arguments": "{}"}}]))
        # No count is built in: the user's own limit is what stops an agent that never answers.
        self.app.edit_spec(self.id, {"goal": self.app.project(self.id)["draft"]["goal"], "budget_usd": 1, "limits": {"agent_tools_per_photo": 2}})
        _, result = self.trial()
        self.assertRegex(result["stopped"], "tool calls|turns")
        self.assertEqual(result["score"] < 1, True)

    def test_its_trajectory_downloads_for_a_person(self):
        run_id, _ = self.trial()
        self.assertTrue(self.app.trajectory_bytes(self.id, run_id))

    def test_it_counts_against_a_grant(self):
        self.app.set_grant(self.id, {"cap_usd": 1, "runs": 2, **ACCEPTED})
        started = self.lx.trial_start(self.path)
        self.assertEqual(started["status"], "started")
        self.finish(started["run_id"])
        grant = self.lx.spec_show(self.path)["grant"]
        self.assertEqual(grant["runs_used"], 1)
        self.assertGreater(grant["spent_usd"], 0)
        self.assertEqual(self.lx.run_status(self.path)["runs"][0]["approved_by"], "grant")


class Package(Trials):
    """A trial of a finished run's package, on a run record written by hand: the loop's own tests cover how a package is made."""

    MARK = "Learned in an earlier run."

    def setUp(self):
        super().setUp()
        self.approve_it()
        self.source = self.finished_run()

    def finished_run(self, **changes):
        job = self.app.start(self.id, launch=False)
        run_dir, spec = self.workspace.run_dir(job["id"]), job["spec"]
        _, truth, _ = tasks.build_dataset(run_dir/"snapshot", run_dir/"dataset", spec["task"], preview=False)
        adapter = _adapter(spec["task"], run_dir, run_dir/"outputs", None, truth, self.workspace.check_source(spec["check"]),
                           live=True, limits=self.app.limits_of(spec), tools=tasks.spec_tools(spec))
        harness = self.workspace.package_dir(job["id"])/"harness"
        shutil.copytree(adapter.base_harness(), harness)
        guidance = harness/"tool_descriptions"/"tool_guidance.md"
        guidance.write_text(guidance.read_text(encoding="utf-8")+self.MARK, encoding="utf-8")
        result = {"scores": {"base": {}, "harness2": {}}, "package_is_base": False}
        return self.workspace.update_run(job["id"], **{"status": "completed", "launch_pending": False, "result": result, **changes})["id"]

    def test_the_agent_gets_the_package_not_the_base(self):
        run_id = self.lx.trial_start(self.path, self.source)["run_id"]
        seen = []
        client = self.transport
        self.transport = lambda body, timeout: (seen.append(json.dumps(body)), client(body, timeout))[1]
        result = self.finish(run_id)
        self.assertTrue(any(self.MARK in body for body in seen))
        self.assertEqual(result["harness"]["from_run"], self.source)
        self.assertEqual((client.calls["improver"], client.calls["reflection"]), (0, 0))

    def test_a_plain_trial_stays_on_the_base(self):
        run_id = self.lx.trial_start(self.path)["run_id"]
        self.assertNotIn("from_run", self.workspace.run(run_id))
        self.assertFalse((self.workspace.run_dir(run_id)/"harness").exists())
        self.assertNotIn("harness", self.finish(run_id))

    def test_it_keeps_its_own_copy_of_the_harness(self):
        run_id = self.lx.trial_start(self.path, self.source)["run_id"]
        self.assertIn(self.MARK, (self.workspace.run_dir(run_id)/"harness"/"tool_descriptions"/"tool_guidance.md").read_text(encoding="utf-8"))
        shutil.rmtree(self.workspace.package_dir(self.source))
        self.assertEqual(self.finish(run_id)["harness"]["sample_seen"], False)

    def test_it_says_when_the_run_saw_the_sample(self):
        run_id = self.lx.trial_start(self.path, self.source)["run_id"]
        self.assertRegex(self.finish(run_id)["note"], "never saw")
        folder = self.workspace.run(run_id)["result"]["sample"]
        sample = next(key for key, value in _folders(self.workspace.run_dir(self.source)).items() if value == folder)
        self.workspace.update_run(self.source, result={"scores": {"base": {sample: 1.0}}, "package_is_base": False})
        second = self.lx.trial_start(self.path, self.source)["run_id"]
        result = self.finish(second)
        self.assertTrue(result["harness"]["sample_seen"])
        self.assertRegex(result["note"], "reads high")

    def test_a_grant_covers_it(self):
        self.app.store.delete("approval", self.id)
        self.app.set_grant(self.id, {"cap_usd": 1, **ACCEPTED})
        started = self.lx.trial_start(self.path, self.source)
        self.assertEqual(started["status"], "started")
        self.assertEqual(self.workspace.run(started["run_id"])["from_run"], self.source)

    def test_only_a_finished_live_task_run_counts(self):
        unfinished = self.app.start(self.id, launch=False)["id"]
        self.workspace.update_run(unfinished, status="stopped", launch_pending=False)
        trial = self.app.start(self.id, launch=False, trial=True)["id"]
        self.workspace.update_run(trial, status="completed", launch_pending=False, result={})
        for run_id in (unfinished, trial):
            with self.subTest(run_id), self.assertRaisesRegex(ValueError, "finished live run"):
                self.lx.trial_start(self.path, run_id)

    def test_a_preview_run_has_no_harness_to_try(self):
        job = self.workspace.run(self.source)
        self.workspace.update_run(self.source, spec={**job["spec"], "mode": "controlled_preview"})
        with self.assertRaisesRegex(ValueError, "finished live run"):
            self.lx.trial_start(self.path, self.source)

    def test_a_package_that_is_the_base_is_refused(self):
        self.workspace.update_run(self.source, result={"scores": {"base": {}}, "package_is_base": True})
        with self.assertRaisesRegex(ValueError, "plain trial"):
            self.lx.trial_start(self.path, self.source)

    def test_another_task_is_refused(self):
        job = self.workspace.run(self.source)
        self.workspace.update_run(self.source, spec={**job["spec"], "task": {**job["spec"]["task"], "name": "another"}})
        with self.assertRaisesRegex(ValueError, "another task"):
            self.lx.trial_start(self.path, self.source)

    def test_another_tool_list_is_refused(self):
        job = self.workspace.run(self.source)
        self.workspace.update_run(self.source, spec={**job["spec"], "mcp_tools": [{"server": "other", "tool": "read"}]})
        with self.assertRaisesRegex(ValueError, "tool list"):
            self.lx.trial_start(self.path, self.source)

    def test_a_run_that_does_not_exist_is_refused(self):
        for run_id in ("0123456789ab", "../../secret"):
            with self.subTest(run_id), self.assertRaisesRegex(ValueError, "Record not found|Invalid run"):
                self.lx.trial_start(self.path, run_id)
        self.assertEqual(len(self.app.runs(self.id)), 1)

    def sample_harness(self):
        run_dir = self.workspace.run_dir(self.source)
        internal, folder = next(iter(_folders(run_dir).items()))
        relative = f"saved/{internal}/harnesses/c1"
        target = run_dir/"outputs"/"runs"/relative
        shutil.copytree(self.workspace.package_dir(self.source)/"harness", target)
        guidance = target/"tool_descriptions"/"tool_guidance.md"
        guidance.write_text(guidance.read_text(encoding="utf-8")+" SAMPLE_CANDIDATE", encoding="utf-8")
        report = run_dir/"outputs"/"ea_report.json"
        report.write_text(json.dumps({"per_sample": {internal: {"harness_dir": relative}}}), encoding="utf-8")
        self.workspace.update_run(self.source, result={"samples": [internal], "package_is_base": True})
        return folder, target, report

    def test_a_sample_harness_runs_even_when_final_package_kept_base(self):
        folder, _, _ = self.sample_harness()
        started = self.lx.trial_start(self.path, self.source, from_sample=folder)
        run_id = started["run_id"]
        self.assertEqual(self.workspace.run(run_id)["from_sample"], folder)
        copied = self.workspace.run_dir(run_id)/"harness"/"tool_descriptions"/"tool_guidance.md"
        self.assertIn("SAMPLE_CANDIDATE", copied.read_text(encoding="utf-8"))
        seen, client = [], self.transport
        self.transport = lambda body, timeout: (seen.append(json.dumps(body)), client(body, timeout))[1]
        result = self.finish(run_id)
        self.assertTrue(any("SAMPLE_CANDIDATE" in body for body in seen))
        self.assertEqual(result["harness"]["from_sample"], folder)
        self.assertRegex(result["note"], "saved harness for sample")
        self.assertEqual(client.calls["improver"], 0)

    def test_sample_selection_is_also_forwarded_under_a_grant(self):
        folder, _, _ = self.sample_harness()
        self.app.store.delete("approval", self.id)
        self.app.set_grant(self.id, {"cap_usd": 1, **ACCEPTED})
        started = self.lx.trial_start(self.path, self.source, from_sample=folder)
        self.assertEqual(started["status"], "started")
        self.assertEqual(self.workspace.run(started["run_id"])["from_sample"], folder)

    def test_sample_harness_is_frozen_before_source_changes(self):
        folder, target, _ = self.sample_harness()
        run_id = self.lx.trial_start(self.path, self.source, from_sample=folder)["run_id"]
        shutil.rmtree(target)
        result = self.finish(run_id)
        self.assertIn("sha256", result["harness"])
        self.assertEqual(result["harness"]["from_sample"], folder)

    def test_unknown_sample_and_missing_harness_are_refused_before_job_creation(self):
        folder, target, _ = self.sample_harness()
        for name in ("missing", "../../secret", True):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "sample folder"):
                self.lx.trial_start(self.path, self.source, from_sample=name)
        shutil.rmtree(target)
        with self.assertRaisesRegex(ValueError, "harness is missing"):
            self.lx.trial_start(self.path, self.source, from_sample=folder)
        self.assertEqual(len(self.app.runs(self.id)), 1)

    def test_saved_report_cannot_escape_run_harnesses(self):
        folder, _, report = self.sample_harness()
        data = json.loads(report.read_text(encoding="utf-8"))
        next(iter(data["per_sample"].values()))["harness_dir"] = "../../outside"
        report.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid snapshot inventory path"):
            self.lx.trial_start(self.path, self.source, from_sample=folder)
        self.assertEqual(len(self.app.runs(self.id)), 1)

    def test_sample_harness_requires_source_run_and_trial_mode(self):
        folder, _, _ = self.sample_harness()
        for start in (self.app.start, self.app.start_granted):
            with self.subTest(start=start.__name__):
                with self.assertRaisesRegex(ValueError, "source run"):
                    start(self.id, launch=False, trial=True, from_sample=folder)
                with self.assertRaisesRegex(ValueError, "only be used for a trial"):
                    start(self.id, launch=False, from_run=self.source, from_sample=folder)


class Refused(Trials):
    def test_without_approval_or_a_grant_it_is_pending(self):
        self.assertEqual(self.lx.trial_start(self.path)["status"], "pending")
        self.assertEqual(self.app.runs(self.id), [])

    def test_a_preview_spec_has_no_trial(self):
        self.lx.spec_draft(self.path, mode="controlled_preview")
        with self.assertRaisesRegex(ValueError, "preview"):
            self.lx.trial_start(self.path)

    def test_a_person_can_start_one_directly(self):
        self.approve_it()
        job = self.app.start(self.id, launch=False, trial=True)
        self.assertEqual(job["kind"], "trial")

    def test_a_grant_counts_the_trial_cap_not_the_run_budget(self):
        self.app.set_grant(self.id, {"cap_usd": 0.5, **ACCEPTED})
        self.assertEqual(self.lx.run_start(self.path)["status"], "pending")
        self.assertEqual(self.lx.trial_start(self.path)["status"], "started")

    def test_past_the_grant_cap_it_is_pending(self):
        self.app.set_grant(self.id, {"cap_usd": 0.2, **ACCEPTED})
        started = self.lx.trial_start(self.path)
        self.assertEqual(started["status"], "pending")
        self.assertRegex(started["reason"], "cap")

    def test_past_the_grant_runs_it_is_pending(self):
        self.app.set_grant(self.id, {"cap_usd": 1, "runs": 1, **ACCEPTED})
        run_id = self.lx.trial_start(self.path)["run_id"]
        self.workspace.update_run(run_id, status="stopped", launch_pending=False)
        self.assertRegex(self.lx.trial_start(self.path)["reason"], "used up")

    def test_a_failed_trial_is_not_regraded(self):
        self.approve_it()
        run_id = self.lx.trial_start(self.path)["run_id"]
        self.workspace.update_run(run_id, status="failed", launch_pending=False)
        with self.assertRaisesRegex(ValueError, "graded again"):
            self.app.regrade(self.id, run_id)


class Cap(Trials):
    def test_it_defaults_to_a_quarter(self):
        self.assertEqual(self.lx.spec_show(self.path)["trial_cap_usd"], 0.25)

    def test_a_person_can_change_it_and_the_run_budget_still_bounds_it(self):
        self.app.set_trial_cap(self.id, 0.5)
        self.assertEqual(self.lx.spec_show(self.path)["trial_cap_usd"], 0.5)
        self.app.set_trial_cap(self.id, 10)
        self.assertEqual(self.lx.spec_show(self.path)["trial_cap_usd"], 1)

    def test_its_bounds_hold(self):
        for cap in (0.01, 21, True, "1", float("nan")):
            with self.subTest(cap), self.assertRaisesRegex(ValueError, "trial's cap"):
                self.app.set_trial_cap(self.id, cap)

    def test_an_agent_cannot_change_it(self):
        self.assertFalse(hasattr(self.lx, "set_trial_cap"))


if __name__ == "__main__":
    unittest.main()
