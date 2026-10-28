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

from lx.live_transport import ENDPOINT, LiveMetaTransport, validate_body
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
        self.transport = LiveMetaTransport(lambda: "dummy-test-key", opener=self.opener)

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
        transport = LiveMetaTransport(lambda: "k", call_limit=1, opener=self.opener)
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
            LiveMetaTransport(lambda: "", opener=self.opener)


@unittest.skipUnless(os.name == "posix", "requires the supported WSL controller")
class LiveWiringTests(unittest.TestCase):
    def setUp(self):
        from lx.app import Application
        from lx.budget import BudgetLedger
        from lx.workflow import PreviewTransport
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Application(Path(self.temp.name))
        self.app.live_ledger = BudgetLedger(Path(self.temp.name) / "live.sqlite3", initial_spend_usd=0)
        truth = self.app.demo.controller_truth
        self.app._live_transport_factory = lambda hint=None: PreviewTransport(hint if hint is not None else truth)

    def _live_project(self):
        project = self.app.create_project({"source": "demo", "synthetic_public": True, "name": "Live wiring"})
        project = self.app.edit_spec(project["id"], {"goal": project["goal"], "budget_usd": 5, "mode": "live"})
        return self.app.approve(project["id"], {"version": project["draft"]["version"],
                                                "data_approved": True, "contributor_accepted": True})

    def test_live_discussion_uses_live_ledger_only(self):
        project = self._live_project()
        before = self.app.preview_ledger.summary()["settled_calls"]
        updated = self.app.discuss(project["id"], "Keep exact text", True, "live")
        self.assertEqual(len(updated["messages"]), 2)
        rows = [r for r in __import__("lx.usage", fromlist=["usage_rows"]).usage_rows(self.app.live_ledger, "discussion-")]
        self.assertTrue(rows)
        self.assertEqual(self.app.preview_ledger.summary()["settled_calls"], before)

    def test_live_job_completes_with_mocked_transport_and_fresh_reuse(self):
        from lx.worker import execute
        project = self._live_project()
        job = self.app.start(project["id"], launch=False)
        result = execute(self.app, job["id"], self.app.root / "inputs" / job["id"])
        self.assertTrue(result["live"])
        self.assertFalse(result["simulated_usage"])
        self.assertTrue(all(result["checks"]["heldout"].values()))
        self.assertGreater(self.app.live_ledger.summary()["settled_calls"], 0)
        reuse = self.app.fresh(job["id"])
        self.assertEqual(reuse["mode"], "live")
        self.assertTrue(reuse["accepted"], reuse)

    def test_live_unavailable_without_shared_ledger(self):
        from lx.app import Application
        other = Application(Path(self.temp.name) / "other")
        self.assertIsNone(other.live_ledger)
        project = other.create_project({"source": "demo", "synthetic_public": True})
        with self.assertRaises(ValueError):
            other.discuss(project["id"], "hello", True, "live")
        with self.assertRaises(ValueError):
            other.edit_spec(project["id"], {"goal": "g", "budget_usd": 1, "mode": "bogus"})


if __name__ == "__main__":
    unittest.main()
