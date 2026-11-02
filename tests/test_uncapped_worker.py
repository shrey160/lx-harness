"""No LX reply cap is opt-in for free local workers; other roles remain bounded."""
from dataclasses import replace
from decimal import Decimal
import json
import tempfile
from pathlib import Path
import unittest

from lx import limits
from lx.budget import BudgetLedger
from lx.engine import Engine
from lx.providers import ProviderProfile, SPARK
from lx.spark import ChatClient, ModelError, normalize
from lx.spike import wire_reply
from test_tasks import Base


LOCAL = ProviderProfile(kind="chat", endpoint="http://127.0.0.1:8888/v1/chat/completions", model="local", token_param="max_tokens")


class Protocol(unittest.TestCase):
    def test_uncapped_wire_omits_limit_and_records_more_than_old_outer_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp)/"ledger.db", initial_spend_usd=0)
            seen = []
            def transport(body, timeout):
                seen.append(body)
                reply = wire_reply("done")
                reply["usage"]["completion_tokens"] = 50000
                return reply
            client = ChatClient(transport, ledger, max_tokens=None, profile=LOCAL, call_seconds=1800)
            answer, usage = client.chat([{"role": "user", "content": "go"}], role="worker")
            self.assertEqual(answer["content"], "done")
            self.assertNotIn("max_tokens", seen[0])
            self.assertNotIn("max_completion_tokens", seen[0])
            self.assertEqual(usage["output_tokens"], 50000)
            summary = ledger.summary()
            self.assertEqual(summary["per_role"]["worker"]["output_tokens"], 50000)
            self.assertEqual(summary["per_role"]["worker"]["spent_usd"], 0)
            self.assertEqual(summary["reserved_usd"], 0)

    def test_uncapped_public_or_priced_profiles_are_refused(self):
        for profile in (SPARK, replace(LOCAL, endpoint="https://example.com/v1/chat/completions"),
                        replace(LOCAL, output_price=Decimal("0.1")), replace(LOCAL, cached_price=Decimal("0.1"))):
            with self.subTest(profile=profile.endpoint), self.assertRaisesRegex(ValueError, "free local"):
                ChatClient(None, None, max_tokens=None, profile=profile)
        with self.assertRaises(ModelError):
            normalize(wire_reply("done"), max_tokens=None)

    def test_input_context_and_finite_reply_bounds_still_hold(self):
        response = wire_reply("done")
        response["usage"]["prompt_tokens"] = LOCAL.context_limit + 1
        with self.assertRaises(ModelError):
            normalize(response, max_tokens=None, profile=LOCAL)
        response = wire_reply("done")
        response["usage"]["completion_tokens"] = 8193
        with self.assertRaises(ModelError):
            normalize(response, max_tokens=8192, profile=LOCAL)

    def test_new_limit_and_legacy_resolution(self):
        limits.check({"worker_reply_tokens": 0})
        values, own = limits.resolve(limits.preset(False), {"worker_reply_tokens": 0})
        self.assertEqual(values["worker_reply_tokens"], 0)
        self.assertEqual(own["worker_reply_tokens"], 0)
        values, _ = limits.resolve(limits.preset(False), {"reply_tokens": 16000})
        self.assertEqual(values["worker_reply_tokens"], 16000)
        for value in (-1, True, None, 32769):
            with self.assertRaises(ValueError):
                limits.check({"worker_reply_tokens": value})
        old = {"providers": {r: {"id": "spark-contributor", "limits": {}} for r in ("orchestrator", "improver", "worker")},
               "limits": {"reply_tokens": 16000}}
        self.assertEqual(Engine.limits_of(object.__new__(Engine), old)["worker_reply_tokens"], 16000)


class Clients(Base):
    def test_only_local_worker_is_uncapped_and_cloud_improver_remains_bounded(self):
        project = self.inventory()
        local = self.entry("local", url="http://127.0.0.1:8888/v1")
        self.app.registry.update(local, pricing={"input_per_million": 0, "output_per_million": 0})
        project = self.app.edit_spec(project["id"], {"roles": {"worker": local, "improver": "spark-contributor", "orchestrator": "external-agent"},
                                                    "limits": {"worker_reply_tokens": 0}})
        clients = self.app.build_clients(self.app.workspace(project["id"]), project["draft"], "bounded-test", 1)
        self.assertIsNone(clients["worker"].max_tokens)
        self.assertEqual(clients["improver"].max_tokens, 8192)
        self.assertIsNone(project["blocked"])
        project = self.app.edit_spec(project["id"], {"judge": {"entry": "spark-contributor", "cap_usd": 0.25,
            "criteria": [{"name": "arithmetic", "question": "Is the arithmetic consistent?"}]}})
        clients = self.app.build_clients(self.app.workspace(project["id"]), project["draft"], "judge-isolation", 1)
        self.assertIsNone(clients["worker"].max_tokens)
        self.assertEqual(clients["judge"].max_tokens, 8192)
        project = self.app.edit_spec(project["id"], {"roles": {"worker": "spark-contributor", "improver": "spark-contributor", "orchestrator": "external-agent"},
                                                    "limits": {"worker_reply_tokens": 0}})
        self.assertIn("free local", project["blocked"])


if __name__ == "__main__":
    unittest.main()
