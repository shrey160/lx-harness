"""Fixed E-B evaluation for PROD-004 (inventory). Requires separate explicit user approval.

Frozen harness vs base harness, both with Spark as the agent and no improver:
the harness T8's loop wrote last (``harnesses/inventory-ea-v1``, one skill and
its guardrail trigger) against the base harness, on samples the loop never
saw: the user bundle's 9 check samples and the 24 hidden held-out samples of
the frozen ``inventory-mix-v1`` dataset. Each sample runs once per arm; the
arm order alternates by sample.

Pre-registered criterion (29 Oct 2026, before any E-B call): on the 24
held-out samples, the mean paired checklist gain is at least 0.10 and the
lower end of its 95% paired bootstrap interval is above 0. The 9 check
samples are reported alongside (the user's own view) but do not decide.
Reviewed in docs/inventory-eval-review.md.
"""
import argparse
import hashlib
import json
import random
from decimal import Decimal
from pathlib import Path
from statistics import mean

from .inventory import answer_correct
from .inventory_probe import KEEP_IMAGES, LEDGER, MAX_VIEW_BYTES, TOOLS, Probe, base_harness
from .perception import MetaToolTransport, PerceptionCache, PerceptionClient
from .providers import SAM, TRANSCRIBE
from .spark import SparkClient
from .usage import BudgetScope, summarize, usage_rows

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT/".lx"/"datasets"/"inventory-mix-v1"
HARNESS = ROOT/"harnesses"/"inventory-ea-v1"
APPROVED_DATASET = "db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322"
APPROVED_HARNESS = "c98c636f83a3fdabf4e3a7ed4acabb3b104de19ed6714822b7ef6283024ecaf6"
SCOPE = "inventory-eval-v1"
RUN_CAP_USD = "2.50"
# 33 samples x 2 arms; caps(n) allows at most 19 turns per run (worst case about 1,250 chat calls).
LIMITS = {"chat": 1300, "segment": 600, "transcribe": 40}
SPLITS = ("check", "heldout")
CRITERION = {"split": "heldout", "minimum_gain": 0.10, "interval": 0.95, "resamples": 10000, "seed": 20261029}
MAX_ERRORS = 5
# Last recorded shared-ledger checkpoint (after T8, 29 Oct 2026).
CHECKPOINT_SPENT = Decimal("0.821485008")
CHECKPOINT_CALLS = 1598


def read_harness(root=HARNESS):
    """The frozen harness as {posix path: text}."""
    root = Path(root)
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in sorted(root.rglob("*")) if p.is_file()}


def harness_hash(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()


def paired_interval(gains, *, interval=CRITERION["interval"], resamples=CRITERION["resamples"], seed=CRITERION["seed"]):
    """Percentile bootstrap interval of the mean paired gain."""
    if not gains:
        return None, None
    rng = random.Random(seed)
    means = sorted(mean(rng.choices(gains, k=len(gains))) for _ in range(resamples))
    tail = (1 - interval) / 2
    return means[int(tail * resamples)], means[min(resamples - 1, int((1 - tail) * resamples))]


def summarize_split(rows):
    gains = [r["frozen"]["score"] - r["base"]["score"] for r in rows.values()]
    low, high = paired_interval(gains)
    return {"items": len(rows), "base_mean": mean(r["base"]["score"] for r in rows.values()) if rows else None,
            "frozen_mean": mean(r["frozen"]["score"] for r in rows.values()) if rows else None,
            "mean_gain": mean(gains) if gains else None, "interval": [low, high],
            "wins": sum(g > 0 for g in gains), "ties": sum(g == 0 for g in gains), "losses": sum(g < 0 for g in gains),
            "base_correct": sum(r["base"]["correct"] for r in rows.values()),
            "frozen_correct": sum(r["frozen"]["correct"] for r in rows.values())}


def run_eval(probe, output, client, perception, frozen, *, items=None, live=False):
    """Both arms per sample; a model error scores 0 for that arm and repeated errors stop the run."""
    output = Path(output)
    items = items or [i for i, s in probe.samples.items() if s["split"] in SPLITS]
    arms = {"base": base_harness(), "frozen": frozen}
    rows, errors = {}, []
    for index, item in enumerate(items):
        row = {"split": probe.samples[item]["split"], "request_type": probe.truth[item]["request_type"]}
        for arm in (("base", "frozen") if index % 2 == 0 else ("frozen", "base")):
            result = probe.agent(client, perception, item, arms[arm])
            if "error" in result:
                errors.append({"item": item, "arm": arm, "error": result["error"]})
                row[arm] = {"score": 0.0, "correct": False, **result}
            else:
                row[arm] = dict(result, correct=answer_correct(probe.truth[item], result["answer"]))
        rows[item] = row
        if len(errors) >= MAX_ERRORS:
            break
    by_split = {split: summarize_split({i: r for i, r in rows.items() if r["split"] == split}) for split in SPLITS}
    by_type = {kind: summarize_split({i: r for i, r in rows.items() if r["request_type"] == kind})
               for kind in sorted({r["request_type"] for r in rows.values()})}
    held = by_split[CRITERION["split"]]
    complete = len(rows) == len(items) and len(errors) < MAX_ERRORS
    met = bool(complete and held["items"] and held["mean_gain"] >= CRITERION["minimum_gain"] and held["interval"][0] > 0)
    report = {"live": live, "capability_evidence": live, "criterion": CRITERION, "items": items, "rows": rows,
              "errors": errors, "complete": complete, "by_split": by_split, "by_type": by_type, "criterion_met": met,
              "decision": ("stopped on model errors: no criterion result" if not complete
                           else "criterion met: the learned harness transfers to unseen samples; prepare E-C" if met
                           else "criterion not met: report the effect as observed; no transfer claim")}
    output.mkdir(parents=True, exist_ok=True)
    (output/"eval.json").write_text(json.dumps(report, indent=2, default=str)+"\n", encoding="utf-8")
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
        raise ValueError("This evaluation has already dispatched calls; obtain a new reviewed retry")
    return ledger, state


def checked_probe():
    """Probe on the frozen dataset with the core surface (as the T8 loop used) and the validated frozen harness."""
    from harness2.substrate.opencode import OpencodeSubstrate

    from .components import validate_components
    frozen = read_harness()
    if harness_hash(frozen) != APPROVED_HARNESS:
        raise ValueError("Frozen harness differs from the reviewed one")
    probe = Probe(DATASET)
    probe.surface = OpencodeSubstrate().surface()
    validate_components(probe.surface, frozen)
    return probe, frozen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-dataset", required=True,
                        help="Exact dataset SHA256, only after the user approves the dated review")
    parser.add_argument("--approve-harness", required=True, help="Exact frozen harness SHA256 from the review")
    args = parser.parse_args()
    from .inventory_data import inventory_dataset_hash
    if args.approve_dataset != APPROVED_DATASET or inventory_dataset_hash(DATASET) != APPROVED_DATASET:
        parser.error("Dataset approval does not match this fixed request")
    if args.approve_harness != APPROVED_HARNESS:
        parser.error("Harness approval does not match this fixed request")
    from .live import _runtime_key
    from .live_transport import LiveMetaTransport
    probe, frozen = checked_probe()
    ledger, state = guarded_ledger()
    output = ROOT/".lx"/SCOPE
    output.mkdir(parents=True, exist_ok=True)
    # Exclusive marker prevents concurrent dispatch or accidental repeat runs.
    with (output/"started.json").open("x", encoding="utf-8") as marker:
        json.dump({"dataset_sha256": APPROVED_DATASET, "harness_sha256": APPROVED_HARNESS, "run_cap_usd": RUN_CAP_USD,
                   "criterion": CRITERION, **LIMITS}, marker)
    scope = BudgetScope(ledger, SCOPE, RUN_CAP_USD)
    perception = PerceptionClient(MetaToolTransport(SAM, _runtime_key, call_limit=LIMITS["segment"]),
                                  MetaToolTransport(TRANSCRIBE, _runtime_key, call_limit=LIMITS["transcribe"]),
                                  scope, PerceptionCache(ROOT/".lx"/"perception-cache"/"inventory-mix-v1"))
    transport = LiveMetaTransport(_runtime_key, agent_tools=TOOLS, image_types=("jpeg",), max_image_bytes=MAX_VIEW_BYTES,
                                  max_images=KEEP_IMAGES, call_limit=LIMITS["chat"], call_ceiling=LIMITS["chat"])
    client = SparkClient(transport, scope, retries=0)
    try:
        report = run_eval(probe, output, client, perception, frozen, live=True)
    finally:
        usage = summarize(usage_rows(ledger, SCOPE+"/"))
        (output/"usage.json").write_text(json.dumps({"usage": usage, "ledger_before": state,
            "ledger_after": ledger.summary()}, indent=2, default=str)+"\n", encoding="utf-8")
    report.update(dataset_sha256=APPROVED_DATASET, harness_sha256=APPROVED_HARNESS, usage=usage,
                  ledger_before=state, ledger_after=ledger.summary(), cache_hits=perception.cache.hits,
                  live_tool_calls=perception.live_calls)
    (output/"eval.json").write_text(json.dumps(report, indent=2, default=str)+"\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("by_split", "criterion_met", "decision", "errors", "usage")},
                     indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
