"""Provider profiles: where a registered model is called and what it costs.

A profile holds no credential. Built-in Meta profiles use the documented
endpoints and list prices (https://dev.meta.ai/docs/pricing-rate-limits,
checked 2026-10-28/29). Any other OpenAI-compatible server, local or hosted,
is described by the same fields; nothing is special-cased per vendor.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlsplit

KINDS = ("chat", "segmentation", "transcription")
TOKEN_PARAMS = ("max_completion_tokens", "max_tokens")
META_API = "https://api.meta.ai/v1"


@dataclass(frozen=True)
class ProviderProfile:
    kind: str
    endpoint: str
    model: str
    input_price: Decimal = Decimal(0)
    cached_price: Decimal = Decimal(0)
    output_price: Decimal = Decimal(0)
    image_price: Decimal = Decimal(0)
    audio_hour_price: Decimal = Decimal(0)
    context_limit: int = 1_048_576
    token_param: str = "max_completion_tokens"
    reasoning_effort: str | None = None
    accepts_images: bool = False
    supports_tools: bool = False
    # OpenAI-compatible llama.cpp-style servers: sent as chat_template_kwargs.enable_thinking when set.
    enable_thinking: bool | None = None

    def __post_init__(self):
        if self.kind not in KINDS or self.token_param not in TOKEN_PARAMS:
            raise ValueError("invalid provider kind or token parameter")
        validate_endpoint(self.endpoint)
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("invalid provider model")
        prices = (self.input_price, self.cached_price, self.output_price, self.image_price, self.audio_hour_price)
        if any(not isinstance(p, Decimal) or not p.is_finite() or p < 0 for p in prices):
            raise ValueError("invalid provider price")
        if type(self.context_limit) is not int or self.context_limit < 1:
            raise ValueError("invalid context limit")
        if self.enable_thinking is not None and type(self.enable_thinking) is not bool:
            raise ValueError("invalid thinking setting")


def _private_host(host):
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def unmetered_local(profile):
    """Only a free private/local chat endpoint may omit LX's output-token cap."""
    host = urlsplit(profile.endpoint).hostname or ""
    return (profile.kind == "chat" and _private_host(host)
            and profile.input_price == profile.cached_price == profile.output_price == 0)


def validate_endpoint(url):
    """HTTPS anywhere; plain HTTP only to loopback or private-network hosts."""
    if not isinstance(url, str) or len(url) > 500:
        raise ValueError("invalid provider endpoint")
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or not parts.hostname:
        raise ValueError("provider endpoint must be an http(s) URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("provider endpoint must not carry credentials, query or fragment")
    if parts.scheme == "http" and not _private_host(parts.hostname):
        raise ValueError("plain HTTP is allowed only for local or private-network providers")
    return url


def chat_endpoint(base_url):
    """OpenAI-compatible chat URL from a base URL such as http://host:8888/v1."""
    validate_endpoint(base_url)
    return base_url.rstrip("/") + "/chat/completions"


SPARK = ProviderProfile(
    kind="chat", endpoint=META_API + "/chat/completions", model="muse-spark-1.3-contributor",
    input_price=Decimal("0.10") / 1_000_000, cached_price=Decimal("0.002") / 1_000_000,
    output_price=Decimal("0.20") / 1_000_000, reasoning_effort="low",
    accepts_images=True, supports_tools=True)

SAM = ProviderProfile(
    kind="segmentation", endpoint=META_API + "/responses", model="sam-3.1",
    image_price=Decimal("2.50") / 1000, accepts_images=True)

TRANSCRIBE = ProviderProfile(
    kind="transcription", endpoint=META_API + "/asr/transcribe", model="muse-voice-transcribe-1.0",
    audio_hour_price=Decimal("0.18"))
