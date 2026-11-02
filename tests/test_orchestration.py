"""The orchestrator's actions (D-024): a scripted caller reaches a finished preview, and what stays with a person is refused."""
from pathlib import Path
import re
import shutil
import unittest
from unittest.mock import patch

from lx import check_forms, sandbox, tasks
from lx.engine import EXTERNAL
from lx.inventory import write_synthetic
from lx.inventory_data import _tone
from lx.orchestration import ACTIONS, Orchestration
from lx.worker import execute
from test_tasks import Base

PERSON_ONLY = ("approve_preview", "set_budget", "create", "update", "delete", "release_held", "edit_spec", "discuss")


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

    def test_a_draft_check_is_tested_without_saving_it(self):
        self.begin()
        before = self.lx.spec_show(self.path)
        self.assertEqual(self.lx.check_test(self.path, tasks.inventory_check_source()+"# draft")["samples"], 6)
        with self.assertRaisesRegex(ValueError, "crashed"):
            self.lx.check_test(self.path, "raise SystemExit(3)")
        after = self.lx.spec_show(self.path)
        self.assertEqual((after["version"], after["check"]), (before["version"], before["check"]))

    def test_a_function_check_is_saved_with_its_kit_and_shown_as_written(self):
        self.begin()
        text = "def score(truth, answer):\n    return answer == truth\n"
        self.lx.spec_draft(self.path, check=text)
        self.assertEqual(self.lx.spec_show(self.path)["check"], text)
        self.assertEqual(self.lx.check_test(self.path)["samples"], 6)
        self.lx.spec_draft(self.path, goal="Count what the request asks for.")
        self.assertEqual(self.lx.spec_show(self.path)["check"], text)

    def write_eval(self, name, text):
        folder = Path(self.path)/"eval"
        folder.mkdir(exist_ok=True)
        (folder/name).write_text(text, encoding="utf-8")

    def test_an_eval_file_is_tested_then_taken_without_resending_it(self):
        self.begin()
        before = self.lx.spec_show(self.path)
        text = "def score(truth, answer):\n    return answer == truth\n"
        self.write_eval("check.py", text)
        self.assertEqual(self.lx.check_test(self.path, use_eval_file=True)["samples"], 6)
        self.assertEqual(self.lx.spec_show(self.path)["version"], before["version"])
        self.lx.spec_draft(self.path, use_eval_file=True)
        after = self.lx.spec_show(self.path)
        self.assertEqual((after["version"], after["check"], after["eval_drift"]), (before["version"]+1, text, []))

    def test_an_eval_file_that_differs_from_the_spec_blocks_approval(self):
        project = self.begin()
        self.write_eval("rules.json", '{"rules": [{"field": "answer", "rule": "equals"}]}')
        self.assertEqual(self.lx.spec_show(self.path)["eval_drift"], ["eval/rules.json differs from the spec's check"])
        self.assertIn("eval file", " ".join(item["problem"] for item in self.lx.ready(self.path)["problems"]))
        with self.assertRaisesRegex(ValueError, "Settle the check"):
            self.app.approve_preview(project["id"])

    def test_two_eval_files_and_none_are_refused(self):
        self.begin()
        with self.assertRaisesRegex(ValueError, "no check in eval/"):
            self.lx.spec_draft(self.path, use_eval_file=True)
        self.write_eval("check.py", "x")
        self.write_eval("rules.json", "{}")
        with self.assertRaisesRegex(ValueError, "Keep one check"):
            self.lx.check_test(self.path, use_eval_file=True)
        with self.assertRaisesRegex(ValueError, "not both"):
            self.lx.check_test(self.path, "x", use_eval_file=True)

    def test_eval_init_writes_rules_to_start_from_and_never_overwrites(self):
        self.begin()
        before = self.lx.spec_show(self.path)
        self.assertEqual(self.lx.eval_init(self.path)["file"], "eval/rules.json")
        written = (Path(self.path)/"eval"/"rules.json").read_text(encoding="utf-8")
        self.assertEqual(self.lx.spec_show(self.path)["version"], before["version"])
        self.assertTrue(check_forms.check_rules(written)["rules"])
        self.assertTrue(self.lx.check_test(self.path, use_eval_file=True)["samples"])
        with self.assertRaisesRegex(ValueError, "already a check"):
            self.lx.eval_init(self.path)

    def test_a_spec_can_drop_its_check_and_take_one_back(self):
        self.begin()
        text = "def score(truth, answer):\n    return answer == truth\n"
        self.lx.spec_draft(self.path, no_check=True)
        self.assertIsNone(self.lx.spec_show(self.path)["check"])
        with self.assertRaisesRegex(ValueError, "no check"):
            self.lx.check_test(self.path)
        with self.assertRaisesRegex(ValueError, "no check"):
            self.lx.check_try(self.path)
        self.lx.spec_draft(self.path, goal="Still none.")
        self.assertIsNone(self.lx.spec_show(self.path)["check"])
        self.assertEqual(self.lx.check_test(self.path, text)["samples"], 6)
        self.lx.spec_draft(self.path, check=text)
        self.assertEqual(self.lx.spec_show(self.path)["check"], text)
        with self.assertRaisesRegex(ValueError, "not both"):
            self.lx.spec_draft(self.path, check=text, no_check=True)

    def test_a_check_file_beside_a_spec_with_none_is_drift(self):
        self.begin()
        self.lx.spec_draft(self.path, no_check=True)
        self.write_eval("check.py", "x")
        self.assertEqual(self.lx.spec_show(self.path)["eval_drift"], ["eval/check.py is not in use, because this spec has no check"])

    def test_live_samples_need_no_truth_only_without_a_check(self):
        self.begin()
        samples = Path(self.path)/"samples"
        for index, folder in enumerate(sorted(samples.iterdir()), 1):
            (folder/"truth.json").unlink()
            (folder/"request.wav").write_bytes(_tone(index))
        self.lx.spec_draft(self.path, mode="live")
        self.assertIn("missing truth.json", str(self.lx.ready(self.path)["problems"]))
        self.lx.spec_draft(self.path, no_check=True)
        self.assertEqual(self.lx.ready(self.path)["problems"], [])
        self.lx.spec_draft(self.path, mode="controlled_preview")
        self.assertIn("missing truth.json", str(self.lx.ready(self.path)["problems"]))

    def test_a_new_task_is_read_by_its_own_rules_not_the_old_drafts(self):
        self.begin()
        samples = Path(self.path)/"samples"
        for folder in samples.iterdir():
            (folder/"truth.json").write_text('{"lines": []}', encoding="utf-8")
        shutil.rmtree(samples/"sample-06")
        before = self.lx.spec_show(self.path)["version"]
        self.assertEqual(self.lx.spec_draft(self.path, task={"truth_schema": "free", "stream_samples": 5})["version"], before+1)

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

    def preview(self, workspace):
        run_id = self.lx.preview(self.path)["run_id"]
        execute(self.app, workspace, run_id, workspace.run_dir(run_id)/"snapshot")
        return run_id

    def test_a_run_with_no_check_finishes_unscored(self):
        project = self.begin()
        workspace = self.app.workspace(project["id"])
        self.lx.spec_draft(self.path, no_check=True)
        run_id = self.preview(workspace)
        run = self.lx.run_status(self.path)["runs"][0]
        self.assertEqual(run["status"], "completed", run)
        result = self.lx.results(self.path, run_id)
        self.assertEqual((result["scored"], result["means"], len(result["samples"])), (False, None, 6))
        self.assertNotIn("scores", result)
        self.assertIn("Not scored", result["learned"])
        package = workspace.package_dir(run_id)
        self.assertFalse((package/"check.py").exists())
        self.assertIn("Never scored", (package/"limitations.md").read_text(encoding="utf-8"))
        self.assertTrue((package/"harness"/"systemprompt.md").is_file())
        replies = self.app.run_replies(project["id"], run_id)["samples"]
        self.assertEqual(len(replies), 6)
        self.assertEqual(set(replies[0]), {"sample", "before", "after", "same"})
        self.assertTrue(replies[0]["before"])

    def improver_input(self, workspace, run_id):
        """Every file of what the improver read and wrote, with what differs between any two runs taken out."""
        audit = workspace.run_dir(run_id)/"outputs"/"proposer_audit"
        folder = str(workspace.run_dir(run_id))
        clean = lambda text: re.sub(r'"\w+": [\d.e-]+,(\s+"cost_usd")', r"\1", text.replace(folder, "").replace(folder.replace("\\", "\\\\"), "").replace(run_id, ""))
        return {path.relative_to(audit).as_posix(): clean(path.read_text(encoding="utf-8", errors="replace")) for path in sorted(audit.rglob("*")) if path.is_file()}

    def test_a_check_changes_nothing_the_loop_sees_or_learns(self):
        project = self.begin()
        workspace = self.app.workspace(project["id"])
        order = lambda stream, identifier: sorted(stream)
        with patch("lx.taskrun.run_order", order):
            checked = self.preview(workspace)
            self.lx.spec_draft(self.path, no_check=True)
            unchecked = self.preview(workspace)
        files = [{path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}
                 for root in (workspace.package_dir(run)/"harness" for run in (checked, unchecked))]
        self.assertEqual(files[0], files[1])
        first, second = self.improver_input(workspace, checked), self.improver_input(workspace, unchecked)
        self.assertTrue(first)
        self.assertEqual(sorted(first), sorted(second))
        self.assertEqual([name for name in first if first[name] != second[name]], [])

    def test_saved_answers_show_what_a_draft_check_would_change(self):
        project = self.begin()
        run_id = self.lx.preview(self.path)["run_id"]
        workspace = self.app.workspace(project["id"])
        execute(self.app, workspace, run_id, workspace.run_dir(run_id)/"snapshot")
        before = self.lx.results(self.path, run_id)
        same = self.lx.check_try(self.path)
        self.assertGreater(same["answers"], 0)
        self.assertEqual((same["changed"], same["results"]), (0, []))
        harsh = self.lx.check_try(self.path, 'def score(truth, answer):\n    return {"never": False}\n')
        self.assertEqual(harsh["changed"], harsh["answers"])
        first = harsh["results"][0]
        self.assertEqual((first["new"], first["run"]), (0, run_id))
        self.assertIn({"name": "never", "old": None, "new": False}, first["changed"])
        self.assertEqual(set(first), {"run", "sample", "where", "old", "new", "changed"})
        self.assertEqual(self.lx.results(self.path, run_id), before)


if __name__ == "__main__":
    unittest.main()
