"""The MCP client: a small server by command, in a real container and over HTTP, and, when the SDK is here, LX's own."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from lx import mcp_client
from lx.mcp_client import Conversation, McpError, McpRun, StdioSession, agent_name, check_record, check_secret_name, check_server_name, check_url, definition, docker_command, read_tools

SERVER = [sys.executable, str(Path(__file__).parent/"mcp_fixture.py")]
LOCAL = {"kind": "command", "command": SERVER}


def local(*flags):
    return {"kind": "command", "command": SERVER+list(flags)}

try:
    import mcp
except ImportError:
    mcp = None


def entries(*flags):
    return [definition("shop", local(*flags), tool) for tool in read_tools(local(*flags)) if tool["tool"] == "price.lookup"]


class Reading(unittest.TestCase):
    def test_it_lists_tools_past_a_stray_log_line_and_a_servers_own_request(self):
        self.assertEqual([tool["tool"] for tool in read_tools(LOCAL)], ["orders_delete", "price.lookup"])

    def test_a_server_inherits_no_key_from_this_process(self):
        with patch.dict(os.environ, {"LX_TEST_API_KEY": "secret"}):
            self.assertEqual(next(tool for tool in read_tools(local("--env")) if tool["tool"] == "price.lookup")["description"], "clean")

    def test_a_server_gets_the_secrets_given_to_it(self):
        found = read_tools(local("--env"), {"LX_TEST_OWN": "value"})
        self.assertEqual(next(tool for tool in found if tool["tool"] == "price.lookup")["description"], "LX_TEST_OWN=value")

    def test_a_command_that_cannot_start_says_so(self):
        with self.assertRaisesRegex(McpError, "Could not start"):
            read_tools({"kind": "command", "command": ["no-such-program-here"]})

    def test_a_program_that_is_no_server_fails_fast(self):
        with self.assertRaisesRegex(McpError, "closed its connection"):
            read_tools({"kind": "command", "command": [sys.executable, "-c", "pass"]})

    def test_a_bad_command_or_name_is_refused(self):
        for command in ("python server.py", [], [""], ["a", 1]):
            with self.subTest(command), self.assertRaisesRegex(McpError, "command"):
                check_record({"kind": "command", "command": command})
        for name in ("Shop", "1shop", "a b", "", "x"*22, None):
            with self.subTest(name), self.assertRaisesRegex(McpError, "server name"):
                check_server_name(name)

    def test_the_agent_sees_a_name_providers_accept(self):
        self.assertEqual(agent_name("shop", "price.lookup"), "mcp_shop_price_lookup")

    @unittest.skipIf(mcp is None, "needs lx-harness[mcp]")
    def test_a_real_sdk_server_reads_the_same_protocol(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
            found = read_tools({"kind": "command", "command": [sys.executable, "-m", "lx.mcp_server", "--root", root]})
        self.assertIn("status", [tool["tool"] for tool in found])


class Running(unittest.TestCase):
    def setUp(self):
        self.run = McpRun(entries(), {"shop": LOCAL}, 3)
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
        changed = McpRun(entries(), {"shop": local("--changed")}, 3)
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
        session = StdioSession(SERVER)
        session.close()
        self.assertIsNotNone(session.process.poll())


class Records(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.folder.cleanup)

    def test_an_image_is_kept_with_network_off_and_nothing_shared(self):
        record = check_record({"kind": "docker", "image": "ghcr.io/acme/prices:1.2", "command": ["ignored"], "url": "ignored"})
        self.assertEqual(record, {"kind": "docker", "image": "ghcr.io/acme/prices:1.2", "args": [], "network": False, "mounts": []})

    def test_an_image_cannot_smuggle_a_flag(self):
        for image in ("--privileged", "-v", "a b", "", None, "x;rm"):
            with self.subTest(image), self.assertRaisesRegex(McpError, "image"):
                check_record({"kind": "docker", "image": image})

    def test_a_shared_folder_must_be_real_and_clean(self):
        good = {"source": self.folder.name, "target": "/data"}
        self.assertEqual(check_record({"kind": "docker", "image": "a", "mounts": [good]})["mounts"], [good])
        for bad in ({"source": self.folder.name, "target": "data"}, {"source": "relative", "target": "/data"}, {"source": self.folder.name+"/missing", "target": "/data"},
                    {"source": self.folder.name, "target": "/data,readonly=false"}, "x"):
            with self.subTest(bad), self.assertRaises(McpError):
                check_record({"kind": "docker", "image": "a", "mounts": [bad]})

    def test_the_container_runs_by_id_shut_in_and_read_only(self):
        record = {"image_id": "sha256:abc", "args": ["serve"], "network": False, "mounts": [{"source": "/here", "target": "/data"}]}
        command = docker_command(record, {"API_KEY": "the-secret"}, "lx-mcp-1")
        self.assertEqual(command[-2:], ["sha256:abc", "serve"])
        for part in ("--cap-drop", "no-new-privileges", "none", "type=bind,source=/here,target=/data,readonly"):
            self.assertIn(part, command)
        self.assertEqual(command[command.index("--env")+1], "API_KEY")
        self.assertNotIn("the-secret", " ".join(command))
        self.assertNotIn("--network", docker_command({**record, "network": True}, {}, "x"))

    def test_an_address_is_https_or_this_machine(self):
        for url in ("https://mcp.example.com/mcp", "http://localhost:8931/mcp", "http://127.0.0.1:1/mcp"):
            self.assertEqual(check_url(url), url)
        for url in ("http://mcp.example.com/mcp", "ftp://localhost/x", "localhost:8931", "https://user:pass@mcp.example.com/", "", None):
            with self.subTest(url), self.assertRaises(McpError):
                check_url(url)

    def test_a_secret_cannot_replace_a_system_variable(self):
        self.assertEqual(check_secret_name("API_KEY"), "API_KEY")
        for name in ("PATH", "lower", "1A", "A B", "", None):
            with self.subTest(name), self.assertRaises(McpError):
                check_secret_name(name)


class Server(BaseHTTPRequestHandler):
    """A streamable HTTP server: JSON or an event stream, a session, a token and a redirect."""
    stream, token, seen = False, None, []

    def log_message(self, *args):
        pass

    def reply(self, status, body=b"", kind="application/json", headers=()):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        self.seen.append("DELETE")
        self.reply(200)

    def do_POST(self):
        # Read the request first: closing with it unread makes Windows reset the connection, which the client sees as no server.
        message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/moved":
            return self.reply(302, headers=[("Location", self.server.elsewhere)])
        if self.token and self.headers.get("Authorization") != "Bearer "+self.token:
            return self.reply(401)
        method = message.get("method")
        self.seen.append(method)
        if "id" not in message:
            return self.reply(202)
        if method != "initialize" and self.headers.get("Mcp-Session-Id") != "s1":
            return self.reply(404)
        result = {"initialize": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}},
                  "tools/list": {"tools": [{"name": "lookup", "description": "d", "inputSchema": {"type": "object"}}]},
                  "tools/call": {"content": [{"type": "text", "text": "from http"}]}}[method]
        body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode()
        headers = [("Mcp-Session-Id", "s1")]
        if self.stream:
            other = json.dumps({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}).encode()
            return self.reply(200, b"event: message\ndata: "+other+b"\n\ndata: "+body+b"\n\n", "text/event-stream", headers)
        self.reply(200, body, headers=headers)


class OverHttp(unittest.TestCase):
    def serve(self, **fields):
        handler = type("Handler", (Server,), {"seen": [], **fields})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        server.elsewhere = f"http://127.0.0.1:{server.server_port}/mcp"
        return server, handler, {"kind": "url", "url": server.elsewhere}

    def test_it_lists_and_calls_over_a_session_and_ends_it(self):
        server, handler, record = self.serve()
        self.assertEqual([tool["tool"] for tool in read_tools(record)], ["lookup"])
        self.assertEqual(handler.seen, ["initialize", "notifications/initialized", "tools/list", "DELETE"])

    def test_it_reads_an_event_stream_past_other_messages(self):
        _, _, record = self.serve(stream=True)
        session = mcp_client.connect(record)
        self.addCleanup(session.close)
        self.assertEqual(session.call("lookup", {}), {"text": "from http", "truncated": False})

    def test_a_token_is_sent_and_a_missing_one_is_refused(self):
        _, _, record = self.serve(token="right")
        self.assertEqual(len(read_tools(record, {"TOKEN": "right"})), 1)
        for secrets in (None, {"TOKEN": "wrong"}):
            with self.subTest(secrets), self.assertRaisesRegex(McpError, "refused the token"):
                read_tools(record, secrets)

    def test_a_redirect_is_not_followed(self):
        server, handler, record = self.serve()
        with self.assertRaisesRegex(McpError, "answered 302"):
            read_tools({"kind": "url", "url": record["url"].replace("/mcp", "/moved")}, {"TOKEN": "x"})
        self.assertEqual(handler.seen, [])

    def test_nothing_there_says_so(self):
        server, _, record = self.serve()
        server.shutdown()
        server.server_close()
        with self.assertRaisesRegex(McpError, "Could not reach"):
            read_tools(record)


def _image_here():
    try:
        return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


IMAGE = "lx-sandbox:pillow-11.3.0"


@unittest.skipUnless(_image_here(), "needs Docker and the sandbox image")
class InDocker(unittest.TestCase):
    def record(self, **fields):
        folder = str(Path(__file__).parent)
        record = check_record({"kind": "docker", "image": IMAGE, "args": ["python", "/srv/mcp_fixture.py", *fields.pop("flags", ())],
                               "mounts": [{"source": folder, "target": "/srv"}], **fields})
        return {**record, "image_id": mcp_client.resolve_image(IMAGE)}

    def test_a_real_container_lists_and_calls_and_leaves_nothing_behind(self):
        record = self.record()
        run = McpRun([definition("shop", record, tool) for tool in read_tools(record) if tool["tool"] == "price.lookup"], {"shop": record}, 3)
        self.assertEqual(run.call("mcp_shop_price_lookup", {"item": "cup"})["text"], "4.5 (call 1)")
        run.close()
        left = subprocess.run(["docker", "ps", "-aq", "--filter", "name=lx-mcp-"], capture_output=True, text=True, timeout=20).stdout
        self.assertEqual(left.strip(), "")

    def test_a_secret_reaches_the_container_by_name(self):
        found = read_tools(self.record(flags=["--env"]), {"LX_TEST_OWN": "value"})
        self.assertEqual(next(tool for tool in found if tool["tool"] == "price.lookup")["description"], "LX_TEST_OWN=value")

    def test_a_missing_image_says_how_to_get_it(self):
        with self.assertRaisesRegex(McpError, "docker pull"):
            mcp_client.resolve_image("lx-no-such-image:0")


if __name__ == "__main__":
    unittest.main()
