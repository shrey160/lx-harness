"""Fixed T6 live probe for PROD-004 (inventory). Requires separate explicit user approval.

Confirms the new wire paths before any experiment relies on them; it is not
a capability measurement. Two parts, each a separate command:

- ``--part meta`` (WSL controller, shared pilot ledger): Transcribe on both
  probe samples, SAM on every photo of both for the requested kind, a view
  check (the agent is told to call ``view`` and describe the photo: does an
  image sent in a user message right after the tool messages work?), then
  the Spark agent on sample A with the base harness and on sample B with a
  probe harness holding the preview skill and helper script (does Spark load
  a skill and run a script in the sandbox on its own?).
- ``--part local`` (Windows controller, where the local server is reachable):
  the view check and the base-harness run on sample A with the local
  OpenAI-compatible provider (Gemma 4 E2B) as the agent; SAM/Transcribe stay cloud
  (cached results are reused). Scripts need the WSL sandbox, so the local
  part does not run sample B.

Only the reviewed calibration samples of the frozen ``inventory-coco-v1``
dataset are used. Reviewed in docs/inventory-probe-review.md.
"""
import argparse
import json
import types
from decimal import Decimal
from pathlib import Path

from .agent import PhotoSetTools, photo_tools, run_agent
from .budget import BudgetLedger
from .components import COMPONENT_TOOLS, ComponentTools, compose_prompt
from .inventory import caps, checklist, expected_photos, verify_inventory
from .inventory_data import load_inventory_dataset
from .inventory_workflow import (BASE_PROMPT, PREVIEW_GUARDRAIL, PREVIEW_SCRIPT, PREVIEW_SKILL, SKILL_NAME,
                                 InventoryDomain, _size)
from .perception import MetaToolTransport, PerceptionCache, PerceptionClient
from .providers import SAM, TRANSCRIBE, ProviderProfile
from .spark import ModelError, ChatClient
from .usage import BudgetScope, summarize, usage_rows
from .voicecount_probe import Recording, word_error_rate

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT/".lx"/"datasets"/"inventory-coco-v1"
LEDGER = ROOT/".lx"/"live-spike"/"pilot-budget.sqlite3"
APPROVED_DATASET = "1c673f8d2445afe2c675900a344b94942cacb3a89552d88961d3b96667767fcc"
SAMPLE_A, SAMPLE_B = "coco-calibration-04", "coco-calibration-05"
CATALOG = [
    {"kind": "segmentation", "name": "SAM 3.1", "capabilities": [
        "Image segmentation: finds every instance of a short noun phrase in a photo and returns one box and mask per instance"]},
    {"kind": "transcription", "name": "Muse Voice Transcribe", "capabilities": [
        "Speech-to-text: transcribes spoken audio (mono WAV) into text"]},
]
TOOLS = photo_tools(CATALOG) + COMPONENT_TOOLS
# Amended 29 Oct 2026 at the user's direction ("Use E2B instead"): the server had Gemma 4 E2B loaded.
LOCAL = ProviderProfile(kind="chat", endpoint="http://127.0.0.1:8888/v1/chat/completions",
                        model="gemma-4-E2B-it-GGUF", token_param="max_tokens",
                        accepts_images=True, supports_tools=True)
# The OpenCode surface's always-on files; a plain stand-in so the Windows local part needs no core import.
SURFACE = types.SimpleNamespace(always_on=("systemprompt.md", "guardrails.md", "LongTermMEMORY.md",
                                           "tool_descriptions/tool_guidance.md"))
VIEW_PROMPT = ("LX_INVENTORY_AGENT\nThis is a connectivity check. Call the view tool once for photo 1. After the "
               "image arrives, reply with one sentence describing what photo 1 shows. Do not call other tools.")
VIEW_CAPS = {"max_turns": 3, "max_tool_calls": 2}
KEEP_IMAGES = 2
MAX_VIEW_BYTES = 400_000
PARTS = {
    # chat: view check 3 + two agent runs at caps(3) = 13 turns each; segment: 6 direct + agent concepts.
    "meta": {"scope": "inventory-probe-v1", "cap_usd": "0.40", "chat": 30, "segment": 30, "transcribe": 4},
    "local": {"scope": "inventory-probe-local-v1", "cap_usd": "0.15", "chat": 16, "segment": 16, "transcribe": 2},
}
# Last recorded shared-ledger checkpoint (after the stopped S7 run, 29 Oct 2026).
CHECKPOINT_SPENT = Decimal("0.347744732")
CHECKPOINT_CALLS = 903


def probe_harness():
    """Base harness plus the preview skill, its trigger and the helper script (sample B only)."""
    return {"systemprompt.md": BASE_PROMPT, "guardrails.md": PREVIEW_GUARDRAIL, "LongTermMEMORY.md": "",
            "tool_descriptions/tool_guidance.md": "", f"skills/{SKILL_NAME}/SKILL.md": PREVIEW_SKILL,
            "scripts/sum_counts.py": PREVIEW_SCRIPT}


def base_harness():
    return {"systemprompt.md": BASE_PROMPT, "guardrails.md": "", "LongTermMEMORY.md": "",
            "tool_descriptions/tool_guidance.md": ""}


class Probe:
    def __init__(self, root=DATASET):
        self.root = Path(root)
        samples, self.truth, self.requests = load_inventory_dataset(self.root)
        self.samples = {s["sample_id"]: s for s in samples}
        self.surface = SURFACE

    def photos(self, item):
        return [(self.root/"worker_inputs"/p).read_bytes() for p in self.samples[item]["photos"]]

    def audio(self, item):
        return (self.root/"worker_inputs"/self.samples[item]["audio"]).read_bytes()

    def transcribe(self, perception, item):
        try:
            heard = perception.transcribe(self.audio(item), candidate="transcribe/"+item)
        except ModelError as exc:
            return {"error": str(exc)}
        return dict(heard, wer=word_error_rate(self.requests[item]["text"], heard["transcript"]))

    def segment(self, perception, item):
        kind, want = self.truth[item]["kind"], expected_photos(self.truth[item])
        out = {}
        for number, data in enumerate(self.photos(item), 1):
            try:
                found = perception.segment(data, kind, candidate=f"segment/{item}/{number}")
            except ModelError as exc:
                out[str(number)] = {"error": str(exc)}
                continue
            boxes = found["boxes"]
            out[str(number)] = {"concept": kind, "instances": len(boxes), "confident_0_5": sum(b.get("c", 1) >= 0.5 for b in boxes),
                                "truth": want[number], "box_sizes_match_image": all((b["w"], b["h"]) == _size(data) for b in boxes)}
        return out

    def _messages(self, item, prompt):
        sizes = [_size(p) for p in self.photos(item)]
        return InventoryDomain().messages(self.samples[item], prompt, sizes)

    def view_check(self, client, perception, item):
        handler = PhotoSetTools(perception, photos=self.photos(item), audio=self.audio(item), candidate="view/"+item)
        messages = [{"role": "developer", "content": VIEW_PROMPT}, self._messages(item, "")[1]]
        try:
            text, trace = run_agent(client, messages, TOOLS, handler, candidate="view/"+item, timeout=600,
                                    keep_images=KEEP_IMAGES, **VIEW_CAPS)
        except ModelError as exc:
            return {"error": str(exc)}
        calls = [s for s in trace if s["type"] == "tool"]
        return {"view_called": any(s["name"] == "view" and "result" in s for s in calls),
                "reply": text, "tool_calls": [{"name": s["name"], "arguments": s["arguments"], "error": s.get("error")}
                                              for s in calls],
                "stopped": next((s["reason"] for s in trace if s["type"] == "stopped"), None)}

    def agent(self, client, perception, item, harness, *, runner=None):
        photos = self.photos(item)
        handler = ComponentTools(PhotoSetTools(perception, photos=photos, audio=self.audio(item), candidate="agent/"+item),
                                 harness, runner=runner)
        try:
            text, trace = run_agent(client, self._messages(item, compose_prompt(self.surface, harness)), TOOLS, handler,
                                    candidate="agent/"+item, timeout=600, keep_images=KEEP_IMAGES, **caps(len(photos)))
        except ModelError as exc:
            return {"error": str(exc)}
        try:
            answer = json.loads(text.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
        except ValueError:
            answer = text
        judged = verify_inventory(self.truth[item], answer)
        return {"answer": answer, "score": judged.score, "failed_checks": [n for n, ok, _ in checklist(self.truth[item], answer) if not ok],
                "turns": sum(s["type"] == "assistant" for s in trace),
                "tool_calls": [{"name": s["name"], "arguments": s["arguments"], "error": s.get("error"),
                                "exit_code": (s.get("result") or {}).get("exit_code")} for s in trace if s["type"] == "tool"],
                "stopped": next((s["reason"] for s in trace if s["type"] == "stopped"), None)}


def run_meta(probe, client, perception, recorders, *, runner=None):
    report = {"transcribe": {item: probe.transcribe(perception, item) for item in (SAMPLE_A, SAMPLE_B)},
              "segment": {item: probe.segment(perception, item) for item in (SAMPLE_A, SAMPLE_B)},
              "view_check": probe.view_check(client, perception, SAMPLE_A),
              "agent": {SAMPLE_A: dict(harness="base", **probe.agent(client, perception, SAMPLE_A, base_harness())),
                        SAMPLE_B: dict(harness="probe skill + script",
                                       **probe.agent(client, perception, SAMPLE_B, probe_harness(), runner=runner))}}
    report["raw_shapes"] = {name: r.replies for name, r in recorders.items()}
    return report


def run_local(probe, client, perception, recorders):
    return {"view_check": probe.view_check(client, perception, SAMPLE_A),
            "agent": {SAMPLE_A: dict(harness="base", **probe.agent(client, perception, SAMPLE_A, base_harness()))},
            "raw_shapes": {name: r.replies for name, r in recorders.items()}}


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
    from .inventory_data import inventory_dataset_hash
    if args.approve_dataset != APPROVED_DATASET or inventory_dataset_hash(DATASET) != APPROVED_DATASET:
        parser.error("Dataset approval does not match this fixed request")
    from .live import _runtime_key
    from .live_transport import LiveChatTransport
    limits = PARTS[args.part]
    probe = Probe()
    ledger, state = guarded_ledger(limits["scope"])
    output = ROOT/".lx"/limits["scope"]
    output.mkdir(parents=True, exist_ok=True)
    # Exclusive marker prevents concurrent dispatch or accidental repeat runs.
    with (output/"started.json").open("x", encoding="utf-8") as marker:
        json.dump({"dataset_sha256": APPROVED_DATASET, **limits}, marker)
    scope = BudgetScope(ledger, limits["scope"], limits["cap_usd"])
    recorders = {"segment": Recording(MetaToolTransport(SAM, _runtime_key, call_limit=limits["segment"])),
                 "transcribe": Recording(MetaToolTransport(TRANSCRIBE, _runtime_key, call_limit=limits["transcribe"]))}
    perception = PerceptionClient(recorders["segment"], recorders["transcribe"], scope,
                                  PerceptionCache(ROOT/".lx"/"perception-cache"/"inventory-coco-v1"))
    contract = {"agent_tools": TOOLS, "image_types": ("jpeg",), "max_image_bytes": MAX_VIEW_BYTES,
                "max_images": KEEP_IMAGES, "call_limit": limits["chat"]}
    try:
        if args.part == "meta":
            client = ChatClient(LiveChatTransport(_runtime_key, **contract), scope, retries=0)
            report = run_meta(probe, client, perception, recorders)
        else:
            client = ChatClient(LiveChatTransport(None, profile=LOCAL, **contract), scope, retries=0, profile=LOCAL)
            report = run_local(probe, client, perception, recorders)
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
