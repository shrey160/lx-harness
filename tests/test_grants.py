"""A person's one-time grant (D-024): an agent may start live runs inside it, and anything outside returns pending."""
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from lx import checks, tasks
from lx.inventory import write_synthetic
from lx.inventory_data import _tone
from lx.orchestration import ACTIONS, Orchestration
from lx.taskrun import limitations
from test_tasks import Base

ACCEPTED = {"data_approved": True, "contributor_accepted": True, "notice_accepted": True}


class Grants(Base):
    def setUp(self):
        super().setUp()
        self.lx = Orchestration(self.app, launch=False)
        self.path = self.folder()
        write_synthetic(Path(self.path)/"samples", 6)
        for sample in (Path(self.path)/"samples").iterdir():
            (sample/"request.wav").write_bytes(_tone(1))
        self.lx.init(self.path, "photo-request", goal="Count things in photos")
        self.lx.spec_draft(self.path, mode="live")
        self.app.registry.update("spark-contributor", api_key="dummy-ui-key")
        self.id = self.app.open_workspace(self.path)["id"]
        self.workspace = self.app.workspace(self.id)

    def grant(self, cap=3, **extra):
        return self.app.set_grant(self.id, {"cap_usd": cap, **ACCEPTED, **extra})

    def start(self):
        return self.lx.run_start(self.path)

    def finish(self, run_id, spent=0.0):
        """A finished run that cost `spent`, through the ledger a real run writes to."""
        ledger = self.app._ledger(self.workspace)
        if spent:
            ledger.settle(ledger.reserve(spent, "worker", "run", run_id+"/call"), cost_usd=spent, input_tokens=1, output_tokens=1)
        self.workspace.update_run(run_id, status="stopped", launch_pending=False)

    def assert_pending(self, reason):
        before = len(self.app.runs(self.id))
        started = self.start()
        self.assertEqual(started["status"], "pending")
        self.assertRegex(started["reason"], reason)
        self.assertEqual(len(self.app.runs(self.id)), before)


class SettingOne(Grants):
    def test_an_agent_has_no_action_for_it(self):
        self.assertFalse({"grant", "set_grant", "revoke_grant", "start_granted"} & set(ACTIONS))

    def test_it_defaults_to_seven_days_and_three_runs(self):
        grant = self.grant()["grant"]
        self.assertEqual(grant["runs"], 3)
        self.assertAlmostEqual(grant["expires_at"], time.time()+7*86400, delta=60)

    def test_it_needs_what_an_approval_needs(self):
        for missing in ("data_approved", "contributor_accepted"):
            with self.subTest(missing), self.assertRaisesRegex(ValueError, "Approve|Accept"):
                self.app.set_grant(self.id, {"cap_usd": 1, **ACCEPTED, missing: False})
        self.assertIsNone(self.app.project(self.id)["grant"])

    def test_its_cap_stays_inside_the_workspace_budget(self):
        for cap in (0.01, 50, True, "1", float("nan")):
            with self.subTest(cap), self.assertRaisesRegex(ValueError, "cap"):
                self.grant(cap=cap)

    def test_its_days_and_runs_are_bounded(self):
        for field, value in (("days", 0), ("days", 31), ("runs", 0), ("runs", 21), ("runs", True), ("days", 1.5)):
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, field):
                self.grant(**{field: value})

    def test_the_notice_it_accepts_names_the_agent(self):
        self.grant()
        self.assertIn("your connected agent", self.app._notice_hosts(self.id))


class Inside(Grants):
    def test_a_run_starts_and_says_where_it_came_from(self):
        self.grant()
        started = self.start()
        self.assertEqual(started["status"], "started")
        run = self.lx.run_status(self.path)["runs"][0]
        self.assertEqual(run["approved_by"], "grant")
        self.assertTrue(run["grant"])
        self.assertEqual(self.lx.spec_show(self.path)["approved"], {"by": "grant"})

    def test_a_person_approved_run_does_not_use_it(self):
        self.grant()
        self.approve(self.app.project(self.id), notice_accepted=True)
        self.assertEqual(self.start()["status"], "started")
        self.assertEqual(self.lx.run_status(self.path)["runs"][0]["approved_by"], "person")
        self.assertEqual(self.lx.spec_show(self.path)["grant"]["runs_used"], 0)

    def test_the_package_says_no_person_reviewed_it(self):
        spec = self.app.project(self.id)["draft"]
        report = {"per_sample": {}, "skipped": []}
        self.assertIn("under a person's grant", limitations(spec, report, None, True, "grant"))
        self.assertNotIn("grant", limitations(spec, report, None, True))

    def test_an_agent_approved_run_does_not_use_it(self):
        self.grant()
        self.assertEqual(self.lx.approve(self.path)["status"], "approved")
        self.assertEqual(self.start()["status"], "started")
        self.assertEqual(self.lx.spec_show(self.path)["grant"]["runs_used"], 0)

    def test_it_shows_what_is_left(self):
        self.grant(cap=3)
        self.finish(self.start()["run_id"], 0.25)
        left = self.lx.spec_show(self.path)["grant"]
        self.assertEqual((left["runs_used"], left["spent_usd"], left["cap_usd"]), (1, 0.25, 3))

    def test_a_second_start_waits_for_the_first_run(self):
        self.grant()
        self.start()
        with self.assertRaisesRegex(ValueError, "active"):
            self.start()


class Outside(Grants):
    def test_no_grant(self):
        self.assert_pending("approve this live run")

    def test_expired(self):
        self.grant()
        with patch("lx.engine.time.time", return_value=time.time()+8*86400):
            self.assert_pending("expired")

    def test_revoked(self):
        self.grant()
        self.app.revoke_grant(self.id)
        self.assert_pending("revoked")

    def test_runs_used_up(self):
        self.grant(runs=1)
        self.finish(self.start()["run_id"])
        self.assert_pending("used up")

    def test_a_new_grant_starts_its_own_count(self):
        self.grant(runs=1)
        self.finish(self.start()["run_id"])
        self.grant(runs=1)
        self.assertEqual(self.start()["status"], "started")

    def test_a_run_that_would_pass_the_cap(self):
        self.grant(cap=1.5)
        self.finish(self.start()["run_id"], 0.75)
        self.assert_pending("cap")

    def test_a_held_call_counts_as_spent(self):
        self.grant(cap=1.5)
        run_id = self.start()["run_id"]
        self.finish(run_id)
        ledger = self.app._ledger(self.workspace)
        ledger.unknown(ledger.reserve(0.75, "worker", "run", run_id+"/call"), "no reply")
        self.assert_pending("cap")

    def test_spend_on_other_runs_does_not_count(self):
        self.grant(cap=1)
        ledger = self.app._ledger(self.workspace)
        ledger.settle(ledger.reserve(0.5, "worker", "run", "someone-else/call"), cost_usd=0.5, input_tokens=1, output_tokens=1)
        self.assertEqual(self.start()["status"], "started")

    def test_a_tool_changed(self):
        self.grant()
        self.lx.spec_draft(self.path, tools=[tasks.TRANSCRIPTION])
        self.assert_pending("tools changed")

    def test_a_worker_changed(self):
        self.grant()
        self.lx.spec_draft(self.path, worker=self.entry("other"))
        self.assert_pending("improver or worker")

    def test_the_samples_changed(self):
        self.grant()
        (Path(self.path)/"samples"/"sample-01"/"extra.txt").write_text("new", encoding="utf-8")
        self.assert_pending("samples changed")

    def test_a_weak_check_waits_for_a_person(self):
        self.grant()
        weak = {"cases": 1, "samples": 6, "blank_score": 0.8, "wrong_score": 0.2, "weaknesses": ["A blank answer scores 0.80."]}
        with patch.object(checks, "self_test", return_value=weak):
            self.assert_pending("review this check: A blank")
        self.assertEqual(self.app.runs(self.id), [])

    def test_a_run_with_no_check_waits_for_a_person(self):
        self.lx.spec_draft(self.path, no_check=True)
        self.grant()
        self.assert_pending("no check")
        self.assertEqual(self.app.runs(self.id), [])

    def test_a_spec_a_person_must_fix_stays_blocked(self):
        self.grant()
        with patch.object(self.app.registry, "meta_key_source", return_value=None):
            self.assertEqual(self.start()["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
