"""The forms a check can be written in, on the real sandbox where one is needed."""
from pathlib import Path
import unittest

from lx import check_forms, checks, sandbox, tasks

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
        self.assertEqual(sorted(line for line in lines if line.startswith(("import ", "from "))), ["import json", "import sys", "import traceback"])
