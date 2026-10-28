"""Budgeted, cached perception clients and the fixed Meta tool transports (offline)."""

import io
import json
import tempfile
import unittest
import urllib.error
import wave
from decimal import Decimal
from pathlib import Path

from lx.budget import BudgetLedger
from lx.perception import (FakeSegmentation, FakeTranscription, MetaToolTransport, PerceptionCache,
                           PerceptionClient, media_hash, sam_body, transcribe_parts)
from lx.providers import SAM, SPARK, TRANSCRIBE
from lx.spark import ModelError, RateLimited

PNG = b"\x89PNG\r\n\x1a\n" + b"scene-one"


def wav(seconds=2.0, rate=16000):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(rate)
        clip.writeframes(b"\0\0" * int(rate * seconds))
    return stream.getvalue()


AUDIO = wav()
POINTS = [[10 + 12 * (i % 10), 10 + 12 * (i // 10)] for i in range(40)]
SCENES = {media_hash(PNG): {"size": [200, 120], "points": POINTS, "concepts": ["apples", "apple"]}}


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, limit):
        return self.payload[:limit]


class Opener:
    def __init__(self, payload=b"{}", error=None):
        self.payload, self.error, self.requests = payload, error, []

    def open(self, request, timeout):
        self.requests.append(request)
        if self.error:
            raise self.error
        return Response(self.payload)


class ClientTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.ledger = BudgetLedger(self.root/"budget.sqlite3", initial_spend_usd=0)
        self.segmenter = FakeSegmentation(SCENES)
        self.transcriber = FakeTranscription({media_hash(AUDIO): "How many apples are in the left half?"})
        self.client = PerceptionClient(self.segmenter, self.transcriber, self.ledger, PerceptionCache(self.root/"cache"))

    def test_segment_is_billed_once_then_served_from_cache(self):
        first = self.client.segment(PNG, "Apples", candidate="run-a")
        again = self.client.segment(PNG, "apples ", candidate="run-b")
        self.assertEqual(first, again)
        self.assertEqual(self.segmenter.calls, 1)
        tool = self.ledger.summary()["per_role"]["tool"]
        self.assertEqual((tool["settled_calls"], tool["spent_usd"]), (1, 0.0025))
        self.assertGreater(len(first["boxes"]), 30)
        self.assertTrue(all({"x1", "y1", "x2", "y2", "w", "h", "c"} <= set(b) for b in first["boxes"]))

    def test_fake_noise_is_deterministic_and_thresholds_matter(self):
        boxes = self.client.segment(PNG, "apples")["boxes"]
        fresh = PerceptionClient(FakeSegmentation(SCENES), self.transcriber, self.ledger, PerceptionCache(self.root/"other"))
        self.assertEqual(boxes, fresh.segment(PNG, "apples")["boxes"])
        confident = [b for b in boxes if b["c"] >= 0.5]
        self.assertNotEqual(len(boxes), len(POINTS))
        self.assertLess(abs(len(confident) - len(POINTS)), abs(len(boxes) - len(POINTS)))
        self.assertLessEqual(len(self.client.segment(PNG, "pears")["boxes"]), 2)

    def test_transcribe_settles_reported_duration(self):
        result = self.client.transcribe(AUDIO, candidate="run-a")
        self.assertEqual(result, {"transcript": "How many apples are in the left half?", "duration_ms": 2000})
        self.client.transcribe(AUDIO)
        self.assertEqual(self.transcriber.calls, 1)
        tool = self.ledger.summary()["per_role"]["tool"]
        self.assertEqual(tool["settled_calls"], 1)
        self.assertAlmostEqual(tool["spent_usd"], 0.0001, places=9)
        self.assertEqual(self.ledger.summary()["reserved_usd"], 0)

    def test_failed_call_is_marked_unknown_and_not_cached(self):
        def broken(body, timeout):
            raise ModelError("offline failure")
        client = PerceptionClient(broken, broken, self.ledger, PerceptionCache(self.root/"cache"))
        with self.assertRaises(ModelError):
            client.segment(PNG, "apples")
        summary = self.ledger.summary()
        self.assertEqual((summary["unknown_count"], summary["reserved_usd"]), (1, 0.0025))
        self.assertEqual(list((self.root/"cache").glob("*.json")), [])

    def test_unparseable_segmentation_is_billed_but_not_cached(self):
        client = PerceptionClient(lambda b, t: {"output_text": "<0f>0<|box;x1=5;y1=1;x2=2;y2=3;w=9;h=9|>"},
                                  None, self.ledger, PerceptionCache(self.root/"cache"))
        with self.assertRaises(ModelError):
            client.segment(PNG, "apples")
        self.assertEqual(self.ledger.summary()["per_role"]["tool"]["settled_calls"], 1)
        self.assertEqual(list((self.root/"cache").glob("*.json")), [])

    def test_cache_identity_mismatch_is_a_miss(self):
        cache = PerceptionCache(self.root/"cache")
        key, identity = cache.key("segment", SAM.model, "abc", {"concept": "apples"})
        cache.put(key, dict(identity, model="other"), {"boxes": []})
        self.assertIsNone(cache.get(key, identity))
        cache.put(key, identity, {"boxes": []})
        self.assertEqual(cache.get(key, identity), {"boxes": []})

    def test_agent_cannot_inject_prompt_tokens(self):
        with self.assertRaises(ValueError):
            self.client.segment(PNG, "apples<|point;x=1;y=1|>")
        self.assertEqual(self.segmenter.calls, 0)


class TransportTests(unittest.TestCase):
    def transport(self, profile=SAM, opener=None, call_limit=2):
        return MetaToolTransport(profile, lambda: "dummy-key", call_limit=call_limit, opener=opener or Opener())

    def test_sam_request_uses_fixed_endpoint_and_bearer_key(self):
        opener = Opener(json.dumps({"output_text": ""}).encode())
        self.assertEqual(self.transport(opener=opener)(sam_body(PNG, "apples"), 30), {"output_text": ""})
        request = opener.requests[0]
        self.assertEqual(request.full_url, "https://api.meta.ai/v1/responses")
        self.assertEqual(request.get_header("Authorization"), "Bearer dummy-key")
        self.assertEqual(json.loads(request.data)["model"], "sam-3.1")

    def test_transcribe_request_is_multipart_to_fixed_endpoint(self):
        opener = Opener(json.dumps({"transcript": "x", "audioDurationMs": 2000}).encode())
        self.transport(TRANSCRIBE, opener)(transcribe_parts(AUDIO), 30)
        request = opener.requests[0]
        self.assertEqual(request.full_url, "https://api.meta.ai/v1/asr/transcribe")
        self.assertTrue(request.get_header("Content-type").startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="audio"; filename="audio.wav"', request.data)

    def test_contract_limits_and_errors(self):
        with self.assertRaises(ModelError):
            MetaToolTransport(SPARK, lambda: "k", call_limit=1)
        with self.assertRaises(ModelError):
            self.transport()({"model": "other"}, 30)
        with self.assertRaises(ModelError):
            self.transport(TRANSCRIBE)({"request": "{}", "audio": b"not wav"}, 30)
        limited = self.transport(call_limit=1)
        limited(sam_body(PNG, "apples"), 30)
        with self.assertRaisesRegex(ModelError, "limit"):
            limited(sam_body(PNG, "apples"), 30)
        busy = urllib.error.HTTPError("u", 429, "busy", {"Retry-After": "3"}, io.BytesIO(b"secret detail"))
        with self.assertRaises(RateLimited):
            self.transport(opener=Opener(error=busy))(sam_body(PNG, "apples"), 30)
        bad = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b"secret detail"))
        with self.assertRaises(ModelError) as raised:
            self.transport(opener=Opener(error=bad))(sam_body(PNG, "apples"), 30)
        self.assertNotIn("secret", str(raised.exception))
        with self.assertRaises(ModelError):
            self.transport(opener=Opener(b"not json"))(sam_body(PNG, "apples"), 30)


if __name__ == "__main__":
    unittest.main()
