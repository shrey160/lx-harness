"""Run safety and visibility: failure reasons, held calls, run health, early stop, regrade."""
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lx import sandbox
from lx.budget import BudgetError, BudgetLedger
from lx.engine import Engine
from lx.health import RunHealth, StoppedEarly, read_health
from lx.inventory_workflow import InventoryPreviewTransport
from lx.proposer import SparkProposer
from lx.providers import SPARK
from lx.spark import ModelError, RateLimited, SparkClient, usage_of
from lx.spike import wire_reply
from lx.usage import summarize, usage_rows


class MemoryKeychain:
    def __init__(self): self.values = {}
    def available(self): return True
    def set(self, name, value): self.values[name] = value
    def get(self, name): return self.values.get(name)
    def delete(self, name): self.values.pop(name, None)


class Temp(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)


class LedgerTests(Temp):
    def ledger(self):
        return BudgetLedger(self.root/"ledger.sqlite3", 1, 0)

    def test_a_failed_call_keeps_its_reason(self):
        ledger = self.ledger()
        ledger.unknown(ledger.reserve("0.1", "worker", "execute", "run/a"), "provider did not reply in time")
        ledger.unknown(ledger.reserve("0.1", "worker", "execute", "run/b"))
        held = summarize(usage_rows(ledger, "run/"))["held"]
        self.assertEqual(held["calls"], 2)
        self.assertAlmostEqual(held["usd"], 0.2)
        self.assertEqual(held["reasons"], {"provider did not reply in time": 1, "no reason recorded": 1})

    def test_release_ends_a_hold_as_free_or_as_spent(self):
        ledger = self.ledger()
        free, spent = ledger.reserve("0.1", "worker", "x"), ledger.reserve("0.2", "worker", "x")
        ledger.unknown(free, "timed out")
        ledger.unknown(spent, "timed out")
        ledger.release(free, charged=False)
        ledger.release(spent, charged=True)
        summary = ledger.summary()
        self.assertEqual((summary["reserved_usd"], summary["spent_usd"], summary["unknown_count"]), (0, 0.2, 0))
        self.assertIn("owner released it", usage_rows(ledger)[0]["reason"])
        with self.assertRaises(BudgetError):
            ledger.release(free, charged=False)

    def test_a_ledger_from_before_reasons_opens_and_keeps_its_rows(self):
        path = self.root/"old.sqlite3"
        db = sqlite3.connect(path)
        db.executescript("""CREATE TABLE ledger_config (singleton INTEGER PRIMARY KEY, cap_nanos INTEGER NOT NULL, initial_nanos INTEGER NOT NULL);
            INSERT INTO ledger_config VALUES (1, 1000000000, 0);
            CREATE TABLE reservations (id TEXT PRIMARY KEY, role TEXT NOT NULL, phase TEXT NOT NULL, candidate TEXT NOT NULL,
              reserved_nanos INTEGER NOT NULL, actual_nanos INTEGER, status TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER);
            INSERT INTO reservations VALUES ('a','worker','x','run/1',5,NULL,'unknown',NULL,NULL);""")
        db.close()
        ledger = BudgetLedger(path, 1, 0)
        self.assertEqual(usage_rows(ledger)[0]["reason"], None)
        ledger.release("a", charged=False)
        self.assertEqual(ledger.summary()["unknown_count"], 0)


class ClientTests(Temp):
    def client(self, transport, ledger):
        return SparkClient(transport, ledger, retries=0)

    def ask(self, client):
        return client.chat([{"role": "user", "content": "x"}], timeout=5)

    def test_each_kind_of_failure_is_named_on_the_ledger(self):
        ledger = BudgetLedger(self.root/"ledger.sqlite3", 20, 0)
        def timed_out(body, timeout): raise ModelError("provider did not reply in time; cost remains reserved")
        def limited(body, timeout): raise RateLimited(0)
        def broken(body, timeout): raise KeyError("private detail")
        def no_usage(body, timeout): return {"choices": []}
        for transport, error in ((timed_out, ModelError), (limited, ModelError), (broken, KeyError), (no_usage, ModelError)):
            with self.assertRaises(error):
                self.ask(self.client(transport, ledger))
        reasons = [row["reason"] for row in usage_rows(ledger)]
        self.assertEqual(reasons, ["provider did not reply in time; cost remains reserved", "provider rate limited the request",
                                   "controller error: KeyError", "provider usage missing; cost unknown"])

    def test_a_cost_the_provider_states_is_kept_beside_ours(self):
        ledger = BudgetLedger(self.root/"ledger.sqlite3", 20, 0)
        reply = wire_reply("ok")
        reply["usage"]["cost"] = 0.0005
        message, usage = self.ask(self.client(lambda body, timeout: reply, ledger))
        self.assertEqual(message["content"], "ok")
        self.assertNotIn("reported_cost_usd", usage)
        compared = summarize(usage_rows(ledger))["provider_reported"]
        self.assertEqual(compared["calls"], 1)
        self.assertEqual(compared["reported_usd"], 0.0005)

    def test_a_strange_stated_cost_is_ignored(self):
        for bad in (True, "0.1", -1, float("inf"), None):
            reply = wire_reply("ok")
            reply["usage"]["cost"] = bad
            self.assertNotIn("reported_cost_usd", usage_of(reply, SPARK))


class HealthTests(Temp):
    def test_stops_at_the_limit_of_empty_runs_in_a_row(self):
        health = RunHealth(self.root/"health.json", stop_after=2)
        health.agent(False, "no answer within 13 turns")
        health.agent(True)
        health.agent(False, "no answer within 13 turns")
        with self.assertRaisesRegex(StoppedEarly, "2 agent runs in a row"):
            health.agent(False, "no answer within 13 turns")
        saved = read_health(self.root/"health.json")
        self.assertEqual((saved["agent"]["answered"], saved["agent"]["no_answer"]), (1, 3))
        self.assertEqual(saved["agent"]["reasons"], {"no answer within 13 turns": 3})
        self.assertIn("Stopped early", saved["stopped_early"])

    def test_a_failed_improver_session_is_counted_with_its_reason(self):
        health = RunHealth(self.root/"health.json", stop_after=8)
        class Failing:
            profile = SPARK
            def session(self, *_args, **_kwargs):
                raise ModelError("tool session exceeded the turn limit")
        proposer = SparkProposer(Failing(), self.root/"audit", health=health)
        context = self.root/"context"/"context"
        context.mkdir(parents=True)
        (context/"note.md").write_text("x", encoding="utf-8")
        with self.assertRaises(Exception):
            proposer.call("mission", context.parent, "stage", harvest="context/harness")
        saved = read_health(self.root/"health.json")
        self.assertEqual(saved["improver"]["failed"], 1)
        self.assertEqual(saved["improver"]["reasons"], {"tool session exceeded the turn limit": 1})

    def test_no_file_means_no_health_yet(self):
        self.assertIsNone(read_health(self.root/"missing.json"))


class NeverAnswers(InventoryPreviewTransport):
    @staticmethod
    def _agent(messages):
        return wire_reply(calls=[{"id": f"step-{len(messages)}", "type": "function",
                                  "function": {"name": "transcribe", "arguments": "{}"}}])


class EngineTests(Temp):
    def setUp(self):
        super().setUp()
        self.app = Engine(self.root/"state", keychain=MemoryKeychain())

    def folder(self):
        return tempfile.mkdtemp(dir=self.root)

    def demo(self):
        return self.app.create_project({"source": "demo", "path": self.folder()})

    def inventory(self):
        return self.app.create_project({"source": "demo", "task": "inventory", "name": "Inventory", "path": self.folder()})

    def approve(self, project):
        return self.app.approve(project["id"], {"version": project["draft"]["version"], "data_approved": True, "contributor_accepted": True})

    def started(self, project):
        project = self.approve(project)
        return project, self.app.start(project["id"], launch=False), self.app.workspace(project["id"])

    def entry(self, **fields):
        return self.app.registry.create("Entry", "test/entry", ["General"], base_url="https://openrouter.ai/api/v1", **fields)

    def test_the_stop_limit_is_a_limit_and_old_specs_get_it(self):
        p = self.demo()
        self.assertEqual(p["draft"]["limits"]["stop_after_empty_runs"], 8)
        older = {key: value for key, value in p["draft"].items() if key != "limits"}
        older["limits"] = {key: value for key, value in p["draft"]["limits"].items() if key != "stop_after_empty_runs"}
        self.assertEqual(self.app.limits_of(older)["stop_after_empty_runs"], 8)

    def test_held_calls_are_released_only_after_the_run_stops(self):
        p, job, workspace = self.started(self.demo())
        ledger = workspace.preview_ledger
        ledger.unknown(ledger.reserve("0.1", "worker", "execute", job["id"]+"/x"), "timed out")
        ledger.settle(ledger.reserve("0.1", "worker", "execute", job["id"]+"/y"), cost_usd="0.05", input_tokens=1, output_tokens=1)
        with self.assertRaisesRegex(ValueError, "Stop the run"):
            self.app.release_held(p["id"], job["id"], False)
        workspace.update_run(job["id"], status="failed", launch_pending=False)
        with self.assertRaisesRegex(ValueError, "billed"):
            self.app.release_held(p["id"], job["id"], "no")
        self.app.release_held(p["id"], job["id"], False)
        self.assertEqual(ledger.summary()["reserved_usd"], 0)
        self.assertEqual(ledger.summary()["spent_usd"], 0.05)  # a settled call is not touched

    def test_a_run_with_no_finished_loop_cannot_be_graded(self):
        p, job, workspace = self.started(self.inventory())
        with self.assertRaisesRegex(ValueError, "Only a failed task run"):
            self.app.regrade(p["id"], job["id"])
        workspace.update_run(job["id"], status="failed", launch_pending=False)
        with self.assertRaisesRegex(ValueError, "nothing to grade"):
            self.app.regrade(p["id"], job["id"])

    def test_a_demo_run_cannot_be_graded_again(self):
        p, job, workspace = self.started(self.demo())
        workspace.update_run(job["id"], status="failed", launch_pending=False)
        with self.assertRaisesRegex(ValueError, "Only a failed task run"):
            self.app.regrade(p["id"], job["id"])

    def test_free_providers_are_named_for_the_warning(self):
        free = self.entry(accepts_images=True, supports_tools=True, pricing={"input_per_million": 0, "output_per_million": 0})
        p = self.app.edit_spec(self.demo()["id"], {"goal": "g", "budget_usd": 1, "roles": {r: free["id"] for r in ("orchestrator", "improver", "worker")}})
        self.assertEqual(p["free_providers"], ["Entry"])
        self.assertEqual(self.demo()["free_providers"], [])

    def test_a_provider_test_makes_one_short_call_on_its_own_ledger(self):
        entry = self.entry(pricing={"input_per_million": 0.3, "output_per_million": 1.2})
        bodies = []
        def transport(body, timeout):
            bodies.append(body)
            return wire_reply("OK")
        self.app._live_transport_factory = lambda hint=None: transport
        result = self.app.test_provider(entry["id"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["reply"], "OK")
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0]["max_tokens"], 256)
        ledger = BudgetLedger(self.root/"state"/"ledgers"/"provider-tests.sqlite3", 1, 0)
        self.assertEqual(ledger.summary()["settled_calls"], 1)

    def test_a_provider_test_reports_a_failure_and_a_cut_off_reply(self):
        entry = self.entry(pricing={"input_per_million": 0, "output_per_million": 0})
        def refused(body, timeout):
            raise ModelError("provider request failed (HTTP 401); response body withheld")
        self.app._live_transport_factory = lambda hint=None: refused
        result = self.app.test_provider(entry["id"])
        self.assertEqual((result["ok"], result["error"]), (False, "provider request failed (HTTP 401); response body withheld"))
        cut = wire_reply("thinking")
        cut["choices"][0]["finish_reason"] = "length"
        self.app._live_transport_factory = lambda hint=None: lambda body, timeout: cut
        self.assertTrue(self.app.test_provider(entry["id"])["ok"])

    def test_a_scratch_engine_never_tests_a_provider_live(self):
        result = self.app.test_provider(self.entry()["id"])
        self.assertFalse(result["ok"])
        self.assertIn("unavailable", result["error"])

    def test_only_a_chat_entry_with_an_endpoint_can_be_tested(self):
        with self.assertRaises(ValueError):
            self.app.test_provider("sam-catalog-missing")

    def test_the_folder_dialog_returns_the_choice_or_says_it_is_not_available(self):
        with patch("lx.engine.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "C:/Work/folder\n", "")):
            self.assertEqual(self.app.pick_folder(), {"path": "C:/Work/folder"})
        with patch("lx.engine.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "\n", "")):
            self.assertEqual(self.app.pick_folder(), {"path": ""})
        with patch("lx.engine.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "no tkinter")):
            with self.assertRaisesRegex(ValueError, "type the path"):
                self.app.pick_folder()


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class TaskRunHealthTests(EngineTests):
    def test_a_run_whose_agent_never_answers_stops_early_and_says_why(self):
        from lx.worker import execute
        p = self.inventory()
        p = self.app.edit_spec(p["id"], {"goal": "g", "budget_usd": 1, "limits": {"stop_after_empty_runs": 2, "agent_turns_per_photo": 1}})
        p, job, workspace = self.started(p)
        with patch("lx.engine.InventoryPreviewTransport", NeverAnswers), self.assertRaisesRegex(StoppedEarly, "agent runs in a row gave no answer"):
            execute(self.app, workspace, job["id"], workspace.run_dir(job["id"])/"snapshot")
        health = read_health(workspace.run_dir(job["id"])/"health.json")
        self.assertLess(health["agent"]["no_answer"], 8)  # agent runs go in parallel, so one more may finish as it stops
        self.assertEqual(health["agent"]["answered"], 0)
        self.assertIn("Stopped early", health["stopped_early"])

    def test_a_run_that_works_records_its_health_and_a_failed_grade_recovers(self):
        from lx.worker import execute
        p, job, workspace = self.started(self.inventory())
        execute(self.app, workspace, job["id"], workspace.run_dir(job["id"])/"snapshot")
        view = next(j for j in self.app.state()["jobs"] if j["id"] == job["id"])
        self.assertGreater(view["health"]["agent"]["answered"], 0)
        self.assertGreater(view["health"]["improver"]["ok"], 0)
        self.assertEqual(view["health"]["improver"]["failed"], 0)
        workspace.update_run(job["id"], status="failed", phase="failed", error="grading crashed", result=None)
        recovered = self.app.regrade(p["id"], job["id"])
        self.assertEqual(recovered["status"], "completed")
        self.assertNotIn("error", recovered)
        self.assertTrue(recovered["result"]["regraded"])
        self.assertEqual(len(recovered["result"]["scores"]["base"]), 6)
        package = json.loads((workspace.package_dir(job["id"])/"result.json").read_text(encoding="utf-8"))
        self.assertTrue(package["regraded"])


if __name__ == "__main__":
    unittest.main()
