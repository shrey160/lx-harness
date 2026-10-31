"""Agent tool loop: the acting model calls registered models as tools.

Tools are generated from catalog entries and their plain-language
descriptions (REQ-07). The controller binds every call to the current item's
media, so the agent never sends or receives raw image or audio bytes. Each
step is traced; the trace is what the improver later reads.
"""
from __future__ import annotations

import base64
import hashlib
import json

from .perception import concept_text
from .spark import IncompleteResponse

REGIONS = ("whole", "left", "right", "top", "bottom")
MAX_BOXES_SHOWN = 300
# A tool result carrying an image for the agent (JPEG bytes); see run_agent.
IMAGE_KEY = "_image"
# Whole-context loop: images held in one request. A sample has at most 7 photos, so all stay, with room for a close-up.
MAX_WINDOW_IMAGES = 8
WINDOW_CHARS = 1_400_000  # base64 text; the transport allows 2 MB for the whole request
NOTE_CHARS = 1_500


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


PHOTO_SEGMENT_PROPS = {"photo": {"type": "integer", "description": "Photo number, starting at 1."},
                       "concept": {"type": "string", "description": "A short noun phrase naming the objects."}}
VIEW = _tool(
    "view",
    "Look at one photo yourself. Without box: the whole photo, downscaled. With box [x1, y1, x2, y2] "
    "(pixel corners of that photo, e.g. from segment): a close-up of that area with a small margin. "
    "The image arrives in the next message.",
    {"photo": {"type": "integer", "description": "Photo number, starting at 1."},
     "box": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4,
             "description": "Optional [x1, y1, x2, y2] to zoom into."}},
    ["photo"])
PHOTO_COUNT_BOXES = _tool(
    "count_boxes",
    "Deterministic helper: count the boxes from an earlier segment call on one photo, after optional "
    "filtering. Drops boxes below min_confidence and merges boxes overlapping more than dedupe_iou "
    "(keeps the more confident one).",
    {"photo": {"type": "integer", "description": "Photo number of the earlier segment call."},
     "concept": {"type": "string", "description": "Concept of that segment call."},
     "min_confidence": {"type": "number", "description": "0 to 1; default 0."},
     "dedupe_iou": {"type": "number", "description": "0 to 1; 1 disables merging; default 1."}},
    ["photo", "concept"])


def photo_tools(entries):
    """Function tools for items with several photos: every perception call names its photo."""
    tools = []
    for entry in entries:
        if entry["kind"] == "transcription":
            tools.append(_tool("transcribe", _describe(entry, "Transcribes this item's audio; takes no arguments."), {}, []))
        elif entry["kind"] == "segmentation":
            tools.append(_tool("segment", _describe(entry, "Runs on one photo of this item; returns instance boxes "
                               "[x1, y1, x2, y2, confidence] in inclusive pixel corners of that photo."),
                               PHOTO_SEGMENT_PROPS, ["photo", "concept"]))
    if any(tool["function"]["name"] == "segment" for tool in tools):
        tools.append(PHOTO_COUNT_BOXES)
    return tools + [VIEW]


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


class PhotoSetTools:
    """Executes tool calls for an item with several photos; media stays on the controller.

    `view` returns the rendered image under IMAGE_KEY; the loop sends it to the
    agent as a follow-up message and the trace keeps only its hash.
    """

    def __init__(self, perception, *, photos, audio=None, candidate="", max_side=768, segmentation=True, counting=True):
        if not photos:
            raise ValueError("an item needs at least one photo")
        self.perception, self.photos, self.audio, self.candidate = perception, list(photos), audio, candidate
        self.max_side, self.segmentation, self.counting = max_side, segmentation, counting
        self.segments = {}

    def _photo(self, arguments):
        number = arguments.get("photo")
        if type(number) is not int or not 1 <= number <= len(self.photos):
            raise ValueError(f"photo must be a number from 1 to {len(self.photos)}")
        return number

    def __call__(self, name, arguments):
        if (name in {"segment", "count_boxes"} and not self.segmentation) or (name == "count_boxes" and not self.counting):
            raise ValueError(f"unknown tool {name!r}")  # switched off: a model naming it anyway reaches nothing
        if name == "transcribe":
            if arguments or self.audio is None:
                raise ValueError("transcribe takes no arguments and needs this item's audio")
            return {"transcript": self.perception.transcribe(self.audio, candidate=self.candidate)["transcript"]}
        if name == "segment":
            if set(arguments) != {"photo", "concept"}:
                raise ValueError("segment takes exactly photo and concept")
            number = self._photo(arguments)
            result = self.perception.segment(self.photos[number - 1], arguments["concept"], candidate=self.candidate)
            self.segments[(number, result["concept"])] = result["boxes"]
            shown = result["boxes"][:MAX_BOXES_SHOWN]
            return {"photo": number, "concept": result["concept"], "instances": len(result["boxes"]),
                    "boxes": [[b["x1"], b["y1"], b["x2"], b["y2"], round(b.get("c", 1.0), 2)] for b in shown],
                    "truncated": len(result["boxes"]) > len(shown)}
        if name == "count_boxes":
            if not {"photo", "concept"} <= set(arguments) <= {"photo", "concept", "min_confidence", "dedupe_iou"}:
                raise ValueError("count_boxes takes photo, concept and optional min_confidence, dedupe_iou")
            key = (self._photo(arguments), concept_text(arguments["concept"]))
            if key not in self.segments:
                raise ValueError("call segment for this photo and concept first")
            result = count_boxes(self.segments[key], min_confidence=arguments.get("min_confidence", 0.0),
                                 dedupe_iou=arguments.get("dedupe_iou", 1.0))
            result.pop("region")
            return {"photo": key[0], "concept": key[1], **result}
        if name == "view":
            if not set(arguments) <= {"photo", "box"}:
                raise ValueError("view takes photo and an optional box")
            number = self._photo(arguments)
            image, shown, size = render_view(self.photos[number - 1], arguments.get("box"), max_side=self.max_side)
            return {"photo": number, "photo_size": list(size), "shown_box": list(shown), IMAGE_KEY: image}
        raise ValueError(f"unknown tool {name!r}")


def render_view(data, box=None, *, max_side=768, margin=0.15, quality=85):
    """(JPEG bytes, shown [x1, y1, x2, y2], (width, height)) for a photo or a zoomed box."""
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(data)) as source:
        image = source.convert("RGB")
    width, height = image.size
    if box is None:
        shown = (0, 0, width - 1, height - 1)
    else:
        if (type(box) is not list or len(box) != 4 or any(type(v) is not int for v in box)
                or not (0 <= box[0] <= box[2] < width and 0 <= box[1] <= box[3] < height)):
            raise ValueError(f"box must be [x1, y1, x2, y2] inside the photo ({width}x{height})")
        pad_x = max(8, int((box[2] - box[0] + 1) * margin))
        pad_y = max(8, int((box[3] - box[1] + 1) * margin))
        shown = (max(0, box[0] - pad_x), max(0, box[1] - pad_y),
                 min(width - 1, box[2] + pad_x), min(height - 1, box[3] + pad_y))
        image = image.crop((shown[0], shown[1], shown[2] + 1, shown[3] + 1))
    scale = max_side / max(image.size)
    if scale < 1:
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))))
    out = BytesIO()
    image.save(out, "JPEG", quality=quality)
    return out.getvalue(), shown, (width, height)


# --- loop -------------------------------------------------------------------

def run_agent(client, messages, tools, handler, *, candidate="", max_turns=8, max_tool_calls=12, timeout=300,
              keep_images=2, whole_context=False):
    """Run the agent until it answers or a cap stops it; never raises for tool errors.

    Returns (final_text, trace). A stopped run returns an empty answer, which
    the verifier scores as a failed item. Images returned by tools (IMAGE_KEY)
    follow the turn's tool messages as one user message; only the latest
    `keep_images` stay in the history, older ones become a text note.

    `whole_context` is for spec-driven tasks; the paused experiments run without
    it. It gives what any agent runtime gives: the conversation stays in view. An
    image already held is not sent again, close-ups leave the window first, and
    reasoning a provider returns stays in the history as text. It holds no strategy
    (D-021): how to spend turns, when to answer and how to call tools are for the
    harness to learn from the run's own traces, as in the Harness\u00b2 paper.
    """
    use_tools = tools if client.profile.supports_tools else None
    history = [dict(m) for m in messages]
    trace = [{"type": "tools", "names": [t["function"]["name"] for t in use_tools or []]}]
    calls, window = 0, []
    for _ in range(max_turns):
        try:
            message, usage = client.chat(history, tools=use_tools, role="worker", phase="execute",
                                         candidate=candidate, timeout=timeout)
        except IncompleteResponse:
            # Billed and recorded by the client; the item fails but keeps its trace.
            trace.append({"type": "stopped", "reason": "model reply was cut off before it finished"})
            return "", trace
        notes = message.get("reasoning", "")[:NOTE_CHARS] if whole_context else ""
        trace.append({"type": "assistant", "content": message["content"],
                      "tool_calls": [{"name": c["name"], "arguments": c["arguments"]} for c in message["tool_calls"]],
                      "output_tokens": usage["output_tokens"], **({"reasoning": notes} if notes else {})})
        if not message["tool_calls"]:
            return (message["content"] or "").strip(), trace
        # Empty text with reasoning would leave the model nothing of this turn; a provider-neutral note keeps it.
        written = message["content"] or (f"(my notes) {notes}" if notes else None)
        history.append({"role": "assistant", "content": written, "tool_calls": [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
            for c in message["tool_calls"]]})
        images = []
        for call in message["tool_calls"]:
            calls += 1
            if calls > max_tool_calls:
                trace.append({"type": "stopped", "reason": f"more than {max_tool_calls} tool calls"})
                return "", trace
            step = {"type": "tool", "name": call["name"], "arguments": call["arguments"]}
            try:
                result = handler(call["name"], call["arguments"])
                if isinstance(result, dict) and IMAGE_KEY in result:
                    result = dict(result)
                    image = result.pop(IMAGE_KEY)
                    if whole_context and any(_image_url(image) == held["url"] for held in window):
                        result["image"] = "already in this conversation; no new copy sent"
                    else:
                        images.append((call["arguments"], image))
                        result["image"] = f"sent in the next message (sha256 {hashlib.sha256(image).hexdigest()[:12]})"
                step["result"] = result
                content = json.dumps({"result": step["result"]}, allow_nan=False)
            except ValueError as exc:
                step["error"] = str(exc)
                content = json.dumps({"error": str(exc)})
            trace.append(step)
            history.append({"role": "tool", "tool_call_id": call["id"], "content": content})
        if images and whole_context:
            _hold_images(history, window, images, keep_images)
        elif images:
            _attach_images(history, images, keep_images)
    trace.append({"type": "stopped", "reason": f"no answer within {max_turns} turns"})
    return "", trace


def _image_url(data):
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def _view_label(args):
    return f"photo {args.get('photo')}" + (f" box {args['box']}" if "box" in args else "")


def _hold_images(history, window, images, keep):
    """Add this turn's images; earlier ones leave when the window is full: close-ups first, then the oldest."""
    added = [{"url": _image_url(data), "label": _view_label(args), "zoom": "box" in args} for args, data in images]
    parts = [{"type": "text", "text": "Images from your view calls, in order: " + "; ".join(i["label"] for i in added)}]
    history.append({"role": "user", "content": parts+[{"type": "image_url", "image_url": {"url": i["url"]}} for i in added]})
    earlier, gone = window[:], {}
    window += added
    while earlier and (len(window) > keep or sum(len(i["url"]) for i in window) > WINDOW_CHARS):
        leaving = next((i for i in earlier if i["zoom"]), earlier[0])
        earlier.remove(leaving)
        window.remove(leaving)
        gone[leaving["url"]] = leaving["label"]
    for message in history:
        if gone and message["role"] == "user" and isinstance(message.get("content"), list):
            message["content"] = [{"type": "text", "text": f"[{gone[part['image_url']['url']]}: no longer attached]"}
                                  if part["type"] == "image_url" and part["image_url"]["url"] in gone else part
                                  for part in message["content"]]


def _attach_images(history, images, keep):
    note = "; ".join(_view_label(args) for args, _ in images)
    parts = [{"type": "text", "text": "Images from your view calls, in order: " + note}]
    parts += [{"type": "image_url", "image_url": {"url": _image_url(data)}} for _, data in images]
    history.append({"role": "user", "content": parts})
    budget = keep
    for message in reversed(history):
        content = message.get("content")
        if message["role"] != "user" or not isinstance(content, list) or not str(content[0].get("text", "")).startswith(
                "Images from your view calls"):
            continue
        kept = []
        for part in reversed(content):
            if part["type"] != "image_url":
                kept.append(part)
            elif budget > 0:
                kept.append(part)
                budget -= 1
            else:
                kept.append({"type": "text", "text": "[an earlier view image was removed to save space; "
                                                     "you have already seen it, so work from what you noted "
                                                     "and view it again only to check one detail]"})
        message["content"] = kept[::-1]


def render_trace(trace, answer, *, obs_trunc=50_000):
    """Markdown trajectory for the improver: every call, argument and result."""
    lines = []
    for step in trace:
        if step["type"] == "tools":
            lines.append("[TOOLS] " + (", ".join(step["names"]) or "none available"))
        elif step["type"] == "assistant":
            if step["content"]:
                lines.append("[AGENT]\n" + step["content"].strip())
            if step.get("reasoning"):
                lines.append("[REASONING]\n" + step["reasoning"].strip())
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
