from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from lx.workspace import Workspace, _samples_dir, native_path, read_json, write_json


def inventory():
    return {"source_name": "x", "inventory": [], "total_bytes": 0}


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()

    def create(self, samples_dir="."):
        return Workspace.create(self.folder, name="Project", goal="Goal", samples_dir=samples_dir,
                                inventory=inventory(), compatible_demo=False)

    def test_create_lays_out_the_folder_and_hides_it_from_git(self):
        workspace = self.create()
        for name in ("project.json", "spec", "runs", "packages", "snapshots"):
            self.assertTrue((self.folder/".lx"/name).exists(), name)
        self.assertEqual((self.folder/".lx"/".gitignore").read_text(encoding="utf-8"), "*\n")
        self.assertEqual(Workspace.open(self.folder).project()["id"], workspace.project()["id"])
        with self.assertRaisesRegex(ValueError, "already a workspace"):
            self.create()

    def test_open_refuses_plain_folders_and_other_versions(self):
        with self.assertRaisesRegex(ValueError, "not a workspace"):
            Workspace.open(self.folder)
        workspace = self.create()
        record = workspace.project()
        record["schema"] = 99
        workspace.write_project(record)
        with self.assertRaisesRegex(ValueError, "different version"):
            Workspace.open(self.folder)

    def test_samples_folder_cannot_leave_the_workspace(self):
        for value in ("../outside", "/etc", "C:\\data", ".lx", "a/../../b", "", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _samples_dir(value)

    def test_spec_versions_are_never_rewritten(self):
        workspace = self.create()
        workspace.add_spec({"version": 1, "goal": "first"})
        workspace.add_spec({"version": 2, "goal": "second"})
        self.assertEqual(workspace.spec_versions(), [1, 2])
        with self.assertRaises(FileExistsError):
            workspace.add_spec({"version": 1, "goal": "changed"})
        self.assertEqual(workspace.spec(1)["goal"], "first")

    def test_run_identity_cannot_escape_the_runs_folder(self):
        workspace = self.create()
        for value in ("../../x", "a"*12+"/..", "ABCDEF123456", "", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                workspace.run_dir(value)

    def test_run_updates_merge_remove_and_log_events(self):
        workspace = self.create()
        (workspace.run_dir("a"*12)).mkdir()
        workspace.write_run({"id": "a"*12, "status": "queued", "phase": "queued", "created_at": 1, "error": "old"})
        record = workspace.update_run("a"*12, status="running", error=None)
        self.assertEqual(record["status"], "running")
        self.assertNotIn("error", workspace.run("a"*12))
        lines = (workspace.run_dir("a"*12)/"events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(l)["status"] for l in lines], ["queued", "running"])

    def test_concurrent_updates_lose_nothing(self):
        workspace = self.create()
        (workspace.run_dir("b"*12)).mkdir()
        workspace.write_run({"id": "b"*12, "status": "queued", "phase": "queued", "created_at": 1, "count": 0})
        def bump(_):
            view = Workspace.open(self.folder)
            with view.lock():
                record = view.run("b"*12)
                record["count"] += 1
                view.write_run(record)
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(bump, range(30)))
        self.assertEqual(workspace.run("b"*12)["count"], 30)

    def test_lock_excludes_another_process(self):
        workspace = self.create()
        script = ("import sys\nfrom h2portable import _platform\nimport os\n"
                  "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)\n"
                  "print(_platform.try_lock(fd))\n")
        with workspace.lock():
            held = subprocess.run([sys.executable, "-c", script, str(workspace.home/"lock")],
                                  capture_output=True, text=True, timeout=30)
        self.assertEqual(held.stdout.strip(), "False", held.stderr)

    def test_write_json_leaves_no_stage_file_and_round_trips(self):
        target = self.folder/"value.json"
        write_json(target, {"text": "नमस्ते"})
        self.assertEqual(read_json(target), {"text": "नमस्ते"})
        self.assertEqual([p.name for p in self.folder.iterdir()], ["value.json"])

    @unittest.skipUnless(os.name == "posix", "WSL path translation")
    def test_native_path_translates_windows_drives_only_on_posix(self):
        self.assertEqual(native_path(r"C:\Work\Project"), Path("/mnt/c/Work/Project"))
        self.assertEqual(native_path("/home/me/project"), Path("/home/me/project"))

    def test_native_path_needs_a_full_path(self):
        for value in ("", "   ", "relative/folder", None, 7):
            with self.subTest(value=value), self.assertRaises(ValueError):
                native_path(value)


if __name__ == "__main__":
    unittest.main()
