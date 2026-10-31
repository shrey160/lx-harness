"""The orchestrator's actions (D-024): a scripted caller reaches a finished preview, and what stays with a person is refused."""
from pathlib import Path
import unittest
from unittest.mock import patch

from lx import sandbox, tasks
from lx.engine import EXTERNAL
from lx.inventory import write_synthetic
from lx.inventory_data import _tone
from lx.orchestration import ACTIONS, Orchestration
from lx.worker import execute
from test_tasks import Base

PERSON_ONLY = ("approve", "approve_preview", "set_budget", "create", "update", "delete", "release_held", "edit_spec", "discuss")


class Actions(Base):
    def setUp(self):
        super().setUp()
        self.lx = Orchestration(self.app, launch=False)
        self.path = self.folder()
        write_synthetic(Path(self.path)/"samples", 6)

    def begin(self):
        self.lx.init(self.path, "photo-request", goal="Count things in photos")
        return self.app.open_workspace(self.path)


class WhatItMayDo(Actions):
    def test_the_action_list_leaves_out_what_a_person_holds(self):
        offered = {name for name in vars(Orchestration) if not name.startswith("_")}
        self.assertEqual(offered, set(ACTIONS))
        self.assertFalse(offered & set(PERSON_ONLY))

    def test_status_says_a_key_exists_and_never_what_it_is(self):
        self.app.registry.update("spark-contributor", api_key="dummy-ui-key")
        status = self.lx.status()
        self.assertTrue(status["meta_key"])
        self.assertNotIn("dummy-ui-key", str(status))
        self.assertFalse({"has_key", "api_key", "base_url"} & set(status["models"][0]))

    def test_samples_show_the_folder_as_it_is_now(self):
        self.begin()
        added = Path(self.path)/"samples"/"sample-01"/"notes.txt"
        added.write_text("new", encoding="utf-8")
        self.assertIn("sample-01/notes.txt", {item["path"] for item in self.lx.samples(self.path)["inventory"]})
        self.assertEqual(self.lx.read_sample(self.path, "sample-01/notes.txt")["text"], "new")

    def test_a_preview_cost_says_it_is_simulated(self):
        self.begin()
        self.lx.preview(self.path)
        self.assertIs(self.lx.run_status(self.path)["runs"][0]["simulated_cost"], True)

    def test_a_run_records_when_it_finished_and_a_resume_clears_it(self):
        self.begin()
        run_id = self.lx.preview(self.path)["run_id"]
        self.assertIsNone(self.lx.run_status(self.path)["runs"][0]["finished_at"])
        workspace = self.app.workspace(self.app.open_workspace(self.path)["id"])
        workspace.update_run(run_id, status="failed", launch_pending=False)
        first = self.lx.run_status(self.path)["runs"][0]["finished_at"]
        self.assertTrue(first)
        workspace.update_run(run_id, phase="failed")
        self.assertEqual(self.lx.run_status(self.path)["runs"][0]["finished_at"], first)
        workspace.update_run(run_id, status="running")
        self.assertNotIn("finished_at", workspace.run(run_id))

    def test_going_live_says_a_person_is_needed(self):
        self.begin()
        self.assertNotIn("note", self.lx.spec_draft(self.path, goal="Count cups"))
        self.assertIn("approval", self.lx.spec_draft(self.path, mode="live")["note"])

    def test_the_budget_a_person_set_stays(self):
        self.begin()
        project = self.app.open_workspace(self.path)
        self.app.edit_spec(project["id"], {"budget_usd": 3})
        self.lx.spec_draft(self.path, goal="Count pens")
        self.assertEqual(self.lx.spec_show(self.path)["budget_usd"], 3)
        with self.assertRaises(TypeError):
            self.lx.spec_draft(self.path, budget_usd=20)

    def test_a_spec_has_no_place_for_what_the_loop_learns(self):
        self.begin()
        for field in ("skills", "scripts", "guidance", "guardrails", "harness"):
            with self.subTest(field), self.assertRaisesRegex(ValueError, "template fields"):
                self.lx.spec_draft(self.path, task={field: "always segment first"})

    def test_every_edit_is_a_new_version_and_clears_approval(self):
        self.begin()
        self.lx.preview(self.path)
        before = self.lx.spec_show(self.path)
        self.assertEqual(before["approved"], {"by": "preview"})
        self.app.stop(self.app.open_workspace(self.path)["id"], self.lx.run_status(self.path)["runs"][0]["id"])
        after = self.lx.spec_draft(self.path, goal="Count cups")
        self.assertEqual(after["version"], before["version"]+1)
        self.assertIsNone(self.lx.spec_show(self.path)["approved"])

    def test_task_text_and_check_change_together_in_one_version(self):
        self.begin()
        before = self.lx.spec_show(self.path)
        check = tasks.inventory_check_source()+"# reviewed"
        self.lx.spec_draft(self.path, task={"base_prompt": "The request is spoken; transcribe turns it into text."}, check=check)
        after = self.lx.spec_show(self.path)
        self.assertEqual(after["version"], before["version"]+1)
        self.assertEqual(after["check"], check)
        self.assertEqual(after["task"]["contract"], before["task"]["contract"])
        self.assertEqual(self.lx.check_test(self.path)["samples"], 6)

    def test_a_task_needs_its_tools(self):
        self.begin()
        with self.assertRaisesRegex(ValueError, "transcription"):
            self.lx.spec_draft(self.path, tools=[tasks.SEGMENTATION])


class LiveStaysWithAPerson(Actions):
    def live(self):
        self.begin()
        self.lx.spec_draft(self.path, mode="live")
        return self.app.open_workspace(self.path)

    def test_a_live_run_stays_pending_without_a_person(self):
        project = self.live()
        with patch.object(self.app.registry, "meta_key_source", return_value=None):
            self.assertEqual(self.lx.run_start(self.path)["status"], "blocked")
        self.app.registry.update("spark-contributor", api_key="dummy-ui-key")
        self.assertEqual(self.lx.run_start(self.path)["status"], "pending")
        self.assertEqual(self.app.runs(project["id"]), [])

    def test_preview_cannot_approve_a_live_spec(self):
        project = self.live()
        with self.assertRaisesRegex(ValueError, "only a person"):
            self.lx.preview(self.path)
        self.assertIsNone(self.app.project(project["id"])["approved"])

    def test_a_preview_approval_cannot_become_a_live_one(self):
        self.begin()
        self.lx.preview(self.path)
        project = self.app.open_workspace(self.path)
        self.app.stop(project["id"], self.lx.run_status(self.path)["runs"][0]["id"])
        self.lx.spec_draft(self.path, mode="live")
        self.app.registry.update("spark-contributor", api_key="dummy-ui-key")
        self.assertEqual(self.lx.run_start(self.path)["status"], "pending")

    def test_a_person_approved_live_run_starts(self):
        project = self.live()
        for sample in (Path(self.path)/"samples").iterdir():
            (sample/"request.wav").write_bytes(_tone(1))
        self.app.registry.update("spark-contributor", api_key="dummy-ui-key")
        self.approve(project, notice_accepted=True)
        started = self.lx.run_start(self.path)
        self.assertEqual(started["status"], "started")
        self.assertEqual(self.lx.run_status(self.path)["runs"][0]["approved_by"], "person")


class ConnectedAgent(Actions):
    def test_it_is_the_orchestrator_and_nothing_else(self):
        project = self.begin()
        self.assertEqual(project["draft"]["roles"]["orchestrator"], EXTERNAL)
        self.assertTrue(project["external_orchestrator"])
        with self.assertRaisesRegex(ValueError, "only be the orchestrator"):
            self.lx.spec_draft(self.path, worker=EXTERNAL)

    def test_limits_match_a_built_in_orchestrator(self):
        project = self.begin()
        built_in = self.inventory()
        self.assertEqual(project["limit_defaults"], built_in["limit_defaults"])

    def test_the_notice_names_it_and_no_key_is_needed_for_it(self):
        project = self.begin()
        entry = next(item for item in project["notice"] if item["who"] == "orchestrator")
        self.assertEqual(entry["host"], "your connected agent")
        self.assertEqual(self.app.build_clients(self.app.workspace(project["id"]), project["draft"], "scope", 1).keys(), {"improver", "worker"})

    def test_the_built_in_chat_points_to_it(self):
        project = self.begin()
        with self.assertRaisesRegex(ValueError, "connected agent"):
            self.app.discuss(project["id"], "hello", True)

    def test_a_built_in_orchestrator_keeps_its_entry(self):
        lx = Orchestration(self.app, external=False, launch=False)
        lx.init(self.path, "photo-request")
        self.assertEqual(lx.spec_show(self.path)["roles"]["orchestrator"], "spark-contributor")


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class WholeFlow(Actions):
    def test_a_scripted_caller_goes_from_samples_to_a_finished_preview(self):
        self.lx.status()
        self.lx.init(self.path, "photo-request", goal="Count things in photos")
        self.assertEqual(len(self.lx.samples(self.path)["inventory"]) > 0, True)
        self.lx.spec_draft(self.path, task={"base_prompt": "The request is spoken; transcribe turns it into text."})
        self.assertEqual(self.lx.check_test(self.path)["samples"], 6)
        run_id = self.lx.preview(self.path)["run_id"]
        project = self.app.open_workspace(self.path)
        workspace = self.app.workspace(project["id"])
        execute(self.app, workspace, run_id, workspace.run_dir(run_id)/"snapshot")
        run = self.lx.run_status(self.path)["runs"][0]
        self.assertEqual((run["status"], run["approved_by"], run["mode"]), ("completed", "preview", "controlled_preview"))
        self.assertNotIn("spec", self.lx.results(self.path, run_id))


if __name__ == "__main__":
    unittest.main()
