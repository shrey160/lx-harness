import unittest

from lx.voicecount import aliases, in_region, jpeg_size, region_count, tolerance, verify_count

E = {"object": "apples", "aliases": ["apple", "apples"], "region": "left", "count": 40, "tolerance": 2,
     "size": [400, 300]}


def answer(**changes):
    fields = {"object": "apples", "region": "left", "count": 40}
    fields.update(changes)
    return fields


def jpeg_header(width=400, height=300):
    return (b"\xff\xd8" + b"\xff\xe0" + (16).to_bytes(2, "big") + b"\0" * 14
            + b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08" + height.to_bytes(2, "big")
            + width.to_bytes(2, "big") + b"\x03" + b"\0" * 9 + b"\xff\xd9")


class VoiceCountVerifyTests(unittest.TestCase):
    def test_tolerance_bands(self):
        for count, expected in ((10, 1), (19, 1), (20, 1), (40, 2), (100, 5), (119, 5)):
            with self.subTest(count=count):
                self.assertEqual(tolerance(count), expected)

    def test_exact_answer_is_accepted(self):
        result = verify_count(E, answer())
        self.assertTrue(result.accepted)
        self.assertEqual(result.score, 1.0)
        self.assertEqual(result.n_passed, 1)
        self.assertEqual(result.n_criteria, 1)
        self.assertEqual(result.errors, ())

    def test_count_within_tolerance_scores_and_outside_fails(self):
        for count in (38, 42):
            with self.subTest(count=count):
                self.assertEqual(verify_count(E, answer(count=count)).score, 1.0)
        for count in (37, 43):
            with self.subTest(count=count):
                result = verify_count(E, answer(count=count))
                self.assertEqual(result.score, 0.0)
                self.assertTrue(any("count" in error for error in result.errors))

    def test_wrong_region_scores_zero(self):
        result = verify_count(E, answer(region="whole"))
        self.assertEqual(result.score, 0.0)
        self.assertTrue(any(error.startswith("region") for error in result.errors))

    def test_object_matches_case_and_alias_but_not_other_nouns(self):
        self.assertTrue(verify_count(E, answer(object="Apple")).accepted)
        result = verify_count(E, answer(object="pears"))
        self.assertEqual(result.score, 1.0)
        self.assertFalse(result.accepted)
        self.assertTrue(any(error.startswith("object") for error in result.errors))

    def test_plausible_faults_score_zero(self):
        faults = (answer(count="40"), answer(count=40.0), answer(count=True),
                  {"object": "apples", "region": "left"}, "40", ["apples", "left", 40])
        for candidate in faults:
            with self.subTest(candidate=repr(candidate)):
                self.assertEqual(verify_count(E, candidate).score, 0.0)
        self.assertEqual(verify_count(E, {"object": "apples", "count": 40}).score, 0.0)

    def test_extra_field_is_unknown_but_scored(self):
        result = verify_count(E, answer(note="x"))
        self.assertEqual(result.score, 1.0)
        self.assertFalse(result.accepted)
        self.assertTrue(any("unknown field" in error for error in result.errors))

    def test_error_messages_never_contain_expected_value(self):
        result = verify_count(E, answer(count=70))
        self.assertEqual(result.score, 0.0)
        self.assertTrue(result.errors)
        for error in result.errors:
            self.assertNotIn("40", error)

    def test_jpeg_size_reads_synthetic_header(self):
        self.assertEqual(jpeg_size(jpeg_header()), (400, 300))
        with self.assertRaises(ValueError):
            jpeg_size(b"\x89PNG\r\n\x1a\n" + b"\0" * 20)
        with self.assertRaises(ValueError):
            jpeg_size(b"\xff\xd8" + b"\0" * 20)

    def test_region_count_and_ambiguous_band(self):
        points = [(10, 10), (390, 290), (100, 250)]
        self.assertEqual(region_count(points, 400, 300, "whole"), 3)
        self.assertEqual(region_count(points, 400, 300, "left"), 2)
        self.assertEqual(region_count(points, 400, 300, "right"), 1)
        self.assertEqual(region_count(points, 400, 300, "top"), 1)
        self.assertEqual(region_count(points, 400, 300, "bottom"), 2)

        vertical_band = [(10, 10), (205, 250), (390, 290)]
        self.assertIsNone(region_count(vertical_band, 400, 300, "left"))
        self.assertIsNone(region_count(vertical_band, 400, 300, "right"))
        self.assertEqual(region_count(vertical_band, 400, 300, "whole"), 3)

        horizontal_band = [(10, 10), (100, 152), (390, 290)]
        self.assertIsNone(region_count(horizontal_band, 400, 300, "top"))
        self.assertIsNone(region_count(horizontal_band, 400, 300, "bottom"))

    def test_midline_counts_as_right(self):
        self.assertTrue(in_region(200, 10, 400, 300, "right"))
        self.assertFalse(in_region(200, 10, 400, 300, "left"))

    def test_aliases(self):
        self.assertEqual(aliases("Sea Shells"), ["sea shell", "sea shells"])
        glasses = aliases("glasses")
        self.assertIn("glass", glasses)
        self.assertIn("glasses", glasses)
        self.assertEqual(aliases("bus"), ["bus"])


if __name__ == "__main__":
    unittest.main()
