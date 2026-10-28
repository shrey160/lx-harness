import unittest
from decimal import Decimal

from lx.providers import SAM, SPARK, TRANSCRIBE, ProviderProfile, chat_endpoint, validate_endpoint

LOCAL = "http://127.0.0.1:8888/v1/chat/completions"


def profile(**changes):
    fields = dict(kind="chat", endpoint=LOCAL, model="gemma-4-E4B-it-GGUF", token_param="max_tokens")
    fields.update(changes)
    return ProviderProfile(**fields)


class EndpointTests(unittest.TestCase):
    def test_accepts_meta_and_private_network_endpoints(self):
        for url in ("https://api.meta.ai/v1", "http://127.0.0.1:8888/v1", "http://localhost:8888/v1",
                    "http://192.168.1.20:8888/v1", "http://172.28.0.1:8888/v1"):
            with self.subTest(url=url):
                self.assertEqual(validate_endpoint(url), url)

    def test_rejects_plain_http_public_host_and_other_schemes(self):
        for url in ("http://example.com/v1", "ftp://127.0.0.1/v1"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    validate_endpoint(url)

    def test_rejects_credentials_query_and_fragment(self):
        for url in ("https://user:pw@api.meta.ai/v1", "https://api.meta.ai/v1?key=x", "https://api.meta.ai/v1#frag"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    validate_endpoint(url)

    def test_rejects_malformed_missing_and_oversized(self):
        for value in ("not a url", "", None, "https://api.meta.ai/" + "a" * 580):
            with self.subTest(value=repr(value)[:40]):
                with self.assertRaises(ValueError):
                    validate_endpoint(value)

    def test_chat_endpoint_handles_trailing_slash(self):
        self.assertEqual(chat_endpoint("http://127.0.0.1:8888/v1/"), "http://127.0.0.1:8888/v1/chat/completions")


class BuiltinProfileTests(unittest.TestCase):
    def test_spark_profile(self):
        self.assertEqual(SPARK.model, "muse-spark-1.3-contributor")
        self.assertEqual(SPARK.endpoint, "https://api.meta.ai/v1/chat/completions")
        self.assertEqual(SPARK.reasoning_effort, "low")
        self.assertEqual(SPARK.token_param, "max_completion_tokens")

    def test_sam_profile(self):
        self.assertEqual(SAM.model, "sam-3.1")
        self.assertEqual(SAM.kind, "segmentation")
        self.assertTrue(SAM.endpoint.endswith("/v1/responses"))
        self.assertEqual(SAM.image_price, Decimal("0.0025"))

    def test_transcribe_profile(self):
        self.assertEqual(TRANSCRIBE.model, "muse-voice-transcribe-1.0")
        self.assertEqual(TRANSCRIBE.kind, "transcription")
        self.assertTrue(TRANSCRIBE.endpoint.endswith("/v1/asr/transcribe"))
        self.assertEqual(TRANSCRIBE.audio_hour_price, Decimal("0.18"))


class ConstructionTests(unittest.TestCase):
    def test_rejects_unknown_kind_or_token_param(self):
        with self.assertRaises(ValueError):
            profile(kind="video")
        with self.assertRaises(ValueError):
            profile(token_param="max_new_tokens")

    def test_rejects_empty_model(self):
        with self.assertRaises(ValueError):
            profile(model="")

    def test_rejects_negative_price(self):
        with self.assertRaises(ValueError):
            profile(input_price=Decimal("-1"))

    def test_rejects_float_price(self):
        with self.assertRaises(ValueError):
            profile(input_price=0.5)

    def test_rejects_zero_context_limit(self):
        with self.assertRaises(ValueError):
            profile(context_limit=0)

    def test_local_profile_defaults_to_zero_cost_text_only(self):
        p = profile()
        for price in (p.input_price, p.cached_price, p.output_price, p.image_price, p.audio_hour_price):
            self.assertEqual(price, Decimal(0))
        self.assertFalse(p.accepts_images)
        self.assertFalse(p.supports_tools)


if __name__ == "__main__":
    unittest.main()
