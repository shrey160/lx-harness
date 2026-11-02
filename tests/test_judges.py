"""Model judges: criteria a chat model answers about each answer, beside the check or in place of one."""
import json
from pathlib import Path
import unittest

from lx import judging
from test_orchestration import Actions

CRITERIA = {"criteria": [{"name": "counts the asked kind", "question": "Does the answer count only the kind of thing the request names?"}]}


class Criteria(unittest.TestCase):
    def test_criteria_are_cleaned_and_checked(self):
        self.assertEqual(judging.check_written({"criteria": [{"name": " a ", "question": " Is it? "}]}, text_task=False),
                         {"criteria": [{"name": "a", "question": "Is it?"}], "sees_request": False})
        for judge, message in (({}, "1 to 50"), ({"criteria": []}, "1 to 50"), ({"criteria": [{"name": "a"}]}, "name and a question"),
                               ({"criteria": [{"name": "", "question": "q"}]}, "name of 1"), ({"criteria": [{"name": "a", "question": "x"*1001}]}, "question of 1"),
                               ({"criteria": [{"name": "a", "question": "q"}]*2}, "own name"), ({**CRITERIA, "weights": [1]}, "an object with criteria"),
                               ({**CRITERIA, "sees_request": "yes"}, "true or false"), ([], "an object")):
            with self.assertRaisesRegex(ValueError, message):
                judging.check_written(judge, text_task=True)

    def test_only_a_text_task_judge_sees_the_request(self):
        self.assertTrue(judging.check_written({**CRITERIA, "sees_request": True}, text_task=True)["sees_request"])
        with self.assertRaisesRegex(ValueError, "text task"):
            judging.check_written({**CRITERIA, "sees_request": True}, text_task=False)

    def test_a_file_that_is_not_json_says_so(self):
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            judging.read_written("{", text_task=False)


class InTheSpec(Actions):
    def write_judges(self, value):
        folder = Path(self.path)/"eval"
        folder.mkdir(exist_ok=True)
        (folder/"judges.json").write_text(json.dumps(value), encoding="utf-8")

    def test_judges_are_kept_until_changed_or_removed(self):
        self.begin()
        self.lx.spec_draft(self.path, judges=CRITERIA)
        expected = {**CRITERIA, "sees_request": False}
        self.assertEqual(self.lx.spec_show(self.path)["judges"], expected)
        self.lx.spec_draft(self.path, goal="Count what the request asks for.")
        self.assertEqual(self.lx.spec_show(self.path)["judges"], expected)
        self.assertIsNotNone(self.lx.spec_show(self.path)["check"])
        self.lx.spec_draft(self.path, no_judges=True)
        self.assertIsNone(self.lx.spec_show(self.path)["judges"])
        with self.assertRaisesRegex(ValueError, "not both"):
            self.lx.spec_draft(self.path, judges=CRITERIA, no_judges=True)

    def test_judges_can_stand_in_for_a_check(self):
        self.begin()
        self.lx.spec_draft(self.path, no_check=True, judges=CRITERIA)
        shown = self.lx.spec_show(self.path)
        self.assertEqual((shown["check"], shown["judges"]["criteria"]), (None, CRITERIA["criteria"]))

    def test_a_photo_task_judge_cannot_see_the_request(self):
        self.begin()
        with self.assertRaisesRegex(ValueError, "text task"):
            self.lx.spec_draft(self.path, judges={**CRITERIA, "sees_request": True})

    def test_the_judges_file_is_taken_and_drift_blocks_until_settled(self):
        self.begin()
        self.write_judges(CRITERIA)
        self.assertEqual(self.lx.spec_show(self.path)["eval_drift"], ["eval/judges.json is not in use, because this spec has no judges"])
        self.lx.spec_draft(self.path, use_judge_file=True)
        self.assertEqual(self.lx.spec_show(self.path)["eval_drift"], [])
        self.write_judges({"criteria": [{"name": "other", "question": "Is it tidy?"}]})
        self.assertEqual(self.lx.spec_show(self.path)["eval_drift"], ["eval/judges.json differs from the spec's judges"])
        self.assertIn("judges.json differs", str(self.lx.ready(self.path)["problems"]))
        with self.assertRaisesRegex(ValueError, "not both"):
            self.lx.spec_draft(self.path, judges=CRITERIA, use_judge_file=True)

    def test_a_missing_judges_file_is_named(self):
        self.begin()
        with self.assertRaisesRegex(ValueError, "eval/judges.json"):
            self.lx.spec_draft(self.path, use_judge_file=True)

    def test_the_judges_file_is_not_a_sample(self):
        self.begin()
        self.write_judges(CRITERIA)
        self.assertNotIn("eval/judges.json", {item["path"] for item in self.lx.samples(self.path)["inventory"]})


class ModelAndCap(Actions):
    def test_a_judge_is_blocked_until_it_has_a_model_and_a_cap(self):
        project = self.begin()
        self.lx.spec_draft(self.path, judges=CRITERIA)
        self.assertEqual(self.app.project(project["id"])["blocked"], "Choose a model for the judges.")
        self.app.edit_spec(project["id"], {"judge": {"entry": "spark-contributor"}})
        self.assertEqual(self.app.project(project["id"])["blocked"], "Set the most the judges may spend on one run.")
        self.app.edit_spec(project["id"], {"judge": {"cap_usd": 0.05}})
        project = self.app.project(project["id"])
        self.assertIsNone(project["blocked"])
        judge = project["draft"]["judge"]
        self.assertEqual((judge["entry"], judge["cap_usd"], judge["provider"]["id"]), ("spark-contributor", 0.05, "spark-contributor"))
        self.assertEqual(judge["criteria"], CRITERIA["criteria"])

    def test_the_cap_and_fields_are_checked(self):
        project = self.begin()
        for given, message in (({**CRITERIA, "cap_usd": 0}, "more than"), ({**CRITERIA, "cap_usd": True}, "more than"),
                               ({**CRITERIA, "cap_usd": 21}, "more than"), ({**CRITERIA, "model": "x"}, "take criteria"),
                               ({**CRITERIA, "entry": "no-such-entry"}, "chat provider")):
            with self.assertRaisesRegex(ValueError, message):
                self.app.edit_spec(project["id"], {"judge": given})

    def test_the_judge_is_named_in_the_data_notice(self):
        project = self.begin()
        self.app.edit_spec(project["id"], {"judge": {**CRITERIA, "entry": "spark-contributor", "cap_usd": 0.05}})
        entry = next(item for item in self.app.project(project["id"])["notice"] if item["who"] == "judge")
        self.assertIn("expected answer", entry["gets"])
        self.assertNotIn("request", entry["gets"])

    def test_a_changed_judge_provider_needs_a_new_approval(self):
        project = self.begin()
        self.app.edit_spec(project["id"], {"judge": {**CRITERIA, "entry": "spark-contributor", "cap_usd": 0.05}})
        draft = self.app.project(project["id"])["draft"]
        self.app._check_providers(draft)
        moved = {**draft, "judge": {**draft["judge"], "provider": {**draft["judge"]["provider"], "base_url": "https://elsewhere.example/v1"}}}
        with self.assertRaisesRegex(ValueError, "approve again"):
            self.app._check_providers(moved)


if __name__ == "__main__":
    unittest.main()
