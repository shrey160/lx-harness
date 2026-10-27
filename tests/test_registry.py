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
                "id", "name", "model_id", "capabilities", "live_eligible", "has_key",
                "credential_source",
            })
            self.assertNotIn("secret", json.dumps(model).lower())
        self.assertFalse(next(m for m in models if m["id"] == "sam-catalog")["live_eligible"])
        self.assertFalse(next(m for m in models if m["id"] == "transcribe-catalog")["live_eligible"])
        loader.assert_not_called()

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
        with self.assertRaisesRegex(RegistryError, "only for the Spark"):
            self.registry.credential(record["id"])

    def test_key_rotation_removes_old_secret_and_delete_removes_rotated_secret(self):
        first = "dummy-first-secret"
        second = "dummy-second-secret"
        record = self.registry.create("Rotating model", "test/rotating", "extract", first)
        original_path = next(self.secrets_dir.iterdir())
        updated = self.registry.update(record["id"], name="Rotated name", api_key=second)
        self.assertEqual(updated["name"], "Rotated name")
        with self.assertRaisesRegex(RegistryError, "only for the Spark"):
            self.registry.credential(record["id"])
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

    def test_non_spark_catalog_credential_does_not_load_runtime_key(self):
        with patch("lx.live._runtime_key") as loader:
            with self.assertRaisesRegex(RegistryError, "only for the Spark"):
                self.registry.credential("sam-catalog")
        loader.assert_not_called()

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
