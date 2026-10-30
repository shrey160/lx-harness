"""One spec-driven run: the Harness² stream over a task's samples, graded by its check file.

The improver stays grade-blind; the check scores runs on the controller side
after the loop, and the harness with the largest gain becomes the package.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from .inventory_probe import CATALOG
from .inventory_workflow import InventoryAdapter, grade_stream, preview_transcription, run_stream
from .tasks import TaskDomain, build_dataset, preview_scenes


def _package(adapter, report, output, spec):
    """Copy the best harness (or the base when nothing gained) into package/harness."""
    from harness2.core import tree
    surface = adapter.substrate.surface()
    gains = {task: row["harness2"]["score"]-row["base"]["score"] for task, row in report["per_sample"].items()}
    best = max(gains, key=lambda task: (gains[task], task))
    relative = report["per_sample"][best].get("harness_dir")
    source = adapter.spec.runs_root/relative if gains[best] > 0 and relative else adapter.base_harness()
    package = Path(output)/"package"
    package.mkdir(exist_ok=True)
    tree.write_tree(surface, tree.read_tree(surface, source), package/"harness", stubs=False)
    (package/"spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return gains, best if gains[best] > 0 else None


def summarize_report(task, report, gains, chosen, live):
    rows = report["per_sample"]
    return {"task": task["name"], "live": live, "simulated_usage": not live, "improved": chosen is not None,
            "retained": "harness2" if chosen else "base", "chosen_sample": chosen, "means": report["means"],
            "gains": gains, "skipped": report["skipped"],
            "checks": {"base": {t: r["base"]["score"] == 1.0 for t, r in rows.items()},
                       "harness2": {t: r["harness2"]["score"] == 1.0 for t, r in rows.items()}},
            "scores": {"base": {t: r["base"]["score"] for t, r in rows.items()},
                       "harness2": {t: r["harness2"]["score"] for t, r in rows.items()}},
            "files_added": {t: r["files_added"] for t, r in rows.items()},
            "grading": "controller-side check file after the run; never shown to the improver"}


def run_task(engine, workspace, job, snapshot, clients, *, live, check):
    spec, identifier = job["spec"], job["id"]
    task = spec["task"]
    run_dir = workspace.run_dir(identifier)
    stream, truth, requests = build_dataset(snapshot, run_dir/"dataset", task, preview=not live)
    limits = engine.limits_of(spec)
    if live:
        perception = engine.build_perception(workspace, identifier, spec["budget_usd"], run_dir/"perception-cache", limits)
    else:
        from .inventory import FakeShapeSegmentation
        from .perception import PerceptionCache, PerceptionClient
        from .usage import BudgetScope
        perception = PerceptionClient(FakeShapeSegmentation(preview_scenes(len(truth))),
                                      preview_transcription(run_dir/"dataset", requests),
                                      BudgetScope(workspace.preview_ledger, identifier, spec["budget_usd"]),
                                      PerceptionCache(run_dir/"perception-cache"))
    output = run_dir/"outputs"
    adapter = InventoryAdapter(run_dir/"dataset"/"worker_inputs", output, clients["worker"], truth, perception=perception,
                               catalog=CATALOG, mode="live" if live else "offline_preview", domain=TaskDomain(task, check),
                               limits=limits)
    report = run_stream(adapter, output, clients["improver"], tasks=stream, limits=limits)
    gains, chosen = _package(adapter, report, output, spec)
    return summarize_report(task, report, gains, chosen, live)


def regrade(engine, workspace, job, *, live):
    """Score a run from its saved answers with its check file; makes no model or tool calls."""
    spec, identifier = job["spec"], job["id"]
    task = spec["task"]
    run_dir = workspace.run_dir(identifier)
    output = run_dir/"outputs"
    stream, truth, _ = build_dataset(run_dir/"snapshot", run_dir/"dataset", task, preview=not live)
    client = SimpleNamespace(profile=engine.registry.profile(spec["roles"]["worker"]))
    adapter = InventoryAdapter(run_dir/"dataset"/"worker_inputs", output, client, truth, perception=None, catalog=CATALOG,
                               mode="live" if live else "offline_preview",
                               domain=TaskDomain(task, workspace.check_source(spec["check"])), limits=engine.limits_of(spec))
    report = grade_stream(adapter, output, stream, {"regraded": True})
    gains, chosen = _package(adapter, report, output, spec)
    return summarize_report(task, report, gains, chosen, live)
