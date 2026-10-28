"""Voicecount dataset build, freeze and an offline preview trial on a synthetic FSC-147 fixture."""

import io
import json
import random
import tempfile
import unittest
import wave
from pathlib import Path

from lx.budget import BudgetLedger
from lx.perception import PerceptionCache, PerceptionClient
from lx.spark import SparkClient
from lx.trials import VOICECOUNT_CATALOG, run_trials
from lx.usage import BudgetScope
from lx.voicecount import build_voicecount_dataset, finalize_voicecount, load_voicecount_dataset
from lx.voicecount_workflow import (VoiceCountAdapter, VoiceCountPreviewTransport, parse_request,
                                    preview_perception)


def jpeg(width, height, tag):
    app0 = tag.encode().ljust(14, b"\0")[:14]
    return (b"\xff\xd8\xff\xe0" + (16).to_bytes(2, "big") + app0 + b"\xff\xc0" + (17).to_bytes(2, "big")
            + b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x03" + b"\0" * 9 + b"\xff\xd9")


def silence(seconds=1.0):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(16000)
        clip.writeframes(b"\0\0" * int(16000 * seconds))
    return stream.getvalue()


def fsc_fixture(root, per_split=12):
    rng = random.Random(7)
    images, annotations, classes, splits = root/"images", {}, [], {"train": [], "val": [], "test": []}
    images.mkdir(parents=True)
    names = ["apples", "sea shells", "birds", "coins", "eggs", "cars", "pens", "cups", "keys", "beads", "nails", "stones"]
    index = 0
    for split in splits:
        for i in range(per_split):
            index += 1
            name = f"{index}.jpg"
            (images/name).write_bytes(jpeg(400, 300, name))
            points = [[rng.uniform(5, 180), rng.uniform(5, 140)] for _ in range(14)]
            points += [[rng.uniform(220, 395), rng.uniform(160, 295)] for _ in range(rng.randint(12, 30))]
            annotations[name] = {"points": points, "H": 300, "W": 400}
            classes.append(f"{name}\t{names[i % len(names)]}")
            splits[split].append(name)
    (root/"annotation_FSC147_384.json").write_text(json.dumps(annotations))
    (root/"ImageClasses_FSC147.txt").write_text("\n".join(classes) + "\n")
    (root/"Train_Test_Val_FSC_147.json").write_text(json.dumps(splits))
    return root, images


class VoiceCountDatasetTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        fsc, images = fsc_fixture(self.root/"fsc")
        self.dataset = build_voicecount_dataset(self.root/"data", fsc, images, 3, 1, 2, 1)
        # Distinct clips per item, as real speech would be; the doubles key on media hashes.
        for index, sample in enumerate(self.dataset.worker_inputs):
            (self.dataset.root/"worker_inputs"/sample["audio"]).write_bytes(silence(1.0 + index / 100))
        finalize_voicecount(self.dataset.root)

    def test_build_is_split_disjoint_and_keeps_requests_controller_side(self):
        dataset = load_voicecount_dataset(self.dataset.root)
        splits = {}
        for sample in dataset.worker_inputs:
            splits.setdefault(sample["split"], set()).add(sample["source_id"])
        self.assertEqual({k: len(v) for k, v in splits.items()}, {"loop": 3, "verify": 1, "test": 2, "calibration": 1})
        self.assertFalse(set.intersection(*[set(v) for v in splits.values()]))
        loop_ids = {int(s) for s in splits["loop"]}
        self.assertTrue(all(i <= 12 for i in loop_ids))
        worker_text = "".join(p.read_text(errors="ignore") for p in (self.dataset.root/"worker_inputs").rglob("*.json"))
        for sid, script in dataset.scripts.items():
            self.assertNotIn(script["text"], worker_text)
            truth = dataset.controller_truth[sid]
            self.assertIn(truth["region"], ("whole", "left", "right", "top", "bottom"))
            self.assertEqual(parse_request(script["text"]), (truth["object"], truth["region"]))
        self.assertNotIn("points", worker_text)

    def test_rebuild_is_deterministic_and_frozen_media_is_checked(self):
        fsc, images = self.root/"fsc", self.root/"fsc"/"images"
        again = build_voicecount_dataset(self.root/"again", fsc, images, 3, 1, 2, 1)
        self.assertEqual(again.controller_truth, self.dataset.controller_truth)
        self.assertEqual(again.scripts, self.dataset.scripts)
        audio = self.dataset.root/"worker_inputs"/self.dataset.worker_inputs[0]["audio"]
        audio.write_bytes(silence(2.0))
        with self.assertRaisesRegex(ValueError, "changed"):
            load_voicecount_dataset(self.dataset.root)

    def test_offline_preview_trial_routes_through_tools_and_improves_on_test(self):
        dataset = load_voicecount_dataset(self.dataset.root)
        ledger = BudgetLedger(self.root/"usage.sqlite3", initial_spend_usd=0)
        segmenter, transcriber = preview_perception(dataset)

        def factory(identifier):
            return SparkClient(VoiceCountPreviewTransport(), BudgetScope(ledger, identifier, "5"))

        def extras(client):
            return {"perception": PerceptionClient(segmenter, transcriber, client.ledger,
                                                   PerceptionCache(self.root/"cache")), "catalog": VOICECOUNT_CATALOG}
        result = run_trials(dataset, self.root/"trials", factory, trials=1, adaptation_count=1,
                            task="voicecount", adapter_kwargs=extras)
        test = result["final_test"]
        self.assertEqual(len(test["base"]), 2)
        trial = result["trials"][0]
        self.assertTrue(trial["improved"])
        self.assertGreaterEqual(min(test["gains"].values()), 0)
        self.assertGreater(trial["means"]["candidate_dev"], trial["means"]["base_dev"])
        guidance = (self.root/"trials"/trial["trial_id"]/"package"/"harness"/"tool_descriptions"/"tool_guidance.md")
        self.assertIn("count_boxes", guidance.read_text())
        rollouts = list((self.root/"trials").rglob("rollouts/*.md"))
        self.assertTrue(rollouts)
        text = rollouts[0].read_text()
        self.assertIn("[CALL] transcribe", text)
        self.assertIn("[RESULT] segment", text)
        self.assertGreater(transcriber.calls, 0)
        self.assertLessEqual(segmenter.calls, len(dataset.worker_inputs) * 2)
        self.assertGreater(result["trials"][0]["usage"]["per_role"]["tool"]["spent_usd"], 0)

    def test_text_only_agent_gets_no_image(self):
        from lx.providers import ProviderProfile
        dataset = load_voicecount_dataset(self.dataset.root)
        seen = []
        transport = VoiceCountPreviewTransport()
        def recording(body, timeout):
            seen.append(body)
            return transport(body, timeout)
        local = ProviderProfile(kind="chat", endpoint="http://127.0.0.1:8888/v1/chat/completions", model="local",
                                token_param="max_tokens", supports_tools=True)
        ledger = BudgetLedger(self.root/"local.sqlite3", initial_spend_usd=0)
        client = SparkClient(recording, ledger, profile=local)
        segmenter, transcriber = preview_perception(dataset)
        adapter = VoiceCountAdapter(dataset.root/"worker_inputs", self.root/"local", client, dataset.controller_truth,
                                    perception=PerceptionClient(segmenter, transcriber, ledger, PerceptionCache(self.root/"c2")),
                                    catalog=VOICECOUNT_CATALOG)
        run = adapter.execute("test-0001", None, "local/test-0001")
        parts = seen[0]["messages"][1]["content"]
        self.assertEqual([p["type"] for p in parts], ["text"])
        self.assertIn("not viewable", parts[0]["text"])
        self.assertTrue((run/"trace.json").is_file())


if __name__ == "__main__":
    unittest.main()
