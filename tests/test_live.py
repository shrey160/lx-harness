"""Synthetic-only tests for the guarded Meta transport; never open the network."""

from io import BytesIO
import json
from unittest import TestCase
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

from lx import live
from lx.proposer import TOOLS
from lx.spark import MODEL, ModelError, RateLimited


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self, _limit):
        return self.payload


class MetaTransportTests(TestCase):
    def setUp(self):
        self.ledger = Mock()
        self.ledger.summary.return_value = {
            "settled_calls": 0,
            "reserved_calls": 0,
            "unknown_count": 0,
        }
        self.opener = Mock()
        self.key_patch = patch("lx.live._runtime_key", return_value="dummy-test-key")
        self.opener_patch = patch("lx.live.urllib.request.build_opener", return_value=self.opener)
        self.key_patch.start()
        self.opener_patch.start()
        self.addCleanup(self.key_patch.stop)
        self.addCleanup(self.opener_patch.stop)
        self.transport = live._MetaTransport(self.ledger)

    @staticmethod
    def body():
        return {"model": MODEL, "tools": TOOLS, "messages": [{"role": "user", "content": "synthetic"}]}

    def test_fixed_endpoint_model_tools_and_dummy_auth(self):
        self.opener.open.return_value = _Response(b'{"choices": []}')
        result = self.transport(self.body(), 99)
        request = self.opener.open.call_args.args[0]
        timeout = self.opener.open.call_args.kwargs["timeout"]
        self.assertEqual(result, {"choices": []})
        self.assertEqual(request.full_url, live.ENDPOINT)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer dummy-test-key")
        self.assertEqual(json.loads(request.data), self.body())
        self.assertEqual(timeout, 60)

    def test_request_outside_fixed_model_or_tools_contract_is_rejected(self):
        body = self.body()
        body["model"] = "other-model"
        with self.assertRaises(ModelError):
            self.transport(body, 10)
        self.opener.open.assert_not_called()

    def test_429_uses_retry_after(self):
        self.opener.open.side_effect = urllib.error.HTTPError(
            live.ENDPOINT, 429, "busy", {"Retry-After": "2.5"}, BytesIO(b"private provider body")
        )
        with self.assertRaises(RateLimited) as raised:
            self.transport(self.body(), 10)
        self.assertEqual(raised.exception.retry_after, 2.5)
        raised.exception.__context__.close()

    def test_generic_http_error_withholds_provider_body(self):
        self.opener.open.side_effect = urllib.error.HTTPError(
            live.ENDPOINT, 503, "unavailable", {}, BytesIO(b"private provider detail")
        )
        with self.assertRaises(ModelError) as raised:
            self.transport(self.body(), 10)
        self.assertIn("response body withheld", str(raised.exception))
        self.assertNotIn("private provider detail", str(raised.exception))
        raised.exception.__context__.close()

    def test_malformed_json_is_model_error(self):
        self.opener.open.return_value = _Response(b"not json")
        with self.assertRaisesRegex(ModelError, "connection/JSON failed"):
            self.transport(self.body(), 10)

    def test_more_than_40_ledger_attempts_refuses_before_network(self):
        self.ledger.summary.return_value = {
            "settled_calls": 39,
            "reserved_calls": 1,
            "unknown_count": 1,
        }
        with self.assertRaisesRegex(ModelError, "call limit"):
            self.transport(self.body(), 10)
        self.opener.open.assert_not_called()

    def test_redirects_are_disabled(self):
        args = live.urllib.request.build_opener.call_args.args
        self.assertEqual(len(args), 1)
        self.assertIsInstance(args[0], live._NoRedirect)
        handler = args[0]
        request = urllib.request.Request(live.ENDPOINT)
        self.assertIsNone(handler.redirect_request(request, None, 302, "redirect", {}, "https://elsewhere.invalid"))


if __name__ == "__main__":
    import unittest

    unittest.main()
