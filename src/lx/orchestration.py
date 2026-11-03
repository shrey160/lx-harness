"""What an orchestrator may do (D-024). The built-in one, the CLI and the MCP server all come through here.

Each action names its workspace folder and returns at once; a run is started,
then polled. Anything left out stays with a person: grants, budgets, keys, provider entries, held spend. The spec has no field for skills,
scripts or guidance, because the loop learns those (D-021).
"""
from __future__ import annotations

from pathlib import PurePosixPath

from . import check_forms, judging, sandbox, tasks
from .engine import EXTERNAL
from .guides import guide_list, read_guide
from .limits import field_list

ACTIONS = ("status", "guide", "environment", "init", "workspace_delete", "samples", "read_sample", "truth_shape", "spec_show", "spec_draft", "eval_init", "check_test", "check_try", "judge_try", "ready", "preview", "approve", "run_start", "trial_start", "run_status", "run_stop", "edits", "results")
TEMPLATES = {"photo-request": "inventory", "text-request": "records"}
_MODEL_FIELDS = ("id", "name", "model_id", "role_ready", "enabled")
MAX_SAMPLE_TEXT = 8000
_RUN_FIELDS = ("id", "kind", "status", "phase", "mode", "created_at", "finished_at", "approved_by", "grant", "progress", "error", "from_run", "from_sample")


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
                "can_delete_workspaces": engine.agent_access() and engine.agent_workspace_deletion(),
                "models": [{field: model.get(field) for field in _MODEL_FIELDS} for model in engine.registry.list_models()]}

    def workspace_delete(self, workspace):
        """Delete LX data only, if a person has enabled agent deletion in Settings."""
        self.engine._require_agent_workspace_deletion()
        return self.engine.delete_workspace(self._id(workspace), by="agent")

    def guide(self, topic=None):
        """The guides, or one guide's text."""
        return {"guides": guide_list()} if topic is None else read_guide(topic)

    def environment(self):
        """What this install offers a run, as facts. Finding the sandbox can start Docker's client."""
        system = self.engine.system()
        return {"sandbox": sandbox.describe(), "model_tools": tasks.tool_list(self.engine.registry.list_models()), "agent_tools": system["agent_tools"],
                "mcp_tools": self.engine.mcp_choices(),
                "limits": field_list(), "templates": list(TEMPLATES), "guides": guide_list()}

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

    def _truths(self, identifier, changes=None, *, need_truth=True):
        task = self.engine.project(identifier)["draft"].get("task")
        if not task:
            raise ValueError("This workflow has a built-in task and check")
        return tasks.sample_truths(self.engine.workspace(identifier).samples_path, {**task, **(changes or {})}, need_truth=need_truth)

    def spec_show(self, workspace):
        project = self.engine.project(self._id(workspace))
        draft, approved = project["draft"], project["approved"]
        check = None
        if draft.get("check"):
            check = check_forms.author_text(self.engine.workspace(project["id"]).check_source(draft["check"]))
        return {"goal": project["goal"], "version": draft["version"], "mode": draft["mode"], "roles": draft["roles"], "tools": draft.get("tools", []), "mcp_tools": [entry["server"]+"/"+entry["tool"] for entry in tasks.spec_mcp(draft)],
                "budget_usd": draft["budget_usd"], "limits": draft["limits"], "task": draft.get("task"), "check": check, "blocked": project["blocked"],
                "approved": {"by": approved.get("by", "person")} if approved and approved["version"] == draft["version"] else None,
                "workspace_budget": project["budget"], "trial_cap_usd": project["trial_cap_usd"], "grant": project["grant"], "notice": project["notice"], "external_orchestrator": project["external_orchestrator"],
                "hints": draft.get("hints"), "hint_drift": project["hint_drift"], "eval_drift": project["eval_drift"], "judges": judging.written(draft.get("judge")),
                "tell_improver_worker": draft.get("tell_improver_worker", False)}

    def spec_draft(self, workspace, *, goal=None, task=None, check=None, tools=None, limits=None, mode=None, worker=None, improver=None, mcp_tools=None,
                   hints=None, sample_hints=None, use_hint_files=False, use_eval_file=False, no_check=False, judges=None, use_judge_file=False, no_judges=False,
                   tell_improver_worker=None):
        """A new spec version, which clears any approval. `task` holds only the fields that change."""
        identifier = self._id(workspace)
        check = self._check_text(identifier, check, use_eval_file)
        if use_judge_file and judges is not None:
            raise ValueError("Give judges or use the judges file, not both")
        judges = self.engine.eval_judges(identifier) if use_judge_file else judges
        current = self.engine.project(identifier)["draft"]["roles"]
        if task and tasks.quotes_answers(" ".join(str(value) for value in task.values()), self._truths(identifier, task, need_truth=False)):
            raise ValueError("The task text repeats answers from the samples; describe the work without them")
        project = self.engine.draft_spec(identifier, goal=goal, mode=mode, roles=self._roles(current, worker, improver), task=task,
                                         check=check, limits=limits, tools=tools, mcp_tools=mcp_tools,
                                         hints=self._hints(identifier, hints, sample_hints), take_hint_files=use_hint_files, no_check=no_check,
                                         judge=judges, no_judge=no_judges, tell_improver_worker=tell_improver_worker)
        live = {"note": "A live run or trial needs a person's approval, or a grant that covers it"} if project["draft"]["mode"] == "live" else {}
        return {"version": project["draft"]["version"], "blocked": project["blocked"], **live}

    def _hints(self, identifier, everyone, per_sample):
        """The spec's hints with these changes: text for all samples replaces, a text per sample is added, empty text removes."""
        if everyone is None and per_sample is None:
            return None
        if per_sample is not None and not isinstance(per_sample, dict):
            raise ValueError("sample_hints maps a sample folder to its text")
        held = self.engine.project(identifier)["draft"].get("hints") or {"all": None, "samples": {}}
        samples = {**held["samples"], **(per_sample or {})}
        return {"all": held["all"] if everyone is None else everyone, "samples": samples}

    def eval_init(self, workspace):
        return {**self.engine.eval_init(self._id(workspace)), "next": "Change the rules to suit the task, try them with check_test and use_eval_file, then save them with spec_draft and use_eval_file."}

    def check_test(self, workspace, check=None, use_eval_file=False):
        identifier = self._id(workspace)
        return self.engine.test_check(identifier, self._check_text(identifier, check, use_eval_file))

    def check_try(self, workspace, check=None, use_eval_file=False):
        identifier = self._id(workspace)
        return self.engine.try_check(identifier, self._check_text(identifier, check, use_eval_file))

    def judge_try(self, workspace, judges=None, use_judge_file=False):
        """What the judges make of saved answers. It never spends: only verdicts already kept count, so new ones wait for a person."""
        identifier = self._id(workspace)
        if use_judge_file and judges is not None:
            raise ValueError("Give judges or use the judges file, not both")
        return self.engine.try_judges(identifier, self.engine.eval_judges(identifier) if use_judge_file else judges)

    def _check_text(self, identifier, check, use_eval_file):
        if use_eval_file and check is not None:
            raise ValueError("Give a check or use the eval file, not both")
        return self.engine.eval_check(identifier) if use_eval_file else check

    def ready(self, workspace):
        return self.engine.ready(self._id(workspace))

    def preview(self, workspace):
        identifier = self._id(workspace)
        self.engine.approve_preview(identifier)
        return {"status": "started", "run_id": self.engine.start(identifier, launch=self.launch)["id"]}

    def approve(self, workspace):
        """Approves the current live spec for the person who connected this agent (D-026)."""
        return self.engine.approve_as_agent(self._id(workspace))

    def run_start(self, workspace):
        """Starts an approved live run, or one a grant covers. Otherwise it stays pending."""
        return self._start(workspace, trial=False)

    def trial_start(self, workspace, from_run=None, from_sample=None):
        """One sample on the base harness, or on a finished run's package, under the same terms as a live run (D-025)."""
        return self._start(workspace, trial=True, from_run=from_run, from_sample=from_sample)

    def _start(self, workspace, *, trial, from_run=None, from_sample=None):
        identifier = self._id(workspace)
        project = self.engine.project(identifier)
        draft, approved = project["draft"], project["approved"]
        if draft["mode"] != "live":
            raise ValueError("This spec is a preview; use preview")
        if project["blocked"]:
            return {"status": "blocked", "reason": project["blocked"]}
        if not approved or approved["version"] != draft["version"] or approved.get("by", "person") not in {"person", "agent"}:
            return self.engine.start_granted(identifier, launch=self.launch, trial=trial, from_run=from_run, from_sample=from_sample)
        return {"status": "started", "run_id": self.engine.start(identifier, launch=self.launch, trial=trial, from_run=from_run, from_sample=from_sample)["id"]}

    def run_status(self, workspace):
        cost = lambda run: sum(role["spent_usd"] for role in (run.get("usage") or {}).get("per_role", {}).values())
        return {"runs": [{field: run.get(field) for field in _RUN_FIELDS} | {"cost_usd": cost(run), "simulated_cost": run["mode"] != "live"}
                         for run in self.engine.runs(self._id(workspace))]}

    def run_stop(self, workspace, run_id):
        return {"status": self.engine.stop(self._id(workspace), run_id)["status"]}

    def edits(self, workspace, run_id):
        """What the improver wrote, sample by sample, during a run or after it."""
        return self.engine.run_edits(self._id(workspace), run_id)

    def results(self, workspace, run_id):
        identifier = self._id(workspace)
        run = next((run for run in self.engine.runs(identifier) if run["id"] == run_id), None)
        if run is None or run["status"] != "completed":
            raise ValueError("That run has no results yet")
        learned = self.engine.workspace(identifier).package_dir(run_id)/"learned.md"
        return {**{key: value for key, value in run.get("result", {}).items() if key != "spec"},
                "learned": learned.read_text(encoding="utf-8")[:MAX_SAMPLE_TEXT] if learned.is_file() else None}
