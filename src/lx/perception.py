"""Model tools: one transport and client, and a format per wire shape.

A `ToolFormat` says how one kind of API is called, read and priced. SAM 3.1 and
Muse Voice Transcribe are the first two; PROD-006 T3 adds others.

The agent never sends media itself; the controller binds the current item's
image or audio and calls these clients. Wire shapes follow the Meta docs read
on 2026-10-29 (https://dev.meta.ai/docs/sam/segmenting,
https://dev.meta.ai/docs/sam/reading-segmentation,
https://dev.meta.ai/docs/speech-to-text). Details marked UNVERIFIED are
checked by the S5 live probe before any experiment relies on them.

Every live call reserves its list price in the shared ledger first and
settles it after. Results are cached by media hash and arguments, so an
identical call (base vs candidate, or a later trial) costs nothing and
returns the same result.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import random
import re
import tempfile
import urllib.error
import urllib.request
import uuid
import wave
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from .providers import SAM, TRANSCRIBE
from .spark import ModelError, RateLimited, failure_reason

MAX_IMAGE_BYTES = 2_000_000
MAX_AUDIO_BYTES = 32_000_000
MAX_AUDIO_MS = 600_000
MAX_RESPONSE_BYTES = 16_000_000
AUDIO_RATES = (16_000, 24_000)
_IMAGE_TYPES = {b"\x89PNG\r\n\x1a\n": "image/png", b"\xff\xd8\xff": "image/jpeg"}
_CONCEPT = re.compile(r"[A-Za-z][A-Za-z0-9 '\-]{0,59}")
# Box tokens only: mask payloads are base85 and may contain `|`, `;` or `<`,
# so a strict key=number grammar keeps mask bytes from parsing as a box.
_BOX = re.compile(r"<\|box;((?:[a-z][a-z0-9]*=-?[0-9.]+;)*[a-z][a-z0-9]*=-?[0-9.]+)\|>")


# --- media checks -----------------------------------------------------------

def image_type(data):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_IMAGE_BYTES:
        raise ModelError("image outside the approved size bound")
    for magic, mime in _IMAGE_TYPES.items():
        if data.startswith(magic):
            return mime
    raise ModelError("image must be PNG or JPEG")


def wav_duration_ms(data):
    """Duration of a WAV the transcription API accepts; reject anything else."""
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_AUDIO_BYTES:
        raise ModelError("audio outside the approved size bound")
    try:
        with wave.open(io.BytesIO(data)) as clip:
            channels, width, rate, frames = clip.getnchannels(), clip.getsampwidth(), clip.getframerate(), clip.getnframes()
    except (wave.Error, EOFError):
        raise ModelError("audio must be a PCM WAV file") from None
    if channels != 1 or width != 2 or rate not in AUDIO_RATES or frames < 1:
        raise ModelError("audio must be mono 16-bit PCM at 16 or 24 kHz")
    duration = frames * 1000 // rate + (frames * 1000 % rate > 0)
    if duration > MAX_AUDIO_MS:
        raise ModelError("audio longer than the 10-minute limit")
    return duration


def concept_text(value):
    """A short noun phrase only, so the agent cannot inject point or box tokens."""
    if not isinstance(value, str) or not _CONCEPT.fullmatch(value.strip()):
        raise ValueError("concept must be a short noun phrase (letters, digits, spaces; at most 60 characters)")
    return " ".join(value.split()).lower()


# --- SAM wire format --------------------------------------------------------

def sam_body(image, concept, *, include_confidence=True):
    mime = image_type(image)
    url = f"data:{mime};base64," + base64.b64encode(image).decode()
    body = {"model": SAM.model, "stream": False,
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": concept_text(concept)},
                {"type": "input_image", "image_url": url}]}]}
    if include_confidence:
        body["metadata"] = {"include_confidence": "true"}
    return body


def sam_text(raw):
    """Output text of a non-streamed Responses reply. UNVERIFIED shape: accepts
    a top-level `output_text` string or `output[].content[]` output_text parts."""
    if not isinstance(raw, dict):
        raise ModelError("segmentation reply malformed")
    if isinstance(raw.get("output_text"), str):
        return raw["output_text"]
    output = raw.get("output")
    if not isinstance(output, list):
        raise ModelError("segmentation reply has no output")
    parts = []
    for item in output:
        for part in (item.get("content") or []) if isinstance(item, dict) else []:
            if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
    return "".join(parts)


def parse_boxes(text, size=None):
    """Instance boxes from SAM output text; masks are ignored.

    Boxes are inclusive source-pixel corners. Fields are read by key, not by
    position. An empty text means zero matches.
    """
    if not isinstance(text, str):
        raise ModelError("segmentation output is not text")
    boxes = []
    for fields in _BOX.findall(text):
        values = {}
        for pair in fields.split(";"):
            key, sep, value = pair.partition("=")
            if not sep or key in values:
                raise ModelError("segmentation box token malformed")
            values[key] = value
        try:
            box = {key: int(values[key]) for key in ("x1", "y1", "x2", "y2", "w", "h")}
            confidence = float(values["c"]) if "c" in values else None
        except (KeyError, ValueError):
            raise ModelError("segmentation box token malformed") from None
        if not (0 <= box["x1"] <= box["x2"] < box["w"] and 0 <= box["y1"] <= box["y2"] < box["h"]):
            raise ModelError("segmentation box outside the image")
        if size is not None and (box["w"], box["h"]) != tuple(size):
            raise ModelError("segmentation box refers to a different image size")
        if confidence is not None:
            if not 0 <= confidence <= 1:
                raise ModelError("segmentation confidence outside 0-1")
            box["c"] = confidence
        boxes.append(box)
    if text.count("<|box;") != len(boxes):
        raise ModelError("segmentation output could not be parsed")
    return boxes


def format_boxes(boxes, frame=0):
    """Inverse of parse_boxes for preview fakes; masks are a fixed placeholder."""
    records = []
    for index, box in enumerate(boxes):
        fields = ";".join(f"{k}={box[k]}" for k in ("x1", "y1", "x2", "y2", "w", "h"))
        if "c" in box:
            fields += f";c={box['c']:.3f}"
        records.append(f"{index}<|box;{fields}|><|mask;x={box['x1']};y={box['y1']};data=0,~|>")
    return (f"<{frame}f>" + ",".join(records)) if records else ""


# --- Transcribe wire format -------------------------------------------------

def transcribe_parts(audio):
    wav_duration_ms(audio)
    # Settings from the documented example (speech-to-text guide, read 29 Oct);
    # PUSH_TO_TALK returns the whole transcript without turns. Confirmed by the S5 probe.
    return {"request": json.dumps({"model": TRANSCRIBE.model, "audioEncoding": "WAV", "mode": "PUSH_TO_TALK"}),
            "audio": audio}


def multipart(parts, boundary=None):
    boundary = boundary or "lx" + uuid.uuid4().hex
    chunks = []
    for name, value in parts.items():
        if isinstance(value, bytes):
            head = (f'Content-Disposition: form-data; name="{name}"; filename="{name}.wav"\r\n'
                    "Content-Type: audio/wav\r\n\r\n")
        else:
            head = (f'Content-Disposition: form-data; name="{name}"\r\n'
                    "Content-Type: application/json\r\n\r\n")
            value = value.encode()
        chunks += [f"--{boundary}\r\n".encode(), head.encode(), value, b"\r\n"]
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), "multipart/form-data; boundary=" + boundary


def parse_transcript(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("transcript"), str):
        raise ModelError("transcription reply malformed")
    duration = raw.get("audioDurationMs")
    if type(duration) is not int or not 0 <= duration <= MAX_AUDIO_MS:
        raise ModelError("transcription reply has no valid duration")
    return {"transcript": raw["transcript"].strip(), "duration_ms": duration}


# --- formats ----------------------------------------------------------------

def _sam_send(payload):
    if not isinstance(payload, dict) or payload.get("model") != SAM.model:
        raise ModelError("request outside the approved segmentation contract")
    return json.dumps(payload, allow_nan=False).encode(), "application/json"


def _transcribe_send(payload):
    if not isinstance(payload, dict) or set(payload) != {"request", "audio"}:
        raise ModelError("request outside the approved transcription contract")
    wav_duration_ms(payload["audio"])
    return multipart(payload)


def _audio_price(milliseconds):
    return (Decimal(milliseconds) / 3_600_000 * TRANSCRIBE.audio_hour_price).quantize(Decimal("1E-9"))


@dataclass(frozen=True)
class ToolFormat:
    """How one API is called, read and priced. `billed_unread`: it charges whether or not its reply parses."""
    name: str
    tool: str
    endpoint: str
    model: str
    request: Callable[..., Any]
    send: Callable[[Any], tuple]
    read: Callable[[Any], Any]
    reserve: Callable[[bytes], Decimal]
    settle: Callable[[Any, bytes], Decimal]
    billed_unread: bool


META_SAM = ToolFormat("meta-sam", "segment", SAM.endpoint, SAM.model, sam_body, _sam_send,
                      lambda raw: parse_boxes(sam_text(raw)), lambda media: SAM.image_price,
                      lambda result, media: SAM.image_price, True)
# Reserve the price of the header duration plus one second of rounding room.
META_TRANSCRIBE = ToolFormat("meta-transcribe", "transcribe", TRANSCRIBE.endpoint, TRANSCRIBE.model, transcribe_parts,
                             _transcribe_send, parse_transcript, lambda media: _audio_price(wav_duration_ms(media)+1000),
                             lambda result, media: _audio_price(result["duration_ms"]), False)
FORMATS = {tool_format.name: tool_format for tool_format in (META_SAM, META_TRANSCRIBE)}


# --- transports -------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ToolTransport:
    """One tool endpoint, bounded calls, error bodies withheld."""

    def __init__(self, tool_format, credential_loader, *, call_limit, opener=None):
        if not callable(credential_loader):
            raise ModelError("a runtime credential loader is required")
        if type(call_limit) is not int or call_limit < 1:
            raise ModelError("call limit outside approved bounds")
        self.format, self.call_limit, self.calls = tool_format, call_limit, 0
        self._key = credential_loader()
        if not isinstance(self._key, str) or not self._key:
            raise ModelError("tool credential unavailable to the application runtime")
        self._opener = opener or urllib.request.build_opener(_NoRedirect())

    def __call__(self, payload, timeout):
        data, content_type = self.format.send(payload)
        if self.calls >= self.call_limit:
            raise ModelError("live tool call limit for this run reached")
        self.calls += 1
        request = urllib.request.Request(self.format.endpoint, data=data, method="POST", headers={
            "Authorization": "Bearer " + self._key, "Content-Type": content_type})
        try:
            with self._opener.open(request, timeout=min(timeout, 120)) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                try:
                    delay = float(exc.headers.get("Retry-After", 0))
                except (TypeError, ValueError):
                    delay = 0
                raise RateLimited(delay) from None
            raise ModelError(f"tool request failed (HTTP {exc.code}); response body withheld") from None
        except OSError:
            raise ModelError("tool connection failed; cost remains reserved") from None
        if len(body) > MAX_RESPONSE_BYTES:
            raise ModelError("tool response exceeded the limit")
        try:
            return json.loads(body)
        except ValueError:
            raise ModelError("tool response is not JSON; cost remains reserved") from None


class MetaToolTransport(ToolTransport):
    """The paused experiments' name for the two built-in tools."""

    def __init__(self, profile, credential_loader, *, call_limit, opener=None):
        if profile not in (SAM, TRANSCRIBE):
            raise ModelError("only the fixed Meta tool profiles are approved")
        super().__init__(META_SAM if profile is SAM else META_TRANSCRIBE, credential_loader, call_limit=call_limit, opener=opener)
        self.profile = profile


# --- cache and clients ------------------------------------------------------

def media_hash(data):
    return hashlib.sha256(data).hexdigest()


class PerceptionCache:
    """Controller-side results keyed by tool, model, media hash and arguments."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.hits = 0

    @staticmethod
    def key(tool, model, media, arguments):
        value = {"tool": tool, "model": model, "media_sha256": media, "arguments": arguments}
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest(), value

    def get(self, key, identity):
        path = self.root / f"{key}.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if record.get("identity") != identity:
            return None
        self.hits += 1
        return record["result"]

    def put(self, key, identity, result):
        handle, temp = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({"identity": identity, "result": result}, stream, allow_nan=False)
        os.replace(temp, self.root / f"{key}.json")


class PerceptionClient:
    """Budgeted, cached calls to a segmentation tool and a transcription tool."""

    def __init__(self, segment_transport, transcribe_transport, ledger, cache, *, segment_format=META_SAM,
                 transcribe_format=META_TRANSCRIBE):
        self.segment_transport, self.transcribe_transport = segment_transport, transcribe_transport
        self.segment_format, self.transcribe_format = segment_format, transcribe_format
        self.ledger, self.cache = ledger, cache
        self.live_calls = 0

    def _call(self, transport, payload, cost, phase, candidate, timeout):
        reservation = self.ledger.reserve(cost, "tool", phase, candidate)
        try:
            raw = transport(payload, timeout)
        except BaseException as exc:
            self.ledger.unknown(reservation, failure_reason(exc))
            raise
        self.live_calls += 1
        return raw, reservation

    def _use(self, tool_format, transport, media, payload, candidate, timeout):
        """One paid call; what the reply reads as. A reply is settled once its cost is known."""
        raw, reservation = self._call(transport, payload, tool_format.reserve(media), tool_format.tool, candidate, timeout)
        settle = lambda result: self.ledger.settle(reservation, cost_usd=tool_format.settle(result, media), input_tokens=0, output_tokens=0)
        if tool_format.billed_unread:
            settle(None)
            return tool_format.read(raw)
        try:
            result = tool_format.read(raw)
        except BaseException as exc:
            self.ledger.unknown(reservation, failure_reason(exc))
            raise
        settle(result)
        return result

    def segment(self, image, concept, *, candidate="", timeout=120):
        concept, tool_format = concept_text(concept), self.segment_format
        key, identity = self.cache.key("segment", tool_format.model, media_hash(image), {"concept": concept})
        cached = self.cache.get(key, identity)
        if cached is not None:
            return cached
        boxes = self._use(tool_format, self.segment_transport, image, tool_format.request(image, concept), candidate, timeout)
        result = {"concept": concept, "boxes": boxes}
        self.cache.put(key, identity, result)
        return result

    def transcribe(self, audio, *, candidate="", timeout=120):
        tool_format = self.transcribe_format
        key, identity = self.cache.key("transcribe", tool_format.model, media_hash(audio), {})
        cached = self.cache.get(key, identity)
        if cached is not None:
            return cached
        result = self._use(tool_format, self.transcribe_transport, audio, tool_format.request(audio), candidate, timeout)
        self.cache.put(key, identity, result)
        return result


# --- preview fakes (offline plumbing only; not capability evidence) ---------

class FakeSegmentation:
    """Wire-format SAM double built from controller truth points.

    `scenes` maps image sha256 to {"size": [w, h], "points": [[x, y], ...],
    "concepts": [accepted noun phrases]}. Noise is seeded per image and
    concept: missed instances, duplicate boxes and low-confidence false
    positives, so harness choices (threshold, dedupe) change the count.
    """

    def __init__(self, scenes, *, miss=0.05, duplicate=0.05, false_positive=0.1, seed=20261029):
        self.scenes, self.calls = scenes, 0
        self.miss, self.duplicate, self.false_positive, self.seed = miss, duplicate, false_positive, seed

    def __call__(self, body, timeout):
        self.calls += 1
        parts = body["input"][0]["content"]
        concept = parts[0]["text"]
        image = base64.b64decode(parts[1]["image_url"].split(",", 1)[1])
        scene = self.scenes[media_hash(image)]
        width, height = scene["size"]
        rng = random.Random(f"{self.seed}:{media_hash(image)}:{concept}")
        boxes = []
        if concept in scene["concepts"]:
            for x, y in scene["points"]:
                if rng.random() < self.miss:
                    continue
                boxes.append(self._box(x, y, width, height, rng, 0.6, 0.99))
                if rng.random() < self.duplicate:
                    boxes.append(self._box(x + 1, y + 1, width, height, rng, 0.4, 0.8))
        extra = int(len(scene["points"]) * self.false_positive) if concept in scene["concepts"] else rng.randint(0, 2)
        for _ in range(extra):
            boxes.append(self._box(rng.uniform(0, width - 1), rng.uniform(0, height - 1), width, height, rng, 0.05, 0.45))
        return {"output_text": format_boxes(boxes)}

    @staticmethod
    def _box(x, y, width, height, rng, low, high):
        half = rng.randint(3, 8)
        x, y = int(min(max(x, 0), width - 1)), int(min(max(y, 0), height - 1))
        return {"x1": max(0, x - half), "y1": max(0, y - half), "x2": min(width - 1, x + half),
                "y2": min(height - 1, y + half), "w": width, "h": height, "c": round(rng.uniform(low, high), 3)}


class FakeTranscription:
    """Transcribe double: `scripts` maps audio sha256 to the spoken text."""

    def __init__(self, scripts):
        self.scripts, self.calls = scripts, 0

    def __call__(self, payload, timeout):
        self.calls += 1
        audio = payload["audio"]
        return {"sessionId": "preview", "transcript": self.scripts[media_hash(audio)],
                "audioDurationMs": wav_duration_ms(audio), "turns": []}
