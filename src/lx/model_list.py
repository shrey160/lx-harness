"""What a provider says about its models, so nobody has to type it in (D-020).

Reads the OpenAI-style `GET {base}/models`. OpenRouter's list carries prices,
context size and what each model accepts; NVIDIA's and most local servers' carry
only names, and those details stay unknown. No model is called and nothing is
spent. The reply is untrusted data: bounded, and reduced to checked fields.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from .providers import validate_endpoint
from .spark import ModelError

MAX_REPLY_BYTES = 16_000_000
MAX_MODELS = 2000
TIMEOUT = 20
# Every call reserves its whole context at the input price, and agents here stay far below a million tokens.
# Without a cap a listed 1M context at $1.25 would reserve more than a $1 run budget on every call.
RESERVED_CONTEXT = 262_144
_PRICES = {"prompt": "input_per_million", "completion": "output_per_million", "input_cache_read": "cached_per_million"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _pricing(raw):
    """Per-million prices, or None unless input and output are both stated. A provider may list -1 for 'varies'."""
    if not isinstance(raw, dict):
        return None
    prices = {}
    for field, name in _PRICES.items():
        try:
            price = Decimal(str(raw.get(field)))*1_000_000
        except (InvalidOperation, ValueError):
            continue
        if price.is_finite() and 0 <= price <= 1000:
            prices[name] = format(price.normalize(), "f")
    return prices if {"input_per_million", "output_per_million"} <= set(prices) else None


def _model(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not 0 < len(raw["id"]) <= 200:
        return None
    architecture = raw.get("architecture") if isinstance(raw.get("architecture"), dict) else {}
    outputs, inputs = architecture.get("output_modalities"), architecture.get("input_modalities")
    if isinstance(outputs, list) and "text" not in outputs:
        return None  # an image or speech generator cannot fill a chat role
    parameters = raw.get("supported_parameters")
    context = raw.get("context_length")
    name = raw.get("name") if isinstance(raw.get("name"), str) and raw["name"].strip() else raw["id"]
    return {"id": raw["id"], "name": name.strip()[:100],
            "pricing": _pricing(raw.get("pricing")),
            "context_limit": min(context, RESERVED_CONTEXT) if type(context) is int and context >= 1024 else None,
            "accepts_images": "image" in inputs if isinstance(inputs, list) else None,
            "supports_tools": "tools" in parameters if isinstance(parameters, list) else None}


def parse_models(payload):
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ModelError("This provider's model list was not in the expected form")
    return [model for model in map(_model, rows[:MAX_MODELS]) if model]


def read_models(base_url, api_key=None, *, opener=None):
    """The provider's models. A key is sent only to this provider, and only when one is given."""
    url = validate_endpoint(base_url).rstrip("/")+"/models"
    headers = {"User-Agent": "lx-harness", **({"Authorization": "Bearer "+api_key} if api_key else {})}
    opener = opener or urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=TIMEOUT) as response:
            body = response.read(MAX_REPLY_BYTES+1)
    except urllib.error.HTTPError as exc:
        needs_key = " Add your API key and try again." if exc.code in {401, 403} else ""
        raise ModelError(f"The provider refused the model list (HTTP {exc.code}).{needs_key}") from None
    except OSError:
        raise ModelError("Could not reach that provider") from None
    if len(body) > MAX_REPLY_BYTES:
        raise ModelError("That provider's model list is too large")
    try:
        return parse_models(json.loads(body))
    except ValueError:
        raise ModelError("This provider's model list was not in the expected form") from None
