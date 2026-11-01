"""Model tools on and off (D-019): with both on nothing changes; a tool that is off is gone."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from lx import sandbox, tasks
from lx.agent import PhotoSetTools, photo_tools, run_agent, tool_identity
from lx.components import COMPONENT_TOOLS, experiment_tools, task_tools
from lx.engine import Engine
from lx.inventory_probe import CATALOG, TOOLS
from lx.inventory_workflow import VIEW_NOTE, InventoryAdapter, InventoryDomain, InventoryPreviewTransport
from lx.live_transport import validate_body
from lx.perception import META_SAM, META_TRANSCRIBE
from lx.registry import Registry, RegistryError
from lx.spark import ModelError
from lx.worker import execute
from test_tasks import Base, MemoryKeychain

SAM, TRANSCRIBE = tasks.SEGMENTATION, tasks.TRANSCRIPTION
# Taken at a7a0437, before tools could be switched: the paused experiments' evidence depends on these staying put.
TOOL_HASH = "ee92f70b0f7835eaee956c0c36aa4d4904546ce4dfef6218573a920a3ae768f1"
FULL_HASH = "65c4ce952a4f9fd42c91dd92920ece2ff31b8c405c8130d57bd9b9ab009e386b"
FIRST_MESSAGE = {"sample_id": "s", "photos": [{"photo": 1, "width": 1, "height": 2}],
                 "photos_note": "not attached; use view to look and segment to find instances",
                 "spoken_request": "audio only; use transcribe"}


def names(tools):
    return [tool["function"]["name"] for tool in tools]


class BothOnChangesNothing(Base):
    def test_the_tool_lists_are_byte_identical(self):
        self.assertEqual(tool_identity(photo_tools(tasks.tool_catalog(tasks.MODEL_TOOLS))), TOOL_HASH)
        self.assertEqual(tool_identity(photo_tools(tasks.tool_catalog(tasks.MODEL_TOOLS))+COMPONENT_TOOLS), FULL_HASH)
        self.assertEqual(tool_identity(experiment_tools(tasks.tool_catalog(tasks.MODEL_TOOLS))), FULL_HASH)
        self.assertEqual(tool_identity(TOOLS), FULL_HASH)

    def test_the_paused_experiments_keep_their_loop(self):
        self.assertFalse(InventoryDomain.whole_context)
        self.assertTrue(tasks.TaskDomain.whole_context)

    def test_the_first_message_is_byte_identical(self):
        task = tasks.inventory_task()
        for domain in (InventoryDomain(), tasks.TaskDomain(task, "check")):
            self.assertEqual(json.loads(domain.messages({"sample_id": "s"}, "p", [(1, 2)])[1]["content"]), FIRST_MESSAGE)

    @unittest.skipUnless(sandbox.available(), "needs the script sandbox")
    def test_a_new_spec_gets_the_task_list(self):
        p = self.inventory()
        self.assertEqual(p["draft"]["tools"], [SAM, TRANSCRIBE])
        self.assertEqual(tool_identity(self.app._agent_tools(p["draft"])), tool_identity(task_tools(tasks.tool_catalog(tasks.MODEL_TOOLS))))

    def test_a_spec_from_before_the_switch_has_both(self):
        self.assertEqual(tasks.spec_tools({"task": tasks.inventory_task()}), tasks.MODEL_TOOLS)

    def test_only_photo_tasks_use_tools(self):
        self.assertEqual(tasks.spec_tools({"task": None}), ())
        self.assertEqual(tasks.spec_tools({"task": {"template": tasks.TEXT_TEMPLATE}}), ())


class TheStockAgent(Base):
    def test_a_task_has_no_helper_the_loop_could_write_itself(self):
        tools = task_tools(tasks.tool_catalog(tasks.MODEL_TOOLS))
        self.assertEqual(names(tools), ["segment", "transcribe", "view", "load_skill", "run_script"])
        self.assertNotIn("e.g. from segment", json.dumps(tools))
        self.assertIn("earlier segment boxes", json.dumps(tools))

    def test_a_model_naming_count_boxes_on_a_task_reaches_nothing(self):
        handler = PhotoSetTools(MagicMock(), photos=[b"x"], counting=False)
        with self.assertRaisesRegex(ValueError, "unknown tool"):
            handler("count_boxes", {"photo": 1, "concept": "pen"})

    def test_the_paused_experiments_still_have_it(self):
        catalog = tasks.tool_catalog(tasks.MODEL_TOOLS)
        offered = lambda domain: names(InventoryAdapter._agent_tools(SimpleNamespace(domain=domain, scripts_enabled=True), catalog))
        self.assertIn("count_boxes", offered(InventoryDomain()))
        self.assertNotIn("count_boxes", offered(tasks.TaskDomain(tasks.inventory_task(), "check")))


class SettingsSwitch(Base):
    def test_the_switch_survives_a_restart_and_seeding(self):
        root = Path(self.temp.name)/"switch"
        registry = Registry(root/"a.sqlite3", root/"secrets")
        self.assertTrue(next(m for m in registry.list_models() if m["id"] == SAM)["enabled"])
        self.assertFalse(registry.set_enabled(SAM, False)["enabled"])
        again = Registry(root/"a.sqlite3", root/"secrets")
        self.assertFalse(next(m for m in again.list_models() if m["id"] == SAM)["enabled"])

    def test_it_refuses_the_default_model_and_takes_only_true_or_false(self):
        with self.assertRaisesRegex(RegistryError, "stays on"):
            self.app.registry.set_enabled("spark-contributor", False)
        with self.assertRaisesRegex(RegistryError, "unknown"):
            self.app.registry.set_enabled("missing", False)
        with self.assertRaisesRegex(RegistryError, "switch"):
            self.app.registry.set_enabled(SAM, "off")

    def test_off_leaves_new_workspaces_without_it_and_settings_list_shorter(self):
        self.app.registry.set_enabled(SAM, False)
        self.assertEqual(self.inventory()["draft"]["tools"], [TRANSCRIBE])
        listed = [tool["name"] for tool in self.app.system()["agent_tools"]]
        self.assertNotIn("segment", listed)
        self.assertNotIn("count_boxes", listed)
        self.assertIn("view", listed)

    def test_off_after_approval_blocks_the_start_with_the_reason(self):
        p = self.inventory()
        with patch.object(self.app, "_prove_task", return_value=None):
            p = self.approve(p)
        self.app.registry.set_enabled(SAM, False)
        self.assertRegex(self.app.project(p["id"])["blocked"], "SAM 3.1 is off in Settings")
        with self.assertRaisesRegex(ValueError, "SAM 3.1 is off"):
            self.app.start(p["id"], launch=False)
        self.app.registry.set_enabled(SAM, True)
        self.assertIsNone(self.app.project(p["id"])["blocked"])

    def test_a_required_tool_that_is_off_says_the_task_needs_it(self):
        self.app.registry.set_enabled(TRANSCRIBE, False)
        p = self.inventory()
        self.assertEqual(p["draft"]["tools"], [SAM, TRANSCRIBE])
        self.assertRegex(p["blocked"], "This task needs Muse Voice Transcribe")
        with self.assertRaisesRegex(ValueError, "needs Muse Voice Transcribe"):
            self.approve(p)

    def test_a_text_task_does_not_care(self):
        self.app.registry.set_enabled(TRANSCRIBE, False)
        self.app.registry.set_enabled(SAM, False)
        p = self.app.create_project({"source": "demo", "task": "records", "path": self.folder()})
        self.assertIsNone(p["blocked"])
        self.assertNotIn("tools", p["draft"])
        self.assertEqual(p["tool_choices"], [])


class SpecList(Base):
    def without_sam(self):
        p = self.inventory()
        return self.app.edit_spec(p["id"], {"goal": "g", "budget_usd": 1, "tools": [TRANSCRIBE]})

    def test_transcription_cannot_be_left_out_and_names_are_checked(self):
        p = self.inventory()
        for bad, message in (([SAM], "needs its transcription"), ([], "needs its transcription"),
                             ([TRANSCRIBE, "spark-contributor"], "from the list"), ("all", "from the list")):
            with self.subTest(bad), self.assertRaisesRegex(ValueError, message):
                self.app.edit_spec(p["id"], {"goal": "g", "budget_usd": 1, "tools": bad})

    def test_a_change_is_a_new_version_and_order_is_the_catalog(self):
        p = self.inventory()
        version = p["draft"]["version"]
        p = self.app.edit_spec(p["id"], {"goal": "g", "budget_usd": 1, "tools": [TRANSCRIBE, SAM]})
        self.assertEqual((p["draft"]["version"], p["draft"]["tools"]), (version+1, [SAM, TRANSCRIBE]))
        p = self.app.edit_spec(p["id"], {"goal": "h", "budget_usd": 1})
        self.assertEqual(p["draft"]["tools"], [SAM, TRANSCRIBE])  # kept when the edit does not name them

    def test_off_takes_it_out_of_the_notice_and_the_limits(self):
        entry = self.entry("glimmer")
        p = self.without_sam()
        p = self.app.edit_spec(p["id"], {"goal": "g", "budget_usd": 1, "mode": "live",
                                         "roles": {r: entry for r in ("orchestrator", "improver", "worker")}})
        self.assertEqual([n["who"] for n in p["notice"]][3:], ["Muse Voice Transcribe"])
        self.assertEqual(p["hidden_limits"], ["mcp_calls", "segment_calls"])
        self.assertEqual([c["id"] for c in p["tool_choices"] if c["required"]], [TRANSCRIBE])
        with patch.object(self.app.registry, "meta_key_source", return_value=None):
            self.assertRegex(self.app.project(p["id"])["blocked"], "Meta key for Transcribe\\.")

    def test_off_takes_it_out_of_the_tools_and_the_first_message(self):
        p = self.without_sam()
        offered = self.app._agent_tools(p["draft"])
        self.assertIn("transcribe", names(offered))
        self.assertIn("view", names(offered))
        for scripts in (True, False):
            text = json.dumps(task_tools(tasks.tool_catalog([TRANSCRIBE]), scripts)).lower()
            for word in ("segment", "count_boxes", "sam 3"):
                self.assertNotIn(word, text)
        domain = tasks.TaskDomain(p["draft"]["task"], "check", segmentation=False)
        first = domain.messages({"sample_id": "s"}, "p", [(1, 2)])[1]["content"]
        self.assertEqual(json.loads(first)["photos_note"], VIEW_NOTE)
        self.assertNotIn("segment", first)
        self.assertNotIn("segments", domain.prompts().work)
        self.assertIn("segments", tasks.TaskDomain(p["draft"]["task"], "check").prompts().work)

    def test_the_live_contract_refuses_a_request_naming_it(self):
        p = self.without_sam()
        allowed = self.app._agent_tools(p["draft"])
        body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
        validate_body({**body, "tools": allowed}, model="m", tool_sets=(allowed,))
        with self.assertRaisesRegex(ModelError, "tool contract"):
            validate_body({**body, "tools": TOOLS}, model="m", tool_sets=(allowed,))

    def test_a_model_naming_it_anyway_reaches_nothing(self):
        perception = MagicMock()
        handler = PhotoSetTools(perception, photos=[b"x"], segmentation=False)
        for name, arguments in (("segment", {"photo": 1, "concept": "pen"}), ("count_boxes", {"photo": 1, "concept": "pen"})):
            with self.subTest(name), self.assertRaisesRegex(ValueError, "unknown tool"):
                handler(name, arguments)
        perception.segment.assert_not_called()

    def test_no_transport_or_key_is_made_for_it(self):
        app = Engine(self.root/"keyed", keychain=MemoryKeychain())
        app.live_enabled = True
        app.registry.update("spark-contributor", api_key="dummy-ui-key")
        with patch("lx.engine.ToolTransport") as transport:
            client = app.build_perception(SimpleNamespace(project=lambda: {"id": "a"*32}), "scope", 1, self.root/"cache",
                                          {"segment_calls": 1, "transcribe_calls": 1}, (TRANSCRIBE,))
        self.assertEqual(transport.call_count, 1)
        self.assertIsNone(client.segment_transport)

    def test_each_tool_gets_its_own_format_and_limit(self):
        app = Engine(self.root/"keyed", keychain=MemoryKeychain())
        app.live_enabled = True
        app.registry.update("spark-contributor", api_key="dummy-ui-key")
        client = app.build_perception(SimpleNamespace(project=lambda: {"id": "a"*32}), "scope", 1, self.root/"cache",
                                      {"segment_calls": 3, "transcribe_calls": 2})
        self.assertEqual((client.segment_transport.format, client.segment_transport.call_limit), (META_SAM, 3))
        self.assertEqual((client.transcribe_transport.format, client.transcribe_transport.call_limit), (META_TRANSCRIBE, 2))
        with self.assertRaisesRegex(RegistryError, "unknown model tool"):
            app.registry.tool_format("spark-contributor")

    @unittest.skipUnless(sandbox.available(), "needs the script sandbox")
    def test_a_preview_runs_without_it_and_never_offers_or_calls_it(self):
        p = self.approve(self.without_sam())
        job = self.app.start(p["id"], launch=False)
        workspace = self.app.workspace(p["id"])
        with patch("lx.inventory_workflow.run_agent", wraps=run_agent) as loop:
            execute(self.app, workspace, job["id"], workspace.run_dir(job["id"])/"snapshot")
        first = loop.call_args_list[0]
        self.assertTrue(first.kwargs["whole_context"])
        self.assertGreaterEqual(first.kwargs["keep_images"], 5)
        self.assertNotIn("Run rules", first.args[1][0]["content"])
        traces = list(workspace.run_dir(job["id"]).glob("outputs/runs/executions/**/trace.json"))
        self.assertTrue(traces)
        for path in traces:
            trace = json.loads(path.read_text(encoding="utf-8"))["trace"]
            self.assertNotIn("segment", trace[0]["names"])
            self.assertNotIn("count_boxes", trace[0]["names"])
            used = {step["name"] for step in trace if step["type"] == "tool"}
            self.assertEqual(used, {"transcribe", "view"})


if __name__ == "__main__":
    unittest.main()
