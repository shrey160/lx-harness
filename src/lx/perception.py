"""Fixed Meta perception tools: SAM 3.1 segmentation and Muse Voice Transcribe.

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
from decimal import Decimal
from pathlib import Path

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


# --- transports -------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class MetaToolTransport:
    """One fixed Meta tool endpoint, bounded calls, error bodies withheld."""

    def __init__(self, profile, credential_loader, *, call_limit, opener=None):
        if profile not in (SAM, TRANSCRIBE):
            raise ModelError("only the fixed Meta tool profiles are approved")
        if not callable(credential_loader):
            raise ModelError("a runtime credential loader is required")
        if type(call_limit) is not int or call_limit < 1:
            raise ModelError("call limit outside approved bounds")
        self.profile, self.call_limit, self.calls = profile, call_limit, 0
        self._key = credential_loader()
        if not isinstance(self._key, str) or not self._key:
            raise ModelError("Meta credential unavailable to the application runtime")
        self._opener = opener or urllib.request.build_opener(_NoRedirect())

    def __call__(self, payload, timeout):
        if self.profile is SAM:
            if not isinstance(payload, dict) or payload.get("model") != SAM.model:
                raise ModelError("request outside the approved segmentation contract")
            data, content_type = json.dumps(payload, allow_nan=False).encode(), "application/json"
        else:
            if not isinstance(payload, dict) or set(payload) != {"request", "audio"}:
                raise ModelError("request outside the approved transcription contract")
            wav_duration_ms(payload["audio"])
            data, content_type = multipart(payload)
        if self.calls >= self.call_limit:
            raise ModelError("live tool call limit for this run reached")
        self.calls += 1
        request = urllib.request.Request(self.profile.endpoint, data=data, method="POST", headers={
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
    """Budgeted, cached calls to the fixed segmentation and transcription tools."""

    def __init__(self, segment_transport, transcribe_transport, ledger, cache):
        self.segment_transport, self.transcribe_transport = segment_transport, transcribe_transport
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

    def segment(self, image, concept, *, candidate="", timeout=120):
        concept = concept_text(concept)
        key, identity = self.cache.key("segment", SAM.model, media_hash(image), {"concept": concept})
        cached = self.cache.get(key, identity)
        if cached is not None:
            return cached
        raw, reservation = self._call(self.segment_transport, sam_body(image, concept), SAM.image_price,
                                      "segment", candidate, timeout)
        # One image request is billed whether or not the reply parses.
        self.ledger.settle(reservation, cost_usd=SAM.image_price, input_tokens=0, output_tokens=0)
        result = {"concept": concept, "boxes": parse_boxes(sam_text(raw))}
        self.cache.put(key, identity, result)
        return result

    def transcribe(self, audio, *, candidate="", timeout=120):
        key, identity = self.cache.key("transcribe", TRANSCRIBE.model, media_hash(audio), {})
        cached = self.cache.get(key, identity)
        if cached is not None:
            return cached
        # Reserve the price of the header duration plus one second of rounding room.
        hours = Decimal(wav_duration_ms(audio) + 1000) / 3_600_000
        raw, reservation = self._call(self.transcribe_transport, transcribe_parts(audio),
                                      (hours * TRANSCRIBE.audio_hour_price).quantize(Decimal("1E-9")),
                                      "transcribe", candidate, timeout)
        try:
            result = parse_transcript(raw)
        except BaseException as exc:
            self.ledger.unknown(reservation, failure_reason(exc))
            raise
        cost = (Decimal(result["duration_ms"]) / 3_600_000 * TRANSCRIBE.audio_hour_price).quantize(Decimal("1E-9"))
        self.ledger.settle(reservation, cost_usd=cost, input_tokens=0, output_tokens=0)
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
