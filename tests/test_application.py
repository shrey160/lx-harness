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


class MemoryKeychain:
    """Never the real Credential Manager in tests."""
    def __init__(self, usable=True):
        self.values, self.usable = {}, usable
    def available(self): return self.usable
    def set(self, name, value): self.values[name] = value
    def get(self, name): return self.values.get(name)
    def delete(self, name): self.values.pop(name, None)


@unittest.skipUnless(os.name in ("posix","nt"),"requires a WSL, Linux or Windows controller")
class ApplicationFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.keychain = MemoryKeychain()
        self.app = Engine(Path(self.temp.name)/"state",keychain=self.keychain)

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
        with self.assertRaises(ValueError):
            self.app.edit_spec(p["id"],{"roles":{"orchestrator":"sam-catalog","improver":"spark-contributor","worker":"spark-contributor"}})

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
        from lx.workflow import PreviewTransport,optimize_images
        from lx.spark import ChatClient
        class NoGain(PreviewTransport):
            def __call__(self,body,timeout):
                body = copy.deepcopy(body)
                if body["messages"][0]["content"].startswith("LX_WORKER"):
                    body["messages"][0]["content"] += "\nLX_BASE"
                return super().__call__(body,timeout)
        p = self.project()
        workspace = self.workspace(p)
        snapshot = workspace.samples_path
        client = ChatClient(NoGain(self.app.demo.controller_truth),BudgetScope(workspace.preview_ledger,"negative",1))
        report = optimize_images(snapshot,self.app.root/"negative",client,self.app.demo.controller_truth,p["draft"])
        self.assertFalse(report["improved"])
        self.assertEqual(report["retained"],"base")
        self.assertFalse(any(report["checks"]["heldout"].values()))
        self.assertIn("LX_BASE",(self.app.root/"negative"/"package"/"harness"/"systemprompt.md").read_text(encoding="utf-8"))

    def test_unapproved_or_nonpublic_data_cannot_start(self):
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
        with patch.object(self.app,"build_clients") as client:
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

    def entry(self,name,**fields):
        values = {"base_url":"https://openrouter.ai/api/v1","accepts_images":True,"supports_tools":True,
                  "pricing":{"input_per_million":0.0375,"output_per_million":0.15},"context_limit":32768}
        return self.app.registry.create(name,"test/"+name,["General"],**{**values,**fields})

    def three_roles(self):
        return {"orchestrator":self.entry("gilmer",api_key="dummy-key-gilmer")["id"],
                "improver":self.entry("local",base_url="http://127.0.0.1:8888/v1",pricing={"input_per_million":0,"output_per_million":0})["id"],
                "worker":self.entry("vision",api_key="dummy-key-vision")["id"]}

    def test_three_providers_in_three_roles_complete_a_run_and_keys_stay_out_of_the_folder(self):
        roles = self.three_roles()
        project = self.app.edit_spec(self.project()["id"],{"goal":"Three providers","budget_usd":1,"roles":roles})
        self.assertEqual(project["draft"]["roles"],roles)
        self.assertEqual(project["draft"]["providers"]["orchestrator"]["base_url"],"https://openrouter.ai/api/v1")
        project = self.app.approve(project["id"],{"version":project["draft"]["version"],"data_approved":True})
        job = self.app.start(project["id"],launch=False)
        run = subprocess.run([sys.executable,"-m","lx.worker","--root",str(self.app.root),"--workspace",project["path"],"--job",job["id"]],
                             capture_output=True,text=True,timeout=90)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        workspace = self.workspace(project)
        saved = workspace.run(job["id"])
        self.assertEqual(saved["status"],"completed",saved)
        self.assertTrue(all(saved["result"]["checks"]["heldout"].values()))
        usage = workspace.preview_ledger.summary()["per_role"]
        for role in roles:
            self.assertGreater(usage[role]["input_tokens"],0,role)
        self.assertEqual(usage["improver"]["spent_usd"],0)  # its entry is free
        self.assertGreater(usage["worker"]["spent_usd"],0)
        self.assertEqual(self.app.state()["jobs"][0]["status"],"completed")
        for path in [*Path(project["path"]).rglob("*"),*self.app.root.rglob("*")]:
            if path.is_file():
                self.assertNotIn(b"dummy-key-",path.read_bytes(),path)
        self.assertEqual(sorted(self.keychain.values.values()),["dummy-key-gilmer","dummy-key-vision"])

    def test_reply_length_follows_the_spec_and_a_mix_of_providers_takes_the_longest(self):
        p = self.project()
        clients = self.app.build_clients(self.workspace(p),p["draft"],"scope",1)
        self.assertEqual({client.max_tokens for client in clients.values()},{4096})
        roles = {"orchestrator":"spark-contributor","improver":self.entry("reasoning")["id"],"worker":self.entry("vision")["id"]}
        p = self.app.edit_spec(p["id"],{"goal":"g","budget_usd":1,"roles":roles})
        clients = self.app.build_clients(self.workspace(p),p["draft"],"scope",1)
        self.assertEqual({client.max_tokens for client in clients.values()},{8192})
        p = self.app.edit_spec(p["id"],{"goal":"g","budget_usd":1,"limits":{"reply_tokens":16000,"call_seconds":120}})
        clients = self.app.build_clients(self.workspace(p),p["draft"],"scope",1)
        self.assertEqual({(c.max_tokens,c.call_seconds) for c in clients.values()},{(16000,120)})

    def test_limits_are_in_the_spec_and_a_change_is_a_new_version_needing_approval(self):
        p = self.approve(self.project())
        self.assertEqual(p["draft"]["limits"]["improver_turns"],12)
        self.assertEqual(p["draft"]["limits_set"],{})
        saved = self.workspace(p).spec(p["draft"]["version"])
        self.assertEqual(saved["limits"]["run_seconds"],900)
        changed = self.app.edit_spec(p["id"],{"goal":p["draft"]["goal"],"budget_usd":1,"limits":{"improver_turns":20}})
        self.assertEqual(changed["draft"]["version"],p["draft"]["version"]+1)
        self.assertIsNone(changed["approved"])
        self.assertEqual(changed["draft"]["limits"]["improver_turns"],20)
        self.assertEqual(changed["draft"]["limits_set"],{"improver_turns":20})
        kept = self.app.edit_spec(p["id"],{"goal":"another goal","budget_usd":1})
        self.assertEqual(kept["draft"]["limits"]["improver_turns"],20)
        self.assertEqual(self.app.state()["limit_fields"][0]["name"],"agent_turns_per_photo")

    def test_a_limit_outside_its_bounds_makes_no_new_version(self):
        p = self.project()
        for bad in ({"improver_turns":0},{"nope":3},{"run_seconds":1}):
            with self.subTest(bad),self.assertRaises(ValueError):
                self.app.edit_spec(p["id"],{"goal":"g","budget_usd":1,"limits":bad})
        self.assertEqual(self.app.project(p["id"])["draft"]["version"],p["draft"]["version"])

    def test_changing_a_provider_moves_defaults_but_keeps_the_users_own_values(self):
        p = self.project()
        p = self.app.edit_spec(p["id"],{"goal":"g","budget_usd":1,"limits":{"chat_calls":150}})
        roles = {r:self.entry("new-"+r)["id"] for r in ("orchestrator","improver","worker")}
        p = self.app.edit_spec(p["id"],{"goal":"g","budget_usd":1,"roles":roles})
        self.assertEqual(p["draft"]["limits"]["improver_turns"],30)
        self.assertEqual(p["draft"]["limits"]["chat_calls"],150)
        self.assertEqual(p["limit_defaults"]["chat_calls"],200)

    def test_a_provider_entrys_limits_become_the_defaults(self):
        roles = {r:self.entry("own-"+r,limits={"improver_turns":45,"call_seconds":120})["id"] for r in ("orchestrator","improver","worker")}
        p = self.app.edit_spec(self.project()["id"],{"goal":"g","budget_usd":1,"roles":roles})
        self.assertEqual((p["draft"]["limits"]["improver_turns"],p["draft"]["limits"]["call_seconds"]),(45,120))
        self.assertEqual(p["draft"]["limits_set"],{})

    def test_a_raised_limit_reaches_a_new_run_and_not_a_resumed_one(self):
        p = self.approve(self.project())
        job = self.app.start(p["id"],launch=False)
        self.workspace(p).update_run(job["id"],status="failed",launch_pending=False)
        self.app.edit_spec(p["id"],{"goal":p["draft"]["goal"],"budget_usd":1,"limits":{"run_seconds":1800}})
        with self.assertRaisesRegex(ValueError,"approve and start a new run"):
            self.app.resume(p["id"],job["id"])

    def test_a_spec_from_before_limits_runs_on_the_defaults(self):
        p = self.project()
        draft = {k:v for k,v in self.workspace(p).spec(p["draft"]["version"]).items() if k not in {"limits","limits_set"}}
        self.assertEqual(self.app.limits_of(draft),p["draft"]["limits"])

    def test_a_run_takes_its_time_limit_from_the_spec(self):
        from unittest.mock import patch
        from lx import worker
        p = self.app.edit_spec(self.project()["id"],{"goal":"g","budget_usd":1,"limits":{"run_seconds":1234}})
        p = self.approve(p)
        job = self.app.start(p["id"],launch=False)
        argv = ["worker","--root",str(self.app.root),"--workspace",p["path"],"--job",job["id"]]
        with patch("sys.argv",argv),patch("h2portable.jobs.run_job",return_value={"status":"completed"}) as run_job:
            self.assertEqual(worker.main(),0)
        self.assertEqual(run_job.call_args.args[4],1234)

    def test_a_changed_rate_stops_the_approved_run_and_the_transport_gets_it(self):
        from unittest.mock import patch
        entry = self.entry("paced",requests_per_minute=40)["id"]
        roles = {r:entry for r in ("orchestrator","improver","worker")}
        p = self.app.edit_spec(self.project()["id"],{"goal":"g","budget_usd":1,"mode":"live","roles":roles})
        self.assertEqual(p["draft"]["providers"]["worker"]["requests_per_minute"],40)
        self.app.live_enabled = True
        with patch("lx.engine.LiveChatTransport") as transport:
            self.app.build_clients(self.workspace(p),p["draft"],"scope",1)
        self.assertEqual(transport.call_count,1)
        fields = transport.call_args.kwargs
        self.assertEqual((fields["requests_per_minute"],fields["max_call_seconds"],fields["call_limit"]),(40,300,200))
        self.app.live_enabled = False
        p = self.app.approve(p["id"],{"version":p["draft"]["version"],"data_approved":True,"notice_accepted":True})
        self.app.registry.update(entry,requests_per_minute=20)
        with self.assertRaisesRegex(ValueError,"provider changed"):
            self.app.start(p["id"],launch=False)

    def test_a_run_keeps_the_reason_it_failed_and_its_limit_follows_the_provider(self):
        from unittest.mock import patch
        from lx import worker
        roles = {r:self.entry("slow-"+r)["id"] for r in ("orchestrator","improver","worker")}
        p = self.app.edit_spec(self.project()["id"],{"goal":"g","budget_usd":1,"roles":roles})
        p = self.app.approve(p["id"],{"version":p["draft"]["version"],"data_approved":True})
        job = self.app.start(p["id"],launch=False)
        workspace = self.workspace(p)
        workspace.update_run(job["id"],error="provider did not reply in time")
        argv = ["worker","--root",str(self.app.root),"--workspace",p["path"],"--job",job["id"]]
        with patch("sys.argv",argv),patch("h2portable.jobs.run_job",return_value={"status":"failed"}) as run_job:
            self.assertEqual(worker.main(),0)
        self.assertEqual(run_job.call_args.args[4],3600)
        saved = workspace.run(job["id"])
        self.assertEqual((saved["status"],saved["error"]),("failed","provider did not reply in time"))

    def test_a_resumed_run_keeps_the_time_limit_it_started_with(self):
        from unittest.mock import patch
        from lx import worker
        roles = {r:self.entry("older-"+r)["id"] for r in ("orchestrator","improver","worker")}
        p = self.app.edit_spec(self.project()["id"],{"goal":"g","budget_usd":1,"roles":roles})
        p = self.app.approve(p["id"],{"version":p["draft"]["version"],"data_approved":True})
        job = self.app.start(p["id"],launch=False)
        argv = ["worker","--root",str(self.app.root),"--workspace",p["path"],"--job",job["id"]]
        with patch("sys.argv",argv),patch("h2portable.jobs.read_job",return_value={"spec":{"timeout":900.0}}), \
             patch("h2portable.jobs.run_job",return_value={"status":"completed"}) as run_job:
            worker.main()
        self.assertEqual(run_job.call_args.args[4],900.0)

    def test_a_provider_failure_is_reported_and_other_errors_stay_private(self):
        from unittest.mock import patch
        from lx import worker
        from lx.spark import ModelError
        p = self.approve(self.project())
        job = self.app.start(p["id"],launch=False)
        argv = ["worker","--root",str(self.app.root),"--workspace",p["path"],"--job",job["id"],"--execute"]
        with patch("sys.argv",argv),patch("lx.worker.execute",side_effect=ModelError("provider did not reply in time")):
            self.assertEqual(worker.main(),1)
        self.assertEqual(self.workspace(p).run(job["id"])["error"],"provider did not reply in time")
        with patch("sys.argv",argv),patch("lx.worker.execute",side_effect=RuntimeError("private path C:/x")):
            self.assertEqual(worker.main(),1)
        self.assertNotIn("private path",self.workspace(p).run(job["id"])["error"])

    def test_a_provider_changed_after_approval_stops_the_run(self):
        roles = self.three_roles()
        project = self.app.edit_spec(self.project()["id"],{"goal":"g","budget_usd":1,"roles":roles})
        project = self.app.approve(project["id"],{"version":project["draft"]["version"],"data_approved":True})
        self.app.registry.update(roles["worker"],base_url="https://elsewhere.example/v1")
        with self.assertRaisesRegex(ValueError,"provider changed"):
            self.app.start(project["id"],launch=False)

    def test_roles_need_the_abilities_they_use(self):
        p = self.project()
        good = self.entry("good")["id"]
        def spec(**roles):
            return self.app.edit_spec(p["id"],{"goal":"g","budget_usd":1,"roles":{"orchestrator":good,"improver":good,"worker":good,**roles}})
        with self.assertRaisesRegex(ValueError,"tool calls"):
            spec(improver=self.entry("no-tools",supports_tools=False)["id"])
        with self.assertRaisesRegex(ValueError,"accept images"):
            spec(worker=self.entry("no-images",accepts_images=False)["id"])
        with self.assertRaisesRegex(ValueError,"chat provider"):
            spec(worker=self.app.registry.create("no-url","test/no-url",["General"])["id"])
        self.assertEqual(spec()["draft"]["roles"]["worker"],good)
        with self.assertRaisesRegex(ValueError,"Assign a provider"):
            self.app.edit_spec(p["id"],{"goal":"g","budget_usd":1,"roles":{"worker":good}})

    def test_a_provider_with_no_published_price_runs_live_and_the_spec_says_what_that_means(self):
        p = self.project()
        unpriced = self.entry("unpriced",pricing=None)
        roles = {"orchestrator":unpriced["id"],"improver":unpriced["id"],"worker":unpriced["id"]}
        p = self.app.edit_spec(p["id"],{"goal":"g","budget_usd":1,"mode":"live","roles":roles})
        self.assertEqual(p["free_providers"],[unpriced["name"]])
        p = self.app.edit_spec(p["id"],{"goal":"g","budget_usd":1,"mode":"live","roles":{r:"spark-contributor" for r in roles}})
        self.assertEqual(p["free_providers"],[])  # built-in Spark is priced in code, not in its entry

    def test_contributor_disclosure_is_asked_only_when_spark_has_a_role(self):
        p = self.project()
        with self.assertRaisesRegex(ValueError,"Contributor"):
            self.app.approve(p["id"],{"version":p["draft"]["version"],"data_approved":True})

    def test_real_spend_is_per_workspace_and_kept_outside_the_folder(self):
        from lx.workflow import PreviewTransport
        self.app._live_transport_factory = lambda hint=None: PreviewTransport(hint if hint is not None else self.app.demo.controller_truth)
        roles = {r:self.entry("paid-"+r)["id"] for r in ("orchestrator","improver","worker")}
        first = self.app.edit_spec(self.project()["id"],{"goal":"g","budget_usd":1,"mode":"live","roles":roles})
        other = self.project()
        workspace = self.workspace(first)
        clients = self.app.build_clients(workspace,first["draft"],"scope",1)
        clients["worker"].chat([{"role":"developer","content":"LX_PREFLIGHT"}],role="worker",phase="preflight",candidate="one")
        self.assertGreater(self.app.spend(first["id"])["settled_calls"],0)
        self.assertEqual(self.app.spend(other["id"])["settled_calls"],0)
        self.assertFalse(list(workspace.home.rglob("*ledger*")))
        self.app.set_budget(first["id"],0.11)
        before = self.app.project(first["id"])["budget"]["spent_usd"]
        self.assertGreater(before,0)
        self.assertEqual(self.app.project(first["id"])["budget"]["cap_usd"],0.11)

    def test_a_run_budget_over_the_workspace_budget_is_refused_at_approval(self):
        roles = {r:self.entry("big-"+r)["id"] for r in ("orchestrator","improver","worker")}
        p = self.app.edit_spec(self.project()["id"],{"goal":"g","budget_usd":2,"mode":"live","roles":roles})
        self.app.set_budget(p["id"],1)
        with self.assertRaisesRegex(ValueError,"workspace budget"):
            self.app.approve(p["id"],{"version":p["draft"]["version"],"data_approved":True})

    def test_a_scratch_root_never_builds_a_real_transport(self):
        roles = {r:self.entry("scratch-"+r)["id"] for r in ("orchestrator","improver","worker")}
        p = self.app.edit_spec(self.project()["id"],{"goal":"g","budget_usd":1,"mode":"live","roles":roles})
        with self.assertRaisesRegex(ValueError,"Live runs are unavailable"):
            self.app.discuss(p["id"],"hello",True)
