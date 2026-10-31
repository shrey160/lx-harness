"""One spec-driven run: the Harness² stream over a task's samples, graded by its check file.

The improver stays grade-blind; the check scores runs on the controller side
after the loop, and the harness with the largest gain becomes the package.
"""
from __future__ import annotations

import difflib
import json
from pathlib import Path
from types import SimpleNamespace

from .inventory_workflow import InventoryAdapter, grade_stream, preview_transcription, run_stream
from .tasks import EXTERNAL, SEGMENTATION, TaskDomain, build_dataset, is_text, preview_scenes, spec_tools, tool_catalog
from .text_task import TextAdapter, TextDomain


MAX_DIFF_LINES = 80


def _adapter(task, run_dir, output, client, truth, check, *, live, limits, tools, perception=None, health=None):
    mode = "live" if live else "offline_preview"
    if is_text(task):
        return TextAdapter(run_dir/"dataset"/"worker_inputs", output, client, truth, domain=TextDomain(task, check),
                           mode=mode, limits=limits, health=health)
    return InventoryAdapter(run_dir/"dataset"/"worker_inputs", output, client, truth, perception=perception,
                            catalog=tool_catalog(tools), mode=mode, limits=limits, health=health,
                            domain=TaskDomain(task, check, segmentation=SEGMENTATION in tools))


def limitations(spec, report, chosen, live):
    """What a reader of this package must not assume; written from the run itself."""
    rows, skipped = report["per_sample"], report["skipped"]
    worker = spec["providers"]["worker"].get("model_id") or spec["roles"]["worker"]
    lines = ["# Limitations", "",
             f"- Measured on {len(rows)} sample(s), one run per harness, so a single unlucky reply can move a score.",
             "- No held-out samples: every score here comes from samples the loop adapted on, so it reads high.",
             (f"- This harness came from one sample ({chosen}), picked for the largest gain after grading, "
              "which adds to that optimism." if chosen else "- No sample gained, so this is the base harness."),
             f"- Learned with {worker} as the agent. A harness can fail on another model; retest it after a swap.",
             "- Reuse on a new sample is not built yet. The harness is plain files you can read and copy.",
             ("- A connected agent set this run up. It could read every file in the workspace, including the answers, "
              "so the task text and check may reflect them." if spec["roles"]["orchestrator"] == EXTERNAL else
              "- The built-in orchestrator can read the file list, sample input text and the shape of the answers, never their values.")]
    if skipped:
        lines.append(f"- Not graded, because they produced no reference run: {', '.join(sorted(skipped))}.")
    if not live:
        lines.append("- Preview run: replies came from doubles, so this shows the plumbing works, not what a model can do.")
    return "\n".join(lines)+"\n"


def _text(content):
    return content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content)


def learned(base, final, report, gains, chosen):
    """What the loop changed, for a person or an outside agent to read. `base` and `final` map file names to contents."""
    means = report["means"]
    scores = f"Mean score, each sample with its own harness: {means['base']:.2f} before, {means['harness2']:.2f} after."
    if chosen is None:
        return f"# What was learned\n\nNothing the loop tried beat the base harness, so this package is the base harness.\n\n{scores}\n"
    lines = ["# What was learned", "", f"This package is the harness from {chosen}, the largest gain ({gains[chosen]:+.2f} on that sample).", "", scores, "",
             "## Changes from the base harness"]
    added, removed = sorted(set(final)-set(base)), sorted(set(base)-set(final))
    changed = sorted(name for name in set(base) & set(final) if base[name] != final[name])
    lines += [f"- Removed `{name}`" for name in removed]
    for name in added+changed:
        lines += ["", f"### {'Added' if name in added else 'Changed'} `{name}`", "", "```diff"]
        diff = list(difflib.unified_diff(_text(base.get(name, "")).splitlines(), _text(final[name]).splitlines(), lineterm="", n=1))[2:]
        lines += diff[:MAX_DIFF_LINES]+([f"... {len(diff)-MAX_DIFF_LINES} more lines"] if len(diff) > MAX_DIFF_LINES else [])+["```"]
    return "\n".join(lines)+"\n"


def _package(adapter, report, output, spec, *, live, check):
    """Copy the best harness (or the base when nothing gained) into package/harness, with what graded it."""
    from harness2.core import tree
    surface = adapter.substrate.surface()
    gains = {task: row["harness2"]["score"]-row["base"]["score"] for task, row in report["per_sample"].items()}
    best = max(gains, key=lambda task: (gains[task], task))
    relative = report["per_sample"][best].get("harness_dir")
    source = adapter.spec.runs_root/relative if gains[best] > 0 and relative else adapter.base_harness()
    package = Path(output)/"package"
    package.mkdir(exist_ok=True)
    final = tree.read_tree(surface, source)
    tree.write_tree(surface, final, package/"harness", stubs=False)
    chosen = best if gains[best] > 0 else None
    (package/"learned.md").write_text(learned(tree.read_tree(surface, adapter.base_harness()), final, report, gains, chosen), encoding="utf-8")
    (package/"spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    (package/"check.py").write_text(check, encoding="utf-8", newline="")
    (package/"limitations.md").write_text(limitations(spec, report, chosen, live), encoding="utf-8")
    return gains, chosen


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


def run_task(engine, workspace, job, snapshot, clients, *, live, check, health=None):
    spec, identifier = job["spec"], job["id"]
    task = spec["task"]
    run_dir = workspace.run_dir(identifier)
    stream, truth, requests = build_dataset(snapshot, run_dir/"dataset", task, preview=not live)
    limits, tools = engine.limits_of(spec), spec_tools(spec)
    if is_text(task):
        perception = None
        if not live:
            clients["worker"].transport.truth = truth
    elif live:
        perception = engine.build_perception(workspace, identifier, spec["budget_usd"], run_dir/"perception-cache", limits, tools)
    else:
        from .inventory import FakeShapeSegmentation
        from .perception import PerceptionCache, PerceptionClient
        from .usage import BudgetScope
        perception = PerceptionClient(FakeShapeSegmentation(preview_scenes(len(truth))),
                                      preview_transcription(run_dir/"dataset", requests),
                                      BudgetScope(workspace.preview_ledger, identifier, spec["budget_usd"]),
                                      PerceptionCache(run_dir/"perception-cache"))
    output = run_dir/"outputs"
    adapter = _adapter(task, run_dir, output, clients["worker"], truth, check, live=live, limits=limits, tools=tools,
                       perception=perception, health=health)
    report = run_stream(adapter, output, clients["improver"], tasks=stream, limits=limits, health=health)
    gains, chosen = _package(adapter, report, output, spec, live=live, check=check)
    return summarize_report(task, report, gains, chosen, live)


def regrade(engine, workspace, job, *, live):
    """Score a run from its saved answers with its check file; makes no model or tool calls."""
    spec, identifier = job["spec"], job["id"]
    task = spec["task"]
    run_dir = workspace.run_dir(identifier)
    output = run_dir/"outputs"
    stream, truth, _ = build_dataset(run_dir/"snapshot", run_dir/"dataset", task, preview=not live)
    client = SimpleNamespace(profile=engine.registry.profile(spec["roles"]["worker"]))
    check = workspace.check_source(spec["check"])
    adapter = _adapter(task, run_dir, output, client, truth, check, live=live, limits=engine.limits_of(spec), tools=spec_tools(spec))
    report = grade_stream(adapter, output, stream, {"regraded": True})
    gains, chosen = _package(adapter, report, output, spec, live=live, check=check)
    return summarize_report(task, report, gains, chosen, live)
