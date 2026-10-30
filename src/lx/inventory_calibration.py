"""Fixed T7 inventory calibration (PROD-004). Requires separate explicit user approval.

One base arm, no improver and no candidate: the Spark agent with the base
harness (empty guardrails, memory and tool guidance; no skills or scripts) on
the 6 calibration samples of the frozen ``inventory-mix-v1`` dataset (2 count
from FSC-147, 2 colour and 2 inventory from cutout composites). The request
is reachable only as audio through Transcribe; photos through SAM, count_boxes
and view.

Gate (pre-registered in the subplan): mean checklist score in 0.30-0.80. Above
the band the task is too easy for the loop to show gains; below it the traces
are read for broken tool binding before anything else. Reviewed in
docs/inventory-calibration-review.md; arbitrary inputs, endpoints and models
are not accepted.
"""
import argparse
import json
from decimal import Decimal
from pathlib import Path
from statistics import mean

from .inventory import answer_correct
from .inventory_probe import KEEP_IMAGES, LEDGER, MAX_VIEW_BYTES, TOOLS, Probe, base_harness
from .perception import MetaToolTransport, PerceptionCache, PerceptionClient
from .providers import SAM, TRANSCRIBE
from .spark import ModelError, ChatClient
from .usage import BudgetScope, summarize, usage_rows

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT/".lx"/"datasets"/"inventory-mix-v1"
APPROVED_DATASET = "db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322"
SCOPE = "inventory-calibration-v1"
RUN_CAP_USD = "0.60"
# caps(5) allows 19 turns and 36 tool calls per sample; most runs use far fewer.
LIMITS = {"chat": 120, "segment": 120, "transcribe": 8}
HEADROOM = (0.30, 0.80)
MAX_ERRORS = 3
# Last recorded shared-ledger checkpoint (after T6 part local, 29 Oct 2026).
CHECKPOINT_SPENT = Decimal("0.36728662")
CHECKPOINT_CALLS = 941


def run_calibration(probe, output, client, perception, *, items=None, live=False):
    """Base-harness score per calibration sample; a model error scores 0 and repeated errors stop."""
    output = Path(output)
    items = items or [i for i, s in probe.samples.items() if s["split"] == "calibration"]
    results, errors = {}, []
    for item in items:
        result = probe.agent(client, perception, item, base_harness())
        if "error" in result:
            errors.append({"item": item, "error": result["error"]})
            results[item] = {"score": 0.0, "correct": False, **result}
            if len(errors) >= MAX_ERRORS:
                break
            continue
        results[item] = dict(result, request_type=probe.truth[item]["request_type"],
                             correct=answer_correct(probe.truth[item], result["answer"]))
    scores = [r["score"] for r in results.values()]
    by_type = {}
    for item, result in results.items():
        by_type.setdefault(probe.truth[item]["request_type"], []).append(result["score"])
    score = mean(scores) if scores else 0.0
    complete = len(results) == len(items) and len(errors) < MAX_ERRORS
    report = {"live": live, "capability_evidence": live, "items": items, "results": results, "errors": errors,
              "summary": {"mean": score, "correct": sum(r["correct"] for r in results.values()), "items": len(results),
                          "by_type": {k: mean(v) for k, v in sorted(by_type.items())}},
              "headroom_band": HEADROOM, "headroom": complete and HEADROOM[0] <= score <= HEADROOM[1],
              "decision": ("stopped on model errors: inspect before any retry" if not complete
                           else "in band: prepare T8 (E-A stream)" if HEADROOM[0] <= score <= HEADROOM[1]
                           else "above band: make samples harder offline" if score > HEADROOM[1]
                           else "below band: inspect traces for broken tool binding before any change")}
    output.mkdir(parents=True, exist_ok=True)
    (output/"calibration.json").write_text(json.dumps(report, indent=2, default=str)+"\n", encoding="utf-8")
    return report


def guarded_ledger():
    if not LEDGER.is_file() or LEDGER.is_symlink():
        raise ValueError("The existing shared pilot ledger is required")
    from .budget import BudgetLedger
    ledger = BudgetLedger(LEDGER)
    state = ledger.summary()
    if Decimal(str(state["spent_usd"])) < CHECKPOINT_SPENT or state["settled_calls"] < CHECKPOINT_CALLS:
        raise ValueError("Shared pilot ledger is older than the recorded checkpoint")
    if usage_rows(ledger, SCOPE+"/"):
        raise ValueError("This calibration has already dispatched calls; obtain a new reviewed retry")
    return ledger, state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-dataset", required=True,
                        help="Exact dataset SHA256, only after the user approves the dated review")
    args = parser.parse_args()
    from .inventory_data import inventory_dataset_hash
    if args.approve_dataset != APPROVED_DATASET or inventory_dataset_hash(DATASET) != APPROVED_DATASET:
        parser.error("Dataset approval does not match this fixed request")
    from .live import _runtime_key
    from .live_transport import LiveChatTransport
    probe = Probe(DATASET)
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
    transport = LiveChatTransport(_runtime_key, agent_tools=TOOLS, image_types=("jpeg",), max_image_bytes=MAX_VIEW_BYTES,
                                  max_images=KEEP_IMAGES, call_limit=LIMITS["chat"], call_ceiling=LIMITS["chat"])
    client = ChatClient(transport, scope, retries=0)
    try:
        report = run_calibration(probe, output, client, perception, live=True)
    except ModelError as exc:  # budget or call-limit refusals end the run; the partial report is lost, usage is kept
        report = {"live": True, "fatal": str(exc)}
    finally:
        usage = summarize(usage_rows(ledger, SCOPE+"/"))
        (output/"usage.json").write_text(json.dumps({"usage": usage, "ledger_before": state,
            "ledger_after": ledger.summary()}, indent=2, default=str)+"\n", encoding="utf-8")
    report.update(dataset_sha256=APPROVED_DATASET, usage=usage, ledger_before=state, ledger_after=ledger.summary(),
                  cache_hits=perception.cache.hits, live_tool_calls=perception.live_calls)
    (output/"calibration.json").write_text(json.dumps(report, indent=2, default=str)+"\n", encoding="utf-8")
    print(json.dumps({k: report.get(k) for k in ("summary", "headroom", "decision", "errors", "fatal", "usage")},
                     indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
