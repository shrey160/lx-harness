import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from lx.inventory import (answer_correct, canonical_colour, canonical_kind, checklist, expected_answer,
                          expected_photos, plural, singular, synthetic_sample, tolerance, validate_truth,
                          verify_inventory, write_synthetic)

COUNT = {"request_type": "count", "kind": "pen", "colour": None, "photos": {
    "1": [{"kind": "pen", "colour": "green", "count": 2}, {"kind": "mug", "count": 1}],
    "2": [{"kind": "pens", "colour": "blue", "count": 3}],
    "3": [{"kind": "apple", "colour": "red", "count": 4}]}}
COLOUR = dict(COUNT, request_type="colour_count", colour="green")
INVENTORY = dict(COUNT, request_type="inventory", kind=None)


def count_answer(**changes):
    out = {"request": {"type": "count", "kind": "pen", "colour": None},
           "photos": [{"photo": 1, "items": [{"kind": "pen", "colour": "green", "count": 2}]},
                      {"photo": 2, "items": [{"kind": "pen", "colour": "blue", "count": 3}]},
                      {"photo": 3, "items": []}],
           "answer": 5}
    out.update(changes)
    return out


def inventory_answer():
    return {"request": {"type": "inventory", "kind": None, "colour": None},
            "photos": [{"photo": 1, "items": [{"kind": "pens", "colour": None, "count": 2},
                                              {"kind": "mug", "colour": None, "count": 1}]},
                       {"photo": 2, "items": [{"kind": "pen", "colour": "blue", "count": 3}]},
                       {"photo": 3, "items": [{"kind": "apples", "colour": "red", "count": 4}]}],
            "answer": [{"kind": "pen", "count": 5}, {"kind": "mug", "count": 1}, {"kind": "apple", "count": 4}]}


def failed(truth, candidate):
    return [name for name, ok, _ in checklist(truth, candidate) if not ok]


class WordTests(unittest.TestCase):
    def test_singular_and_plural(self):
        for word, single in (("pens", "pen"), ("Glasses", "glass"), ("boxes", "box"), ("batteries", "battery"),
                             ("knives", "knife"), ("scissors", "scissors"), ("shoes", "shoe"),
                             ("paper  clips", "paper clip"), ("bus", "bus"), ("mug", "mug")):
            with self.subTest(word=word):
                self.assertEqual(singular(word), single)
        for kind, many in (("pen", "pens"), ("glass", "glasses"), ("battery", "batteries"), ("box", "boxes"),
                           ("knife", "knives"), ("scissors", "scissors"), ("key", "keys")):
            with self.subTest(kind=kind):
                self.assertEqual(plural(kind), many)

    def test_aliases_and_colours(self):
        self.assertEqual(canonical_kind("Biros"), "pen")
        self.assertEqual(canonical_kind("cups", {"cup": "mug"}), "mug")
        self.assertIsNone(canonical_kind(""))
        self.assertEqual(canonical_colour("Gray"), "grey")
        self.assertIsNone(canonical_colour(None))
        self.assertIs(canonical_colour(3), False)

    def test_tolerance_is_exact_for_small_counts(self):
        self.assertEqual([tolerance(n) for n in (1, 19, 20, 40, 100)], [0, 0, 1, 2, 5])


class TruthTests(unittest.TestCase):
    def test_valid_truths(self):
        for truth in (COUNT, COLOUR, INVENTORY):
            with self.subTest(truth=truth["request_type"]):
                self.assertIs(validate_truth(truth), truth)

    def test_expected_values(self):
        self.assertEqual(expected_photos(COUNT), {1: 2, 2: 3, 3: 0})
        self.assertEqual(expected_answer(COUNT), 5)
        self.assertEqual(expected_answer(COLOUR), 2)
        self.assertEqual(expected_answer(INVENTORY), {"pen": 5, "mug": 1, "apple": 4})

    def test_sample_aliases_merge_kinds(self):
        truth = dict(INVENTORY, aliases={"mug": ["cup"]},
                     photos=dict(INVENTORY["photos"], **{"3": [{"kind": "cup", "count": 2}]}))
        self.assertEqual(expected_answer(truth), {"pen": 5, "mug": 3})

    def test_invalid_truths(self):
        two_photos = {k: v for k, v in COUNT["photos"].items() if k != "3"}
        cases = {"unknown field": dict(COUNT, note="x"), "bad type": dict(COUNT, request_type="sum"),
                 "inventory kind": dict(INVENTORY, kind="pen"), "missing kind": dict(COUNT, kind=None),
                 "bad colour": dict(COLOUR, colour="teal"), "colour on count": dict(COUNT, colour="green"),
                 "too few photos": dict(COUNT, photos=two_photos),
                 "gap in numbers": dict(COUNT, photos={"1": [], "2": [], "4": []}),
                 "zero count": dict(COUNT, photos=dict(COUNT["photos"], **{"3": [{"kind": "apple", "count": 0}]})),
                 "zero answer": dict(COUNT, kind="spoon"), "bad aliases": dict(COUNT, aliases={"mug": "cup"})}
        for name, truth in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ValueError):
                    validate_truth(truth)


class ChecklistTests(unittest.TestCase):
    def test_perfect_answers_pass_every_check(self):
        for truth, candidate in ((COUNT, count_answer()), (INVENTORY, inventory_answer())):
            with self.subTest(truth=truth["request_type"]):
                result = verify_inventory(truth, candidate)
                self.assertTrue(result.accepted)
                self.assertEqual((result.score, result.n_passed, result.n_criteria), (1.0, 8, 8))
                self.assertTrue(answer_correct(truth, candidate))

    def test_check_names_depend_only_on_truth(self):
        names = [name for name, _, _ in checklist(COUNT, "not json")]
        self.assertEqual(names, ["schema", "request", "coverage", "photo 1", "photo 2", "photo 3",
                                 "consistency", "answer"])
        self.assertEqual(verify_inventory(COUNT, "not json").score, 0.0)

    def test_colour_count_counts_only_that_colour(self):
        candidate = count_answer(request={"type": "colour_count", "kind": "pen", "colour": "green"},
                                 photos=[{"photo": 1, "items": [{"kind": "pen", "colour": "green", "count": 2}]},
                                         {"photo": 2, "items": []}, {"photo": 3, "items": []}], answer=2)
        self.assertEqual(failed(COLOUR, candidate), [])
        # Blue pens listed and summed into the answer: per-photo green counts still pass, the total does not.
        self.assertEqual(failed(COLOUR, count_answer(request=candidate["request"])), ["answer"])

    def test_partial_credit_for_a_skipped_photo(self):
        candidate = count_answer(photos=count_answer()["photos"][:2], answer=5)
        self.assertEqual(failed(COUNT, candidate), ["coverage", "photo 3"])
        self.assertTrue(answer_correct(COUNT, candidate))
        self.assertEqual(verify_inventory(COUNT, candidate).score, 6 / 8)

    def test_sum_mismatch_fails_consistency_only(self):
        self.assertEqual(failed(COUNT, count_answer(answer=6)), ["consistency", "answer"])
        wrong_photo = count_answer(photos=[{"photo": 1, "items": [{"kind": "pen", "colour": None, "count": 3}]},
                                           *count_answer()["photos"][1:]], answer=5)
        self.assertEqual(failed(COUNT, wrong_photo), ["photo 1", "consistency"])

    def test_wrong_request_and_schema(self):
        self.assertEqual(failed(COUNT, count_answer(request={"type": "count", "kind": "mug", "colour": None})),
                         ["request"])
        self.assertIn("schema", failed(COUNT, dict(count_answer(), note="x")))
        self.assertIn("schema", failed(COUNT, count_answer(answer="5")))
        duplicate = count_answer(photos=count_answer()["photos"] + [{"photo": 1, "items": []}])
        self.assertEqual(failed(COUNT, duplicate)[:2], ["schema", "coverage"])

    def test_inventory_needs_exact_kind_sets(self):
        candidate = inventory_answer()
        candidate["photos"][0]["items"].append({"kind": "spoon", "colour": None, "count": 1})
        candidate["answer"].append({"kind": "spoon", "count": 1})
        self.assertEqual(failed(INVENTORY, candidate), ["photo 1", "answer"])
        missing = inventory_answer()
        missing["answer"] = missing["answer"][:2]
        self.assertEqual(failed(INVENTORY, missing), ["consistency", "answer"])

    def test_errors_carry_no_values(self):
        result = verify_inventory(COUNT, count_answer(answer=99))
        self.assertTrue(result.errors)
        self.assertFalse(any(ch.isdigit() for e in result.errors for ch in e.replace("photo ", "")))


def png_size(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


class SyntheticTests(unittest.TestCase):
    def test_samples_are_valid_deterministic_and_cover_types(self):
        seen = set()
        for index in range(1, 7):
            photos, truth, text = synthetic_sample(7, index)
            validate_truth(truth)
            seen.add(truth["request_type"])
            self.assertEqual(len(photos), len(truth["photos"]))
            self.assertTrue(all(png_size(p) == (320, 240) for p in photos))
            self.assertTrue(text.endswith((".", "?")))
            if truth["kind"]:
                self.assertIn(plural(truth["kind"]), text)
        self.assertEqual(seen, {"count", "colour_count", "inventory"})
        self.assertEqual(synthetic_sample(7, 2), synthetic_sample(7, 2))
        self.assertNotEqual(synthetic_sample(7, 2)[1], synthetic_sample(8, 2)[1])

    def test_png_decodes_to_drawn_pixels(self):
        photos, truth, _ = synthetic_sample(3, 1)
        data = photos[0]
        raw = zlib.decompress(data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8])
        self.assertEqual(len(raw), 240 * (1 + 320 * 3))
        self.assertGreater(len(set(raw)), 3)

    def test_write_synthetic_uses_the_sample_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            names = write_synthetic(Path(tmp) / "d3", 3, seed=5)
            self.assertEqual(names, ["sample-01", "sample-02", "sample-03"])
            folder = Path(tmp) / "d3" / "sample-02"
            truth = json.loads((folder / "truth.json").read_text())
            validate_truth(truth)
            self.assertEqual(sorted(p.name for p in (folder / "photos").iterdir()),
                             [f"{i}.png" for i in range(1, len(truth["photos"]) + 1)])
            self.assertTrue((folder / "request.txt").read_text().strip())
            with self.assertRaises(ValueError):
                write_synthetic(Path(tmp) / "d3", 1)


if __name__ == "__main__":
    unittest.main()
