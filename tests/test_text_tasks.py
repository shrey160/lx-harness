"""The text-request template: spec, samples, check, and a run with no photos, audio or tools."""
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from lx import limits, sandbox, tasks, text_task
from lx.components import COMPONENT_TOOLS
from lx.engine import Engine
from lx.live_transport import validate_body
from lx.records import verify_record
from lx.text_task import records_check_source, records_task, synthetic_samples, write_samples


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
        self.app = Engine(Path(self.temp.name)/"state", keychain=MemoryKeychain())

    def folder(self):
        return tempfile.mkdtemp(dir=self.temp.name)

    def records(self):
        return self.app.create_project({"source": "demo", "task": "records", "name": "Orders", "path": self.folder()})

    def approve(self, project):
        return self.app.approve(project["id"], {"version": project["draft"]["version"], "data_approved": True,
                                                "contributor_accepted": True})


class SpecTests(Base):
    def test_the_task_is_valid_and_no_photo_schema_is_allowed(self):
        good = records_task()
        self.assertIs(tasks.validate_task(good), good)
        with self.assertRaises(ValueError):
            tasks.validate_task({**good, "truth_schema": "inventory"})

    def test_samples_are_text_and_truth_only(self):
        project = self.records()
        folder = Path(project["path"])/"samples"
        self.assertEqual(len(tasks.sample_folders(folder)), 6)
        self.assertEqual({p.name for p in (folder/"sample-01").iterdir()}, {"request.txt", "truth.json"})
        self.assertNotIn("photo", project["goal"])

    def test_bad_samples_are_named(self):
        folder = Path(self.folder())
        write_samples(folder, 6)
        (folder/"sample-02"/"request.txt").unlink()
        with self.assertRaisesRegex(ValueError, "sample-02: missing request.txt"):
            tasks.sample_truths(folder, records_task())
        (folder/"sample-02"/"request.txt").write_text(" ", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sample-02: request.txt"):
            tasks.sample_truths(folder, records_task())
        (folder/"sample-02"/"request.txt").write_text("x", encoding="utf-8")
        (folder/"sample-03"/"truth.json").write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sample-03: truth.json"):
            tasks.sample_truths(folder, records_task())

    def test_photo_tools_are_left_out_of_the_notice_and_the_limits(self):
        project = self.records()
        self.assertEqual(project["hidden_limits"], list(limits.PHOTO_ONLY))
        notice = self.app.data_notice(project["draft"])
        self.assertEqual({row["who"] for row in notice}, {"orchestrator", "improver", "worker"})

    def test_a_worker_without_images_is_enough(self):
        entry = self.app.registry.create("Text only", "test/text", ["General"], base_url="https://openrouter.ai/api/v1",
                                         supports_tools=True, pricing={"input_per_million": 0.3, "output_per_million": 1.2},
                                         context_limit=131072)["id"]
        project = self.records()
        roles = {**project["draft"]["roles"], "worker": entry}
        edited = self.app.edit_spec(project["id"], {"goal": "g", "budget_usd": 1, "roles": roles})
        self.assertEqual(edited["draft"]["roles"]["worker"], entry)

    def test_the_live_contract_allows_the_agent_tools_and_no_photo_tools(self):
        body = {"model": "m", "tools": COMPONENT_TOOLS, "messages": [{"role": "user", "content": "x"}]}
        validate_body(body, model="m", tool_sets=(COMPONENT_TOOLS,))
        project = self.records()
        spec = project["draft"]
        clients = self.app.build_clients(self.app.workspace(project["id"]), spec, "scope", spec["budget_usd"])
        self.assertIsInstance(clients["worker"].transport, text_task.TextPreviewTransport)


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class CheckTests(unittest.TestCase):
    def test_the_check_agrees_with_the_records_verifier_on_a_correct_and_a_wrong_answer(self):
        _, truth = synthetic_samples(1)[0]
        for answer in (truth, {**truth, "currency": "XXX"}, {**truth, "phone": "bad"}, {}):
            ours = tasks.run_check(records_check_source(), truth, answer)
            self.assertEqual(ours.n_passed == ours.n_criteria, verify_record(truth, answer).accepted, answer)

    def test_an_unknown_field_fails_and_errors_hold_no_expected_value(self):
        _, truth = synthetic_samples(1)[0]
        extra = tasks.run_check(records_check_source(), truth, {**truth, "note": "x"})
        self.assertLess(extra.score, 1.0)
        wrong = tasks.run_check(records_check_source(), truth, {**truth, "customer": "Nobody"})
        self.assertNotIn(truth["customer"], " ".join(wrong.errors))


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class RunTests(Base):
    def test_a_run_goes_from_samples_to_a_package_without_photo_tools(self):
        from lx.worker import execute
        project = self.approve(self.records())
        self.assertEqual(project["approved"]["self_test"]["samples"], 6)
        job = self.app.start(project["id"], launch=False)
        workspace = self.app.workspace(project["id"])
        result = execute(self.app, workspace, job["id"], workspace.run_dir(job["id"])/"snapshot")
        self.assertEqual(result["task"], "lx_order_records")
        self.assertFalse(result["live"])
        self.assertEqual(result["means"]["harness2"], 1.0)
        self.assertLess(result["means"]["base"], result["means"]["harness2"])
        self.assertTrue(result["improved"])
        self.assertFalse(any(row["role"] == "tool" for row in self.app.spend_rows(project["id"], job["id"])))
        with zipfile.ZipFile(io.BytesIO(self.app.package_bytes(project["id"], job["id"]))) as archive:
            self.assertIn("harness/scripts/sort_tags.py", archive.namelist())
            self.assertEqual(archive.read("check.py").decode("utf-8"), records_check_source())

    def test_a_preview_refuses_samples_its_double_does_not_know(self):
        project = self.records()
        (Path(project["path"])/"samples"/"sample-01"/"request.txt").write_text("Someone else's order", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "synthetic samples"):
            self.approve(project)

    def test_a_failed_run_regrades_from_its_saved_answers(self):
        from lx.worker import execute
        project = self.approve(self.records())
        job = self.app.start(project["id"], launch=False)
        workspace = self.app.workspace(project["id"])
        first = execute(self.app, workspace, job["id"], workspace.run_dir(job["id"])/"snapshot")
        workspace.update_run(job["id"], status="failed", launch_pending=False)
        regraded = self.app.regrade(project["id"], job["id"])
        self.assertEqual(regraded["result"]["scores"], first["scores"])
