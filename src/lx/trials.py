"""Offline trial and calibration mechanics. CLI never loads keys or uses network.

Live calibration uses its separately reviewed fixed controller. No arbitrary
dataset/network/credential flags are provided by this offline command.
"""
import argparse
import hashlib
import json
import inspect
from pathlib import Path
from statistics import mean
import uuid

from .budget import BudgetLedger
from .records import build_records_dataset
from .records_workflow import CONTRACT, RecordsAdapter, RecordsDomain, RecordsPreviewTransport
from .spark import SparkClient
from .usage import BudgetScope, summarize, usage_rows
from .workflow import optimize_adapter


CRITERION = {"trials": 5, "minimum_gain": 0.10, "required_wins": 4, "maximum_regression": 0.05}


def dataset_hash(dataset):
    return hashlib.sha256(json.dumps(list(dataset.worker_inputs), sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def experiment_hash(dataset):
    from . import records
    value = {"inputs": dataset_hash(dataset), "truth": dataset.controller_truth,
             "contract": CONTRACT, "base_prompt": RecordsDomain.base_prompt,
             "message_builder": inspect.getsource(RecordsDomain.messages),
             "verifier_source": inspect.getsource(records)}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def metrics(values):
    values = list(values)
    return {"mean": mean(values), "min": min(values), "max": max(values)}


def criterion_result(gains):
    return {"wins": sum(g >= CRITERION["minimum_gain"]-1e-12 for g in gains),
            "passed": len(gains) == CRITERION["trials"]
                and sum(g >= CRITERION["minimum_gain"]-1e-12 for g in gains) >= CRITERION["required_wins"]
                and min(gains) >= -CRITERION["maximum_regression"]-1e-12}


def calibrate(dataset, output, client, *, live=False):
    """Base-only held-out measurement; no proposer or candidate construction."""
    adapter = RecordsAdapter(dataset.root/"worker_inputs", output, client,
                             dataset.controller_truth, mode="live" if live else "offline_preview")
    scores = {}
    for task in adapter.tasks("heldout"):
        run = adapter.execute(task, None, "calibration/"+task)
        judged = adapter.score(run, task)
        scores[task] = judged["n_passed"]/judged["n_criteria"]
    if not scores:
        raise ValueError("Calibration requires held-out items")
    report = {"live": live, "dataset_sha256": dataset_hash(dataset), "experiment_sha256": experiment_hash(dataset), "scores": scores,
              "summary": metrics(scores.values()), "headroom": 0.1 <= mean(scores.values()) <= 0.9,
              "capability_evidence": live}
    (Path(output)/"calibration.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    return report


def run_trials(dataset, output, client_factory, *, trials=5, adaptation_count=1, live=False):
    """Fresh optimization state per trial; shared inputs and fixed held-out set.

    One development item trains the harness by default; all development items
    decide retention. This keeps the bounded transport budget feasible. No
    judge result is inserted into the core's blind adaptation contexts.
    """
    if live:
        raise ValueError("Live trial activation needs a passed calibration and separate E4b approval")
    if type(trials) is not int or not 1 <= trials <= 5:
        raise ValueError("Use five trials for a full experiment; offline smoke checks may use 1..5")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if (output/"trials.json").exists():
        raise ValueError("Use a fresh experiment output directory")
    experiment = uuid.uuid4().hex
    spec = {"task": "noisy_order_normalization", "dataset_sha256": dataset_hash(dataset),
            "experiment_sha256": experiment_hash(dataset),
            "criterion": CRITERION, "adaptation_count": adaptation_count,
            "experiment_cap_usd": 2, "per_trial_cap_usd": 0.4,
            "per_trial_call_limit": 200, "experiment_call_limit": 200*trials,
            "mode": "live" if live else "offline_preview"}
    reports = []
    for index in range(trials):
        identifier = f"records-{experiment}-{index+1}"
        root = output/identifier
        client = client_factory(identifier)
        adapter = RecordsAdapter(dataset.root/"worker_inputs", root, client,
                                 dataset.controller_truth, mode=spec["mode"])
        report = optimize_adapter(adapter, root, client, spec, live=live, adaptation_count=adaptation_count)
        ledger = client.ledger.ledger if isinstance(client.ledger, BudgetScope) else client.ledger
        report["usage"] = summarize(usage_rows(ledger, client.ledger.prefix if isinstance(client.ledger, BudgetScope) else ""))
        report["trial_id"] = identifier
        report["candidate_gain"] = report["means"]["candidate_heldout"]-report["means"]["base_heldout"]
        report["retained_gain"] = report["means"]["retained_heldout"]-report["means"]["base_heldout"]
        (root/"package"/"result.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
        reports.append(report)
        # Preserve completed trials even if a later trial fails or is stopped.
        (output/"partial-trials.json").write_text(json.dumps({"spec": spec, "trials": reports}, indent=2)+"\n", encoding="utf-8")
    gains = [r["retained_gain"] for r in reports]
    result = {"spec": spec, "live": live, "capability_evidence": live, "trials": reports,
              "retained_gain": metrics(gains), "candidate_gain": metrics(r["candidate_gain"] for r in reports),
              "criterion": criterion_result(gains), "live_success": live and criterion_result(gains)["passed"],
              "usage_basis": "reported token usage" if live else "simulated usage; zero live calls and spend"}
    (output/"trials.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--dev-count", type=int, default=20)
    parser.add_argument("--heldout-count", type=int, default=40)
    parser.add_argument("--adaptation-count", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20261028)
    args = parser.parse_args()
    if not 1 <= args.trials <= 5 or not 1 <= args.adaptation_count <= args.dev_count or args.heldout_count < 1:
        parser.error("Use 1..5 trials, positive held-out count, and 1..dev-count adaptation items")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Use an empty output directory")
    dataset = build_records_dataset(args.output/"dataset", args.dev_count, args.heldout_count, args.seed)
    ledger = BudgetLedger(args.output/"simulated-usage.sqlite3", initial_spend_usd=0)
    def factory(identifier):
        return SparkClient(RecordsPreviewTransport(), BudgetScope(ledger, identifier, "0.4"))
    result = run_trials(dataset, args.output, factory, trials=args.trials, adaptation_count=args.adaptation_count)
    print(json.dumps({"live": False, "dataset_sha256": dataset_hash(dataset),
                      "trials": len(result["trials"]), "criterion": result["criterion"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
