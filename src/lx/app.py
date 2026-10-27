"""Local application services. Live application egress is deliberately disabled.

The separately approved JSON live spike remains available in lx.live. This
application currently exercises its real core lifecycle using controlled preview.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import uuid
import zipfile

from .budget import BudgetLedger
from .domain import build_demo_dataset, verify_output, _Rectangle, _write_png
from .intake import snapshot_folder, snapshot_zip, windows_to_wsl
from .registry import Registry
from .spark import SparkClient
from .store import Store
from .usage import BudgetScope, usage_rows, summarize
from .workflow import ImagesAdapter, PreviewTransport, samples_from, sample_path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = Path.home()/".local"/"state"/"lx-harness"/hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


class Application:
    def __init__(self, root=DEFAULT_ROOT):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.root/"app.sqlite3")
        secret_root = (Path.home()/".local"/"share"/"lx-harness"/fingerprint(str(ROOT))[:16]/"secrets"
                       if self.root == DEFAULT_ROOT.resolve() and os.name == "posix" else self.root/"secrets")
        self.registry = Registry(self.root/"app.sqlite3", secret_root)
        self.demo = build_demo_dataset(self.root/"demo")
        # Real pilot spend is never reset by creating the application or previews.
        self.live_ledger = BudgetLedger(ROOT/".lx"/"live-spike"/"pilot-budget.sqlite3") if self.root == DEFAULT_ROOT.resolve() else None
        self.preview_ledger = BudgetLedger(self.root/"preview-usage.sqlite3", initial_spend_usd=0)
        self.lock = threading.RLock()

    def state(self):
        jobs = self.store.list("job")
        from h2portable.jobs import read_job
        for job in jobs:
            job["usage"] = summarize(usage_rows(self.preview_ledger,job["id"]+"/"))
            if not job.get("launch_pending") and job["status"] in {"queued", "running", "interrupted", "cancelled", "failed"}:
                try:
                    core = read_job(job["id"], self.root/"core-jobs")
                    job["status"] = core["status"]
                    if job["status"] in {"completed","cancelled","failed","interrupted","timed_out"}:
                        latest = self.store.get("job",job["id"])
                        latest["status"] = job["status"]
                        if job["status"] == "completed": latest["phase"] = "complete"
                        job.update(self.store.save("job",latest))
                except ValueError:
                    pass
            result_file = self.root/"outputs"/job["id"]/"package"/"result.json"
            if job["status"] == "completed" and result_file.is_file():
                job["result"] = json.loads(result_file.read_text())
        return {"mode": "controlled_preview", "live_application_enabled": False,
                "models": self.registry.list_models(), "projects": self.store.list("project"),
                "jobs": jobs, "preview_usage": self.preview_ledger.summary(),
                "usage_events": usage_rows(self.preview_ledger)[-80:],
                "live_usage": self.live_ledger.summary() if self.live_ledger else None}

    def project(self, identifier):
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Invalid project identity")
        return self.store.get("project", identifier)

    def _draft(self, project, goal=None, budget=1):
        if isinstance(budget, bool) or not isinstance(budget, (int,float)) or not math.isfinite(budget) or not 0.11 <= budget <= 20:
            raise ValueError("Budget must be between $0.11 and $20")
        goal = goal if goal is not None else project["goal"]
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 4000:
            raise ValueError("Enter a goal of at most 4000 characters")
        versions = project.setdefault("spec_versions", [])
        spec = {"version": len(versions)+1, "goal": goal.strip(), "budget_usd": budget,
                "model": "spark-contributor", "roles": {r:"spark-contributor" for r in ("orchestrator","improver","worker")},
                "domain": "images-and-supplied-transcripts", "output": {"objects": [{"label":"color_shape", "box":["x1","y1","x2","y2"]}], "text":"exact supplied transcript"},
                "box_edge_tolerance_px": 1, "test_data": "synthetic/public only", "mode": "controlled_preview"}
        spec["estimated_cost_usd"] = {"orchestrator":0.00002,"improver":0.0003,"worker":0.0003,"basis":"Approximate controlled preview only; live cost unknown"}
        versions.append(spec)
        project.update(goal=goal.strip(), draft=spec, approved=None)
        return project

    def create_project(self, data, zip_data=None):
        if data.get("synthetic_public") is not True:
            raise ValueError("Confirm that samples are synthetic or public")
        identifier = uuid.uuid4().hex
        directory = self.root/"projects"/identifier
        directory.mkdir(parents=True)
        destination = directory/"snapshot"
        source = data.get("source", "demo")
        if source == "demo":
            inventory = snapshot_folder(self.demo.root/"worker_inputs", destination)
        elif source == "folder":
            path = data.get("path", "")
            if not isinstance(path, str) or len(path)>2048:
                raise ValueError("Invalid folder path")
            inventory = snapshot_folder(Path(windows_to_wsl(path)), destination)
        elif source == "zip" and zip_data is not None:
            inventory = snapshot_zip(zip_data, destination)
        else:
            raise ValueError("Choose demo data, a folder, or data.zip")
        compatible = False
        try:
            input_root, samples = samples_from(destination)
            expected = {s["sample_id"]:s for s in self.demo.worker_inputs}
            compatible = len(samples) == len(expected) and all(
                s == expected.get(s["sample_id"]) and sample_path(input_root,s["image"]).read_bytes() ==
                sample_path(self.demo.root/"worker_inputs",s["image"]).read_bytes() for s in samples)
        except (ValueError, KeyError, OSError):
            pass
        record = {"id":identifier,"name":str(data.get("name") or "Image annotation workflow")[:120],
            "goal":data.get("goal") or "Find colored shapes, give pixel boxes, and preserve each transcript.",
            "inventory":inventory,"compatible_demo":compatible,"messages":[],"spec_versions":[],"approved":None}
        return self.store.save("project", self._draft(record))

    def discuss(self, identifier, text, consent):
        if consent is not True:
            raise ValueError("Approve the discussion data disclosure first")
        if not isinstance(text,str) or not text.strip() or len(text)>4000:
            raise ValueError("Enter a short requirements message")
        with self.lock:
            project = self.project(identifier)
            client = SparkClient(PreviewTransport(self.demo.controller_truth), BudgetScope(self.preview_ledger, "discussion-"+identifier, 1))
            history = [{"role":"developer","content":"LX_DISCUSSION\nUse inventory metadata first; ask only consequential questions."},
                {"role":"user","content":json.dumps({"goal":project["goal"],"inventory":project["inventory"],"message":text})}]
            reply,_ = client.chat(history, role="orchestrator",phase="discussion",candidate=identifier)
            project["messages"].extend([{"role":"user","content":text},{"role":"assistant","content":reply["content"]}])
            return self.store.save("project", project)

    def edit_spec(self, identifier, data):
        with self.lock:
            if any(j["project_id"]==identifier and j["status"] in {"queued","running"} for j in self.state()["jobs"]):
                raise ValueError("Stop the active run before editing its spec")
            project = self.project(identifier)
            if data.get("model", "spark-contributor") != "spark-contributor":
                raise ValueError("Only Spark Contributor may fill a live role")
            return self.store.save("project", self._draft(project, data.get("goal"), data.get("budget_usd",1)))

    def approve(self, identifier, data):
        with self.lock:
            project = self.project(identifier)
            if data.get("version") != project["draft"]["version"] or data.get("data_approved") is not True or data.get("contributor_accepted") is not True:
                raise ValueError("Approve the current spec, synthetic data and Contributor disclosure")
            if not project["compatible_demo"]:
                raise ValueError("This first workflow requires the constructed demo manifest and images; other inputs can be inventoried and discussed")
            project["approved"] = {"version":project["draft"]["version"],"spec_hash":fingerprint(project["draft"]),"data_hash":fingerprint(project["inventory"])}
            return self.store.save("project", project)

    def start(self, identifier, launch=True):
        with self.lock:
            project = self.project(identifier)
            if not project["approved"] or project["approved"]["spec_hash"] != fingerprint(project["draft"]):
                raise ValueError("Approve the current spec before starting")
            if any(j["status"] in {"queued","running"} for j in self.state()["jobs"]):
                raise ValueError("One run is already active")
            job_id = uuid.uuid4().hex
            job = {"id":job_id,"project_id":identifier,"name":project["name"],"status":"queued","phase":"queued",
                   "mode":"controlled_preview","spec":project["draft"],"approved":project["approved"],"launch_pending":True}
            inputs = self.root/"inputs"/job_id
            captured = snapshot_folder(self.root/"projects"/identifier/"snapshot",inputs)
            if captured["inventory"] != project["inventory"]["inventory"]:
                raise ValueError("Snapshot changed since approval; create a new project snapshot")
            # Verify frozen bytes against the approved inventory, never arbitrary paths.
            for item in project["inventory"]["inventory"]:
                target = inputs.joinpath(*PureSafe(item["path"]))
                if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != item["sha256"]:
                    raise ValueError("Snapshot changed since approval; create a new project snapshot")
            self.store.save("job", job)
            if launch:
                self._launch(job_id)
            return job

    def verify_job_approval(self,job,snapshot):
        project = self.project(job["project_id"])
        if (project["approved"] != job["approved"] or fingerprint(job["spec"]) != job["approved"]["spec_hash"]
            or fingerprint(project["inventory"]) != job["approved"]["data_hash"]):
            raise ValueError("Approval changed before execution; approve a new run")
        for item in project["inventory"]["inventory"]:
            target = Path(snapshot)
            for part in PureSafe(item["path"]):
                target = target/part
                if target.is_symlink():
                    raise ValueError("Linked snapshot rejected before execution")
            if not target.is_file() or target.stat().st_size != item["size"] or hashlib.sha256(target.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("Frozen snapshot changed before execution")

    def _launch(self, identifier):
        # Worker main owns POSIX signal handlers; HTTP request threads do not.
        with open(os.devnull,"wb") as output:
            subprocess.Popen([sys.executable,"-m","lx.worker","--root",str(self.root),"--job",identifier],
                             stdout=output,stderr=output,start_new_session=True)

    def stop(self, identifier):
        from h2portable.jobs import stop_job
        job = self.store.get("job",identifier)
        try:
            state = stop_job(identifier,self.root/"core-jobs")
        except ValueError:
            if job["status"] != "queued":
                raise
            state = {"status":"cancelled"}
        job["status"],job["phase"] = state["status"],"stopped"
        job["launch_pending"] = False
        return self.store.save("job",job)

    def resume(self, identifier):
        with self.lock:
            return self._resume(identifier)

    def _resume(self, identifier):
        if any(j["id"] != identifier and j["status"] in {"queued","running"} for j in self.state()["jobs"]):
            raise ValueError("Another run is already active")
        job = self.store.get("job",identifier)
        if job["status"] in {"queued","running","completed"}:
            raise ValueError("Only interrupted, stopped or failed jobs can resume")
        project = self.project(job["project_id"])
        if project["approved"] != job["approved"]:
            raise ValueError("Spec changed; approve and start a new run")
        job.update(status="queued",phase="resuming",launch_pending=True)
        job.pop("error",None)
        self.store.save("job",job)
        self._launch(identifier)
        return job

    def package_bytes(self, identifier):
        job = self.store.get("job",identifier)
        if job["status"] != "completed":
            raise ValueError("No completed package is available")
        folder = self.root/"outputs"/identifier/"package"
        stream = io.BytesIO()
        with zipfile.ZipFile(stream,"w",zipfile.ZIP_DEFLATED) as archive:
            for path in folder.rglob("*"):
                if path.is_file() and not path.is_symlink() and path.suffix in {".md",".json"}:
                    archive.write(path,path.relative_to(folder).as_posix())
            if not (folder/"usage.json").exists():
                archive.writestr("usage.json",json.dumps(summarize(usage_rows(self.preview_ledger,identifier+"/")),indent=2))
        return stream.getvalue()

    def fresh(self, identifier):
        job = self.store.get("job",identifier)
        if job["status"] != "completed":
            raise ValueError("Complete the run before reusing its harness")
        root = self.root/"fresh"/uuid.uuid4().hex
        root.mkdir(parents=True)
        offset = int(root.name[:2],16)%10
        rectangles = (_Rectangle("red_square",(5+offset,9,25+offset,29),(220,45,45)),
                      _Rectangle("blue_rectangle",(49,32,85,55),(45,90,220)))
        _write_png(root/"fresh.png",rectangles)
        sample = {"sample_id":"fresh-scene","source_scene_id":root.name,"split":"heldout","image":"fresh.png",
                  "transcript":"A red square is above a blue rectangle."}
        (root/"manifest.json").write_text(json.dumps({"samples":[sample]}))
        truth = {"fresh-scene":{"objects":[{"label":r.label,"box":list(r.box)} for r in rectangles],"text":sample["transcript"]}}
        client = SparkClient(PreviewTransport(truth),BudgetScope(self.preview_ledger,"reuse-"+identifier,1))
        adapter = ImagesAdapter(root,root/"execution",client)
        run = adapter.execute("fresh-scene",self.root/"outputs"/identifier/"package"/"harness","fresh")
        answer = json.loads(adapter.deliverable_text(run))
        checked = verify_output(truth["fresh-scene"],answer)
        result = {"mode":"controlled_preview","accepted":checked.accepted,"errors":checked.errors,"answer":answer,
                  "source_scene_id":sample["source_scene_id"],"note":"New constructed scene; controlled response, no live model call"}
        job["fresh_result"] = result
        self.store.save("job",job)
        return result


def PureSafe(value):
    from pathlib import PurePosixPath
    path = PurePosixPath(value)
    if path.is_absolute() or any(p.startswith(".") for p in path.parts) or "\\" in value or ":" in value:
        raise ValueError("Invalid snapshot inventory path")
    return path.parts
