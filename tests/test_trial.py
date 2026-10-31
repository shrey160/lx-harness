"""`trial`: one real sample on the base harness (D-025), run here on doubles of the live providers."""
import json
from pathlib import Path
import unittest

from lx import sandbox, tasks
from lx.inventory import FakeShapeSegmentation, write_synthetic
from lx.inventory_data import _tone
from lx.inventory_workflow import InventoryPreviewTransport
from lx.orchestration import Orchestration
from lx.perception import FakeTranscription, media_hash
from lx.spike import wire_reply
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
        self.assertEqual(set(seen) - {"usage"}, {"kind", "task", "live", "sample", "score", "checks", "stopped", "tool_calls", "note", "learned"})
        self.assertIsNone(seen["learned"])
        self.assertEqual({tuple(sorted(item)) for item in seen["checks"]}, {("name", "passed")})
        self.assertGreater(seen["tool_calls"]["transcribe"], 0)
        self.assertTrue(0 <= seen["score"] <= 1)
        text = json.dumps(seen)
        answer = (self.workspace.run_dir(run_id)/"outputs"/"runs"/"executions"/"trial"/"answer.json").read_text(encoding="utf-8")
        self.assertNotIn(answer, text)
        self.assertNotIn("final_text", text)

    def test_a_trial_that_stops_says_so(self):
        self.transport = lambda body, timeout: (wire_reply("PREFLIGHT_OK") if body["messages"][0]["content"].startswith("LX_PREFLIGHT") else
                                                wire_reply(calls=[{"id": "x", "type": "function", "function": {"name": "transcribe", "arguments": "{}"}}]))
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
