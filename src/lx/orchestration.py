"""What an orchestrator may do (D-024). The built-in one, the CLI and the MCP server all come through here.

Each action names its workspace folder and returns at once; a run is started,
then polled. Anything left out stays with a person: approving a live run,
budgets, keys, provider entries, held spend. The spec has no field for skills,
scripts or guidance, because the loop learns those (D-021).
"""
from __future__ import annotations

from .engine import EXTERNAL

ACTIONS = ("status", "init", "samples", "spec_show", "spec_draft", "check_test", "preview", "run_start", "run_status", "run_stop", "results")
TEMPLATES = {"photo-request": "inventory", "text-request": "records"}
_MODEL_FIELDS = ("id", "name", "model_id", "role_ready", "enabled")
_RUN_FIELDS = ("id", "status", "phase", "mode", "created_at", "approved_by", "progress", "error")


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
        return {"inventory": self.engine.project(self._id(workspace))["inventory"]}

    def spec_show(self, workspace):
        project = self.engine.project(self._id(workspace))
        draft, approved = project["draft"], project["approved"]
        check = None
        if draft.get("check"):
            check = self.engine.workspace(project["id"]).check_source(draft["check"])
        return {"goal": project["goal"], "version": draft["version"], "mode": draft["mode"], "roles": draft["roles"], "tools": draft.get("tools", []),
                "budget_usd": draft["budget_usd"], "limits": draft["limits"], "task": draft.get("task"), "check": check, "blocked": project["blocked"],
                "approved": {"by": approved.get("by", "person")} if approved and approved["version"] == draft["version"] else None,
                "workspace_budget": project["budget"], "notice": project["notice"], "external_orchestrator": project["external_orchestrator"]}

    def spec_draft(self, workspace, *, goal=None, task=None, check=None, tools=None, limits=None, mode=None, worker=None, improver=None):
        """A new spec version, which clears any approval. `task` holds only the fields that change."""
        identifier = self._id(workspace)
        current = self.engine.project(identifier)["draft"]["roles"]
        project = self.engine.draft_spec(identifier, goal=goal, mode=mode, roles=self._roles(current, worker, improver), task=task,
                                         check=check, limits=limits, tools=tools)
        return {"version": project["draft"]["version"], "blocked": project["blocked"]}

    def check_test(self, workspace):
        return self.engine.test_check(self._id(workspace))

    def preview(self, workspace):
        identifier = self._id(workspace)
        self.engine.approve_preview(identifier)
        return {"status": "started", "run_id": self.engine.start(identifier, launch=self.launch)["id"]}

    def run_start(self, workspace):
        """Starts a live run a person has approved. Otherwise it stays pending for them."""
        identifier = self._id(workspace)
        project = self.engine.project(identifier)
        draft, approved = project["draft"], project["approved"]
        if draft["mode"] != "live":
            raise ValueError("This spec is a preview; use preview")
        if project["blocked"]:
            return {"status": "blocked", "reason": project["blocked"]}
        if not approved or approved["version"] != draft["version"] or approved.get("by", "person") != "person":
            return {"status": "pending", "reason": "A person needs to approve this live run in the LX app"}
        return {"status": "started", "run_id": self.engine.start(identifier, launch=self.launch)["id"]}

    def run_status(self, workspace):
        cost = lambda run: sum(role["spent_usd"] for role in (run.get("usage") or {}).get("per_role", {}).values())
        return {"runs": [{field: run.get(field) for field in _RUN_FIELDS} | {"cost_usd": cost(run)} for run in self.engine.runs(self._id(workspace))]}

    def run_stop(self, workspace, run_id):
        return {"status": self.engine.stop(self._id(workspace), run_id)["status"]}

    def results(self, workspace, run_id):
        identifier = self._id(workspace)
        run = next((run for run in self.engine.runs(identifier) if run["id"] == run_id), None)
        if run is None or run["status"] != "completed":
            raise ValueError("That run has no results yet")
        return {key: value for key, value in run.get("result", {}).items() if key != "spec"}
