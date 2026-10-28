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
        self.assertTrue(spark["live_eligible"])
        self.assertFalse(spark["has_key"])
        self.assertEqual(spark["credential_source"], "runtime configuration")
        for model in models:
            self.assertEqual(set(model), {
                "id", "name", "model_id", "capabilities", "kind", "base_url", "accepts_images",
                "supports_tools", "pricing", "live_eligible", "has_key", "credential_source",
            })
            self.assertNotIn("secret", json.dumps(model).lower())
            self.assertEqual(model["base_url"], "https://api.meta.ai/v1")
            self.assertEqual(model["credential_source"], "runtime configuration")
        sam = next(m for m in models if m["id"] == "sam-catalog")
        transcribe = next(m for m in models if m["id"] == "transcribe-catalog")
        self.assertEqual((sam["kind"], sam["model_id"]), ("segmentation", "sam-3.1"))
        self.assertEqual((transcribe["kind"], transcribe["model_id"]), ("transcription", "muse-voice-transcribe-1.0"))
        self.assertEqual((spark["kind"], spark["accepts_images"], spark["supports_tools"]), ("chat", True, True))
        self.assertFalse(sam["live_eligible"])
        self.assertFalse(transcribe["live_eligible"])
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
        self.assertFalse(record["live_eligible"])
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


if __name__ == "__main__":
    unittest.main()
