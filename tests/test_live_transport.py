"""Mocked-network tests for the O-11 live transport and live wiring; no real calls."""

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import urllib.error

from lx.live_transport import ENDPOINT, LiveChatTransport, validate_body
from lx.proposer import TOOLS
from lx.spark import MODEL, ModelError, RateLimited
from lx.spike import wire_reply


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self, _limit):
        return self.payload


PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32).decode()


def worker_body():
    return {"model": MODEL, "messages": [
        {"role": "developer", "content": "LX_WORKER\nprompt"},
        {"role": "user", "content": [
            {"type": "text", "text": json.dumps({"sample_id": "s", "transcript": "t"})},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + PNG}}]}]}


class LiveTransportTests(unittest.TestCase):
    def setUp(self):
        self.opener = Mock()
        self.transport = LiveChatTransport(lambda: "dummy-test-key", opener=self.opener)

    def test_fixed_endpoint_model_and_auth(self):
        self.opener.open.return_value = _Response(b'{"choices": []}')
        self.assertEqual(self.transport(worker_body(), 5), {"choices": []})
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + "dummy-test-key")
        self.assertEqual(json.loads(request.data)["model"], MODEL)

    def test_discussion_and_tool_shapes_accepted(self):
        validate_body({"model": MODEL, "messages": [{"role": "user", "content": "goal"}]})
        validate_body({"model": MODEL, "tools": TOOLS, "messages": [
            {"role": "user", "content": "mission"},
            {"role": "assistant", "content": None, "tool_calls": []},
            {"role": "tool", "tool_call_id": "1", "content": "{}"}]})

    def test_rejects_other_model_and_tools(self):
        body = worker_body(); body["model"] = "other"
        with self.assertRaises(ModelError):
            self.transport(body, 5)
        body = worker_body(); body["tools"] = [{"type": "function"}]
        with self.assertRaises(ModelError):
            self.transport(body, 5)
        self.opener.open.assert_not_called()

    def test_rejects_external_and_multiple_images(self):
        body = worker_body()
        body["messages"][1]["content"][1]["image_url"]["url"] = "https://example.invalid/x.png"
        with self.assertRaises(ModelError):
            validate_body(body)
        body = worker_body()
        body["messages"][1]["content"].append(
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + PNG}})
        with self.assertRaises(ModelError):
            validate_body(body)

    def test_rejects_non_png_data(self):
        body = worker_body()
        bad = base64.b64encode(b"not-a-png").decode()
        body["messages"][1]["content"][1]["image_url"]["url"] = "data:image/png;base64," + bad
        with self.assertRaises(ModelError):
            validate_body(body)

    def test_call_limit(self):
        transport = LiveChatTransport(lambda: "k", call_limit=1, opener=self.opener)
        self.opener.open.return_value = _Response(b'{"choices": []}')
        transport(worker_body(), 5)
        with self.assertRaisesRegex(ModelError, "call limit"):
            transport(worker_body(), 5)

    def test_429_and_error_body_withheld(self):
        self.opener.open.side_effect = urllib.error.HTTPError(
            ENDPOINT, 429, "busy", {"Retry-After": "1.5"}, BytesIO(b"private"))
        with self.assertRaises(RateLimited) as raised:
            self.transport(worker_body(), 5)
        self.assertEqual(raised.exception.retry_after, 1.5)
        raised.exception.__context__.close()
        self.opener.open.side_effect = urllib.error.HTTPError(
            ENDPOINT, 500, "x", {}, BytesIO(b"private provider detail"))
        with self.assertRaises(ModelError) as raised:
            self.transport(worker_body(), 5)
        self.assertNotIn("private provider detail", str(raised.exception))
        raised.exception.__context__.close()

    def test_missing_credential_fails_closed(self):
        with self.assertRaises(ModelError):
            LiveChatTransport(lambda: "", opener=self.opener)


@unittest.skipUnless(os.name in ("posix", "nt"), "requires a WSL, Linux or Windows controller")
class LiveWiringTests(unittest.TestCase):
    def setUp(self):
        from lx.engine import Engine
        from lx.workflow import PreviewTransport
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Engine(Path(self.temp.name))
        truth = self.app.demo.controller_truth
        self.app._live_transport_factory = lambda hint=None: PreviewTransport(hint if hint is not None else truth)

    def folder(self):
        path = Path(tempfile.mkdtemp(dir=self.temp.name))
        return str(path)

    def _live_project(self):
        project = self.app.create_project({"source": "demo", "synthetic_public": True, "name": "Live wiring", "path": self.folder()})
        project = self.app.edit_spec(project["id"], {"goal": project["goal"], "budget_usd": 5, "mode": "live"})
        return self.app.approve(project["id"], {"version": project["draft"]["version"],
                                                "data_approved": True, "contributor_accepted": True, "notice_accepted": True})

    def test_live_discussion_uses_the_workspace_ledger_only(self):
        project = self._live_project()
        ledger = self.app.workspace(project["id"]).preview_ledger
        before = ledger.summary()["settled_calls"]
        updated = self.app.discuss(project["id"], "Keep exact text", True, "live")
        self.assertEqual(len(updated["messages"]), 2)
        spending = self.app._ledger(self.app.workspace(project["id"]))
        rows = [r for r in __import__("lx.usage", fromlist=["usage_rows"]).usage_rows(spending, "discussion-")]
        self.assertTrue(rows)
        self.assertEqual(ledger.summary()["settled_calls"], before)

    def test_live_job_completes_with_mocked_transport_and_fresh_reuse(self):
        from lx.worker import execute
        project = self._live_project()
        job = self.app.start(project["id"], launch=False)
        workspace = self.app.workspace(project["id"])
        result = execute(self.app, workspace, job["id"], workspace.run_dir(job["id"]) / "snapshot")
        self.assertTrue(result["live"])
        self.assertFalse(result["simulated_usage"])
        self.assertTrue(all(result["checks"]["heldout"].values()))
        self.assertGreater(self.app._ledger(workspace).summary()["settled_calls"], 0)
        reuse = self.app.fresh(project["id"], job["id"])
        self.assertEqual(reuse["mode"], "live")
        self.assertTrue(reuse["accepted"], reuse)

    def test_live_unavailable_without_shared_ledger(self):
        from lx.engine import Engine
        other = Engine(Path(self.temp.name) / "other")
        self.assertIsNone(other.pilot_ledger)
        project = other.create_project({"source": "demo", "synthetic_public": True, "path": self.folder()})
        with self.assertRaises(ValueError):
            other.discuss(project["id"], "hello", True, "live")
        with self.assertRaises(ValueError):
            other.edit_spec(project["id"], {"goal": "g", "budget_usd": 1, "mode": "bogus"})

class TimeLimitTests(unittest.TestCase):
    def test_a_stated_call_bound_replaces_the_default_one(self):
        from dataclasses import replace
        from lx.providers import SPARK
        seen = []
        class Opener:
            def open(self, request, timeout):
                seen.append(timeout)
                return _Response(b"{}")
        body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
        LiveChatTransport(lambda: "dummy", opener=Opener(), profile=replace(SPARK, model="m"), max_call_seconds=45)(body, 300)
        LiveChatTransport(lambda: "dummy", opener=Opener(), profile=replace(SPARK, model="m"))(body, 900)
        self.assertEqual(seen, [45, 600])

    def test_a_rate_spaces_calls_evenly(self):
        from dataclasses import replace
        from lx.providers import SPARK
        now, waits = [100.0], []
        def sleep(seconds):
            waits.append(seconds)
            now[0] += seconds
        opener = Mock()
        opener.open.side_effect = lambda *_a, **_k: _Response(b"{}")
        transport = LiveChatTransport(lambda: "dummy", opener=opener, profile=replace(SPARK, model="m"), requests_per_minute=40,
                                      sleep=sleep, clock=lambda: now[0])
        body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
        for _ in range(3):
            transport(body, 5)
        self.assertEqual(waits, [1.5, 1.5])
        now[0] += 10
        transport(body, 5)
        self.assertEqual(len(waits), 2)  # idle time needs no wait

    def test_a_bad_rate_is_refused(self):
        with self.assertRaises(ModelError):
            LiveChatTransport(lambda: "dummy", requests_per_minute=0)

    def test_a_timeout_is_named_as_one(self):
        from dataclasses import replace
        from lx.providers import SPARK
        class Slow:
            def open(self, request, timeout):
                raise TimeoutError()
        transport = LiveChatTransport(lambda: "dummy", opener=Slow(), profile=replace(SPARK, model="m"))
        with self.assertRaisesRegex(ModelError, "did not reply in time"):
            transport({"model": "m", "messages": [{"role": "user", "content": "x"}]}, 5)


if __name__ == "__main__":
    unittest.main()
