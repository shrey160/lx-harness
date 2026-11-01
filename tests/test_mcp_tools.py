"""MCP tools in a run (D-030): a person connects and marks, a spec chooses, and the agent's calls reach the server."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from lx import components, mcp_client, sandbox, tasks
from lx.components import ComponentTools
from lx.inventory_workflow import InventoryPreviewTransport
from lx.live_transport import validate_body
from lx.orchestration import ACTIONS, Orchestration
from lx.spark import ModelError
from lx.spike import wire_reply
from lx.taskrun import MCP_NOTE, limitations
from test_agent import call
from test_tasks import Base
from test_trial import Trials

SERVER = [sys.executable, str(Path(__file__).parent/"mcp_fixture.py")]
LOOKUP = "shop/price.lookup"
NAME = "mcp_shop_price_lookup"


class Connected(Base):
    def setUp(self):
        super().setUp()
        self.server = self.app.mcp_connect("shop", SERVER)
        self.lx = Orchestration(self.app, launch=False)
        self.path = self.folder()
        from lx.inventory import write_synthetic
        write_synthetic(Path(self.path)/"samples", 6)
        self.lx.init(self.path, "photo-request")
        self.id = self.app.open_workspace(self.path)["id"]

    def choose(self):
        self.app.mcp_mark("shop", ["price.lookup"])
        return self.lx.spec_draft(self.path, mcp_tools=[LOOKUP])

    def draft(self):
        return self.app.project(self.id)["draft"]


class ConnectingIsAPersonsStep(Connected):
    def test_no_orchestrator_action_can_connect_mark_or_remove(self):
        self.assertFalse([name for name in ACTIONS if "mcp" in name])
        self.assertFalse({"mcp_connect", "mcp_mark", "mcp_remove", "mcp_refresh"} & set(vars(Orchestration)))

    def test_an_agent_sees_tools_but_never_a_command(self):
        shown = json.dumps(self.lx.environment()["mcp_tools"])
        self.assertIn(LOOKUP, shown)
        self.assertNotIn("mcp_fixture", shown)

    def test_a_new_tool_starts_unmarked_and_cannot_be_chosen(self):
        self.assertFalse(any(tool["read_only"] for tool in self.server["tools"]))
        with self.assertRaisesRegex(ValueError, "not marked read-only"):
            self.lx.spec_draft(self.path, mcp_tools=[LOOKUP])

    def test_a_tool_that_writes_stays_out_once_others_are_marked(self):
        self.app.mcp_mark("shop", ["price.lookup"])
        with self.assertRaisesRegex(ValueError, "not marked read-only"):
            self.lx.spec_draft(self.path, mcp_tools=["shop/orders_delete"])

    def test_an_unknown_tool_or_server_is_refused(self):
        with self.assertRaisesRegex(ValueError, "No connected MCP tool"):
            self.lx.spec_draft(self.path, mcp_tools=["other/thing"])
        with self.assertRaisesRegex(ValueError, "offers"):
            self.app.mcp_mark("shop", ["nothing"])
        with self.assertRaisesRegex(ValueError, "has that name"):
            self.app.mcp_connect("shop", SERVER)


class Choosing(Connected):
    def test_the_spec_holds_what_was_approved_as_a_new_version(self):
        before = self.draft()["version"]
        self.choose()
        draft = self.draft()
        self.assertEqual(draft["version"], before+1)
        self.assertEqual([entry["name"] for entry in draft["mcp_tools"]], [NAME])
        self.assertEqual(self.lx.spec_show(self.path)["mcp_tools"], [LOOKUP])

    def test_a_later_edit_keeps_it_and_an_empty_list_takes_it_out(self):
        self.choose()
        self.lx.spec_draft(self.path, goal="another goal")
        self.assertEqual(len(self.draft()["mcp_tools"]), 1)
        self.lx.spec_draft(self.path, mcp_tools=[])
        self.assertNotIn("mcp_tools", self.draft())

    def test_a_spec_with_none_is_unchanged(self):
        self.assertNotIn("mcp_tools", self.draft())
        self.assertIn("mcp_calls", self.app.project(self.id)["hidden_limits"])

    def test_the_notice_and_limit_appear_with_it(self):
        self.choose()
        project = self.app.project(self.id)
        self.assertIn("mcp:shop", [entry["host"] for entry in project["notice"]])
        self.assertNotIn("mcp_calls", project["hidden_limits"])

    def test_the_live_contract_holds_the_tool_and_refuses_a_list_without_it(self):
        self.choose()
        allowed = self.app._agent_tools(self.draft())
        self.assertIn(NAME, [tool["function"]["name"] for tool in allowed])
        body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
        validate_body({**body, "tools": allowed}, model="m", tool_sets=(allowed,))
        with self.assertRaisesRegex(ModelError, "tool contract"):
            validate_body({**body, "tools": allowed[:-1]}, model="m", tool_sets=(allowed,))

    def test_it_blocks_when_the_server_goes_or_a_mark_is_lifted(self):
        self.choose()
        self.app.mcp_mark("shop", [])
        self.assertRegex(self.app.project(self.id)["blocked"], "not marked read-only")
        self.app.mcp_mark("shop", ["price.lookup"])
        self.assertIsNone(self.app.project(self.id)["blocked"])
        self.app.mcp_remove("shop")
        self.assertRegex(self.app.project(self.id)["blocked"], "no longer connected")

    def test_a_changed_tool_loses_its_mark_and_blocks_the_spec(self):
        self.choose()
        self.app.store.save("mcp_server", {**self.app.mcp_servers()[0], "command": SERVER+["--changed"]})
        refreshed = self.app.mcp_refresh("shop")
        self.assertFalse(any(tool["read_only"] for tool in refreshed["tools"]))
        self.assertRegex(self.app.project(self.id)["blocked"], "changed since this spec")

    def test_a_refresh_keeps_the_mark_on_an_unchanged_tool(self):
        self.app.mcp_mark("shop", ["price.lookup"])
        self.assertEqual([tool["tool"] for tool in self.app.mcp_refresh("shop")["tools"] if tool["read_only"]], ["price.lookup"])

    def test_a_preview_starts_no_server(self):
        self.choose()
        draft = self.draft()
        self.assertIsNone(self.app.mcp_run({**draft, "mode": "controlled_preview"}, self.app.limits_of(draft)))

    def test_the_package_limits_name_the_server(self):
        self.choose()
        self.assertIn("Needs the MCP server(s) shop", limitations(self.draft(), {"per_sample": {}, "skipped": []}, None, True))

    def test_a_grant_stops_covering_a_spec_with_other_tools(self):
        draft = self.draft()
        self.assertEqual(self.app._granted_tools(draft), sorted(tasks.spec_tools(draft)))
        self.choose()
        self.assertIn(NAME, self.app._granted_tools(self.draft()))


class Scripts(unittest.TestCase):
    def test_a_script_receives_this_conversations_results(self):
        run = mcp_client.McpRun([mcp_client.definition("shop", tool) for tool in mcp_client.read_tools(SERVER) if tool["tool"] == "price.lookup"], {"shop": SERVER}, 5)
        self.addCleanup(run.close)
        seen = {}
        def runner(source, payload, files, timeout):
            seen.update(payload)
            return {"exit_code": 0, "output": {}, "timed_out": False, "stderr": ""}
        handler = ComponentTools(lambda name, arguments: {}, {"scripts/s.py": "pass"}, runner=runner, mcp=mcp_client.Conversation(run))
        handler(NAME, {"item": "cup"})
        handler("run_script", {"name": "s", "input": {}})
        self.assertEqual(seen["mcp_results"][0]["arguments"], {"item": "cup"})
        self.assertEqual(seen["mcp_results"][0]["text"], "4.5 (call 1)")

    def test_a_run_without_mcp_gives_scripts_what_it_always_did(self):
        seen = {}
        def runner(source, payload, files, timeout):
            seen.update(payload)
            return {"exit_code": 0, "output": {}, "timed_out": False, "stderr": ""}
        ComponentTools(lambda name, arguments: {}, {"scripts/s.py": "pass"}, runner=runner)("run_script", {"name": "s", "input": {}})
        self.assertEqual(set(seen), {"input", "photos", "segments"})


class Asks(InventoryPreviewTransport):
    """The preview double, but the agent looks a price up first."""

    def __init__(self):
        super().__init__()
        self.offered = []

    def __call__(self, body, timeout):
        names = [tool["function"]["name"] for tool in body.get("tools") or []]
        if NAME in names:
            self.offered = names
            if not any(message.get("role") == "tool" for message in body["messages"]):
                return wire_reply(calls=[call(NAME, {"item": "cup"}, "m1")])
        ours = lambda message: message.get("tool_call_id") == "m1" or any(item["id"] == "m1" for item in message.get("tool_calls") or [])
        return super().__call__({**body, "messages": [message for message in body["messages"] if not ours(message)]}, timeout)


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class ARun(Trials):
    def setUp(self):
        super().setUp()
        self.app.mcp_connect("shop", SERVER)
        self.app.mcp_mark("shop", ["price.lookup"])
        self.lx.spec_draft(self.path, mcp_tools=[LOOKUP])
        self.transport = Asks()
        self.app._live_transport_factory = lambda hint=None: self.transport

    def test_a_trial_offers_the_tool_and_its_call_reaches_the_server(self):
        run_id, result = self.trial()
        self.assertIn(NAME, self.transport.offered)
        self.assertEqual(result["tool_calls"][NAME], 1)
        trace = next((self.workspace.run_dir(run_id)/"outputs").rglob("trace.json")).read_text(encoding="utf-8")
        self.assertIn("4.5 (call 1)", trace)

    def test_a_server_that_changed_since_approval_stops_the_run(self):
        self.approve_it()
        run_id = self.lx.trial_start(self.path)["run_id"]
        self.app.store.save("mcp_server", {**self.app.mcp_servers()[0], "command": SERVER+["--changed"]})
        with self.assertRaisesRegex(ValueError, "changed its tool"):
            self.finish(run_id)
        self.assertEqual(self.transport.calls["worker"], 0)

    def test_the_improver_is_told_what_a_script_receives(self):
        from lx.taskrun import _noted
        domain = _noted(tasks.TaskDomain(tasks.inventory_task(), "check"), object())
        self.assertIn(MCP_NOTE, domain.prompts().work)
        self.assertNotIn(MCP_NOTE, tasks.TaskDomain(tasks.inventory_task(), "check").prompts().work)


if __name__ == "__main__":
    unittest.main()
