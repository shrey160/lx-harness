"""A connected agent can approve a live spec (D-026): the record says so, and the budget stays with a person."""
from pathlib import Path
import unittest
from unittest.mock import patch

from lx import checks
from lx.inventory import write_synthetic
from lx.inventory_data import _tone
from lx.orchestration import Orchestration
from lx.taskrun import limitations
from test_tasks import Base


class Approving(Base):
    def setUp(self):
        super().setUp()
        self.lx = Orchestration(self.app, launch=False)
        self.path = self.folder()
        write_synthetic(Path(self.path)/"samples", 6)
        for sample in (Path(self.path)/"samples").iterdir():
            (sample/"request.wav").write_bytes(_tone(1))
        self.lx.init(self.path, "photo-request")
        self.lx.spec_draft(self.path, mode="live")
        self.app.registry.update("spark-contributor", api_key="dummy-ui-key")
        self.id = self.app.open_workspace(self.path)["id"]

    def test_an_agent_approves_and_starts_a_live_run(self):
        self.assertEqual(self.lx.approve(self.path), {"status": "approved", "version": 3})
        self.assertEqual(self.lx.spec_show(self.path)["approved"], {"by": "agent"})
        self.assertEqual(self.lx.run_start(self.path)["status"], "started")
        self.assertEqual(self.lx.run_status(self.path)["runs"][0]["approved_by"], "agent")

    def test_it_covers_a_trial(self):
        self.lx.approve(self.path)
        self.assertEqual(self.lx.trial_start(self.path)["status"], "started")

    def test_a_new_version_needs_approving_again(self):
        self.lx.approve(self.path)
        self.lx.spec_draft(self.path, goal="Count cups")
        self.assertEqual(self.lx.run_start(self.path)["status"], "pending")

    def test_the_budget_stays_with_a_person(self):
        self.lx.approve(self.path)
        self.assertFalse({"set_budget", "set_trial_cap", "set_grant"} & set(dir(self.lx)))
        with self.assertRaises(TypeError):
            self.lx.spec_draft(self.path, budget_usd=20)

    def test_a_run_budget_above_the_workspace_budget_is_refused(self):
        self.app.set_budget(self.id, 0.5)
        with self.assertRaisesRegex(ValueError, "workspace budget"):
            self.lx.approve(self.path)
        self.assertIsNone(self.lx.spec_show(self.path)["approved"])

    def test_a_weak_check_waits_for_a_person(self):
        weak = {"cases": 1, "samples": 6, "blank_score": 0.8, "wrong_score": 0.2, "weaknesses": ["A blank answer scores 0.80."]}
        with patch.object(checks, "self_test", return_value=weak):
            result = self.lx.approve(self.path)
        self.assertEqual(result["status"], "pending")
        self.assertRegex(result["reason"], "A blank")
        self.assertIsNone(self.lx.spec_show(self.path)["approved"])

    def test_a_preview_has_nothing_to_approve(self):
        self.lx.spec_draft(self.path, mode="controlled_preview")
        with self.assertRaisesRegex(ValueError, "preview"):
            self.lx.approve(self.path)

    def test_a_blocked_spec_stays_blocked(self):
        with patch.object(self.app.registry, "meta_key_source", return_value=None), self.assertRaisesRegex(ValueError, "Meta key"):
            self.lx.approve(self.path)

    def test_nothing_approves_while_a_run_is_going(self):
        self.lx.approve(self.path)
        self.lx.run_start(self.path)
        with self.assertRaisesRegex(ValueError, "active"):
            self.lx.approve(self.path)

    def test_the_package_says_an_agent_approved_it(self):
        spec = self.app.project(self.id)["draft"]
        self.assertIn("Approved by a connected agent", limitations(spec, {"per_sample": {}, "skipped": []}, None, True, "agent"))


if __name__ == "__main__":
    unittest.main()
