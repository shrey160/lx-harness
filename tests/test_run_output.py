"""What a run leaves for its reader (D-024): what was learned, what the orchestrator could read, and trajectories on request."""
import io
import json
from pathlib import Path
import unittest
import zipfile

from lx import sandbox
from lx.inventory import write_synthetic
from lx.orchestration import Orchestration
from lx.taskrun import MAX_DIFF_LINES, learned, limitations
from lx.worker import execute
from test_tasks import Base

REPORT = {"means": {"base": 0.5, "harness2": 0.75}, "per_sample": {"s": {}}, "skipped": []}


class Learned(unittest.TestCase):
    def test_no_gain_reads_plainly(self):
        text = learned({"a.md": "x"}, {"a.md": "x"}, REPORT, {"s": 0}, None)
        self.assertIn("Nothing the loop tried beat the base harness, so this package is the base harness.", text)
        self.assertIn("0.50 before, 0.75 after", text)
        self.assertNotIn("Changes", text)

    def test_a_gain_names_its_sample_and_every_change(self):
        text = learned({"kept.md": "same", "edited.md": "old line", "gone.md": "x"}, {"kept.md": "same", "edited.md": "new line", "skill.md": "use it"},
                       REPORT, {"s": 0.25}, "s")
        self.assertIn("the harness from s, the largest gain (+0.25 on that sample)", text)
        for line in ("Added `skill.md`", "+use it", "Changed `edited.md`", "-old line", "+new line", "Removed `gone.md`"):
            self.assertIn(line, text)
        self.assertNotIn("kept.md", text)

    def test_a_long_change_is_cut_and_says_so(self):
        text = learned({}, {"long.md": "\n".join(f"line {n}" for n in range(500))}, REPORT, {"s": 1}, "s")
        self.assertIn("more lines", text)
        self.assertLess(text.count("+line"), MAX_DIFF_LINES+1)


class WhatTheOrchestratorCouldRead(unittest.TestCase):
    def notes(self, orchestrator):
        spec = {"providers": {"worker": {"model_id": "m"}}, "roles": {"orchestrator": orchestrator, "worker": "w"}}
        return limitations(spec, {"per_sample": {}, "skipped": []}, None, False)

    def test_a_connected_agent_could_read_the_answers(self):
        self.assertIn("could read every file in the workspace, including the answers", self.notes("external-agent"))

    def test_the_built_in_one_could_not(self):
        self.assertIn("never their values", self.notes("spark-contributor"))


class Trajectories(Base):
    def test_they_need_a_finished_run_with_traces(self):
        path = self.folder()
        write_synthetic(Path(path)/"samples", 6)
        lx = Orchestration(self.app, launch=False)
        lx.init(path, "photo-request")
        run = lx.preview(path)["run_id"]
        project = self.app.open_workspace(path)
        with self.assertRaisesRegex(ValueError, "Wait for the run"):
            self.app.trajectory_bytes(project["id"], run)
        self.app.stop(project["id"], run)
        with self.assertRaisesRegex(ValueError, "no trajectories"):
            self.app.trajectory_bytes(project["id"], run)

    def test_an_agent_cannot_ask_for_them(self):
        self.assertFalse(hasattr(Orchestration, "trajectory_bytes"))


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class APreviewPackage(Base):
    def setUp(self):
        super().setUp()
        self.path = self.folder()
        write_synthetic(Path(self.path)/"samples", 6)
        self.lx = Orchestration(self.app, launch=False)
        self.lx.init(self.path, "photo-request")
        self.run_id = self.lx.preview(self.path)["run_id"]
        self.project = self.app.open_workspace(self.path)
        workspace = self.app.workspace(self.project["id"])
        execute(self.app, workspace, self.run_id, workspace.run_dir(self.run_id)/"snapshot")

    def test_it_holds_what_was_learned_and_who_could_read_what(self):
        with zipfile.ZipFile(io.BytesIO(self.app.package_bytes(self.project["id"], self.run_id))) as archive:
            names = set(archive.namelist())
            self.assertIn("learned.md", names)
            self.assertFalse({name for name in names if "trace" in name})
            self.assertIn("connected agent", archive.read("limitations.md").decode("utf-8"))
        self.assertIn("What was learned", self.lx.results(self.path, self.run_id)["learned"])

    def test_trajectories_hold_traces_and_no_answer_files(self):
        with zipfile.ZipFile(io.BytesIO(self.app.trajectory_bytes(self.project["id"], self.run_id))) as archive:
            names = archive.namelist()
            self.assertTrue(names)
            self.assertTrue(all(name.endswith("/trace.json") for name in names))
            self.assertTrue(json.loads(archive.read(names[0])))


if __name__ == "__main__":
    unittest.main()
