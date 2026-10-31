"""The built-in orchestrator (D-024): it remembers, drafts through the shared actions, and never holds the answers."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from lx import sandbox, tasks
from lx.inventory import write_synthetic
from lx.live_transport import validate_body
from lx.orchestration import Orchestration
from lx.orchestrator import PROMPT, TOOLS, converse
from lx.spike import wire_reply
from test_tasks import Base


class Scripted:
    """A chat client's transport: plays `replies` and keeps what it was sent."""

    def __init__(self, replies):
        self.replies, self.bodies = list(replies), []

    def session(self, history, handle, tools, **options):
        self.bodies.append(history)
        self.results = [handle(name, arguments) for name, arguments in self.replies]
        return "done", 0

    def chat(self, history, **options):
        self.bodies.append(history)
        return {"content": "plain"}, {}


class Tools(Base):
    def setUp(self):
        super().setUp()
        self.path = self.folder()
        write_synthetic(Path(self.path)/"samples", 6)
        self.lx = Orchestration(self.app, external=False, launch=False)
        self.lx.init(self.path, "photo-request")
        self.project = self.app.open_workspace(self.path)

    def calling(self, *replies):
        client = Scripted(replies)
        self.assertEqual(converse(self.lx, self.path, client, self.project, "hello"), "done")
        return client

    def truths(self):
        return self.lx._truths(self.project["id"])


class TheAnswersStayAway(Tools):
    def test_answer_files_cannot_be_read(self):
        path = next(item["path"] for item in self.project["inventory"]["inventory"] if item["path"].endswith("truth.json"))
        with self.assertRaisesRegex(ValueError, "Answer files"):
            self.lx.read_sample(self.path, path)

    def test_only_listed_files_can_be_read(self):
        for path in ("../../secret.txt", "sample-01/nothing.txt"):
            with self.subTest(path), self.assertRaisesRegex(ValueError, "not in the sample list"):
                self.lx.read_sample(self.path, path)

    def test_a_listed_text_file_reads_and_a_photo_does_not(self):
        photo = next(item["path"] for item in self.project["inventory"]["inventory"] if item["kind"] == "image")
        with self.assertRaisesRegex(ValueError, "text files"):
            self.lx.read_sample(self.path, photo)
        text = next(item["path"] for item in self.project["inventory"]["inventory"] if item["path"].endswith("request.txt"))
        self.assertTrue(self.lx.read_sample(self.path, text)["text"])

    def test_the_shape_holds_field_names_and_types_only(self):
        truths = {"a": {"label": "red bicycle", "n": 3, "items": [{"ok": True}]}, "b": {"label": "blue kettle", "n": 7, "items": []}}
        shape = tasks.truth_shape(truths)
        self.assertEqual(shape["shape"], {"label": "text", "n": "number", "items": [{"ok": "boolean"}]})
        self.assertFalse(shape["same_shape"])
        self.assertNotIn("bicycle", json.dumps(shape))

    def test_a_task_that_repeats_answers_is_refused(self):
        truths = {"a": {"label": "a very long answer"}, "b": {"label": "pear"}, "c": {"label": "plum"}, "d": {"label": "kiwi"}}
        self.assertTrue(tasks.quotes_answers("Say A Very Long Answer here", truths))
        self.assertTrue(tasks.quotes_answers("pear, plum and kiwi", truths))
        self.assertFalse(tasks.quotes_answers("name the pear", truths))
        self.assertFalse(tasks.quotes_answers("kiwifruit and pearl", truths))
        with patch.object(tasks, "quotes_answers", return_value=True), self.assertRaisesRegex(ValueError, "repeats answers"):
            self.lx.spec_draft(self.path, task={"base_prompt": "anything"})


class TheConversation(Tools):
    def test_a_reply_builds_on_what_was_said(self):
        history = [{"role": "user", "content": "count cups"}, {"role": "assistant", "content": "which photos?"}]
        client = Scripted([])
        converse(self.lx, self.path, client, {**self.project, "messages": history}, "all of them")
        sent = client.bodies[0]
        self.assertEqual(sent[0]["content"], PROMPT)
        self.assertEqual(sent[1:3], history)
        self.assertEqual(json.loads(sent[3]["content"])["message"], "all of them")

    def test_a_tool_error_reaches_it_and_ends_nothing(self):
        client = self.calling(("spec_draft", {"task": {"skills": "segment first"}}), ("no_such_tool", {}), ("spec_show", {"bad": 1}))
        self.assertIn("template fields", client.results[0]["error"])
        self.assertEqual(client.results[1], {"error": "unknown tool"})
        self.assertIn("error", client.results[2])

    def test_it_cannot_reach_what_a_person_holds(self):
        names = {tool["function"]["name"] for tool in TOOLS}
        self.assertEqual(names, {"samples", "read_sample", "truth_shape", "spec_show", "spec_draft", "check_test"})
        client = self.calling(("spec_draft", {"mode": "live"}), ("spec_draft", {"worker": "x"}), ("preview", {}), ("run_start", {}))
        self.assertTrue(all("error" in result for result in client.results))
        self.assertEqual(self.lx.spec_show(self.path)["mode"], "controlled_preview")

    def test_it_stays_the_built_in_orchestrator(self):
        self.calling(("spec_draft", {"goal": "Count cups"}))
        self.assertEqual(self.lx.spec_show(self.path)["roles"]["orchestrator"], "spark-contributor")

    def test_a_provider_without_tools_still_talks(self):
        client = Scripted([])
        self.assertEqual(converse(self.lx, self.path, client, self.project, "hello", tools=False), "plain")

    def test_the_live_contract_takes_these_tools_and_no_others(self):
        body = {"model": "m", "messages": [{"role": "user", "content": "x"}], "tools": TOOLS}
        self.assertIs(validate_body(body, model="m", tool_sets=(TOOLS,)), body)
        with self.assertRaisesRegex(Exception, "tool contract"):
            validate_body({**body, "tools": TOOLS[:1]}, model="m", tool_sets=(TOOLS,))

    def test_the_prompt_keeps_procedure_with_the_loop(self):
        self.assertIn("must not say how to do the work", PROMPT)
        self.assertIn("cannot approve or start a run", PROMPT)


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class APreviewConversation(Tools):
    def test_it_produces_a_valid_spec_and_check_for_the_inventory_samples(self):
        before = self.project["draft"]["version"]
        project = self.app.discuss(self.project["id"], "Please draft this task", True)
        self.assertEqual(project["draft"]["version"], before+1)
        self.assertEqual(self.lx.check_test(self.path)["samples"], 6)
        self.assertEqual(project["messages"][-2]["content"], "Please draft this task")
        self.assertIn("check passed its self-test", project["messages"][-1]["content"])
        self.assertIsNone(project["approved"])
        self.assertTrue(self.approve(project)["approved"])

    def test_a_plain_question_changes_nothing(self):
        project = self.app.discuss(self.project["id"], "What can you do?", True)
        self.assertEqual(project["draft"]["version"], self.project["draft"]["version"])
        self.assertIn("nothing was sent", project["messages"][-1]["content"])


if __name__ == "__main__":
    unittest.main()
