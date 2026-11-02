"""The forms a check can be written in, on the real sandbox where one is needed."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from lx import check_forms, checks, intake, sandbox, tasks, workspace

FUNCTION = '''def score(truth, answer):
    return {"value": answer["value"] == truth["value"], "note": "note" in answer}


def example(truth):
    return {**truth, "note": "seen"}
'''
PLAIN = "def score(truth, answer):\n    return answer == truth\n"
TRUTHS = {"a": {"value": "first"}, "b": {"value": "second"}}


class SavedForm(unittest.TestCase):
    def test_a_function_gets_the_kit_and_the_author_sees_only_their_text(self):
        saved = check_forms.saved_form(FUNCTION)
        self.assertIn(check_forms.MARKER, saved)
        self.assertEqual(check_forms.author_text(saved), FUNCTION)

    def test_a_saved_check_is_not_wrapped_again(self):
        saved = check_forms.saved_form(FUNCTION)
        self.assertEqual(check_forms.saved_form(saved), saved)

    def test_a_full_check_stays_exactly_as_it_is(self):
        for source in (tasks.inventory_check_source(), "import sys\ndef score(a):\n    return sys.stdin.read()\n", "raise SystemExit(3)"):
            self.assertEqual(check_forms.saved_form(source), source)
            self.assertEqual(check_forms.author_text(source), source)

    def test_a_syntax_error_names_its_line(self):
        with self.assertRaisesRegex(ValueError, "syntax error on line 2"):
            check_forms.saved_form("def score(truth, answer):\n    return (\n")

    def test_a_check_too_long_with_the_kit_says_how_long_it_can_be(self):
        with self.assertRaisesRegex(ValueError, "at most 1[0-9]{4} characters"):
            tasks.validate_check_source(PLAIN+"#"*(tasks.MAX_CHECK-len(PLAIN)-1))


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class FunctionForm(unittest.TestCase):
    def test_a_function_with_an_example_passes_the_self_test(self):
        summary = checks.self_test(check_forms.saved_form(FUNCTION), TRUTHS)
        self.assertEqual(summary["checks_per_sample"], 2)

    def test_the_correct_answer_defaults_to_the_truth(self):
        self.assertEqual(checks.examples(check_forms.saved_form(PLAIN), [{"a": 1}]), [{"a": 1}])

    def test_a_named_result_scores_as_the_fraction_of_its_checks(self):
        result = checks.run_check(check_forms.saved_form(FUNCTION), TRUTHS["a"], {"value": "first"})
        self.assertEqual((result.score, result.errors), (0.5, ("note: failed",)))

    def test_an_answer_the_function_cannot_read_scores_zero_and_names_the_line(self):
        result = checks.run_check(check_forms.saved_form(FUNCTION), TRUTHS["a"], None)
        self.assertEqual(result.score, 0)
        self.assertIn("TypeError on line 2", result.errors[0])

    def test_what_comes_back_is_read_as_pass_or_fail(self):
        source = check_forms.saved_form("def score(truth, answer):\n    return answer and answer == truth\n")
        self.assertEqual(checks.run_check(source, {"a": 1}, {}).score, 0)
        self.assertEqual(checks.run_check(source, {"a": 1}, {"a": 1}).score, 1)

    def test_a_number_is_refused_with_a_reason(self):
        with self.assertRaisesRegex(checks.CheckError, "must say pass or fail"):
            checks.run_check(check_forms.saved_form("def score(truth, answer):\n    return 0.5\n"), {}, {})

    def test_a_correct_answer_that_fails_says_which_check(self):
        source = check_forms.saved_form('def score(truth, answer):\n    return {"always": False}\n')
        with self.assertRaisesRegex(checks.CheckError, r"\(always: failed\)"):
            checks.self_test(source, TRUTHS)

    def test_the_kit_needs_nothing_beyond_the_standard_library(self):
        lines = (Path(check_forms.__file__).parent/"check_kit.py").read_text(encoding="utf-8").splitlines()
        self.assertEqual(sorted(line for line in lines if line.startswith(("import ", "from "))), ["import json", "import re", "import sys", "import traceback"])


RECORD_FIELDS = ("order_id", "customer", "date", "currency", "amount_minor", "phone", "tags")
RECORD_RULES = json.dumps({"rules": [{"field": field, "rule": "equals"} for field in RECORD_FIELDS], "extra_fields": False})
RECORD = {"order_id": "A-1", "customer": "Ada Byron", "date": "2026-01-02", "currency": "EUR", "amount_minor": 1200, "phone": None, "tags": ["a", "b"]}


class RulesAreRead(unittest.TestCase):
    def test_starting_rules_name_each_field_and_no_value(self):
        text = check_forms.starting_rules({"order_id": "text", "total": "number"})
        self.assertEqual([rule["field"] for rule in check_forms.check_rules(text)["rules"]], ["order_id", "total"])

    def test_answers_without_fields_get_no_starting_rules(self):
        with self.assertRaisesRegex(ValueError, "def score"):
            check_forms.starting_rules(["text"])

    def test_rules_come_back_exactly_as_written(self):
        text = '{"rules": [\n  {"field": "total", "rule": "equals"}\n]}\n'
        self.assertEqual(check_forms.author_text(check_forms.saved_form(text)), text)

    def test_mistakes_say_what_to_fix(self):
        cases = {
            "not valid JSON": '{"rules": [',
            "an object with rules": '{"rule": []}',
            "Give 1 to": '{"rules": []}',
            "Rule 1 needs a field": '{"rules": [{"rule": "equals"}]}',
            "Rule 2 needs a rule, one of": '{"rules": [{"field": "a", "rule": "equals"}, {"field": "b", "rule": "same"}]}',
            "equals takes only field and rule": '{"rules": [{"field": "a", "rule": "equals", "values": []}]}',
            "one_of takes only field, rule and values": '{"rules": [{"field": "a", "rule": "one_of"}]}',
            "values must be a list": '{"rules": [{"field": "a", "rule": "one_of", "values": []}]}',
            "valid regular expression": '{"rules": [{"field": "a", "rule": "matches", "pattern": "("}]}',
            "within must be a number": '{"rules": [{"field": "a", "rule": "near", "within": -1}]}',
            "only once": '{"rules": [{"field": "a", "rule": "equals"}, {"field": "a", "rule": "equals"}]}',
        }
        for message, text in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                check_forms.saved_form(text)


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class RulesForm(unittest.TestCase):
    def score(self, rules, truth, answer):
        return checks.run_check(check_forms.saved_form(json.dumps({"rules": rules})), truth, answer)

    def test_each_rule_decides_as_it_says(self):
        cases = [
            ({"field": "a", "rule": "equals"}, {"a": 1}, {"a": 1}, 1),
            ({"field": "a", "rule": "equals"}, {"a": 1}, {"a": True}, 0),
            ({"field": "a", "rule": "equals"}, {"a": 1}, {}, 0),
            ({"field": "a.b", "rule": "equals"}, {"a": {"b": 2}}, {"a": {"b": 2}}, 1),
            ({"field": "a.1", "rule": "equals"}, {"a": [1, 2]}, {"a": [0, 2]}, 1),
            ({"field": "a", "rule": "one_of", "values": ["x", "y"]}, {}, {"a": "y"}, 1),
            ({"field": "a", "rule": "one_of", "values": ["x", "y"]}, {}, {"a": "z"}, 0),
            ({"field": "a", "rule": "matches", "pattern": "[0-9]+"}, {}, {"a": "12"}, 1),
            ({"field": "a", "rule": "matches", "pattern": "[0-9]+"}, {}, {"a": "12x"}, 0),
            ({"field": "a", "rule": "matches", "pattern": "[0-9]+"}, {}, {"a": 12}, 0),
            ({"field": "a", "rule": "near", "within": 0.5}, {"a": 10}, {"a": 10.4}, 1),
            ({"field": "a", "rule": "near", "within": 0.5}, {"a": 10}, {"a": 11}, 0),
            ({"field": "a", "rule": "near", "within": 0.5}, {"a": 10}, {"a": "10"}, 0),
            ({"field": "a", "rule": "same_items"}, {"a": [1, {"b": 2}]}, {"a": [{"b": 2}, 1]}, 1),
            ({"field": "a", "rule": "same_items"}, {"a": [1, 1]}, {"a": [1]}, 0),
        ]
        for rule, truth, answer, expected in cases:
            with self.subTest(rule=rule, answer=answer):
                self.assertEqual(self.score([rule], truth, answer).score, expected)

    def test_two_rules_on_a_field_get_separate_names(self):
        rules = [{"field": "a", "rule": "equals"}, {"field": "a", "rule": "matches", "pattern": "[a-z]+"}]
        self.assertEqual(self.score(rules, {"a": "x"}, {"a": "Y"}).errors, ("a equals: does not match", "a matches: wrong format"))

    def test_a_field_missing_from_the_truth_is_said_plainly(self):
        self.assertEqual(self.score([{"field": "a", "rule": "equals"}], {}, {"a": 1}).errors, ("a: the expected answer has no such field",))

    def test_errors_never_carry_a_value(self):
        result = self.score([{"field": "a", "rule": "equals"}], {"a": "secretvalue"}, {"a": "othervalue"})
        self.assertEqual(result.errors, ("a: does not match",))

    def test_the_self_test_passes_with_the_truth_as_the_correct_answer(self):
        truths = {"one": RECORD, "two": {**RECORD, "order_id": "B-2", "amount_minor": 99}}
        self.assertEqual(checks.self_test(check_forms.saved_form(RECORD_RULES), truths)["checks_per_sample"], 8)

    def test_rules_score_as_the_records_check_does(self):
        full = (Path(check_forms.__file__).parent/"records_check.py").read_text(encoding="utf-8")
        answers = [RECORD, {**RECORD, "amount_minor": 1}, {**RECORD, "extra": 1}, {k: v for k, v in RECORD.items() if k != "tags"},
                   *checks.JUNK_ANSWERS, {**RECORD, "phone": "+15550100"}, {**RECORD, "tags": ["b", "a"]}, {**RECORD, "date": "02/01/2026"}]
        pairs = [(RECORD, answer) for answer in answers]
        rules = checks.run_checks(check_forms.saved_form(RECORD_RULES), pairs)
        self.assertEqual([(r.score, r.n_passed, r.n_criteria) for r in rules], [(r.score, r.n_passed, r.n_criteria) for r in checks.run_checks(full, pairs)])


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class ShownChecks(unittest.TestCase):
    def test_a_name_that_reveals_the_truth_is_withheld(self):
        source = check_forms.saved_form('def score(truth, answer):\n    return {truth["value"]: True, "plain": False}\n')
        self.assertEqual(checks.shown_checks(source, [(TRUTHS["a"], {})]), [[{"name": "check 1", "passed": True}, {"name": "plain", "passed": False}]])


class EvalFolderIsNotSamples(unittest.TestCase):
    def test_a_samples_folder_at_the_workspace_root_leaves_eval_out(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"sample-01").mkdir()
            (root/"sample-01"/"truth.json").write_text("{}", encoding="utf-8")
            (root/"eval").mkdir()
            (root/"eval"/"rules.json").write_text("{}", encoding="utf-8")
            listed = intake.inspect_folder(root, skip_root=workspace.NOT_SAMPLES)
            copied = intake.snapshot_folder(root, root.parent/(root.name+"-copy"), skip_root=workspace.NOT_SAMPLES)
            self.addCleanup(shutil.rmtree, root.parent/(root.name+"-copy"), True)
            for result in (listed, copied):
                self.assertEqual([item["path"] for item in result["inventory"]], ["sample-01/truth.json"])
