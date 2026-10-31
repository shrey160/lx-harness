"""`ready`: a free check that a spec could run live on any samples, with no model and no network."""
from io import BytesIO
from pathlib import Path
import random
import shutil
import socket
import unittest
from unittest.mock import patch

from PIL import Image

from lx import sandbox, text_task
from lx.inventory import write_synthetic
from lx.inventory_data import _tone
from lx.orchestration import Orchestration
from test_tasks import Base


def own_photo(path, shade):
    """A plain photo the preview doubles have never seen."""
    Image.new("RGB", (320, 240), (shade, 90, 60)).save(path, "JPEG")


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class Photos(Base):
    def setUp(self):
        super().setUp()
        self.lx = Orchestration(self.app, launch=False)
        self.path = self.folder()
        self.samples = Path(self.path)/"samples"
        write_synthetic(self.samples, 6)
        for index, sample in enumerate(sorted(self.samples.iterdir())):
            (sample/"request.wav").write_bytes(_tone(1))
            for number, photo in enumerate(sorted((sample/"photos").iterdir())):
                own_photo(photo, (index*20+number) % 256)
        self.lx.init(self.path, "photo-request")
        self.app.registry.update("spark-contributor", api_key="dummy-ui-key")

    def problems(self):
        return {(item["sample"], item["problem"].split(":")[0]) for item in self.lx.ready(self.path)["problems"]}

    def test_your_own_samples_can_be_ready_when_a_preview_refuses_them(self):
        result = self.lx.ready(self.path)
        self.assertEqual((result["ready"], result["problems"], result["blocked"]), (True, [], None))
        self.assertTrue(result["check"]["samples"])
        with self.assertRaisesRegex(ValueError, "synthetic"):
            self.lx.preview(self.path)

    def test_it_uses_no_network(self):
        with patch.object(socket.socket, "connect", side_effect=AssertionError("no network")), \
                patch.object(socket.socket, "connect_ex", side_effect=AssertionError("no network")):
            self.assertTrue(self.lx.ready(self.path)["ready"])

    def test_it_changes_nothing(self):
        before = self.lx.spec_show(self.path)
        self.lx.ready(self.path)
        self.assertEqual(self.lx.spec_show(self.path), before)
        self.assertEqual(self.lx.run_status(self.path)["runs"], [])

    def test_a_broken_photo_is_reported_against_its_sample(self):
        (self.samples/"sample-03"/"photos"/"1.png").write_bytes(b"not a photo")
        self.assertIn(("sample-03", "photos/1.png"), self.problems())

    def test_missing_audio_is_reported(self):
        (self.samples/"sample-02"/"request.wav").unlink()
        self.assertIn(("sample-02", "add request.wav"), self.problems())

    def test_malformed_audio_is_reported(self):
        (self.samples/"sample-04"/"request.wav").write_bytes(b"RIFFnonsense")
        problems = self.lx.ready(self.path)["problems"]
        self.assertEqual([item["sample"] for item in problems], ["sample-04"])
        self.assertIn("mono 16-bit PCM", problems[0]["problem"])

    def test_an_oversized_photo_is_reported(self):
        noise = Image.frombytes("RGB", (900, 900), random.Random(1).randbytes(900*900*3))
        out = BytesIO()
        noise.save(out, "PNG")
        (self.samples/"sample-05"/"photos"/"1.png").write_bytes(out.getvalue())
        with patch("lx.inventory_probe.MAX_VIEW_BYTES", 1000):
            self.assertIn(("sample-05", "photos/1.png"), self.problems())

    def test_every_bad_sample_shows_at_once(self):
        (self.samples/"sample-01"/"request.wav").unlink()
        (self.samples/"sample-06"/"truth.json").unlink()
        self.assertEqual({sample for sample, _ in self.problems()}, {"sample-01", "sample-06"})

    def test_too_few_samples_are_reported(self):
        shutil.rmtree(self.samples/"sample-06")
        self.assertIn((None, "This task needs at least 6 sample folders; found 5"), self.problems())

    def test_a_tool_turned_off_blocks_it(self):
        self.app.registry.set_enabled("transcribe-catalog", False)
        result = self.lx.ready(self.path)
        self.assertFalse(result["ready"])
        self.assertIn("off in Settings", result["blocked"])
        self.assertEqual(result["problems"], [])

    def test_no_meta_key_blocks_it(self):
        with patch.object(self.app.registry, "meta_key_source", return_value=None):
            result = self.lx.ready(self.path)
        self.assertIn("needs a Meta key", result["blocked"])

    def test_a_check_that_cannot_run_is_a_problem(self):
        with patch("lx.checks._call", side_effect=ValueError("The scoring check crashed")):
            self.assertIn((None, "The scoring check crashed"), self.problems())


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class Text(Base):
    def test_a_text_task_is_read_by_its_own_rules(self):
        lx = Orchestration(self.app, launch=False)
        path = self.folder()
        samples = Path(path)/"samples"
        text_task.write_samples(samples, 6)
        lx.init(path, "text-request")
        self.app.registry.update("spark-contributor", api_key="dummy-ui-key")
        self.assertTrue(lx.ready(path)["ready"], lx.ready(path))
        (samples/"sample-02"/"request.txt").unlink()
        self.assertEqual(lx.ready(path)["problems"], [{"sample": "sample-02", "problem": "missing request.txt"}])


if __name__ == "__main__":
    unittest.main()
