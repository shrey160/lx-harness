"""Fixed E-A stream for PROD-004 with a swapped agent (G1/G2). Requires separate explicit user approval.

T8's loop as before (Harness² k=1, sequential mode, library carried, the 6
stream samples of ``inventory-mix-v1`` in T8's interleaved order, skills and
sandboxed scripts) with the agent swapped for Gemma 4 E4B. Gemma is an
ordinary OpenAI-compatible provider entry: its base URL is a stated input of
the request, approved with it, like any provider's. Spark stays the improver
and the reflector; SAM 3.1 and Transcribe stay fixed cloud tools. D-013: these
6 samples are all a user provides.

The agent's model, quantisation, tools, reply cap and per-call bound match
the E-C retry, so the held-out check (G3) can reuse that run's base rows. The
improver is grade-blind. WSL controller (the core loop and the script
sandbox need it). Reviewed in docs/inventory-stream-local-review.md.
"""
import argparse
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from .budget import BudgetLedger
from .inventory_local_eval import LOCAL, MAX_OUTPUT_TOKENS, served_models
from .inventory_probe import KEEP_IMAGES, LEDGER, MAX_VIEW_BYTES, TOOLS
from .inventory_stream import APPROVED_DATASET, DATASET, run_ea
from .perception import MetaToolTransport, PerceptionCache, PerceptionClient
from .providers import SAM, TRANSCRIBE, chat_endpoint
from .spark import SparkClient
from .usage import BudgetScope, summarize, usage_rows

ROOT = Path(__file__).resolve().parents[2]
SCOPE = "inventory-ea-local-v1"
# Gemma is $0; T8's Spark improver/reflector cost about $0.11 and its new SAM pairs about $0.13.
RUN_CAP_USD = "1.00"
# Per sample about 4 agent runs (<= 19 turns each) on Gemma and 3-4 improver/reflection sessions
# (<= 12 turns each, plus one retry of an invalid proposal) on Spark.
LIMITS = {"agent_chat": 600, "improver_chat": 500, "segment": 300, "transcribe": 40}
# Last recorded shared-ledger checkpoint (after the E-C retry, 30 Oct 2026).
CHECKPOINT_SPENT = Decimal("2.054275772")
CHECKPOINT_CALLS = 3576


def agent_profile(base_url):
    """The E-C agent entry at the stated base URL (validated like any provider endpoint)."""
    return replace(LOCAL, endpoint=chat_endpoint(base_url))


def guarded_ledger():
    if not LEDGER.is_file() or LEDGER.is_symlink():
        raise ValueError("The existing shared pilot ledger is required")
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
    parser.add_argument("--agent-base-url", required=True,
                        help="The agent provider's OpenAI-compatible base URL, as approved (e.g. http://host:8888/v1)")
    args = parser.parse_args()
    from .inventory_data import inventory_dataset_hash
    if args.approve_dataset != APPROVED_DATASET or inventory_dataset_hash(DATASET) != APPROVED_DATASET:
        parser.error("Dataset approval does not match this fixed request")
    try:
        profile = agent_profile(args.agent_base_url)
    except ValueError as exc:
        parser.error(str(exc))
    from . import sandbox
    if not sandbox.available():
        parser.error("The script sandbox is required (run from WSL)")
    try:
        models = served_models(url=args.agent_base_url.rstrip("/")+"/models")
    except OSError:
        parser.error("The agent provider is not reachable at the stated base URL")
    if profile.model not in models:
        parser.error(f"The agent provider does not report {profile.model} as loaded")
    from .live import _runtime_key
    from .live_transport import LiveMetaTransport
    ledger, state = guarded_ledger()
    output = ROOT/".lx"/SCOPE
    output.mkdir(parents=True, exist_ok=True)
    # Exclusive marker prevents concurrent dispatch or accidental repeat runs.
    with (output/"started.json").open("x", encoding="utf-8") as marker:
        json.dump({"dataset_sha256": APPROVED_DATASET, "agent": profile.model, "agent_endpoint": profile.endpoint,
                   "max_output_tokens": MAX_OUTPUT_TOKENS, "run_cap_usd": RUN_CAP_USD, **LIMITS}, marker)
    scope = BudgetScope(ledger, SCOPE, RUN_CAP_USD)
    perception = PerceptionClient(MetaToolTransport(SAM, _runtime_key, call_limit=LIMITS["segment"]),
                                  MetaToolTransport(TRANSCRIBE, _runtime_key, call_limit=LIMITS["transcribe"]),
                                  scope, PerceptionCache(ROOT/".lx"/"perception-cache"/"inventory-mix-v1"))
    images = {"image_types": ("jpeg",), "max_image_bytes": MAX_VIEW_BYTES, "max_images": KEEP_IMAGES}
    # The agent provider accepts exactly the agent tools; Spark accepts only the improver file tools.
    agent = SparkClient(LiveMetaTransport(None, profile=profile, agent_tools=TOOLS, call_limit=LIMITS["agent_chat"],
                                          call_ceiling=LIMITS["agent_chat"], **images),
                        scope, max_tokens=MAX_OUTPUT_TOKENS, retries=0, profile=profile)
    improver = SparkClient(LiveMetaTransport(_runtime_key, call_limit=LIMITS["improver_chat"],
                                             call_ceiling=LIMITS["improver_chat"], **images), scope, retries=0)
    try:
        report = run_ea(DATASET, output/"run", agent, perception, live=True, improver=improver)
    finally:
        usage = summarize(usage_rows(ledger, SCOPE+"/"))
        (output/"usage.json").write_text(json.dumps({"usage": usage, "ledger_before": state,
            "ledger_after": ledger.summary()}, indent=2, default=str)+"\n", encoding="utf-8")
    summary = {"agent": profile.model, "agent_endpoint": profile.endpoint, "means": report["means"],
               "gains": report["gains"], "improved": report["improved"], "regressed": report["regressed"],
               "run": report["run"], "usage": usage, "cache_hits": perception.cache.hits,
               "live_tool_calls": perception.live_calls}
    (output/"summary.json").write_text(json.dumps(summary, indent=2, default=str)+"\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
