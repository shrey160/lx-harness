"""Adding a provider without typing its details (D-020): what its model list gives, and when it gives nothing."""
import io
import json
import tempfile
import unittest
import urllib.error
from decimal import Decimal
from pathlib import Path

from lx.engine import Engine
from lx.model_list import MAX_REPLY_BYTES, RESERVED_CONTEXT, parse_models, read_models
from lx.registry import Registry, RegistryError
from lx.spark import ModelError, usage_of
from test_tasks import MemoryKeychain

# Shapes as read from the public lists on 31 Oct 2026.
CONTRIBUTOR = {"id": "meta/muse-spark-1.3-contributor", "name": "Meta: Muse Spark 1.3 Contributor", "context_length": 1048576,
               "pricing": {"prompt": "0.0000001", "completion": "0.0000002", "web_search": "0.0025", "input_cache_read": "0.000000002"},
               "supported_parameters": ["max_tokens", "tools"],
               "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}}
NAME_ONLY = {"id": "meta/muse-spark-1.3-contributor", "object": "model", "owned_by": "meta"}


class Opener:
    def __init__(self, body=None, error=None):
        self.body, self.error, self.requests = body, error, []

    def open(self, request, timeout):
        self.requests.append(request)
        if self.error:
            raise self.error
        return io.BytesIO(self.body)


def listed(*rows):
    return json.dumps({"data": list(rows)}).encode()


class Reading(unittest.TestCase):
    def test_a_full_entry_gives_everything_the_form_used_to_ask(self):
        model = parse_models({"data": [CONTRIBUTOR]})[0]
        self.assertEqual(model["pricing"], {"input_per_million": "0.1", "output_per_million": "0.2", "cached_per_million": "0.002"})
        self.assertEqual((model["accepts_images"], model["supports_tools"]), (True, True))
        self.assertEqual(model["name"], "Meta: Muse Spark 1.3 Contributor")

    def test_a_name_only_entry_leaves_the_rest_unknown(self):
        model = parse_models({"data": [NAME_ONLY]})[0]
        self.assertEqual(model["name"], "meta/muse-spark-1.3-contributor")
        self.assertIsNone(model["pricing"])
        self.assertIsNone(model["accepts_images"])
        self.assertIsNone(model["supports_tools"])
        self.assertIsNone(model["context_limit"])

    def test_the_reserved_context_stays_inside_a_small_budget(self):
        dear = {**CONTRIBUTOR, "pricing": {"prompt": "0.00000125", "completion": "0.00000425"}}
        model = parse_models({"data": [dear]})[0]
        self.assertEqual(model["context_limit"], RESERVED_CONTEXT)
        self.assertLess(model["context_limit"]*Decimal(model["pricing"]["input_per_million"])/1_000_000, 1)

    def test_a_varying_price_counts_as_unknown(self):
        row = {**CONTRIBUTOR, "pricing": {"prompt": "-1", "completion": "-1"}}
        self.assertIsNone(parse_models({"data": [row]})[0]["pricing"])

    def test_a_model_that_cannot_chat_is_left_out(self):
        row = {**CONTRIBUTOR, "architecture": {"output_modalities": ["image"]}}
        self.assertEqual(parse_models({"data": [row]}), [])

    def test_bad_rows_are_dropped_and_a_bad_list_is_refused(self):
        self.assertEqual(parse_models({"data": [{"name": "no id"}, 7, {"id": ""}]}), [])
        with self.assertRaisesRegex(ModelError, "expected form"):
            parse_models(["x"])

    def test_no_key_is_sent_unless_given_and_then_only_to_that_url(self):
        opener = Opener(listed(NAME_ONLY))
        read_models("https://integrate.api.nvidia.com/v1/", opener=opener)
        self.assertEqual(opener.requests[0].full_url, "https://integrate.api.nvidia.com/v1/models")
        self.assertNotIn("Authorization", opener.requests[0].headers)
        read_models("https://example.com/v1", "dummy-key", opener=opener)
        self.assertEqual(opener.requests[1].get_header("Authorization"), "Bearer dummy-key")

    def test_a_refusal_says_to_add_a_key_and_never_shows_the_reply(self):
        error = urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(b"secret detail"))
        with self.assertRaises(ModelError) as raised:
            read_models("https://api.meta.ai/v1", opener=Opener(error=error))
        self.assertIn("Add your API key", str(raised.exception))
        self.assertNotIn("secret", str(raised.exception))

    def test_an_address_is_checked_first(self):
        for bad in ("http://example.com/v1", "ftp://x/v1", "https://user:pw@example.com/v1"):
            with self.subTest(bad), self.assertRaises(ValueError):
                read_models(bad, opener=Opener(listed()))

    def test_a_huge_or_broken_reply_is_refused(self):
        with self.assertRaisesRegex(ModelError, "too large"):
            read_models("https://example.com/v1", opener=Opener(b"x"*(MAX_REPLY_BYTES+1)))
        with self.assertRaisesRegex(ModelError, "expected form"):
            read_models("https://example.com/v1", opener=Opener(b"not json"))


class Saved(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.registry = Registry(root/"a.sqlite3", root/"secrets", MemoryKeychain())

    def add(self, pricing):
        return self.registry.create("Listed", "meta/x", ["General"], base_url="https://openrouter.ai/api/v1", pricing=pricing)

    def test_the_cached_price_is_billed_so_the_overcount_goes(self):
        entry = self.add({"input_per_million": "0.1", "output_per_million": "0.2", "cached_per_million": "0.002"})
        self.assertTrue(entry["priced"])
        raw = {"usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0, "prompt_tokens_details": {"cached_tokens": 1_000_000}}}
        self.assertEqual(usage_of(raw, self.registry.profile(entry["id"]))["cost_usd"], Decimal("0.002"))

    def test_without_one_cached_tokens_stay_at_the_input_price(self):
        entry = self.add({"input_per_million": "0.1", "output_per_million": "0.2"})
        raw = {"usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0, "prompt_tokens_details": {"cached_tokens": 1_000_000}}}
        self.assertEqual(usage_of(raw, self.registry.profile(entry["id"]))["cost_usd"], Decimal("0.1"))

    def test_no_price_is_a_valid_entry_that_says_so(self):
        self.assertFalse(self.add(None)["priced"])

    def test_a_stray_price_field_is_refused(self):
        with self.assertRaises(RegistryError):
            self.add({"input_per_million": 1, "output_per_million": 1, "web_search": 1})


class Engine_(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Engine(Path(self.temp.name)/"state", keychain=MemoryKeychain())

    def test_a_scratch_app_makes_no_real_request(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.app.provider_models("https://openrouter.ai/api/v1")

    def test_a_failure_reaches_the_form_as_a_plain_message(self):
        def refuse(base, key):
            raise ModelError("Could not reach that provider")
        self.app._model_list = refuse
        with self.assertRaisesRegex(ValueError, "Could not reach"):
            self.app.provider_models("https://x.example/v1")

    def test_the_list_comes_back_for_the_form(self):
        self.app._model_list = lambda base, key: [{"id": base, "key": key}]
        self.assertEqual(self.app.provider_models("https://x.example/v1", "")["models"], [{"id": "https://x.example/v1", "key": None}])

    def test_a_model_already_on_the_provider_lends_its_key_to_the_lookup(self):
        url = "https://x.example/v1"
        first = self.app.registry.create("First", "x/first", ["General"], "dummy-lent-key", base_url=url)
        self.app._model_list = lambda base, key: [{"id": base, "key": key}]
        self.assertEqual(self.app.provider_models(url, None, first["id"])["models"], [{"id": url, "key": "dummy-lent-key"}])
        with self.assertRaisesRegex(Exception, "no stored key"):
            self.app.provider_models("https://y.example/v1", None, first["id"])

if __name__ == "__main__":
    unittest.main()
