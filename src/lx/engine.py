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
(pilot_ledger) stays for our experiments only.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
from importlib.metadata import version
from importlib.util import find_spec
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
import uuid
import zipfile

from .budget import BudgetLedger
from .domain import build_demo_dataset, verify_output, _Rectangle, _write_png
from .health import read_health
from .home import home
from . import check_forms, checks, hints as hint_notes, judging, launcher, limits as run_limits, sandbox, tasks, text_task
from .intake import IntakeError, MAX_BYTES, inspect_folder, snapshot_folder, snapshot_zip
from .keychain import SystemKeychain
from .live_transport import DISCUSSION_CALL_LIMIT, RUN_CALL_LIMIT, LiveChatTransport
from .perception import FORMATS, PerceptionCache, PerceptionClient, ToolTransport
from .providers import META_API, SPARK
from .model_list import read_models
from .registry import Registry
from .spark import IncompleteResponse, ModelError, ChatClient
from .store import Store
from .usage import BudgetScope, usage_rows, summarize
from .inventory import write_synthetic
from . import mcp_client
from .components import COMPONENT_TOOLS, LOAD_SKILL, task_tools
from .inventory_workflow import InventoryPreviewTransport
from .workflow import ImagesAdapter, PreviewTransport, samples_from, sample_path
from .text_task import TextPreviewTransport
from .workspace import NOT_SAMPLES, Workspace, native_path, write_json

ROOT = Path(__file__).resolve().parents[2]
NO_CHECK = "This run has no check, so it will not be scored."
JUDGED = "Scored partly by a model judge, whose verdicts can vary and may be wrong."
MAX_REPLY = 20_000
DEFAULT_ROOT = home()/".local"/"state"/"lx-harness"/hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
SAMPLES = "samples"
SAMPLE_MEDIA = {".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".wav":"audio/wav"}
ROLES =("orchestrator","improver","worker")
DEFAULT_ROLE_ENTRY = "spark-contributor"
EXTERNAL = tasks.EXTERNAL
EXTERNAL_PROVIDER = {"id":EXTERNAL,"name":"Connected agent","model_id":"","base_url":"","pricing":{},"context_limit":0,"accepts_images":False,
                     "supports_tools":True,"priced":True,"requests_per_minute":None,"limits":{}}
SHORT_NAMES = {tasks.SEGMENTATION:"SAM",tasks.TRANSCRIPTION:"Transcribe"}
DEFAULT_WORKSPACE_BUDGET = 5
DEFAULT_TRIAL_CAP = 0.25
GRANT_DAYS, GRANT_RUNS = 7, 3
MAX_GRANT_DAYS, MAX_GRANT_RUNS = 30, 20
PROVIDER_TEST_CAP = 1
PROVIDER_TEST_SECONDS = 60


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


class Engine:
    def __init__(self, root=DEFAULT_ROOT, keychain=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.root/"app.sqlite3")
        secret_root = (home()/".local"/"share"/"lx-harness"/fingerprint(str(ROOT))[:16]/"secrets"
                       if self.root == DEFAULT_ROOT.resolve() and os.name == "posix" else self.root/"secrets")
        self.registry = Registry(self.root/"app.sqlite3", secret_root, keychain or SystemKeychain())
        self.demo = build_demo_dataset(self.root/"demo")
        # Real pilot spend is never reset by creating the engine or previews.
        self.pilot_ledger = BudgetLedger(ROOT/".lx"/"live-spike"/"pilot-budget.sqlite3") if self.root == DEFAULT_ROOT.resolve() else None
        self.live_enabled = self.root == DEFAULT_ROOT.resolve()  # a scratch root never makes real calls
        self._live_transport_factory = None
        self._live_perception_factory = None
        self._model_list = None

    def make_live_transport(self, call_limit=RUN_CALL_LIMIT, truth_hint=None):
        if self._live_transport_factory is not None:
            return self._live_transport_factory(truth_hint)
        return LiveChatTransport(lambda: self.registry.credential("spark-contributor"), call_limit=call_limit)

    def _transport(self, entry, profile, call_limit, truth, *, limits, rate, contract=None):
        if self._live_transport_factory is not None:
            return self._live_transport_factory(truth)
        if not self.live_enabled:
            raise ValueError("Live runs are unavailable here; use the local app workspace")
        keyed = entry == DEFAULT_ROLE_ENTRY or any(m["id"] == entry and (m["has_key"] or m["credential_source"] == "shared Meta key") for m in self.registry.list_models())
        return LiveChatTransport((lambda: self.registry.credential(entry)) if keyed else None, call_limit=call_limit,
                                 call_ceiling=call_limit, profile=profile, max_call_seconds=limits["call_seconds"],
                                 requests_per_minute=rate, **(contract or {}))

    def limits_of(self, spec):
        """A spec's limits; anything it predates runs on the default."""
        values = {**self._limit_defaults(spec["providers"], spec.get("task")), **(spec.get("limits") or {})}
        if "worker_reply_tokens" not in (spec.get("limits") or {}):
            values["worker_reply_tokens"] = values["reply_tokens"]
        return values

    @staticmethod
    def _limit_defaults(providers, task):
        return run_limits.defaults({role:(p["id"] in {DEFAULT_ROLE_ENTRY,EXTERNAL}, p.get("limits") or {}) for role,p in providers.items()},
                                   task=bool(task))

    def _tool_transport(self, entry, call_limit):
        return ToolTransport(FORMATS[self.registry.tool_format(entry)], lambda: self.registry.credential(entry), call_limit=call_limit)

    def build_perception(self, workspace, scope_id, cap_usd, cache, limits, tools=tasks.MODEL_TOOLS):
        """Live SAM and Transcribe for a task's agent; their cost is on the workspace ledger. A tool that is off stays unbuilt."""
        scope = BudgetScope(self._ledger(workspace), scope_id, cap_usd)
        if self._live_perception_factory is not None:
            segment, transcribe = self._live_perception_factory()
        elif not self.live_enabled:
            raise ValueError("Live runs are unavailable here; use the local app workspace")
        else:
            segment, transcribe = (self._tool_transport(tool, limits[limit]) if tool in tools else None
                                   for tool, limit in ((tasks.SEGMENTATION, "segment_calls"), (tasks.TRANSCRIPTION, "transcribe_calls")))
        return PerceptionClient(segment, transcribe, scope, PerceptionCache(cache))

    @staticmethod
    def _agent_tools(spec):
        """Exactly what the run's adapter sends; the live transport refuses any other list."""
        scripts = sandbox.available()
        chosen = [mcp_client.function_tool(entry) for entry in tasks.spec_mcp(spec)]
        if tasks.uses_tools(spec.get("task")):
            return task_tools(tasks.tool_catalog(tasks.spec_tools(spec)), scripts)+chosen
        return (COMPONENT_TOOLS if scripts else [LOAD_SKILL])+chosen

    def build_clients(self, workspace, spec, scope_id, cap_usd, *, call_limit=None, truth=None, contract=None):
        """One client per role. Live spends from the workspace ledger; preview never does.

        Roles sharing an entry share a transport, so its call limit covers the run."""
        mode = spec.get("mode")
        if mode not in {"live","controlled_preview"}:
            raise ValueError("Unknown run mode")
        truth = truth if truth is not None else self.demo.controller_truth
        ledger = self._ledger(workspace) if mode == "live" else workspace.preview_ledger
        limits = self.limits_of(spec)
        call_limit = call_limit or limits["chat_calls"]
        if contract is not None:
            pass
        elif tasks.is_text(spec.get("task")):
            contract = {"agent_tools":self._agent_tools(spec)}
        elif spec.get("task"):
            from .inventory_probe import MAX_VIEW_BYTES
            contract = {"agent_tools":self._agent_tools(spec),"image_types":("jpeg",),"max_image_bytes":MAX_VIEW_BYTES,"max_images":None}
        transports, clients = {}, {}
        for role in ROLES:
            entry = spec["roles"][role]
            if entry == EXTERNAL:
                continue
            profile = self.registry.profile(entry)
            if entry not in transports:
                transports[entry] = (self._transport(entry, profile, call_limit, truth, limits=limits, contract=contract,
                                                     rate=spec["providers"][role].get("requests_per_minute")) if mode == "live"
                                     else TextPreviewTransport() if tasks.is_text(spec.get("task"))
                                     else InventoryPreviewTransport(segmentation=tasks.SEGMENTATION in tasks.spec_tools(spec)) if spec.get("task")
                                     else PreviewTransport(truth))
            reply = limits["worker_reply_tokens"] if role == "worker" else limits["reply_tokens"]
            clients[role] = ChatClient(transports[entry], BudgetScope(ledger, scope_id, cap_usd), profile=profile,
                                        max_tokens=None if reply == 0 else reply, call_seconds=limits["call_seconds"])
        judge = spec.get("judge") or {}
        if judge.get("entry") and judge.get("cap_usd"):
            # Its own scope and cap, inside the run's, so a loop that spends its whole budget is still graded.
            clients["judge"] = self._judge_client(workspace, spec, f"{scope_id}/judge", judge["cap_usd"], transports.get(judge["entry"]) if mode == "live" else None,
                                                  call_limit=call_limit, truth=truth, contract=contract)
        return clients

    def _judge_client(self, workspace, spec, scope_id, cap_usd, transport=None, *, call_limit=None, truth=None, contract=None):
        judge, limits, live = spec["judge"], self.limits_of(spec), spec.get("mode") == "live"
        profile = self.registry.profile(judge["entry"])
        if transport is None:
            transport = (self._transport(judge["entry"], profile, call_limit or limits["chat_calls"], truth, limits=limits, contract=contract or {},
                                         rate=judge["provider"].get("requests_per_minute")) if live else judging.JudgePreviewTransport())
        return ChatClient(transport, BudgetScope(self._ledger(workspace) if live else workspace.preview_ledger, scope_id, cap_usd), profile=profile,
                          max_tokens=limits["reply_tokens"], call_seconds=limits["call_seconds"])

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

    def trial_cap(self, identifier, draft):
        """The most one trial may spend: the workspace's setting, within the run budget (D-025)."""
        try:
            cap = self.store.get("trial_cap", identifier)["cap_usd"]
        except ValueError:
            cap = DEFAULT_TRIAL_CAP
        return min(cap, draft["budget_usd"])

    def set_trial_cap(self, identifier, cap_usd):
        self.workspace(identifier)
        if isinstance(cap_usd, bool) or not isinstance(cap_usd, (int,float)) or not math.isfinite(cap_usd) or not 0.11 <= cap_usd <= 20:
            raise ValueError("A trial's cap must be between $0.11 and $20")
        self.store.save("trial_cap", {"id":identifier,"cap_usd":cap_usd})
        return self.project(identifier)

    def _provider(self, entry):
        """What an approval binds for a role: where its data goes and the price. Never the key."""
        if entry == EXTERNAL:
            return dict(EXTERNAL_PROVIDER)
        model = next((m for m in self.registry.list_models() if m["id"] == entry), None)
        if model is not None and model["kind"] == "chat" and model["base_url"] and not model["enabled"]:
            raise ValueError(f"{model['name']} is switched off in Settings, under Providers. Switch it on or choose another.")
        if model is None or not model["role_ready"]:
            raise ValueError("Each role needs a chat provider entry with an endpoint")
        return {**{key:model[key] for key in ("id","name","model_id","base_url","pricing","context_limit","accepts_images","supports_tools","priced",
                                              "requests_per_minute","limits")},
                **({} if model["thinking"] == "default" else {"thinking":model["thinking"]})}  # left out so older approvals still match

    def data_notice(self, draft):
        """Who receives what in a live run, for the owner to accept (D-015(8))."""
        host = lambda url: urlsplit(url).netloc
        task = draft.get("task")
        gets = {"orchestrator":"your goal, messages, file names, sizes and hashes, and the sample input text it reads",
                "improver":"run traces and answers, including text from the samples"+(", and the hints you wrote" if draft.get("hints") else "")
                           +(", and which model the worker is" if draft.get("tell_improver_worker") else ""),
                "worker":"the sample requests" if tasks.is_text(task) else "the sample photos, transcribed requests and any input.json task data" if task else "the sample images and transcripts"}
        notice = [{"who":role,"host":host(draft["providers"][role]["base_url"]),"gets":gets[role]} for role in ROLES if draft["roles"][role] != EXTERNAL]
        notice += [{"who":"orchestrator","host":"your connected agent","gets":"whatever it reads in this folder, on its own provider and account"}
                   ] if draft["roles"]["orchestrator"] == EXTERNAL else []
        judge = draft.get("judge") or {}
        notice += [{"who":"judge","host":host(judge["provider"]["base_url"]),
                    "gets":"each answer, its expected answer where the sample has one"+(", and the sample's request" if judge["sees_request"] else "")}
                   ] if judge.get("provider") else []
        tools = tasks.spec_tools(draft)
        notice += [{"who":"SAM 3.1","host":host(META_API),"gets":"the sample photos"}] if tasks.SEGMENTATION in tools else []
        notice += [{"who":"Muse Voice Transcribe","host":host(META_API),"gets":"the sample audio"}] if tasks.TRANSCRIPTION in tools else []
        notice += [{"who":f"MCP server {server}","host":"mcp:"+server,"gets":f"whatever the agent passes to its tools, at {place}"}
                   for server,place in sorted({(entry["server"],entry["where"]) for entry in tasks.spec_mcp(draft)})]
        return notice

    def _assign(self, roles, mode, task=None):
        unknown = set(roles) - set(ROLES)
        if unknown or set(roles) != set(ROLES):
            raise ValueError("Assign a provider to the orchestrator, improver and worker")
        if EXTERNAL in (roles["improver"], roles["worker"]):
            raise ValueError("A connected agent can only be the orchestrator")
        providers = {role:self._provider(roles[role]) for role in ROLES}
        if not providers["improver"]["supports_tools"]:
            raise ValueError("The improver's provider must support tool calls")
        if not tasks.is_text(task) and not providers["worker"]["accepts_images"]:
            raise ValueError("The worker's provider must accept images for this workflow")
        if task and not providers["worker"]["supports_tools"]:
            raise ValueError("The worker's provider must support tool calls for this task")
        return providers

    def _check_providers(self, spec):
        current = {role:self._provider(spec["roles"][role]) for role in ROLES}
        judge = (spec.get("judge") or {}).get("entry")
        if spec.get("providers") != current or (judge and spec["judge"].get("provider") != self._provider(judge)):
            raise ValueError("A provider changed since approval; approve again")
        blocked = self._blocked(spec)
        if blocked:
            raise ValueError(blocked)

    def _blocked(self, spec):
        """Why this spec cannot run, found here and not partway into its run."""
        tools = tasks.spec_tools(spec)
        models = {m["id"]:m for m in self.registry.list_models()}
        off = [models[tool]["name"] for tool in tools if not models[tool]["enabled"]]
        if off:
            needed = [models[tool]["name"] for tool in tasks.required_tools(spec.get("task")) if not models[tool]["enabled"]]
            return (f"This task needs {needed[0]}, which is off in Settings, under Tools." if needed else
                    f"{off[0]} is off in Settings, under Tools. Turn it on, or take it out of this spec.")
        stale = self._stale_mcp(spec)
        if stale:
            return stale
        if self.limits_of(spec)["worker_reply_tokens"] == 0:
            from .providers import unmetered_local
            if not unmetered_local(self.registry.profile(spec["roles"]["worker"])):
                return "No LX worker reply cap is supported only for a free local model."
        unset = judging.missing(spec.get("judge")) or self._judge_cap_short(spec)
        if unset:
            return unset
        if spec.get("mode") != "live" or self._live_transport_factory is not None:
            return None  # previews need no key
        entries = self._entries(spec)
        meta_models = [models[entry]["name"] for entry in sorted(entries)
                       if entry in models and entry != DEFAULT_ROLE_ENTRY and models[entry]["credential_source"] == "shared Meta key"]
        users = (["Spark"] if DEFAULT_ROLE_ENTRY in entries else []) + meta_models + [SHORT_NAMES[tool] for tool in tools]
        if not users or self.registry.meta_key_source():
            return None
        who = ", ".join(users[:-1])+" and "+users[-1] if len(users) > 1 else users[0]
        return f"A live run needs a Meta key for {who}. Add it in Settings, under Tools."

    def judge_call_usd(self, spec):
        """What one judge call holds while it runs: the whole context and reply at the entry's prices, as every call reserves."""
        profile = self.registry.profile(spec["judge"]["entry"])
        return float(profile.context_limit*profile.input_price + self.limits_of(spec)["reply_tokens"]*profile.output_price)

    def _judge_view(self, draft):
        """What a person approving a judged spec should see: how many calls, what each holds while it runs, and what the cap allows."""
        judge = draft.get("judge")
        if not judge or not judge.get("entry"):
            return None
        provider = judge["provider"]
        return {"model": provider["name"], "calls_per_run": 2*draft["task"]["stream_samples"], "calls_per_trial": 1,
                "pricing": provider["pricing"], "priced": provider["priced"], "call_holds_usd": self.judge_call_usd(draft), "cap_usd": judge.get("cap_usd"),
                "same_as_worker": judge["entry"] == draft["roles"]["worker"],
                "note": "Each answer is judged once, and a kept verdict is never paid for twice. The judges never spend more than their cap."}

    def _judge_cap_short(self, spec):
        judge = spec.get("judge")
        if not judge:
            return None
        needed = self.judge_call_usd(spec)
        if judge["cap_usd"] < needed:
            return (f"The judges' cap of ${judge['cap_usd']:.2f} is less than one judge call holds while it runs (${needed:.4f}), "
                    f"so no answer could be judged. Raise it to at least ${math.ceil(needed*100)/100:.2f}.")
        return None

    @staticmethod
    def _entries(spec):
        """Every provider entry a spec sends data to: its roles and its judge."""
        judge = (spec.get("judge") or {}).get("entry")
        return set(spec["roles"].values())|({judge} if judge else set())

    def _stale_mcp(self, spec):
        """Why a spec's MCP tools no longer match what a person connected and marked read-only."""
        servers = {server["id"]:server for server in self.mcp_servers()}
        for entry in tasks.spec_mcp(spec):
            server = servers.get(entry["server"])
            if server is None:
                return f"The MCP server {entry['server']} is no longer connected. Connect it, or take its tools out of this spec."
            found = next((tool for tool in server["tools"] if tool["tool"] == entry["tool"]), None)
            if found is None or mcp_client.definition(entry["server"], server, found) != entry:
                return f"The MCP tool {entry['name']} changed since this spec was written. Write a new spec version."
            if not found["read_only"]:
                return f"The MCP tool {entry['name']} is not marked read-only. Mark it in Settings, or take it out of this spec."
        return None

    # MCP servers (D-030). Connecting runs a program here or sends data to an address, so no orchestrator action reaches these changes.

    def mcp_servers(self):
        return sorted(self.store.list("mcp_server"), key=lambda server: server["id"])

    def mcp_view(self):
        """Settings' list. Secrets by name only; a value never leaves the keychain here."""
        return [{**{key:value for key,value in server.items() if key != "secrets"},"secrets":sorted(server["secrets"])} for server in self.mcp_servers()]

    def _mcp_server(self, name):
        return self.store.get("mcp_server", mcp_client.check_server_name(name))

    def _mcp_secrets(self, server):
        return {name:self.registry.kept_secret(reference) for name,reference in server["secrets"].items()}

    def _read_mcp(self, name, server, read_only):
        """Reads what a server offers and saves it, with this set of tools marked."""
        tools = mcp_client.read_tools(server, self._mcp_secrets(server))
        names = [mcp_client.agent_name(name, tool["tool"]) for tool in tools]
        if len(set(names)) != len(names) or any(len(item) > 64 for item in names):
            raise ValueError("This server's tool names clash or run too long once prefixed")
        self.store.save("mcp_server", {**server,"id":name,"tools":[{**tool,"read_only":tool["tool"] in read_only} for tool in tools]})
        return self._mcp_server(name)

    def mcp_connect(self, name, config, secrets=None):
        """A person's step. Starts or reaches the server and pins what it offers; nothing is marked read-only yet."""
        name = mcp_client.check_server_name(name)
        if any(server["id"] == name for server in self.mcp_servers()):
            raise ValueError("A server has that name")
        record = mcp_client.check_record(config)
        if record["kind"] == "docker":
            record["image_id"] = mcp_client.resolve_image(record["image"])
        secrets = secrets or {}
        if type(secrets) is not dict or any(type(value) is not str or not value for value in secrets.values()):
            raise ValueError("Give each secret a value")
        for variable in secrets:
            mcp_client.check_secret_name(variable)
        record["secrets"] = {variable:self.registry.keep_secret(value) for variable,value in secrets.items()}
        try:
            return self._read_mcp(name, record, ())
        except ValueError:
            for reference in record["secrets"].values():
                self.registry.drop_secret(reference)
            raise

    def mcp_secret(self, name, variable, value):
        """A person's step: adds or replaces one secret. The server is read again, so a wrong one shows now."""
        server, variable = self._mcp_server(name), mcp_client.check_secret_name(variable)
        old = server["secrets"].get(variable)
        reference = self.registry.keep_secret(value)
        try:
            kept = [tool["tool"] for tool in server["tools"] if tool["read_only"]]
            result = self._read_mcp(name, {**server,"secrets":{**server["secrets"],variable:reference}}, ())
        except ValueError:
            self.registry.drop_secret(reference)
            raise
        if old:
            self.registry.drop_secret(old)
        return self._marked_if_same(name, server, result, kept)

    def mcp_refresh(self, name):
        """A person's step. Reads the tools again. A tool whose text changed, or any tool of an image that changed, needs marking again."""
        server = self._mcp_server(name)
        kept = [tool["tool"] for tool in server["tools"] if tool["read_only"]]
        fresh = dict(server)
        if server["kind"] == "docker":
            fresh["image_id"] = mcp_client.resolve_image(server["image"])
        return self._marked_if_same(name, server, self._read_mcp(name, fresh, ()), kept)

    def _marked_if_same(self, name, before, after, kept):
        if mcp_client.pin(before) != mcp_client.pin(after):
            return after
        old = {tool["tool"]:tool for tool in before["tools"]}
        return self.mcp_mark(name, [tool["tool"] for tool in after["tools"] if tool["tool"] in kept and all(tool[key] == old[tool["tool"]][key] for key in mcp_client.TOOL_FIELDS)])

    def mcp_mark(self, name, read_only):
        """A person's step: only they can say a tool changes nothing."""
        server = self._mcp_server(name)
        known = {tool["tool"] for tool in server["tools"]}
        if type(read_only) is not list or any(tool not in known for tool in read_only):
            raise ValueError("Choose tools this server offers")
        self.store.save("mcp_server", {**server,"tools":[{**tool,"read_only":tool["tool"] in read_only} for tool in server["tools"]]})
        return self._mcp_server(name)

    def mcp_remove(self, name):
        server = self._mcp_server(name)
        for reference in server["secrets"].values():
            self.registry.drop_secret(reference)
        self.store.delete("mcp_server", name)
        return {"removed":name}

    def mcp_choices(self):
        """What a spec may choose, as `server/tool`. Commands, images and addresses stay out: an agent reads this."""
        return [{"id":f"{server['id']}/{tool['tool']}","server":server["id"],"tool":tool["tool"],"name":mcp_client.agent_name(server["id"],tool["tool"]),
                 "description":tool["description"],"read_only":tool["read_only"]} for server in self.mcp_servers() for tool in server["tools"]]

    def _spec_mcp(self, chosen):
        """Definitions for a spec, from `server/tool` ids."""
        if type(chosen) is not list or any(type(item) is not str for item in chosen):
            raise ValueError("Choose MCP tools by their server/tool id")
        known = {item["id"]:item for item in self.mcp_choices()}
        found = []
        for item in sorted(set(chosen)):
            if item not in known:
                raise ValueError(f"No connected MCP tool is called {item}")
            if not known[item]["read_only"]:
                raise ValueError(f"{item} is not marked read-only; a person marks that in Settings")
            server = self._mcp_server(known[item]["server"])
            found.append(mcp_client.definition(server["id"], server, next(tool for tool in server["tools"] if tool["tool"] == known[item]["tool"])))
        return found

    def mcp_run(self, spec, limits):
        """A run's connections, or None. Previews answer from doubles, so they start nothing."""
        chosen = tasks.spec_mcp(spec)
        if spec.get("mode") != "live" or not chosen:
            return None
        servers = {entry["server"]:self._mcp_server(entry["server"]) for entry in chosen}
        return mcp_client.McpRun(chosen, servers, limits["mcp_calls"], {name:self._mcp_secrets(server) for name,server in servers.items()})

    def _tool_choices(self, draft):
        """What the Spec tab lists: this task's model tools, and which of them it may leave out."""
        needed = tasks.required_tools(draft.get("task"))
        return [{"id":m["id"],"name":m["name"],"enabled":m["enabled"],"required":m["id"] in needed}
                for tool in (tasks.MODEL_TOOLS if tasks.uses_tools(draft.get("task")) else ())
                for m in self.registry.list_models() if m["id"] == tool]

    def _hidden_limits(self, draft):
        """Limits that do nothing for this spec."""
        unused = [] if tasks.spec_mcp(draft) else ["mcp_calls"]
        if not tasks.uses_tools(draft.get("task")):
            return unused+(list(run_limits.PHOTO_ONLY) if tasks.is_text(draft.get("task")) else [])
        return unused+([] if tasks.SEGMENTATION in tasks.spec_tools(draft) else ["segment_calls"])

    # Workspaces

    def workspace(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Invalid project identity")
        entry = self.store.get("workspace", identifier)
        if entry.get("deleting"):
            raise ValueError("This workspace is being deleted")
        workspace = Workspace.open(entry["path"])
        if workspace.project()["id"] != identifier:
            raise ValueError("That folder now holds a different project")
        return workspace

    def _remember(self, workspace):
        identifier = workspace.project()["id"]
        try:
            previous = self.store.get("workspace", identifier)
        except ValueError:
            previous = {}
        if previous.get("deleting"):
            raise ValueError("This workspace is being deleted")
        self.store.save("workspace", {**previous, "id": identifier, "path": str(workspace.folder)})
        return self._view(workspace)

    def _require_active_workspace(self, identifier):
        entry = self.store.get("workspace", identifier)
        if entry.get("deleting"):
            raise ValueError("This workspace is being deleted")
        if entry.get("archived"):
            raise ValueError("Restore this archived workspace before starting work")

    def archive_workspace(self, identifier, archived=True):
        """Person-facing archive/restore; keeps files, spend and approvals."""
        if type(archived) is not bool:
            raise ValueError("Choose archive or restore")
        workspace = self.workspace(identifier)
        self._jobs(workspace)
        with workspace.lock():
            if self.store.get("workspace", identifier).get("deleting"):
                raise ValueError("This workspace is being deleted")
            if any(j["status"] in {"queued", "running"} for j in workspace.runs()):
                raise ValueError("Stop the active run before archiving or restoring this workspace")
            if any(row["status"] in {"reserved", "unknown"} for row in usage_rows(self._ledger(workspace))):
                raise ValueError("Resolve unsettled calls before archiving or restoring this workspace")
            self.store.save("workspace", {**self.store.get("workspace", identifier), "archived": archived})
        return self._view(workspace)

    def delete_workspace(self, identifier, confirmation=None, *, by="person"):
        """Delete LX's own directory only; original samples and user-level billing stay."""
        if by not in {"person", "agent"}:
            raise ValueError("Unknown deletion caller")
        if by == "agent":
            self._require_agent_workspace_deletion()
        workspace = self.workspace(identifier)
        self._jobs(workspace)
        with workspace.lock():
            if by == "agent":
                self._require_agent_workspace_deletion()
            entry = self.store.get("workspace", identifier)
            if entry.get("deleting"):
                raise ValueError("This workspace is being deleted")
            record = workspace.project()
            if confirmation is not None and confirmation != record["name"]:
                raise ValueError("Type the workspace name to confirm deletion")
            if any(j["status"] in {"queued", "running"} for j in workspace.runs()):
                raise ValueError("Stop the active run before deleting this workspace")
            target = workspace.home
            if target.is_symlink() or getattr(target, "is_junction", lambda: False)() or target.resolve() != workspace.folder/".lx":
                raise ValueError("Refusing to delete outside this workspace's LX folder")
            self.store.save("workspace", {**entry, "deleting": True})
        # The lock file must be closed before moving the directory on Windows.
        stage = workspace.folder/(".lx-delete-"+uuid.uuid4().hex)
        try:
            os.replace(target, stage)
        except OSError as error:
            self.store.save("workspace", entry)
            raise ValueError("Windows could not remove the workspace; close files in it and try again") from error
        # Both resolved targets are fixed children of this workspace, never its samples directory.
        if stage.parent.resolve() != workspace.folder or not stage.name.startswith(".lx-delete-"):
            raise ValueError("Invalid LX cleanup directory")
        cleanup_pending = False
        try:
            shutil.rmtree(stage)
        except OSError:
            cleanup_pending = True
        self.store.save("deleted_workspace", {"id":identifier,"name":record["name"],"deleted_at":time.time()})
        for kind in ("workspace", "approval", "grant", "notice"):
            self.store.delete(kind, identifier)
        result = {"deleted": True, "samples_preserved": True, "spend_records_preserved": True}
        if cleanup_pending:
            result["cleanup_pending"] = str(stage)
        return result

    def _deleted_workspace_ledger(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Invalid workspace identity")
        self.store.get("deleted_workspace", identifier)
        return BudgetLedger(self.root/"ledgers"/f"{identifier}.sqlite3", self._workspace_budget(identifier), 0, adopt_cap=True)

    def release_deleted_workspace_calls(self, identifier, charged):
        """A person's later billing decision; workspace deletion makes no billing decision."""
        if type(charged) is not bool:
            raise ValueError("Say whether the provider billed these calls")
        ledger = self._deleted_workspace_ledger(identifier)
        for row in usage_rows(ledger):
            if row["status"] in {"reserved", "unknown"}:
                ledger.release(row["id"], charged=charged)
        return {"usage":summarize(usage_rows(ledger))}

    def _view(self, workspace):
        record = workspace.project()
        draft = workspace.spec(record["spec_version"])
        return {**record, "archived": self.store.get("workspace", record["id"]).get("archived", False),
                "path": str(workspace.folder), "draft": {**draft, "limits": self.limits_of(draft)},
                "limit_defaults": self._limit_defaults(draft["providers"], draft.get("task")),
                "hidden_limits": self._hidden_limits(draft),
                "tool_choices": self._tool_choices(draft),
                "mcp_choices": [item for item in self.mcp_choices() if item["read_only"]] if draft.get("task") else [],
                "free_providers": sorted({p["name"] for p in draft["providers"].values()
                                          if not p["priced"] or (p["pricing"] and not any(float(price) for price in p["pricing"].values()))}),
                "spec_versions": workspace.spec_versions(), "approved": self._approval(record["id"]),
                "notice": self.data_notice(draft),
                "hint_drift": self.hint_drift(workspace, draft), "eval_drift": self.eval_drift(workspace, draft),
                "external_orchestrator": draft["roles"]["orchestrator"] == EXTERNAL,
                "notice_accepted": self._notice_hosts(record["id"]),
                "blocked": self._blocked(draft),
                "grant": self._grant_view(workspace, record["id"]),
                "trial_cap_usd": self.trial_cap(record["id"], draft),
                "judge": self._judge_view(draft),
                "budget": {key:value for key,value in self._ledger(workspace).summary().items()
                           if key in {"cap_usd","spent_usd","reserved_usd","remaining_usd"}}}

    def project(self, identifier):
        return self._view(self.workspace(identifier))

    def open_workspace(self, path):
        return self._remember(Workspace.open(native_path(path)))

    def create_project(self, data, zip_data=None):
        folder = native_path(data.get("path"))
        if not folder.is_dir():
            raise ValueError("The workspace folder does not exist")
        if (folder/".lx").exists():
            raise ValueError("This folder is already a workspace; open it instead")
        source = data.get("source", "folder")
        task = check = None
        if data.get("task") == "inventory":
            task, check = tasks.inventory_task(), tasks.inventory_check_source()
        elif data.get("task") == "records":
            task, check = text_task.records_task(), text_task.records_check_source()
        elif data.get("task") is not None:
            raise ValueError("Unknown task")
        if source == "demo" and task is not None:
            if (folder/SAMPLES).exists():
                raise IntakeError("destination already exists")
            if tasks.is_text(task):
                text_task.write_samples(folder/SAMPLES, task["stream_samples"])
            else:
                write_synthetic(folder/SAMPLES, task["stream_samples"], seed=tasks.PREVIEW_SEED)
            samples = SAMPLES
        elif source == "demo":
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
            inventory = inspect_folder(location, skip_root=NOT_SAMPLES, max_bytes=tasks.MAX_SAMPLE_BYTES if task else MAX_BYTES)
        except IntakeError as exc:
            raise ValueError(f"{exc}. Put your samples in a '{SAMPLES}' folder inside the workspace") from exc
        workspace = Workspace.create(folder, name=str(data.get("name") or folder.name or "Workspace")[:120],
            goal=data.get("goal") or ("Normalize noisy order records into strict JSON." if tasks.is_text(task) else
                                      "Answer spoken inventory requests about photos." if task else
                                      "Find colored shapes, give pixel boxes, and preserve each transcript."),
            samples_dir=samples, inventory=inventory, compatible_demo=task is None and self._matches_demo(location))
        with workspace.lock():
            self._draft(workspace, workspace.project(), task=task, check=check)
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

    def _notice_hosts(self, identifier):
        try:
            return self.store.get("notice", identifier)["hosts"]
        except ValueError:
            return []

    def _accept_notice(self, identifier, draft, accepted):
        """Once per workspace, and again only when a new host would receive data."""
        hosts = sorted({entry["host"] for entry in self.data_notice(draft)})
        known = self._notice_hosts(identifier)
        if set(hosts) <= set(known):
            return
        if accepted is not True:
            raise ValueError("Accept the notice of which provider receives what before a live run")
        self.store.save("notice", {"id":identifier,"hosts":sorted(set(known)|set(hosts))})

    def _approval(self, identifier):
        try:
            return self.store.get("approval", identifier)
        except ValueError:
            return None

    def _draft(self, workspace, record, goal=None, budget=1, mode=None, roles=None, task=None, check=None, limits=None, tools=None, mcp_tools=None,
               hints=None, take_hint_files=False, no_check=False, judge=None, no_judge=False, tell_improver_worker=None):
        """Write the next spec version; any new version clears approval.

        `limits` is the user's own values, replacing any earlier ones. Everything
        else follows the providers' defaults, so changing a provider moves those."""
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
        task = task if task is not None else previous.get("task")
        providers = self._assign(roles, mode, task)
        resolved, own = run_limits.resolve(self._limit_defaults(providers, task),
                                           limits if limits is not None else previous.get("limits_set", {}))
        goal = goal if goal is not None else record["goal"]
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 4000:
            raise ValueError("Enter a goal of at most 4000 characters")
        versions = workspace.spec_versions()
        version = (versions[-1] if versions else 0)+1
        reference, source = (None if no_check else previous.get("check")), None
        if task is not None and check is not None:
            tasks.validate_task(task)
            source = tasks.validate_check_source(check)
            reference = {"version":version,"sha256":tasks.source_hash(source)}
        spec = {"version": version, "goal": goal.strip(), "budget_usd": budget,
                "roles": {r:roles[r] for r in ROLES}, "providers": providers,
                "mode": mode, "limits": resolved, "limits_set": own}
        if tasks.uses_tools(task):
            if tools is None:
                on = [m["id"] for m in self.registry.list_models() if m["enabled"] and m["id"] in tasks.MODEL_TOOLS]
                tools = previous.get("tools", list(tasks.MODEL_TOOLS) if previous else on+list(tasks.required_tools(task)))
            spec["tools"] = tasks.check_tools(task, tools)
        chosen = tasks.spec_mcp(previous) if mcp_tools is None else self._spec_mcp(mcp_tools)
        if chosen and task is None:
            raise ValueError("Only a task can use MCP tools")
        if chosen:
            spec["mcp_tools"] = chosen
        kept = self._hints(workspace, task, previous, hints, take_hint_files)
        if kept:
            spec["hints"] = kept
        judged = self._judge(task, previous, judge, no_judge)
        if judged:
            spec["judge"] = judged
        told = previous.get("tell_improver_worker", False) if tell_improver_worker is None else tell_improver_worker
        if type(told) is not bool:
            raise ValueError("tell_improver_worker is true or false")
        if told and task is None:
            raise ValueError("Only a task can tell the improver about the worker")
        if told:
            spec["tell_improver_worker"] = True  # left out when off, so older specs and approvals still match
        spec.update({"task":task,"check":reference} if task is not None else {
                "domain": "images-and-supplied-transcripts", "output": {"objects": [{"label":"color_shape", "box":["x1","y1","x2","y2"]}], "text":"exact supplied transcript"},
                "box_edge_tolerance_px": 1})
        spec["estimated_cost_usd"] = ({"basis":"Live cost reserved before each call under the workspace budget; actual cost from reported usage at the entry's prices, unknown until reported"}
            if mode == "live" else {"orchestrator":0.00002,"improver":0.0003,"worker":0.0003,"basis":"Approximate controlled preview only; live cost unknown"})
        if source is not None:
            workspace.add_check(version, source)  # only once nothing above can refuse, so a refused draft leaves no file to block the next
        workspace.add_spec(spec)
        record.update(goal=goal.strip(), spec_version=spec["version"])
        workspace.write_project(record)
        self._drop_approval(workspace, record["id"])

    def _hints(self, workspace, task, previous, given, take_files):
        """The spec's improver hints: what was given (or kept), seeded from the sample files where blank. Files win
        only when asked. A hint that carries box coordinates or an expected total is refused."""
        hints = previous.get("hints") if given is None else hint_notes.clean(given)
        if task is None:
            if hints:
                raise ValueError("Only a task can have improver hints")
            return None
        files = hint_notes.from_files(workspace.samples_path)
        if take_files and files:
            hints = hint_notes.clean({"all": files["all"] or (hints or {}).get("all"), "samples": {**(hints or {}).get("samples", {}), **files["samples"]}})
        else:
            hints = hint_notes.fill_blanks(hints, files)
        if hints:
            try:
                truths = tasks.sample_truths(workspace.samples_path, task)
            except ValueError:
                truths = {}
            hint_notes.refuse_answers(hints, truths)
            if any(tasks.quotes_answers(text, truths) for text in ([hints["all"]] if hints["all"] else []) + list(hints["samples"].values())):
                raise ValueError("A hint repeats answers from the samples. Hints describe how the task is done, never the answers")
        return hints

    def _judge(self, task, previous, given, remove):
        """The spec's judge: what was given, laid over what it had, or none when removed."""
        if remove:
            if given is not None:
                raise ValueError("Give judges or remove them, not both")
            return None
        if given is None:
            return previous.get("judge") if task is not None else None
        if task is None:
            raise ValueError("Only a task can have model judges")
        if type(given) is not dict:
            raise ValueError("Judges are an object with criteria")
        held = previous.get("judge") or {}
        unknown = set(given)-set(judging.FIELDS)
        if unknown:
            raise ValueError("Judges take criteria, sees_request, entry and cap_usd")
        judge = {**held, **judging.check_written({**(judging.written(held) or {}), **{k: v for k, v in given.items() if k in judging.WRITTEN}},
                                                 text_task=tasks.is_text(task))}
        if "cap_usd" in given:
            judge["cap_usd"] = None if given["cap_usd"] is None else judging.check_cap(given["cap_usd"])
        if "entry" in given:
            judge["entry"] = given["entry"]
        if judge.get("entry"):
            judge["provider"] = self._provider(judge["entry"])  # bound by approval, as each role's is
        else:
            judge.pop("provider", None)
        return judge

    def hint_drift(self, workspace, draft):
        """Where a hint file differs from the spec's hints; approval waits until this is settled."""
        return hint_notes.drift(draft.get("hints"), hint_notes.from_files(workspace.samples_path)) if draft.get("task") else []

    def set_task(self, identifier, task, check):
        """An orchestrator's task and check, as a new spec version; a human still approves it."""
        tasks.validate_task(task)
        tasks.validate_check_source(check)
        workspace = self.workspace(identifier)
        self._jobs(workspace)
        with workspace.lock():
            if any(j["status"] in {"queued","running"} for j in workspace.runs()):
                raise ValueError("Stop the active run before editing its spec")
            record = workspace.project()
            draft = workspace.spec(record["spec_version"])
            self._draft(workspace, record, budget=draft["budget_usd"], task=task, check=check)
        return self._view(workspace)

    def draft_spec(self, identifier, *, goal=None, mode=None, roles=None, task=None, check=None, limits=None, tools=None, mcp_tools=None,
                   hints=None, take_hint_files=False, no_check=False, judge=None, no_judge=False, tell_improver_worker=None):
        """An orchestrator's changes, as a new spec version. The run budget stays what a person set."""
        workspace = self.workspace(identifier)
        self._jobs(workspace)
        with workspace.lock():
            if any(j["status"] in {"queued","running"} for j in workspace.runs()):
                raise ValueError("Stop the active run before editing its spec")
            record = workspace.project()
            draft = workspace.spec(record["spec_version"])
            if (task is not None or check is not None or no_check) and not draft.get("task"):
                raise ValueError("This workflow has a built-in task and check")
            if no_check and check is not None:
                raise ValueError("Give a check or run with none, not both")
            if draft.get("task"):
                task = tasks.validate_task({**draft["task"], **(task or {})})
                if check is not None:
                    check = tasks.validate_check_source(check)
                elif draft.get("check") and not no_check:
                    check = workspace.check_source(draft["check"])
            self._draft(workspace, record, goal, draft["budget_usd"], mode, roles, task=task, check=check, limits=limits, tools=tools, mcp_tools=mcp_tools,
                        hints=hints, take_hint_files=take_hint_files, no_check=no_check, judge=judge, no_judge=no_judge,
                        tell_improver_worker=tell_improver_worker)
        return self._view(workspace)

    def eval_drift(self, workspace, draft):
        """Where the check or judges in eval/ differ from the spec's; approval waits until this is settled, so a file is never mistaken for what runs."""
        if not draft.get("task"):
            return []
        return self._check_drift(workspace, draft)+self._judge_drift(workspace, draft)

    @staticmethod
    def _judge_drift(workspace, draft):
        try:
            text = workspace.eval_judges()
            if text is None or judging.read_written(text, text_task=tasks.is_text(draft["task"])) == judging.written(draft.get("judge")):
                return []
        except ValueError as exc:
            return [str(exc)]
        return ["eval/judges.json differs from the spec's judges" if draft.get("judge") else "eval/judges.json is not in use, because this spec has no judges"]

    def eval_judges(self, identifier):
        """The judges kept in eval/judges.json."""
        workspace = self.workspace(identifier)
        text = workspace.eval_judges()
        if text is None:
            raise ValueError("There are no judges in eval/: add eval/judges.json")
        return judging.read_written(text, text_task=tasks.is_text(workspace.spec(workspace.project()["spec_version"]).get("task")))

    def _check_drift(self, workspace, draft):
        try:
            found = workspace.eval_file()
            if found is None:
                return []
            if not draft.get("check"):
                return [f"eval/{found[0]} is not in use, because this spec has no check"]
            if found[1].strip() == check_forms.author_text(workspace.check_source(draft["check"])).strip():
                return []
        except ValueError as exc:
            return [str(exc)]
        return [f"eval/{found[0]} differs from the spec's check"]

    def draft_rules(self, identifier):
        """Rules to start from, drawn from the shape of this workspace's answers; saves nothing."""
        workspace = self.workspace(identifier)
        draft = workspace.spec(workspace.project()["spec_version"])
        if not draft.get("task"):
            raise ValueError("This workflow has a built-in check")
        return check_forms.starting_rules(tasks.truth_shape(tasks.sample_truths(workspace.samples_path, draft["task"]))["shape"])

    def eval_init(self, identifier):
        """Write the starting rules to eval/rules.json. They take effect only when a spec takes them, and a file is never overwritten."""
        workspace = self.workspace(identifier)
        if workspace.eval_file():
            raise ValueError("There is already a check in eval/")
        return {"file": workspace.write_eval_rules(self.draft_rules(identifier))}

    def eval_check(self, identifier):
        """The text of the check kept in eval/."""
        found = self.workspace(identifier).eval_file()
        if found is None:
            raise ValueError("There is no check in eval/: add eval/rules.json or eval/check.py")
        return found[1]

    def test_check(self, identifier, check=None):
        """Self-test the current check, or a draft's text, on the samples as they are now; changes nothing."""
        workspace = self.workspace(identifier)
        draft = workspace.spec(workspace.project()["spec_version"])
        if not draft.get("task"):
            raise ValueError("This workflow has a built-in check")
        return checks.self_test(self._check_for(workspace, draft, check), tasks.sample_truths(workspace.samples_path, draft["task"]))

    def _check_for(self, workspace, draft, check):
        """A draft's text, or the spec's own check."""
        if check is not None:
            return tasks.validate_check_source(check)
        if not draft.get("check"):
            raise ValueError("This spec has no check; give one to test")
        return workspace.check_source(draft["check"])

    def try_check(self, identifier, check=None):
        """Score saved answers with the current check, or a draft's text, and say what changes; changes nothing."""
        workspace = self.workspace(identifier)
        draft = workspace.spec(workspace.project()["spec_version"])
        if not draft.get("task"):
            raise ValueError("This workflow has a built-in check")
        from .taskrun import try_check
        return try_check(workspace, self._check_for(workspace, draft, check))

    def try_judges(self, identifier, judges=None, *, spend=None):
        """Judge saved answers with the spec's judges, or with draft criteria laid over them, and say what changes; changes no spec.

        Only a person can spend: `spend` is {"cap_usd", "notice_accepted", "contributor_accepted"}. Without it, only
        verdicts already kept count, and any other answer is left not judged, so an agent's try costs nothing."""
        from .taskrun import _NoCalls, try_judges
        if spend is not None:
            self._require_active_workspace(identifier)
        workspace = self.workspace(identifier)
        draft = workspace.spec(workspace.project()["spec_version"])
        if not draft.get("task"):
            raise ValueError("This workflow has a built-in check")
        judge = self._judge(draft["task"], draft, judges, False) if judges is not None else draft.get("judge")
        if not judge:
            raise ValueError("This spec has no judges; give some to try")
        if not judge.get("entry"):
            raise ValueError("Choose a model for the judges first")
        live = draft["mode"] == "live"
        spec = {**draft, "judge": judge}
        if spend is None or not live:
            client = _NoCalls() if live else self._judge_client(workspace, spec, "judge-try-"+uuid.uuid4().hex[:12], 1)
        else:
            cap = judging.check_cap(spend.get("cap_usd"))
            if cap < self.judge_call_usd(spec):
                raise ValueError(f"A cap of ${cap:.2f} is less than one judge call holds while it runs (${self.judge_call_usd(spec):.4f})")
            host = urlsplit(judge["provider"]["base_url"]).netloc
            if host not in self._notice_hosts(identifier):
                if spend.get("notice_accepted") is not True:
                    raise ValueError(f"Accept that the judge at {host} receives the saved answers, their expected answers"+(" and the requests" if judge["sees_request"] else ""))
                self.store.save("notice", {"id":identifier,"hosts":sorted(set(self._notice_hosts(identifier))|{host})})
            if judge["entry"] == DEFAULT_ROLE_ENTRY and spend.get("contributor_accepted") is not True:
                raise ValueError("Accept that Spark Contributor may use what it judges for training")
            client = self._judge_client(workspace, spec, "judge-try-"+uuid.uuid4().hex[:12], cap)
        found = judging.Judge(judge, client, workspace.home/"judge-cache", live=live, timeout=max(120, self.limits_of(spec)["call_seconds"]))
        return try_judges(workspace, found, reasons=spend is not None)

    def ready(self, identifier):
        """Whether the current spec could run live on the samples as they are now. No model and no network:
        it says this can run, never that it is good. Works on any samples, unlike a preview."""
        workspace = self.workspace(identifier)
        draft = workspace.spec(workspace.project()["spec_version"])
        if not draft.get("task"):
            raise ValueError("This workflow has a built-in task and check")
        need_truth = tasks.needs_truth(draft)
        problems, check, blocked = tasks.sample_problems(workspace.samples_path, draft["task"], need_truth=need_truth), None, None
        problems += [{"sample":None,"problem":text+"; save the spec with the hint files to take them, or change the file"} for text in self.hint_drift(workspace, draft)]
        problems += [{"sample":None,"problem":text+"; save the spec with the eval file to take it, or change the file"} for text in self.eval_drift(workspace, draft)]
        if not problems:
            try:
                with tempfile.TemporaryDirectory() as folder:
                    tasks.build_dataset(workspace.samples_path, Path(folder)/"dataset", draft["task"], preview=False, need_truth=need_truth)
                check = self.test_check(identifier) if draft.get("check") else None
            except ValueError as exc:
                problems.append({"sample":None,"problem":str(exc)})
        try:
            self._check_providers({**draft,"mode":"live"})
        except ValueError as exc:
            blocked = str(exc)
        return {"ready":not problems and not blocked,"problems":problems,"blocked":blocked,"check":check}

    def _drop_approval(self, workspace, identifier):
        approval = self._approval(identifier)
        if approval:
            self.store.delete("approval", identifier)
            shutil.rmtree(workspace.snapshot_dir(approval["snapshot"]), ignore_errors=True)

    # Discussion, spec and approval

    def discuss(self, identifier, text, consent, mode=None):
        self._require_active_workspace(identifier)
        if consent is not True:
            raise ValueError("Approve the discussion data disclosure first")
        if not isinstance(text,str) or not text.strip() or len(text)>4000:
            raise ValueError("Enter a short requirements message")
        workspace = self.workspace(identifier)
        project = self._view(workspace)
        if project["external_orchestrator"]:
            raise ValueError("A connected agent is this workspace's orchestrator; talk to it there")
        mode = mode or project["draft"].get("mode", "controlled_preview")
        from .orchestration import Orchestration
        from .orchestrator import TOOLS, converse
        tools = bool(project["draft"].get("task")) and project["draft"]["providers"]["orchestrator"]["supports_tools"]
        client = self.build_clients(workspace, {**project["draft"],"mode":mode}, "discussion-"+identifier, project["draft"]["budget_usd"],
                                    call_limit=DISCUSSION_CALL_LIMIT, contract={"agent_tools":TOOLS} if tools else {})["orchestrator"]
        reply = converse(Orchestration(self, external=False), str(workspace.folder), client, project, text, tools=tools)
        with workspace.lock():
            record = workspace.project()
            record["messages"].extend([{"role":"user","content":text},{"role":"assistant","content":reply or "(no reply)"}])
            workspace.write_project(record)
        return self._view(workspace)

    def edit_spec(self, identifier, data):
        workspace = self.workspace(identifier)
        self._jobs(workspace)  # reconcile first: the lock below is not re-entrant
        with workspace.lock():
            if any(j["status"] in {"queued","running"} for j in workspace.runs()):
                raise ValueError("Stop the active run before editing its spec")
            self._draft(workspace, workspace.project(), data.get("goal"), data.get("budget_usd",1), data.get("mode"), data.get("roles"),
                        limits=data.get("limits"), tools=data.get("tools"), mcp_tools=data.get("mcp_tools"),
                        hints=data.get("hints"), take_hint_files=data.get("take_hint_files") is True, no_check=data.get("no_check") is True,
                        judge=data.get("judge"), no_judge=data.get("no_judge") is True, tell_improver_worker=data.get("tell_improver_worker"))
        return self._view(workspace)

    def approve(self, identifier, data, by="person", grant=None):
        """A human step. Freezes the samples as they are now, whatever was seen earlier."""
        self._require_active_workspace(identifier)
        workspace = self.workspace(identifier)
        with workspace.lock():
            self._require_active_workspace(identifier)
            record = workspace.project()
            draft = workspace.spec(record["spec_version"])
            if data.get("version") != draft["version"] or data.get("data_approved") is not True:
                raise ValueError("Approve the current spec and its data snapshot")
            if DEFAULT_ROLE_ENTRY in self._entries(draft) and data.get("contributor_accepted") is not True:
                raise ValueError("Accept the Contributor disclosure for Spark")
            self._check_providers(draft)
            drifted = self.hint_drift(workspace, draft)
            if drifted:
                raise ValueError("Settle the hints before approving: "+"; ".join(drifted))
            drifted = self.eval_drift(workspace, draft)
            if drifted:
                raise ValueError("Settle the check before approving: "+"; ".join(drifted))
            if draft["mode"] == "live":
                if draft["budget_usd"] > self._workspace_budget(identifier):
                    raise ValueError("This run's budget is more than the workspace budget")
                self._accept_notice(identifier, draft, data.get("notice_accepted"))
            task = draft.get("task")
            limit = tasks.MAX_SAMPLE_BYTES if task else MAX_BYTES
            record.update(inventory=workspace.inspect_samples(limit),
                          compatible_demo=task is None and self._matches_demo(workspace.samples_path))
            workspace.write_project(record)
            if task is None and not record["compatible_demo"]:
                raise ValueError("This first workflow requires the constructed demo manifest and images; other inputs can be inventoried and discussed")
            self._drop_approval(workspace, identifier)
            name = uuid.uuid4().hex
            captured = snapshot_folder(workspace.samples_path, workspace.snapshot_dir(name), skip_root=NOT_SAMPLES, max_bytes=limit)
            try:
                tested = self._prove_task(workspace, draft, workspace.snapshot_dir(name)) if task else None
            except ValueError:
                shutil.rmtree(workspace.snapshot_dir(name), ignore_errors=True)
                raise
            self.store.save("approval", {"id":identifier,"version":draft["version"],"spec_hash":fingerprint(draft),
                "data_hash":fingerprint(captured["inventory"]),"inventory":captured["inventory"],"snapshot":name,"self_test":tested,"by":by,"grant":grant})
        return self._view(workspace)

    def approve_preview(self, identifier):
        """A preview runs on doubles: nothing leaves and nothing is spent, so it needs no person (D-024).
        The approval binds the spec, and the spec holds the mode, so it can never start a live run."""
        workspace = self.workspace(identifier)
        draft = workspace.spec(workspace.project()["spec_version"])
        if draft.get("mode") == "live":
            raise ValueError("This spec is a live run; only a person can approve it")
        if any(j["status"] in {"queued","running"} for j in self._jobs(workspace)):
            raise ValueError("One run is already active")
        return self.approve(identifier, {"version":draft["version"],"data_approved":True,"contributor_accepted":True}, by="preview")

    # Grants

    def _grant(self, identifier):
        try:
            return self.store.get("grant", identifier)
        except ValueError:
            return None

    def sample_files(self, workspace, draft):
        """The sample folder as it is now; a project's stored inventory dates from its last intake or approval."""
        return workspace.inspect_samples(tasks.MAX_SAMPLE_BYTES if draft.get("task") else MAX_BYTES)["inventory"]

    def _sample_hash(self, workspace, draft):
        return fingerprint(self.sample_files(workspace, draft))

    def set_grant(self, identifier, data):
        """A human step (D-024): lets a connected agent start live runs here, within a cap, an expiry and a run count.
        It holds what a person accepts at approval, for these samples, providers and tools; a change needs a person again."""
        self._require_active_workspace(identifier)
        workspace = self.workspace(identifier)
        draft = workspace.spec(workspace.project()["spec_version"])
        cap, days, runs = data.get("cap_usd"), data.get("days",GRANT_DAYS), data.get("runs",GRANT_RUNS)
        if data.get("data_approved") is not True:
            raise ValueError("Approve the data snapshot first")
        if DEFAULT_ROLE_ENTRY in self._entries(draft) and data.get("contributor_accepted") is not True:
            raise ValueError("Accept the Contributor disclosure for Spark")
        room = self._ledger(workspace).summary()["remaining_usd"]
        if isinstance(cap,bool) or not isinstance(cap,(int,float)) or not math.isfinite(cap) or not 0.11 <= cap <= room:
            raise ValueError(f"A grant's cap must be between $0.11 and the ${room:.2f} left in this workspace")
        for value,top,name in ((days,MAX_GRANT_DAYS,"days"),(runs,MAX_GRANT_RUNS,"runs")):
            if isinstance(value,bool) or not isinstance(value,int) or not 1 <= value <= top:
                raise ValueError(f"A grant's {name} must be from 1 to {top}")
        connected = {**draft,"roles":{**draft["roles"],"orchestrator":EXTERNAL}}
        self._accept_notice(identifier, connected, data.get("notice_accepted"))
        now = time.time()
        self.store.save("grant", {"id":identifier,"grant":uuid.uuid4().hex,"created_at":now,"expires_at":now+days*86400,"cap_usd":cap,"runs":runs,"revoked":False,
            "providers":{role:draft["providers"][role] for role in ("improver","worker")},"tools":self._granted_tools(draft),
            "hosts":sorted({entry["host"] for entry in self.data_notice(connected)}),"data_hash":self._sample_hash(workspace, draft)})
        return self._view(workspace)

    @staticmethod
    def _granted_tools(draft):
        return sorted(tasks.spec_tools(draft))+sorted(entry["name"] for entry in tasks.spec_mcp(draft))

    def revoke_grant(self, identifier):
        """Takes effect at the next start; a run already going keeps its own budget and can be stopped."""
        workspace = self.workspace(identifier)
        grant = self._grant(identifier)
        if grant:
            self.store.save("grant", {**grant,"revoked":True})
        return self._view(workspace)

    def _grant_view(self, workspace, identifier):
        """What is left of a grant, read from the runs and ledger, so a crash between steps cannot undercount it."""
        grant = self._grant(identifier)
        if grant is None:
            return None
        runs = [job["id"] for job in workspace.runs() if job.get("grant") == grant["grant"]]
        rows = usage_rows(self._ledger(workspace)) if runs else []
        used = sum(row["actual_nanos"] or 0 if row["status"] == "settled" else row["reserved_nanos"] for row in rows
                   if row["candidate"].startswith(tuple(job+"/" for job in runs)))
        return {key:grant[key] for key in ("cap_usd","runs","expires_at","revoked")} | {"runs_used":len(runs),"spent_usd":used/1_000_000_000}

    def _grant_refusal(self, workspace, draft, identifier, cost):
        """Why a run of this spec, costing at most `cost`, falls outside this workspace's grant."""
        grant, left = self._grant(identifier), self._grant_view(workspace, identifier)
        if grant is None:
            return "A person needs to approve this live run in the LX app"
        if grant["revoked"]:
            return "The grant was revoked; a person needs to approve this live run"
        if time.time() >= grant["expires_at"]:
            return "The grant has expired; a person needs to approve this live run"
        if left["runs_used"] >= grant["runs"]:
            return "The grant's runs are used up; a person needs to approve this live run"
        if left["spent_usd"]+cost > grant["cap_usd"]:
            return "This run's budget would pass the grant's spending cap; a person needs to decide"
        if {role:draft["providers"][role] for role in ("improver","worker")} != grant["providers"]:
            return "The improver or worker changed since the grant; a person needs to approve this live run"
        if self._granted_tools(draft) != grant["tools"]:
            return "The tools changed since the grant; a person needs to approve this live run"
        if not {entry["host"] for entry in self.data_notice(draft)} <= set(grant["hosts"]):
            return "This run would send data to a host the grant does not cover; a person needs to approve it"
        if self._sample_hash(workspace, draft) != grant["data_hash"]:
            return "The samples changed since the grant; a person needs to approve this live run"
        return None

    def start_granted(self, identifier, launch=True, trial=False, from_run=None, from_sample=None):
        """Approves and starts the current live spec, or a trial of it, under the grant, or says why a person must."""
        self._require_active_workspace(identifier)
        if (from_run is not None or from_sample is not None) and not trial:
            raise ValueError("A saved harness can only be used for a trial")
        if from_sample is not None and from_run is None:
            raise ValueError("A sample harness needs its source run")
        workspace = self.workspace(identifier)
        draft = workspace.spec(workspace.project()["spec_version"])
        if from_run is not None:
            self._trial_package(workspace, from_run, draft, from_sample)
        if any(j["status"] in {"queued","running"} for j in self._jobs(workspace)):
            raise ValueError("One run is already active")
        refusal = self._grant_refusal(workspace, draft, identifier, self.trial_cap(identifier, draft) if trial else draft["budget_usd"])
        if refusal:
            return {"status":"pending","reason":refusal}
        approved = self.approve(identifier, {"version":draft["version"],"data_approved":True,"contributor_accepted":True},
                                by="grant", grant=self._grant(identifier)["grant"])["approved"]
        weak = (approved["self_test"] or {}).get("weaknesses")
        if weak:  # no person reads the scores under a grant, so a weak check is theirs to judge
            self._drop_approval(workspace, identifier)
            return {"status":"pending","reason":"A person needs to review this check: "+weak[0]}
        return {"status":"started","run_id":self.start(identifier, launch=launch, trial=trial, from_run=from_run, from_sample=from_sample)["id"]}

    def approve_as_agent(self, identifier):
        """A connected agent approves the current live spec (D-026). It accepts the data notice and the Contributor
        disclosure for the person who connected it, and the record says so. Money stays with a person: the agent
        cannot set a budget, and approve refuses a run budget above the workspace's."""
        workspace = self.workspace(identifier)
        draft = workspace.spec(workspace.project()["spec_version"])
        if draft.get("mode") != "live":
            raise ValueError("This spec is a preview; use preview")
        if any(j["status"] in {"queued","running"} for j in self._jobs(workspace)):
            raise ValueError("One run is already active")
        approved = self.approve(identifier, {"version":draft["version"],"data_approved":True,"contributor_accepted":True,"notice_accepted":True},
                                by="agent")["approved"]
        weak = (approved["self_test"] or {}).get("weaknesses")
        if weak:  # the check decides what counts as a good run, so one that gives marks away waits for a person
            self._drop_approval(workspace, identifier)
            return {"status":"pending","reason":"A person needs to review this check: "+weak[0]}
        return {"status":"approved","version":draft["version"]}

    def _prove_task(self, workspace, draft, snapshot):
        """At approval: the frozen samples fit the task and the check proves itself on them."""
        task = draft["task"]
        truths = tasks.sample_truths(snapshot, task, need_truth=tasks.needs_truth(draft))
        if tasks.is_text(task):
            if draft["mode"] != "live":
                text_task.require_preview_samples(snapshot, len(truths))
        elif draft["mode"] == "live":
            tasks.require_audio(snapshot)
        else:
            tasks.require_preview_samples(snapshot, len(truths))
        judged = [JUDGED] if draft.get("judge") else []  # a model's verdicts are a person's to accept, as a weak check is
        if not draft.get("check"):
            return {"no_check": True, "weaknesses": judged or [NO_CHECK]}  # a weakness waits for a person, so no agent or grant starts this alone
        proven = checks.self_test(workspace.check_source(draft["check"]), truths)
        return {**proven, "weaknesses": proven["weaknesses"]+judged}

    # Runs

    def _trial_package(self, workspace, run_id, draft, from_sample=None):
        """The harness a finished run left, if a trial of this spec can fairly use it. The worker model and the
        check may differ, which is how a harness gets retested; the task and the agent's tools may not."""
        source = workspace.run(run_id)
        if source["status"] != "completed" or source.get("kind") == "trial" or source["spec"].get("mode") != "live" or not source["spec"].get("task"):
            raise ValueError("Only a finished live run left a harness to try")
        if from_sample is None and source["result"].get("package_is_base"):
            raise ValueError("That run's package is the base harness; start a plain trial")
        if (source["spec"]["task"], tasks.spec_tools(source["spec"]), tasks.spec_mcp(source["spec"])) != (draft["task"], tasks.spec_tools(draft), tasks.spec_mcp(draft)):
            raise ValueError("That harness was learned on another task or tool list than this spec")
        if from_sample is not None:
            from .taskrun import _folders
            run_dir = workspace.run_dir(run_id)
            report_file = run_dir/"outputs"/"ea_report.json"
            if not isinstance(from_sample, str) or not report_file.is_file():
                raise ValueError("Choose a sample folder from that finished run")
            report = json.loads(report_file.read_text(encoding="utf-8"))
            sample = next((key for key, folder in _folders(run_dir).items() if folder == from_sample), None)
            row = report.get("per_sample", {}).get(sample, {})
            if not row.get("harness_dir"):
                raise ValueError("No saved harness for that sample folder in this run")
            root = run_dir/"outputs"/"runs"
            target = root
            for part in PureSafe(row["harness_dir"]):
                target = target/part
                if target.is_symlink():
                    raise ValueError("Linked trial harness rejected")
            if not target.is_dir():
                raise ValueError("The saved sample harness is missing")
            return target
        return workspace.package_dir(run_id)/"harness"

    def start(self, identifier, launch=True, trial=False, from_run=None, from_sample=None):
        workspace = self.workspace(identifier)
        self._jobs(workspace)
        with workspace.lock():
            self._require_active_workspace(identifier)
            record = workspace.project()
            draft = workspace.spec(record["spec_version"])
            if trial and (draft.get("mode") != "live" or not draft.get("task")):
                raise ValueError("A trial tries a task's live spec on one sample")
            if (from_run is not None or from_sample is not None) and not trial:
                raise ValueError("A saved harness can only be used for a trial")
            if from_sample is not None and from_run is None:
                raise ValueError("A sample harness needs its source run")
            package = self._trial_package(workspace, from_run, draft, from_sample) if from_run is not None else None
            approved = self._approval(identifier)
            if not approved or approved["spec_hash"] != fingerprint(draft):
                raise ValueError("Approve the current spec before starting")
            self._check_providers(draft)
            if any(j["status"] in {"queued","running"} for j in workspace.runs()):
                raise ValueError("One run is already active")
            job_id = uuid.uuid4().hex[:12]
            inputs = workspace.run_dir(job_id)/"snapshot"
            captured = snapshot_folder(workspace.snapshot_dir(approved["snapshot"]),inputs,max_bytes=tasks.MAX_SAMPLE_BYTES)
            if captured["inventory"] != approved["inventory"]:
                shutil.rmtree(workspace.run_dir(job_id))
                raise ValueError("Snapshot changed since approval; approve again")
            if package:
                shutil.copytree(package, workspace.run_dir(job_id)/"harness")
            job = {"id":job_id,"project_id":identifier,"name":record["name"],"status":"queued","phase":"queued","created_at":time.time(),
                   "mode":draft.get("mode","controlled_preview"),"spec":draft,"approved":_approval_identity(approved),"approved_by":approved.get("by","person"),"grant":approved.get("grant"),"launch_pending":True,
                   **({"kind":"trial","cap_usd":self.trial_cap(identifier, draft)} if trial else {}),**({"from_run":from_run} if package else {}),
                   **({"from_sample":from_sample} if from_sample is not None else {})}
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
        launcher.start([sys.executable,"-m","lx.launcher",sys.executable,"-m","lx.worker","--root",str(self.root),
                        "--workspace",str(workspace.folder),"--job",identifier]).wait()

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
        """A run keeps the limits it was approved with; raised limits are a new version and a new run."""
        workspace = self.workspace(identifier)
        self._jobs(workspace)
        with workspace.lock():
            self._require_active_workspace(identifier)
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

    def finish_run(self, workspace, job, result):
        """Publish a run's package and mark it completed."""
        identifier, live = job["id"], job["spec"].get("mode") == "live"
        output = workspace.run_dir(identifier)/"outputs"
        result["usage"] = summarize(usage_rows(self._ledger(workspace) if live else workspace.preview_ledger,identifier+"/"))
        result["usage_basis"] = ("live usage at the prices in each provider entry; not provider billing"
                                 if live else "simulated provider usage; no live calls or charges")
        result["package_version"] = identifier+"-spec-v"+str(job["spec"]["version"])
        write_json(output/"package"/"usage.json",result["usage"])
        write_json(output/"package"/"result.json",result)
        workspace.publish_package(identifier)
        workspace.update_run(identifier,status="completed",phase="complete",result=result,error=None)
        return result

    def run_edits(self, identifier, job_id):
        """What the improver has written in a run so far. Works during the run."""
        from .taskrun import harness_edits, token_report
        workspace = self.workspace(identifier)
        job = workspace.run(job_id)
        edits = harness_edits(workspace.run_dir(job_id))
        ledger = self._ledger(workspace) if job["spec"].get("mode") == "live" else workspace.preview_ledger
        return {"status":job["status"],"package_sample":(job.get("result") or {}).get("package_sample"),**edits,
                "tokens":token_report(usage_rows(ledger,job_id+"/"),edits["samples"])}

    def finish_trial(self, workspace, job, result):
        """A trial ends in its result alone: nothing was learned, so there is no package."""
        result["usage"] = summarize(usage_rows(self._ledger(workspace),job["id"]+"/"))
        workspace.update_run(job["id"],status="completed",phase="complete",result=result,error=None)
        return result

    def regrade(self, identifier, job_id):
        """Score a failed task run from its saved answers; no model or tool calls."""
        from .taskrun import regrade
        workspace = self.workspace(identifier)
        self._jobs(workspace)
        job = workspace.run(job_id)
        if job["status"] != "failed" or not job["spec"].get("task") or job.get("kind") == "trial":
            raise ValueError("Only a failed task run can be graded again")
        if not job["spec"].get("check") and not job["spec"].get("judge"):
            raise ValueError("This run has no check, so there is nothing to grade")
        try:
            result = regrade(self, workspace, job, live=job["spec"].get("mode") == "live")
        except (OSError, KeyError):
            raise ValueError("This run's loop did not finish, so there is nothing to grade; resume it instead") from None
        result["regraded"] = True
        self.finish_run(workspace, job, result)
        return workspace.run(job_id)

    def release_held(self, identifier, job_id, charged):
        """A human's step after checking the provider's bill: ends a stopped run's held calls."""
        if type(charged) is not bool:
            raise ValueError("Say whether the provider billed these calls")
        workspace = self.workspace(identifier)
        self._jobs(workspace)
        job = workspace.run(job_id)
        if job["status"] in {"queued","running"}:
            raise ValueError("Stop the run before releasing its held calls")
        ledger = self._ledger(workspace) if job["spec"].get("mode") == "live" else workspace.preview_ledger
        for row in usage_rows(ledger, job_id+"/"):
            if row["status"] in {"reserved","unknown"}:
                ledger.release(row["id"], charged=charged)
        return self._view(workspace)

    def provider_models(self, base_url, api_key=None, key_from=None):
        """What a provider lists, for the add form to fill in. Calls no model and spends nothing.

        `key_from` names a model already on this provider, whose stored key is used and never returned."""
        if self._model_list is None and not self.live_enabled:
            raise ValueError("Live runs are unavailable here; use the local app workspace")
        if key_from:
            api_key = self.registry.sibling_key(key_from, base_url)
        try:
            return {"models":(self._model_list or read_models)(base_url, api_key or None)}
        except ModelError as exc:
            raise ValueError(str(exc)) from None

    def test_provider(self, entry):
        """One short live call, on the owner's click: is this key and model answering?

        Not a test of images or tools. It spends from a small ledger of its own."""
        model = next((m for m in self.registry.list_models() if m["id"] == entry), None)
        if model is None or not model["role_ready"]:
            raise ValueError("Only a chat provider with an endpoint can be tested")
        profile = self.registry.profile(entry)
        started = time.monotonic()
        try:
            transport = self._transport(entry, profile, 1, None, limits={"call_seconds":PROVIDER_TEST_SECONDS}, rate=None)
            scope = BudgetScope(BudgetLedger(self.root/"ledgers"/"provider-tests.sqlite3", PROVIDER_TEST_CAP, 0), entry, PROVIDER_TEST_CAP)
            client = ChatClient(transport, scope, profile=replace(profile, context_limit=4096), max_tokens=256, retries=0,
                                 call_seconds=PROVIDER_TEST_SECONDS)
            reply,usage = client.chat([{"role":"user","content":"Reply OK"}], role="orchestrator", phase="provider-test",
                                      timeout=PROVIDER_TEST_SECONDS)
            outcome = {"ok":True,"reply":(reply["content"] or "")[:200],"cost_usd":float(usage["cost_usd"])}
        except IncompleteResponse:
            outcome = {"ok":True,"reply":"The provider answered, but its reply was cut off at the short test length"}
        except (ModelError, ValueError) as exc:
            outcome = {"ok":False,"error":str(exc)}
        return {**outcome,"seconds":round(time.monotonic()-started,1)}

    def pick_folder(self):
        """A native folder dialog on this machine; a browser page cannot give a full path."""
        script = ("import tkinter, tkinter.filedialog as dialog\nroot = tkinter.Tk()\nroot.withdraw()\n"
                  "root.attributes('-topmost', True)\nprint(dialog.askdirectory(title='Choose a workspace folder'))\n")
        try:
            done = subprocess.run([sys.executable,"-c",script],capture_output=True,encoding="utf-8",timeout=600,
                                  env={**os.environ,"PYTHONUTF8":"1"})
        except (OSError, subprocess.TimeoutExpired):
            done = None
        if done is None or done.returncode != 0:
            raise ValueError("No folder dialog is available here; type the path instead")
        return {"path":done.stdout.strip()}

    def _jobs(self, workspace):
        """Run records with usage and health attached, reconciled against the core's job state."""
        from h2portable.jobs import read_job
        jobs = workspace.runs()
        spent, simulated = usage_rows(self._ledger(workspace)), usage_rows(workspace.preview_ledger)
        for job in jobs:
            rows = spent if job.get("spec",{}).get("mode") == "live" else simulated
            job["usage"] = summarize([row for row in rows if row["candidate"].startswith(job["id"]+"/")])
            job["health"] = read_health(workspace.run_dir(job["id"])/"health.json")
            task = job.get("spec",{}).get("task")
            if task and job.get("kind") != "trial":  # the loop writes one summary per sample as it finishes it
                done = sum(1 for _ in workspace.run_dir(job["id"]).glob("outputs/runs/*-agg1/*/summary.json"))
                job["progress"] = {"done":done,"total":task["stream_samples"]}
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

    @staticmethod
    def _brief(job):
        """A run as the page polls it. A whole spec with every run was a third of each reply, and the page reads only these."""
        spec, providers = job.get("spec",{}), job.get("spec",{}).get("providers") or {}
        return {**job,"spec":{"version":spec.get("version"),"task":bool(spec.get("task")),
                              "models":{role:providers[role]["name"] for role in ("improver","worker") if role in providers}}}

    def state(self):
        from .taskrun import _folders
        projects, jobs, rows, spent, missing = [], [], [], [], []
        for entry in self.store.list("workspace"):
            try:
                workspace = self.workspace(entry["id"])
            except ValueError:
                missing.append(entry["path"])
                continue
            projects.append(self._view(workspace))
            jobs.extend({**self._brief(job), "sample_folders": _folders(workspace.run_dir(job["id"]))}
                        for job in self._jobs(workspace))
            rows.extend(usage_rows(workspace.preview_ledger))
            spent.extend(usage_rows(self._ledger(workspace)))
        deleted = []
        for entry in self.store.list("deleted_workspace"):
            if any(project["id"] == entry["id"] for project in projects):
                continue
            ledger = self._deleted_workspace_ledger(entry["id"])
            kept_rows = usage_rows(ledger)
            spent.extend(kept_rows)
            deleted.append({**entry,"usage":summarize(kept_rows)})
        return {"models": self.registry.list_models(), "limit_fields": run_limits.field_list(),
                "projects": projects, "missing_workspaces": missing,
                "deleted_workspaces":deleted,
                "jobs": jobs, "preview_usage": summarize(rows),
                "usage_events": (rows + spent)[-80:],
                "live_usage": self.pilot_ledger.summary() if self.pilot_ledger else None}

    def sample_media(self, identifier, path):
        """A listed photo or voice note for the Samples tab. Nothing else in the folder, so never an answer file."""
        workspace = self.workspace(identifier)
        mime = SAMPLE_MEDIA.get(PurePosixPath(path or "").suffix.lower())
        target = workspace.samples_path/(path or "")
        if mime is None or path not in {item["path"] for item in workspace.project()["inventory"]["inventory"]} or target.is_symlink() or not target.is_file():
            raise ValueError("Only a listed photo or voice note can be shown")
        return target.read_bytes(), mime

    def check_text(self, identifier):
        """The scoring check of the current version, for the Spec tab. It reads the answers when it runs and holds none."""
        workspace = self.workspace(identifier)
        draft = workspace.spec(workspace.project()["spec_version"])
        if not draft.get("task"):
            raise ValueError("This workflow has a built-in check")
        return check_forms.author_text(workspace.check_source(draft["check"])) if draft.get("check") else ""

    def runs(self, identifier):
        """A workspace's runs, newest first, reconciled with the core's job state."""
        return self._jobs(self.workspace(identifier))

    def spend(self, identifier):
        """A workspace's real-spend summary: its cap, what is spent, held and left."""
        return self._ledger(self.workspace(identifier)).summary()

    def spend_rows(self, identifier, prefix=""):
        """Its real-spend ledger, one row per call, optionally for one run or scope."""
        return usage_rows(self._ledger(self.workspace(identifier)), prefix)

    def system(self):
        """What Settings shows. Apart from state, because finding the sandbox can start Docker's client."""
        enabled = [m["id"] for m in self.registry.list_models() if m["enabled"] and m["id"] in tasks.MODEL_TOOLS]
        calls = [tool.get("function", tool) for tool in task_tools(tasks.tool_catalog(enabled))]
        return {"version":version("lx-harness"),"state_root":str(self.root),"sandbox":sandbox.backend(),
                "meta_key":self.registry.meta_key_source(),"agent_setup":self.agent_setup(),"agent_access":self.agent_access(),
                "agent_workspace_deletion":self.agent_workspace_deletion(),
                "agent_tools":[{"name":call["name"],"description":call["description"]} for call in calls],
                "mcp_servers":self.mcp_view()}

    def agent_access(self):
        """Whether connected agents may act. Each agent's server asks on every call, so Stop reaches sessions already open."""
        try:
            return self.store.get("setting", "agent_access")["on"]
        except ValueError:
            return True

    def set_agent_access(self, on):
        """A person's switch; no action offers it to an agent."""
        if type(on) is not bool:
            raise ValueError("Say whether agents may connect")
        self.store.save("setting", {"id":"agent_access","on":on})
        return {"agent_access":on}

    def agent_workspace_deletion(self):
        """A separate person-controlled permission, off on existing and new installs."""
        try:
            return self.store.get("setting", "agent_workspace_deletion")["on"] is True
        except ValueError:
            return False

    def set_agent_workspace_deletion(self, on):
        """Only Settings exposes this switch; agents cannot turn it on themselves."""
        if type(on) is not bool:
            raise ValueError("Say whether agents may delete workspaces")
        self.store.save("setting", {"id":"agent_workspace_deletion","on":on})
        return {"agent_workspace_deletion":on}

    def _require_agent_workspace_deletion(self):
        if not self.agent_access():
            raise ValueError("Agent access is stopped in LX: Settings, Connectivity")
        if not self.agent_workspace_deletion():
            raise ValueError("Workspace deletion by agents is off: a person can enable it in Settings, Connectivity")

    def agent_setup(self):
        """Ready setup for an agent client, with this install's own paths (D-024). No keys: the agent never needs one."""
        args = ["-m","lx.mcp_server"]+([] if self.root == DEFAULT_ROOT.resolve() else ["--root",str(self.root)])
        # Muse Code starts a server with only the variables listed here, and Python on Windows needs these to find the profile folder.
        env = {name:os.environ[name] for name in ("USERPROFILE","HOMEDRIVE","HOMEPATH","APPDATA","LOCALAPPDATA","SYSTEMROOT","TEMP","TMP") if os.environ.get(name)} if os.name == "nt" else {}
        muse = {"schema_version":1,"mcp_servers":{"lx":{"transport":"stdio","command":sys.executable,"args":args,**({"env":env} if env else {}),"mode":"optional"}}}
        # JSON strings and arrays are also TOML basic strings and arrays. Escape Windows paths rather than interpolating them.
        toml = "\n".join(["[mcp_servers.lx]", "command = "+json.dumps(sys.executable, ensure_ascii=False),
                          "args = "+json.dumps(args, ensure_ascii=False),
                          *(["env_vars = "+json.dumps(list(env))] if env else [])])
        quote = subprocess.list2cmdline if os.name == "nt" else shlex.join
        return {"installed":find_spec("mcp") is not None,
                "claude":subprocess.list2cmdline(["claude","mcp","add","--transport","stdio","--scope","local","lx","--",sys.executable,*args]),
                "codex":quote(["codex","mcp","add","lx","--",sys.executable,*args]),
                "codex_config":toml,
                "muse":json.dumps(muse,indent=2)}

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
                if path.is_file() and not path.is_symlink() and path.suffix in {".md",".json",".py"}:
                    archive.write(path,path.relative_to(folder).as_posix())
        return stream.getvalue()

    def trajectory_bytes(self, identifier, job_id):
        """Every agent run's trace, for fine-tuning (REQ-01). Only on request: traces can hold the user's data, and no answers."""
        workspace = self.workspace(identifier)
        job = workspace.run(job_id)
        if job["status"] in {"queued","running"}:
            raise ValueError("Wait for the run to end")
        folder = workspace.run_dir(job_id)/"outputs"/"runs"/"executions"
        traces = sorted(path for path in folder.rglob("trace.json") if path.is_file() and not path.is_symlink()) if folder.is_dir() else []
        if not traces:
            raise ValueError("This run left no trajectories")
        stream = io.BytesIO()
        with zipfile.ZipFile(stream,"w",zipfile.ZIP_DEFLATED) as archive:
            for path in traces:
                archive.write(path,path.relative_to(folder).as_posix())
        return stream.getvalue()

    def _execution_traces(self, workspace, job_id):
        folder = workspace.run_dir(job_id)/"outputs"/"runs"/"executions"
        traces = sorted(path for path in folder.rglob("trace.json") if path.is_file() and not path.is_symlink()) if folder.is_dir() else []
        return folder, traces

    def run_replies(self, identifier, job_id):
        """Each sample's final reply before and after the loop, for a person reading a run. Not an orchestrator action: agents get no answers."""
        from .taskrun import _folders
        workspace = self.workspace(identifier)
        run_dir = workspace.run_dir(job_id)
        files = (workspace.run(job_id).get("result") or {}).get("reply_files") or {}
        folders = _folders(run_dir)
        def text(relative):
            answer = json.loads((run_dir/"outputs"/"runs"/relative/"answer.json").read_text(encoding="utf-8"))
            return (answer if isinstance(answer, str) else json.dumps(answer, indent=2, ensure_ascii=False))[:MAX_REPLY]
        judged_file = run_dir/"outputs"/"judged.json"
        kept = json.loads(judged_file.read_text(encoding="utf-8")) if judged_file.is_file() else None
        # A run written on Windows keeps its reply paths with backslashes; verdicts are keyed with forward slashes.
        verdicts = lambda relative: kept.get(relative.replace("\\", "/"))
        judged = lambda pair: {"before": verdicts(pair["base"]), "after": verdicts(pair["after"])}
        return {"samples":[{"sample":folders.get(sample, sample),"before":text(pair["base"]),"after":text(pair["after"]),"same":pair["base"] == pair["after"],
                            **({"judged": judged(pair)} if kept is not None else {})}
                           for sample, pair in files.items()]}

    def run_trajectories(self, identifier, job_id):
        """What each agent run of this job did, in brief. A trace is written when its agent run ends, so a running job shows the finished ones."""
        from .taskrun import _folders
        workspace = self.workspace(identifier)
        job = workspace.run(job_id)
        folder, traces = self._execution_traces(workspace, job_id)
        found, folders = [], _folders(workspace.run_dir(job_id))
        for path in traces:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                steps = record["trace"]
                calls = {}
                for step in steps:
                    if step["type"] == "tool":
                        calls[step["name"]] = calls.get(step["name"], 0)+1
                stopped = next((step["reason"] for step in steps if step["type"] == "stopped"), None)
                relative = path.parent.relative_to(folder).as_posix()
                found.append({"path":relative, **self._trace_sample(path.parent, relative, folders), "turns":sum(step["type"] == "assistant" for step in steps),
                              "tool_calls":calls,"stopped":stopped,"answered":bool((record.get("final_text") or "").strip()),
                              "reply":self._reply_form(path.with_name("answer.json"))})
            except (OSError, ValueError, KeyError, TypeError):
                continue
        stages = {"base1": 0, "inv1": 1, "dom1": 2, "c1": 3, "trial": 4}
        found.sort(key=lambda item: (item.get("sample") or "", stages.get(item["path"].split("/")[-1], 5), item["path"]))
        return {"status":job["status"],"executions":found}

    @staticmethod
    def _trace_sample(directory, relative, folders):
        """Display the original folder while leaving recorded execution IDs untouched."""
        metadata = directory/"execution.json"
        try:
            internal = json.loads(metadata.read_text(encoding="utf-8")).get("task") if metadata.is_file() and not metadata.is_symlink() else None
        except (OSError, ValueError, TypeError, AttributeError):
            internal = None
        parts = relative.split("/")
        internal = internal if isinstance(internal, str) else parts[-2] if len(parts) >= 3 else None
        return {"sample": folders.get(internal, internal), "internal_sample": internal} if internal else {}

    @staticmethod
    def _reply_form(answer_file):
        """"json" if the final reply was read as JSON, "text" if it stayed text, None if no answer was saved. For a person reading the run."""
        if not answer_file.is_file():
            return None
        return "text" if isinstance(json.loads(answer_file.read_text(encoding="utf-8")),str) else "json"

    def run_trajectory(self, identifier, job_id, path):
        """One agent run's trace, as recorded. Only a path under this job's executions folder is read."""
        from .taskrun import _folders
        workspace = self.workspace(identifier)
        workspace.run(job_id)
        folder, traces = self._execution_traces(workspace, job_id)
        wanted = PurePosixPath(path or "")
        if not path or wanted.is_absolute() or ".." in wanted.parts or "\\" in path:
            raise ValueError("Unknown trajectory")
        target = next((item for item in traces if item.parent.relative_to(folder).as_posix() == wanted.as_posix()), None)
        if target is None or target.stat().st_size > 20_000_000:
            raise ValueError("Unknown trajectory")
        record = json.loads(target.read_text(encoding="utf-8"))
        opening = target.parent/"opening.json"
        try:
            messages = json.loads(opening.read_text(encoding="utf-8")) if opening.is_file() and not opening.is_symlink() else None
        except (OSError, ValueError):
            messages = None  # a run from before this was kept has none
        return {"path":wanted.as_posix(), **self._trace_sample(target.parent, wanted.as_posix(), _folders(workspace.run_dir(job_id))),
                "final_text":record.get("final_text") or "","trace":record["trace"],"opening":messages}

    def fresh(self, identifier, job_id):
        self._require_active_workspace(identifier)
        workspace = self.workspace(identifier)
        job = workspace.run(job_id)
        if job["status"] != "completed":
            raise ValueError("Complete the run before reusing its harness")
        if job["spec"].get("task"):
            raise ValueError("Reuse on a new sample is not available for this task yet")
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
