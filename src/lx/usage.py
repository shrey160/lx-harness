"""Scope the shared durable pilot ledger to an approved run budget."""
from decimal import Decimal
import sqlite3

from .budget import BudgetExceeded


def usage_rows(ledger, prefix=""):
    with sqlite3.connect(ledger.path, timeout=30) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT id,role,phase,candidate,status,reserved_nanos,actual_nanos,input_tokens,output_tokens FROM reservations ORDER BY rowid").fetchall()
    return [dict(r) for r in rows if r["candidate"].startswith(prefix)]


def summarize(rows):
    roles = {}
    for role in ("orchestrator","improver","worker"):
        matching = [r for r in rows if r["role"]==role]
        roles[role] = {"input_tokens":sum(r["input_tokens"] or 0 for r in matching),
            "output_tokens":sum(r["output_tokens"] or 0 for r in matching),
            "spent_usd":sum(r["actual_nanos"] or 0 for r in matching if r["status"]=="settled")/1_000_000_000}
    return {"per_role":roles,"settled_calls":sum(r["status"]=="settled" for r in rows),
            "spent_usd":sum(r["actual_nanos"] or 0 for r in rows if r["status"]=="settled")/1_000_000_000}


class BudgetScope:
    """One controller per job; the global ledger admits all calls atomically."""
    def __init__(self, ledger, identifier, cap):
        self.ledger, self.prefix, self.cap = ledger, identifier+"/", Decimal(str(cap))

    def reserve(self, amount, role, phase, candidate=""):
        return self.ledger.reserve(amount, role, phase, self.prefix+candidate,
                                   scope_prefix=self.prefix,scope_cap_usd=self.cap)

    def settle(self, *args, **kwargs):
        return self.ledger.settle(*args, **kwargs)

    def unknown(self, *args, **kwargs):
        return self.ledger.unknown(*args, **kwargs)

    def summary(self):
        return self.ledger.summary()

