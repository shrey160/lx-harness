"""Credential-safe model registry tests using dummy keys only."""

import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch

from lx.registry import Registry, RegistryError
from lx.spark import MODEL


class MemoryKeychain:
    """Never the real Credential Manager in tests."""
    def __init__(self, usable=True):
        self.values, self.usable = {}, usable
    def available(self): return self.usable
    def set(self, name, value): self.values[name] = value
    def get(self, name): return self.values.get(name)
    def delete(self, name): self.values.pop(name, None)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(dir=None if os.name == "posix" else Path(__file__).resolve().parents[1])
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.db_path = root / "registry.sqlite3"
        self.secrets_dir = root / "private-secrets"
        self.registry = Registry(self.db_path, self.secrets_dir)

    def test_seed_catalog_and_safe_public_records(self):
        with patch("lx.live._runtime_key") as loader:
            models = self.registry.list_models()
        self.assertEqual({m["id"] for m in models}, {
            "spark-contributor", "sam-catalog", "transcribe-catalog"
        })
        spark = next(m for m in models if m["id"] == "spark-contributor")
        self.assertEqual(spark["model_id"], MODEL)
        self.assertEqual(spark["name"], "Muse Spark 1.3 Contributor")
        self.assertTrue(spark["role_ready"])
        self.assertFalse(spark["has_key"])
        self.assertEqual(spark["credential_source"], "runtime configuration")
        for model in models:
            self.assertEqual(set(model), {
                "id", "name", "model_id", "capabilities", "kind", "base_url", "accepts_images",
                "supports_tools", "pricing", "context_limit", "requests_per_minute", "limits", "enabled", "role_ready", "priced", "has_key", "credential_source",
            })
            self.assertNotIn("secret", json.dumps(model).lower())
            self.assertEqual(model["base_url"], "https://api.meta.ai/v1")
            self.assertEqual(model["credential_source"], "runtime configuration")
        sam = next(m for m in models if m["id"] == "sam-catalog")
        transcribe = next(m for m in models if m["id"] == "transcribe-catalog")
        self.assertEqual((sam["kind"], sam["model_id"]), ("segmentation", "sam-3.1"))
        self.assertEqual((transcribe["kind"], transcribe["model_id"]), ("transcription", "muse-voice-transcribe-1.0"))
        self.assertEqual((spark["kind"], spark["accepts_images"], spark["supports_tools"]), ("chat", True, True))
        self.assertFalse(sam["role_ready"])
        self.assertFalse(transcribe["role_ready"])
        loader.assert_not_called()

    def test_existing_database_is_migrated_to_provider_fields(self):
        root = Path(self.tempdir.name)
        old = root / "old.sqlite3"
        db = sqlite3.connect(old)
        db.executescript(
            "CREATE TABLE models (id TEXT PRIMARY KEY, name TEXT NOT NULL, model_id TEXT NOT NULL,"
            " capabilities TEXT NOT NULL, builtin INTEGER NOT NULL DEFAULT 0, secret_ref TEXT);"
            "INSERT INTO models VALUES ('sam-catalog','SAM','meta/sam','[\"old\"]',1,NULL);"
            "INSERT INTO models VALUES ('custom','Custom','test/m','[\"x\"]',0,NULL);")
        db.commit()
        db.close()
        models = {m["id"]: m for m in Registry(old, root / "old-secrets").list_models()}
        self.assertEqual(models["sam-catalog"]["model_id"], "sam-3.1")
        self.assertEqual(models["custom"]["kind"], "chat")
        self.assertIsNone(models["custom"]["base_url"])
        self.assertEqual(models["custom"]["pricing"], {})

    def test_local_provider_is_an_ordinary_entry_with_a_profile(self):
        record = self.registry.create("Gemma local", "gemma-4-E4B-it-GGUF", ["Small local chat model"],
                                      base_url="http://127.0.0.1:8888/v1/", accepts_images=True,
                                      supports_tools=True)
        self.assertEqual(record["base_url"], "http://127.0.0.1:8888/v1")
        self.assertFalse(record["has_key"])
        self.assertIsNone(record["credential_source"])
        profile = self.registry.profile(record["id"])
        self.assertEqual(profile.endpoint, "http://127.0.0.1:8888/v1/chat/completions")
        self.assertEqual((profile.model, profile.token_param, profile.reasoning_effort),
                         ("gemma-4-E4B-it-GGUF", "max_tokens", None))
        self.assertEqual(profile.input_price + profile.output_price, 0)
        with self.assertRaisesRegex(RegistryError, "no API key"):
            self.registry.credential(record["id"])
        priced = self.registry.update(record["id"], pricing={"input_per_million": "0.5", "output_per_million": 2})
        self.assertEqual(priced["pricing"], {"input_per_million": "0.5", "output_per_million": "2"})
        self.assertEqual(self.registry.profile(record["id"]).output_price * 1_000_000, 2)

    def test_provider_fields_are_validated(self):
        for kwargs in ({"base_url": "http://example.com/v1"}, {"base_url": "https://x.test/v1?key=1"},
                       {"kind": "segmentation"}, {"pricing": {"input_per_million": -1}},
                       {"pricing": {"per_image": "1"}}, {"supports_tools": "yes"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(RegistryError):
                self.registry.create("Bad", "test/bad", ["x"], **kwargs)
        no_url = self.registry.create("No URL", "test/no-url", ["x"])
        with self.assertRaisesRegex(RegistryError, "no endpoint"):
            self.registry.profile(no_url["id"])
        with self.assertRaisesRegex(RegistryError, "immutable"):
            self.registry.update("spark-contributor", base_url="http://127.0.0.1:1/v1")

    def test_builtin_profiles_are_the_fixed_meta_services(self):
        from lx.providers import SAM, SPARK, TRANSCRIBE
        self.assertIs(self.registry.profile("spark-contributor"), SPARK)
        self.assertIs(self.registry.profile("sam-catalog"), SAM)
        self.assertIs(self.registry.profile("transcribe-catalog"), TRANSCRIBE)

    def test_custom_key_is_separate_private_and_never_returned(self):
        dummy = "synthetic-dummy-key-123"
        record = self.registry.create("Local test model", "test/model-v1", ["summarize"], dummy)
        self.assertTrue(record["has_key"])
        self.assertFalse(record["role_ready"])  # no endpoint yet
        self.assertNotIn(dummy, repr(record))
        db = sqlite3.connect(self.db_path)
        try:
            data = repr(db.execute("SELECT * FROM models WHERE id=?", (record["id"],)).fetchone())
        finally:
            db.close()
        self.assertNotIn(dummy, data)
        secret_files = list(self.secrets_dir.iterdir())
        self.assertEqual(len(secret_files), 1)
        self.assertNotEqual(secret_files[0].name, dummy)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(secret_files[0].stat().st_mode), 0o600)
        # Runtime code may load a registered key; it is never part of the public record.
        self.assertEqual(self.registry.credential(record["id"]), dummy)

    def test_key_rotation_removes_old_secret_and_delete_removes_rotated_secret(self):
        first = "dummy-first-secret"
        second = "dummy-second-secret"
        record = self.registry.create("Rotating model", "test/rotating", "extract", first)
        original_path = next(self.secrets_dir.iterdir())
        updated = self.registry.update(record["id"], name="Rotated name", api_key=second)
        self.assertEqual(updated["name"], "Rotated name")
        self.assertEqual(self.registry.credential(record["id"]), second)
        self.assertFalse(original_path.exists())
        self.assertEqual(len(list(self.secrets_dir.iterdir())), 1)
        current_path = next(self.secrets_dir.iterdir())
        self.registry.delete(record["id"])
        self.assertFalse(current_path.exists())
        with self.assertRaisesRegex(RegistryError, "unknown model"):
            self.registry.credential(record["id"])

    def test_spark_credential_is_loaded_only_on_runtime_credential_call(self):
        with patch("lx.live._runtime_key", return_value="dummy-runtime-key") as loader:
            models = self.registry.list_models()
            loader.assert_not_called()
            key = self.registry.credential("spark-contributor")
        self.assertEqual(key, "dummy-runtime-key")
        loader.assert_called_once_with()

    def test_registered_spark_key_precedes_runtime_loader_and_metadata_can_change(self):
        record = self.registry.update(
            "spark-contributor",
            name="Spark renamed locally",
            capabilities=["reason", "plan"],
            api_key="dummy-registered-spark-key",
        )
        self.assertTrue(record["has_key"])
        self.assertEqual(record["credential_source"], "registered")
        self.assertEqual(record["model_id"], MODEL)
        with patch("lx.live._runtime_key") as loader:
            self.assertEqual(
                self.registry.credential("spark-contributor"), "dummy-registered-spark-key"
            )
        loader.assert_not_called()

    def test_meta_tools_share_the_runtime_credential_only_on_request(self):
        with patch("lx.live._runtime_key", return_value="dummy-runtime-key") as loader:
            self.registry.list_models()
            loader.assert_not_called()
            self.assertEqual(self.registry.credential("sam-catalog"), "dummy-runtime-key")
            self.assertEqual(self.registry.credential("transcribe-catalog"), "dummy-runtime-key")
        self.assertEqual(loader.call_count, 2)

    def test_a_key_on_the_spark_entry_serves_sam_and_transcribe(self):
        registry = self.keyed(MemoryKeychain())
        registry.update("spark-contributor", api_key="dummy-ui-key")
        with patch("lx.live._runtime_key") as loader:
            self.assertEqual(registry.credential("sam-catalog"), "dummy-ui-key")
            self.assertEqual(registry.credential("transcribe-catalog"), "dummy-ui-key")
        loader.assert_not_called()

    def test_the_meta_key_source_names_where_it_is_and_never_the_key(self):
        registry = self.keyed(MemoryKeychain())
        with patch("lx.live._runtime_key", side_effect=ValueError("none")):
            self.assertIsNone(registry.meta_key_source())
        with patch("lx.live._runtime_key", return_value="dummy-runtime-key"):
            self.assertEqual(registry.meta_key_source(), "runtime configuration")
        registry.update("spark-contributor", api_key="dummy-ui-key")
        with patch("lx.live._runtime_key") as loader:
            self.assertEqual(registry.meta_key_source(), "registered")
        loader.assert_not_called()

    def test_a_key_on_another_entry_is_not_the_meta_key(self):
        registry = self.keyed(MemoryKeychain())
        registry.create("Hosted", "test/model", ["General"], "dummy-other")
        with patch("lx.live._runtime_key", side_effect=ValueError("none")):
            self.assertIsNone(registry.meta_key_source())
            with self.assertRaises(RegistryError):
                registry.credential("sam-catalog")

    def test_non_spark_catalog_is_immutable_and_spark_model_id_cannot_be_created(self):
        with self.assertRaisesRegex(RegistryError, "immutable"):
            self.registry.delete("sam-catalog")
        with self.assertRaisesRegex(RegistryError, "runtime credential"):
            self.registry.create("Duplicate Spark", MODEL, ["reason"], "dummy-key")

    def test_fields_and_keys_are_validated_without_echoing_key(self):
        secret = "do-not-echo-this-dummy"
        invalid_values = (
            ("", "test/model", ["ok"], None),
            ("x" * 121, "test/model", ["ok"], None),
            ("Name", "", ["ok"], None),
            ("Name", "test/model", [], None),
            ("Name", "test/model", [" "], None),
            ("Name", "test/model", ["ok"], ""),
            ("Name", "test/model", ["ok"], secret * 500),
        )
        for name, model_id, caps, key in invalid_values:
            with self.subTest(name=name[:8], model=model_id[:8]), self.assertRaises(RegistryError) as raised:
                self.registry.create(name, model_id, caps, key)
            self.assertNotIn(secret, str(raised.exception))

    def test_update_cannot_change_model_id_and_unknown_ids_fail(self):
        record = self.registry.create("Custom", "test/immutable-id", ["classify"])
        updated = self.registry.update(record["id"], capabilities=["classify", "rank"])
        self.assertEqual(updated["model_id"], "test/immutable-id")
        with self.assertRaises(TypeError):
            self.registry.update(record["id"], model_id="other")
        with self.assertRaisesRegex(RegistryError, "unknown model"):
            self.registry.update("missing", name="Nope")

    def test_any_chat_entry_with_an_endpoint_is_ready_and_prices_are_tracked(self):
        free = self.registry.create("Glimmer", "muse/glimmer", ["General"], base_url="https://openrouter.ai/api/v1")
        self.assertTrue(free["role_ready"])
        self.assertFalse(free["priced"])
        priced = self.registry.update(free["id"], pricing={"input_per_million": 0.0375, "output_per_million": 0},
                                      context_limit=32768)
        self.assertTrue(priced["priced"])
        profile = self.registry.profile(free["id"])
        self.assertEqual(profile.context_limit, 32768)
        self.assertEqual(profile.endpoint, "https://openrouter.ai/api/v1/chat/completions")
        with self.assertRaises(RegistryError):
            self.registry.update(free["id"], context_limit=10)

    def test_an_entry_holds_a_rate_and_its_own_limits(self):
        entry = self.registry.create("Paced", "muse/paced", ["General"], base_url="https://openrouter.ai/api/v1",
                                     requests_per_minute=40, limits={"call_seconds": 120})
        self.assertEqual((entry["requests_per_minute"], entry["limits"]), (40, {"call_seconds": 120}))
        kept = self.registry.update(entry["id"], name="Renamed")
        self.assertEqual((kept["requests_per_minute"], kept["limits"]), (40, {"call_seconds": 120}))
        cleared = self.registry.update(entry["id"], requests_per_minute=0, limits={})
        self.assertEqual((cleared["requests_per_minute"], cleared["limits"]), (None, {}))
        for bad in ({"requests_per_minute": -1}, {"requests_per_minute": 1.5}, {"limits": {"call_seconds": 1}}, {"limits": {"nope": 5}}):
            with self.subTest(bad), self.assertRaises(RegistryError):
                self.registry.update(entry["id"], **bad)

    def test_a_catalog_entry_takes_no_rate(self):
        with self.assertRaises(RegistryError):
            self.registry.update("sam-catalog", requests_per_minute=10)

    def keyed(self, keychain):
        return Registry(self.db_path, self.secrets_dir, keychain)

    def test_keychain_holds_the_key_and_no_file_is_written(self):
        keychain = MemoryKeychain()
        registry = self.keyed(keychain)
        record = registry.create("Hosted", "test/model", ["General"], "dummy-one")
        self.assertEqual(list(keychain.values.values()), ["dummy-one"])
        self.assertEqual(list(self.secrets_dir.iterdir()), [])
        self.assertEqual(registry.credential(record["id"]), "dummy-one")
        self.assertNotIn(b"dummy-one", self.db_path.read_bytes())
        registry.update(record["id"], api_key="dummy-two")
        self.assertEqual(list(keychain.values.values()), ["dummy-two"])
        registry.delete(record["id"])
        self.assertEqual(keychain.values, {})

    def test_existing_key_files_move_to_the_keychain(self):
        record = self.registry.create("Old", "test/old", ["General"], "dummy-old")
        self.assertEqual(len(list(self.secrets_dir.iterdir())), 1)
        keychain = MemoryKeychain()
        registry = self.keyed(keychain)
        self.assertEqual(list(self.secrets_dir.iterdir()), [])
        self.assertEqual(registry.credential(record["id"]), "dummy-old")

    def test_no_keychain_uses_private_files_on_posix_and_refuses_on_windows(self):
        registry = self.keyed(MemoryKeychain(usable=False))
        if os.name == "nt":
            with self.assertRaisesRegex(RegistryError, "Credential Manager"):
                registry.create("Hosted", "test/model", ["General"], "dummy-one")
        else:
            record = registry.create("Hosted", "test/model", ["General"], "dummy-one")
            self.assertEqual(registry.credential(record["id"]), "dummy-one")

    def test_a_missing_keychain_entry_reports_without_detail(self):
        keychain = MemoryKeychain()
        registry = self.keyed(keychain)
        record = registry.create("Hosted", "test/model", ["General"], "dummy-one")
        keychain.values.clear()
        with self.assertRaisesRegex(RegistryError, "credential unavailable"):
            registry.credential(record["id"])

    def test_a_second_model_on_a_provider_takes_the_first_ones_key(self):
        root = Path(self.tempdir.name)/"shared"
        keychain = MemoryKeychain()
        registry = Registry(root/"a.sqlite3", root/"secrets", keychain)
        url = "https://openrouter.ai/api/v1"
        first = registry.create("First", "muse/first", ["General"], "dummy-shared-key", base_url=url)
        second = registry.create("Second", "muse/second", ["General"], base_url=url, key_from=first["id"])
        self.assertTrue(second["has_key"])
        self.assertEqual(registry.credential(second["id"]), "dummy-shared-key")
        self.assertNotEqual(first["id"], second["id"])
        registry.delete(first["id"])  # each model owns its copy, so removing one leaves the other working
        self.assertEqual(registry.credential(second["id"]), "dummy-shared-key")

    def test_a_key_is_lent_only_to_the_same_endpoint(self):
        first = self.registry.create("First", "muse/first", ["General"], "dummy-shared-key", base_url="https://openrouter.ai/api/v1")
        with self.assertRaisesRegex(RegistryError, "no stored key"):
            self.registry.create("Other", "x/y", ["General"], base_url="https://integrate.api.nvidia.com/v1", key_from=first["id"])
        with self.assertRaisesRegex(RegistryError, "not both"):
            self.registry.create("Both", "x/y", ["General"], "dummy-other", base_url="https://openrouter.ai/api/v1", key_from=first["id"])
        with self.assertRaisesRegex(RegistryError, "no stored key"):
            self.registry.create("Spark", "x/y", ["General"], base_url="https://openrouter.ai/api/v1", key_from="spark-contributor")

    def test_a_chat_model_can_be_switched_off_and_is_then_not_ready(self):
        entry = self.registry.create("Glimmer", "muse/glimmer", ["General"], base_url="https://openrouter.ai/api/v1")
        self.assertTrue(entry["enabled"] and entry["role_ready"])
        off = self.registry.set_enabled(entry["id"], False)
        self.assertFalse(off["enabled"])
        self.assertFalse(off["role_ready"])
        self.assertTrue(self.registry.set_enabled(entry["id"], True)["role_ready"])
        with self.assertRaisesRegex(RegistryError, "stays on"):
            self.registry.set_enabled("spark-contributor", False)

    def test_a_model_on_meta_shares_the_meta_key_without_copying_it(self):
        from lx.providers import META_API
        with patch("lx.live._runtime_key", return_value="dummy-meta-key"):
            entry = self.registry.create("Meta other", "meta/other", ["General"], base_url=META_API, key_from="spark-contributor")
            self.assertEqual((entry["has_key"], entry["credential_source"]), (False, "shared Meta key"))
            self.assertEqual(self.registry.credential(entry["id"]), "dummy-meta-key")
            self.assertEqual(self.registry.sibling_key("spark-contributor", META_API), "dummy-meta-key")
        profile = self.registry.profile(entry["id"])
        self.assertEqual((profile.endpoint, profile.token_param), (META_API + "/chat/completions", "max_completion_tokens"))
        self.assertEqual(self.registry.profile(self.registry.create("Else", "x/y", ["General"], base_url="https://openrouter.ai/api/v1")["id"]).token_param, "max_tokens")


if __name__ == "__main__":
    unittest.main()
