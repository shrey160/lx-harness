"""Fixed E-C evaluation for PROD-004 (inventory). Requires separate explicit user approval.

E-B with the agent swapped for a local model: the same two arms (base harness
vs the frozen T8 harness ``harnesses/inventory-ea-v1``), the same 9 check and
24 held-out samples of ``inventory-mix-v1`` and the same alternating order,
with Gemma 4 E4B served by the local OpenAI-compatible server as the agent.
SAM and Transcribe stay cloud tools and share E-B's perception cache. Each
reply is capped at a lower output limit, because E2B ran away to the
4,096-token limit in T6. Windows controller (where the local server is
reachable).

Retry (30 Oct 2026, reviewed again): the first run (scope
``inventory-eval-local-v1``) stopped on 5 model errors that were a 60-second
client timeout, since fixed in ``SparkClient.chat``. Same request otherwise,
under a new scope; the server must report the model as loaded.

Pre-registered criterion (29 Oct 2026, before any E-C call), the same as E-B:
on the 24 held-out samples, the mean paired checklist gain is at least 0.10
and the lower end of its 95% paired bootstrap interval is above 0. E-B's
Spark results are reported alongside, descriptive only. Reviewed in
docs/inventory-local-eval-review.md.
"""
import argparse
import json
import urllib.request
from decimal import Decimal
from pathlib import Path

from .budget import BudgetLedger
from .inventory_eval import APPROVED_DATASET, APPROVED_HARNESS, CRITERION, DATASET, checked_probe, run_eval
from .inventory_probe import KEEP_IMAGES, LEDGER, MAX_VIEW_BYTES, TOOLS
from .perception import MetaToolTransport, PerceptionCache, PerceptionClient
from .providers import SAM, TRANSCRIBE, ProviderProfile
from .spark import SparkClient
from .usage import BudgetScope, summarize, usage_rows

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ProviderProfile(kind="chat", endpoint="http://127.0.0.1:8888/v1/chat/completions",
                        model="gemma-4-E4B-it-GGUF", token_param="max_tokens",
                        accepts_images=True, supports_tools=True)
MODELS_URL = "http://127.0.0.1:8888/v1/models"
# E-B's longest answer was about 1,000 JSON characters; E2B's runaway reply in T6 hit 4,096 tokens.
MAX_OUTPUT_TOKENS = 1536
SCOPE = "inventory-eval-local-v2"
# Gemma is $0 per token; the cap covers new SAM photo-phrase pairs (cache shared with E-B).
RUN_CAP_USD = "1.50"
LIMITS = {"chat": 1300, "segment": 600, "transcribe": 40}
SPARK_EVIDENCE = ROOT/"evidence"/"2026-10-29-inventory-eval"/"eval.json"
# Last recorded shared-ledger checkpoint (after the stopped first E-C run, 30 Oct 2026).
CHECKPOINT_SPENT = Decimal("1.744275772")
CHECKPOINT_CALLS = 2725


def served_models(opener=urllib.request.urlopen):
    """Model ids the local server reports as loaded (a GET, no generation); servers without the flag list only loaded ones."""
    with opener(MODELS_URL, timeout=10) as reply:
        return [m.get("id") for m in json.load(reply).get("data", []) if m.get("loaded", True)]


def spark_comparison(path=SPARK_EVIDENCE):
    """E-B's Spark summaries, for a descriptive side-by-side."""
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    return {"by_split": report["by_split"], "by_type": report["by_type"]}


def decision(report):
    if not report["complete"]:
        return "stopped on model errors: no criterion result"
    return ("criterion met: the Spark-learned harness also helps the local agent on unseen samples" if report["criterion_met"]
            else "criterion not met: report the effect as observed; no transfer claim for the local agent")


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
        raise ValueError("This evaluation has already dispatched calls; obtain a new reviewed retry")
    return ledger, state


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
    try:
        models = served_models()
    except OSError:
        parser.error("The local model server is not reachable")
    if LOCAL.model not in models:
        parser.error(f"The local server does not report {LOCAL.model} as loaded")
    from .live import _runtime_key
    from .live_transport import LiveMetaTransport
    probe, frozen = checked_probe()
    spark = spark_comparison()
    ledger, state = guarded_ledger()
    output = ROOT/".lx"/SCOPE
    output.mkdir(parents=True, exist_ok=True)
    # Exclusive marker prevents concurrent dispatch or accidental repeat runs.
    with (output/"started.json").open("x", encoding="utf-8") as marker:
        json.dump({"dataset_sha256": APPROVED_DATASET, "harness_sha256": APPROVED_HARNESS, "model": LOCAL.model,
                   "max_output_tokens": MAX_OUTPUT_TOKENS, "run_cap_usd": RUN_CAP_USD, "criterion": CRITERION,
                   **LIMITS}, marker)
    scope = BudgetScope(ledger, SCOPE, RUN_CAP_USD)
    perception = PerceptionClient(MetaToolTransport(SAM, _runtime_key, call_limit=LIMITS["segment"]),
                                  MetaToolTransport(TRANSCRIBE, _runtime_key, call_limit=LIMITS["transcribe"]),
                                  scope, PerceptionCache(ROOT/".lx"/"perception-cache"/"inventory-mix-v1"))
    transport = LiveMetaTransport(None, profile=LOCAL, agent_tools=TOOLS, image_types=("jpeg",),
                                  max_image_bytes=MAX_VIEW_BYTES, max_images=KEEP_IMAGES, call_limit=LIMITS["chat"],
                                  call_ceiling=LIMITS["chat"])
    client = SparkClient(transport, scope, max_tokens=MAX_OUTPUT_TOKENS, retries=0, profile=LOCAL)
    try:
        report = run_eval(probe, output, client, perception, frozen, live=True)
        report["decision"] = decision(report)
    finally:
        usage = summarize(usage_rows(ledger, SCOPE+"/"))
        (output/"usage.json").write_text(json.dumps({"usage": usage, "ledger_before": state,
            "ledger_after": ledger.summary()}, indent=2, default=str)+"\n", encoding="utf-8")
    report.update(model=LOCAL.model, max_output_tokens=MAX_OUTPUT_TOKENS, spark_eb=spark,
                  dataset_sha256=APPROVED_DATASET, harness_sha256=APPROVED_HARNESS, usage=usage,
                  ledger_before=state, ledger_after=ledger.summary(), cache_hits=perception.cache.hits,
                  live_tool_calls=perception.live_calls)
    (output/"eval.json").write_text(json.dumps(report, indent=2, default=str)+"\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("by_split", "criterion_met", "decision", "errors", "usage")},
                     indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
