"""Fixed S7 Stage-A trials for voicecount. Requires separate explicit user approval.

Five fresh trials: Spark (`muse-spark-1.3-contributor`) is the agent, the
improver, selector and reflector; SAM 3.1 and Muse Voice Transcribe are the
fixed tools. Each trial adapts the harness on 3 loop items, keeps the
candidate only on a strict mean gain over all 40 loop items, and checks the
5 verify items. After all trials, base and every retained package are scored
once on the frozen 40-item test split. Pre-registered criterion (29 Oct 2026):
gain >= 0.10 in at least 4 of 5 trials and no trial below -0.05.

Reviewed in docs/voicecount-trials-review.md; arbitrary inputs, endpoints and
models are not accepted.
"""
import argparse
import json
from decimal import Decimal
from pathlib import Path

from .perception import MetaToolTransport, PerceptionCache, PerceptionClient
from .providers import SAM, TRANSCRIBE
from .spark import SparkClient
from .trials import CRITERION, dataset_hash, experiment_hash, run_trials
from .usage import BudgetScope, summarize, usage_rows
from .voicecount import load_voicecount_dataset
from .voicecount_calibration import APPROVED_DATASET, APPROVED_EXPERIMENT, CATALOG

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT/".lx"/"datasets"/"voicecount-v1"
TRIALS = 5
ADAPTATION_COUNT = 3
SCOPE = "voicecount-trials-v1"
# Per transport (one per trial, one for the final test); each also has its own budget scope.
LIMITS = {"trial": {"chat": 1000, "segment": 300, "transcribe": 100, "cap_usd": "0.80"},
          "final": {"chat": 2000, "segment": 600, "transcribe": 100, "cap_usd": "1.50"}}
EXPERIMENT_CAP_USD = "5.50"
# Last recorded shared-ledger checkpoint (S6 calibration, 29 Oct 2026).
CHECKPOINT_SPENT = Decimal("0.146868398")
CHECKPOINT_CALLS = 401


def run_live(dataset, output, make_transports, ledger, *, live=True,
             cache_root=ROOT/".lx"/"perception-cache"/"voicecount-v1"):
    """Shared by the live command and the offline test (with preview transports)."""
    clients = {}

    def factory(identifier):
        kind = "final" if identifier.endswith("final-test") else "trial"
        chat, segment, transcribe = make_transports(kind)
        client = SparkClient(chat, BudgetScope(ledger, SCOPE+"/"+identifier, LIMITS[kind]["cap_usd"]), retries=0)
        clients[id(client)] = (segment, transcribe)
        return client

    def extras(client):
        segment, transcribe = clients[id(client)]
        # One cache for the experiment: an identical perception call is paid once.
        return {"perception": PerceptionClient(segment, transcribe, client.ledger, PerceptionCache(cache_root)),
                "catalog": CATALOG}
    approved = {"experiment_cap_usd": float(EXPERIMENT_CAP_USD), "per_trial_cap_usd": float(LIMITS["trial"]["cap_usd"]),
                "final_test_cap_usd": float(LIMITS["final"]["cap_usd"]), "per_trial_call_limit": LIMITS["trial"]["chat"],
                "final_test_call_limit": LIMITS["final"]["chat"], "tool_limits": LIMITS, "review": "voicecount-trials-review.md"}
    return run_trials(dataset, output, factory, trials=TRIALS, adaptation_count=ADAPTATION_COUNT, live=live,
                      task="voicecount", adapter_kwargs=extras, approved_live=approved)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-dataset", required=True,
                        help="Exact dataset SHA256, only after the user approves the dated review")
    args = parser.parse_args()
    if args.approve_dataset != APPROVED_DATASET:
        parser.error("Dataset approval does not match this fixed request")
    from .agent import agent_tools
    from .live import _runtime_key
    from .live_transport import LiveMetaTransport
    from .voicecount_probe import guarded_ledger
    dataset = load_voicecount_dataset(DATASET)
    if dataset_hash(dataset) != APPROVED_DATASET:
        raise ValueError("Dataset differs from the reviewed frozen dataset")
    if experiment_hash(dataset, "voicecount") != APPROVED_EXPERIMENT:
        raise ValueError("Contract, truth, verifier or agent code differs from the reviewed experiment")
    if CRITERION != {"trials": 5, "minimum_gain": 0.10, "required_wins": 4, "maximum_regression": 0.05}:
        raise ValueError("Success criterion differs from the pre-registered one")
    ledger, state = guarded_ledger(SCOPE)
    if Decimal(str(state["spent_usd"])) < CHECKPOINT_SPENT or state["settled_calls"] < CHECKPOINT_CALLS:
        raise ValueError("Shared pilot ledger is older than the S6 checkpoint")
    if Decimal(str(state["remaining_usd"])) < Decimal(EXPERIMENT_CAP_USD):
        raise ValueError("Not enough shared budget left for the approved experiment cap")
    output = ROOT/".lx"/SCOPE
    output.mkdir(parents=True, exist_ok=True)
    with (output/"started.json").open("x", encoding="utf-8") as marker:
        json.dump({"dataset_sha256": APPROVED_DATASET, "trials": TRIALS, "adaptation_count": ADAPTATION_COUNT,
                   "limits": LIMITS, "experiment_cap_usd": EXPERIMENT_CAP_USD}, marker)
    tools = agent_tools(CATALOG)

    def transports(kind):
        limit = LIMITS[kind]
        return (LiveMetaTransport(_runtime_key, call_limit=limit["chat"], agent_tools=tools, image_types=("jpeg",),
                                  max_image_bytes=1_000_000, call_ceiling=limit["chat"]),
                MetaToolTransport(SAM, _runtime_key, call_limit=limit["segment"]),
                MetaToolTransport(TRANSCRIBE, _runtime_key, call_limit=limit["transcribe"]))
    try:
        result = run_live(dataset, output/"run", transports, ledger)
    finally:
        usage = summarize(usage_rows(ledger, SCOPE+"/"))
        (output/"usage.json").write_text(json.dumps({"usage": usage, "ledger_before": state,
            "ledger_after": ledger.summary()}, indent=2, default=str)+"\n", encoding="utf-8")
    summary = {"criterion": result["criterion"], "retained_gain": result["retained_gain"],
               "final_test": {k: result["final_test"][k] for k in ("base_mean", "gains", "executions")},
               "retained": [t["retained"] for t in result["trials"]], "usage": usage}
    (output/"summary.json").write_text(json.dumps(summary, indent=2, default=str)+"\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
