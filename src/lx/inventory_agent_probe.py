"""Fixed G1b probe of a swapped agent for PROD-004. Requires separate explicit user approval.

Before the G2 stream, checks that the stated agent provider (D-014:
LFM3-VL-2B on Unsloth) can do the job's mechanics: a view check (call `view`,
then describe photo 1) and one base-harness run on each of two calibration
samples of the frozen ``inventory-mix-v1`` (a count and a colour_count, 3
photos each; T7 ran them on Spark, so perception is mostly cached). It is a
wire and behaviour check, not a capability measurement. Same agent settings
and guards as G2. WSL controller. Reviewed in
docs/inventory-stream-local-review.md (section G1b).
"""
import argparse
import json
from pathlib import Path

from .inventory_data import inventory_dataset_hash
from .inventory_probe import Probe, base_harness
from .inventory_stream import APPROVED_DATASET, DATASET
from .inventory_stream_local import add_agent_arguments, agent_client, checked_agent, guarded_ledger
from .perception import MetaToolTransport, PerceptionCache, PerceptionClient
from .providers import SAM, TRANSCRIBE
from .usage import BudgetScope, summarize, usage_rows

ROOT = Path(__file__).resolve().parents[2]
SCOPE = "inventory-agent-probe-v1"
RUN_CAP_USD = "0.10"
VIEW_SAMPLE = "fsc-calibration-02"
RUN_SAMPLES = ("fsc-calibration-02", "mix-calibration-colourcount-01")
# chat: view check 3 + two runs at caps(3) = 13 turns each.
LIMITS = {"agent_chat": 30, "segment": 20, "transcribe": 4}


def run_probe(probe, client, perception):
    """View check, then one base-harness run per sample with its checklist score and stop reason."""
    view = probe.view_check(client, perception, VIEW_SAMPLE)
    runs = {item: probe.agent(client, perception, item, base_harness()) for item in RUN_SAMPLES}
    return {"view_check": view, "runs": runs,
            "answered": sum(isinstance(r.get("answer"), dict) for r in runs.values()),
            "tools_used": sorted({c["name"] for r in runs.values() for c in r.get("tool_calls", [])})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-dataset", required=True,
                        help="Exact dataset SHA256, only after the user approves the dated review")
    add_agent_arguments(parser)
    args = parser.parse_args()
    if args.approve_dataset != APPROVED_DATASET or inventory_dataset_hash(DATASET) != APPROVED_DATASET:
        parser.error("Dataset approval does not match this fixed request")
    profile, loader = checked_agent(parser, args)
    from .live import _runtime_key
    ledger, state = guarded_ledger(SCOPE, RUN_CAP_USD)
    output = ROOT/".lx"/SCOPE
    output.mkdir(parents=True, exist_ok=True)
    # Exclusive marker prevents concurrent dispatch or accidental repeat runs.
    with (output/"started.json").open("x", encoding="utf-8") as marker:
        json.dump({"dataset_sha256": APPROVED_DATASET, "agent": profile.model, "agent_endpoint": profile.endpoint,
                   "agent_key_env": args.agent_key_env, "run_cap_usd": RUN_CAP_USD, **LIMITS}, marker)
    scope = BudgetScope(ledger, SCOPE, RUN_CAP_USD)
    perception = PerceptionClient(MetaToolTransport(SAM, _runtime_key, call_limit=LIMITS["segment"]),
                                  MetaToolTransport(TRANSCRIBE, _runtime_key, call_limit=LIMITS["transcribe"]),
                                  scope, PerceptionCache(ROOT/".lx"/"perception-cache"/"inventory-mix-v1"))
    client = agent_client(profile, loader, scope, LIMITS["agent_chat"])
    try:
        report = run_probe(Probe(DATASET), client, perception)
    finally:
        usage = summarize(usage_rows(ledger, SCOPE+"/"))
        (output/"usage.json").write_text(json.dumps({"usage": usage, "ledger_before": state,
            "ledger_after": ledger.summary()}, indent=2, default=str)+"\n", encoding="utf-8")
    report.update(live=True, capability_evidence=False, agent=profile.model, agent_endpoint=profile.endpoint,
                  dataset_sha256=APPROVED_DATASET, cache_hits=perception.cache.hits,
                  live_tool_calls=perception.live_calls, usage=usage, ledger_after=ledger.summary())
    (output/"probe.json").write_text(json.dumps(report, indent=2, default=str)+"\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("view_check", "answered", "tools_used", "usage")}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
