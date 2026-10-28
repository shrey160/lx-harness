"""Agent tool loop: the acting model calls registered models as tools.

Tools are generated from catalog entries and their plain-language
descriptions (REQ-07). The controller binds every call to the current item's
media, so the agent never sends or receives raw image or audio bytes. Each
step is traced; the trace is what the improver later reads.
"""
from __future__ import annotations

import hashlib
import json

from .perception import concept_text

REGIONS = ("whole", "left", "right", "top", "bottom")
MAX_BOXES_SHOWN = 300


def _tool(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": required, "additionalProperties": False}}}


def _describe(entry, use):
    return f"{entry['name']}: {'; '.join(entry['capabilities'])}. {use}"


COUNT_BOXES = _tool(
    "count_boxes",
    "Deterministic helper: count the boxes from an earlier segment call, after optional filtering. "
    "Drops boxes below min_confidence, merges boxes overlapping more than dedupe_iou (keeps the more "
    "confident one), and keeps boxes whose centre lies in the region of the image.",
    {"concept": {"type": "string", "description": "Concept of an earlier segment call; default: the last one."},
     "min_confidence": {"type": "number", "description": "0 to 1; default 0."},
     "dedupe_iou": {"type": "number", "description": "0 to 1; 1 disables merging; default 1."},
     "region": {"type": "string", "enum": list(REGIONS), "description": "Default whole."}},
    [])


def agent_tools(entries):
    """Function tools for the catalog entries the agent may call, plus helpers."""
    tools = []
    for entry in entries:
        if entry["kind"] == "transcription":
            tools.append(_tool("transcribe", _describe(entry, "Transcribes this item's audio; takes no arguments."), {}, []))
        elif entry["kind"] == "segmentation":
            tools.append(_tool("segment", _describe(entry, "Runs on this item's image; returns instance boxes "
                               "[x1, y1, x2, y2, confidence] in inclusive pixel corners."),
                               {"concept": {"type": "string", "description": "A short noun phrase naming the objects."}},
                               ["concept"]))
    if any(tool["function"]["name"] == "segment" for tool in tools):
        tools.append(COUNT_BOXES)
    return tools


def tool_identity(tools):
    return hashlib.sha256(json.dumps(tools, sort_keys=True).encode()).hexdigest()


# --- deterministic helpers --------------------------------------------------

def iou(a, b):
    width = min(a["x2"], b["x2"]) - max(a["x1"], b["x1"]) + 1
    height = min(a["y2"], b["y2"]) - max(a["y1"], b["y1"]) + 1
    if width <= 0 or height <= 0:
        return 0.0
    inter = width * height
    area = lambda r: (r["x2"] - r["x1"] + 1) * (r["y2"] - r["y1"] + 1)
    return inter / (area(a) + area(b) - inter)


def in_region(box, region):
    cx, cy = (box["x1"] + box["x2"] + 1) / 2, (box["y1"] + box["y2"] + 1) / 2
    return {"whole": True, "left": cx < box["w"] / 2, "right": cx >= box["w"] / 2,
            "top": cy < box["h"] / 2, "bottom": cy >= box["h"] / 2}[region]


def count_boxes(boxes, *, min_confidence=0.0, dedupe_iou=1.0, region="whole"):
    for value in (min_confidence, dedupe_iou):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError("min_confidence and dedupe_iou must be numbers from 0 to 1")
    if region not in REGIONS:
        raise ValueError("region must be one of " + ", ".join(REGIONS))
    confident = [b for b in boxes if b.get("c", 1.0) >= min_confidence]
    kept = []
    for box in sorted(confident, key=lambda b: -b.get("c", 1.0)):
        if dedupe_iou < 1 and any(iou(box, other) > dedupe_iou for other in kept):
            continue
        kept.append(box)
    inside = [b for b in kept if in_region(b, region)]
    return {"count": len(inside), "region": region, "boxes_in": len(boxes),
            "removed_low_confidence": len(boxes) - len(confident),
            "removed_duplicates": len(confident) - len(kept)}


# --- item binding -----------------------------------------------------------

class ItemTools:
    """Executes tool calls for one item; media stays on the controller."""

    def __init__(self, perception, *, image=None, audio=None, candidate=""):
        self.perception, self.image, self.audio, self.candidate = perception, image, audio, candidate
        self.segments = {}
        self.last = None

    def __call__(self, name, arguments):
        if name == "transcribe":
            if arguments or self.audio is None:
                raise ValueError("transcribe takes no arguments and needs this item's audio")
            return {"transcript": self.perception.transcribe(self.audio, candidate=self.candidate)["transcript"]}
        if name == "segment":
            if set(arguments) != {"concept"} or self.image is None:
                raise ValueError("segment takes exactly one argument, concept")
            result = self.perception.segment(self.image, arguments["concept"], candidate=self.candidate)
            self.segments[result["concept"]] = result["boxes"]
            self.last = result["concept"]
            shown = result["boxes"][:MAX_BOXES_SHOWN]
            return {"concept": result["concept"], "instances": len(result["boxes"]),
                    "boxes": [[b["x1"], b["y1"], b["x2"], b["y2"], round(b.get("c", 1.0), 2)] for b in shown],
                    "truncated": len(result["boxes"]) > len(shown)}
        if name == "count_boxes":
            if not set(arguments) <= {"concept", "min_confidence", "dedupe_iou", "region"}:
                raise ValueError("unknown count_boxes argument")
            concept = concept_text(arguments["concept"]) if "concept" in arguments else self.last
            if concept not in self.segments:
                raise ValueError("call segment for this concept first")
            return count_boxes(self.segments[concept], min_confidence=arguments.get("min_confidence", 0.0),
                               dedupe_iou=arguments.get("dedupe_iou", 1.0), region=arguments.get("region", "whole"))
        raise ValueError(f"unknown tool {name!r}")


# --- loop -------------------------------------------------------------------

def run_agent(client, messages, tools, handler, *, candidate="", max_turns=8, max_tool_calls=12, timeout=300):
    """Run the agent until it answers or a cap stops it; never raises for tool errors.

    Returns (final_text, trace). A stopped run returns an empty answer, which
    the verifier scores as a failed item.
    """
    use_tools = tools if client.profile.supports_tools else None
    history = [dict(m) for m in messages]
    trace = [{"type": "tools", "names": [t["function"]["name"] for t in use_tools or []]}]
    calls = 0
    for _ in range(max_turns):
        message, usage = client.chat(history, tools=use_tools, role="worker", phase="execute",
                                     candidate=candidate, timeout=timeout)
        trace.append({"type": "assistant", "content": message["content"],
                      "tool_calls": [{"name": c["name"], "arguments": c["arguments"]} for c in message["tool_calls"]],
                      "output_tokens": usage["output_tokens"]})
        if not message["tool_calls"]:
            return (message["content"] or "").strip(), trace
        history.append({"role": "assistant", "content": message["content"], "tool_calls": [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
            for c in message["tool_calls"]]})
        for call in message["tool_calls"]:
            calls += 1
            if calls > max_tool_calls:
                trace.append({"type": "stopped", "reason": f"more than {max_tool_calls} tool calls"})
                return "", trace
            step = {"type": "tool", "name": call["name"], "arguments": call["arguments"]}
            try:
                step["result"] = handler(call["name"], call["arguments"])
                content = json.dumps({"result": step["result"]}, allow_nan=False)
            except ValueError as exc:
                step["error"] = str(exc)
                content = json.dumps({"error": str(exc)})
            trace.append(step)
            history.append({"role": "tool", "tool_call_id": call["id"], "content": content})
    trace.append({"type": "stopped", "reason": f"no answer within {max_turns} turns"})
    return "", trace


def render_trace(trace, answer, *, obs_trunc=50_000):
    """Markdown trajectory for the improver: every call, argument and result."""
    lines = []
    for step in trace:
        if step["type"] == "tools":
            lines.append("[TOOLS] " + (", ".join(step["names"]) or "none available"))
        elif step["type"] == "assistant":
            if step["content"]:
                lines.append("[AGENT]\n" + step["content"].strip())
            for call in step["tool_calls"]:
                lines.append(f"[CALL] {call['name']} {json.dumps(call['arguments'], sort_keys=True)}")
        elif step["type"] == "tool":
            body = json.dumps(step["result"]) if "result" in step else "ERROR: " + step["error"]
            if len(body) > obs_trunc:
                body = body[:obs_trunc] + f" ... [{len(body) - obs_trunc} characters truncated]"
            lines.append(f"[RESULT] {step['name']}\n{body}")
        elif step["type"] == "stopped":
            lines.append("[STOPPED] " + step["reason"])
    lines.append("[FINAL ANSWER]\n" + (answer or "(none)"))
    return "\n".join(lines)
