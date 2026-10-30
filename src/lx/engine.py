"""The engine API: one service layer for the GUI and, later, the CLI and MCP server.

Every project is a workspace folder (lx.workspace). The engine itself is
user-level: it remembers which folders are open and holds the model registry,
the demo truth, the approval records and each workspace's real-spend ledger.
Those live here, not in the folder, so an agent working in a workspace can
neither approve a run nor reset what it has spent.

Any chat provider entry can fill any role, and the spec binds each role to that
entry's endpoint and prices. Specs default to controlled preview. A spec saved
with mode "live" makes real calls only after versioned spec, data and budget
approval, under the workspace's own cap. The shared $20 pilot ledger
(live_ledger) stays for our experiments only.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

from .budget import BudgetLedger
from .domain import build_demo_dataset, verify_output, _Rectangle, _write_png
from .intake import IntakeError, inspect_folder, snapshot_folder, snapshot_zip
from .keychain import SystemKeychain
from .live_transport import DISCUSSION_CALL_LIMIT, RUN_CALL_LIMIT, LiveMetaTransport
from .registry import Registry
from .spark import SparkClient
from .store import Store
from .usage import BudgetScope, usage_rows, summarize
from .workflow import ImagesAdapter, PreviewTransport, samples_from, sample_path
from .workspace import Workspace, native_path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = Path.home()/".local"/"state"/"lx-harness"/hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
SAMPLES = "samples"
ROLES = ("orchestrator","improver","worker")
DEFAULT_ROLE_ENTRY = "spark-contributor"
DEFAULT_WORKSPACE_BUDGET = 5


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


class Engine:
    def __init__(self, root=DEFAULT_ROOT, keychain=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.root/"app.sqlite3")
        secret_root = (Path.home()/".local"/"share"/"lx-harness"/fingerprint(str(ROOT))[:16]/"secrets"
                       if self.root == DEFAULT_ROOT.resolve() and os.name == "posix" else self.root/"secrets")
        self.registry = Registry(self.root/"app.sqlite3", secret_root, keychain or SystemKeychain())
        self.demo = build_demo_dataset(self.root/"demo")
        # Real pilot spend is never reset by creating the engine or previews.
        self.live_ledger = BudgetLedger(ROOT/".lx"/"live-spike"/"pilot-budget.sqlite3") if self.root == DEFAULT_ROOT.resolve() else None
        self.live_enabled = self.root == DEFAULT_ROOT.resolve()  # a scratch root never makes real calls
        self._live_transport_factory = None

    def make_live_transport(self, call_limit=RUN_CALL_LIMIT, truth_hint=None):
        if self._live_transport_factory is not None:
            return self._live_transport_factory(truth_hint)
        return LiveMetaTransport(lambda: self.registry.credential("spark-contributor"), call_limit=call_limit)

    def _transport(self, entry, profile, call_limit, truth):
        if self._live_transport_factory is not None:
            return self._live_transport_factory(truth)
        if not self.live_enabled:
            raise ValueError("Live runs are unavailable here; use the local app workspace")
        if entry == DEFAULT_ROLE_ENTRY:
            return self.make_live_transport(call_limit, truth)
        keyed = any(m["id"] == entry and m["has_key"] for m in self.registry.list_models())
        return LiveMetaTransport((lambda: self.registry.credential(entry)) if keyed else None,
                                 call_limit=call_limit, profile=profile)

    def build_clients(self, workspace, spec, scope_id, cap_usd, *, call_limit=RUN_CALL_LIMIT, truth=None):
        """One client per role. Live spends from the workspace ledger; preview never does.

        Roles sharing an entry share a transport, so its call limit covers the run."""
        mode = spec.get("mode")
        if mode not in {"live","controlled_preview"}:
            raise ValueError("Unknown run mode")
        truth = truth if truth is not None else self.demo.controller_truth
        ledger = self._ledger(workspace) if mode == "live" else workspace.preview_ledger
        transports, clients = {}, {}
        for role in ROLES:
            entry = spec["roles"][role]
            profile = self.registry.profile(entry)
            if entry not in transports:
                transports[entry] = self._transport(entry, profile, call_limit, truth) if mode == "live" else PreviewTransport(truth)
            clients[role] = SparkClient(transports[entry], BudgetScope(ledger, scope_id, cap_usd), profile=profile)
        return clients

    def _ledger(self, workspace):
        identifier = workspace.project()["id"]
        return BudgetLedger(self.root/"ledgers"/f"{identifier}.sqlite3", self._workspace_budget(identifier), 0, adopt_cap=True)

    def _workspace_budget(self, identifier):
        try:
            return self.store.get("budget", identifier)["cap_usd"]
        except ValueError:
            return DEFAULT_WORKSPACE_BUDGET

    def set_budget(self, identifier, cap_usd):
        """The most this workspace may spend in total, across runs. Recorded spend stays."""
        self.workspace(identifier)
        if isinstance(cap_usd, bool) or not isinstance(cap_usd, (int,float)) or not math.isfinite(cap_usd) or not 0.11 <= cap_usd <= 20:
            raise ValueError("A workspace budget must be between $0.11 and $20")
        self.store.save("budget", {"id":identifier,"cap_usd":cap_usd})
        return self.project(identifier)

    def _provider(self, entry):
        """What an approval binds for a role: where its data goes and the price. Never the key."""
        model = next((m for m in self.registry.list_models() if m["id"] == entry), None)
        if model is None or not model["role_ready"]:
            raise ValueError("Each role needs a chat provider entry with an endpoint")
        return {key:model[key] for key in ("id","name","model_id","base_url","pricing","context_limit","accepts_images","supports_tools","priced")}

    def _assign(self, roles, mode):
        unknown = set(roles) - set(ROLES)
        if unknown or set(roles) != set(ROLES):
            raise ValueError("Assign a provider to the orchestrator, improver and worker")
        providers = {role:self._provider(roles[role]) for role in ROLES}
        if not providers["improver"]["supports_tools"]:
            raise ValueError("The improver's provider must support tool calls")
        if not providers["worker"]["accepts_images"]:
            raise ValueError("The worker's provider must accept images for this workflow")
        if mode == "live" and not all(p["priced"] for p in providers.values()):
            raise ValueError("A live run needs input and output prices on every provider, so its budget can be enforced; use 0 for a free one")
        return providers

    def _check_providers(self, spec):
        current = {role:self._provider(spec["roles"][role]) for role in ROLES}
        if spec.get("providers") != current:
            raise ValueError("A provider changed since approval; approve again")

    # Workspaces

    def workspace(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Invalid project identity")
        workspace = Workspace.open(self.store.get("workspace", identifier)["path"])
        if workspace.project()["id"] != identifier:
            raise ValueError("That folder now holds a different project")
        return workspace

    def _remember(self, workspace):
        self.store.save("workspace", {"id": workspace.project()["id"], "path": str(workspace.folder)})
        return self._view(workspace)

    def _view(self, workspace):
        record = workspace.project()
        return {**record, "path": str(workspace.folder), "draft": workspace.spec(record["spec_version"]),
                "spec_versions": workspace.spec_versions(), "approved": self._approval(record["id"]),
                "budget": {key:value for key,value in self._ledger(workspace).summary().items()
                           if key in {"cap_usd","spent_usd","reserved_usd","remaining_usd"}}}

    def project(self, identifier):
        return self._view(self.workspace(identifier))

    def open_workspace(self, path):
        return self._remember(Workspace.open(native_path(path)))

    def create_project(self, data, zip_data=None):
        if data.get("synthetic_public") is not True:
            raise ValueError("Confirm that samples are synthetic or public")
        folder = native_path(data.get("path"))
        if not folder.is_dir():
            raise ValueError("The workspace folder does not exist")
        if (folder/".lx").exists():
            raise ValueError("This folder is already a workspace; open it instead")
        source = data.get("source", "folder")
        if source == "demo":
            snapshot_folder(self.demo.root/"worker_inputs", folder/SAMPLES)
            samples = SAMPLES
        elif source == "zip" and zip_data is not None:
            snapshot_zip(zip_data, folder/SAMPLES)
            samples = SAMPLES
        elif source == "folder":
            samples = data.get("samples_dir") or (SAMPLES if (folder/SAMPLES).is_dir() else ".")
        else:
            raise ValueError("Choose demo data, this folder, or data.zip")
        location = folder if samples == "." else folder.joinpath(*PurePosixPath(samples).parts)
        try:
            inventory = inspect_folder(location, skip_root=(".lx",))
        except IntakeError as exc:
            raise ValueError(f"{exc}. Put your samples in a '{SAMPLES}' folder inside the workspace") from exc
        workspace = Workspace.create(folder, name=str(data.get("name") or folder.name or "Workspace")[:120],
            goal=data.get("goal") or "Find colored shapes, give pixel boxes, and preserve each transcript.",
            samples_dir=samples, inventory=inventory, compatible_demo=self._matches_demo(location))
        with workspace.lock():
            self._draft(workspace, workspace.project())
        return self._remember(workspace)

    def _matches_demo(self, location):
        try:
            input_root, samples = samples_from(location)
            expected = {s["sample_id"]:s for s in self.demo.worker_inputs}
            return len(samples) == len(expected) and all(
                s == expected.get(s["sample_id"]) and sample_path(input_root,s["image"]).read_bytes() ==
                sample_path(self.demo.root/"worker_inputs",s["image"]).read_bytes() for s in samples)
        except (ValueError, KeyError, OSError):
            return False

    def _approval(self, identifier):
        try:
            return self.store.get("approval", identifier)
        except ValueError:
            return None

    def _draft(self, workspace, record, goal=None, budget=1, mode=None, roles=None):
        """Write the next spec version; any new version clears approval."""
        if isinstance(budget, bool) or not isinstance(budget, (int,float)) or not math.isfinite(budget) or not 0.11 <= budget <= 20:
            raise ValueError("Budget must be between $0.11 and $20")
        previous = workspace.spec(record["spec_version"]) if record["spec_version"] else {}
        if mode is None:
            mode = previous.get("mode", "controlled_preview")
        if mode not in {"controlled_preview", "live"}:
            raise ValueError("Choose controlled preview or a live run")
        roles = roles if roles is not None else previous.get("roles", {r:DEFAULT_ROLE_ENTRY for r in ROLES})
        if not isinstance(roles, dict):
            raise ValueError("Assign a provider to the orchestrator, improver and worker")
        providers = self._assign(roles, mode)
        goal = goal if goal is not None else record["goal"]
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 4000:
            raise ValueError("Enter a goal of at most 4000 characters")
        versions = workspace.spec_versions()
        spec = {"version": (versions[-1] if versions else 0)+1, "goal": goal.strip(), "budget_usd": budget,
                "roles": {r:roles[r] for r in ROLES}, "providers": providers,
                "domain": "images-and-supplied-transcripts", "output": {"objects": [{"label":"color_shape", "box":["x1","y1","x2","y2"]}], "text":"exact supplied transcript"},
                "box_edge_tolerance_px": 1, "test_data": "synthetic/public only", "mode": mode}
        spec["estimated_cost_usd"] = ({"basis":"Live cost reserved before each call under the workspace budget; actual cost from reported usage at the entry's prices, unknown until reported"}
            if mode == "live" else {"orchestrator":0.00002,"improver":0.0003,"worker":0.0003,"basis":"Approximate controlled preview only; live cost unknown"})
        workspace.add_spec(spec)
        record.update(goal=goal.strip(), spec_version=spec["version"])
        workspace.write_project(record)
        self._drop_approval(workspace, record["id"])

    def _drop_approval(self, workspace, identifier):
        approval = self._approval(identifier)
        if approval:
            self.store.delete("approval", identifier)
            shutil.rmtree(workspace.snapshot_dir(approval["snapshot"]), ignore_errors=True)

    # Discussion, spec and approval

    def discuss(self, identifier, text, consent, mode=None):
        if consent is not True:
            raise ValueError("Approve the discussion data disclosure first")
        if not isinstance(text,str) or not text.strip() or len(text)>4000:
            raise ValueError("Enter a short requirements message")
        workspace = self.workspace(identifier)
        project = self._view(workspace)
        mode = mode or project["draft"].get("mode", "controlled_preview")
        client = self.build_clients(workspace, {**project["draft"],"mode":mode}, "discussion-"+identifier, project["draft"]["budget_usd"],
                                    call_limit=DISCUSSION_CALL_LIMIT if mode == "live" else RUN_CALL_LIMIT)["orchestrator"]
        history = [{"role":"developer","content":"LX_DISCUSSION\nUse inventory metadata first; ask only consequential questions."},
            {"role":"user","content":json.dumps({"goal":project["goal"],"inventory":project["inventory"],"message":text})}]
        reply,_ = client.chat(history, role="orchestrator",phase="discussion",candidate=identifier)
        with workspace.lock():
            record = workspace.project()
            record["messages"].extend([{"role":"user","content":text},{"role":"assistant","content":reply["content"]}])
            workspace.write_project(record)
        return self._view(workspace)

    def edit_spec(self, identifier, data):
        workspace = self.workspace(identifier)
        self._jobs(workspace)  # reconcile first: the lock below is not re-entrant
        with workspace.lock():
            if any(j["status"] in {"queued","running"} for j in workspace.runs()):
                raise ValueError("Stop the active run before editing its spec")
            self._draft(workspace, workspace.project(), data.get("goal"), data.get("budget_usd",1), data.get("mode"), data.get("roles"))
        return self._view(workspace)

    def approve(self, identifier, data):
        """A human step. Freezes the samples as they are now, whatever was seen earlier."""
        workspace = self.workspace(identifier)
        with workspace.lock():
            record = workspace.project()
            draft = workspace.spec(record["spec_version"])
            if data.get("version") != draft["version"] or data.get("data_approved") is not True:
                raise ValueError("Approve the current spec and synthetic data")
            if DEFAULT_ROLE_ENTRY in draft["roles"].values() and data.get("contributor_accepted") is not True:
                raise ValueError("Accept the Contributor disclosure for Spark")
            self._check_providers(draft)
            if draft["mode"] == "live" and draft["budget_usd"] > self._workspace_budget(identifier):
                raise ValueError("This run's budget is more than the workspace budget")
            record.update(inventory=workspace.inspect_samples(), compatible_demo=self._matches_demo(workspace.samples_path))
            workspace.write_project(record)
            if not record["compatible_demo"]:
                raise ValueError("This first workflow requires the constructed demo manifest and images; other inputs can be inventoried and discussed")
            self._drop_approval(workspace, identifier)
            name = uuid.uuid4().hex
            captured = snapshot_folder(workspace.samples_path, workspace.snapshot_dir(name), skip_root=(".lx",))
            self.store.save("approval", {"id":identifier,"version":draft["version"],"spec_hash":fingerprint(draft),
                "data_hash":fingerprint(captured["inventory"]),"inventory":captured["inventory"],"snapshot":name})
        return self._view(workspace)

    # Runs

    def start(self, identifier, launch=True):
        workspace = self.workspace(identifier)
        self._jobs(workspace)
        with workspace.lock():
            record = workspace.project()
            draft = workspace.spec(record["spec_version"])
            approved = self._approval(identifier)
            if not approved or approved["spec_hash"] != fingerprint(draft):
                raise ValueError("Approve the current spec before starting")
            self._check_providers(draft)
            if any(j["status"] in {"queued","running"} for j in workspace.runs()):
                raise ValueError("One run is already active")
            job_id = uuid.uuid4().hex[:12]
            inputs = workspace.run_dir(job_id)/"snapshot"
            captured = snapshot_folder(workspace.snapshot_dir(approved["snapshot"]),inputs)
            if captured["inventory"] != approved["inventory"]:
                shutil.rmtree(workspace.run_dir(job_id))
                raise ValueError("Snapshot changed since approval; approve again")
            job = {"id":job_id,"project_id":identifier,"name":record["name"],"status":"queued","phase":"queued","created_at":time.time(),
                   "mode":draft.get("mode","controlled_preview"),"spec":draft,"approved":_approval_identity(approved),"launch_pending":True}
            workspace.write_run(job)
        if launch:
            self._launch(workspace, job_id)
        return job

    def verify_job_approval(self,workspace,job,snapshot):
        approved = self._approval(job["project_id"])
        if (not approved or _approval_identity(approved) != job["approved"] or fingerprint(job["spec"]) != approved["spec_hash"]
            or fingerprint(workspace.spec(job["spec"]["version"])) != approved["spec_hash"]):
            raise ValueError("Approval changed before execution; approve a new run")
        self._check_providers(job["spec"])
        for item in approved["inventory"]:
            target = Path(snapshot)
            for part in PureSafe(item["path"]):
                target = target/part
                if target.is_symlink():
                    raise ValueError("Linked snapshot rejected before execution")
            if not target.is_file() or target.stat().st_size != item["size"] or hashlib.sha256(target.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("Frozen snapshot changed before execution")

    def _launch(self, workspace, identifier):
        # Worker main owns POSIX signal handlers; HTTP request threads do not.
        # Windows: UTF-8 mode, because the vendored engine reads text with the default encoding.
        detach = ({"creationflags":subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW,
                   "env":{**os.environ,"PYTHONUTF8":"1"}} if os.name == "nt" else {"start_new_session":True})
        with open(os.devnull,"wb") as output:
            subprocess.Popen([sys.executable,"-m","lx.worker","--root",str(self.root),"--workspace",str(workspace.folder),"--job",identifier],
                             stdout=output,stderr=output,**detach)

    def stop(self, identifier, job_id):
        from h2portable.jobs import stop_job
        workspace = self.workspace(identifier)
        job = workspace.run(job_id)
        try:
            state = stop_job(job_id,workspace.home/"core-jobs")
        except ValueError:
            if job["status"] != "queued":
                raise
            state = {"status":"cancelled"}
        return workspace.update_run(job_id,status=state["status"],phase="stopped",launch_pending=False)

    def resume(self, identifier, job_id):
        workspace = self.workspace(identifier)
        self._jobs(workspace)
        with workspace.lock():
            if any(j["id"] != job_id and j["status"] in {"queued","running"} for j in workspace.runs()):
                raise ValueError("Another run is already active")
            job = workspace.run(job_id)
            if job["status"] in {"queued","running","completed"}:
                raise ValueError("Only interrupted, stopped or failed jobs can resume")
            approved = self._approval(identifier)
            if not approved or _approval_identity(approved) != job["approved"]:
                raise ValueError("Spec changed; approve and start a new run")
            job.update(status="queued",phase="resuming",launch_pending=True)
            job.pop("error",None)
            workspace.write_run(job)
        self._launch(workspace, job_id)
        return job

    def _jobs(self, workspace):
        """Run records with usage attached, reconciled against the core's job state."""
        from h2portable.jobs import read_job
        jobs = workspace.runs()
        for job in jobs:
            live = job.get("spec",{}).get("mode") == "live"
            job["usage"] = summarize(usage_rows(self._ledger(workspace) if live else workspace.preview_ledger,job["id"]+"/"))
            if not job.get("launch_pending") and job["status"] in {"queued", "running", "interrupted", "cancelled", "failed"}:
                try:
                    core = read_job(job["id"], workspace.home/"core-jobs")
                    job["status"] = core["status"]
                    if job["status"] in {"completed","cancelled","failed","interrupted","timed_out"}:
                        changes = {"phase":"complete"} if job["status"] == "completed" else {}
                        job.update(workspace.update_run(job["id"],status=job["status"],**changes))
                except ValueError:
                    pass
            result_file = workspace.package_dir(job["id"])/"result.json"
            if job["status"] == "completed" and result_file.is_file():
                job["result"] = json.loads(result_file.read_text(encoding="utf-8"))
        return jobs

    def state(self):
        projects, jobs, rows, spent, missing = [], [], [], [], []
        for entry in self.store.list("workspace"):
            try:
                workspace = self.workspace(entry["id"])
            except ValueError:
                missing.append(entry["path"])
                continue
            projects.append(self._view(workspace))
            jobs.extend(self._jobs(workspace))
            rows.extend(usage_rows(workspace.preview_ledger))
            spent.extend(usage_rows(self._ledger(workspace)))
        return {"mode": "controlled_preview", "live_application_enabled": True, "live_flow": "approval_gated_any_provider",
                "models": self.registry.list_models(), "projects": projects, "missing_workspaces": missing,
                "jobs": jobs, "preview_usage": summarize(rows),
                "usage_events": (rows + spent)[-80:],
                "live_usage": self.live_ledger.summary() if self.live_ledger else None}

    # Packages

    def package_bytes(self, identifier, job_id):
        workspace = self.workspace(identifier)
        job = workspace.run(job_id)
        if job["status"] != "completed":
            raise ValueError("No completed package is available")
        folder = workspace.package_dir(job_id)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream,"w",zipfile.ZIP_DEFLATED) as archive:
            for path in folder.rglob("*"):
                if path.is_file() and not path.is_symlink() and path.suffix in {".md",".json"}:
                    archive.write(path,path.relative_to(folder).as_posix())
        return stream.getvalue()

    def fresh(self, identifier, job_id):
        workspace = self.workspace(identifier)
        job = workspace.run(job_id)
        if job["status"] != "completed":
            raise ValueError("Complete the run before reusing its harness")
        root = workspace.run_dir(job_id)/"fresh"/uuid.uuid4().hex[:12]
        root.mkdir(parents=True)
        offset = int(root.name[:2],16)%10
        rectangles = (_Rectangle("red_square",(5+offset,9,25+offset,29),(220,45,45)),
                      _Rectangle("blue_rectangle",(49,32,85,55),(45,90,220)))
        _write_png(root/"fresh.png",rectangles)
        sample = {"sample_id":"fresh-scene","source_scene_id":root.name,"split":"heldout","image":"fresh.png",
                  "transcript":"A red square is above a blue rectangle."}
        (root/"manifest.json").write_text(json.dumps({"samples":[sample]}),encoding="utf-8")
        truth = {"fresh-scene":{"objects":[{"label":r.label,"box":list(r.box)} for r in rectangles],"text":sample["transcript"]}}
        mode = job["spec"].get("mode","controlled_preview")
        client = self.build_clients(workspace, job["spec"], "reuse-"+job_id, job["spec"]["budget_usd"], truth=truth)["worker"]
        adapter = ImagesAdapter(root,root/"execution",client,mode=mode)
        run = adapter.execute("fresh-scene",workspace.package_dir(job_id)/"harness","fresh")
        answer = json.loads(adapter.deliverable_text(run))
        checked = verify_output(truth["fresh-scene"],answer)
        result = {"mode":mode,"accepted":checked.accepted,"errors":checked.errors,"answer":answer,
                  "source_scene_id":sample["source_scene_id"],"note":("New constructed scene; live provider call" if mode=="live" else "New constructed scene; controlled response, no live model call")}
        workspace.update_run(job_id,fresh_result=result)
        return result


def _approval_identity(approved):
    return {key:approved[key] for key in ("version","spec_hash","data_hash")}


def PureSafe(value):
    path = PurePosixPath(value)
    if path.is_absolute() or any(p.startswith(".") for p in path.parts) or "\\" in value or ":" in value:
        raise ValueError("Invalid snapshot inventory path")
    return path.parts
