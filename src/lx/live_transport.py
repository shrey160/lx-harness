"""Constrained Meta transport for the O-11 application live flow.

Approval: application-live-review.md, approved by the user on execution of
the PROD-001 continuation plan. This transport is deliberately not generic:
fixed endpoint, one model, approved payload shapes only. Credentials are
supplied by a caller-provided loader at construction time in the WSL
controller only; no value is logged, returned, or passed to workers.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

from .proposer import TOOLS
from .spark import MODEL, ModelError, RateLimited

ENDPOINT = "https://api.meta.ai/v1/chat/completions"
MAX_BODY_BYTES = 2_000_000
MAX_IMAGE_BYTES = 262_144
MAX_RESPONSE_BYTES = 4_000_000
DISCUSSION_CALL_LIMIT = 20
RUN_CALL_LIMIT = 200

_ROLES = {"developer", "system", "user", "assistant", "tool"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _check_content(content, images):
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
            if not isinstance(url, str) or not url.startswith("data:image/png;base64,"):
                raise ModelError("only inline PNG data may be sent as an image")
            images.append(url)
            try:
                raw = base64.b64decode(url.split(",", 1)[1], validate=True)
            except ValueError:
                raise ModelError("inline image is not valid base64") from None
            if len(raw) > MAX_IMAGE_BYTES or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ModelError("inline image outside the approved PNG bounds")
        else:
            raise ModelError("message content type outside the approved payload shapes")


def validate_body(body):
    """Reject anything outside the approved discussion/worker/improver shapes."""
    if not isinstance(body, dict):
        raise ModelError("request outside the approved contract")
    if body.get("model") != MODEL:
        raise ModelError("request outside the approved model contract")
    tools = body.get("tools")
    if tools is not None and tools != TOOLS:
        raise ModelError("request outside the approved tool contract")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ModelError("request messages malformed")
    images = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in _ROLES:
            raise ModelError("request message outside the approved shapes")
        _check_content(message.get("content"), images)
        calls = message.get("tool_calls")
        if calls is not None and not isinstance(calls, list):
            raise ModelError("request tool history malformed")
    if len(images) > 1:
        raise ModelError("only one inline image may be sent per request")
    try:
        size = len(json.dumps(body, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise ModelError("request body is not strict JSON") from None
    if size > MAX_BODY_BYTES:
        raise ModelError("request body exceeds the approved size bound")
    return body


class LiveMetaTransport:
    """One bounded live session source; the ledger scope enforces spend."""

    def __init__(self, credential_loader, *, call_limit=RUN_CALL_LIMIT, opener=None):
        if not callable(credential_loader):
            raise ModelError("a runtime credential loader is required")
        if type(call_limit) is not int or not 1 <= call_limit <= RUN_CALL_LIMIT:
            raise ModelError("call limit outside approved bounds")
        self._key = credential_loader()
        if not isinstance(self._key, str) or not self._key:
            raise ModelError("Spark credential unavailable to the application runtime")
        self.call_limit = call_limit
        self.calls = 0
        self._opener = opener or urllib.request.build_opener(_NoRedirect())

    def __call__(self, body, timeout):
        validate_body(body)
        if self.calls >= self.call_limit:
            raise ModelError("live call limit for this run reached")
        self.calls += 1
        request = urllib.request.Request(
            ENDPOINT, data=json.dumps(body, allow_nan=False).encode("utf-8"), method="POST",
            headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=min(timeout, 60)) as response:
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
        except (OSError, ValueError):
            raise ModelError("provider connection/JSON failed; cost remains reserved") from None
