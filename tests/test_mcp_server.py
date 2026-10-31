"""The MCP server: the SDK's own client takes a workspace to a finished preview, offline, and nothing a person holds is offered."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
import unittest

from lx import sandbox
from lx.inventory import write_synthetic
from lx.orchestration import ACTIONS
from test_run_survival import ReleasesItsFiles
from test_tasks import Base

try:
    from mcp import Client, StdioServerParameters
    from lx.mcp_server import server_for
except ImportError:
    Client = None


def reply(result):
    return result.structured_content if result.structured_content is not None else json.loads(result.content[0].text)


@unittest.skipIf(Client is None, "needs lx-harness[mcp]")
class Tools(Base):
    def setUp(self):
        super().setUp()
        self.path = self.folder()
        write_synthetic(Path(self.path)/"samples", 6)

    def using(self, flow):
        async def run():
            async with Client(server_for(self.app)) as client:
                return await flow(client)
        return asyncio.run(run())

    def test_the_tools_are_the_actions(self):
        async def flow(client):
            return (await client.list_tools()).tools
        tools = self.using(flow)
        self.assertEqual({tool.name for tool in tools}, set(ACTIONS))
        read = {tool.name for tool in tools if tool.annotations.read_only_hint}
        self.assertEqual(read, {"status", "samples", "read_sample", "truth_shape", "spec_show", "check_test", "run_status", "results"})

    def test_a_refused_action_comes_back_as_an_error_with_its_reason(self):
        async def flow(client):
            await client.call_tool("init", {"workspace": self.path, "template": "photo-request"})
            return await client.call_tool("read_sample", {"workspace": self.path, "path": "sample-01/truth.json"})
        result = self.using(flow)
        self.assertTrue(result.is_error)
        self.assertIn("Answer files cannot be read", result.content[0].text)

    def test_an_unexpected_failure_keeps_its_details_private(self):
        async def flow(client):
            return await client.call_tool("spec_show", {"workspace": self.path})
        self.app.open_workspace = lambda path: (_ for _ in ()).throw(KeyError("private/path"))
        result = self.using(flow)
        self.assertTrue(result.is_error)
        self.assertNotIn("private/path", result.content[0].text)

    def test_a_live_run_stays_pending(self):
        async def flow(client):
            await client.call_tool("init", {"workspace": self.path, "template": "photo-request"})
            await client.call_tool("spec_draft", {"workspace": self.path, "mode": "live"})
            return reply(await client.call_tool("run_start", {"workspace": self.path}))
        self.app.registry.update("spark-contributor", api_key="dummy-ui-key")
        self.assertEqual(self.using(flow)["status"], "pending")


@unittest.skipIf(Client is None, "needs lx-harness[mcp]")
@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class WholeFlow(ReleasesItsFiles):
    def test_a_workspace_goes_from_samples_to_a_finished_preview_over_stdio(self):
        path = self.folder()
        write_synthetic(Path(path)/"samples", 6)
        server = StdioServerParameters(command=sys.executable, args=["-m", "lx.mcp_server", "--root", str(self.app.root)],
                                       env={**os.environ, "PYTHONUTF8": "1"})

        async def flow():
            async with Client(server) as client:
                call = lambda name, **arguments: client.call_tool(name, {"workspace": path, **arguments})
                self.assertFalse((await client.call_tool("status")).is_error)
                self.assertEqual(reply(await call("init", template="photo-request", goal="Count things in photos"))["version"], 2)
                self.assertTrue(reply(await call("samples"))["inventory"])
                self.assertTrue(reply(await call("read_sample", path="sample-01/request.txt"))["text"])
                self.assertTrue(reply(await call("check_test"))["samples"])
                run_id = reply(await call("preview"))["run_id"]
                deadline = time.time()+400
                while time.time() < deadline:
                    run = reply(await call("run_status"))["runs"][0]
                    if run["status"] not in {"queued", "running"}:
                        break
                    await asyncio.sleep(1)
                self.assertEqual(run["status"], "completed", run["error"])
                return reply(await call("results", run_id=run_id))

        finished = asyncio.run(flow())
        self.assertIn("means", finished)
        self.assertIn("What was learned", finished["learned"])
        self.addCleanup(self.clear_when_released, path)


if __name__ == "__main__":
    unittest.main()
