"""Persistent, concurrency-safe spending ledger for model calls."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class BudgetError(ValueError):
    """Invalid ledger operation or persistent configuration mismatch."""


class BudgetExceeded(BudgetError):
    """A reservation would exceed the configured cap, or actual spend did."""


_ROLES = frozenset({"orchestrator", "improver", "worker"})
_NANO = Decimal("1000000000")


def _nanos(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise BudgetError(f"{name} must be a non-negative USD amount")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise BudgetError(f"{name} must be a non-negative USD amount") from None
    if not amount.is_finite() or amount < 0:
        raise BudgetError(f"{name} must be a non-negative USD amount")
    scaled = amount * _NANO
    if scaled != scaled.to_integral_value():
        raise BudgetError(f"{name} supports precision up to nine decimal places")
    return int(scaled)


class BudgetLedger:
    """SQLite-backed budget with durable reservations and actual settlements."""

    def __init__(
        self,
        path: str | Path,
        cap_usd: Any = 20,
        initial_spend_usd: Any = "0.0010",
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        cap = _nanos(cap_usd, "cap_usd")
        initial = _nanos(initial_spend_usd, "initial_spend_usd")
        if cap > 20_000_000_000:
            raise BudgetError("cap_usd must not exceed the $20 product pilot limit")
        if initial > cap:
            raise BudgetExceeded("initial spend exceeds the configured cap")
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS ledger_config (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    cap_nanos INTEGER NOT NULL,
                    initial_nanos INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reservations (
                    id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    candidate TEXT NOT NULL,
                    reserved_nanos INTEGER NOT NULL,
                    actual_nanos INTEGER,
                    status TEXT NOT NULL CHECK(status IN ('reserved','settled','unknown')),
                    input_tokens INTEGER,
                    output_tokens INTEGER
                );
                """
            )
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT cap_nanos, initial_nanos FROM ledger_config WHERE singleton=1"
            ).fetchone()
            if existing is None:
                db.execute(
                    "INSERT INTO ledger_config VALUES (1, ?, ?)", (cap, initial)
                )
            elif tuple(existing) != (cap, initial):
                raise BudgetError(
                    "ledger already exists with a different cap or initial spend"
                )
            db.commit()

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def _role(role: str) -> str:
        if role not in _ROLES:
            raise BudgetError(f"role must be one of {', '.join(sorted(_ROLES))}")
        return role

    def reserve(self, amount_usd: Any, role: str, phase: str, candidate: str = "", *, scope_prefix=None, scope_cap_usd=None) -> str:
        amount = _nanos(amount_usd, "amount_usd")
        role = self._role(role)
        if not isinstance(phase, str) or not phase:
            raise BudgetError("phase must be a non-empty string")
        if not isinstance(candidate, str):
            raise BudgetError("candidate must be a string")
        reservation_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cfg = db.execute("SELECT * FROM ledger_config WHERE singleton=1").fetchone()
            sums = db.execute(
                "SELECT COALESCE(SUM(CASE WHEN status='settled' THEN actual_nanos "
                "ELSE 0 END),0) AS spent, "
                "COALESCE(SUM(CASE WHEN status IN ('reserved','unknown') "
                "THEN reserved_nanos ELSE 0 END),0) AS held FROM reservations"
            ).fetchone()
            overrun = db.execute(
                "SELECT 1 FROM reservations WHERE status='settled' "
                "AND actual_nanos > reserved_nanos LIMIT 1"
            ).fetchone()
            if overrun:
                db.rollback()
                raise BudgetExceeded("a settled call exceeded its reservation")
            if cfg["initial_nanos"] + sums["spent"] + sums["held"] + amount > cfg["cap_nanos"]:
                db.rollback()
                raise BudgetExceeded("reservation would exceed the configured budget cap")
            if scope_prefix is not None:
                if not isinstance(scope_prefix,str) or not candidate.startswith(scope_prefix):
                    raise BudgetError("invalid budget scope")
                scoped = db.execute("SELECT COALESCE(SUM(CASE WHEN status='settled' THEN actual_nanos ELSE reserved_nanos END),0) FROM reservations WHERE substr(candidate,1,?)=?",
                                    (len(scope_prefix),scope_prefix)).fetchone()[0]
                if scoped+amount > _nanos(scope_cap_usd,"scope_cap_usd"):
                    raise BudgetExceeded("Approved run budget has no room for another reservation")
            db.execute(
                "INSERT INTO reservations "
                "(id,role,phase,candidate,reserved_nanos,status) VALUES (?,?,?,?,?,'reserved')",
                (reservation_id, role, phase, candidate, amount),
            )
            db.commit()
        return reservation_id

    def settle(
        self,
        reservation_id: str,
        *,
        cost_usd: Any,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        actual = _nanos(cost_usd, "cost_usd")
        in_tokens = self._tokens(input_tokens, "input_tokens")
        out_tokens = self._tokens(output_tokens, "output_tokens")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM reservations WHERE id=?", (reservation_id,)).fetchone()
            if row is None:
                db.rollback()
                raise BudgetError("unknown reservation id")
            if row["status"] != "reserved":
                db.rollback()
                raise BudgetError("reservation has already been settled or marked unknown")
            db.execute(
                "UPDATE reservations SET status='settled', actual_nanos=?, "
                "input_tokens=?, output_tokens=? WHERE id=?",
                (actual, in_tokens, out_tokens, reservation_id),
            )
            db.commit()
        state = self.summary()["error_state"]
        if state == "actual_cost_exceeded_reservation":
            raise BudgetExceeded("actual cost exceeded its reservation")
        if state == "actual_spend_exceeded_cap":
            raise BudgetExceeded("actual spend exceeded the configured budget cap")

    def unknown(self, reservation_id: str) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM reservations WHERE id=?", (reservation_id,)).fetchone()
            if row is None:
                db.rollback()
                raise BudgetError("unknown reservation id")
            if row["status"] != "reserved":
                db.rollback()
                raise BudgetError("reservation has already been settled or marked unknown")
            db.execute("UPDATE reservations SET status='unknown' WHERE id=?", (reservation_id,))
            db.commit()

    @staticmethod
    def _tokens(value: int, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise BudgetError(f"{name} must be a non-negative integer")
        return value

    def summary(self) -> dict[str, Any]:
        with self._connect() as db:
            cfg = db.execute("SELECT * FROM ledger_config WHERE singleton=1").fetchone()
            rows = db.execute(
                "SELECT role, status, reserved_nanos, actual_nanos, "
                "input_tokens, output_tokens FROM reservations"
            ).fetchall()
        spent = cfg["initial_nanos"] + sum(
            r["actual_nanos"] or 0 for r in rows if r["status"] == "settled"
        )
        reserved = sum(
            r["reserved_nanos"] for r in rows if r["status"] in ("reserved", "unknown")
        )
        cap = cfg["cap_nanos"]
        per_role: dict[str, dict[str, Any]] = {
            role: {
                "spent_usd": 0.0,
                "reserved_usd": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "settled_calls": 0,
                "reserved_calls": 0,
            }
            for role in sorted(_ROLES)
        }
        role_spent = {role: 0 for role in _ROLES}
        role_reserved = {role: 0 for role in _ROLES}
        for r in rows:
            usage = per_role[r["role"]]
            if r["status"] == "settled":
                role_spent[r["role"]] += r["actual_nanos"] or 0
                usage["input_tokens"] += r["input_tokens"] or 0
                usage["output_tokens"] += r["output_tokens"] or 0
                usage["settled_calls"] += 1
            elif r["status"] in ("reserved", "unknown"):
                role_reserved[r["role"]] += r["reserved_nanos"]
                if r["status"] == "reserved":
                    usage["reserved_calls"] += 1
        for role, usage in per_role.items():
            usage["spent_usd"] = role_spent[role] / 1_000_000_000
            usage["reserved_usd"] = role_reserved[role] / 1_000_000_000
        over = spent + reserved > cap
        reservation_overrun = any(
            r["status"] == "settled" and r["actual_nanos"] > r["reserved_nanos"]
            for r in rows
        )
        return {
            "cap_usd": cap / 1_000_000_000,
            "spent_usd": spent / 1_000_000_000,
            "reserved_usd": reserved / 1_000_000_000,
            "remaining_usd": max(0, cap - spent - reserved) / 1_000_000_000,
            "settled_calls": sum(r["status"] == "settled" for r in rows),
            "reserved_calls": sum(r["status"] == "reserved" for r in rows),
            "unknown_count": sum(r["status"] == "unknown" for r in rows),
            "per_role": per_role,
            "over_budget": over,
            "error_state": (
                "actual_cost_exceeded_reservation"
                if reservation_overrun
                else "actual_spend_exceeded_cap" if over else None
            ),
        }
