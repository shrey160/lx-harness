"""One spec-driven run: the Harness² stream over a task's samples, graded by its check file.

The improver stays grade-blind; the check scores runs on the controller side
after the loop, and the harness with the largest gain becomes the package.
"""
from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from .checks import named_checks
from .inventory_workflow import InventoryAdapter, grade_stream, preview_transcription, run_stream
from .tasks import EXTERNAL, SEGMENTATION, TaskDomain, build_dataset, is_text, preview_scenes, spec_tools, tool_catalog
from .text_task import TextAdapter, TextDomain


MAX_DIFF_LINES, MAX_EDIT_LINES = 80, 400
STAGE_NAMES = {"inv": "How the agent works", "dom": "The subject", "c": "Combined"}
STAGE_ORDER = ("inv", "dom", "c")
LEARNED_FOLDERS = ("skills", "scripts", "plugin", "sub_agents")


def _adapter(task, run_dir, output, client, truth, check, *, live, limits, tools, perception=None, health=None):
    mode = "live" if live else "offline_preview"
    if is_text(task):
        return TextAdapter(run_dir/"dataset"/"worker_inputs", output, client, truth, domain=TextDomain(task, check),
                           mode=mode, limits=limits, health=health)
    return InventoryAdapter(run_dir/"dataset"/"worker_inputs", output, client, truth, perception=perception,
                            catalog=tool_catalog(tools), mode=mode, limits=limits, health=health,
                            domain=TaskDomain(task, check, segmentation=SEGMENTATION in tools))


def limitations(spec, report, chosen, live, approved_by="person"):
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
    if approved_by == "grant":
        lines.append("- Started by a connected agent under a person's grant. No person reviewed this spec or check.")
    if approved_by == "agent":
        lines.append("- Approved by a connected agent, which accepted the data notice for its owner. No person reviewed this spec or check.")
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
    changes = file_changes(base, final, MAX_DIFF_LINES)
    lines += [f"- Removed `{item['file']}`" for item in changes if item["change"] == "removed"]
    for item in changes:
        if item["change"] != "removed":
            lines += ["", f"### {item['change'].capitalize()} `{item['file']}`", "", "```diff", *item["diff"], "```"]
    return "\n".join(lines)+"\n"


def file_changes(base, final, limit):
    """[{file, change, diff}] between two harnesses: added, then changed, then removed."""
    def diff(name):
        lines = list(difflib.unified_diff(_text(base.get(name, "")).splitlines(), _text(final[name]).splitlines(), lineterm="", n=1))[2:]
        return lines[:limit]+([f"... {len(lines)-limit} more lines"] if len(lines) > limit else [])
    added, removed = sorted(set(final)-set(base)), sorted(set(base)-set(final))
    changed = sorted(name for name in set(base) & set(final) if base[name] != final[name])
    return ([{"file": name, "change": "added", "diff": diff(name)} for name in added]
            + [{"file": name, "change": "changed", "diff": diff(name)} for name in changed]
            + [{"file": name, "change": "removed", "diff": []} for name in removed])


def _harness_files(folder):
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file() and not path.is_symlink()}


def clean_base(adapter):
    """The fingerprint of the harness a run starts from, refused if it holds anything learned. Each run rebuilds
    its base from code in its own folder, so nothing carries over from an earlier run; this keeps that true."""
    folder = Path(adapter.base_harness())
    files = _harness_files(folder)
    learned = [name for name in files if name.split("/")[0] in LEARNED_FOLDERS]
    if learned:
        raise ValueError("The base harness must start with nothing learned; found "+", ".join(learned[:3]))
    return hashlib.sha256(json.dumps({name: _text(body) for name, body in files.items()}, sort_keys=True).encode()).hexdigest()


def _stage_key(label):
    name = label.rstrip("0123456789")
    return STAGE_ORDER.index(name) if name in STAGE_ORDER else len(STAGE_ORDER)


def _stage_status(meta):
    if meta is None:
        return "written"
    return "failed" if meta["fell_back"] else "no change kept" if meta["kept_base"] else "written"


def harness_edits(run_dir):
    """What the improver has written so far, sample by sample, from the files the loop leaves as it goes,
    so it works during a run. Each stage is compared with the base harness, because the invariant and
    domain stages each start from it and the combined stage merges them."""
    output = Path(run_dir)/"outputs"/"runs"
    sources = Path(run_dir)/"dataset"/"controller_truth"/"sources.json"
    folders = {key: value.get("folder") for key, value in json.loads(sources.read_text(encoding="utf-8")).items()} if sources.is_file() else {}
    samples = []
    for sample in sorted(output.glob("*-seq1/*")):
        harnesses = sample/"harnesses"
        if not (harnesses/"base").is_dir():
            continue
        summary_file = sample.parent.with_name(sample.parent.name.replace("-seq1", "-agg1"))/sample.name/"summary.json"
        summary = json.loads(summary_file.read_text(encoding="utf-8")) if summary_file.is_file() else {}
        metas = {meta["label"]: meta for meta in summary.get("modes", {}).get("seq", {}).get("stages", [])}
        base, stages = _harness_files(harnesses/"base"), []
        for label in sorted((path.name for path in harnesses.iterdir() if path.is_dir() and path.name != "base"), key=_stage_key):
            meta = metas.get(label)
            stages.append({"label": label, "stage": STAGE_NAMES.get(label.rstrip("0123456789"), label), "status": _stage_status(meta),
                           "notes": (meta or {}).get("notes"), "changes": file_changes(base, _harness_files(harnesses/label), MAX_EDIT_LINES)})
        samples.append({"sample": sample.name, "folder": folders.get(sample.name), "done": bool(summary),
                        "kept_base": summary.get("selections", {}).get("seq", {}).get("chosen_tree_is_base"), "stages": stages})
    return {"samples": samples}


def kept_sample(best, gains, base, final):
    """The sample whose harness counts as an improvement. A rerun of the same harness can score
    higher by chance, so a gain with no edits is not one."""
    return best if gains[best] > 0 and final != base else None


def _package(adapter, report, output, spec, *, live, check, approved_by="person"):
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
    base = tree.read_tree(surface, adapter.base_harness())
    chosen = kept_sample(best, gains, base, final)
    (package/"learned.md").write_text(learned(base, final, report, gains, chosen), encoding="utf-8")
    (package/"spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    (package/"check.py").write_text(check, encoding="utf-8", newline="")
    (package/"limitations.md").write_text(limitations(spec, report, chosen, live, approved_by), encoding="utf-8")
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
    base = clean_base(adapter)
    report = run_stream(adapter, output, clients["improver"], tasks=stream, limits=limits, health=health,
                        on_grading=lambda: workspace.update_run(identifier, phase="grading"))
    gains, chosen = _package(adapter, report, output, spec, live=live, check=check, approved_by=job.get("approved_by", "person"))
    return {**summarize_report(task, report, gains, chosen, live), "base_sha256": base}


def run_trial(engine, workspace, job, snapshot, client, *, check, health=None):
    """One stream sample on the base harness, graded by the check (D-025). No improver and no package.

    The result holds no trace or answer, because it reaches an outside agent, and
    those would invite procedure into the task text, which the loop learns (D-021).
    """
    spec, identifier = job["spec"], job["id"]
    task = spec["task"]
    run_dir, output = workspace.run_dir(identifier), workspace.run_dir(identifier)/"outputs"
    stream, truth, _ = build_dataset(snapshot, run_dir/"dataset", task, preview=False)
    limits, tools = engine.limits_of(spec), spec_tools(spec)
    perception = None if is_text(task) else engine.build_perception(workspace, identifier, job["cap_usd"], run_dir/"perception-cache", limits, tools)
    adapter = _adapter(task, run_dir, output, client, truth, check, live=True, limits=limits, tools=tools, perception=perception, health=health)
    sample, base = stream[0], clean_base(adapter)
    run = adapter.execute(sample, adapter.base_harness(), "trial")
    workspace.update_run(identifier, phase="grading")
    named = named_checks(check, truth[sample], json.loads(adapter.deliverable_text(run)))
    steps = json.loads((run/"trace.json").read_text(encoding="utf-8"))["trace"]
    calls = {}
    for step in steps:
        if step["type"] == "tool":
            calls[step["name"]] = calls.get(step["name"], 0)+1
    sources = run_dir/"dataset"/"controller_truth"/"sources.json"
    folder = json.loads(sources.read_text(encoding="utf-8")).get(sample, {}).get("folder", sample) if sources.is_file() else sample
    return {"kind": "trial", "task": task["name"], "live": True, "sample": folder, "score": sum(item["passed"] for item in named)/len(named),
            "base_sha256": base, "checks": named, "stopped": next((step["reason"] for step in steps if step["type"] == "stopped"), None), "tool_calls": calls,
            "note": "One sample on the base harness: a smoke test, not a measure of what the loop can reach."}


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
    gains, chosen = _package(adapter, report, output, spec, live=live, check=check, approved_by=job.get("approved_by", "person"))
    return summarize_report(task, report, gains, chosen, live)
