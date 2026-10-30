"""Multi-photo agent tools, views, image messages and the transport image bound (offline)."""

import base64
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from lx.agent import IMAGE_KEY, PhotoSetTools, agent_tools, photo_tools, render_trace, render_view, run_agent, tool_identity
from lx.budget import BudgetLedger
from lx.inventory import FakeShapeSegmentation, caps, fake_scenes, synthetic_sample
from lx.live_transport import LiveChatTransport, validate_body
from lx.perception import FakeTranscription, PerceptionCache, PerceptionClient, media_hash
from lx.spark import MODEL, ModelError, ChatClient
from lx.spike import wire_reply

from test_agent import CATALOG, Scripted, call
from test_perception import AUDIO

PHOTOS, TRUTH, TEXT, LAYOUTS = synthetic_sample(11, 1)
TOOLS = photo_tools(CATALOG)


def jpeg_url(size=(40, 30)):
    out = BytesIO()
    Image.new("RGB", size, (200, 10, 10)).save(out, "JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


def body_with(n_images):
    parts = [{"type": "text", "text": "item"}] + [{"type": "image_url", "image_url": {"url": jpeg_url()}}] * n_images
    return {"model": MODEL, "messages": [{"role": "user", "content": parts}]}


def images_in(body):
    return sum(part.get("type") == "image_url" for m in body["messages"] if isinstance(m.get("content"), list)
               for part in m["content"])


class _Fixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.ledger = BudgetLedger(root / "budget.sqlite3", initial_spend_usd=0)
        self.sam = FakeShapeSegmentation(fake_scenes(PHOTOS, LAYOUTS), miss=0, false_positive=1)
        self.perception = PerceptionClient(self.sam, FakeTranscription({media_hash(AUDIO): TEXT}), self.ledger,
                                           PerceptionCache(root / "cache"))
        self.tools = PhotoSetTools(self.perception, photos=PHOTOS, audio=AUDIO, candidate="item-1")


class PhotoToolTests(_Fixture):
    def test_tool_set_names_photos_and_differs_from_single_image_tools(self):
        self.assertEqual([t["function"]["name"] for t in TOOLS], ["segment", "transcribe", "count_boxes", "view"])
        segment = TOOLS[0]["function"]["parameters"]
        self.assertEqual(segment["required"], ["photo", "concept"])
        self.assertNotEqual(tool_identity(TOOLS), tool_identity(agent_tools(CATALOG)))

    def test_segment_and_count_are_keyed_by_photo(self):
        shape, colour, _ = LAYOUTS[0][0]
        expected = sum(1 for s, _, _ in LAYOUTS[0] if s == shape)
        result = self.tools("segment", {"photo": 1, "concept": shape})
        self.assertEqual((result["photo"], result["instances"]), (1, expected + 1))  # one false positive
        counted = self.tools("count_boxes", {"photo": 1, "concept": shape, "min_confidence": 0.5})
        self.assertEqual((counted["count"], counted["removed_low_confidence"]), (expected, 1))
        self.assertNotIn("region", counted)
        with self.assertRaisesRegex(ValueError, "segment for this photo"):
            self.tools("count_boxes", {"photo": 2, "concept": shape})
        coloured = self.tools("segment", {"photo": 1, "concept": f"{colour} {shape}"})
        self.assertEqual(coloured["instances"] - 1, sum(1 for s, c, _ in LAYOUTS[0] if (s, c) == (shape, colour)))

    def test_bad_arguments_are_errors_for_the_agent(self):
        for name, arguments in (("segment", {"concept": "circle"}), ("segment", {"photo": 0, "concept": "circle"}),
                                ("segment", {"photo": len(PHOTOS) + 1, "concept": "circle"}),
                                ("segment", {"photo": "1", "concept": "circle"}), ("view", {"photo": 1, "zoom": 2}),
                                ("view", {"photo": 1, "box": [5, 5, 4, 9]}), ("view", {"photo": 1, "box": [0, 0, 999, 9]}),
                                ("transcribe", {"photo": 1}), ("crop", {})):
            with self.subTest(name=name, arguments=arguments):
                with self.assertRaises(ValueError):
                    self.tools(name, arguments)

    def test_view_whole_photo_and_zoom(self):
        whole = self.tools("view", {"photo": 2})
        self.assertEqual((whole["photo_size"], whole["shown_box"]), ([320, 240], [0, 0, 319, 239]))
        with Image.open(BytesIO(whole[IMAGE_KEY])) as image:
            self.assertEqual((image.format, image.size), ("JPEG", (320, 240)))
        x1, y1, x2, y2 = LAYOUTS[1][0][2]
        zoom = self.tools("view", {"photo": 2, "box": [x1, y1, x2, y2]})
        sx1, sy1, sx2, sy2 = zoom["shown_box"]
        self.assertTrue(sx1 < x1 and sy1 < y1 and sx2 > x2 and sy2 > y2)
        with Image.open(BytesIO(zoom[IMAGE_KEY])) as image:
            self.assertEqual(image.size, (sx2 - sx1 + 1, sy2 - sy1 + 1))
            # The zoom centre is the shape's colour, not the background.
            self.assertNotEqual(image.convert("RGB").getpixel((image.width // 2, image.height // 2))[:3], (235, 232, 225))

    def test_render_view_downscales_large_photos(self):
        out = BytesIO()
        Image.new("RGB", (2000, 1000), (1, 2, 3)).save(out, "PNG")
        data, shown, size = render_view(out.getvalue(), max_side=500)
        with Image.open(BytesIO(data)) as image:
            self.assertEqual(image.size, (500, 250))
        self.assertEqual((shown, size), ((0, 0, 1999, 999), (2000, 1000)))


class ImageLoopTests(_Fixture):
    def run_script(self, *turns, **caps_):
        transport = Scripted(*turns)
        client = ChatClient(transport, self.ledger)
        messages = [{"role": "developer", "content": "LX_AGENT"}, {"role": "user", "content": "item"}]
        answer, trace = run_agent(client, messages, TOOLS, self.tools, candidate="item-1", **caps_)
        return answer, trace, transport

    def test_view_images_follow_the_tool_messages(self):
        answer, trace, transport = self.run_script(
            wire_reply(calls=[call("view", {"photo": 1}, "v1"), call("view", {"photo": 2}, "v2")]),
            wire_reply(content='{"done": true}'))
        self.assertEqual(answer, '{"done": true}')
        roles = [m["role"] for m in transport.bodies[1]["messages"]]
        self.assertEqual(roles[-4:], ["assistant", "tool", "tool", "user"])
        self.assertEqual(images_in(transport.bodies[1]), 2)
        tool_text = transport.bodies[1]["messages"][-3]["content"]
        self.assertIn("sent in the next message", tool_text)
        self.assertNotIn("base64", tool_text)
        rendered = render_trace(trace, answer)
        self.assertIn("[CALL] view", rendered)
        self.assertLess(len(json.dumps(trace)), 5000)

    def test_only_the_latest_images_stay_in_history(self):
        _, _, transport = self.run_script(
            wire_reply(calls=[call("view", {"photo": 1}, "v1")]),
            wire_reply(calls=[call("view", {"photo": 2}, "v2")]),
            wire_reply(calls=[call("view", {"photo": 3}, "v3")]),
            wire_reply(content="{}"), keep_images=2)
        self.assertEqual([images_in(b) for b in transport.bodies], [0, 1, 2, 2])
        self.assertIn("was removed to save space", json.dumps(transport.bodies[-1]["messages"]))
        self.assertNotIn("call view again", json.dumps(transport.bodies[-1]["messages"]))  # Glimmer cycled on that wording

    def test_caps_grow_with_photo_count(self):
        self.assertEqual(caps(3), {"max_turns": 13, "max_tool_calls": 24})
        self.assertEqual(caps(7), {"max_turns": 25, "max_tool_calls": 48})
        for bad in (2, 8, "3"):
            with self.assertRaises(ValueError):
                caps(bad)


class TransportImageBoundTests(unittest.TestCase):
    def test_default_contract_still_allows_one_image(self):
        validate_body(body_with(1), image_types=("jpeg",))
        with self.assertRaisesRegex(ModelError, "only one inline image"):
            validate_body(body_with(2), image_types=("jpeg",))

    def test_raised_bound_is_enforced(self):
        validate_body(body_with(3), image_types=("jpeg",), max_images=3)
        with self.assertRaisesRegex(ModelError, "more inline images than approved"):
            validate_body(body_with(4), image_types=("jpeg",), max_images=3)

    def test_transport_limits_the_image_bound(self):
        for bad in (0, 9, True):
            with self.subTest(max_images=bad):
                with self.assertRaises(ModelError):
                    LiveChatTransport(lambda: "k", max_images=bad)
        transport = LiveChatTransport(lambda: "k", max_images=3, image_types=("jpeg",))
        self.assertEqual(transport._contract["max_images"], 3)


if __name__ == "__main__":
    unittest.main()
