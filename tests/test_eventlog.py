import json
import tempfile
import unittest
from pathlib import Path

from lx.eventlog import (
    FIELDS,
    LEVELS,
    MAX_SPLIT_COUNT,
    SPLITS,
    build_eventlog_dataset,
    replay,
    verify_case,
)


def _event(ts, eid, kind, order="LX-1", **params):
    return {"ts": f"2025-01-01T00:{ts:02d}:00Z", "event_id": eid, "order_id": order, "kind": kind, **params}


def _currency(log):
    return next(e["currency"] for e in log if e["kind"] == "create")


def _create(currency="USD"):
    return _event(0, "E0", "create", currency=currency)


def _add(ts, eid, sku="SKU-1", qty=1, price="10.00", ccy="USD", order="LX-1"):
    return _event(ts, eid, "add_line", order=order, sku=sku, qty=qty, price=price, price_currency=ccy)


class ReplayRuleTests(unittest.TestCase):
    def test_timestamp_order_not_log_order(self):
        log = [_event(3, "E3", "set_qty", sku="SKU-1", qty=4), _create(), _add(1, "E1", qty=2)]
        self.assertEqual([{"sku": "SKU-1", "qty": 4, "unit_minor": 1000}], replay(log, "LX-1", {})["lines"])

    def test_duplicates_and_other_orders_are_ignored(self):
        add = _add(1, "E1", qty=2)
        log = [_create(), add, dict(add), _add(2, "E2", qty=9, order="LX-2")]
        self.assertEqual(2, replay(log, "LX-1", {})["lines"][0]["qty"])

    def test_readd_accumulates_qty_and_replaces_price(self):
        log = [_create(), _add(1, "E1", qty=2, price="10.00"), _add(2, "E2", qty=1, price="12.00")]
        self.assertEqual([{"sku": "SKU-1", "qty": 3, "unit_minor": 1200}], replay(log, "LX-1", {})["lines"])

    def test_absent_sku_edits_ignored_and_qty_zero_removes(self):
        log = [_create(), _add(1, "E1"), _event(2, "E2", "set_qty", sku="SKU-9", qty=3),
               _event(3, "E3", "remove_line", sku="SKU-8"), _event(4, "E4", "set_qty", sku="SKU-1", qty=0)]
        self.assertEqual([], replay(log, "LX-1", {})["lines"])

    def test_cancel_suppresses_line_events_but_not_refunds(self):
        log = [_create(), _add(1, "E1", qty=2), _event(2, "E2", "cancel"), _add(3, "E3", qty=5),
               _event(4, "E4", "discount", code="VIP15"), _event(5, "E5", "refund", amount="3.00")]
        result = replay(log, "LX-1", {})
        self.assertEqual(("cancelled", 2000, 0, 300), (result["status"], result["subtotal_minor"],
                                                       result["discount_minor"], result["refunded_minor"]))
        log.append(_event(6, "E6", "reinstate"))
        log.append(_add(7, "E7", sku="SKU-2"))
        self.assertEqual("open", replay(log, "LX-1", {})["status"])
        self.assertEqual(3000, replay(log, "LX-1", {})["subtotal_minor"])

    def test_latest_discount_half_even_and_refund_cap(self):
        # 3 x 0.10 + 1 x 0.00 subtotal 30; 5% = 1.5 -> 2 (half-even), and 10 x 0.05 = 0.5 -> 0.
        log = [_create(), _add(1, "E1", qty=3, price="0.10"), _event(2, "E2", "discount", code="VIP15"),
               _event(3, "E3", "discount", code="SAVE5"), _event(4, "E4", "refund", amount="9.00")]
        result = replay(log, "LX-1", {})
        self.assertEqual((30, 2, 28, 28), (result["subtotal_minor"], result["discount_minor"],
                                            result["total_minor"], result["refunded_minor"]))
        half_down = [_create(), _add(1, "E1", qty=10, price="0.01"), _event(2, "E2", "discount", code="SAVE5")]
        self.assertEqual(0, replay(half_down, "LX-1", {})["discount_minor"])
        cleared = log[:3] + [_event(3, "E3", "discount", code="CLEAR")]
        self.assertEqual(0, replay(cleared, "LX-1", {})["discount_minor"])

    def test_foreign_price_converts_then_rounds_half_even(self):
        log = [_create("USD"), _add(1, "E1", price="12.50", ccy="EUR"), _add(2, "E2", sku="SKU-2", price="0.25", ccy="INR")]
        lines = replay(log, "LX-1", {"EUR": "1.0850", "INR": "0.0100"})["lines"]
        # 1250 * 1.085 = 1356.25 -> 1356; 25 * 0.01 = 0.25 -> 0.
        self.assertEqual([1356, 0], [line["unit_minor"] for line in lines])


class EventLogDatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def build(self, name, *counts, **kwargs):
        return build_eventlog_dataset(self.root / name, *counts, **kwargs)

    def test_default_split_shape_and_separated_truth(self):
        dataset = self.build("default")
        by_split = {split: [i for i in dataset.worker_inputs if i["split"] == split] for split in SPLITS}
        self.assertEqual((40, 5, 40, 0), tuple(len(by_split[split]) for split in SPLITS))
        self.assertEqual(3, dataset.difficulty)
        worker_dir = dataset.root / "worker_inputs"
        manifest = json.loads((worker_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(all(set(item) == {"sample_id", "source_id", "split", "text"} for item in manifest["samples"]))
        for name in ("ground_truth.json", "events.json", "dataset.json"):
            path = dataset.root / "controller_truth" / name
            self.assertTrue(path.exists())
            self.assertFalse(path.is_relative_to(worker_dir))
        self.assertTrue(all(set(t) == set(FIELDS) for t in dataset.controller_truth.values()))

    def test_truth_matches_controller_replay_of_saved_events(self):
        dataset = self.build("replay", 6, 2, 6, 2, difficulty=4)
        for sample_id, saved in dataset.controller_events.items():
            self.assertEqual(dataset.controller_truth[sample_id], replay(saved["log"], saved["order_id"], saved["rates"]))
            result = verify_case(dataset.controller_truth[sample_id], dataset.controller_truth[sample_id])
            self.assertTrue(result.accepted, result.errors)
            self.assertEqual(1.0, result.score)

    def test_splits_are_disjoint_in_orders_skus_and_wording(self):
        dataset = self.build("disjoint", 30, 10, 30, 10, difficulty=4)
        orders, skus = {}, {}
        for sample_id, saved in dataset.controller_events.items():
            split = sample_id.split("-", 1)[0]
            for event in saved["log"]:
                self.assertEqual(split, orders.setdefault(event["order_id"], split))
                if "sku" in event:
                    self.assertEqual(split, skus.setdefault(event["sku"], split))
        loop_text = "\n".join(i["text"] for i in dataset.worker_inputs if i["split"] == "loop")
        heldout_text = "\n".join(i["text"] for i in dataset.worker_inputs if i["split"] != "loop")
        for marker in ("time=", " :: "):
            self.assertNotIn(marker, loop_text)
            self.assertIn(marker, heldout_text)
        for marker in (" | ", "At 20"):
            self.assertIn(marker, loop_text)
            self.assertNotIn(marker, heldout_text)

    def test_generation_is_deterministic_and_seed_sensitive(self):
        first = self.build("a", 3, 2, 3, 1, seed=91)
        same = self.build("b", 3, 2, 3, 1, seed=91)
        other = self.build("c", 3, 2, 3, 1, seed=92)
        self.assertEqual(first.worker_inputs, same.worker_inputs)
        self.assertEqual((first.root / "controller_truth" / "ground_truth.json").read_bytes(),
                         (same.root / "controller_truth" / "ground_truth.json").read_bytes())
        self.assertNotEqual(first.worker_inputs, other.worker_inputs)

    def test_changing_one_split_count_leaves_other_splits_intact(self):
        small = self.build("small", 1, 5, 4, 2, seed=772)
        large = self.build("large", 12, 1, 4, 2, seed=772)
        for split in ("test", "calibration"):
            pick = lambda d: tuple(i for i in d.worker_inputs if i["split"] == split)
            self.assertEqual(pick(small), pick(large))

    def test_difficulty_ladder_adds_structure(self):
        def logs(level):
            dataset = self.build(f"level-{level}", 20, 0, 0, difficulty=level, seed=5)
            return dataset, [saved["log"] for saved in dataset.controller_events.values()]

        sizes = []
        for level in sorted(LEVELS):
            dataset, all_logs = logs(level)
            sizes.append(sum(map(len, all_logs)) / len(all_logs))
            kinds = {event["kind"] for log in all_logs for event in log}
            foreign = any(e["price_currency"] != _currency(log)
                          for log in all_logs for e in log if e["kind"] == "add_line")
            rates_shown = any("Exchange rates" in item["text"] for item in dataset.worker_inputs)
            if level == 1:
                self.assertLessEqual(kinds, {"create", "add_line", "set_qty"})
                self.assertTrue(all([e["ts"] for e in log] == sorted(e["ts"] for e in log) for log in all_logs))
                self.assertFalse(rates_shown)
            if level >= 3:
                self.assertIn("cancel", kinds)
                self.assertTrue(all(len({e["order_id"] for e in log}) == 2 for log in all_logs))
            self.assertEqual(level == 4, rates_shown)
            if level == 4:
                self.assertTrue(foreign)
        self.assertEqual(sizes, sorted(sizes))

    def test_counts_difficulty_and_seed_validation(self):
        self.assertEqual((), self.build("empty", 0, 0, 0, 0).worker_inputs)
        for args, kwargs in (((-1,), {}), ((0, MAX_SPLIT_COUNT + 1), {}), ((True,), {}), ((1.5,), {}),
                             ((1,), {"difficulty": 0}), ((1,), {"difficulty": 5}), ((1,), {"seed": "7"})):
            with self.subTest(args=args, kwargs=kwargs), self.assertRaises(ValueError):
                self.build("invalid", *args, **kwargs)


class VerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(dir=Path.cwd())
        cls.dataset = build_eventlog_dataset(Path(cls.temp_dir.name) / "v", 0, 0, 60, 0, difficulty=4, seed=31)

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def truth(self):
        sample_id = next(iter(self.dataset.controller_truth))
        return json.loads(json.dumps(self.dataset.controller_truth[sample_id]))

    def test_schema_and_type_errors_score_down_without_leaking_values(self):
        truth = self.truth()
        for field, value in (("total_minor", True), ("status", 1), ("lines", "SKU-1"),
                             ("lines", [{"sku": "B", "qty": 1, "unit_minor": 1}, {"sku": "A", "qty": 1, "unit_minor": 1}]),
                             ("lines", [{"sku": "A", "qty": 0, "unit_minor": 1}])):
            with self.subTest(field=field, value=value):
                candidate = dict(truth, **{field: value})
                result = verify_case(truth, candidate)
                self.assertFalse(result.accepted)
                self.assertEqual((6, 7), (result.n_passed, result.n_criteria))
                self.assertTrue(any(e.startswith(f"{field}:") for e in result.errors))
        extra = verify_case(truth, dict(truth, note="x"))
        self.assertEqual((7, 8), (extra.n_passed, extra.n_criteria))
        missing = dict(truth)
        del missing["refunded_minor"]
        self.assertIn("refunded_minor: required field is missing", verify_case(truth, missing).errors)
        for value in (None, [], "{}"):
            result = verify_case(truth, value)
            self.assertEqual((0.0, 0, 7), (result.score, result.n_passed, result.n_criteria))
        wrong = verify_case(truth, dict(truth, total_minor=truth["total_minor"] + 1))
        self.assertTrue(all(str(truth["total_minor"]) not in error for error in wrong.errors))

    def assert_fault_rejected(self, wrong_answer):
        """A plausible reasoning fault must change and be rejected on some generated case."""
        triggered = 0
        for sample_id, saved in self.dataset.controller_events.items():
            truth = self.dataset.controller_truth[sample_id]
            candidate = wrong_answer(saved)
            if candidate != truth:
                triggered += 1
                result = verify_case(truth, candidate)
                self.assertFalse(result.accepted)
                self.assertLess(result.score, 1.0)
        self.assertGreater(triggered, 0)

    def test_rejects_log_order_instead_of_timestamp_order(self):
        def fault(saved):
            # Keep create first: a worker making this mistake still knows the currency.
            ordered = sorted(saved["log"], key=lambda e: e["kind"] != "create")
            log = [dict(e, ts=f"{i:06d}") for i, e in enumerate(ordered)]
            return replay(log, saved["order_id"], saved["rates"])
        self.assert_fault_rejected(fault)

    def test_rejects_counting_duplicate_events(self):
        def fault(saved):
            log = [dict(e, event_id=f"{e['event_id']}#{i}") for i, e in enumerate(saved["log"])]
            return replay(log, saved["order_id"], saved["rates"])
        self.assert_fault_rejected(fault)

    def test_rejects_applying_distractor_order_events(self):
        def fault(saved):
            log = [dict(e, order_id=saved["order_id"]) for e in saved["log"]]
            return replay(log, saved["order_id"], saved["rates"])
        self.assert_fault_rejected(fault)

    def test_rejects_ignoring_cancellation(self):
        def fault(saved):
            log = [e for e in saved["log"] if e["kind"] != "cancel"]
            return replay(log, saved["order_id"], saved["rates"])
        self.assert_fault_rejected(fault)

    def test_rejects_uncapped_refunds(self):
        def fault(saved):
            answer = replay(saved["log"], saved["order_id"], saved["rates"])
            seen, refunds = set(), 0
            for e in saved["log"]:
                if e["order_id"] == saved["order_id"] and e["kind"] == "refund" and e["event_id"] not in seen:
                    seen.add(e["event_id"])
                    refunds += int(e["amount"].replace(".", ""))
            return dict(answer, refunded_minor=refunds)
        self.assert_fault_rejected(fault)

    def test_rejects_ignoring_exchange_rates(self):
        def fault(saved):
            log = [dict(e, price_currency=_currency(saved["log"])) if e["kind"] == "add_line" else e
                   for e in saved["log"]]
            return replay(log, saved["order_id"], saved["rates"])
        self.assert_fault_rejected(fault)


if __name__ == "__main__":
    unittest.main()
