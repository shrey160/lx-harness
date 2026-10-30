"""Constrained chat transport for live application calls.

Approved payload shapes only, one profile per transport, no redirects, and a
provider's error bodies are withheld. Spark was the only provider at first
(O-11, application-live-review.md); D-015 opens this to any provider entry a
human approved in the spec. Credentials come from a caller-provided loader at
construction time in the controller only; no value is logged, returned, or
passed to workers.
"""
from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.request

from .proposer import TOOLS
from .providers import SPARK
from .spark import MODEL, ModelError, RateLimited

ENDPOINT = SPARK.endpoint
MAX_BODY_BYTES = 2_000_000
MAX_IMAGE_BYTES = 262_144
MAX_RESPONSE_BYTES = 4_000_000
DISCUSSION_CALL_LIMIT = 20
RUN_CALL_LIMIT = 200
MAX_IMAGES_PER_REQUEST = 4

_ROLES = {"developer", "system", "user", "assistant", "tool"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_IMAGE_MAGIC = {"png": b"\x89PNG\r\n\x1a\n", "jpeg": b"\xff\xd8\xff"}


def _check_content(content, images, image_types=("png",), max_image_bytes=MAX_IMAGE_BYTES):
    if content is None or isinstance(content, str):
        return
    if not isinstance(content, list) or not content:
        raise ModelError("message content outside the approved payload shapes")
    for part in content:
        if not isinstance(part, dict):
            raise ModelError("message content part malformed")
        kind = part.get("type")
        if kind == "text":
            if set(part) != {"type", "text"} or not isinstance(part["text"], str):
                raise ModelError("text content part malformed")
        elif kind == "image_url":
            if set(part) != {"type", "image_url"} or not isinstance(part.get("image_url"), dict):
                raise ModelError("image content part malformed")
            url = part["image_url"].get("url")
            kind = next((t for t in image_types if isinstance(url, str) and url.startswith(f"data:image/{t};base64,")), None)
            if kind is None:
                raise ModelError("only inline PNG data may be sent as an image" if image_types == ("png",)
                                 else "image outside the approved inline types")
            images.append(url)
            try:
                raw = base64.b64decode(url.split(",", 1)[1], validate=True)
            except ValueError:
                raise ModelError("inline image is not valid base64") from None
            if len(raw) > max_image_bytes or not raw.startswith(_IMAGE_MAGIC[kind]):
                raise ModelError("inline image outside the approved bounds")
        else:
            raise ModelError("message content type outside the approved payload shapes")


def validate_body(body, *, model=MODEL, tool_sets=(TOOLS,), image_types=("png",), max_image_bytes=MAX_IMAGE_BYTES,
                  max_images=1):
    """Reject anything outside the approved discussion/worker/improver shapes."""
    if not isinstance(body, dict):
        raise ModelError("request outside the approved contract")
    if body.get("model") != model:
        raise ModelError("request outside the approved model contract")
    tools = body.get("tools")
    if tools is not None and tools not in tool_sets:
        raise ModelError("request outside the approved tool contract")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ModelError("request messages malformed")
    images = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in _ROLES:
            raise ModelError("request message outside the approved shapes")
        _check_content(message.get("content"), images, image_types, max_image_bytes)
        calls = message.get("tool_calls")
        if calls is not None and not isinstance(calls, list):
            raise ModelError("request tool history malformed")
    if len(images) > max_images:
        raise ModelError("only one inline image may be sent per request" if max_images == 1
                         else "request carries more inline images than approved")
    try:
        size = len(json.dumps(body, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise ModelError("request body is not strict JSON") from None
    if size > MAX_BODY_BYTES:
        raise ModelError("request body exceeds the approved size bound")
    return body


class LiveMetaTransport:
    """One bounded live session source; the ledger scope enforces spend."""

    def __init__(self, credential_loader, *, call_limit=RUN_CALL_LIMIT, opener=None, profile=SPARK,
                 agent_tools=None, image_types=("png",), max_image_bytes=MAX_IMAGE_BYTES, call_ceiling=RUN_CALL_LIMIT,
                 max_images=1, max_call_seconds=None, requests_per_minute=None, sleep=time.sleep, clock=time.monotonic):
        """Defaults are the approved O-11 contract. PROD-003 (D-008) may pass a
        provider profile, the exact agent tool set, JPEG images up to a stated
        bound and a call ceiling set from measured counts; a keyless local
        provider passes no credential loader. PROD-004 may allow up to
        MAX_IMAGES_PER_REQUEST images per request (view results kept in the
        agent history); the body size bound still applies. D-017: the spec may
        set the call bound, and a provider entry's rate spaces calls evenly,
        within one run only."""
        if credential_loader is None and profile is SPARK:
            raise ModelError("a runtime credential loader is required")
        if credential_loader is not None and not callable(credential_loader):
            raise ModelError("a runtime credential loader is required")
        if profile.kind != "chat" or not set(image_types) <= {"png", "jpeg"}:
            raise ModelError("transport outside the approved chat contract")
        if type(max_images) is not int or not 1 <= max_images <= MAX_IMAGES_PER_REQUEST:
            raise ModelError("image count outside approved bounds")
        if type(call_ceiling) is not int or not 1 <= call_ceiling <= 5000:
            raise ModelError("call ceiling outside approved bounds")
        if type(call_limit) is not int or not 1 <= call_limit <= call_ceiling:
            raise ModelError("call limit outside approved bounds")
        self._key = credential_loader() if credential_loader is not None else None
        if credential_loader is not None and (not isinstance(self._key, str) or not self._key):
            raise ModelError("credential unavailable to the application runtime")
        self.profile = profile
        self._contract = {"model": profile.model, "tool_sets": (TOOLS,) + ((agent_tools,) if agent_tools else ()),
                          "image_types": tuple(image_types), "max_image_bytes": max_image_bytes,
                          "max_images": max_images}
        if requests_per_minute is not None and (type(requests_per_minute) is not int or requests_per_minute < 1):
            raise ModelError("request rate outside approved bounds")
        # Local models answer slowly; Spark keeps its original 60-second bound.
        self._max_timeout = max_call_seconds or (60 if profile is SPARK else 600)
        self._spacing = 60/requests_per_minute if requests_per_minute else 0
        self._next_call, self._pace = 0, threading.Lock()
        self._sleep, self._clock = sleep, clock
        self.call_limit = call_limit
        self.calls = 0
        self._opener = opener or urllib.request.build_opener(_NoRedirect())

    def _wait_turn(self):
        """Hold this call until its slot; a rate limit is better avoided than backed off from."""
        if not self._spacing:
            return
        with self._pace:
            now = self._clock()
            slot = max(now, self._next_call)
            self._next_call = slot+self._spacing
        if slot > now:
            self._sleep(slot-now)

    def __call__(self, body, timeout):
        validate_body(body, **self._contract)
        if self.calls >= self.call_limit:
            raise ModelError("live call limit for this run reached")
        self.calls += 1
        self._wait_turn()
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = "Bearer " + self._key
        request = urllib.request.Request(
            self.profile.endpoint, data=json.dumps(body, allow_nan=False).encode("utf-8"), method="POST",
            headers=headers)
        try:
            with self._opener.open(request, timeout=min(timeout, self._max_timeout)) as response:
                payload = response.read(MAX_RESPONSE_BYTES + 1)
                if len(payload) > MAX_RESPONSE_BYTES:
                    raise ModelError("provider response exceeded the limit")
                return json.loads(payload)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                try:
                    delay = float(exc.headers.get("Retry-After", 0))
                except (TypeError, ValueError):
                    delay = 0
                raise RateLimited(delay) from None
            raise ModelError(f"provider request failed (HTTP {exc.code}); response body withheld") from None
        except (OSError, ValueError) as exc:
            if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError):
                raise ModelError("provider did not reply in time; cost remains reserved") from None
            raise ModelError("provider connection/JSON failed; cost remains reserved") from None
