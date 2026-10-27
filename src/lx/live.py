"""User-approved S1 live experiment: fixed synthetic fixture, endpoint and budget.

Authorization: user approved docs/live-spike-review.md on 28 October 2026.
Credential loading happens only in the application runtime. No secret values,
environment dumps or provider error bodies are printed or written to artifacts.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

from .budget import BudgetLedger
from .proposer import TOOLS
from .spark import MODEL, ModelError, RateLimited, SparkClient
from .spike import run_spike

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT.parent/"harness2_portable_core"
RUNTIME = ROOT/".lx"/"live-spike"
ENDPOINT = "https://api.meta.ai/v1/chat/completions"
FIXTURE_SHA256 = "93c8039a5ac1635a33e150b926615d69a765a367ee664b197c7ab756c3f0965d"
MAX_CALLS = 40


def _runtime_key():
    """Fixed local credential configuration; never accepts a caller-supplied file."""
    key = os.environ.get("MODEL_API_KEY") or os.environ.get("META_API_KEY")
    if not key:
        credential_file = CORE/".env"
        if credential_file.is_file():
            for line in credential_file.read_text(encoding="utf-8").splitlines():
                name, separator, value = line.strip().removeprefix("export ").partition("=")
                if separator and name.strip() in {"MODEL_API_KEY", "META_API_KEY"}:
                    key = value.strip().strip('"').strip("'")
                    if key:
                        break
    if not key:
        raise ModelError("MODEL_API_KEY or META_API_KEY is unavailable to the application runtime")
    return key


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _MetaTransport:
    def __init__(self, ledger):
        self.ledger = ledger
        self.calls = 0
        self._key = _runtime_key()
        self._opener = urllib.request.build_opener(_NoRedirect())

    def __call__(self, body, timeout):
        if body.get("model") != MODEL or body.get("tools") != TOOLS:
            raise ModelError("request outside the approved model/tool contract")
        state = self.ledger.summary()
        total = state["settled_calls"]+state["reserved_calls"]+state["unknown_count"]
        if total > MAX_CALLS:
            raise ModelError("shared live spike call limit reached")
        self.calls += 1
        request = urllib.request.Request(
            ENDPOINT, data=json.dumps(body, allow_nan=False).encode("utf-8"), method="POST",
            headers={"Authorization": "Bearer "+self._key, "Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=min(timeout, 60)) as response:
                payload = response.read(4_000_001)
                if len(payload) > 4_000_000:
                    raise ModelError("provider response exceeded the limit")
                return json.loads(payload)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                try:
                    delay = float(exc.headers.get("Retry-After", 0))
                except (TypeError, ValueError):
                    delay = 0
                raise RateLimited(delay) from None
            raise ModelError(f"provider request failed (HTTP {exc.code}); response body withheld") from None
        except (OSError, ValueError):
            raise ModelError("provider connection/JSON failed; cost remains reserved") from None


def _guard_fixture():
    source = CORE/"fixtures"/"custom_workflow.py"
    if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != FIXTURE_SHA256:
        raise ModelError("synthetic fixture differs from the approved source")
    for path in (ROOT/".lx", RUNTIME):
        if path.is_symlink():
            raise ModelError("linked live runtime directory rejected")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    # A previously supplied input directory must never become a live payload.
    for path in (RUNTIME/"run"/"sources").glob("*/records.json"):
        expected = {
            "records-1": [{"id": "A-1", "amount": "14.25"}, {"id": "A-2", "amount": "-20.10"}, {"id": "A-3", "amount": "3.85"}],
            "records-2": [{"id": "B-1", "amount": "-4.50"}, {"id": "B-2", "amount": "1.25"}, {"id": "B-3", "amount": "2.25"}],
        }.get(path.parent.name)
        if path.is_symlink() or expected is None or json.loads(path.read_text()) != {"records": expected}:
            raise ModelError("live fixture inputs differ from approved synthetic records")


def worker():
    _guard_fixture()
    ledger = BudgetLedger(RUNTIME/"pilot-budget.sqlite3")
    provider = _MetaTransport(ledger)
    before = ledger.summary()
    first = run_spike(CORE, RUNTIME/"run", SparkClient(provider, ledger), backend="docker")
    spent = ledger.summary()
    count = provider.calls
    resumed = run_spike(CORE, RUNTIME/"run", SparkClient(provider, ledger), backend="docker")
    if provider.calls != count or ledger.summary() != spent or resumed["execution_count"] != 0:
        raise ModelError("completed resume unexpectedly consumed work or budget")
    report = {"mode": "live_spark_synthetic_only", "state": "passed", "model": MODEL,
              "endpoint": ENDPOINT, "task": "records-1", "k": 1, "backend": "docker",
              "independent_check": first["independent_check"],
              "candidate_edited_and_harvested": first["candidate_edited_and_harvested"],
              "selected_run_id": first["selected_run_id"],
              "execution_count_first_pass": first["execution_count"],
              "live_calls_this_invocation": count, "resume_live_calls": provider.calls-count,
              "resume_execution_count": resumed["execution_count"],
              "ledger_before": before, "ledger_after": spent,
              "cost_basis": "provider token usage at verified Contributor list prices; not an invoice"}
    (RUNTIME/"live-report.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report, indent=2))


def main():
    if os.name != "posix":
        print("Run the live spike in the supported WSL runtime.")
        return 2
    try:
        if sys.argv[1:] == ["--worker"]:
            worker()
        elif not sys.argv[1:]:
            from h2portable.jobs import run_job
            _guard_fixture()
            inputs = RUNTIME/"job-input"
            inputs.mkdir(exist_ok=True)
            (inputs/"approval.json").write_text(json.dumps({"fixture_sha256": FIXTURE_SHA256,
                "model": MODEL, "task": "records-1", "k": 1, "cap_usd": 20, "call_limit": MAX_CALLS})+"\n")
            job = run_job("s1-spark-live", RUNTIME/"jobs", "local", inputs, 900,
                          [sys.executable, "-m", "lx.live", "--worker"])
            if job["status"] != "completed":
                print(json.dumps({"state": job["status"], "detail": "inspect controller logs in .lx/live-spike/jobs"}))
                return 1
            print((RUNTIME/"live-report.json").read_text())
        else:
            raise ModelError("the approved live spike accepts no custom paths or inputs")
        return 0
    except Exception as exc:
        # Do not expose arbitrary provider/credential exceptions in foreground logs.
        print(json.dumps({"state": "failed", "error_type": type(exc).__name__,
                          "detail": str(exc) if isinstance(exc, ModelError) else "live spike failed; inspect synthetic run artifacts"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
