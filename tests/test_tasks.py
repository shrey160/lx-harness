"""Spec-driven tasks: a run defined by its spec and check file, on the real engine and sandbox."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from lx import sandbox, tasks
from lx.engine import Engine
from lx.inventory import verify_inventory, write_synthetic
from lx.inventory_data import _tone
from lx.perception import media_hash

CUSTOM_CHECK = '''
import json, sys
request = json.load(sys.stdin)
if "examples" in request:
    print(json.dumps({"answers": [{"total": t["total"]} for t in request["examples"]]}))
else:
    out = []
    for item in request["items"]:
        answer = item["answer"]
        shape = isinstance(answer, dict) and set(answer) == {"total"}
        right = shape and answer["total"] == item["truth"]["total"]
        out.append({"checks": [{"name": "shape", "passed": shape, "error": None if shape else "shape: wrong"},
                               {"name": "total", "passed": right, "error": None if right else "total: wrong"}]})
    print(json.dumps({"results": out}))
'''


class MemoryKeychain:
    def __init__(self): self.values = {}
    def available(self): return True
    def set(self, name, value): self.values[name] = value
    def get(self, name): return self.values.get(name)
    def delete(self, name): self.values.pop(name, None)


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Engine(self.root/"state", keychain=MemoryKeychain())

    def folder(self):
        return tempfile.mkdtemp(dir=self.temp.name)

    def inventory(self):
        return self.app.create_project({"source": "demo", "task": "inventory", "name": "Inventory", "path": self.folder()})

    def approve(self, project, **extra):
        return self.app.approve(project["id"], {"version": project["draft"]["version"], "data_approved": True,
                                                "contributor_accepted": True, **extra})

    def entry(self, name, url="https://openrouter.ai/api/v1"):
        return self.app.registry.create(name, "test/"+name, ["General"], base_url=url, accepts_images=True, supports_tools=True,
                                        pricing={"input_per_million": 0.3, "output_per_million": 1.2}, context_limit=131072)["id"]


class TaskSpecTests(Base):
    def test_validation_refuses_malformed_tasks(self):
        good = tasks.inventory_task()
        self.assertIs(tasks.validate_task(good), good)
        for change in ({"template": "other"}, {"extra": 1}, {"name": "Bad Name"}, {"contract": ""}, {"stream_samples": 0},
                       {"truth_schema": "x"}, {"run_seconds": 5}):
            with self.subTest(change), self.assertRaises(ValueError):
                tasks.validate_task({**good, **change})

    def test_a_workspace_holds_the_task_in_its_spec_and_the_check_as_a_file(self):
        p = self.inventory()
        self.assertEqual(p["draft"]["task"]["name"], "lx_inventory")
        check = Path(p["path"])/".lx"/"checks"/"v1"/"check.py"
        self.assertEqual(check.read_text(encoding="utf-8"), tasks.inventory_check_source())
        self.assertEqual(p["draft"]["check"], {"version": 1, "sha256": tasks.source_hash(tasks.inventory_check_source())})
        edited = self.app.edit_spec(p["id"], {"goal": "New goal", "budget_usd": 1})
        self.assertEqual(edited["draft"]["version"], 2)
        self.assertEqual(edited["draft"]["check"], p["draft"]["check"])
        self.assertEqual(edited["draft"]["task"], p["draft"]["task"])

    def test_audio_is_taken_in_as_a_sample(self):
        p = self.inventory()
        folder = Path(p["path"])/"samples"/"sample-01"
        (folder/"request.wav").write_bytes(_tone(1))
        workspace = self.app.workspace(p["id"])
        kinds = {i["path"]: i["kind"] for i in workspace.inspect_samples()["inventory"]}
        self.assertEqual(kinds["sample-01/request.wav"], "audio")

    def test_too_few_samples_are_refused_before_any_check_runs(self):
        folder = Path(self.folder())
        write_synthetic(folder/"samples", 5)
        p = self.app.create_project({"source": "folder", "task": "inventory", "path": str(folder)})
        with self.assertRaisesRegex(ValueError, "at least 6"):
            self.approve(p)
        self.assertIsNone(self.app.project(p["id"])["approved"])


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class TaskRunTests(Base):
    def test_inventory_runs_from_its_spec_and_check_file_through_a_launched_worker(self):
        p = self.approve(self.inventory())
        self.assertEqual(p["approved"]["self_test"]["samples"], 6)
        job = self.app.start(p["id"], launch=False)
        run = subprocess.run([sys.executable, "-m", "lx.worker", "--root", str(self.app.root), "--workspace", p["path"], "--job", job["id"]],
                             capture_output=True, text=True, timeout=400, env={**os.environ, "PYTHONUTF8": "1"})
        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
        workspace = self.app.workspace(p["id"])
        saved = workspace.run(job["id"])
        self.assertEqual(saved["status"], "completed", saved)
        result = saved["result"]
        self.assertEqual(result["task"], "lx_inventory")
        self.assertFalse(result["live"])
        self.assertEqual(set(result["scores"]["base"]), {f"sample-{i:02d}" for i in range(1, 7)})
        self.assertTrue(0 <= result["means"]["base"] <= 1)
        run_dir = workspace.run_dir(job["id"])
        truth = json.loads((run_dir/"dataset"/"controller_truth"/"ground_truth.json").read_text(encoding="utf-8"))
        for sample, score in result["scores"]["harness2"].items():
            answers = sorted(run_dir.glob(f"outputs/runs/executions/**/{sample}/**/answer.json"))
            self.assertTrue(answers, sample)
        # The check file and the built-in scorer agree on what this run produced.
        for path in sorted(run_dir.glob("outputs/runs/executions/**/answer.json"))[:6]:
            sample = next(part for part in path.parts if part.startswith("sample-"))
            answer = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(tasks.run_check(tasks.inventory_check_source(), truth[sample], answer),
                             verify_inventory(truth[sample], answer))
        self.assertTrue((workspace.package_dir(job["id"])/"harness"/"systemprompt.md").is_file())
        self.assertTrue((workspace.package_dir(job["id"])/"spec.json").is_file())
        for path in (run_dir/"dataset"/"worker_inputs").rglob("*"):
            self.assertNotEqual(path.name, "truth.json")
        for path in (run_dir/"outputs"/"sources").rglob("*"):
            if path.is_file():
                self.assertNotIn('"request_type"', path.read_text(encoding="utf-8", errors="replace"))

    def test_a_sample_with_no_reference_run_is_skipped_not_fatal(self):
        p = self.approve(self.inventory())
        job = self.app.start(p["id"], launch=False)
        workspace = self.app.workspace(p["id"])
        from lx.worker import execute
        snapshot = workspace.run_dir(job["id"])/"snapshot"
        execute(self.app, workspace, job["id"], snapshot)
        run_dir = workspace.run_dir(job["id"])
        summary = next(run_dir.glob("outputs/runs/*agg1/sample-03/summary.json"))
        data = json.loads(summary.read_text(encoding="utf-8"))
        summary.write_text(json.dumps({**data, "base": [{"label": "base1", "run_dir": "gone/missing"}], "selections": {},
                                       "status": "base-failed"}), encoding="utf-8")
        from lx.taskrun import regrade
        result = regrade(self.app, workspace, workspace.run(job["id"]), live=False)
        self.assertEqual(result["skipped"], {"sample-03": "base-failed"})
        self.assertEqual(len(result["scores"]["base"]), 5)

    def test_a_changed_check_file_voids_the_spec(self):
        p = self.approve(self.inventory())
        check = Path(p["path"])/".lx"/"checks"/"v1"/"check.py"
        check.write_text(check.read_text(encoding="utf-8")+"\n# edited\n", encoding="utf-8")
        job = self.app.start(p["id"], launch=False)
        from lx.worker import execute
        workspace = self.app.workspace(p["id"])
        with self.assertRaisesRegex(ValueError, "scoring check changed"):
            execute(self.app, workspace, job["id"], workspace.run_dir(job["id"])/"snapshot")
        with self.assertRaisesRegex(ValueError, "scoring check changed"):
            self.app.test_check(p["id"])

    def test_an_orchestrator_task_and_check_are_tested_then_approved_only_by_the_human_step(self):
        p = self.inventory()
        samples = Path(p["path"])/"samples"
        for index, folder in enumerate(sorted(samples.iterdir()), 1):
            (folder/"truth.json").write_text(json.dumps({"total": index}), encoding="utf-8")
        task = {**tasks.inventory_task(), "name": "photo_total", "truth_schema": "free", "contract": "Return {\"total\": n}."}
        p = self.app.set_task(p["id"], task, CUSTOM_CHECK)
        self.assertEqual(p["draft"]["version"], 2)
        self.assertIsNone(p["approved"])
        self.assertEqual(self.app.test_check(p["id"])["samples"], 6)
        self.assertEqual(self.approve(p)["approved"]["self_test"]["checks_per_sample"], 2)
        broken = self.app.set_task(p["id"], task, CUSTOM_CHECK.replace('right = shape and answer["total"] == item["truth"]["total"]', "right = shape"))
        self.assertIsNone(broken["approved"])
        with self.assertRaisesRegex(ValueError, "cannot tell them apart"):
            self.approve(broken)
        self.assertIsNone(self.app.project(p["id"])["approved"])

    def test_a_live_task_run_spends_from_the_workspace_with_tools_on_the_ledger(self):
        from lx.inventory import FakeShapeSegmentation
        from lx.inventory_workflow import InventoryPreviewTransport
        from lx.perception import FakeTranscription
        from lx.worker import execute
        p = self.inventory()
        samples = Path(p["path"])/"samples"
        scripts = {}
        for index, folder in enumerate(sorted(samples.iterdir()), 1):
            audio = _tone(index)
            (folder/"request.wav").write_bytes(audio)
            scripts[media_hash(audio)] = (folder/"request.txt").read_text(encoding="utf-8").strip()
        transport = InventoryPreviewTransport()
        self.app._live_transport_factory = lambda hint=None: transport
        self.app._live_perception_factory = lambda: (FakeShapeSegmentation(tasks.preview_scenes(6)), FakeTranscription(scripts))
        entry = self.entry("glimmer")
        p = self.app.edit_spec(p["id"], {"goal": "g", "budget_usd": 5, "mode": "live", "roles": {r: entry for r in ("orchestrator", "improver", "worker")}})
        with self.assertRaisesRegex(ValueError, "Accept the notice"):
            self.approve(p)
        p = self.approve(p, notice_accepted=True)
        self.assertEqual(sorted(p["notice_accepted"]), ["api.meta.ai", "openrouter.ai"])
        job = self.app.start(p["id"], launch=False)
        workspace = self.app.workspace(p["id"])
        result = execute(self.app, workspace, job["id"], workspace.run_dir(job["id"])/"snapshot")
        self.assertTrue(result["live"])
        usage = self.app.spend(p["id"])
        self.assertGreater(usage["per_role"]["tool"]["settled_calls"], 0)
        self.assertGreater(usage["per_role"]["worker"]["settled_calls"], 0)
        self.assertEqual(workspace.preview_ledger.summary()["settled_calls"], 0)


class NoticeAndPreviewTests(Base):
    def test_the_notice_says_who_gets_what_and_a_new_host_asks_again(self):
        first = self.entry("first")
        p = self.app.create_project({"source": "demo", "path": self.folder()})
        roles = {r: first for r in ("orchestrator", "improver", "worker")}
        p = self.app.edit_spec(p["id"], {"goal": "g", "budget_usd": 1, "mode": "live", "roles": roles})
        self.assertEqual({n["host"] for n in p["notice"]}, {"openrouter.ai"})
        self.assertIn("images", next(n for n in p["notice"] if n["who"] == "worker")["gets"])
        self.approve(p, notice_accepted=True)
        p = self.app.edit_spec(p["id"], {"goal": "h", "budget_usd": 1})
        self.approve(p)  # same host: not asked twice
        other = self.entry("second", "https://integrate.api.nvidia.com/v1")
        p = self.app.edit_spec(p["id"], {"goal": "i", "budget_usd": 1, "roles": {**roles, "worker": other}})
        with self.assertRaisesRegex(ValueError, "Accept the notice"):
            self.approve(p)

    def test_a_preview_needs_no_notice(self):
        p = self.app.create_project({"source": "demo", "path": self.folder()})
        self.assertEqual(self.approve(p)["approved"]["version"], 1)

    @unittest.skipUnless(sandbox.available(), "needs the script sandbox")
    def test_a_preview_refuses_samples_its_doubles_do_not_know(self):
        from PIL import Image
        p = self.inventory()
        photo = Path(p["path"])/"samples"/"sample-01"/"photos"/"1.png"
        Image.new("RGB", (64, 64), (1, 2, 3)).save(photo)
        with self.assertRaisesRegex(ValueError, "synthetic demo samples"):
            self.approve(p)

    def test_a_live_task_needs_recorded_audio(self):
        entry = self.entry("glimmer")
        p = self.inventory()
        p = self.app.edit_spec(p["id"], {"goal": "g", "budget_usd": 1, "mode": "live", "roles": {r: entry for r in ("orchestrator", "improver", "worker")}})
        with self.assertRaisesRegex(ValueError, "request.wav"):
            self.approve(p, notice_accepted=True)


if __name__ == "__main__":
    unittest.main()
