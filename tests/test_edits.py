"""What the improver changed: read from the loop's files, so it shows during a run and after it."""
import json
from pathlib import Path
import unittest

from lx import sandbox
from lx.inventory import write_synthetic
from lx.orchestration import ACTIONS, Orchestration
from lx.taskrun import MAX_EDIT_LINES, clean_base, harness_edits, kept_sample
from lx.worker import execute
from test_tasks import Base


def finished(run, sample, stages, *, is_base=False):
    folder = Path(run)/"outputs"/"runs"/"lx_task-oc-agg1"/sample
    folder.mkdir(parents=True)
    (folder/"summary.json").write_text(json.dumps({"modes": {"seq": {"stages": stages}}, "selections": {"seq": {"chosen_tree_is_base": is_base}}}), encoding="utf-8")


class Files(unittest.TestCase):
    def setUp(self):
        import tempfile
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.run_dir = temp.name
        self.write("base", "systemprompt.md", "old line\n")

    def write(self, label, name, text):
        target = Path(self.run_dir)/"outputs"/"runs"/"lx_task-oc-seq1"/"sample-01"/"harnesses"/label/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def only(self):
        return harness_edits(self.run_dir)["samples"][0]

    def test_a_stage_shows_while_the_sample_is_still_running(self):
        self.write("inv1", "systemprompt.md", "new line\n")
        sample = self.only()
        self.assertFalse(sample["done"])
        self.assertEqual([(s["label"], s["status"]) for s in sample["stages"]], [("inv1", "written")])
        self.assertIn("+new line", sample["stages"][0]["changes"][0]["diff"])

    def test_nothing_written_yet_is_an_empty_list(self):
        self.assertEqual(self.only()["stages"], [])

    def test_a_run_with_no_loop_has_no_samples(self):
        self.assertEqual(harness_edits(Path(self.run_dir)/"nothing"), {"samples": []})

    def test_each_stage_is_compared_with_the_base_harness(self):
        self.write("inv1", "systemprompt.md", "old line\n")
        self.write("inv1", "skills/count/SKILL.md", "count\n")
        self.write("dom1", "systemprompt.md", "old line\n")
        changes = {s["label"]: [(c["file"], c["change"]) for c in s["changes"]] for s in self.only()["stages"]}
        self.assertEqual(changes, {"inv1": [("skills/count/SKILL.md", "added")], "dom1": []})

    def test_stages_come_in_loop_order(self):
        for label in ("c1", "dom1", "inv1"):
            self.write(label, "systemprompt.md", "x\n")
        self.assertEqual([s["label"] for s in self.only()["stages"]], ["inv1", "dom1", "c1"])

    def test_a_finished_sample_says_what_was_kept_and_why(self):
        self.write("inv1", "systemprompt.md", "a\n")
        self.write("dom1", "systemprompt.md", "old line\n")
        self.write("c1", "systemprompt.md", "old line\n")
        finished(self.run_dir, "sample-01", [{"label": "inv1", "fell_back": False, "kept_base": False, "notes": "added a line"},
                                             {"label": "dom1", "fell_back": True, "kept_base": False, "notes": ""},
                                             {"label": "c1", "fell_back": False, "kept_base": True, "notes": "no gain"}], is_base=True)
        sample = self.only()
        self.assertEqual((sample["done"], sample["kept_base"]), (True, True))
        self.assertEqual([s["status"] for s in sample["stages"]], ["written", "failed", "no change kept"])
        self.assertEqual(sample["stages"][0]["notes"], "added a line")

    def test_the_real_folder_name_is_shown(self):
        truth = Path(self.run_dir)/"dataset"/"controller_truth"
        truth.mkdir(parents=True)
        (truth/"sources.json").write_text(json.dumps({"sample-01": {"folder": "sample-05"}}), encoding="utf-8")
        self.assertEqual(self.only()["folder"], "sample-05")

    def test_a_long_diff_is_cut_and_says_so(self):
        self.write("inv1", "systemprompt.md", "".join(f"line {i}\n" for i in range(MAX_EDIT_LINES+50)))
        diff = self.only()["stages"][0]["changes"][0]["diff"]
        self.assertEqual(len(diff), MAX_EDIT_LINES+1)
        self.assertRegex(diff[-1], "more lines")


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class WholeRun(Base):
    def test_a_preview_shows_the_improvers_edits_to_a_person_and_an_agent(self):
        lx = Orchestration(self.app, launch=False)
        path = self.folder()
        write_synthetic(Path(path)/"samples", 6)
        lx.init(path, "photo-request")
        run_id = lx.preview(path)["run_id"]
        self.assertEqual(lx.edits(path, run_id)["samples"], [])
        identifier = self.app.open_workspace(path)["id"]
        workspace = self.app.workspace(identifier)
        execute(self.app, workspace, run_id, workspace.run_dir(run_id)/"snapshot")
        seen = lx.edits(path, run_id)
        self.assertEqual(seen, self.app.run_edits(identifier, run_id))
        self.assertEqual(len(seen["samples"]), 6)
        files = {change["file"] for sample in seen["samples"] for stage_ in sample["stages"] for change in stage_["changes"]}
        self.assertIn("guardrails.md", files)
        self.assertTrue(any(name.startswith("skills/") for name in files))
        self.assertEqual(json.loads(json.dumps(seen)), seen)


class ImprovedMeansEdited(unittest.TestCase):
    def test_a_gain_with_no_edits_is_not_an_improvement(self):
        self.assertIsNone(kept_sample("sample-02", {"sample-02": 0.125}, {"a": "x"}, {"a": "x"}))

    def test_a_gain_with_edits_is(self):
        self.assertEqual(kept_sample("sample-02", {"sample-02": 0.125}, {"a": "x"}, {"a": "y"}), "sample-02")

    def test_edits_with_no_gain_are_not(self):
        self.assertIsNone(kept_sample("sample-02", {"sample-02": 0.0}, {"a": "x"}, {"a": "y"}))


class StartingHarness(unittest.TestCase):
    def adapter(self, **files):
        import tempfile
        from types import SimpleNamespace
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for name, text in {"systemprompt.md": "Answer.", **files}.items():
            path = root/name.replace("__", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return SimpleNamespace(base_harness=lambda: root)

    def test_a_stock_harness_passes_and_its_fingerprint_repeats(self):
        self.assertEqual(clean_base(self.adapter()), clean_base(self.adapter()))

    def test_a_different_prompt_changes_the_fingerprint(self):
        self.assertNotEqual(clean_base(self.adapter()), clean_base(self.adapter(**{"systemprompt.md": "Other."})))

    def test_anything_learned_is_refused(self):
        for name in ("skills__count__SKILL.md", "scripts__sum.py", "plugin__x.md", "sub_agents__y.md"):
            with self.subTest(name), self.assertRaisesRegex(ValueError, "nothing learned"):
                clean_base(self.adapter(**{name: "learned"}))


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class NothingCarriesOver(Base):
    def test_a_second_run_starts_as_the_first_did_though_the_first_learned(self):
        lx = Orchestration(self.app, launch=False)
        path = self.folder()
        write_synthetic(Path(path)/"samples", 6)
        lx.init(path, "photo-request")
        workspace = self.app.workspace(self.app.open_workspace(path)["id"])

        def finish():
            run_id = lx.preview(path)["run_id"]
            return run_id, execute(self.app, workspace, run_id, workspace.run_dir(run_id)/"snapshot")
        first_id, first = finish()
        self.assertTrue(list(workspace.package_dir(first_id).glob("harness/skills/*/SKILL.md")))
        lx.spec_draft(path, goal="A second run")
        second_id, second = finish()
        self.assertEqual(first["base_sha256"], second["base_sha256"])
        base = workspace.run_dir(second_id)/"outputs"/"base"
        self.assertEqual([path_.name for path_ in base.rglob("SKILL.md")], [])
        self.assertEqual([path_.name for path_ in base.rglob("*.py")], [])
        self.assertNotEqual(workspace.run_dir(first_id), workspace.run_dir(second_id))


class Offered(unittest.TestCase):
    def test_an_agent_may_read_them(self):
        self.assertIn("edits", ACTIONS)


if __name__ == "__main__":
    unittest.main()
