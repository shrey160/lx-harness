"""Fixed S6 voicecount calibration. Requires separate explicit user approval.

Two base arms on the 20 calibration items of the frozen ``voicecount-v1``
dataset, no improver and no candidate:

- A0, Spark alone: the photo plus the request as text, no tools (how well the
  agent counts by itself).
- A1, base harness: the photo plus the spoken request reachable only through
  the generated tools (SAM, Transcribe, count_boxes), empty tool guidance.

Gate (pre-registered in the subplan): A1 mean in 0.30-0.80 on the primary
metric. Reviewed in docs/voicecount-calibration-review.md; arbitrary inputs,
endpoints and models are not accepted.
"""
import argparse
import json
from decimal import Decimal
from pathlib import Path
from statistics import mean

from .perception import MetaToolTransport, PerceptionCache, PerceptionClient
from .providers import SAM, TRANSCRIBE
from .spark import ModelError, SparkClient
from .usage import BudgetScope, summarize, usage_rows
from .voicecount import load_voicecount_dataset, verify_count
from .voicecount_workflow import BASE_PROMPT, CONTRACT, VoiceCountAdapter

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT/".lx"/"datasets"/"voicecount-v1"
APPROVED_DATASET = "b306c42f11c867c6746ba9202dcd4208884610d836edcff5a1322460e4fa2146"
APPROVED_EXPERIMENT = "129449c2299be3acd8b38e1946c6ecd1039761c3e5fdf2327c6750d2eb0e779c"
SCOPE = "voicecount-calibration-v1"
RUN_CAP_USD = "1.00"
LIMITS = {"chat": 180, "segment": 150, "transcribe": 25}
HEADROOM = (0.30, 0.80)
MAX_ERRORS = 3
# Last recorded shared-ledger checkpoint (S5 probe, 29 Oct 2026).
CHECKPOINT_SPENT = Decimal("0.070598338")
CHECKPOINT_CALLS = 257
ALONE_MARKER = "LX_VOICECOUNT_ALONE"
ALONE_CONTRACT = CONTRACT.replace("the request is provided only as audio", "the request is given as text")
CATALOG = [
    {"kind": "segmentation", "name": "SAM 3.1", "capabilities": [
        "Image segmentation: finds every instance of a short noun phrase in a photo and returns one box and mask per instance"]},
    {"kind": "transcription", "name": "Muse Voice Transcribe", "capabilities": [
        "Speech-to-text: transcribes spoken audio (mono WAV) into text"]},
]


def parse_answer(text):
    content = (text or "").strip()
    if content.startswith("```"):
        content = "\n".join(content.splitlines()[1:-1])
    try:
        return json.loads(content)
    except ValueError:
        return content


def alone_messages(dataset, item):
    """A0: same base prompt and contract, the request as text, no tools."""
    import base64
    from .voicecount import jpeg_size
    sample = next(s for s in dataset.worker_inputs if s["sample_id"] == item)
    image = (dataset.root/"worker_inputs"/sample["image"]).read_bytes()
    width, height = jpeg_size(image)
    text = json.dumps({"sample_id": item, "image_size": [width, height], "request": dataset.scripts[item]["text"]})
    return [{"role": "developer", "content": ALONE_MARKER+"\nsystemprompt.md\n"+BASE_PROMPT+"\n\nTask contract:\n"+ALONE_CONTRACT},
            {"role": "user", "content": [{"type": "text", "text": text}, {"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64,"+base64.b64encode(image).decode()}}]}]


def run_calibration(dataset, output, client, perception, *, live=False):
    """Score A0 and A1 per item; a model error scores 0 and repeated errors stop."""
    output = Path(output)
    items = [s["sample_id"] for s in dataset.worker_inputs if s["split"] == "calibration"]
    truth, errors, arms = dataset.controller_truth, [], {"A0": {}, "A1": {}}

    def failed(arm, item, exc):
        errors.append({"arm": arm, "item": item, "error": str(exc)})
        arms[arm][item] = {"score": 0.0, "error": str(exc)}
        if len(errors) >= MAX_ERRORS:
            raise ModelError(f"stopped after {len(errors)} model errors")

    for item in items:
        try:
            message, usage = client.chat(alone_messages(dataset, item), role="worker", phase="alone", candidate="A0/"+item)
        except ModelError as exc:
            failed("A0", item, exc)
            continue
        answer = parse_answer(message["content"])
        judged = verify_count(truth[item], answer)
        arms["A0"][item] = {"answer": answer, "score": judged.score, "errors": list(judged.errors),
                            "output_tokens": usage["output_tokens"]}
    adapter = VoiceCountAdapter(dataset.root/"worker_inputs", output/"A1", client, truth, perception=perception,
                                catalog=CATALOG, mode="live" if live else "offline_preview")
    for item in items:
        try:
            run = adapter.execute(item, None, "A1/"+item)
        except ModelError as exc:
            failed("A1", item, exc)
            continue
        answer = json.loads(adapter.deliverable_text(run))
        judged = verify_count(truth[item], answer)
        trace = json.loads((run/"trace.json").read_text(encoding="utf-8"))["trace"]
        tools = [s for s in trace if s["type"] == "tool"]
        arms["A1"][item] = {"answer": answer, "score": judged.score, "errors": list(judged.errors),
                            "turns": sum(s["type"] == "assistant" for s in trace),
                            "tools": [s["name"] for s in tools], "tool_errors": sum("error" in s for s in tools),
                            "stopped": next((s["reason"] for s in trace if s["type"] == "stopped"), None)}
    summary = {}
    for arm, results in arms.items():
        scores = [r["score"] for r in results.values()]
        summary[arm] = {"mean": mean(scores), "passed": sum(scores), "items": len(scores),
                        "by_region": {region: mean([results[i]["score"] for i in items if truth[i]["region"] == region])
                                      for region in sorted({truth[i]["region"] for i in items})}}
    a1 = summary["A1"]["mean"]
    report = {"live": live, "capability_evidence": live, "items": items, "arms": arms, "summary": summary,
              "headroom_band": HEADROOM, "headroom": HEADROOM[0] <= a1 <= HEADROOM[1], "errors": errors,
              "decision": ("A1 in band: prepare S7 trials" if HEADROOM[0] <= a1 <= HEADROOM[1]
                           else "A1 above band: raise difficulty offline" if a1 > HEADROOM[1]
                           else "A1 below band: inspect traces for broken tool binding before any change")}
    output.mkdir(parents=True, exist_ok=True)
    (output/"calibration.json").write_text(json.dumps(report, indent=2, default=str)+"\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-dataset", required=True,
                        help="Exact dataset SHA256, only after the user approves the dated review")
    args = parser.parse_args()
    if args.approve_dataset != APPROVED_DATASET:
        parser.error("Dataset approval does not match this fixed request")
    from .live import _runtime_key
    from .live_transport import LiveMetaTransport
    from .agent import agent_tools
    from .trials import dataset_hash, experiment_hash
    from .voicecount_probe import guarded_ledger
    dataset = load_voicecount_dataset(DATASET)
    if dataset_hash(dataset) != APPROVED_DATASET:
        raise ValueError("Dataset differs from the reviewed frozen dataset")
    if experiment_hash(dataset, "voicecount") != APPROVED_EXPERIMENT:
        raise ValueError("Contract, truth, verifier or agent code differs from the reviewed experiment")
    ledger, state = guarded_ledger(SCOPE)
    if Decimal(str(state["spent_usd"])) < CHECKPOINT_SPENT or state["settled_calls"] < CHECKPOINT_CALLS:
        raise ValueError("Shared pilot ledger is older than the S5 checkpoint")
    output = ROOT/".lx"/SCOPE
    output.mkdir(parents=True, exist_ok=True)
    with (output/"started.json").open("x", encoding="utf-8") as marker:
        json.dump({"dataset_sha256": APPROVED_DATASET, "run_cap_usd": RUN_CAP_USD, **LIMITS}, marker)
    scope = BudgetScope(ledger, SCOPE, RUN_CAP_USD)
    perception = PerceptionClient(MetaToolTransport(SAM, _runtime_key, call_limit=LIMITS["segment"]),
                                  MetaToolTransport(TRANSCRIBE, _runtime_key, call_limit=LIMITS["transcribe"]),
                                  scope, PerceptionCache(ROOT/".lx"/"perception-cache"/"voicecount-v1"))
    transport = LiveMetaTransport(_runtime_key, call_limit=LIMITS["chat"], agent_tools=agent_tools(CATALOG),
                                  image_types=("jpeg",), max_image_bytes=1_000_000, call_ceiling=LIMITS["chat"])
    client = SparkClient(transport, scope, retries=0)
    try:
        report = run_calibration(dataset, output, client, perception, live=True)
    finally:
        usage = summarize(usage_rows(ledger, SCOPE+"/"))
        (output/"usage.json").write_text(json.dumps({"usage": usage, "ledger_before": state,
            "ledger_after": ledger.summary()}, indent=2, default=str)+"\n", encoding="utf-8")
    report.update(usage=usage, ledger_before=state, ledger_after=ledger.summary(),
                  cache_hits=perception.cache.hits, live_tool_calls=perception.live_calls)
    (output/"calibration.json").write_text(json.dumps(report, indent=2, default=str)+"\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("summary", "headroom", "decision", "errors", "usage")}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
