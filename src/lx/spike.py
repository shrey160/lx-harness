"""S1 controlled-response integration with real run_domain; no network calls.

The existing fixture is loaded from the explicitly supplied core source folder.
This experiment does not modify the core, its mirror, or the vendored engine.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from .budget import BudgetLedger
from .proposer import SparkProposer
from .spark import SparkClient


def wire_reply(content=None, calls=None):
    return {"choices": [{"message": {"role": "assistant", "content": content,
                                    "tool_calls": calls or []},
                         "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 32}}


class FixtureTransport:
    """Deterministic provider double for plumbing checks, not model evidence."""
    def __init__(self):
        self.calls = 0

    def __call__(self, request, timeout):
        self.calls += 1
        history = request["messages"]
        filenames = history[1]["content"].split("Available files:\n", 1)[1].splitlines()
        tool_messages = [m for m in history if m["role"] == "tool"]
        if not tool_messages:
            return wire_reply(calls=[{"id": f"read-{i}", "type": "function", "function": {
                "name": "read_file", "arguments": json.dumps({"path": path})}}
                for i, path in enumerate(filenames)])
        reads = {}
        for message in tool_messages:
            if message["tool_call_id"].startswith("read-"):
                index = int(message["tool_call_id"].split("-", 1)[1])
                reads[filenames[index]] = json.loads(message["content"]).get("result", "")
        if "adoption.json" in reads:
            return wire_reply("<experience>\ncue: signed amounts\nlesson: Sum signed Decimal values.\n"
                              "evidence: Probe retains negative amounts.\nconfidence: high\n</experience>")
        candidates = [name for name in filenames if name.startswith("candidates/")]
        if candidates:
            source = json.loads(reads["source/records.json"])
            target = format(sum((Decimal(r["amount"]) for r in source["records"]), Decimal(0)), ".2f")
            selected = None
            for name in candidates:
                try:
                    result = json.loads(reads[name].split("-->\n", 1)[1])
                except (ValueError, IndexError):
                    continue
                if result.get("total") == target:
                    selected = Path(name).stem
                    break
            if selected is None:
                raise AssertionError("no correct controlled candidate")
            return wire_reply(f"<why>Signed total matches source.</why>\n```choice\n{selected}\n```")
        if not any(m["tool_call_id"] == "write-1" for m in tool_messages):
            script = reads["harness/scripts/normalize.py"].replace(" if amount > 0", "")
            return wire_reply(calls=[{"id": "write-1", "type": "function", "function": {
                "name": "write_file", "arguments": json.dumps({"path": "harness/scripts/normalize.py", "content": script})}}])
        return wire_reply("<notes>Preserve all signed amounts in the total.</notes>")


def fixture_module(core_source):
    file = Path(core_source).resolve()/"fixtures"/"custom_workflow.py"
    spec = importlib.util.spec_from_file_location("lx_core_fixture", file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_spike(core_source, output, client, *, backend="local", image="h2portable:offline"):
    from h2portable.execution import DockerRunner, LocalRunner, resolve_image
    from harness2.core.schedule import Namespace, run_domain
    fixture = fixture_module(core_source)
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    runner = LocalRunner() if backend == "local" else DockerRunner(image=resolve_image(image))
    class CheckedAdapter(fixture.CustomJSONAdapter):
        def _input_intact(self, run_dir, task_id):
            inputs = run_dir/"workspace"/"inputs"
            copied = inputs/"records.json"
            return (not inputs.is_symlink() and not copied.is_symlink() and copied.is_file()
                    and copied.read_bytes() == (self.task_files(task_id)/"records.json").read_bytes())

        def execute(self, task_id, harness_dir, run_id, *, timeout=None):
            run_dir = super().execute(task_id, harness_dir, run_id, timeout=timeout)
            if not self._input_intact(run_dir, task_id):
                raise ValueError("candidate changed its synthetic input copy")
            return run_dir

        def artifacts_intact(self, run_dir):
            if not super().artifacts_intact(run_dir):
                return False
            try:
                task = json.loads((run_dir/"execution.json").read_text())["task"]
                return self._input_intact(run_dir, task)
            except (OSError, ValueError, KeyError):
                return False

    adapter = CheckedAdapter(root, runner, backend)
    proposer = SparkProposer(client, root/"proposer_audit")
    reflector = SparkProposer(client, root/"reflector_audit")
    adapter.spec = replace(adapter.spec, channels={"proposer": proposer.spec, "selector": proposer.spec})
    result = run_domain("data-cleaning", ["records-1"], 1, adapter=adapter,
                        prop=proposer, runs_root=adapter.spec.runs_root,
                        library_root=root/"library"/"k1", modes=("seq",), reflector_prop=reflector)
    if result["done"] != 1 or result["failed"] != 0:
        raise AssertionError(f"spike workflow failed: {result}")
    namespace = Namespace(adapter.spec.name, adapter.substrate.name, "agg", 1)
    summary = json.loads((namespace.task_dir(adapter.spec.runs_root, "records-1")/"summary.json").read_text())
    selected = summary.get("selections", {}).get("seq", {}).get("chosen_run_id")
    if not selected:
        raise AssertionError("selection missing")
    run_dir = adapter.spec.runs_root/"executions"/selected
    record = json.loads((run_dir/"execution.json").read_text())
    expected_backend = "local-posix" if backend == "local" else "docker-cli"
    if record["execution"]["backend"] != expected_backend or not adapter.artifacts_intact(run_dir):
        raise AssertionError("selected result has the wrong backend or damaged artifacts")
    answer = json.loads((run_dir/"answer.json").read_text())
    fixture._verify_answer("records-1", answer)
    harvested = list((root/"proposer_audit").glob("*.workspace/scripts/normalize.py"))
    if not harvested or not any("if amount > 0" not in p.read_text() for p in harvested):
        raise AssertionError("no edited candidate harvested")
    report = {"done": result["done"], "failed": result["failed"], "selected_run_id": selected,
              "independent_check": True, "candidate_edited_and_harvested": True,
              "execution_count": adapter.execution_count,
              "fresh_sessions": sum(not c.cached for c in proposer._calls+reflector._calls),
              "cached_sessions": sum(c.cached for c in proposer._calls+reflector._calls),
              "ledger": client.ledger.summary()}
    (root/"spike-report.json").write_text(json.dumps(report, indent=2)+"\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ledger = BudgetLedger(args.output/"budget.sqlite3")
    transport = FixtureTransport()
    report = run_spike(args.core_source, args.output, SparkClient(transport, ledger))
    report["mode"] = "controlled_responses_no_network"
    report["controlled_transport_calls"] = transport.calls
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
