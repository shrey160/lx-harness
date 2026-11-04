"""Verify the packaged example and its documented fresh stdio MCP preparation.

Run with LX's installed Python and a working script sandbox. No paid execution.
The scratch root disables live calls. The summary contains no local paths/keys.
"""
import argparse
import asyncio
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

from mcp import Client, StdioServerParameters


REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "examples/inventory-cash-three"


def load_prepare():
    spec = importlib.util.spec_from_file_location("example_prepare", EXAMPLE / "prepare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def decoded(result):
    if result.is_error:
        raise RuntimeError(result.content[0].text)
    return result.structured_content if result.structured_content is not None else json.loads(result.content[0].text)


async def flow(scratch, prepare):
    workspace = scratch / "task with spaces"
    copied = prepare.export(EXAMPLE, workspace)
    state = scratch / "state"
    server = StdioServerParameters(command=sys.executable,
        args=["-m", "lx.mcp_server", "--root", str(state)],
        env={**os.environ, "PYTHONUTF8": "1", "TMP": str(scratch), "TEMP": str(scratch)})
    async with Client(server) as client:
        async def call(tool_name, **arguments):
            return decoded(await client.call_tool(tool_name, arguments))
        status = await call("status")
        assert status["live_available"] is False
        await call("guide", topic="tasks")
        await call("guide", topic="environment")
        environment = await call("environment")
        assert environment["sandbox"]["backend"] is not None, "A real script sandbox is required"
        await call("init", workspace=str(workspace), template="photo-request",
                   name="Inventory and cash example",
                   goal="Update stock and cash from object photos and spoken shop transactions.")
        listed = await call("samples", workspace=str(workspace))
        before = await call("spec_show", workspace=str(workspace))
        assert before["task"]["stream_samples"] == 6
        task = json.loads((workspace / "task.json").read_text(encoding="utf-8"))
        await call("spec_draft", workspace=str(workspace), task=task, use_eval_file=True,
                   tools=["sam-catalog", "transcribe-catalog"], mode="live")
        tested = await call("check_test", workspace=str(workspace), use_eval_file=True)
        shown = await call("spec_show", workspace=str(workspace))
        assert shown["task"] == task
        assert shown["eval_drift"] == [] and shown["hint_drift"] == []
        assert shown["approved"] is None and shown["judges"] is None
        assert set(shown["tools"]) == {"sam-catalog", "transcribe-catalog"}
        loaded = await call("read_sample", workspace=str(workspace), path="sample-03/input.json")
        assert json.loads(loaded["text"]) == json.loads((workspace / "samples/sample-03/input.json").read_text(encoding="utf-8"))
        refused = await client.call_tool("read_sample", {"workspace":str(workspace), "path":"sample-03/truth.json"})
        assert refused.is_error and "cannot be read" in refused.content[0].text
        readiness = await call("ready", workspace=str(workspace))
        assert not readiness["problems"], readiness["problems"]
        assert readiness["check"] == tested
        assert (await call("run_status", workspace=str(workspace)))["runs"] == []
        assert Decimal(str(shown["workspace_budget"]["spent_usd"])) == 0
    return {"offline": {key:value for key,value in copied.items() if key not in {"output", "kind"}},
            "mcp": {"transport":"fresh stdio SDK client", "live_disabled_by_scratch_root":True,
                    "sample_files":len(listed["inventory"]), "task_loaded":True,
                    "input_json_read_matches":True, "truth_read_refused":True,
                    "sandbox":environment["sandbox"], "self_test":tested,
                    "ready":readiness["ready"], "blocked":readiness["blocked"],
                    "data_problems":readiness["problems"], "eval_drift":[], "hint_drift":[],
                    "approved":False, "runs":0, "spent_usd":"0"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(args.scratch.resolve())
    prepare = load_prepare()
    with tempfile.TemporaryDirectory(prefix="example-mcp-", dir=args.scratch) as work:
        result = asyncio.run(flow(Path(work), prepare))
    result["manifest_sha256"] = hashlib.sha256((EXAMPLE / "manifest.json").read_bytes()).hexdigest()
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
