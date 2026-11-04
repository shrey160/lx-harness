"""Check an isolated fresh installation through its HTTP UI API and generated stdio setup.

Run with that installation's Python. Requires an already-created synthetic text
workspace, a running UI with the same scratch root, and a working real sandbox.
This refuses live-enabled state and never makes provider calls.
"""
import argparse
import asyncio
from decimal import Decimal
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import sys
import time
import tomllib
from urllib.request import urlopen
import zipfile

from mcp import Client, StdioServerParameters


def get(base, path):
    with urlopen(base + path, timeout=30) as response:
        return response.read()


def read(base, path):
    return json.loads(get(base, path))


def decoded(result):
    if result.is_error:
        raise RuntimeError(result.content[0].text)
    return result.structured_content if result.structured_content is not None else json.loads(result.content[0].text)


async def check(args):
    system = read(args.url, "/api/system")
    state = read(args.url, "/api/state")
    workspace = args.workspace.resolve()
    project = next(p for p in state["projects"] if Path(p["path"]).resolve() == workspace)
    assert project["draft"]["task"]["template"] == "text-request"
    assert system["meta_key"] is None, "Fresh test state must not have a provider key"
    assert system["agent_workspace_deletion"] is False
    setup = tomllib.loads(system["agent_setup"]["codex_config"])["mcp_servers"]["lx"]
    assert Path(setup["command"]).resolve() == Path(sys.executable).resolve()
    assert setup["args"] == ["-m", "lx.mcp_server", "--root", system["state_root"]]
    forwarded = {name:os.environ[name] for name in setup.get("env_vars", []) if name in os.environ}
    server = StdioServerParameters(command=setup["command"], args=setup["args"],
                                   env={**os.environ, **forwarded, "PYTHONUTF8":"1"})
    print("Fresh MCP client: connecting to generated installation setup", flush=True)
    async with Client(server) as client:
        async def call(tool_name, **arguments):
            return decoded(await client.call_tool(tool_name, arguments))
        status = await call("status")
        assert status["live_available"] is False
        assert status["meta_key"] is False
        tools = (await client.list_tools()).tools
        await call("guide", topic="tasks")
        await call("guide", topic="environment")
        env = await call("environment")
        assert next(t for t in env["model_tools"] if t["id"] == "sam-catalog")["enabled"] is False
        shown = await call("spec_show", workspace=str(workspace))
        runs = (await call("run_status", workspace=str(workspace)))["runs"]
        if args.restart_check:
            previous = json.loads(args.output.read_text(encoding="utf-8"))
            run_id = previous["run"]["id"]
            run = next(r for r in runs if r["id"] == run_id)
            assert run["status"] == "completed"
            assert run["simulated_cost"] is True
            previous["restart"] = {"tool_setting_persisted":True, "workspace_persisted":True,
                                   "completed_run_persisted":True, "fresh_mcp_reconnected":True}
            return previous
        assert not runs, "Use a fresh workspace; do not duplicate an existing preview"
        await call("spec_draft", workspace=str(workspace), mode="controlled_preview")
        tested = await call("check_test", workspace=str(workspace))
        print(json.dumps({"check_self_test":tested}), flush=True)
        assert tested["weaknesses"] == [] and tested["blank_score"] < 0.5
        started = await call("preview", workspace=str(workspace))
        run_id = started["run_id"]
        print("Synthetic preview started: " + run_id, flush=True)
        deadline, last_phase = time.monotonic()+900, None
        while time.monotonic() < deadline:
            run = next(r for r in (await call("run_status", workspace=str(workspace)))["runs"] if r["id"] == run_id)
            phase = (run["status"], run["phase"], run["progress"])
            if phase != last_phase:
                print(json.dumps({"run":run_id, "status":run["status"], "phase":run["phase"], "progress":run["progress"]}), flush=True)
                last_phase = phase
            if run["status"] not in {"queued", "running"}:
                break
            await asyncio.sleep(1)
        assert run["status"] == "completed", run.get("error")
        assert run["simulated_cost"] is True and run["mode"] == "controlled_preview"
        result = await call("results", workspace=str(workspace), run_id=run_id)
        edits = await call("edits", workspace=str(workspace), run_id=run_id)
    state = read(args.url, "/api/state")
    project = next(p for p in state["projects"] if p["id"] == project["id"])
    assert Decimal(str(project["budget"]["spent_usd"])) == 0
    assert Decimal(str(project["budget"]["reserved_usd"])) == 0
    summaries = {}
    for kind, endpoint in (("package", "packages"), ("trajectories", "trajectories")):
        data = get(args.url, f"/api/{endpoint}/{project['id']}/{run_id}/download")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert archive.testzip() is None
            summaries[kind] = {"files":len(archive.namelist()), "bytes":len(data)}
    print("Preview completed; results, edits and both downloads verified", flush=True)
    return {"platform":platform.platform(), "python":platform.python_version(),
            "packages":{name:importlib.metadata.version(name) for name in ("lx-harness", "h2portable", "harness2-cli", "mcp", "pillow")},
            "fresh_ui_state_shared":True, "generated_interpreter_and_root_match":True,
            "mcp_transport":"fresh SDK stdio client", "registered_tools":len(tools),
            "scratch_root_live_disabled":True, "provider_key_present":False,
            "sam_disabled_seen_from_ui":True, "sandbox":env["sandbox"], "check_self_test":tested,
            "run":{key:run[key] for key in ("id", "status", "mode", "progress", "simulated_cost", "cost_usd")},
            "means":result.get("means"), "result_fields":sorted(result),
            "learned_text_present":bool(result.get("learned")), "edits_available":bool(edits),
            "downloads":summaries, "real_spend_usd":"0", "real_reserved_usd":"0", "provider_calls":0}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--restart-check", action="store_true")
    args = parser.parse_args()
    assert args.url in {"http://127.0.0.1:8876", "http://127.0.0.1:8877"}
    result = asyncio.run(check(args))
    args.output.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
