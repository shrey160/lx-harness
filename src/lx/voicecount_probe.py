"""Fixed S5 live probe for PROD-003. Requires separate explicit user approval.

Confirms wire formats before any experiment relies on them; it is not a
capability measurement. Two parts, each a separate command:

- ``--part meta`` (WSL controller, shared pilot ledger): 2 Transcribe clips,
  3 SAM images (two true concepts, one absent concept) and the Spark agent
  with the base harness on the same 2 items.
- ``--part local`` (Windows controller, where the local server is reachable):
  the same agent loop on 1 item with a local OpenAI-compatible provider as the
  agent, still calling cloud SAM/Transcribe (cached results are reused).

Only the reviewed items of the frozen ``voicecount-v1`` dataset are used;
arbitrary inputs, endpoints and models are not accepted. Reviewed in
docs/voicecount-probe-review.md.
"""
import argparse
import json
from decimal import Decimal
from pathlib import Path

from .agent import ItemTools, agent_tools, run_agent
from .budget import BudgetLedger
from .perception import MetaToolTransport, PerceptionCache, PerceptionClient, parse_boxes, sam_text
from .providers import SAM, TRANSCRIBE, ProviderProfile
from .spark import ModelError, SparkClient
from .usage import BudgetScope, summarize, usage_rows
from .voicecount import load_voicecount_dataset, verify_count
from .voicecount_workflow import BASE_PROMPT, VoiceCountDomain

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT/".lx"/"datasets"/"voicecount-v1"
LEDGER = ROOT/".lx"/"live-spike"/"pilot-budget.sqlite3"
APPROVED_DATASET = "b306c42f11c867c6746ba9202dcd4208884610d836edcff5a1322460e4fa2146"
ITEMS = ("loop-0005", "loop-0036")
NEGATIVE = ("loop-0005", "giraffes")
LOCAL_ITEM = "loop-0005"
LOCAL = ProviderProfile(kind="chat", endpoint="http://127.0.0.1:8888/v1/chat/completions",
                        model="gemma-4-E4B-it-GGUF", token_param="max_tokens",
                        accepts_images=True, supports_tools=True)
PARTS = {
    # segment: 3 direct + at most 2 items x 6 agent tool calls; repeats are cached.
    "meta": {"scope": "voicecount-probe-v1", "cap_usd": "0.50", "chat": 16, "segment": 15, "transcribe": 4},
    "local": {"scope": "voicecount-probe-local-v1", "cap_usd": "0.10", "chat": 8, "segment": 6, "transcribe": 2},
}
CAPS = {"max_turns": 8, "max_tool_calls": 6}
# Last recorded shared-ledger checkpoint (R3 event-log calibration, 29 Oct 2026).
CHECKPOINT_SPENT = Decimal("0.049064596")
CHECKPOINT_CALLS = 234
CATALOG = [
    {"kind": "segmentation", "name": "SAM 3.1", "capabilities": [
        "Image segmentation: finds every instance of a short noun phrase in a photo and returns one box and mask per instance"]},
    {"kind": "transcription", "name": "Muse Voice Transcribe", "capabilities": [
        "Speech-to-text: transcribes spoken audio (mono WAV) into text"]},
]


class Recording:
    """Keeps each raw reply's shape (keys, text size, a bounded excerpt) for the report."""

    def __init__(self, inner):
        self.inner, self.replies = inner, []

    def __call__(self, payload, timeout):
        raw = self.inner(payload, timeout)
        entry = {"keys": sorted(raw) if isinstance(raw, dict) else type(raw).__name__}
        if isinstance(raw, dict):
            entry["excerpt"] = json.dumps(raw)[:3000]
            for name in ("output", "usage", "status", "turns"):
                if name in raw:
                    entry[name+"_type"] = type(raw[name]).__name__
        self.replies.append(entry)
        return raw


def word_error_rate(reference, hypothesis):
    ref = [w.strip(".,?!'\"").lower() for w in reference.split()]
    hyp = [w.strip(".,?!'\"").lower() for w in hypothesis.split()]
    row = list(range(len(hyp)+1))
    for i, word in enumerate(ref, 1):
        previous, row[0] = row[:], i
        for j, other in enumerate(hyp, 1):
            row[j] = min(previous[j]+1, row[j-1]+1, previous[j-1]+(word != other))
    return row[-1]/max(1, len(ref))


def agent_messages(dataset, item, image):
    sample = next(s for s in dataset.worker_inputs if s["sample_id"] == item)
    # Same text the adapter builds from the untouched base harness.
    return VoiceCountDomain().messages(sample, "systemprompt.md\n"+BASE_PROMPT, image)


def media(dataset, item, kind):
    sample = next(s for s in dataset.worker_inputs if s["sample_id"] == item)
    return (dataset.root/"worker_inputs"/sample[kind]).read_bytes()


def run_agent_items(dataset, client, perception, items):
    results, tools = {}, agent_tools(CATALOG)
    for item in items:
        image = media(dataset, item, "image")
        handler = ItemTools(perception, image=image, audio=media(dataset, item, "audio"), candidate="agent/"+item)
        try:
            text, trace = run_agent(client, agent_messages(dataset, item, image if client.profile.accepts_images else None),
                                    tools, handler, candidate="agent/"+item, timeout=600, **CAPS)
        except ModelError as exc:
            results[item] = {"error": str(exc)}
            continue
        try:
            answer = json.loads(text.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
        except ValueError:
            answer = text
        judged = verify_count(dataset.controller_truth[item], answer)
        results[item] = {"answer": answer, "score": judged.score, "errors": list(judged.errors),
                         "turns": sum(s["type"] == "assistant" for s in trace),
                         "tool_calls": [{"name": s["name"], "arguments": s["arguments"], "error": s.get("error")}
                                        for s in trace if s["type"] == "tool"],
                         "stopped": next((s["reason"] for s in trace if s["type"] == "stopped"), None)}
    return results


def run_meta(dataset, perception, client, recorders):
    report = {"transcribe": {}, "segment": {}}
    for item in ITEMS:
        try:
            heard = perception.transcribe(media(dataset, item, "audio"), candidate="transcribe/"+item)
            report["transcribe"][item] = dict(heard, wer=word_error_rate(dataset.scripts[item]["text"], heard["transcript"]))
        except ModelError as exc:
            report["transcribe"][item] = {"error": str(exc)}
    for item, concept in [(item, dataset.controller_truth[item]["object"]) for item in ITEMS] + [NEGATIVE]:
        try:
            found = perception.segment(media(dataset, item, "image"), concept, candidate="segment/"+item)
        except ModelError as exc:
            report["segment"][f"{item}:{concept}"] = {"error": str(exc)}
            continue
        boxes, truth = found["boxes"], dataset.controller_truth[item]
        report["segment"][f"{item}:{concept}"] = {
            "instances": len(boxes), "truth_count": truth["count"] if concept == truth["object"] else 0,
            "with_confidence": sum("c" in b for b in boxes),
            "confidence_min_max": [min((b.get("c", 1) for b in boxes), default=None), max((b.get("c", 1) for b in boxes), default=None)],
            "image_size": truth["size"], "box_sizes_match_image": all([b["w"], b["h"]] == truth["size"] for b in boxes)}
    report["agent"] = run_agent_items(dataset, client, perception, ITEMS)
    report["raw_shapes"] = {name: r.replies for name, r in recorders.items()}
    return report


def guarded_ledger(scope):
    if not LEDGER.is_file() or LEDGER.is_symlink():
        raise ValueError("The existing shared pilot ledger is required")
    ledger = BudgetLedger(LEDGER)
    state = ledger.summary()
    if Decimal(str(state["spent_usd"])) < CHECKPOINT_SPENT or state["settled_calls"] < CHECKPOINT_CALLS:
        raise ValueError("Shared pilot ledger is older than the recorded checkpoint")
    if usage_rows(ledger, scope+"/"):
        raise ValueError("This probe part has already dispatched calls; obtain a new reviewed retry")
    return ledger, state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--part", choices=sorted(PARTS), required=True)
    parser.add_argument("--approve-dataset", required=True,
                        help="Exact dataset SHA256, only after the user approves the dated review")
    args = parser.parse_args()
    if args.approve_dataset != APPROVED_DATASET:
        parser.error("Dataset approval does not match this fixed request")
    from .live import _runtime_key
    from .trials import dataset_hash
    limits = PARTS[args.part]
    dataset = load_voicecount_dataset(DATASET)
    if dataset_hash(dataset) != APPROVED_DATASET:
        raise ValueError("Dataset differs from the reviewed frozen dataset")
    ledger, state = guarded_ledger(limits["scope"])
    output = ROOT/".lx"/limits["scope"]
    output.mkdir(parents=True, exist_ok=True)
    # Exclusive marker prevents concurrent dispatch or accidental repeat runs.
    with (output/"started.json").open("x", encoding="utf-8") as marker:
        json.dump({"dataset_sha256": APPROVED_DATASET, **limits}, marker)
    scope = BudgetScope(ledger, limits["scope"], limits["cap_usd"])
    recorders = {"segment": Recording(MetaToolTransport(SAM, _runtime_key, call_limit=limits["segment"])),
                 "transcribe": Recording(MetaToolTransport(TRANSCRIBE, _runtime_key, call_limit=limits["transcribe"]))}
    # The cache lives beside the dataset so later steps reuse these paid results.
    perception = PerceptionClient(recorders["segment"], recorders["transcribe"], scope,
                                  PerceptionCache(ROOT/".lx"/"perception-cache"/"voicecount-v1"))
    from .live_transport import LiveMetaTransport
    tools = agent_tools(CATALOG)
    try:
        if args.part == "meta":
            transport = LiveMetaTransport(_runtime_key, call_limit=limits["chat"], agent_tools=tools,
                                          image_types=("jpeg",), max_image_bytes=1_000_000)
            report = run_meta(dataset, perception, SparkClient(transport, scope, retries=0), recorders)
        else:
            transport = LiveMetaTransport(None, profile=LOCAL, call_limit=limits["chat"], agent_tools=tools,
                                          image_types=("jpeg",), max_image_bytes=1_000_000)
            client = SparkClient(transport, scope, retries=0, profile=LOCAL)
            report = {"agent": run_agent_items(dataset, client, perception, (LOCAL_ITEM,)),
                      "raw_shapes": {name: r.replies for name, r in recorders.items()}}
    finally:
        usage = summarize(usage_rows(ledger, limits["scope"]+"/"))
        (output/"usage.json").write_text(json.dumps({"usage": usage, "ledger_before": state,
            "ledger_after": ledger.summary()}, indent=2, default=str)+"\n", encoding="utf-8")
    report.update(part=args.part, live=True, capability_evidence=False, dataset_sha256=APPROVED_DATASET,
                  limits=limits, cache_hits=perception.cache.hits, live_tool_calls=perception.live_calls,
                  usage=usage, ledger_before=state, ledger_after=ledger.summary())
    (output/"probe.json").write_text(json.dumps(report, indent=2, default=str)+"\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("part", "live_tool_calls", "cache_hits", "usage")}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
