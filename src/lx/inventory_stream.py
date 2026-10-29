"""Fixed T8 E-A stream for PROD-004 (inventory). Requires separate explicit user approval.

The paper's loop as published: Harness² k=1 in sequential mode over the 6
stream samples of the frozen ``inventory-mix-v1`` dataset, in order, with the
experience library carried from sample to sample. Spark
(`muse-spark-1.3-contributor`) is the agent, the improver and the reflector;
SAM 3.1 and Muse Voice Transcribe are fixed tools; skills and scripts are
editable components (scripts run in the WSL sandbox). The improver is
grade-blind: it sees trajectories, never checklist scores.

After the loop, the controller grades the base run and the chosen run of each
sample with the checklist (E-A metric: the paper's per-task improvement).
No held-out claim is made here; E-B (frozen harness on check + held-out) is a
separate request. Reviewed in docs/inventory-stream-review.md.
"""
import argparse
import json
from decimal import Decimal
from pathlib import Path

from .inventory_data import load_inventory_dataset
from .inventory_probe import CATALOG, KEEP_IMAGES, LEDGER, MAX_VIEW_BYTES, TOOLS
from .inventory_workflow import InventoryAdapter, run_stream
from .perception import MetaToolTransport, PerceptionCache, PerceptionClient
from .providers import SAM, TRANSCRIBE
from .spark import SparkClient
from .usage import BudgetScope, summarize, usage_rows

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT/".lx"/"datasets"/"inventory-mix-v1"
APPROVED_DATASET = "db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322"
SCOPE = "inventory-ea-v1"
RUN_CAP_USD = "1.50"
# Per sample about 4 worker runs (<= 19 turns each) and 3-4 improver/reflection sessions (<= 12 turns each).
LIMITS = {"chat": 900, "segment": 300, "transcribe": 40}
# Last recorded shared-ledger checkpoint (after T7, 29 Oct 2026).
CHECKPOINT_SPENT = Decimal("0.50603337")
CHECKPOINT_CALLS = 1058


def stream_samples(root=DATASET):
    """Stream samples interleaved by request type (count, colour, inventory, count, ...) and their truth."""
    samples, truth, _ = load_inventory_dataset(root)
    by_type = {}
    for s in samples:
        if s["split"] == "stream":
            by_type.setdefault(truth[s["sample_id"]]["request_type"], []).append(s["sample_id"])
    queues = [by_type[t] for t in ("count", "colour_count", "inventory") if t in by_type]
    order = [q[i] for i in range(max(map(len, queues), default=0)) for q in queues if i < len(q)]
    return order, truth


def run_ea(root, output, client, perception, *, live=False, script_runner=None, tasks=None):
    """Shared by the live command and the offline test (with preview doubles)."""
    stream, truth = stream_samples(root)
    adapter = InventoryAdapter(Path(root)/"worker_inputs", output, client, truth, perception=perception,
                               catalog=CATALOG, mode="live" if live else "offline_preview", script_runner=script_runner)
    report = run_stream(adapter, output, client, tasks=tasks or stream)
    report.update(live=live, capability_evidence=live)
    gains = {t: r["harness2"]["score"] - r["base"]["score"] for t, r in report["per_sample"].items()}
    report["gains"] = gains
    report["improved"] = sorted(t for t, g in gains.items() if g > 0)
    report["regressed"] = sorted(t for t, g in gains.items() if g < 0)
    (Path(output)/"ea_report.json").write_text(json.dumps(report, indent=2, default=str)+"\n", encoding="utf-8")
    return report


def guarded_ledger():
    if not LEDGER.is_file() or LEDGER.is_symlink():
        raise ValueError("The existing shared pilot ledger is required")
    from .budget import BudgetLedger
    ledger = BudgetLedger(LEDGER)
    state = ledger.summary()
    if Decimal(str(state["spent_usd"])) < CHECKPOINT_SPENT or state["settled_calls"] < CHECKPOINT_CALLS:
        raise ValueError("Shared pilot ledger is older than the recorded checkpoint")
    if Decimal(str(state["remaining_usd"])) < Decimal(RUN_CAP_USD):
        raise ValueError("Not enough shared budget left for the approved cap")
    if usage_rows(ledger, SCOPE+"/"):
        raise ValueError("This stream has already dispatched calls; obtain a new reviewed retry")
    return ledger, state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-dataset", required=True,
                        help="Exact dataset SHA256, only after the user approves the dated review")
    args = parser.parse_args()
    from .inventory_data import inventory_dataset_hash
    if args.approve_dataset != APPROVED_DATASET or inventory_dataset_hash(DATASET) != APPROVED_DATASET:
        parser.error("Dataset approval does not match this fixed request")
    from . import sandbox
    if not sandbox.available():
        parser.error("The script sandbox is required (run from WSL)")
    from .live import _runtime_key
    from .live_transport import LiveMetaTransport
    ledger, state = guarded_ledger()
    output = ROOT/".lx"/SCOPE
    output.mkdir(parents=True, exist_ok=True)
    # Exclusive marker prevents concurrent dispatch or accidental repeat runs.
    with (output/"started.json").open("x", encoding="utf-8") as marker:
        json.dump({"dataset_sha256": APPROVED_DATASET, "run_cap_usd": RUN_CAP_USD, **LIMITS}, marker)
    scope = BudgetScope(ledger, SCOPE, RUN_CAP_USD)
    perception = PerceptionClient(MetaToolTransport(SAM, _runtime_key, call_limit=LIMITS["segment"]),
                                  MetaToolTransport(TRANSCRIBE, _runtime_key, call_limit=LIMITS["transcribe"]),
                                  scope, PerceptionCache(ROOT/".lx"/"perception-cache"/"inventory-mix-v1"))
    # The transport accepts exactly the agent tools or the improver file tools, nothing else.
    transport = LiveMetaTransport(_runtime_key, agent_tools=TOOLS, image_types=("jpeg",), max_image_bytes=MAX_VIEW_BYTES,
                                  max_images=KEEP_IMAGES, call_limit=LIMITS["chat"], call_ceiling=LIMITS["chat"])
    client = SparkClient(transport, scope, retries=0)
    try:
        report = run_ea(DATASET, output/"run", client, perception, live=True)
    finally:
        usage = summarize(usage_rows(ledger, SCOPE+"/"))
        (output/"usage.json").write_text(json.dumps({"usage": usage, "ledger_before": state,
            "ledger_after": ledger.summary()}, indent=2, default=str)+"\n", encoding="utf-8")
    summary = {"means": report["means"], "gains": report["gains"], "improved": report["improved"],
               "regressed": report["regressed"], "run": report["run"], "usage": usage,
               "cache_hits": perception.cache.hits, "live_tool_calls": perception.live_calls}
    (output/"summary.json").write_text(json.dumps(summary, indent=2, default=str)+"\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
