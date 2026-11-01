"""Improver hints: a person's notes on how a kind of task is done, read by the improver alone."""
import json
from pathlib import Path
import tempfile
import unittest

from lx import hints, tasks
from lx.taskrun import _adapter, _folders
from test_tasks import Base
import test_trial

NOTE = "Hear the supervisor's note first, because it can change quantities and prices."


class Notes(unittest.TestCase):
    def test_empty_hints_are_none_and_text_is_trimmed(self):
        self.assertIsNone(hints.clean(None))
        self.assertIsNone(hints.clean({"all": "  ", "samples": {"sample-01": ""}}))
        self.assertEqual(hints.clean({"all": " a ", "samples": {"sample-01": " b "}}), {"all": "a", "samples": {"sample-01": "b"}})

    def test_bad_shapes_and_sizes_are_refused(self):
        for bad in ("text", {"other": "x"}, {"all": 3}, {"samples": []}, {"samples": {"photo": "x"}}, {"all": "x"*(hints.MAX_CHARS+1)}):
            with self.subTest(bad), self.assertRaises(ValueError):
                hints.clean(bad)

    def test_the_spec_wins_over_a_file_and_a_file_fills_a_blank(self):
        merged = hints.fill_blanks({"all": "spec", "samples": {}}, {"all": "file", "samples": {"sample-02": "only in file"}})
        self.assertEqual(merged, {"all": "spec", "samples": {"sample-02": "only in file"}})

    def test_drift_names_each_file_that_differs(self):
        spec = {"all": "a", "samples": {"sample-01": "b"}}
        self.assertEqual(hints.drift(spec, spec), [])
        found = hints.drift(spec, {"all": "changed", "samples": {"sample-01": "b", "sample-02": "new"}})
        self.assertEqual(len(found), 2)
        self.assertTrue(any("sample-02" in text for text in found))

    def test_a_hint_with_boxes_or_the_expected_total_is_refused_without_saying_which(self):
        truths = {"sample-01": {"total_claim": 17.25}}
        for bad in ("the apple is at [90, 194, 233, 342]", "the claim comes to 17.25 here"):
            with self.subTest(bad), self.assertRaises(ValueError) as caught:
                hints.refuse_answers(hints.clean({"all": bad}), truths)
            self.assertNotIn("17.25", str(caught.exception))
        hints.refuse_answers(hints.clean({"all": NOTE + " Totals add up per line, such as 3 + 4."}), truths)

    def test_what_the_improver_reads_opens_with_the_preface_and_holds_both_texts(self):
        text = hints.compose({"all": "everyone", "samples": {"sample-02": "only here"}}, "sample-02")
        self.assertIn(hints.PREFACE.strip(), text)
        self.assertIn("everyone", text)
        self.assertIn("only here", text)
        self.assertNotIn("only here", hints.compose({"all": "everyone", "samples": {"sample-02": "only here"}}, "sample-01"))
        self.assertIsNone(hints.compose({"all": None, "samples": {"sample-02": "x"}}, "sample-01"))
        self.assertIsNone(hints.compose(None, "sample-01"))

    def test_files_in_the_samples_become_hints(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"sample-01").mkdir()
            (root/hints.FILE).write_text("for all", encoding="utf-8")
            (root/"sample-01"/hints.FILE).write_text("for one", encoding="utf-8")
            (root/"sample-02").mkdir()
            self.assertEqual(hints.from_files(root), {"all": "for all", "samples": {"sample-01": "for one"}})


class InTheSpec(Base):
    def setUp(self):
        super().setUp()
        self.project = self.inventory()
        self.id = self.project["id"]

    def draft(self):
        return self.app.project(self.id)["draft"]

    def test_hints_are_saved_in_a_new_version_that_clears_approval_and_are_kept_by_later_edits(self):
        approved = self.approve(self.project)
        self.assertIsNotNone(approved["approved"])
        saved = self.app.draft_spec(self.id, hints={"all": NOTE, "samples": {}})
        self.assertEqual(saved["draft"]["hints"], {"all": NOTE, "samples": {}})
        self.assertEqual(saved["draft"]["version"], self.project["draft"]["version"]+1)
        self.assertIsNone(saved["approved"])
        kept = self.app.edit_spec(self.id, {"goal": "another goal", "budget_usd": 1})
        self.assertEqual(kept["draft"]["hints"]["all"], NOTE)
        cleared = self.app.draft_spec(self.id, hints={})
        self.assertNotIn("hints", cleared["draft"])

    def test_a_hint_with_boxes_makes_no_new_version(self):
        with self.assertRaisesRegex(ValueError, "never the answers"):
            self.app.draft_spec(self.id, hints={"all": "look at [1, 2, 3, 4]"})
        self.assertEqual(self.draft()["version"], self.project["draft"]["version"])

    def test_the_notice_says_the_improver_gets_the_hints(self):
        self.assertNotIn("hints", json.dumps(self.app.project(self.id)["notice"]))
        saved = self.app.draft_spec(self.id, hints={"all": NOTE})
        self.assertIn("hints you wrote", json.dumps(saved["notice"]))

    def test_a_file_seeds_a_blank_and_a_later_difference_blocks_approval_until_settled(self):
        samples = self.app.workspace(self.id).samples_path
        (samples/hints.FILE).write_text("from the file", encoding="utf-8")
        seeded = self.app.draft_spec(self.id, goal="g")
        self.assertEqual(seeded["draft"]["hints"]["all"], "from the file")
        self.assertEqual(seeded["hint_drift"], [])
        (samples/hints.FILE).write_text("changed in the file", encoding="utf-8")
        view = self.app.project(self.id)
        self.assertEqual(len(view["hint_drift"]), 1)
        with self.assertRaisesRegex(ValueError, "Settle the hints"):
            self.approve(view)
        self.assertTrue(any("differs" in item["problem"] for item in self.app.ready(self.id)["problems"]))
        taken = self.app.draft_spec(self.id, take_hint_files=True)
        self.assertEqual(taken["draft"]["hints"]["all"], "changed in the file")
        self.assertEqual(taken["hint_drift"], [])
        self.assertIsNotNone(self.approve(taken)["approved"])

    def test_no_hints_and_no_files_leave_the_spec_as_it_was(self):
        self.assertNotIn("hints", self.draft())
        self.assertEqual(self.app.project(self.id)["hint_drift"], [])


class ReachesOnlyTheImprover(test_trial.Trials):
    def test_hints_are_staged_for_the_adapted_samples_and_never_reach_the_worker(self):
        self.lx.spec_draft(self.path, hints=NOTE, sample_hints={"sample-01": "Only this sample."})
        self.approve_it()
        job = self.app.start(self.id, launch=False)
        run_dir, spec = self.workspace.run_dir(job["id"]), job["spec"]
        self.assertEqual(spec["hints"]["all"], NOTE)
        stream, truth, _ = tasks.build_dataset(run_dir/"snapshot", run_dir/"dataset", spec["task"], preview=False)
        adapter = _adapter(spec["task"], run_dir, run_dir/"outputs", None, truth, self.workspace.check_source(spec["check"]),
                           live=True, limits=self.app.limits_of(spec), tools=tasks.spec_tools(spec))
        folders = _folders(run_dir)
        wrote = hints.stage(adapter, spec["hints"], folders, stream)
        self.assertEqual(sorted(wrote), sorted(stream))
        for sample in stream:
            text = (Path(adapter.task_files(sample))/"hints.md").read_text(encoding="utf-8")
            self.assertIn(NOTE, text)
            self.assertEqual("Only this sample." in text, folders[sample] == "sample-01")
        from harness2.core import context
        with tempfile.TemporaryDirectory() as folder:
            built = context.build(Path(folder)/"context", adapter, stream[0])
            self.assertTrue((Path(folder)/"context"/"source"/"hints.md").is_file())
            self.assertIn("task source materials", built.layout)
        sample = adapter.by_id[stream[0]]
        messages = adapter.domain.messages(sample, "prompt", [(100, 100)]*len(sample["photos"]))
        self.assertNotIn(NOTE, json.dumps(messages))

    def test_a_run_without_hints_stages_nothing(self):
        self.approve_it()
        job = self.app.start(self.id, launch=False)
        run_dir, spec = self.workspace.run_dir(job["id"]), job["spec"]
        stream, truth, _ = tasks.build_dataset(run_dir/"snapshot", run_dir/"dataset", spec["task"], preview=False)
        adapter = _adapter(spec["task"], run_dir, run_dir/"outputs", None, truth, self.workspace.check_source(spec["check"]),
                           live=True, limits=self.app.limits_of(spec), tools=tasks.spec_tools(spec))
        self.assertEqual(hints.stage(adapter, spec.get("hints"), _folders(run_dir), stream), [])
        self.assertFalse(any((Path(adapter.task_files(sample))/"hints.md").exists() for sample in stream))

    def test_an_agent_can_set_hints_and_a_sample_text_is_removed_by_empty_text(self):
        self.lx.spec_draft(self.path, hints=NOTE, sample_hints={"sample-01": "one", "sample-02": "two"})
        shown = self.lx.spec_show(self.path)
        self.assertEqual(shown["hints"], {"all": NOTE, "samples": {"sample-01": "one", "sample-02": "two"}})
        self.lx.spec_draft(self.path, sample_hints={"sample-01": ""})
        self.assertEqual(self.lx.spec_show(self.path)["hints"]["samples"], {"sample-02": "two"})
        self.assertEqual(self.lx.spec_show(self.path)["hints"]["all"], NOTE)


if __name__ == "__main__":
    unittest.main()
