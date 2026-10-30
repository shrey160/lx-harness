from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from lx.engine import Engine
from lx.budget import BudgetExceeded
from lx.usage import BudgetScope


@unittest.skipUnless(os.name in ("posix","nt"),"requires a WSL, Linux or Windows controller")
class ApplicationFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Engine(Path(self.temp.name)/"state")

    def folder(self):
        return tempfile.mkdtemp(dir=self.temp.name)

    def project(self):
        return self.app.create_project({"source":"demo","synthetic_public":True,"name":"Image workflow","path":self.folder()})

    def workspace(self,project):
        return self.app.workspace(project["id"])

    def approve(self,project):
        return self.app.approve(project["id"],{"version":project["draft"]["version"],"data_approved":True,"contributor_accepted":True})

    def test_spec_change_invalidates_approval_and_catalog_cannot_be_assigned(self):
        p = self.approve(self.project())
        p = self.app.edit_spec(p["id"],{"goal":"Preserve exact text and all boxes","budget_usd":1})
        self.assertIsNone(p["approved"])
        self.assertEqual(p["draft"]["version"],2)
        with self.assertRaises(ValueError): self.app.start(p["id"],launch=False)
        with self.assertRaises(ValueError): self.app.edit_spec(p["id"],{"model":"sam-catalog"})

    def test_real_managed_core_job_package_and_fresh_reuse(self):
        project = self.project()
        project = self.app.discuss(project["id"],"Preserve exact transcript and boxes",True)
        self.assertEqual(len(project["messages"]),2)
        project = self.approve(project)
        job = self.app.start(project["id"],launch=False)
        run = subprocess.run([sys.executable,"-m","lx.worker","--root",str(self.app.root),"--workspace",project["path"],"--job",job["id"]],
                             capture_output=True,text=True,timeout=90)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        workspace = self.workspace(project)
        saved = workspace.run(job["id"])
        self.assertEqual(saved["status"],"completed",saved)
        self.assertFalse(saved["result"]["live"])
        self.assertTrue(all(saved["result"]["checks"]["heldout"].values()))
        with zipfile.ZipFile(io.BytesIO(self.app.package_bytes(project["id"],job["id"]))) as archive:
            self.assertIn("spec.json",archive.namelist())
            self.assertIn("usage.json",archive.namelist())
            self.assertIn("harness/systemprompt.md",archive.namelist())
            self.assertFalse(any("secret" in n or ".env" in n for n in archive.namelist()))
        reuse = self.app.fresh(project["id"],job["id"])
        self.assertTrue(reuse["accepted"],reuse)
        self.assertEqual(reuse["mode"],"controlled_preview")
        self.assertTrue((workspace.package_dir(job["id"])/"harness"/"systemprompt.md").is_file())
        self.assertTrue((workspace.run_dir(job["id"])/"events.jsonl").is_file())
        state = self.app.state()
        self.assertEqual(state["jobs"][0]["status"],"completed")
        self.assertIn("checks",state["jobs"][0]["result"])
        roles = workspace.preview_ledger.summary()["per_role"]
        self.assertGreater(roles["orchestrator"]["input_tokens"],0)
        self.assertGreater(roles["improver"]["input_tokens"],0)
        self.assertGreater(roles["worker"]["input_tokens"],0)

    def test_snapshot_change_cannot_run_after_approval(self):
        p = self.approve(self.project())
        snapshot = self.workspace(p).snapshot_dir(p["approved"]["snapshot"])
        (snapshot/"manifest.json").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"Snapshot changed"):
            self.app.start(p["id"],launch=False)

    def test_per_run_reservations_are_atomic_across_connections(self):
        ledger = self.workspace(self.project()).preview_ledger
        a = BudgetScope(ledger,"job-test",1)
        b = BudgetScope(ledger,"job-test",1)
        def reserve(scope):
            try: return scope.reserve("0.75","worker","execute")
            except BudgetExceeded: return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve,(a,b)))
        self.assertEqual(sum(r is not None for r in results),1)

    def test_no_improvement_retains_base_and_reports_failed_heldout_checks(self):
        import copy
        from lx.workflow import PreviewTransport,optimize_preview
        from lx.spark import SparkClient
        class NoGain(PreviewTransport):
            def __call__(self,body,timeout):
                body = copy.deepcopy(body)
                if body["messages"][0]["content"].startswith("LX_WORKER"):
                    body["messages"][0]["content"] += "\nLX_BASE"
                return super().__call__(body,timeout)
        p = self.project()
        workspace = self.workspace(p)
        snapshot = workspace.samples_path
        client = SparkClient(NoGain(self.app.demo.controller_truth),BudgetScope(workspace.preview_ledger,"negative",1))
        report = optimize_preview(snapshot,self.app.root/"negative",client,self.app.demo.controller_truth,p["draft"])
        self.assertFalse(report["improved"])
        self.assertEqual(report["retained"],"base")
        self.assertFalse(any(report["checks"]["heldout"].values()))
        self.assertIn("LX_BASE",(self.app.root/"negative"/"package"/"harness"/"systemprompt.md").read_text(encoding="utf-8"))

    def test_unapproved_or_nonpublic_data_cannot_start(self):
        with self.assertRaises(ValueError): self.app.create_project({"source":"demo","path":self.folder()})
        p = self.project()
        with self.assertRaises(ValueError): self.app.start(p["id"],launch=False)
        with self.assertRaises(ValueError): self.app.approve(p["id"],{"version":p["draft"]["version"]})

    def test_active_spec_edit_and_changed_frozen_input_are_blocked(self):
        from unittest.mock import patch
        from lx.worker import execute
        p = self.approve(self.project())
        job = self.app.start(p["id"],launch=False)
        with self.assertRaisesRegex(ValueError,"Stop the active"):
            self.app.edit_spec(p["id"],{"goal":"Changed","budget_usd":1})
        workspace = self.workspace(p)
        snapshot = workspace.run_dir(job["id"])/"snapshot"
        (snapshot/"manifest.json").write_text("changed", encoding="utf-8")
        with patch.object(self.app,"build_client") as client:
            with self.assertRaisesRegex(ValueError,"Frozen snapshot changed"):
                execute(self.app,workspace,job["id"],snapshot)
        client.assert_not_called()

    def test_resume_pending_does_not_restore_old_cancelled_state(self):
        from unittest.mock import patch
        p = self.approve(self.project())
        job = self.app.start(p["id"],launch=False)
        self.workspace(p).update_run(job["id"],status="cancelled",launch_pending=False)
        with patch.object(self.app,"_launch"):
            self.app.resume(p["id"],job["id"])
        with patch("h2portable.jobs.read_job",return_value={"status":"cancelled"}) as observed:
            current = self.app.state()["jobs"][0]
        self.assertEqual(current["status"],"queued")
        self.assertTrue(current["launch_pending"])
        observed.assert_not_called()

    def test_samples_stay_in_the_folder_and_a_later_edit_does_not_reach_the_approved_run(self):
        p = self.approve(self.project())
        samples = Path(p["path"])/"samples"
        original = (samples/"manifest.json").read_bytes()
        job = self.app.start(p["id"],launch=False)
        (samples/"manifest.json").write_text("changed after approval",encoding="utf-8")
        frozen = self.workspace(p).run_dir(job["id"])/"snapshot"/"manifest.json"
        self.assertEqual(frozen.read_bytes(),original)

    def test_changed_samples_refuse_a_new_approval(self):
        p = self.project()
        (Path(p["path"])/"samples"/"manifest.json").write_text("{}",encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"requires the constructed demo"):
            self.approve(p)
        self.assertFalse(self.app.project(p["id"])["compatible_demo"])

    def test_approval_is_not_kept_in_the_folder_an_agent_can_write(self):
        p = self.project()
        workspace = self.workspace(p)
        record = workspace.project()
        record["approved"] = {"version":1,"spec_hash":"forged","data_hash":"forged"}
        workspace.write_project(record)
        self.assertIsNone(self.app.project(p["id"])["approved"])
        with self.assertRaisesRegex(ValueError,"Approve the current spec"):
            self.app.start(p["id"],launch=False)
        self.assertFalse(list(workspace.home.rglob("*approval*")))

    def test_hand_edited_spec_file_voids_the_approval(self):
        p = self.approve(self.project())
        workspace = self.workspace(p)
        path = workspace.home/"spec"/"v1.json"
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec["budget_usd"] = 20
        path.write_text(json.dumps(spec),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"Approve the current spec"):
            self.app.start(p["id"],launch=False)

    def test_folder_holding_code_needs_a_samples_folder(self):
        folder = Path(self.folder())
        (folder/".git").mkdir()
        with self.assertRaisesRegex(ValueError,"'samples' folder"):
            self.app.create_project({"source":"folder","synthetic_public":True,"path":str(folder)})
        self.assertFalse((folder/".lx").exists())
        samples = folder/"samples"
        samples.mkdir()
        (samples/"note.txt").write_text("hello",encoding="utf-8")
        project = self.app.create_project({"source":"folder","synthetic_public":True,"path":str(folder)})
        self.assertEqual(project["samples_dir"],"samples")
        self.assertEqual([i["path"] for i in project["inventory"]["inventory"]],["note.txt"])

    def test_missing_folder_is_reported_not_fatal(self):
        import shutil
        keep = self.project()
        gone = self.project()
        shutil.rmtree(gone["path"])
        state = self.app.state()
        self.assertEqual([p["id"] for p in state["projects"]],[keep["id"]])
        self.assertEqual(state["missing_workspaces"],[str(Path(gone["path"]))])

