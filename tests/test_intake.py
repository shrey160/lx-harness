import hashlib
import io
import os
import stat
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from lx.intake import (
    IntakeError,
    snapshot_folder,
    snapshot_zip,
    windows_to_wsl,
)


def make_zip(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, contents in entries:
            if isinstance(name, zipfile.ZipInfo):
                archive.writestr(name, contents)
            else:
                archive.writestr(name, contents)
    return stream.getvalue()


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_folder_snapshots_supported_files_and_hashes_copy(self):
        source = self.root / "样本 data"
        source.mkdir()
        nested = source / "画像 files"
        nested.mkdir()
        (nested / "猫.png").write_bytes(b"known png bytes")
        (source / "notes 1.txt").write_bytes("hello \u03bb\n".encode("utf-8"))
        (source / "ignored.bin").write_bytes(b"ignored")
        destination = self.root / "snapshot"

        result = snapshot_folder(source, destination)

        self.assertEqual(source.name, result["source_name"])
        self.assertEqual(2, len(result["inventory"]))
        self.assertEqual(sum(item["size"] for item in result["inventory"]), result["total_bytes"])
        by_path = {item["path"]: item for item in result["inventory"]}
        for relative, content in (
            ("画像 files/猫.png", b"known png bytes"),
            ("notes 1.txt", "hello \u03bb\n".encode("utf-8")),
        ):
            copied = (destination / Path(*relative.split("/"))).read_bytes()
            self.assertEqual(content, copied)
            self.assertEqual(hashlib.sha256(content).hexdigest(), by_path[relative]["sha256"])
        self.assertEqual("image", by_path["画像 files/猫.png"]["kind"])
        self.assertEqual("text", by_path["notes 1.txt"]["kind"])
        self.assertFalse((destination / "ignored.bin").exists())

    def test_protected_names_rejected_before_any_file_open(self):
        source = self.root / "samples"
        source.mkdir()
        for protected_name in (
            ".env", ".env.local", "backup.env", "prod.env.backup",
            "credentials.json", "service-private-key.txt", "id_rsa",
        ):
            with self.subTest(name=protected_name):
                fake_entry = source / protected_name
                with patch("lx.intake.Path.iterdir", return_value=iter([fake_entry])), patch(
                    "lx.intake.os.open", side_effect=AssertionError("protected file opened")
                ) as open_mock:
                    with self.assertRaises(IntakeError):
                        snapshot_folder(source, self.root / ("out-" + str(len(protected_name))))
                open_mock.assert_not_called()

    def test_rejects_links_and_source_destination_overlap(self):
        source = self.root / "source"
        source.mkdir()
        link = source / "link.txt"
        real_lstat = Path.lstat
        for mode, destination in (
            (stat.S_IFLNK | 0o777, "snapshot-link"),
            (stat.S_IFIFO | 0o600, "snapshot-fifo"),
        ):
            fake_stat = os.stat_result((mode, 0, 0, 1, 0, 0, 0, 0, 0, 0))

            def fake_lstat(path, expected_mode=fake_stat):
                return expected_mode if path == link else real_lstat(path)

            with patch("lx.intake.Path.iterdir", return_value=iter([link])), patch(
                "lx.intake.Path.lstat", autospec=True, side_effect=fake_lstat
            ):
                with self.assertRaises(IntakeError):
                    snapshot_folder(source, self.root / destination)
        with self.assertRaises(IntakeError):
            snapshot_folder(source, source / "nested-snapshot")
        with self.assertRaises(IntakeError):
            snapshot_folder(source, self.root)

    def test_rejects_protected_zip_names_before_decompression(self):
        data = make_zip([("folder/credentials.json", b"not a real credential")])
        with patch.object(zipfile.ZipFile, "open", side_effect=AssertionError("member opened")) as open_mock:
            with self.assertRaises(IntakeError):
                snapshot_zip(data, self.root / "snapshot")
        open_mock.assert_not_called()
        self.assertFalse((self.root / "snapshot").exists())

    def test_zip_snapshot_accepts_one_wrapper_folder_and_checks_hashes(self):
        contents = "line one\nline \u03bb\n".encode("utf-8")
        data = make_zip([("data-set/notes.txt", contents), ("data-set/image.jpg", b"jpeg sample")])
        destination = self.root / "zip snapshot"

        result = snapshot_zip(data, destination)

        self.assertEqual("data.zip", result["source_name"])
        by_path = {item["path"]: item for item in result["inventory"]}
        self.assertEqual({"notes.txt", "image.jpg"}, set(by_path))
        self.assertEqual(contents, (destination / "notes.txt").read_bytes())
        self.assertEqual(hashlib.sha256(contents).hexdigest(), by_path["notes.txt"]["sha256"])
        self.assertEqual(len(contents) + len(b"jpeg sample"), result["total_bytes"])

    def test_rejects_zip_traversal_absolute_backslash_and_drive_paths(self):
        for name in ("../escape.txt", "/absolute.txt", "C:/drive.txt", "folder\\escape.txt"):
            with self.subTest(name=name):
                if "\\" in name:
                    fake_info = SimpleNamespace(
                        filename=name, is_dir=lambda: False, external_attr=0,
                        file_size=1, compress_size=1,
                    )
                    with patch.object(zipfile.ZipFile, "infolist", return_value=[fake_info]):
                        with self.assertRaises(IntakeError):
                            snapshot_zip(make_zip([("safe.txt", b"x")]), self.root / ("bad-" + str(len(name))))
                else:
                    with self.assertRaises(IntakeError):
                        snapshot_zip(make_zip([(name, b"x")]), self.root / ("bad-" + str(len(name))))

    def test_rejects_casefold_duplicates_and_symlink_members(self):
        duplicate = make_zip([("data/a.txt", b"a"), ("data/A.TXT", b"b")])
        with self.assertRaises(IntakeError):
            snapshot_zip(duplicate, self.root / "duplicate")

        link = zipfile.ZipInfo("data/link.txt")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        symlink_zip = make_zip([(link, "target.txt")])
        with self.assertRaises(IntakeError):
            snapshot_zip(symlink_zip, self.root / "symlink")

    def test_rejects_compression_bomb_and_configured_file_and_byte_limits(self):
        bomb = make_zip([("data/zeros.txt", b"\x00" * (8 * 1024 * 1024))])
        with self.assertRaises(IntakeError):
            snapshot_zip(bomb, self.root / "bomb")

        two_files = make_zip([("a.txt", b"a"), ("b.txt", b"b")])
        with patch("lx.intake.MAX_FILES", 1):
            with self.assertRaises(IntakeError):
                snapshot_zip(two_files, self.root / "too-many")
        with patch("lx.intake.MAX_BYTES", 3):
            with self.assertRaises(IntakeError):
                snapshot_zip(make_zip([("a.txt", b"four")]), self.root / "too-large")

        folder = self.root / "folder"
        folder.mkdir()
        (folder / "a.txt").write_bytes(b"a")
        (folder / "b.txt").write_bytes(b"b")
        with patch("lx.intake.MAX_FILES", 1):
            with self.assertRaises(IntakeError):
                snapshot_folder(folder, self.root / "folder-many")
        with patch("lx.intake.MAX_BYTES", 1):
            with self.assertRaises(IntakeError):
                snapshot_folder(folder, self.root / "folder-bytes")

    def test_existing_destination_is_never_overwritten(self):
        source = self.root / "input"
        source.mkdir()
        (source / "note.txt").write_text("source", encoding="utf-8")
        destination = self.root / "existing"
        destination.mkdir()
        sentinel = destination / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaises(IntakeError):
            snapshot_folder(source, destination)
        self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))

    def test_windows_to_wsl_translation_and_rejections(self):
        self.assertEqual("/mnt/c/Users/Me/My Data", windows_to_wsl(r"C:\Users\Me\My Data"))
        self.assertEqual("/mnt/d", windows_to_wsl("D:/"))
        for invalid in ("", r"\\server\share", "//server/share", r"C:\a\..\b", "relative/path"):
            with self.subTest(path=invalid):
                with self.assertRaises(ValueError):
                    windows_to_wsl(invalid)


if __name__ == "__main__":
    unittest.main()
