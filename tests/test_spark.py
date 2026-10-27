import json
import tempfile
import threading
import unittest
from pathlib import Path

from lx.budget import BudgetExceeded, BudgetLedger
from lx.proposer import FileTools
from lx.spark import ModelError, RateLimited, SparkClient, normalize
from lx.spike import wire_reply


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger = BudgetLedger(self.root/"budget.sqlite3")

    def test_full_assistant_tool_replay_and_tokens(self):
        seen = []
        def transport(body, timeout):
            seen.append(body)
            if len(seen) == 1:
                return wire_reply(calls=[{"id": "abc", "type": "function", "function": {
                    "name": "read_file", "arguments": '{"path":"source.txt"}'}}])
            return wire_reply("finished")
        client = SparkClient(transport, self.ledger)
        text, _ = client.session([{"role": "user", "content": "work"}], lambda n,a: "source", [])
        self.assertEqual(text, "finished")
        history = seen[1]["messages"]
        self.assertEqual(history[-2]["tool_calls"][0]["id"], "abc")
        self.assertEqual(history[-1]["tool_call_id"], "abc")
        self.assertEqual(history[-1]["role"], "tool")
        totals = self.ledger.summary()["per_role"]["improver"]
        self.assertEqual(totals["input_tokens"], 240)
        self.assertEqual(totals["output_tokens"], 64)
        self.assertEqual(self.ledger.summary()["reserved_usd"], 0)

    def test_cap_rejects_before_transport(self):
        ledger = BudgetLedger(self.root/"small.sqlite3", cap_usd="0.01", initial_spend_usd=0)
        client = SparkClient(lambda *a: self.fail("transport invoked after cap"), ledger)
        with self.assertRaises(BudgetExceeded):
            client.chat([])

    def test_missing_usage_keeps_reservation_across_restart(self):
        client = SparkClient(lambda *a: {"choices": []}, self.ledger)
        with self.assertRaises(ModelError):
            client.chat([])
        state = BudgetLedger(self.root/"budget.sqlite3").summary()
        self.assertEqual(state["unknown_count"], 1)
        self.assertGreater(state["reserved_usd"], 0)

    def test_malformed_tool_not_dispatched(self):
        reply = wire_reply(calls=[{"id": "a", "type": "function", "function": {
            "name": "write_file", "arguments": "not json"}}])
        with self.assertRaises(ModelError):
            SparkClient(lambda *a: reply, self.ledger).session([], lambda *a: self.fail("dispatched"), [])
        self.assertEqual(self.ledger.summary()["settled_calls"], 1)

    def test_provider_overrun_records_actual_cost_and_blocks_future_dispatch(self):
        reply = wire_reply("ok")
        reply["usage"]["completion_tokens"] = 1_000_000
        client = SparkClient(lambda *a: reply, self.ledger)
        with self.assertRaises(BudgetExceeded): client.chat([])
        state = self.ledger.summary()
        self.assertEqual(state["error_state"], "actual_cost_exceeded_reservation")
        self.assertGreater(state["per_role"]["improver"]["spent_usd"], 0.2)
        with self.assertRaises(BudgetExceeded): client.chat([])

    def test_rate_limit_retries_owned_by_protocol_and_held(self):
        seen = []
        class InstantCancel:
            def is_set(self): return False
            def wait(self, _): return False
        def transport(*args):
            seen.append(1)
            if len(seen) == 1: raise RateLimited()
            return wire_reply("ok")
        SparkClient(transport, self.ledger, cancel=InstantCancel()).chat([])
        self.assertEqual(len(seen), 2)
        self.assertEqual(self.ledger.summary()["unknown_count"], 1)

    def test_cancellation_prevents_dispatch(self):
        cancel = threading.Event()
        cancel.set()
        client = SparkClient(lambda *a: self.fail("transport invoked"), self.ledger, cancel=cancel)
        with self.assertRaises(ModelError): client.chat([])

    def test_cancel_during_dispatch_records_usage_and_suppresses_response(self):
        cancel = threading.Event()
        def transport(*args):
            cancel.set()
            return wire_reply("do not use this result")
        client = SparkClient(transport, self.ledger, cancel=cancel)
        with self.assertRaises(ModelError): client.chat([])
        self.assertEqual(self.ledger.summary()["settled_calls"], 1)
        self.assertEqual(self.ledger.summary()["reserved_usd"], 0)

    def test_usage_and_completion_validation(self):
        for value in (True, -1, None, 1.2):
            reply = wire_reply("ok")
            reply["usage"]["prompt_tokens"] = value
            with self.assertRaises(ModelError): normalize(reply, max_tokens=4096)
        reply = wire_reply("partial")
        reply["choices"][0]["finish_reason"] = "length"
        with self.assertRaises(ModelError): normalize(reply, max_tokens=4096)
        reply = wire_reply("ok")
        reply["choices"][0]["message"]["tool_calls"] = {}
        with self.assertRaises(ModelError): normalize(reply, max_tokens=4096)
        reply = wire_reply("ok")
        reply["choices"][0]["finish_reason"] = "tool_calls"
        with self.assertRaises(ModelError): normalize(reply, max_tokens=4096)

    def test_file_tools_constrain_writes_and_paths(self):
        (self.root/"harness").mkdir()
        (self.root/"source.txt").write_text("input")
        files = FileTools(self.root)
        files("write_file", {"path": "harness/test.txt", "content": "ok"})
        self.assertEqual(files("read_file", {"path": "harness/test.txt"}), "ok")
        for name in ("../elsewhere", "/outside", "C:/outside", "harness/../../outside", ".env", "harness/.env.example", "key.pem", "config.env", "config.env.local", "credentials.json", "service-account.json", "id_rsa"):
            with self.assertRaises(ValueError): files("read_file", {"path": name})
        with self.assertRaises(ValueError): files("write_file", {"path": "source.txt", "content": "changed"})
        self.assertEqual((self.root/"source.txt").read_text(), "input")

    @unittest.skipUnless(__import__("os").name == "posix", "requires symlink support")
    def test_linked_file_access_rejected(self):
        (self.root/"harness").mkdir()
        (self.root/"source.txt").write_text("input")
        (self.root/"harness"/"linked.txt").symlink_to(self.root/"source.txt")
        files = FileTools(self.root)
        with self.assertRaises(ValueError): files("read_file", {"path": "harness/linked.txt"})
        with self.assertRaises(ValueError): files("write_file", {"path": "harness/linked.txt", "content": "changed"})
