"""Offline-testable Spark protocol. A transport is injected; no network/key loading.

Contributor prices checked at https://dev.meta.ai/docs/pricing-rate-limits
on 2026-10-28. Costs are computed from token usage, not an invoice.
"""
from __future__ import annotations

import copy
import json
import random
import threading
import time
from decimal import Decimal

MODEL = "muse-spark-1.3-contributor"
INPUT_PRICE = Decimal("0.10") / 1_000_000
CACHED_PRICE = Decimal("0.002") / 1_000_000
OUTPUT_PRICE = Decimal("0.20") / 1_000_000
CONTEXT_LIMIT = 1_048_576


class ModelError(RuntimeError):
    pass


class RateLimited(ModelError):
    def __init__(self, retry_after=0):
        super().__init__("provider rate limited the request")
        self.retry_after = min(max(float(retry_after), 0), 60)


def _integer(value, name):
    if type(value) is not int or value < 0:
        raise ModelError(f"invalid or missing provider {name}")
    return value


def usage_of(raw):
    """Extract billable usage separately from content validation."""
    if not isinstance(raw, dict) or not isinstance(raw.get("usage"), dict):
        raise ModelError("provider usage missing; cost unknown")
    usage = raw["usage"]
    inputs = _integer(usage.get("prompt_tokens"), "input tokens")
    outputs = _integer(usage.get("completion_tokens"), "output tokens")
    details = usage.get("prompt_tokens_details") or {}
    if not isinstance(details, dict):
        raise ModelError("invalid cached token details")
    cached = _integer(details.get("cached_tokens", 0), "cached tokens")
    if cached > inputs:
        raise ModelError("cached token count exceeds input tokens")
    cost = (inputs-cached)*INPUT_PRICE + cached*CACHED_PRICE + outputs*OUTPUT_PRICE
    return {"input_tokens": inputs, "output_tokens": outputs, "cost_usd": cost}


def normalize(raw: dict, *, max_tokens: int) -> tuple[dict, dict]:
    """Validate wire data and normalize function arguments before tool dispatch."""
    computed = usage_of(raw)
    if computed["input_tokens"] > CONTEXT_LIMIT or computed["output_tokens"] > max_tokens:
        raise ModelError("provider usage exceeded the declared request bounds")
    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ModelError("provider choices malformed")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict) or choice.get("finish_reason") not in {"stop", "tool_calls"}:
        raise ModelError("provider returned an incomplete response")
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise ModelError("provider content malformed")
    calls = message.get("tool_calls", [])
    if calls is None:
        calls = []
    if not isinstance(calls, list):
        raise ModelError("provider tool calls malformed")
    if bool(calls) != (choice["finish_reason"] == "tool_calls"):
        raise ModelError("provider tool calls do not match finish reason")
    normalized = []
    identifiers = set()
    for call in calls:
        if not isinstance(call, dict) or call.get("type") != "function":
            raise ModelError("unsupported tool call")
        identifier = call.get("id")
        function = call.get("function")
        if not isinstance(identifier, str) or not identifier or identifier in identifiers or not isinstance(function, dict):
            raise ModelError("invalid tool call identity")
        identifiers.add(identifier)
        name, arguments = function.get("name"), function.get("arguments")
        if not isinstance(name, str) or not isinstance(arguments, str):
            raise ModelError("tool function malformed")
        try:
            arguments = json.loads(arguments)
        except ValueError:
            raise ModelError("tool arguments are not JSON") from None
        if not isinstance(arguments, dict):
            raise ModelError("tool arguments must be an object")
        normalized.append({"id": identifier, "name": name, "arguments": arguments})
    if not normalized and (not content or not content.strip()):
        raise ModelError("provider returned no usable content")
    return {"content": content, "tool_calls": normalized}, computed


class SparkClient:
    def __init__(self, transport, ledger, *, max_tokens=4096, retries=2, cancel=None):
        if type(max_tokens) is not int or not 1 <= max_tokens <= 8192:
            raise ValueError("max_tokens outside supported bounds")
        self.transport, self.ledger = transport, ledger
        self.max_tokens, self.retries = max_tokens, retries
        self.cancel = cancel or threading.Event()

    def chat(self, messages, *, tools=None, role="improver", phase="session", candidate="", timeout=120):
        body = {"model": MODEL, "messages": copy.deepcopy(messages),
                "max_completion_tokens": self.max_tokens, "reasoning_effort": "low"}
        if tools:
            body["tools"] = tools
        # Reserve the entire context ceiling instead of relying on a guessed tokenizer.
        maximum = CONTEXT_LIMIT*INPUT_PRICE + self.max_tokens*OUTPUT_PRICE
        deadline = time.monotonic()+timeout
        for attempt in range(self.retries+1):
            if self.cancel.is_set() or time.monotonic() >= deadline:
                raise ModelError("model session stopped or timed out")
            reservation = self.ledger.reserve(maximum, role, phase, candidate)
            try:
                raw = self.transport(body, min(60, max(0.1, deadline-time.monotonic())))
            except RateLimited as exc:
                self.ledger.unknown(reservation)
                if attempt >= self.retries:
                    raise ModelError("rate limit retries exhausted") from None
                delay = max(exc.retry_after, 0.5*2**attempt+random.random()*0.1)
                if delay >= deadline-time.monotonic() or self.cancel.wait(delay):
                    raise ModelError("model session stopped or timed out") from None
                continue
            except BaseException:
                self.ledger.unknown(reservation)
                raise
            try:
                usage = usage_of(raw)
            except BaseException:
                # Missing/malformed usage cannot establish what was billed.
                self.ledger.unknown(reservation)
                raise
            self.ledger.settle(reservation, **usage)
            # Account for a charged response even if its content is malformed or
            # reported usage violates the requested bounds. Ledger detects overruns.
            message, _ = normalize(raw, max_tokens=self.max_tokens)
            if self.cancel.is_set() or time.monotonic() >= deadline:
                raise ModelError("model session stopped or timed out after dispatch; usage recorded")
            return message, usage
        raise ModelError("model call did not complete")

    def session(self, messages, handler, tools, *, role="improver", phase="session", candidate="", timeout=120, max_turns=12):
        history = copy.deepcopy(messages)
        deadline = time.monotonic()+timeout
        total = Decimal(0)
        for _ in range(max_turns):
            message, usage = self.chat(history, tools=tools, role=role, phase=phase,
                                       candidate=candidate, timeout=max(0, deadline-time.monotonic()))
            total += usage["cost_usd"]
            calls = message["tool_calls"]
            if not calls:
                return message["content"], total
            history.append({"role": "assistant", "content": message["content"],
                            "tool_calls": [{"id": c["id"], "type": "function", "function": {
                                "name": c["name"], "arguments": json.dumps(c["arguments"])}} for c in calls]})
            for call in calls:
                if self.cancel.is_set():
                    raise ModelError("model session stopped")
                try:
                    value = handler(call["name"], call["arguments"])
                    content = json.dumps({"result": value}, allow_nan=False)
                except (ValueError, OSError):
                    content = json.dumps({"error": "tool arguments or file access rejected"})
                history.append({"role": "tool", "tool_call_id": call["id"], "content": content})
        raise ModelError("tool session exceeded the turn limit")
