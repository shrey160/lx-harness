"""The client for local MCP servers, against a small server and, when the SDK is here, LX's own."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from lx import mcp_client
from lx.mcp_client import Conversation, McpError, McpRun, Session, agent_name, check_server_name, definition, read_tools

SERVER = [sys.executable, str(Path(__file__).parent/"mcp_fixture.py")]

try:
    import mcp
except ImportError:
    mcp = None


def entries(*flags):
    return [definition("shop", tool) for tool in read_tools(SERVER+list(flags)) if tool["tool"] == "price.lookup"]


class Reading(unittest.TestCase):
    def test_it_lists_tools_past_a_stray_log_line_and_a_servers_own_request(self):
        self.assertEqual([tool["tool"] for tool in read_tools(SERVER)], ["orders_delete", "price.lookup"])

    def test_a_server_inherits_no_key_from_this_process(self):
        with patch.dict(os.environ, {"LX_TEST_API_KEY": "secret"}):
            self.assertEqual(next(tool for tool in read_tools(SERVER+["--env"]) if tool["tool"] == "price.lookup")["description"], "clean")

    def test_a_command_that_cannot_start_says_so(self):
        with self.assertRaisesRegex(McpError, "Could not start"):
            read_tools(["no-such-program-here"])

    def test_a_program_that_is_no_server_fails_fast(self):
        with self.assertRaisesRegex(McpError, "closed its connection"):
            read_tools([sys.executable, "-c", "pass"])

    def test_a_bad_command_or_name_is_refused(self):
        for command in ("python server.py", [], [""], ["a", 1]):
            with self.subTest(command), self.assertRaisesRegex(McpError, "command"):
                read_tools(command)
        for name in ("Shop", "1shop", "a b", "", "x"*22, None):
            with self.subTest(name), self.assertRaisesRegex(McpError, "server name"):
                check_server_name(name)

    def test_the_agent_sees_a_name_providers_accept(self):
        self.assertEqual(agent_name("shop", "price.lookup"), "mcp_shop_price_lookup")

    @unittest.skipIf(mcp is None, "needs lx-harness[mcp]")
    def test_a_real_sdk_server_reads_the_same_protocol(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
            found = read_tools([sys.executable, "-m", "lx.mcp_server", "--root", root])
        self.assertIn("status", [tool["tool"] for tool in found])


class Running(unittest.TestCase):
    def setUp(self):
        self.run = McpRun(entries(), {"shop": SERVER}, 3)
        self.addCleanup(self.run.close)
        self.name = "mcp_shop_price_lookup"

    def test_a_call_returns_text_and_a_repeat_comes_from_the_cache(self):
        first = self.run.call(self.name, {"item": "cup"})
        self.assertEqual(first, {"text": "4.5 (call 1)", "truncated": False})
        self.assertEqual(self.run.call(self.name, {"item": "cup"}), first)
        self.assertEqual(self.run.calls, 1)

    def test_a_long_result_is_cut_and_says_so(self):
        result = self.run.call(self.name, {"item": "huge"})
        self.assertEqual((len(result["text"]), result["truncated"]), (mcp_client.MAX_RESULT, True))

    def test_content_that_is_not_text_is_left_out_and_noted(self):
        self.assertEqual(self.run.call(self.name, {"item": "picture"})["text"], "[content that is not text was left out]\nbeside it")

    def test_a_tool_error_reaches_the_agent_as_an_error(self):
        with self.assertRaisesRegex(McpError, "no such item: pear"):
            self.run.call(self.name, {"item": "pear"})

    def test_the_call_limit_covers_the_run(self):
        for item in ("cup", "bowl", "huge"):
            self.run.call(self.name, {"item": item})
        with self.assertRaisesRegex(McpError, "limit"):
            self.run.call(self.name, {"item": "picture"})

    def test_a_hung_tool_times_out(self):
        with patch.object(mcp_client, "CALL_SECONDS", 1), self.assertRaisesRegex(McpError, "did not answer"):
            self.run.call(self.name, {"item": "sleep"})

    def test_the_pinned_tool_is_checked_before_a_run(self):
        self.run.check()
        changed = McpRun(entries(), {"shop": SERVER+["--changed"]}, 3)
        self.addCleanup(changed.close)
        with self.assertRaisesRegex(McpError, "changed its tool price.lookup"):
            changed.check()

    def test_a_conversation_keeps_its_own_results_for_scripts(self):
        one, two = Conversation(self.run), Conversation(self.run)
        self.assertIn(self.name, one)
        self.assertNotIn("segment", one)
        self.assertEqual(one(self.name, {"item": "cup"})["tool"], self.name)
        self.assertEqual(one.results[0]["arguments"], {"item": "cup"})
        self.assertEqual(two.results, [])

    def test_close_ends_the_server(self):
        session = Session(SERVER)
        session.close()
        self.assertIsNotNone(session.process.poll())


if __name__ == "__main__":
    unittest.main()
