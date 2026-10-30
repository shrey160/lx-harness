"""Fixed R3 event-log difficulty ladder. Requires separate explicit user approval.

The base harness alone answers 10 calibration-split cases at each difficulty
level 1-4: 40 worker calls, no improver, no candidate. No live request is made
by imports or by the offline trial command. The only live entry point is the
exact ladder reviewed in docs/eventlog-calibration-review.md; arbitrary input
files are not accepted.
"""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from statistics import mean
import tempfile

from .eventlog import FIELDS, build_eventlog_dataset
from .eventlog_workflow import EventLogAdapter
from .spark import ModelError, SparkClient
from .trials import dataset_hash, experiment_hash
from .usage import BudgetScope, summarize, usage_rows


LEVELS = (1, 2, 3, 4)
PER_LEVEL = 10
SEED = 20261029
CALL_LIMIT = len(LEVELS)*PER_LEVEL
RUN_CAP_USD = "0.50"
HEADROOM = (0.30, 0.80)
MAX_ERRORS = 3
APPROVED_DATASET = "04455ea074864b87f614c2cb595080ce464c8c6181d2b59603e95354a7502822"
APPROVED_EXPERIMENT = "4399af14c327250bb8f5b32f633f9566742dd4bf50964393b3e83be57da246ef"
SCOPE = "eventlog-calibration-v1"
# Last recorded shared-ledger checkpoint (records calibration, 28 Oct 2026).
CHECKPOINT_SPENT = Decimal("0.041283498")
CHECKPOINT_CALLS = 194


def build_ladder(root):
    """Calibration-split cases only; loop, verify and test stay unbuilt."""
    return {level: build_eventlog_dataset(Path(root)/f"level-{level}", 0, 0, 0, PER_LEVEL,
                                          difficulty=level, seed=SEED) for level in LEVELS}


def ladder_hashes(ladder):
    def combined(values):
        return hashlib.sha256(json.dumps(values).encode()).hexdigest()
    return (combined([dataset_hash(ladder[level]) for level in LEVELS]),
            combined([experiment_hash(ladder[level], "eventlog") for level in LEVELS]))


def run_ladder(ladder, output, client, *, live=False):
    """Base-only scoring per level. A model error scores 0; repeated errors stop."""
    output = Path(output)
    levels, errors = {}, []
    for level in LEVELS:
        dataset = ladder[level]
        adapter = EventLogAdapter(dataset.root/"worker_inputs", output/f"level-{level}", client,
                                  dataset.controller_truth, mode="live" if live else "offline_preview")
        scores, field_hits, output_tokens = {}, {field: 0 for field in FIELDS}, {}
        for task in adapter.tasks("calibration"):
            try:
                run = adapter.execute(task, None, "calibration/"+task)
            except ModelError as exc:
                # A truncated or malformed reply is a failed item, not a gap.
                errors.append({"level": level, "task": task, "error": str(exc)})
                scores[task] = 0.0
                if len(errors) >= MAX_ERRORS:
                    raise ModelError(f"stopped after {len(errors)} model errors") from None
                continue
            judged = adapter.score(run, task)
            scores[task] = judged["n_passed"]/judged["n_criteria"]
            answer = json.loads(adapter.deliverable_text(run))
            expected = dataset.controller_truth[task]
            for field in FIELDS:
                field_hits[field] += type(answer) is dict and answer.get(field) == expected[field]
            output_tokens[task] = json.loads((run/"execution.json").read_text(encoding="utf-8"))["usage"].get("output_tokens")
        level_mean = mean(scores.values())
        levels[level] = {"scores": scores, "mean": level_mean, "min": min(scores.values()),
                         "max": max(scores.values()), "accepted": sum(s == 1.0 for s in scores.values()),
                         "field_accuracy": {f: hits/len(scores) for f, hits in field_hits.items()},
                         "output_tokens": output_tokens,
                         "headroom": HEADROOM[0] <= level_mean <= HEADROOM[1]}
    chosen = next((level for level in LEVELS if levels[level]["headroom"]), None)
    dataset_sha, experiment_sha = ladder_hashes(ladder)
    report = {"live": live, "capability_evidence": False, "dataset_sha256": dataset_sha,
              "experiment_sha256": experiment_sha, "headroom_band": HEADROOM, "levels": levels,
              "chosen_level": chosen, "errors": errors,
              "decision": (f"use level {chosen} for R4" if chosen is not None
                           else "no level in band; revise generator (R1) before R4")}
    output.mkdir(parents=True, exist_ok=True)
    (output/"calibration.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-dataset", required=True,
                        help="Exact ladder SHA256, only after the user approves the dated review")
    args = parser.parse_args()
    if args.approve_dataset != APPROVED_DATASET:
        parser.error("Dataset approval does not match this fixed request")
    from .app import Application, ROOT
    # Refuse to initialize an empty replacement for the historical pilot ledger.
    ledger_path = ROOT/".lx"/"live-spike"/"pilot-budget.sqlite3"
    if not ledger_path.is_file() or ledger_path.is_symlink():
        raise ValueError("The existing shared pilot ledger is required")
    app = Application()
    ledger = app.live_ledger
    state = ledger.summary()
    if Decimal(str(state["spent_usd"])) < CHECKPOINT_SPENT or state["settled_calls"] < CHECKPOINT_CALLS:
        raise ValueError("Shared pilot ledger is older than the recorded checkpoint")
    if usage_rows(ledger, SCOPE+"/"):
        raise ValueError("This calibration has already dispatched calls; obtain a new reviewed retry")
    output = app.root/SCOPE
    output.mkdir(parents=True, exist_ok=True)
    # Exclusive marker prevents concurrent dispatch or accidental repeat runs.
    with (output/"started.json").open("x", encoding="utf-8") as marker:
        json.dump({"dataset_sha256": APPROVED_DATASET, "run_cap_usd": float(RUN_CAP_USD),
                   "call_limit": CALL_LIMIT}, marker)
    with tempfile.TemporaryDirectory(prefix="lx-eventlog-calibration-") as directory:
        ladder = build_ladder(directory)
        dataset_sha, experiment_sha = ladder_hashes(ladder)
        if dataset_sha != APPROVED_DATASET:
            raise ValueError("Generated inputs differ from the reviewed synthetic ladder")
        if experiment_sha != APPROVED_EXPERIMENT:
            raise ValueError("Contract, controller truth or verifier differs from the reviewed experiment")
        # Runtime-only credential access through the existing registered Spark
        # loader. Fixed Meta transport; no credentials enter files or outputs.
        client = SparkClient(app.make_live_transport(call_limit=CALL_LIMIT),
                             BudgetScope(ledger, SCOPE, RUN_CAP_USD), retries=0)
        try:
            report = run_ladder(ladder, output, client, live=True)
        finally:
            usage = summarize(usage_rows(ledger, SCOPE+"/"))
            (output/"usage.json").write_text(json.dumps({"usage": usage, "ledger_before": state,
                "ledger_after": ledger.summary()}, indent=2, default=str)+"\n", encoding="utf-8")
    report.update(usage=usage, ledger_before=state, ledger_after=ledger.summary())
    (output/"calibration.json").write_text(json.dumps(report, indent=2, default=str)+"\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("chosen_level", "decision", "errors", "usage")}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
