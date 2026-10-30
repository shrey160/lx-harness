"""How a run is really going: answers, improver sessions and why any failed.

These facts used to sit only in audit files, so a run could spend its budget
with nothing working and still look busy. The record is a small file beside the
run; it holds counts and our own failure messages, never sample content. A run
whose agent keeps giving no answer is stopped through the core's own stop signal.
"""
from __future__ import annotations

from pathlib import Path
import threading

from harness2.core.events import Stopped

from .workspace import read_json, write_json


class StoppedEarly(Stopped):
    """Nothing is working, so more calls would only spend the budget."""


def read_health(path):
    return read_json(path) if Path(path).is_file() else None


class RunHealth:
    def __init__(self, path, *, stop_after):
        self.path, self.stop_after = Path(path), stop_after
        self.in_a_row, self._lock = 0, threading.Lock()
        self.data = read_health(path) or {"agent": {"answered": 0, "no_answer": 0, "reasons": {}},
                                          "improver": {"ok": 0, "failed": 0, "reasons": {}}}

    def _count(self, section, reason):
        reasons = self.data[section]["reasons"]
        reasons[reason] = reasons.get(reason, 0)+1

    def agent(self, answered, reason=None):
        """Record one agent run; raises StoppedEarly at the limit of empty runs in a row."""
        with self._lock:
            self.data["agent"]["answered" if answered else "no_answer"] += 1
            self.in_a_row = 0 if answered else self.in_a_row+1
            if reason:
                self._count("agent", reason)
            stop = self.in_a_row >= self.stop_after
            if stop:
                self.data["stopped_early"] = (f"Stopped early: {self.in_a_row} agent runs in a row gave no answer. "
                                              f"Latest reason: {reason or 'empty reply'}.")
            write_json(self.path, self.data)
        if stop:
            raise StoppedEarly(self.data["stopped_early"])

    def improver(self, failure=None):
        with self._lock:
            self.data["improver"]["failed" if failure else "ok"] += 1
            if failure:
                self._count("improver", failure)
            write_json(self.path, self.data)
