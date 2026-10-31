"""The check protocol, on the real sandbox; these skip only where no sandbox exists."""
import json
from pathlib import Path
import unittest

from lx import checks, sandbox
from lx.inventory import synthetic_sample, verify_inventory

SOURCE = (Path(checks.__file__).parent/"inventory_check.py").read_text(encoding="utf-8")
GOOD = '''
import json, sys
request = json.load(sys.stdin)
if "examples" in request:
    print(json.dumps({"answers": [{"value": t["value"]} for t in request["examples"]]}))
else:
    print(json.dumps({"results": [{"checks": [
        {"name": "shape", "passed": isinstance(i["answer"], dict), "error": None if isinstance(i["answer"], dict) else "shape: wrong"},
        {"name": "value", "passed": isinstance(i["answer"], dict) and i["answer"].get("value") == i["truth"]["value"],
         "error": "value: wrong"}]} for i in request["items"]]}))
'''
TRUTHS = {"a": {"value": "first"}, "b": {"value": "second"}}


def truth_of(index):
    return synthetic_sample(20261029, index)[1]


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class CheckProtocolTests(unittest.TestCase):
    def test_a_sound_check_passes_its_self_test(self):
        summary = checks.self_test(GOOD, TRUTHS)
        self.assertEqual(summary["samples"], 2)
        self.assertEqual(summary["checks_per_sample"], 2)

    def test_a_sound_check_says_how_blank_and_wrong_answers_score(self):
        summary = checks.self_test(GOOD, TRUTHS)
        self.assertEqual((summary["blank_score"], summary["wrong_score"], summary["weaknesses"]), (0.5, 0.5, []))

    def test_free_marks_for_a_blank_answer_are_a_weakness(self):
        filler = '{"name": "ok%d", "passed": True, "error": None},'
        source = GOOD.replace('"checks": [', '"checks": ['+"".join(filler % n for n in range(3)), 1)
        summary = checks.self_test(source, TRUTHS)
        self.assertEqual(summary["blank_score"], 0.8)
        self.assertEqual(len(summary["weaknesses"]), 1)
        self.assertIn("blank answer scores 0.80", summary["weaknesses"][0])

    def test_a_check_that_barely_tells_right_from_wrong_is_a_weakness(self):
        filler = '{"name": "has%d", "passed": isinstance(i["answer"], dict) and "value" in i["answer"], "error": "shape: wrong"},'
        source = GOOD.replace('"checks": [', '"checks": ['+"".join(filler % n for n in range(8)), 1).replace(
            '"passed": isinstance(i["answer"], dict), "error"', '"passed": isinstance(i["answer"], dict) and "value" in i["answer"], "error"')
        summary = checks.self_test(source, TRUTHS)
        self.assertEqual((summary["blank_score"], summary["wrong_score"]), (0, 0.9))
        self.assertIn("barely tells right from wrong", summary["weaknesses"][0])

    def test_samples_that_all_expect_the_same_answer_say_so(self):
        summary = checks.self_test(GOOD, {"a": {"value": "same"}, "b": {"value": "same"}})
        self.assertIsNone(summary["wrong_score"])
        self.assertIn("No wrong answer could be tried", summary["weaknesses"][0])

    def test_a_crash_that_prints_an_expected_value_is_withheld(self):
        source = 'import json,sys\nraise ValueError(str(json.load(sys.stdin)))'
        with self.assertRaisesRegex(checks.CheckError, "withheld") as raised:
            checks.self_test(source, TRUTHS)
        self.assertNotIn("first", str(raised.exception))

    def test_the_inventory_check_is_not_flagged(self):
        truths = {str(index): truth_of(index) for index in range(6)}
        summary = checks.self_test(SOURCE, truths)
        self.assertLess(summary["blank_score"], checks.BLANK_LIMIT)
        self.assertLess(summary["wrong_score"], checks.WRONG_LIMIT)
        self.assertEqual(summary["weaknesses"], [])

    def test_the_score_is_the_fraction_of_named_checks(self):
        result = checks.run_check(GOOD, {"value": "x"}, {"value": "y"})
        self.assertEqual((result.score, result.n_passed, result.n_criteria, result.accepted), (0.5, 1, 2, False))
        self.assertEqual(result.errors, ("value: wrong",))

    def test_broken_checks_are_refused(self):
        cases = {
            "crashed": "raise SystemExit(3)",
            "one JSON object": "print('hello')",
            "correct answer scored": GOOD.replace('i["truth"]["value"]', '"nothing"'),
            "full marks": GOOD.replace('isinstance(i["answer"], dict) and i["answer"].get("value") == i["truth"]["value"]', "True")
                              .replace('"passed": isinstance(i["answer"], dict),', '"passed": True,'),
            "cannot tell them apart": GOOD.replace('i["answer"].get("value") == i["truth"]["value"]', 'isinstance(i["answer"].get("value"), str)'),
            "different results": GOOD.replace('"error": "value: wrong"', '"error": "value: wrong" + str(__import__("os").urandom(4))'),
            "reveals an expected value": GOOD.replace('"error": "value: wrong"', '"error": "wanted " + str(i["truth"]["value"])'),
            "named checks": GOOD.replace('"checks": [', '"checks": [][:0] and [', 1),
        }
        for message, source in cases.items():
            with self.subTest(message), self.assertRaisesRegex(checks.CheckError, message):
                checks.self_test(source, TRUTHS)

    def test_a_check_cannot_reach_the_network_or_outlive_its_limit(self):
        source = ('import json,socket,sys\ntry:\n    socket.create_connection(("1.1.1.1",53),2); got="network"\n'
                  'except OSError:\n    got="blocked"\nprint(json.dumps({"results":[{"checks":[{"name":got,"passed":True,"error":None}]}]}))')
        output = sandbox.run_json(source, {"items": [{}]}, timeout=20)
        self.assertEqual(output["output"]["results"][0]["checks"][0]["name"], "blocked")

    def test_the_inventory_check_file_passes_and_matches_the_built_in_score(self):
        truths = {f"s{i}": truth_of(i) for i in range(1, 7)}
        summary = checks.self_test(SOURCE, truths)
        self.assertEqual(summary["samples"], 6)
        answers = checks.examples(SOURCE, list(truths.values()))
        pairs = []
        for (name, truth), answer in zip(truths.items(), answers):
            wrong_total = dict(answer, answer=7)
            pairs += [(truth, answer), (truth, wrong_total), (truth, {"answer": 1}), (truth, None),
                      (truth, dict(answer, photos=answer["photos"][:-1]))]
        for (truth, answer), result in zip(pairs, checks.run_checks(SOURCE, pairs)):
            self.assertEqual(result, verify_inventory(truth, answer))


class CheckRefusalTests(unittest.TestCase):
    def test_no_sandbox_means_no_check(self):
        from unittest.mock import patch
        with patch("lx.sandbox.available", return_value=False), patch("lx.sandbox.run_json") as run:
            with self.assertRaisesRegex(checks.CheckError, "only in the script sandbox"):
                checks.run_check(GOOD, {"value": "x"}, {})
        run.assert_not_called()

    def test_the_inventory_check_needs_nothing_beyond_the_standard_library(self):
        imports = [line for line in SOURCE.splitlines() if line.startswith(("import ", "from "))]
        self.assertEqual(sorted(imports), ["import json", "import sys"])
        self.assertLess(len(SOURCE), sandbox.MAX_SOURCE)
        self.assertTrue(json.dumps(imports))


if __name__ == "__main__":
    unittest.main()
