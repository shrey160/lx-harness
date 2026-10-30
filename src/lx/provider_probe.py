"""Fixed three-call compatibility probe for the NVIDIA-hosted Muse Glimmer endpoint.

Approved by the user on 30 Oct 2026 ("Yes, run the probe"): one text call, one
tool call and one synthetic demo image, stopping at the first failure, with no
retries. It records the shape of each reply so the client can be fitted to the
provider. The key is read at runtime and is never recorded or printed.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import tempfile

from .budget import BudgetLedger
from .domain import build_demo_dataset
from .live_transport import LiveMetaTransport
from .proposer import TOOLS
from .providers import ProviderProfile, chat_endpoint
from .spark import ModelError, SparkClient

BASE_URL = "https://integrate.api.nvidia.com/v1"
MODEL = "meta/muse-glimmer-30b"
KEY_ENV = "NVIDIA_API"
CONTEXT_LIMIT = 131_072
MAX_TOKENS = 8192
CALLS = 3
TIMEOUT = 180
PROFILE = ProviderProfile(kind="chat", endpoint=chat_endpoint(BASE_URL), model=MODEL, token_param="max_tokens",
                          context_limit=CONTEXT_LIMIT, accepts_images=True, supports_tools=True)


def shape(raw):
    """The reply's structure and sizes; answers are short and about synthetic input."""
    choices = raw.get("choices") if isinstance(raw, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    text = lambda value: len(value) if isinstance(value, str) else None
    return {"top_keys": sorted(raw) if isinstance(raw, dict) else None, "finish_reason": choice.get("finish_reason"),
            "message_keys": sorted(message), "content_chars": text(message.get("content")),
            "reasoning_chars": {key: text(message[key]) for key in message if "reason" in key.lower()},
            "tool_calls": [call.get("function", {}).get("name") for call in message.get("tool_calls") or []],
            "usage": raw.get("usage") if isinstance(raw, dict) else None}


def requests(image_path):
    encoded = base64.b64encode(Path(image_path).read_bytes()).decode()
    return (
        ("text", [{"role": "developer", "content": "Answer briefly."},
                  {"role": "user", "content": "Which number is larger, 9.11 or 9.8?"}], None),
        ("tool", [{"role": "developer", "content": "Use the tools provided."},
                  {"role": "user", "content": "List the available files."}], TOOLS),
        ("image", [{"role": "developer", "content": "Answer briefly."},
                   {"role": "user", "content": [{"type": "text", "text": "What colored shapes are in this image?"},
                                                {"type": "image_url", "image_url": {"url": "data:image/png;base64,"+encoded}}]}], None),
    )


def run(transport, ledger, image_path):
    """Run the calls in order and stop at the first failure."""
    replies = []
    def recording(body, timeout):
        raw = transport(body, timeout)
        replies.append(raw)
        return raw
    client = SparkClient(recording, ledger, max_tokens=MAX_TOKENS, retries=0, profile=PROFILE)
    results = []
    for name, messages, tools in requests(image_path):
        before = len(replies)
        result = {"call": name}
        try:
            message, _ = client.chat(messages, tools=tools, role="worker", phase="probe", candidate=name, timeout=TIMEOUT)
            result.update(ok=True, answer=(message["content"] or "")[:300])
        except ModelError as exc:
            result.update(ok=False, error=str(exc))
        if len(replies) > before:
            result["reply"] = shape(replies[-1])
        results.append(result)
        if not result["ok"]:
            break
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from .inventory_stream_local import key_loader
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        demo = build_demo_dataset(root/"demo")
        image = sorted((demo.root/"worker_inputs").rglob("*.png"))[0]
        transport = LiveMetaTransport(key_loader(KEY_ENV), call_limit=CALLS, profile=PROFILE)
        results = run(transport, BudgetLedger(root/"ledger.sqlite3", 1, 0), image)
    report = {"endpoint": PROFILE.endpoint, "model": MODEL, "max_tokens": MAX_TOKENS, "calls_made": transport.calls,
              "passed": len(results) == CALLS and all(r["ok"] for r in results), "results": results}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/"probe.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
