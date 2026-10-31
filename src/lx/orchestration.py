"""What an orchestrator may do (D-024). The built-in one, the CLI and the MCP server all come through here.

Each action names its workspace folder and returns at once; a run is started,
then polled. Anything left out stays with a person: approving a live run,
grants, budgets, keys, provider entries, held spend. The spec has no field for skills,
scripts or guidance, because the loop learns those (D-021).
"""
from __future__ import annotations

from pathlib import PurePosixPath

from . import tasks
from .engine import EXTERNAL

ACTIONS = ("status", "init", "samples", "read_sample", "truth_shape", "spec_show", "spec_draft", "check_test", "ready", "preview", "run_start", "run_status", "run_stop", "results")
TEMPLATES = {"photo-request": "inventory", "text-request": "records"}
_MODEL_FIELDS = ("id", "name", "model_id", "role_ready", "enabled")
MAX_SAMPLE_TEXT = 8000
_RUN_FIELDS = ("id", "status", "phase", "mode", "created_at", "approved_by", "grant", "progress", "error")


class Orchestration:
    """`external`: the caller is an outside agent, so specs it writes name it as orchestrator."""

    def __init__(self, engine, *, external=True, launch=True):
        self.engine, self.external, self.launch = engine, external, launch

    def _id(self, workspace):
        return self.engine.open_workspace(workspace)["id"]

    def _roles(self, current, worker, improver):
        return {"orchestrator": EXTERNAL if self.external else current["orchestrator"],
                "improver": improver or current["improver"], "worker": worker or current["worker"]}

    def status(self):
        """Whether LX can run. Says a key exists, never what it is."""
        engine = self.engine
        return {"live_available": engine.live_enabled, "meta_key": bool(engine.registry.meta_key_source()),
                "models": [{field: model.get(field) for field in _MODEL_FIELDS} for model in engine.registry.list_models()]}

    def init(self, workspace, template, *, goal=None, name=None):
        if template not in TEMPLATES:
            raise ValueError("Choose a template: "+", ".join(TEMPLATES))
        project = self.engine.create_project({"source": "folder", "task": TEMPLATES[template], "path": workspace, "goal": goal, "name": name})
        if self.external:
            project = self.engine.draft_spec(project["id"], roles=self._roles(project["draft"]["roles"], None, None))
        return {"workspace": project["path"], "version": project["draft"]["version"]}

    def samples(self, workspace):
        return {"inventory": self._files(self._id(workspace))}

    def _files(self, identifier):
        return self.engine.sample_files(self.engine.workspace(identifier), self.engine.project(identifier)["draft"])

    def read_sample(self, workspace, path):
        """One listed input file as text, never an answer file."""
        identifier = self._id(workspace)
        if PurePosixPath(path).name == "truth.json":
            raise ValueError("Answer files cannot be read")
        if path not in {item["path"] for item in self._files(identifier)}:
            raise ValueError("That file is not in the sample list")
        if PurePosixPath(path).suffix.lower() not in {".txt", ".json", ".md", ".csv"}:
            raise ValueError("Only text files can be read here")
        data = (self.engine.workspace(identifier).samples_path/path).read_bytes()
        return {"text": data[:MAX_SAMPLE_TEXT].decode("utf-8", "replace"), "cut": len(data) > MAX_SAMPLE_TEXT}

    def truth_shape(self, workspace):
        """Field names and types of the answers, so a check can be written without seeing them."""
        return tasks.truth_shape(self._truths(self._id(workspace)))

    def _truths(self, identifier):
        task = self.engine.project(identifier)["draft"].get("task")
        if not task:
            raise ValueError("This workflow has a built-in task and check")
        return tasks.sample_truths(self.engine.workspace(identifier).samples_path, task)

    def spec_show(self, workspace):
        project = self.engine.project(self._id(workspace))
        draft, approved = project["draft"], project["approved"]
        check = None
        if draft.get("check"):
            check = self.engine.workspace(project["id"]).check_source(draft["check"])
        return {"goal": project["goal"], "version": draft["version"], "mode": draft["mode"], "roles": draft["roles"], "tools": draft.get("tools", []),
                "budget_usd": draft["budget_usd"], "limits": draft["limits"], "task": draft.get("task"), "check": check, "blocked": project["blocked"],
                "approved": {"by": approved.get("by", "person")} if approved and approved["version"] == draft["version"] else None,
                "workspace_budget": project["budget"], "grant": project["grant"], "notice": project["notice"], "external_orchestrator": project["external_orchestrator"]}

    def spec_draft(self, workspace, *, goal=None, task=None, check=None, tools=None, limits=None, mode=None, worker=None, improver=None):
        """A new spec version, which clears any approval. `task` holds only the fields that change."""
        identifier = self._id(workspace)
        current = self.engine.project(identifier)["draft"]["roles"]
        if task and tasks.quotes_answers(" ".join(str(value) for value in task.values()), self._truths(identifier)):
            raise ValueError("The task text repeats answers from the samples; describe the work without them")
        project = self.engine.draft_spec(identifier, goal=goal, mode=mode, roles=self._roles(current, worker, improver), task=task,
                                         check=check, limits=limits, tools=tools)
        return {"version": project["draft"]["version"], "blocked": project["blocked"]}

    def check_test(self, workspace):
        return self.engine.test_check(self._id(workspace))

    def ready(self, workspace):
        return self.engine.ready(self._id(workspace))

    def preview(self, workspace):
        identifier = self._id(workspace)
        self.engine.approve_preview(identifier)
        return {"status": "started", "run_id": self.engine.start(identifier, launch=self.launch)["id"]}

    def run_start(self, workspace):
        """Starts a live run a person has approved or a grant covers. Otherwise it stays pending for them."""
        identifier = self._id(workspace)
        project = self.engine.project(identifier)
        draft, approved = project["draft"], project["approved"]
        if draft["mode"] != "live":
            raise ValueError("This spec is a preview; use preview")
        if project["blocked"]:
            return {"status": "blocked", "reason": project["blocked"]}
        if not approved or approved["version"] != draft["version"] or approved.get("by", "person") != "person":
            return self.engine.start_granted(identifier, launch=self.launch)
        return {"status": "started", "run_id": self.engine.start(identifier, launch=self.launch)["id"]}

    def run_status(self, workspace):
        cost = lambda run: sum(role["spent_usd"] for role in (run.get("usage") or {}).get("per_role", {}).values())
        return {"runs": [{field: run.get(field) for field in _RUN_FIELDS} | {"cost_usd": cost(run), "simulated_cost": run["mode"] != "live"}
                         for run in self.engine.runs(self._id(workspace))]}

    def run_stop(self, workspace, run_id):
        return {"status": self.engine.stop(self._id(workspace), run_id)["status"]}

    def results(self, workspace, run_id):
        identifier = self._id(workspace)
        run = next((run for run in self.engine.runs(identifier) if run["id"] == run_id), None)
        if run is None or run["status"] != "completed":
            raise ValueError("That run has no results yet")
        learned = self.engine.workspace(identifier).package_dir(run_id)/"learned.md"
        return {**{key: value for key, value in run.get("result", {}).items() if key != "spec"},
                "learned": learned.read_text(encoding="utf-8")[:MAX_SAMPLE_TEXT] if learned.is_file() else None}
