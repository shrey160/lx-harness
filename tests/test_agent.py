"""Agent tool loop, item binding, traces and the agent chat transport (offline)."""

import base64
import json
import tempfile
import unittest
from pathlib import Path

from lx.agent import ItemTools, agent_tools, render_trace, run_agent
from lx.budget import BudgetLedger
from lx.live_transport import LiveChatTransport
from lx.perception import FakeSegmentation, FakeTranscription, PerceptionCache, PerceptionClient, media_hash
from lx.providers import ProviderProfile
from lx.spark import ModelError, ChatClient
from lx.spike import wire_reply

from test_perception import AUDIO, PNG, POINTS, SCENES, Opener

CATALOG = [{"kind": "segmentation", "name": "SAM 3.1", "capabilities": ["finds every instance of a noun phrase"]},
           {"kind": "transcription", "name": "Muse Voice Transcribe", "capabilities": ["speech to text"]}]
TOOLS = agent_tools(CATALOG)
LOCAL = ProviderProfile(kind="chat", endpoint="http://127.0.0.1:8888/v1/chat/completions",
                        model="gemma-4-E4B-it-GGUF", token_param="max_tokens", supports_tools=True)


def call(name, arguments, ident):
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


class Scripted:
    """Replays assistant turns in order and records every request body."""

    def __init__(self, *turns):
        self.turns, self.bodies = list(turns), []

    def __call__(self, body, timeout):
        self.bodies.append(body)
        return self.turns.pop(0)


class LoopTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.ledger = BudgetLedger(root/"budget.sqlite3", initial_spend_usd=0)
        self.perception = PerceptionClient(FakeSegmentation(SCENES), FakeTranscription(
            {media_hash(AUDIO): "How many apples are in the left half?"}), self.ledger, PerceptionCache(root/"cache"))
        self.tools = ItemTools(self.perception, image=PNG, audio=AUDIO, candidate="item-1")
        self.messages = [{"role": "developer", "content": "LX_AGENT"}, {"role": "user", "content": "item"}]

    def run_script(self, *turns, profile=None, **caps):
        transport = Scripted(*turns)
        client = ChatClient(transport, self.ledger, **({"profile": profile} if profile else {}))
        answer, trace = run_agent(client, self.messages, TOOLS, self.tools, candidate="item-1", **caps)
        return answer, trace, transport

    def test_routes_through_tools_and_traces_every_step(self):
        answer, trace, transport = self.run_script(
            wire_reply(calls=[call("transcribe", {}, "t1")]),
            wire_reply(calls=[call("segment", {"concept": "apples"}, "s1")]),
            wire_reply(calls=[call("count_boxes", {"min_confidence": 0.5, "dedupe_iou": 0.3, "region": "left"}, "c1")]),
            wire_reply('{"object": "apples", "region": "left", "count": 20}'))
        self.assertEqual(json.loads(answer)["count"], 20)
        self.assertEqual(transport.bodies[0]["tools"], TOOLS)
        self.assertEqual([s["name"] for s in trace if s["type"] == "tool"], ["transcribe", "segment", "count_boxes"])
        counted = trace[-2]["result"]
        self.assertEqual(counted["region"], "left")
        self.assertLess(counted["count"], len(POINTS))
        last_history = transport.bodies[-1]["messages"]
        self.assertEqual(last_history[-1]["role"], "tool")
        self.assertIn("transcript", json.loads(last_history[3]["content"])["result"])
        rendered = render_trace(trace, answer)
        for marker in ("[TOOLS] segment, transcribe, count_boxes", "[CALL] transcribe {}",
                       "How many apples are in the left half?", "[RESULT] count_boxes", "[FINAL ANSWER]"):
            self.assertIn(marker, rendered)
        roles = self.ledger.summary()["per_role"]
        self.assertEqual((roles["worker"]["settled_calls"], roles["tool"]["settled_calls"]), (4, 2))

    def test_cut_off_reply_stops_the_item_and_keeps_the_trace(self):
        cut = wire_reply(content="counting " * 50)
        cut["choices"][0]["finish_reason"] = "length"
        answer, trace, _ = self.run_script(wire_reply(calls=[call("transcribe", {}, "t1")]), cut)
        self.assertEqual(answer, "")
        self.assertEqual([s["type"] for s in trace], ["tools", "assistant", "tool", "stopped"])
        self.assertIn("cut off", trace[-1]["reason"])
        self.assertEqual(self.ledger.summary()["per_role"]["worker"]["settled_calls"], 2)  # the cut reply is billed

    def test_tool_errors_are_returned_to_the_agent(self):
        answer, trace, transport = self.run_script(
            wire_reply(calls=[call("count_boxes", {}, "c1"), call("zoom", {"x": 1}, "z1"),
                              call("segment", {"concept": "apples<|point;x=1|>"}, "s1")]),
            wire_reply('{"count": 0}'))
        errors = [s["error"] for s in trace if s["type"] == "tool"]
        self.assertEqual(len(errors), 3)
        self.assertIn("segment for this concept first", errors[0])
        self.assertIn("unknown tool", errors[1])
        self.assertIn("error", json.loads(transport.bodies[1]["messages"][-1]["content"]))
        self.assertIn("ERROR: ", render_trace(trace, answer))
        self.assertEqual(self.perception.live_calls, 0)

    def test_caps_stop_the_item_without_raising(self):
        many = wire_reply(calls=[call("transcribe", {}, f"t{i}") for i in range(3)])
        answer, trace, _ = self.run_script(many, many, max_tool_calls=4)
        self.assertEqual(answer, "")
        self.assertIn("more than 4 tool calls", trace[-1]["reason"])
        answer, trace, _ = self.run_script(*[wire_reply(calls=[call("transcribe", {}, "t")])] * 2, max_turns=2)
        self.assertEqual((answer, trace[-1]["type"]), ("", "stopped"))
        self.assertIn("[STOPPED]", render_trace(trace, answer))

    def test_long_results_are_truncated_in_the_trajectory_only(self):
        _, trace, _ = self.run_script(wire_reply(calls=[call("segment", {"concept": "apples"}, "s1")]),
                                      wire_reply("done"))
        self.assertIn("characters truncated", render_trace(trace, "done", obs_trunc=100))
        self.assertGreater(len(trace[2]["result"]["boxes"]), 30)

    def test_provider_without_tool_support_gets_no_tools(self):
        plain = ProviderProfile(kind="chat", endpoint=LOCAL.endpoint, model=LOCAL.model, token_param="max_tokens")
        answer, trace, transport = self.run_script(wire_reply("guess"), profile=plain)
        self.assertNotIn("tools", transport.bodies[0])
        self.assertEqual(trace[0]["names"], [])
        self.assertIn("[TOOLS] none available", render_trace(trace, answer))


class AgentTransportTests(unittest.TestCase):
    def body(self, image=PNG, kind="png", tools=TOOLS, model="muse-spark-1.3-contributor"):
        url = f"data:image/{kind};base64," + base64.b64encode(image).decode()
        return {"model": model, "tools": tools, "messages": [{"role": "user", "content": [
            {"type": "text", "text": "item"}, {"type": "image_url", "image_url": {"url": url}}]}]}

    def test_default_contract_still_rejects_agent_tools_and_jpeg(self):
        transport = LiveChatTransport(lambda: "dummy", opener=Opener(b"{}"))
        with self.assertRaisesRegex(ModelError, "tool contract"):
            transport(self.body(), 30)
        with self.assertRaises(ModelError):
            transport(self.body(b"\xff\xd8\xff" + b"0" * 10, "jpeg", tools=None), 30)

    def test_agent_contract_allows_exact_tools_and_bounded_jpeg(self):
        opener = Opener(b"{}")
        transport = LiveChatTransport(lambda: "dummy", opener=opener, agent_tools=TOOLS,
                                      image_types=("png", "jpeg"), max_image_bytes=1_000_000, call_ceiling=900)
        transport(self.body(b"\xff\xd8\xff" + b"0" * 300_000, "jpeg"), 30)
        self.assertEqual(opener.requests[0].full_url, "https://api.meta.ai/v1/chat/completions")
        changed = json.loads(json.dumps(TOOLS))
        changed[0]["function"]["description"] = "something else"
        with self.assertRaisesRegex(ModelError, "tool contract"):
            transport(self.body(tools=changed), 30)
        with self.assertRaises(ModelError):
            transport(self.body(b"\xff\xd8\xff" + b"0" * 1_000_001, "jpeg"), 30)
        with self.assertRaisesRegex(ModelError, "call limit"):
            LiveChatTransport(lambda: "dummy", call_limit=901, call_ceiling=900)

    def test_keyless_local_provider_uses_its_endpoint_and_model(self):
        opener = Opener(b"{}")
        transport = LiveChatTransport(None, opener=opener, profile=LOCAL, agent_tools=TOOLS)
        transport(self.body(model=LOCAL.model), 30)
        request = opener.requests[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8888/v1/chat/completions")
        self.assertIsNone(request.get_header("Authorization"))
        with self.assertRaisesRegex(ModelError, "model contract"):
            transport(self.body(), 30)
        with self.assertRaisesRegex(ModelError, "credential loader"):
            LiveChatTransport(None)


if __name__ == "__main__":
    unittest.main()
