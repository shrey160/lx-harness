"""Workspace lifecycle on disposable data only; no real credentials or model calls."""
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from lx.engine import Engine
from lx.orchestration import Orchestration
from lx.usage import usage_rows
from test_tasks import Base, MemoryKeychain


class Lifecycle(Base):
    def project(self):
        return self.app.create_project({"source": "demo", "name": "Disposable workspace", "path": self.folder()})

    def test_archive_is_sticky_across_reopen_and_new_engine_and_restore_allows_start(self):
        p = self.project()
        self.approve(p)
        self.assertTrue(self.app.archive_workspace(p["id"])["archived"])
        self.assertTrue(self.app.open_workspace(p["path"])["archived"])
        other = Engine(self.app.root, keychain=MemoryKeychain())
        self.assertTrue(other.project(p["id"])["archived"])
        self.assertTrue(other.state()["projects"][0]["archived"])
        self.assertIsNotNone(other.project(p["id"])["approved"])
        self.assertFalse(other.archive_workspace(p["id"], False)["archived"])
        self.assertEqual(other.start(p["id"], launch=False)["status"], "queued")

    def test_archived_work_cannot_spend_or_start_through_other_entry_points(self):
        p = self.project()
        self.approve(p)
        self.app.archive_workspace(p["id"])
        calls = [lambda: self.app.start(p["id"], launch=False),
                 lambda: self.app.start(p["id"], launch=False, trial=True),
                 lambda: self.app.start_granted(p["id"], launch=False),
                 lambda: self.app.resume(p["id"], "0"*12),
                 lambda: self.app.approve(p["id"], {}),
                 lambda: self.app.set_grant(p["id"], {}),
                 lambda: self.app.discuss(p["id"], "hello", True),
                 lambda: self.app.try_judges(p["id"], spend={}),
                 lambda: self.app.fresh(p["id"], "0"*12)]
        for call in calls:
            with self.subTest(call=call), self.assertRaisesRegex(ValueError, "archived"):
                call()

    def test_active_jobs_block_archive_restore_and_delete(self):
        p = self.project()
        self.approve(p)
        job = self.app.start(p["id"], launch=False)
        workspace = self.app.workspace(p["id"])
        for status in ("queued", "running"):
            workspace.update_run(job["id"], status=status)
            for action in (lambda: self.app.archive_workspace(p["id"]),
                           lambda: self.app.archive_workspace(p["id"], False),
                           lambda: self.app.delete_workspace(p["id"], p["name"])):
                with self.subTest(status=status), self.assertRaisesRegex(ValueError, "active run"):
                    action()
        self.assertTrue(workspace.home.exists())

    def test_unsettled_calls_do_not_block_deletion_or_change_the_ledger(self):
        for unknown in (False, True):
            p = self.project()
            ledger = self.app._ledger(self.app.workspace(p["id"]))
            reservation = ledger.reserve(0, "worker", "test", "scratch")
            if unknown:
                ledger.unknown(reservation, "test uncertainty")
            with self.assertRaisesRegex(ValueError, "unsettled"):
                self.app.archive_workspace(p["id"])
            before = usage_rows(ledger)
            self.assertTrue(self.app.delete_workspace(p["id"])["deleted"])
            self.assertEqual(usage_rows(ledger), before)
            kept = next(item for item in self.app.state()["deleted_workspaces"] if item["id"] == p["id"])
            self.assertEqual(kept["usage"]["held"]["calls"], 1)

    def test_deleted_workspace_billing_can_be_reconciled_later_without_restoring_it(self):
        for charged in (False, True):
            p = self.project()
            ledger = self.app._ledger(self.app.workspace(p["id"]))
            ledger.reserve(0.01, "worker", "test", "scratch/a")
            uncertain = ledger.reserve(0.02, "tool", "test", "scratch/b")
            ledger.unknown(uncertain, "Test uncertainty")
            self.app.delete_workspace(p["id"])
            fresh = Engine(self.app.root, keychain=MemoryKeychain())
            kept = next(item for item in fresh.state()["deleted_workspaces"] if item["id"] == p["id"])
            self.assertEqual(kept["usage"]["held"], {"calls":2,"usd":0.03,"reasons":{"no reason recorded":1,"Test uncertainty":1}})
            with self.assertRaises(ValueError):
                fresh.release_deleted_workspace_calls(p["id"], "yes")
            result = fresh.release_deleted_workspace_calls(p["id"], charged)
            self.assertEqual(result["usage"]["held"]["calls"], 0)
            self.assertAlmostEqual(result["usage"]["spent_usd"], 0.03 if charged else 0)
            self.assertFalse((Path(p["path"])/".lx").exists())
            self.assertTrue((Path(p["path"])/"samples"/"manifest.json").exists())

    def test_one_click_delete_preserves_files_spend_and_legacy_name_checks(self):
        p = self.project()
        workspace = self.app.workspace(p["id"])
        original = {path.relative_to(workspace.folder): path.read_bytes() for path in workspace.samples_path.rglob("*") if path.is_file()}
        unrelated = workspace.folder/"notes.txt"
        unrelated.write_text("Keep me", encoding="utf-8")
        ledger = self.app._ledger(workspace)
        reservation = ledger.reserve(0.01, "worker", "test", "scratch")
        ledger.settle(reservation, cost_usd=0.005, input_tokens=1, output_tokens=1)
        before = usage_rows(ledger)
        for confirmation in ("", p["name"].lower(), p["name"]+" "):
            with self.assertRaisesRegex(ValueError, "workspace name"):
                self.app.delete_workspace(p["id"], confirmation)
        self.app.archive_workspace(p["id"])
        for kind in ("approval", "grant", "notice"):
            self.app.store.save(kind, {"id": p["id"], "test": True})
        result = self.app.delete_workspace(p["id"])
        self.assertEqual(result, {"deleted": True, "samples_preserved": True, "spend_records_preserved": True})
        self.assertFalse(workspace.home.exists())
        self.assertEqual(list(workspace.folder.glob(".lx-delete-*")), [])
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "Keep me")
        for name, data in original.items():
            self.assertEqual((workspace.folder/name).read_bytes(), data)
        self.assertEqual(usage_rows(ledger), before)
        for kind in ("workspace", "approval", "grant", "notice"):
            with self.assertRaises(ValueError):
                self.app.store.get(kind, p["id"])
        self.assertEqual(self.app.state()["projects"], [])

    def test_agent_permission_is_off_by_default_persists_and_can_be_revoked(self):
        p = self.project()
        lx = Orchestration(self.app, launch=False)
        self.assertFalse(lx.status()["can_delete_workspaces"])
        with self.assertRaisesRegex(ValueError, "deletion by agents is off"):
            lx.workspace_delete(p["path"])
        for bad in (None, 1, "true", {}, []):
            with self.assertRaises(ValueError):
                self.app.set_agent_workspace_deletion(bad)
        self.app.set_agent_workspace_deletion(True)
        other = Engine(self.app.root, keychain=MemoryKeychain())
        self.assertTrue(other.agent_workspace_deletion())
        self.assertTrue(lx.status()["can_delete_workspaces"])
        other.set_agent_workspace_deletion(False)
        with self.assertRaisesRegex(ValueError, "deletion by agents is off"):
            lx.workspace_delete(p["path"])
        other.set_agent_workspace_deletion(True)
        self.app.set_agent_access(False)
        self.assertFalse(lx.status()["can_delete_workspaces"])
        with self.assertRaisesRegex(ValueError, "Agent access is stopped"):
            lx.workspace_delete(p["path"])
        self.app.set_agent_access(True)
        self.assertTrue(lx.workspace_delete(p["path"])["deleted"])
        self.assertTrue((Path(p["path"])/"samples"/"manifest.json").exists())

    def test_agent_deletion_blocks_active_runs_but_keeps_unsettled_calls(self):
        p = self.project()
        lx = Orchestration(self.app, launch=False)
        self.app.set_agent_workspace_deletion(True)
        self.approve(p)
        job = self.app.start(p["id"], launch=False)
        with self.assertRaisesRegex(ValueError, "active run"):
            lx.workspace_delete(p["path"])
        self.app.workspace(p["id"]).update_run(job["id"], status="cancelled", launch_pending=False)
        self.app._ledger(self.app.workspace(p["id"])).reserve(0, "worker", "test", "scratch")
        self.assertTrue(lx.workspace_delete(p["path"])["deleted"])
        self.assertEqual(self.app.state()["deleted_workspaces"][0]["usage"]["held"]["calls"], 1)

    def test_failed_rename_restores_registration_and_archive_flag(self):
        p = self.project()
        self.app.archive_workspace(p["id"])
        with patch("lx.engine.os.replace", side_effect=PermissionError("in use")):
            with self.assertRaisesRegex(ValueError, "close files"):
                self.app.delete_workspace(p["id"], p["name"])
        self.assertTrue(self.app.project(p["id"])["archived"])
        self.assertFalse(self.app.store.get("workspace", p["id"]).get("deleting", False))

    def test_partial_cleanup_reports_exact_retained_directory(self):
        p = self.project()
        with patch("lx.engine.shutil.rmtree", side_effect=PermissionError("in use")):
            result = self.app.delete_workspace(p["id"], p["name"])
        retained = Path(result["cleanup_pending"])
        self.assertEqual(retained.parent, Path(p["path"]))
        self.assertTrue(retained.name.startswith(".lx-delete-"))
        self.assertTrue((retained/"project.json").is_file())
        self.assertFalse((Path(p["path"])/".lx").exists())
        self.assertEqual(self.app.state()["projects"], [])

    def test_tombstone_blocks_open_and_start_during_rename(self):
        p = self.project()
        replace = os.replace
        def rename(source, target):
            for action in (lambda: self.app.open_workspace(p["path"]),
                           lambda: self.app.start(p["id"], launch=False)):
                with self.assertRaisesRegex(ValueError, "being deleted"):
                    action()
            return replace(source, target)
        with patch("lx.engine.os.replace", side_effect=rename):
            self.app.delete_workspace(p["id"], p["name"])

    def test_nested_link_does_not_delete_its_target(self):
        p = self.project()
        workspace = self.app.workspace(p["id"])
        outside = Path(self.folder())
        sentinel = outside/"keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        try:
            (workspace.home/"linked").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks requires Windows developer mode")
        self.app.delete_workspace(p["id"], p["name"])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_rejects_linked_lx_root(self):
        p = self.project()
        workspace = self.app.workspace(p["id"])
        moved = workspace.folder/"keep-lx"
        workspace.home.rename(moved)
        try:
            workspace.home.symlink_to(moved, target_is_directory=True)
        except OSError:
            moved.rename(workspace.home)
            self.skipTest("Creating symlinks requires Windows developer mode")
        with self.assertRaisesRegex(ValueError, "cannot be a link"):
            self.app.delete_workspace(p["id"], p["name"])
        self.assertTrue((moved/"project.json").exists())

    @unittest.skipUnless(os.name == "nt", "Windows junction boundary")
    def test_windows_junctions_cannot_redirect_deletion(self):
        # Junctions need no developer-mode privilege, unlike Windows symlinks.
        p = self.project()
        workspace = self.app.workspace(p["id"])
        outside = Path(self.folder())
        sentinel = outside/"keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(workspace.home/"linked"), str(outside)],
                       check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        self.app.delete_workspace(p["id"], p["name"])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        p = self.project()
        workspace = self.app.workspace(p["id"])
        moved = workspace.folder/"keep-lx"
        workspace.home.rename(moved)
        subprocess.run(["cmd", "/c", "mklink", "/J", str(workspace.home), str(moved)],
                       check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        with self.assertRaisesRegex(ValueError, "cannot be a link"):
            self.app.delete_workspace(p["id"], p["name"])
        self.assertTrue((moved/"project.json").exists())
