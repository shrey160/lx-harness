"""Tests for durable, concurrent budget accounting."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from lx.budget import BudgetError, BudgetExceeded, BudgetLedger


class BudgetLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.tempdir.cleanup)
        self.path = Path(self.tempdir.name) / "budget.sqlite3"

    def test_restart_preserves_reservations_unknown_and_configuration(self):
        ledger = BudgetLedger(self.path, cap_usd=2, initial_spend_usd="0.0010")
        reservation = ledger.reserve("0.50", "worker", "execute", "candidate-1")
        ledger.unknown(reservation)

        reopened = BudgetLedger(self.path, cap_usd=2, initial_spend_usd="0.0010")
        summary = reopened.summary()
        self.assertEqual(summary["spent_usd"], 0.001)
        self.assertEqual(summary["reserved_usd"], 0.5)
        self.assertEqual(summary["unknown_count"], 1)
        self.assertEqual(summary["remaining_usd"], 1.499)
        with self.assertRaisesRegex(BudgetError, "different cap"):
            BudgetLedger(self.path, cap_usd=3, initial_spend_usd="0.0010")
        with self.assertRaisesRegex(BudgetError, "different cap"):
            BudgetLedger(self.path, cap_usd=2, initial_spend_usd="0")

    def test_two_connections_cannot_over_reserve_shared_cap(self):
        first = BudgetLedger(self.path, cap_usd=1, initial_spend_usd=0)
        second = BudgetLedger(self.path, cap_usd=1, initial_spend_usd=0)

        def attempt(ledger):
            try:
                return ledger.reserve("0.75", "worker", "execute")
            except BudgetExceeded:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, (first, second)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(first.summary()["reserved_usd"], 0.75)

    def test_invalid_amounts_are_rejected(self):
        ledger = BudgetLedger(self.path)
        for amount in (-1, True, float("nan"), float("inf"), "bad", "0.0000000001"):
            with self.subTest(amount=amount), self.assertRaises(BudgetError):
                ledger.reserve(amount, "worker", "execute")
        with self.assertRaises(BudgetError):
            BudgetLedger(Path(self.tempdir.name) / "too-large.sqlite3", cap_usd=20.000000001)

    def test_unknown_roles_are_rejected(self):
        ledger = BudgetLedger(self.path)
        for role in ("selector", "", None):
            with self.subTest(role=role), self.assertRaises(BudgetError):
                ledger.reserve("0.1", role, "execute")

    def test_nine_decimal_settlement_usage_and_duplicate_rejection(self):
        ledger = BudgetLedger(self.path, cap_usd=3, initial_spend_usd=0)
        reservation = ledger.reserve("0.000001000", "improver", "refine", "c2")
        ledger.settle(
            reservation,
            cost_usd="0.000000002",
            input_tokens=1_000_000,
            output_tokens=32,
        )
        summary = ledger.summary()
        self.assertEqual(summary["spent_usd"], 0.000000002)
        self.assertEqual(summary["reserved_usd"], 0)
        self.assertEqual(summary["per_role"]["improver"], {
            "spent_usd": 0.000000002,
            "reserved_usd": 0.0,
            "input_tokens": 1_000_000,
            "output_tokens": 32,
            "settled_calls": 1,
            "reserved_calls": 0,
        })
        with self.assertRaisesRegex(BudgetError, "already been"):
            ledger.settle(reservation, cost_usd=0.1, input_tokens=0, output_tokens=0)

    def test_actual_cost_over_reservation_sets_error_and_blocks_calls(self):
        ledger = BudgetLedger(self.path, cap_usd=1, initial_spend_usd=0)
        reservation = ledger.reserve("0.25", "orchestrator", "plan")
        with self.assertRaisesRegex(BudgetExceeded, "actual cost"):
            ledger.settle(reservation, cost_usd="1.10", input_tokens=2, output_tokens=1)
        summary = ledger.summary()
        self.assertEqual(summary["spent_usd"], 1.1)
        self.assertTrue(summary["over_budget"])
        self.assertEqual(summary["error_state"], "actual_cost_exceeded_reservation")
        with self.assertRaises(BudgetExceeded):
            ledger.reserve("0", "worker", "execute")

    def test_reservation_overrun_blocks_calls_even_when_cap_remains(self):
        ledger = BudgetLedger(self.path, cap_usd=5, initial_spend_usd=0)
        reservation = ledger.reserve("0.25", "worker", "execute")
        with self.assertRaisesRegex(BudgetExceeded, "actual cost"):
            ledger.settle(reservation, cost_usd="0.50", input_tokens=0, output_tokens=0)
        summary = ledger.summary()
        self.assertEqual(summary["remaining_usd"], 4.5)
        self.assertEqual(summary["error_state"], "actual_cost_exceeded_reservation")
        with self.assertRaisesRegex(BudgetExceeded, "exceeded its reservation"):
            ledger.reserve("0.1", "worker", "execute")

    def test_invalid_token_counts_are_rejected(self):
        ledger = BudgetLedger(self.path)
        reservation = ledger.reserve("0.1", "worker", "execute")
        for tokens in (-1, True, 1.5):
            with self.subTest(tokens=tokens), self.assertRaises(BudgetError):
                ledger.settle(reservation, cost_usd="0.1", input_tokens=tokens, output_tokens=0)


if __name__ == "__main__":
    unittest.main()
