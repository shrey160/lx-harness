"""What a run leaves for its reader (D-024): what was learned, what the orchestrator could read, and trajectories on request."""
import io
import json
from pathlib import Path
import unittest
import zipfile

from lx import sandbox
from lx.inventory import write_synthetic
from lx.orchestration import Orchestration
from lx.taskrun import MAX_DIFF_LINES, last_sample, learned, limitations, run_order
from lx.worker import execute
from test_tasks import Base

REPORT = {"means": {"base": 0.5, "harness2": 0.75}, "per_sample": {"s": {}}, "skipped": []}


class Learned(unittest.TestCase):
    def test_a_package_equal_to_the_base_reads_plainly(self):
        text = learned({"a.md": "x"}, {"a.md": "x"}, REPORT, None)
        self.assertIn("kept the base harness, so this package is the base harness.", text)
        self.assertIn("0.50 before, 0.75 after", text)
        self.assertIn("scores chose nothing", text)
        self.assertNotIn("Changes", text)

    def test_a_change_names_its_sample_and_every_change(self):
        text = learned({"kept.md": "same", "edited.md": "old line", "gone.md": "x"}, {"kept.md": "same", "edited.md": "new line", "skill.md": "use it"},
                       REPORT, "s")
        self.assertIn("the harness adopted on s, the last sample in this run's order", text)
        for line in ("Added `skill.md`", "+use it", "Changed `edited.md`", "-old line", "+new line", "Removed `gone.md`"):
            self.assertIn(line, text)
        self.assertNotIn("kept.md", text)

    def test_a_long_change_is_cut_and_says_so(self):
        text = learned({}, {"long.md": "\n".join(f"line {n}" for n in range(500))}, REPORT, "s")
        self.assertIn("more lines", text)
        self.assertLess(text.count("+line"), MAX_DIFF_LINES+1)


class StreamOrder(unittest.TestCase):
    samples = [f"sample-{number:02d}" for number in range(1, 7)]

    def test_runs_differ_and_hold_the_same_samples(self):
        orders = [run_order(self.samples, identifier) for identifier in ("a1", "b2", "c3")]
        self.assertGreater(len({tuple(order) for order in orders}), 1)
        for order in orders:
            self.assertEqual(sorted(order), self.samples)

    def test_a_resume_keeps_its_order(self):
        self.assertEqual(run_order(self.samples, "a1"), run_order(self.samples[::-1], "a1"))


class NoChoiceByScore(unittest.TestCase):
    def report(self, **scores):
        rows = {name: {"base": {"score": 0.5}, "harness2": {"score": score}, "harness_dir": f"h/{name}"} for name, score in scores.items()}
        return {"stream": list(scores), "per_sample": rows}

    def test_the_last_sample_wins_though_another_scored_higher(self):
        self.assertEqual(last_sample(self.report(a=1.0, b=0.0)), "b")
        self.assertEqual(last_sample(self.report(b=0.0, a=1.0)), "a")

    def test_a_last_sample_that_left_no_harness_falls_back_to_the_one_before(self):
        report = self.report(a=1.0, b=0.5)
        del report["per_sample"]["b"]
        self.assertEqual(last_sample(report), "a")
        report["per_sample"]["a"]["harness_dir"] = None
        self.assertIsNone(last_sample(report))

    def test_the_limits_say_where_the_harness_came_from(self):
        spec = {"providers": {"worker": {"model_id": "m"}}, "roles": {"orchestrator": "o", "worker": "w"}}
        report = {"per_sample": {}, "skipped": []}
        self.assertIn("Scores played no part", limitations(spec, report, "s", True))
        self.assertIn("kept the base harness", limitations(spec, report, None, True))


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

    def test_a_trace_can_be_listed_and_read_and_nothing_outside_the_list(self):
        path = self.folder()
        write_synthetic(Path(path)/"samples", 6)
        lx = Orchestration(self.app, launch=False)
        lx.init(path, "photo-request")
        run = lx.preview(path)["run_id"]
        project = self.app.open_workspace(path)
        self.app.stop(project["id"], run)
        self.assertEqual(self.app.run_trajectories(project["id"], run)["executions"], [])
        workspace = self.app.workspace(project["id"])
        folder = workspace.run_dir(run)/"outputs"/"runs"/"executions"/"task-x"/"sample-01"/"base1"
        folder.mkdir(parents=True)
        trace = [{"type": "tools", "names": ["view"]},
                 {"type": "assistant", "content": "looking", "tool_calls": [{"name": "view", "arguments": {"photo": 1}}], "output_tokens": 5},
                 {"type": "tool", "name": "view", "arguments": {"photo": 1}, "result": {"photo": 1}},
                 {"type": "assistant", "content": "done", "tool_calls": [], "output_tokens": 2}]
        (folder/"trace.json").write_text(json.dumps({"final_text": "done", "trace": trace}), encoding="utf-8")
        (workspace.run_dir(run)/"secret.txt").write_text("not a trace", encoding="utf-8")
        listed = self.app.run_trajectories(project["id"], run)["executions"]
        self.assertEqual(listed, [{"path": "task-x/sample-01/base1", "turns": 2, "tool_calls": {"view": 1}, "stopped": None, "answered": True}])
        one = self.app.run_trajectory(project["id"], run, "task-x/sample-01/base1")
        self.assertEqual((one["final_text"], len(one["trace"])), ("done", 4))
        self.assertIsNone(one["opening"])  # a run from before the opening was kept
        (folder/"opening.json").write_text(json.dumps([{"role": "developer", "content": "rules"}, {"role": "user", "content": "request"}]), encoding="utf-8")
        self.assertEqual([m["role"] for m in self.app.run_trajectory(project["id"], run, "task-x/sample-01/base1")["opening"]], ["developer", "user"])
        (folder/"opening.json").write_text("not json", encoding="utf-8")
        self.assertIsNone(self.app.run_trajectory(project["id"], run, "task-x/sample-01/base1")["opening"])
        for bad in ("", None, "../secret.txt", "/etc/passwd", "task-x/..", "task-x\sample-01", "task-x/sample-01", "task-x/sample-01/base1/trace.json"):
            with self.subTest(path=bad), self.assertRaisesRegex(ValueError, "Unknown trajectory"):
                self.app.run_trajectory(project["id"], run, bad)


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

    def test_its_result_holds_no_verdict_from_scores(self):
        result = self.lx.results(self.path, self.run_id)
        self.assertFalse({"improved", "retained", "chosen_sample", "gains"} & set(result))
        self.assertEqual(result["package_sample"] in {f"sample-{number:02d}" for number in range(1, 7)}, True)

    def test_it_records_the_order_its_samples_ran_in(self):
        order = self.lx.results(self.path, self.run_id)["stream_order"]
        self.assertEqual(len(order), 6)
        self.assertTrue(all(folder.startswith("sample-") for folder in order))
        self.assertEqual(len(set(order)), 6)

    def test_an_agent_run_keeps_its_opening_apart_from_the_trace_the_improver_reads(self):
        workspace = self.app.workspace(self.project["id"])
        paths = [item["path"] for item in self.app.run_trajectories(self.project["id"], self.run_id)["executions"]]
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path):
                one = self.app.run_trajectory(self.project["id"], self.run_id, path)
                self.assertEqual([m["role"] for m in one["opening"]][-1], "user")
                self.assertTrue(all(isinstance(m["content"], str) and m["content"] for m in one["opening"]))
                raw = json.loads((workspace.run_dir(self.run_id)/"outputs"/"runs"/"executions"/path/"trace.json").read_text(encoding="utf-8"))
                self.assertEqual(set(raw), {"final_text", "trace"})

    def test_trajectories_hold_traces_and_no_answer_files(self):
        with zipfile.ZipFile(io.BytesIO(self.app.trajectory_bytes(self.project["id"], self.run_id))) as archive:
            names = archive.namelist()
            self.assertTrue(names)
            self.assertTrue(all(name.endswith("/trace.json") for name in names))
            self.assertTrue(json.loads(archive.read(names[0])))


if __name__ == "__main__":
    unittest.main()
