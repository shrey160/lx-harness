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

from lx.app import Application
from lx.budget import BudgetExceeded
from lx.usage import BudgetScope


@unittest.skipUnless(os.name=="posix","requires the supported WSL controller")
class ApplicationFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Application(Path(self.temp.name))

    def project(self):
        return self.app.create_project({"source":"demo","synthetic_public":True,"name":"Image workflow"})

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
        run = subprocess.run([sys.executable,"-m","lx.worker","--root",str(self.app.root),"--job",job["id"]],
                             capture_output=True,text=True,timeout=90)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        saved = self.app.store.get("job",job["id"])
        self.assertEqual(saved["status"],"completed",saved)
        self.assertFalse(saved["result"]["live"])
        self.assertTrue(all(saved["result"]["checks"]["heldout"].values()))
        with zipfile.ZipFile(io.BytesIO(self.app.package_bytes(job["id"]))) as archive:
            self.assertIn("spec.json",archive.namelist())
            self.assertIn("usage.json",archive.namelist())
            self.assertIn("harness/systemprompt.md",archive.namelist())
            self.assertFalse(any("secret" in n or ".env" in n for n in archive.namelist()))
        reuse = self.app.fresh(job["id"])
        self.assertTrue(reuse["accepted"],reuse)
        self.assertEqual(reuse["mode"],"controlled_preview")
        roles = self.app.preview_ledger.summary()["per_role"]
        self.assertGreater(roles["orchestrator"]["input_tokens"],0)
        self.assertGreater(roles["improver"]["input_tokens"],0)
        self.assertGreater(roles["worker"]["input_tokens"],0)

    def test_snapshot_change_cannot_run_after_approval(self):
        p = self.approve(self.project())
        snapshot = self.app.root/"projects"/p["id"]/"snapshot"
        (snapshot/"manifest.json").write_text("changed")
        with self.assertRaisesRegex(ValueError,"Snapshot changed"):
            self.app.start(p["id"],launch=False)

    def test_per_run_reservations_are_atomic_across_connections(self):
        a = BudgetScope(self.app.preview_ledger,"job-test",1)
        b = BudgetScope(self.app.preview_ledger,"job-test",1)
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
        snapshot = self.app.root/"projects"/p["id"]/"snapshot"
        client = SparkClient(NoGain(self.app.demo.controller_truth),BudgetScope(self.app.preview_ledger,"negative",1))
        report = optimize_preview(snapshot,self.app.root/"negative",client,self.app.demo.controller_truth,p["draft"])
        self.assertFalse(report["improved"])
        self.assertEqual(report["retained"],"base")
        self.assertFalse(any(report["checks"]["heldout"].values()))
        self.assertIn("LX_BASE",(self.app.root/"negative"/"package"/"harness"/"systemprompt.md").read_text())

    def test_unapproved_or_nonpublic_data_cannot_start(self):
        with self.assertRaises(ValueError): self.app.create_project({"source":"demo"})
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
        snapshot = self.app.root/"inputs"/job["id"]
        (snapshot/"manifest.json").write_text("changed")
        with patch("lx.worker.SparkClient") as client:
            with self.assertRaisesRegex(ValueError,"Frozen snapshot changed"):
                execute(self.app,job["id"],snapshot)
        client.assert_not_called()

    def test_resume_pending_does_not_restore_old_cancelled_state(self):
        from unittest.mock import patch
        p = self.approve(self.project())
        job = self.app.start(p["id"],launch=False)
        job.update(status="cancelled",launch_pending=False)
        self.app.store.save("job",job)
        with patch.object(self.app,"_launch"):
            self.app.resume(job["id"])
        with patch("h2portable.jobs.read_job",return_value={"status":"cancelled"}) as observed:
            current = self.app.state()["jobs"][0]
        self.assertEqual(current["status"],"queued")
        self.assertTrue(current["launch_pending"])
        observed.assert_not_called()
