"""The harder order records: fair samples the ordinary check can score."""
import importlib.util
import json
from pathlib import Path
import random
import tempfile
import unittest

from lx import records_check
from lx.text_task import check_sample_folder

ROOT = Path(__file__).resolve().parent.parent/"scripts"/"records_hard"
spec = importlib.util.spec_from_file_location("records_hard_build", ROOT/"build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class Samples(unittest.TestCase):
    def test_the_check_gives_full_marks_to_each_truth(self):
        with tempfile.TemporaryDirectory() as folder:
            build.write_samples(folder, 8)
            for number in range(1, 9):
                _, truth = check_sample_folder(Path(folder)/f"sample-{number:02d}")
                self.assertTrue(all(passed for _, passed, _ in records_check.checklist(truth, truth)))

    def test_the_same_seed_gives_the_same_samples(self):
        self.assertEqual(build.record(random.Random(5), 0), build.record(random.Random(5), 0))

    def test_every_right_value_can_be_read_from_the_record(self):
        rng = random.Random(9)
        for index in range(40):
            text, truth = build.record(rng, index)
            self.assertIn(truth["order_id"], text)
            self.assertIn(", ".join(reversed(truth["customer"].upper().split())).split(", ")[0], text)
            self.assertIn(truth["date"][2:4], text)
            self.assertIn(build.grouped(truth["amount_minor"], truth["currency"]), text)
            if truth["phone"]:
                self.assertIn(f"{truth['phone'][2:5]}.{truth['phone'][5:8]}.{truth['phone'][8:]}", text)
            for tag in truth["tags"]:
                self.assertIn(tag, text.lower())

    def test_amounts_follow_each_currencys_habit(self):
        self.assertEqual(build.grouped(807121, "EUR"), "8.071,21")
        self.assertEqual(build.grouped(807121, "USD"), "8,071.21")
        self.assertEqual(build.grouped(98765432, "INR"), "9,87,654.32")

    def test_the_task_text_gives_no_answer_and_keeps_the_original_fields(self):
        contract = json.loads((ROOT/"task.json").read_text(encoding="utf-8"))["contract"]
        self.assertIn("order_id", contract)
        self.assertNotIn("LX-", contract)


if __name__ == "__main__":
    unittest.main()
