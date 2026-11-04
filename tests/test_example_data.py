"""The shipped example copies and packs verified inputs without local LX state."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile


EXAMPLE = Path(__file__).resolve().parents[1] / "examples/inventory-cash-three"
spec = importlib.util.spec_from_file_location("example_prepare", EXAMPLE / "prepare.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class ExampleDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_fresh_copy_is_self_contained_and_does_not_overwrite(self):
        destination = self.root / "task with spaces"
        summary = prepare.export(EXAMPLE, destination)
        self.assertEqual(summary["photos"], 8)
        self.assertEqual(summary["audio_clips"], 3)
        self.assertEqual(summary["provider_calls"], 0)
        self.assertEqual(prepare.verify(destination)[0], prepare.verify(EXAMPLE)[0])
        self.assertTrue((destination / "AGENT.md").is_file())
        self.assertTrue((destination / "LICENSE.txt").is_file())
        self.assertFalse((destination / ".lx").exists())
        self.assertFalse((destination / "eval/judges.json").exists())
        with self.assertRaisesRegex(ValueError, "already exists"):
            prepare.export(EXAMPLE, destination)

    def test_zip_excludes_unlisted_files_and_retains_verifiable_bundle(self):
        source = self.root / "copy"
        prepare.export(EXAMPLE, source)
        (source / "unlisted.txt").write_text("not part of dataset", encoding="utf-8")
        (source / ".lx").mkdir()
        (source / ".lx/runtime.txt").write_text("local state", encoding="utf-8")
        archive_path = self.root / "example.zip"
        prepare.export(source, archive_path, zipped=True)
        with zipfile.ZipFile(archive_path) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), set(prepare.verify(source)[1]))
            self.assertNotIn("unlisted.txt", archive.namelist())
            archive.extractall(self.root / "extracted")
        self.assertEqual(prepare.verify(self.root / "extracted")[0], prepare.verify(EXAMPLE)[0])
        with self.assertRaisesRegex(ValueError, "already exists"):
            prepare.export(source, archive_path, zipped=True)

    def test_changed_input_is_refused_before_export(self):
        source = self.root / "copy"
        prepare.export(EXAMPLE, source)
        (source / "samples/sample-03/input.json").write_text('{}', encoding="utf-8")
        destination = self.root / "bad.zip"
        with self.assertRaisesRegex(ValueError, "integrity failed"):
            prepare.export(source, destination, zipped=True)
        self.assertFalse(destination.exists())

    def test_manifest_cannot_escape_or_read_hidden_paths(self):
        source = self.root / "copy"
        prepare.export(EXAMPLE, source)
        manifest_path = source / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for name in ("../outside.json", "/outside.json", "C:/outside.json", "reviewer\\outside.json", ".hidden.json"):
            with self.subTest(name=name):
                changed = {**manifest, "files": {name: {"bytes": 0, "sha256": ""}}}
                manifest_path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Unsafe manifest path"):
                    prepare.payload_files(source)

    def test_copy_and_zip_cannot_be_created_inside_source(self):
        for zipped in (False, True):
            with self.subTest(zipped=zipped), self.assertRaisesRegex(ValueError, "outside this example"):
                prepare.export(EXAMPLE, EXAMPLE / "nested-output", zipped=zipped)


if __name__ == "__main__":
    unittest.main()
