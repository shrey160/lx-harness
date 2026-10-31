"""The receiving audit's check and task, on five truths shaped like the planned samples."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

from lx import checks, sandbox, tasks

FOLDER = Path(__file__).parent.parent/"scripts"/"receiving_audit"
SOURCE = (FOLDER/"check.py").read_text(encoding="utf-8")
TASK = json.loads((FOLDER/"task.json").read_text(encoding="utf-8"))
spec = importlib.util.spec_from_file_location("receiving_audit_check", FOLDER/"check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def row(photo, count, *, top=20):
    return [{"photo": photo, "box": [10+index*70, top, 69+index*70, top+79]} for index in range(count)]


def line(item, ordered, received, credit, boxes, substitute=None):
    return {"item": item, "ordered": ordered, "received": received, "substitute": substitute, "credit": credit, "boxes": boxes}


def truth(*lines):
    return {"lines": list(lines), "total_claim": round(sum(entry["credit"] for entry in lines), 2)}


TRUTHS = {
    "order-changed": truth(line("cup", 8, 6, 5.0, row(2, 6)), line("book", 3, 3, 0, row(3, 3)), line("clock", 2, 2, 0, row(3, 2, top=120))),
    "slip-overclaims": truth(line("backpack", 5, 3, 40.0, row(2, 3)), line("apple", 4, 4, 0, row(3, 4)), line("mouse", 2, 2, 0, row(4, 2))),
    "substitution": truth(line("bottle", 6, 0, 18.0, row(2, 6), {"item": "vase", "count": 6}), line("bowl", 4, 4, 0, row(3, 4))),
    "retake": truth(line("book", 7, 5, 12.5, row(3, 3)+row(4, 2)), line("clock", 1, 1, 0, row(4, 1, top=120))),
    "price-rule": truth(line("cup", 20, 19, 0, row(2, 7)+row(2, 7, top=120)+row(3, 5)), line("bowl", 4, 2, 7.5, row(4, 2))),
}


def answer_for(name):
    return copy.deepcopy(check.example_answer(TRUTHS[name]))


def failed(name, answer):
    return [check_name for check_name, passed, _ in check.chains(TRUTHS[name], answer) if not passed]


class ChainTests(unittest.TestCase):
    def test_a_correct_answer_passes_every_chain(self):
        for name in TRUTHS:
            self.assertEqual(failed(name, answer_for(name)), [], name)

    def test_there_is_one_chain_per_line_and_one_for_the_claim(self):
        self.assertEqual([entry[0] for entry in check.chains(TRUTHS["order-changed"], None)], ["line 1", "line 2", "line 3", "claim"])

    def test_one_wrong_link_loses_only_its_chain(self):
        changes = {"ordered": 99, "received": 99, "status": "over", "boxes": [], "substitute": {"item": "bowl", "count": 1}}
        for field, value in changes.items():
            answer = answer_for("order-changed")
            answer["lines"][1][field] = value
            self.assertEqual(failed("order-changed", answer), ["line 2"], field)

    def test_a_wrong_credit_loses_its_line_and_the_claim(self):
        answer = answer_for("order-changed")
        answer["lines"][0]["credit"] = 1.0
        self.assertEqual(failed("order-changed", answer), ["line 1", "claim"])

    def test_the_old_paper_number_fails_the_changed_line(self):
        answer = answer_for("order-changed")
        answer["lines"][0]["ordered"] = 6
        answer["lines"][0]["status"] = "complete"
        answer["lines"][0]["credit"] = 0
        answer["total_claim"] = 0
        self.assertEqual(failed("order-changed", answer), ["line 1", "claim"])

    def test_a_substitute_must_be_named_and_counted(self):
        for substitute in (None, {"item": "cup", "count": 6}, {"item": "vase", "count": 5}, {"item": "vase", "count": True}, "vase"):
            answer = answer_for("substitution")
            answer["lines"][0]["substitute"] = substitute
            self.assertEqual(failed("substitution", answer), ["line 1"], substitute)

    def test_counting_a_retaken_photo_twice_fails(self):
        answer = answer_for("retake")
        answer["lines"][0]["boxes"] = answer["lines"][0]["boxes"]+[{"photo": 2, "box": [10, 20, 69, 99]}]
        self.assertEqual(failed("retake", answer), ["line 1"])

    def test_a_short_line_under_the_rule_keeps_its_status_and_earns_nothing(self):
        answer = answer_for("price-rule")
        self.assertEqual((answer["lines"][0]["status"], answer["lines"][0]["credit"]), ("short", 0))

    def test_an_invented_line_fails_the_claim_alone(self):
        answer = answer_for("substitution")
        answer["lines"].append({"item": "clock"})
        self.assertEqual(failed("substitution", answer), ["claim"])

    def test_a_repeated_line_fails(self):
        answer = answer_for("substitution")
        answer["lines"].append(copy.deepcopy(answer["lines"][1]))
        self.assertEqual(failed("substitution", answer), ["line 2", "claim"])

    def test_a_wrong_kind_fails_its_line_and_the_claim(self):
        answer = answer_for("slip-overclaims")
        answer["lines"][1]["item"] = "orange"
        self.assertEqual(failed("slip-overclaims", answer), ["line 2", "claim"])

    def test_a_total_that_is_not_the_sum_fails_the_claim(self):
        answer = answer_for("slip-overclaims")
        answer["total_claim"] = 41.0
        self.assertEqual(failed("slip-overclaims", answer), ["claim"])

    def test_extra_fields_are_refused(self):
        answer = answer_for("substitution")
        answer["notes"] = "all done"
        self.assertEqual(failed("substitution", answer), ["claim"])
        answer = answer_for("substitution")
        answer["lines"][1]["notes"] = "all done"
        self.assertEqual(failed("substitution", answer), ["line 2"])

    def test_plurals_case_and_aliases_name_the_same_item(self):
        truths = dict(TRUTHS["substitution"], aliases={"bowl": ["dish"]})
        answer = copy.deepcopy(check.example_answer(truths))
        answer["lines"][0]["item"] = "Bottles"
        answer["lines"][0]["substitute"]["item"] = "VASES"
        answer["lines"][1]["item"] = "dishes"
        self.assertTrue(all(entry[1] for entry in check.chains(truths, answer)))

    def test_junk_is_graded_without_a_crash_and_passes_nothing(self):
        junk = [*checks.JUNK_ANSWERS, {"lines": None}, {"lines": [1]}, {"lines": [], "total_claim": 0}, {"lines": [{"item": 3}]},
                {"lines": [{"item": "cup", "credit": float("nan")}], "total_claim": float("nan")}, {"lines": [{"item": "cup", "credit": "1"}], "total_claim": 1}]
        for answer in junk:
            self.assertEqual(len(failed("order-changed", answer)), 4, answer)

    def test_booleans_and_text_are_not_numbers(self):
        for field, value in (("ordered", True), ("received", "6"), ("credit", "5.0"), ("credit", True)):
            answer = answer_for("order-changed")
            answer["lines"][0][field] = value
            self.assertIn("line 1", failed("order-changed", answer), field)

    def test_errors_name_the_failed_link_and_no_value(self):
        answer = answer_for("substitution")
        answer["lines"][0]["boxes"] = []
        error = [entry[2] for entry in check.chains(TRUTHS["substitution"], answer) if entry[2]]
        self.assertEqual(error, ["line 1: failed at boxes"])


class BoxTests(unittest.TestCase):
    want = row(1, 2)

    def test_a_small_shift_still_matches(self):
        self.assertTrue(check.boxes_match([{"photo": 1, "box": [15, 25, 72, 102]}, {"photo": 1, "box": [80, 20, 139, 99]}], self.want))

    def test_order_does_not_matter(self):
        self.assertTrue(check.boxes_match(self.want[::-1], self.want))

    def test_a_box_off_the_object_fails(self):
        self.assertFalse(check.boxes_match([{"photo": 1, "box": [40, 60, 99, 139]}, self.want[1]], self.want))

    def test_the_right_box_on_the_wrong_photo_fails(self):
        self.assertFalse(check.boxes_match([{"photo": 2, "box": self.want[0]["box"]}, self.want[1]], self.want))

    def test_one_object_cannot_stand_for_two(self):
        self.assertFalse(check.boxes_match([self.want[0], self.want[0]], self.want))

    def test_extra_or_missing_boxes_fail(self):
        self.assertFalse(check.boxes_match(self.want+[{"photo": 1, "box": [200, 20, 259, 99]}], self.want))
        self.assertFalse(check.boxes_match(self.want[:1], self.want))

    def test_pairing_finds_a_full_match_where_the_first_pick_would_block_it(self):
        want = [{"photo": 1, "box": [0, 0, 99, 99]}, {"photo": 1, "box": [40, 0, 139, 99]}]
        given = [{"photo": 1, "box": [20, 0, 119, 99]}, {"photo": 1, "box": [0, 0, 99, 99]}]
        self.assertGreaterEqual(check.iou(given[0]["box"], want[0]["box"]), check.MIN_IOU)
        self.assertLess(check.iou(given[1]["box"], want[1]["box"]), check.MIN_IOU)
        self.assertTrue(check.boxes_match(given, want))

    def test_iou_uses_inclusive_corners(self):
        self.assertEqual(check.iou([0, 0, 9, 9], [0, 0, 9, 9]), 1.0)
        self.assertEqual(check.iou([0, 0, 0, 0], [0, 0, 0, 0]), 1.0)
        self.assertEqual(check.iou([0, 0, 9, 9], [10, 0, 19, 9]), 0.0)

    def test_malformed_boxes_fail(self):
        for box in ([1, 2, 3], [9, 9, 1, 1], [True, 0, 5, 5], "box", [0, 0, 59, 79, 0.9]):
            self.assertFalse(check.boxes_match([{"photo": 1, "box": box}], self.want[:1]), box)


class TruthTests(unittest.TestCase):
    def test_the_planned_truths_are_sound(self):
        for value in TRUTHS.values():
            check.validate_truth(value)

    def test_a_status_is_worked_out_from_the_counts(self):
        self.assertEqual([check.expected_status(entry) for entry in (
            line("cup", 2, 2, 0, []), line("cup", 3, 2, 0, []), line("cup", 2, 3, 0, []), line("cup", 2, 0, 0, [], {"item": "bowl", "count": 2}))],
            ["complete", "short", "over", "wrong_item"])

    def test_bad_truths_are_refused(self):
        base = TRUTHS["substitution"]
        def changed(**fields):
            return dict(base, lines=[dict(base["lines"][0], **fields), base["lines"][1]])
        cases = {"equal the credits": dict(base, total_claim=1.0), "one box": changed(boxes=[]), "fewer than ordered": changed(received=6, boxes=row(2, 12)),
                 "needs an item": changed(substitute={"item": "vase"}), "bad item": changed(credit=-1)}
        cases["only a short"] = truth(line("cup", 2, 2, 3.0, row(1, 2)))
        cases["one line"] = truth(line("cup", 3, 2, 3.0, row(1, 2)), line("cups", 3, 2, 3.0, row(1, 2)))
        for message, value in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                check.validate_truth(value)


class TaskTests(unittest.TestCase):
    def test_the_task_is_a_valid_spec_and_quotes_no_answers(self):
        tasks.validate_task(TASK)
        self.assertEqual(tasks.required_tools(TASK), (tasks.TRANSCRIPTION,))
        self.assertFalse(tasks.quotes_answers(" ".join(str(value) for value in TASK.values()), TRUTHS))

    def test_the_task_names_the_deliverable_and_no_sample_trap(self):
        text = TASK["contract"].lower()
        for word in ("lines", "total_claim", "boxes", "wrong_item"):
            self.assertIn(word, text)
        for word in ("retake", "ignore", "segment", "transcribe", "vase", "10%"):
            self.assertNotIn(word, text)

    def test_the_check_is_one_standard_library_file_within_its_limit(self):
        imports = [entry for entry in SOURCE.splitlines() if entry.startswith(("import ", "from "))]
        self.assertEqual(sorted(imports), ["import json", "import sys"])
        tasks.validate_check_source(SOURCE)
        self.assertLess(len(SOURCE), sandbox.MAX_SOURCE)


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class SandboxTests(unittest.TestCase):
    def test_the_self_test_passes_with_no_weaknesses(self):
        summary = checks.self_test(SOURCE, TRUTHS)
        self.assertEqual(summary["samples"], 5)
        self.assertEqual(summary["blank_score"], 0)
        self.assertLess(summary["wrong_score"], checks.WRONG_LIMIT)
        self.assertEqual(summary["weaknesses"], [])

    def test_scores_in_the_sandbox_are_the_fraction_of_chains(self):
        answer = answer_for("order-changed")
        answer["lines"][1]["boxes"] = []
        self.assertEqual(checks.run_check(SOURCE, TRUTHS["order-changed"], answer).score, 0.75)
        self.assertEqual(checks.run_check(SOURCE, TRUTHS["order-changed"], {}).score, 0)

    def test_a_bad_truth_fails_the_self_test(self):
        with self.assertRaisesRegex(checks.CheckError, "crashed"):
            checks.self_test(SOURCE, {"bad": dict(TRUTHS["retake"], total_claim=1.0)})


if __name__ == "__main__":
    unittest.main()
