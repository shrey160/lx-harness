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
from .eventlog import build_eventlog_dataset
from .eventlog_workflow import EventLogAdapter, EventLogDomain, EventLogPreviewTransport
from .records import build_records_dataset
from .records_workflow import CONTRACT, RecordsAdapter, RecordsDomain, RecordsPreviewTransport
from .spark import ChatClient
from .usage import BudgetScope, summarize, usage_rows
from .voicecount import load_voicecount_dataset
from .voicecount_workflow import VoiceCountAdapter, VoiceCountDomain, VoiceCountPreviewTransport, preview_perception
from .workflow import optimize_adapter


CRITERION = {"trials": 5, "minimum_gain": 0.10, "required_wins": 4, "maximum_regression": 0.05}


def dataset_hash(dataset):
    return hashlib.sha256(json.dumps(list(dataset.worker_inputs), sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


# Task kits: adapter class, domain and report label.
TASKS = {
    "records": (RecordsAdapter, RecordsDomain, "noisy_order_normalization"),
    "eventlog": (EventLogAdapter, EventLogDomain, "order_event_log_reconciliation"),
    "voicecount": (VoiceCountAdapter, VoiceCountDomain, "spoken_photo_counting"),
}
# Tasks whose success is judged once on a frozen test split after all trials.
FINAL_TEST_TASKS = ("eventlog", "voicecount")


def experiment_hash(dataset, task="records"):
    from . import eventlog, records
    if task == "records":
        # Unchanged value: the approved records calibration hash depends on it.
        value = {"inputs": dataset_hash(dataset), "truth": dataset.controller_truth,
                 "contract": CONTRACT, "base_prompt": RecordsDomain.base_prompt,
                 "message_builder": inspect.getsource(RecordsDomain.messages),
                 "verifier_source": inspect.getsource(records)}
    elif task == "voicecount":
        from . import voicecount, voicecount_workflow, agent
        media = json.loads((dataset.root/"controller_truth"/"media.json").read_text(encoding="utf-8"))
        value = {"inputs": dataset_hash(dataset), "media": media, "truth": dataset.controller_truth,
                 "contract": VoiceCountDomain.task_prompt, "base_prompt": VoiceCountDomain.base_prompt,
                 "message_builder": inspect.getsource(VoiceCountDomain.messages),
                 "verifier_source": inspect.getsource(voicecount),
                 "agent_source": inspect.getsource(agent), "caps": voicecount_workflow.CAPS}
    else:
        value = {"inputs": dataset_hash(dataset), "truth": dataset.controller_truth,
                 "difficulty": dataset.difficulty, "contract": EventLogDomain.task_prompt,
                 "base_prompt": EventLogDomain.base_prompt,
                 "message_builder": inspect.getsource(EventLogDomain.messages),
                 "verifier_source": inspect.getsource(eventlog)}
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


def final_test(dataset, output, client, packages, *, mode="offline_preview", task="eventlog", adapter_kwargs=None):
    """Score base and each retained package once on the frozen test split.

    Runs after every trial has finished, so test grades cannot influence any
    adaptation or retention decision. A package identical to base reuses the
    base scores instead of spending calls on a duplicate evaluation.
    """
    from harness2.core import tree
    adapter = TASKS[task][0](dataset.root/"worker_inputs", output, client, dataset.controller_truth, mode=mode,
                             **(adapter_kwargs or {}))
    tasks = adapter.tasks("test")
    if not tasks:
        raise ValueError("The final test requires test items")
    surface = adapter.substrate.surface()
    base_tree = tree.read_tree(surface, adapter.base_harness())

    def scores_for(harness, label):
        result = {}
        for task in tasks:
            run = adapter.execute(task, harness, f"final-test/{label}/{task}")
            judged = adapter.score(run, task)
            result[task] = judged["n_passed"]/judged["n_criteria"]
        return result

    base = scores_for(None, "base")
    package_scores = {}
    for label, harness in packages.items():
        same = tree.read_tree(surface, harness) == base_tree
        package_scores[label] = base if same else scores_for(harness, label)
    return {"base": base, "packages": package_scores, "base_mean": mean(base.values()),
            "gains": {label: mean(values.values())-mean(base.values()) for label, values in package_scores.items()},
            "executions": adapter.executions}


def run_trials(dataset, output, client_factory, *, trials=5, adaptation_count=1, live=False, task="records",
               adapter_kwargs=None, approved_live=None):
    """Fresh optimization state per trial; shared inputs and fixed held-out set.

    One development item trains the harness by default; all development items
    decide retention. This keeps the bounded transport budget feasible. No
    judge result is inserted into the core's blind adaptation contexts. For the
    event-log task, loop items are the development set, verify items are the
    per-trial held-out check, and the criterion uses the frozen test split.
    """
    if task not in TASKS:
        raise ValueError(f"task must be one of {sorted(TASKS)}")
    adapter_cls, _, label = TASKS[task]
    # Live trials run only through a reviewed controller that passes its fixed
    # limits (currently lx.voicecount_trials, S7); every other path stays offline.
    if live and not (task == "voicecount" and isinstance(approved_live, dict)):
        raise ValueError("Live trial activation needs a passed calibration and separate E4b approval")
    if type(trials) is not int or not 1 <= trials <= 5:
        raise ValueError("Use five trials for a full experiment; offline smoke checks may use 1..5")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if (output/"trials.json").exists():
        raise ValueError("Use a fresh experiment output directory")
    experiment = uuid.uuid4().hex
    spec = {"task": label, "dataset_sha256": dataset_hash(dataset),
            "experiment_sha256": experiment_hash(dataset, task),
            "criterion": CRITERION, "adaptation_count": adaptation_count,
            "experiment_cap_usd": 2, "per_trial_cap_usd": 0.4,
            "per_trial_call_limit": 200, "experiment_call_limit": 200*trials,
            "mode": "live" if live else "offline_preview"}
    if approved_live:
        spec.update(approved_live)
    reports = []
    for index in range(trials):
        identifier = f"{task}-{experiment}-{index+1}"
        root = output/identifier
        client = client_factory(identifier)
        # adapter_kwargs(client) supplies task extras, e.g. voicecount's perception tools.
        adapter = adapter_cls(dataset.root/"worker_inputs", root, client,
                              dataset.controller_truth, mode=spec["mode"],
                              **(adapter_kwargs(client) if adapter_kwargs else {}))
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
    test = None
    if task in FINAL_TEST_TASKS:
        identifier = f"{task}-{experiment}-final-test"
        client = client_factory(identifier)
        test = final_test(dataset, output/identifier, client,
                          {r["trial_id"]: output/r["trial_id"]/"package"/"harness" for r in reports}, mode=spec["mode"],
                          task=task, adapter_kwargs=adapter_kwargs(client) if adapter_kwargs else None)
        ledger = client.ledger.ledger if isinstance(client.ledger, BudgetScope) else client.ledger
        test["usage"] = summarize(usage_rows(ledger, client.ledger.prefix if isinstance(client.ledger, BudgetScope) else ""))
        # The verify split is only a per-trial smoke check; success is judged on test.
        spec["criterion_basis"] = "final test split, retained package vs base"
        gains = [test["gains"][r["trial_id"]] for r in reports]
    result = {"spec": spec, "live": live, "capability_evidence": live, "trials": reports, "final_test": test,
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
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--task", choices=sorted(TASKS), default="records")
    parser.add_argument("--loop-count", type=int, default=40, help="event-log task only")
    parser.add_argument("--verify-count", type=int, default=5, help="event-log task only")
    parser.add_argument("--test-count", type=int, default=40, help="event-log task only")
    parser.add_argument("--difficulty", type=int, default=3, help="event-log task only")
    parser.add_argument("--dataset", type=Path, help="voicecount task only: a frozen dataset built offline")
    args = parser.parse_args()
    if args.task == "voicecount":
        return _voicecount_preview(parser, args)
    development, heldout = ((args.loop_count, args.verify_count) if args.task == "eventlog"
                            else (args.dev_count, args.heldout_count))
    if not 1 <= args.trials <= 5 or not 1 <= args.adaptation_count <= development or heldout < 1:
        parser.error("Use 1..5 trials, positive held-out count, and 1..dev-count adaptation items")
    if args.task == "eventlog" and args.test_count < 1:
        parser.error("The event-log task needs a positive test count")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Use an empty output directory")
    if args.task == "eventlog":
        dataset = build_eventlog_dataset(args.output/"dataset", args.loop_count, args.verify_count, args.test_count,
                                         difficulty=args.difficulty, seed=20261029 if args.seed is None else args.seed)
        transport = EventLogPreviewTransport
    else:
        dataset = build_records_dataset(args.output/"dataset", args.dev_count, args.heldout_count,
                                        20261028 if args.seed is None else args.seed)
        transport = RecordsPreviewTransport
    ledger = BudgetLedger(args.output/"simulated-usage.sqlite3", initial_spend_usd=0)
    def factory(identifier):
        return ChatClient(transport(), BudgetScope(ledger, identifier, "0.4"))
    result = run_trials(dataset, args.output, factory, trials=args.trials, adaptation_count=args.adaptation_count,
                        task=args.task)
    print(json.dumps({"live": False, "dataset_sha256": dataset_hash(dataset),
                      "trials": len(result["trials"]), "criterion": result["criterion"]}, indent=2))
    return 0


VOICECOUNT_CATALOG = [
    {"kind": "segmentation", "name": "SAM 3.1", "capabilities": [
        "Image segmentation: finds every instance of a short noun phrase in a photo and returns one box and mask per instance"]},
    {"kind": "transcription", "name": "Muse Voice Transcribe", "capabilities": [
        "Speech-to-text: transcribes spoken audio (mono WAV) into text"]},
]


def _voicecount_preview(parser, args):
    """Offline voicecount trials with wire-format doubles for agent and tools."""
    from .perception import PerceptionCache, PerceptionClient
    if args.dataset is None:
        parser.error("--dataset is required for the voicecount task")
    if not 1 <= args.trials <= 5 or not 1 <= args.adaptation_count:
        parser.error("Use 1..5 trials and a positive adaptation count")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Use an empty output directory")
    dataset = load_voicecount_dataset(args.dataset)
    ledger = BudgetLedger(args.output/"simulated-usage.sqlite3", initial_spend_usd=0)
    segmenter, transcriber = preview_perception(dataset)
    transports = []

    def factory(identifier):
        transports.append(VoiceCountPreviewTransport())
        return ChatClient(transports[-1], BudgetScope(ledger, identifier, "5"))

    def extras(client):
        return {"perception": PerceptionClient(segmenter, transcriber, client.ledger,
                                               PerceptionCache(args.output/"perception-cache")),
                "catalog": VOICECOUNT_CATALOG}
    result = run_trials(dataset, args.output, factory, trials=args.trials, adaptation_count=args.adaptation_count,
                        task="voicecount", adapter_kwargs=extras)
    calls = {kind: sum(t.calls[kind] for t in transports) for kind in ("worker", "improver", "reflection")}
    measurement = {"trials": args.trials, "adaptation_count": args.adaptation_count, "chat_calls": calls,
                   "tool_calls_live": {"segment": segmenter.calls, "transcribe": transcriber.calls},
                   "usage": summarize(usage_rows(ledger)), "criterion": result["criterion"],
                   "final_test": {k: result["final_test"][k] for k in ("base_mean", "gains", "executions")}}
    (args.output/"call-measurement.json").write_text(json.dumps(measurement, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"live": False, "dataset_sha256": dataset_hash(dataset), **measurement}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
