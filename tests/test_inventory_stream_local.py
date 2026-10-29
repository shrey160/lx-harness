"""G1/G1b swapped-agent controllers offline: doubles only, no credentials, network or provider server."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lx import inventory_agent_probe as agent_probe
from lx import inventory_stream as stream
from lx import inventory_stream_local as local
from lx import sandbox
from lx.budget import BudgetLedger
from lx.inventory import FakeShapeSegmentation
from lx.inventory_data import build_synthetic_dataset
from lx.inventory_probe import TOOLS as AGENT_TOOLS
from lx.inventory_probe import Probe
from lx.inventory_workflow import InventoryPreviewTransport, preview_transcription
from lx.live_transport import validate_body
from lx.perception import PerceptionCache, PerceptionClient
from lx.proposer import TOOLS as FILE_TOOLS
from lx.providers import SPARK
from lx.spark import ModelError, SparkClient
from lx.spike import wire_reply
from lx.usage import BudgetScope

URL = "http://172.20.0.1:8888/v1"
PROBE_SCOPE = "inventory-agent-probe-v2-thinking-on"
MODEL = "LiquidAI/LFM3-VL-2B-GGUF"


class Checked:
    """A preview double behind the live transport's body contract."""

    def __init__(self, **contract):
        self.inner, self.contract, self.bodies = InventoryPreviewTransport(), contract, []

    def __call__(self, body, timeout):
        validate_body(body, image_types=("jpeg",), max_images=2, **self.contract)
        self.bodies.append(body)
        return self.inner(body, timeout)


class LocalStreamTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def _synthetic(self):
        scenes, requests = build_synthetic_dataset(self.root / "ds", 2)
        ledger = BudgetLedger(self.root / "ledger.sqlite3", initial_spend_usd=0)
        perception = PerceptionClient(FakeShapeSegmentation(scenes), preview_transcription(self.root / "ds", requests),
                                      ledger, PerceptionCache(self.root / "cache"))
        return BudgetScope(ledger, "g1-test", "5"), perception

    @unittest.skipUnless(sandbox.available(), "the preview skill runs its script in the sandbox")
    def test_agent_and_improver_use_separate_providers(self):
        scope, perception = self._synthetic()
        profile = local.agent_profile(URL, MODEL)
        agent_side = Checked(model=MODEL, tool_sets=(FILE_TOOLS, AGENT_TOOLS))
        improver_side = Checked(model=SPARK.model, tool_sets=(FILE_TOOLS,))
        agent = SparkClient(agent_side, scope, max_tokens=local.MAX_OUTPUT_TOKENS, profile=profile)
        report = stream.run_ea(self.root / "ds", self.root / "out", agent, perception,
                               improver=SparkClient(improver_side, scope))
        self.assertEqual(report["run"]["done"], 2)
        # The swapped provider only ran the agent; Spark only improved and reflected.
        self.assertGreater(agent_side.inner.calls["worker"], 0)
        self.assertEqual(agent_side.inner.calls["improver"] + agent_side.inner.calls["reflection"], 0)
        self.assertEqual(improver_side.inner.calls["worker"], 0)
        self.assertGreater(improver_side.inner.calls["improver"], 0)
        self.assertTrue(all(b["model"] == MODEL and b["max_tokens"] == 1536 for b in agent_side.bodies))
        for row in report["per_sample"].values():
            self.assertIn("scripts/sum_counts.py", row["files_added"])  # scripts stay available (WSL sandbox)

    def test_agent_profile_is_an_ordinary_provider_entry(self):
        profile = local.agent_profile(URL + "/", MODEL)
        self.assertEqual((profile.endpoint, profile.model), (URL + "/chat/completions", MODEL))
        self.assertEqual((profile.token_param, profile.accepts_images, profile.supports_tools), ("max_tokens", True, True))
        self.assertEqual(profile.input_price + profile.output_price, 0)
        self.assertEqual(local.agent_profile("https://models.example.com/v1", MODEL).endpoint,
                         "https://models.example.com/v1/chat/completions")
        for bad in ("http://example.com:8888/v1", "ftp://127.0.0.1/v1", "http://user:pw@127.0.0.1:8888/v1"):
            with self.assertRaises(ValueError):
                local.agent_profile(bad, MODEL)
        with self.assertRaises(ValueError):
            local.agent_profile(URL, " ")

    def test_thinking_switch_and_reply_cap_reach_the_request(self):
        scope, _ = self._synthetic()
        def capture(bodies):
            return lambda body, timeout: bodies.append(body) or wire_reply("ok")
        for thinking, expected in (("on", {"enable_thinking": True}), ("off", {"enable_thinking": False}), (None, None)):
            bodies = []
            client = SparkClient(capture(bodies), scope, max_tokens=8192, profile=local.agent_profile(URL, MODEL, thinking))
            client.chat([{"role": "user", "content": "hi"}], role="worker", phase="execute")
            self.assertEqual(bodies[0].get("chat_template_kwargs"), expected)
            self.assertEqual(bodies[0]["max_tokens"], 8192)
            validate_body(bodies[0], model=MODEL, tool_sets=(FILE_TOOLS, AGENT_TOOLS))  # the live contract accepts it
        bodies = []
        SparkClient(capture(bodies), scope).chat([{"role": "user", "content": "hi"}])
        self.assertNotIn("chat_template_kwargs", bodies[0])  # Spark's request is unchanged
        with self.assertRaises(ValueError):
            local.ProviderProfile(kind="chat", endpoint=URL + "/chat/completions", model=MODEL, enable_thinking="on")

    def test_key_comes_from_a_named_variable_never_the_meta_key(self):
        self.assertIsNone(local.key_loader(None))
        for bad in ("MODEL_API_KEY", "META_API_KEY", "lower", "BAD-NAME", ""):
            with self.assertRaises(ValueError):
                local.key_loader(bad)
        loader = local.key_loader("LX_TEST_AGENT_KEY")
        core = self.root / "core"
        core.mkdir()
        with patch("lx.live.CORE", core):  # never the real core .env
            with patch.dict(os.environ, {"LX_TEST_AGENT_KEY": "k"}):
                self.assertEqual(loader(), "k")
            with patch.dict(os.environ, {}, clear=True), self.assertRaises(ModelError):
                loader()
            (core / ".env").write_text("META_API_KEY=meta\nexport LX_TEST_AGENT_KEY='from-file'\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(loader(), "from-file")  # read from the core .env like the Meta key
                with self.assertRaises(ModelError):  # an absent name is not filled from other entries
                    local.key_loader("OTHER_KEY")()

    def test_main_refuses_bad_inputs_before_the_ledger(self):
        seen = []

        def unloaded(url, key):
            seen.append((url, key))
            return ["gemma-4-E4B-it-GGUF"]
        refusals = [(["--agent-base-url", "http://example.com/v1"], None),
                    (["--agent-base-url", URL, "--agent-max-tokens", "9000"], None),
                    (["--agent-base-url", URL, "--agent-thinking", "maybe"], None),
                    (["--agent-base-url", URL, "--agent-key-env", "META_API_KEY"], None),
                    (["--agent-base-url", URL], unloaded),
                    (["--agent-base-url", URL], lambda url, key: (_ for _ in ()).throw(OSError("down")))]
        for module in (local, agent_probe):
            with patch.object(sys, "argv", ["g", "--approve-dataset", "0" * 64, "--agent-base-url", URL,
                                            "--agent-model", MODEL]), self.assertRaises(SystemExit):
                module.main()
            with patch("lx.inventory_data.inventory_dataset_hash", return_value=local.APPROVED_DATASET), \
                    patch.object(agent_probe, "inventory_dataset_hash", return_value=local.APPROVED_DATASET), \
                    patch.object(sandbox, "available", return_value=True), \
                    patch.object(module, "guarded_ledger", side_effect=AssertionError("must stop first")):
                for extra, served in refusals:
                    fake = served or (lambda url, key: self.fail("no GET for refused settings"))
                    argv = ["g", "--approve-dataset", local.APPROVED_DATASET, "--agent-model", MODEL, *extra]
                    with patch.object(sys, "argv", argv), \
                            patch.object(local, "served_models", lambda url, key, f=fake: f(url, key)), \
                            self.assertRaises(SystemExit):
                        module.main()
        self.assertEqual(seen, [(URL + "/models", None)] * 2)

    def test_probe_runs_the_view_check_and_base_runs(self):
        scope, perception = self._synthetic()
        client = SparkClient(Checked(model=MODEL, tool_sets=(FILE_TOOLS, AGENT_TOOLS)), scope,
                             max_tokens=local.MAX_OUTPUT_TOKENS, profile=local.agent_profile(URL, MODEL))
        with patch.multiple(agent_probe, VIEW_SAMPLE="synth-01", RUN_SAMPLES=("synth-01", "synth-02")):
            report = agent_probe.run_probe(Probe(self.root / "ds"), client, perception)
        self.assertEqual(sorted(report["runs"]), ["synth-01", "synth-02"])
        self.assertIn("view_called", report["view_check"])
        self.assertEqual(report["answered"], 2)
        self.assertIn("transcribe", report["tools_used"])

    def test_guard_refuses_old_ledger_and_reused_scope(self):
        path = self.root / "pilot.sqlite3"
        ledger = BudgetLedger(path)
        with patch.object(local, "LEDGER", path), self.assertRaisesRegex(ValueError, "older than"):
            local.guarded_ledger()
        with patch.multiple(local, LEDGER=path, CHECKPOINT_SPENT=0, CHECKPOINT_CALLS=0):
            local.guarded_ledger(PROBE_SCOPE, agent_probe.RUN_CAP_USD)
            reservation = ledger.reserve("0", "tool", "segment", PROBE_SCOPE + "/segment/x")
            ledger.settle(reservation, cost_usd=0, input_tokens=0, output_tokens=0)
            with self.assertRaisesRegex(ValueError, "already dispatched"):
                local.guarded_ledger(PROBE_SCOPE, agent_probe.RUN_CAP_USD)
            local.guarded_ledger()  # the stream scope is separate

    def test_fixed_request_constants(self):
        self.assertEqual(local.SCOPE, "inventory-ea-local-v1")
        self.assertNotEqual(local.SCOPE, stream.SCOPE)
        self.assertEqual(local.RUN_CAP_USD, "1.00")
        self.assertEqual(local.LIMITS, {"agent_chat": 600, "improver_chat": 500, "segment": 300, "transcribe": 40})
        self.assertEqual((local.CHECKPOINT_SPENT, local.CHECKPOINT_CALLS), (local.Decimal("2.054275772"), 3576))
        self.assertEqual(local.APPROVED_DATASET, "db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322")
        self.assertEqual(agent_probe.RUN_CAP_USD, "0.10")
        for thinking, scope in (("on", PROBE_SCOPE), ("off", "inventory-agent-probe-v2-thinking-off"),
                                (None, "inventory-agent-probe-v2-thinking-default")):
            self.assertEqual(agent_probe.scope_for(type("A", (), {"agent_thinking": thinking})), scope)
        self.assertEqual(agent_probe.RUN_SAMPLES, ("fsc-calibration-02", "mix-calibration-colourcount-01"))
        self.assertEqual(agent_probe.LIMITS, {"agent_chat": 30, "segment": 20, "transcribe": 4})


if __name__ == "__main__":
    unittest.main()
