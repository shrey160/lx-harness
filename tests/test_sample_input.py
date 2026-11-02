"""Direct photo-task data reaches workers through frozen inputs, never answer truth."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lx import sandbox, tasks
from lx.budget import BudgetLedger
from lx.inventory_data import add_user_samples, finalize_inventory, load_inventory_dataset, _tone
from lx.inventory_workflow import InventoryAdapter, InventoryDomain
from lx.sample_input import MAX_INPUT_BYTES, read_input
from lx.spark import ChatClient
from lx.spike import wire_reply
from lx.usage import BudgetScope
from test_inventory_data import make_sample
from test_tasks import Base


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def dataset(self):
        source = self.root / "samples"
        for index in (1, 2):
            folder = make_sample(source, f"sample-{index:02d}", n=2, wav=_tone(index), text=None,
                                 truth={"private_answer": "ANSWER_SENTINEL"})
            (folder / "input.json").write_text(json.dumps({"stock": {"cup": index}, "account": {"balance": 50}}), encoding="utf-8")
        dataset = self.root / "dataset"
        add_user_samples(dataset, source, stream_count=2, inventory=False, min_photos=2)
        finalize_inventory(dataset, inventory=False)
        return dataset

    def test_validation_rejects_bad_oversized_and_ambiguous_json(self):
        path = self.root / "input.json"
        self.assertIsNone(read_input(path))
        with self.assertRaisesRegex(ValueError, "missing"):
            read_input(path, required=True)
        for data in (b"", b"[]", b"null", b"{bad", b'{"a":1,"a":2}', b'{"a":{"x":1,"x":2}}',
                     b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1e999}', b'{"a":"\xff"}', b" " * (MAX_INPUT_BYTES + 1)):
            with self.subTest(data=data[:30]):
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    read_input(path)
        value = {"inventory": {"café cup": {"quantity": 4, "unit_price": 2.5}}, "account": {"balance": 50}}
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(read_input(path), value)

    def test_input_links_and_linked_parent_are_refused(self):
        source = self.root / "source.json"
        source.write_text('{"stock":4}', encoding="utf-8")
        link = self.root / "input.json"
        try:
            link.symlink_to(source)
        except OSError:
            self.skipTest("This Windows account cannot create symbolic links")
        with self.assertRaisesRegex(ValueError, "linked"):
            read_input(link)
        directory = self.root / "linked"
        directory.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "linked"):
            read_input(directory / "source.json")

    def test_freeze_hashes_input_and_manifests_cannot_read_truth(self):
        dataset = self.dataset()
        samples, truth, _ = load_inventory_dataset(dataset)
        inputs = dataset / "worker_inputs"
        _, loaded = tasks.TaskDomain.samples_from(inputs)
        self.assertEqual(loaded[0]["input"]["account"], {"balance": 50})
        with self.assertRaises(ValueError):
            InventoryDomain.samples_from(inputs)  # legacy experiments still require three photos
        manifest = inputs / "manifest.json"
        saved = manifest.read_text(encoding="utf-8")
        for path in ("../controller_truth/ground_truth.json", samples[1]["input"], "media/sample-01/truth.json", None):
            bad = json.loads(saved)
            bad["samples"][0]["input"] = path
            manifest.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(ValueError):
                tasks.TaskDomain.samples_from(inputs)
            with self.assertRaises(ValueError):
                load_inventory_dataset(dataset)
        manifest.write_text(saved, encoding="utf-8")
        file = inputs / samples[0]["input"]
        file.write_text('{"stock":{"cup":99},"account":{"balance":50}}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed"):
            load_inventory_dataset(dataset)

    def test_worker_and_improver_get_frozen_json_and_cache_tracks_it(self):
        dataset = self.dataset()
        task = {**tasks.inventory_task(), "truth_schema": "free", "stream_samples": 2}
        calls = []
        def transport(body, timeout=None):
            first = json.loads(body["messages"][1]["content"])
            calls.append(copy.deepcopy(body["messages"]))
            return wire_reply(json.dumps({"stock": first["input"]["stock"]}))
        client = ChatClient(transport, BudgetScope(BudgetLedger(self.root / "ledger.db", initial_spend_usd=0), "input-test", "2"))
        with patch.object(sandbox, "available", return_value=False):
            adapter = InventoryAdapter(dataset / "worker_inputs", self.root / "out", client, {}, perception=None, catalog=[],
                                       domain=tasks.TaskDomain(task, None))
            sample_id = adapter.tasks("dev")[0]
            data = adapter.by_id[sample_id]["input"]
            run = adapter.execute(sample_id, None, "same-run")
            self.assertEqual(json.loads((run / "answer.json").read_text()), {"stock": data["stock"]})
            opening = json.loads((run / "opening.json").read_text())
            self.assertEqual(json.loads(opening[1]["content"])["input"], data)
            source = (adapter.task_files(sample_id) / "sample.json").read_text()
            self.assertEqual(json.loads(source)["input"], data)
            self.assertNotIn("ANSWER_SENTINEL", source + json.dumps(opening))
            self.assertNotIn("private_answer", json.dumps(calls))
            adapter.execute(sample_id, None, "same-run")
            self.assertEqual(len(calls), 1)  # unchanged inputs reuse the execution
            identity = adapter._identity(sample_id, None)
            manifest = json.loads((dataset / "worker_inputs/manifest.json").read_text())
            file = dataset / "worker_inputs" / manifest["samples"][0]["input"]
            altered = copy.deepcopy(data)
            altered["account"]["balance"] = 45
            file.write_text(json.dumps(altered), encoding="utf-8")
            finalize_inventory(dataset, inventory=False)
            refreshed = InventoryAdapter(dataset / "worker_inputs", self.root / "out", client, {}, perception=None, catalog=[],
                                         domain=tasks.TaskDomain(task, None))
            self.assertNotEqual(identity, refreshed._identity(sample_id, None))
            refreshed.execute(sample_id, None, "same-run")
            self.assertEqual(len(calls), 2)
            self.assertEqual(json.loads(calls[-1][1]["content"])["input"]["account"]["balance"], 45)

    def test_no_input_preserves_the_existing_user_message(self):
        sample = {"sample_id": "s1", "split": "dev", "photos": ["1.jpg"] * 3, "audio": "s1.wav"}
        message = InventoryDomain().messages(sample, "base", [(200, 100)] * 3)
        data = json.loads(message[1]["content"])
        self.assertEqual(set(data), {"sample_id", "photos", "photos_note", "spoken_request"})


class ApprovalTests(Base):
    def test_json_is_frozen_at_approval_and_snapshot_tampering_is_refused(self):
        project = self.inventory()
        samples = Path(project["path"]) / "samples"
        for index, folder in enumerate(sorted(samples.iterdir()), 1):
            (folder / "request.wav").write_bytes(_tone(index))
            (folder / "input.json").write_text(json.dumps({"stock": index}), encoding="utf-8")
        project = self.app.edit_spec(project["id"], {"mode": "live", "goal": "g", "budget_usd": 1})
        with patch.object(self.app.registry, "meta_key_source", return_value="registered"):
            project = self.approve(project, notice_accepted=True)
        workspace = self.app.workspace(project["id"])
        approved = project["approved"]
        snapshot = workspace.snapshot_dir(approved["snapshot"])
        self.assertEqual(json.loads((snapshot / "sample-01/input.json").read_text()), {"stock": 1})
        (samples / "sample-01/input.json").write_text('{"stock":99}', encoding="utf-8")
        with patch.object(self.app.registry, "meta_key_source", return_value="registered"):
            job = self.app.start(project["id"], launch=False)
        self.assertEqual(json.loads((snapshot / "sample-01/input.json").read_text()), {"stock": 1})
        frozen = workspace.run_dir(job["id"]) / "snapshot"
        self.assertEqual(json.loads((frozen / "sample-01/input.json").read_text()), {"stock": 1})
        (frozen / "sample-01/input.json").write_text('{"stock":99}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "snapshot changed"):
            self.app.verify_job_approval(workspace, job, frozen)


if __name__ == "__main__":
    unittest.main()
