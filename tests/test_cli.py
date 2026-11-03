"""The `lx` command: the actions in a terminal, and a person's steps refused without one."""
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from lx import cli
from lx.inventory import write_synthetic
from lx.inventory_data import _tone
from lx.orchestration import Orchestration
from test_tasks import Base


class Terminal:
    def __init__(self, answer):
        self.answer = answer

    def isatty(self):
        return self.answer is not None


class Commands(Base):
    def test_workspace_delete_is_gated_even_without_an_interactive_terminal(self):
        p = self.app.create_project({"source": "demo", "name": "Disposable CLI workspace", "path": self.folder()})
        self.assertEqual(self.lx("workspace", "delete", "--workspace", p["path"])[0], 1)
        self.assertIn("deletion by agents is off", self.err)
        self.app.set_agent_workspace_deletion(True)
        code, result = self.lx("workspace", "delete", "--workspace", p["path"])
        self.assertEqual(code, 0, self.err)
        self.assertTrue(result["deleted"])
        self.assertTrue((Path(p["path"])/"samples").exists())

    def setUp(self):
        super().setUp()
        self.path = self.folder()
        write_synthetic(Path(self.path)/"samples", 6)
        for sample in (Path(self.path)/"samples").iterdir():
            (sample/"request.wav").write_bytes(_tone(1))

    def lx(self, *argv, answer=None):
        """Runs a command on this test's engine. `answer` is what a person types; None means no terminal."""
        out, err = io.StringIO(), io.StringIO()
        action = Orchestration(self.app, launch=False)
        with patch.object(cli, "Engine", return_value=self.app), patch.object(cli, "Orchestration", return_value=action), \
                patch("sys.stdin", Terminal(answer)), patch("builtins.input", return_value=answer or ""), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--root", str(self.app.root), *argv])
        self.err = err.getvalue()
        return code, json.loads(out.getvalue()) if out.getvalue() else None

    def here(self, *argv, **options):
        return self.lx(*argv, "--workspace", self.path, **options)

    def begin(self):
        self.assertEqual(self.here("init", "--template", "photo-request", "--goal", "Count things")[0], 0)

    def live(self):
        self.begin()
        self.assertEqual(self.here("spec", "draft", "--mode", "live")[0], 0)
        self.app.registry.update("spark-contributor", api_key="dummy-ui-key")


class Actions(Commands):
    def test_a_workspace_goes_from_samples_to_a_started_preview(self):
        self.begin()
        self.assertEqual(self.here("spec", "show")[1]["goal"], "Count things")
        self.assertIn("inventory", self.here("samples")[1])
        self.assertTrue(self.here("check", "test")[1]["samples"])
        self.assertEqual(self.here("preview")[1]["status"], "started")
        self.assertEqual(len(self.here("run", "status")[1]["runs"]), 1)

    def test_the_folder_you_are_in_is_the_default_workspace(self):
        self.begin()
        with contextlib.chdir(self.path):
            code, shown = self.lx("spec", "show")
        self.assertEqual((code, shown["goal"]), (0, "Count things"))

    def test_a_sample_reads_but_an_answer_does_not(self):
        self.begin()
        self.assertTrue(self.here("samples", "--read", "sample-01/request.txt")[1]["text"])
        code, _ = self.here("samples", "--read", "sample-01/truth.json")
        self.assertEqual(code, 1)
        self.assertIn("Answer files", self.err)

    def test_status_never_shows_a_key(self):
        self.app.registry.update("spark-contributor", api_key="dummy-ui-key")
        code, status = self.lx("status")
        self.assertEqual(code, 0)
        self.assertNotIn("dummy-ui-key", str(status))

    def test_a_refused_action_says_why_on_stderr(self):
        self.live()
        code, result = self.here("preview")
        self.assertEqual((code, result), (1, None))
        self.assertIn("only a person", self.err)


class PersonSteps(Commands):
    def test_a_live_run_stays_pending_until_a_person_approves(self):
        self.live()
        self.assertEqual(self.here("run", "start")[1]["status"], "pending")
        self.assertEqual(self.here("approve", answer="yes")[1]["by"], "person")
        self.assertEqual(self.here("run", "start")[1]["status"], "started")

    def test_no_terminal_means_no_approval(self):
        self.live()
        self.assertEqual(self.here("approve")[0], 1)
        self.assertIn("person at a terminal", self.err)
        self.assertIsNone(self.here("spec", "show")[1]["approved"])

    def test_an_agent_approves_without_a_terminal_and_the_record_says_so(self):
        self.live()
        self.assertEqual(self.here("approve", "--as-agent")[1]["status"], "approved")
        self.assertEqual(self.here("spec", "show")[1]["approved"], {"by": "agent"})
        self.assertEqual(self.here("run", "start")[1]["status"], "started")
        self.assertEqual(self.here("run", "status")[1]["runs"][0]["approved_by"], "agent")

    def test_no_terminal_means_no_grant(self):
        self.live()
        self.assertEqual(self.here("grant", "--cap", "2")[0], 1)
        self.assertIsNone(self.here("spec", "show")[1]["grant"])

    def test_anything_but_yes_changes_nothing(self):
        self.live()
        self.assertEqual(self.here("approve", answer="no")[0], 1)
        self.assertIsNone(self.here("spec", "show")[1]["approved"])

    def test_the_prompt_shows_what_is_accepted(self):
        self.live()
        self.here("approve", answer="no")
        self.assertIn("training", self.err)
        self.assertIn("your connected agent", self.err)
        self.assertIn("a blank answer scores", self.err)

    def test_a_grant_is_set_used_and_revoked(self):
        self.live()
        grant = self.here("grant", "--cap", "2", answer="yes")[1]["grant"]
        self.assertEqual((grant["cap_usd"], grant["runs"]), (2, 3))
        self.assertEqual(self.here("grant", "--revoke")[1]["grant"]["revoked"], True)
        self.assertEqual(self.here("run", "start")[1]["status"], "pending")

    def test_resume_needs_a_terminal(self):
        self.live()
        self.assertEqual(self.here("run", "resume", "abc")[0], 1)
        self.assertIn("person at a terminal", self.err)


if __name__ == "__main__":
    unittest.main()
