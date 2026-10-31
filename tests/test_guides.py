"""What an orchestrator can read about LX: guides by topic, and what this install offers."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from lx import cli, guides, tasks
from lx.orchestration import ACTIONS, Orchestration
from test_tasks import Base


class Guides(unittest.TestCase):
    def test_every_guide_exists_and_reads(self):
        for entry in guides.guide_list():
            self.assertGreater(len(guides.read_guide(entry["topic"])["text"]), 1000, entry["topic"])

    def test_only_a_listed_topic_reads(self):
        for topic in ("../README.md", "history", "environment.md", "", None, "..\\.env"):
            with self.subTest(topic), self.assertRaisesRegex(ValueError, "Choose a guide"):
                guides.read_guide(topic)

    def test_a_missing_file_says_so(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ValueError, "not in this install"):
            guides.read_guide("tasks", Path(folder))

    def test_the_guides_name_only_commands_that_exist(self):
        for name in ("guide", "environment", "ready", "trial_start"):
            self.assertIn(name, ACTIONS)
        self.assertIn("`lx guide environment`", guides.read_guide("tasks")["text"])


class Environment(Base):
    def setUp(self):
        super().setUp()
        self.lx = Orchestration(self.app, launch=False)

    def run_cli(self, *arguments):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--root", str(self.app.root), *arguments])
        return code, out.getvalue()

    def test_it_lists_each_tool_with_what_it_does(self):
        found = {tool["id"]: tool for tool in self.lx.environment()["model_tools"]}
        self.assertEqual(set(found), set(tasks.MODEL_TOOLS))
        self.assertTrue(found[tasks.TRANSCRIPTION]["required_on_photo_tasks"])
        self.assertFalse(found[tasks.SEGMENTATION]["required_on_photo_tasks"])
        self.assertIn("segmentation", found[tasks.SEGMENTATION]["does"][0].lower())

    def test_it_shows_a_tool_a_person_turned_off(self):
        self.app.registry.set_enabled(tasks.SEGMENTATION, False)
        found = {tool["id"]: tool for tool in self.lx.environment()["model_tools"]}
        self.assertFalse(found[tasks.SEGMENTATION]["enabled"])
        self.assertNotIn("segment", {tool["name"] for tool in self.lx.environment()["agent_tools"]})

    def test_it_states_the_sandbox_limits_and_no_network(self):
        shown = self.lx.environment()
        self.assertIs(shown["sandbox"]["network"], False)
        self.assertEqual(shown["sandbox"]["script_seconds"], 20)
        self.assertIn("reply_tokens", {field["name"] for field in shown["limits"]})
        self.assertEqual({entry["topic"] for entry in shown["guides"]}, set(guides.GUIDES))

    def test_it_shows_no_key_or_private_path(self):
        self.app.registry.update("spark-contributor", api_key="dummy-ui-key")
        text = json.dumps(self.lx.environment())
        self.assertNotIn("dummy-ui-key", text)
        self.assertNotIn(str(self.app.root), text)

    def test_the_command_line_reads_both(self):
        code, listing = self.run_cli("guide")
        self.assertEqual((code, {entry["topic"] for entry in json.loads(listing)["guides"]}), (0, set(guides.GUIDES)))
        code, text = self.run_cli("guide", "tasks")
        self.assertEqual((code, json.loads(text)["topic"]), (0, "tasks"))
        code, shown = self.run_cli("environment")
        self.assertEqual(code, 0)
        self.assertIn("sandbox", json.loads(shown))

    def test_an_unknown_topic_fails_with_its_reason(self):
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            code, _ = self.run_cli("guide", "secrets")
        self.assertEqual(code, 1)
        self.assertIn("Choose a guide", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
