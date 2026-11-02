"""One spec-driven run: the Harness² stream over a task's samples, graded by its check file.

The improver stays grade-blind; the check scores runs on the controller side
after the loop. The package is the harness adopted on the last sample, because
no harness is kept or chosen by score (D-028). Scores are only reported.
"""
from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
import random
from types import SimpleNamespace

from dataclasses import replace

from . import hints as hint_notes, judging
from .checks import CheckError, named_checks, shown_checks
from .inventory_workflow import InventoryAdapter, grade_stream, preview_transcription, run_stream
from .tasks import EXTERNAL, SEGMENTATION, TaskDomain, build_dataset, is_text, needs_truth, preview_scenes, spec_mcp, spec_tools, tool_catalog
from .text_task import TextAdapter, TextDomain


MAX_DIFF_LINES, MAX_EDIT_LINES = 80, 400
STAGE_NAMES = {"inv": "How the agent works", "dom": "The subject", "c": "Combined"}
STAGE_ORDER = ("inv", "dom", "c")
LEARNED_FOLDERS = ("skills", "scripts", "plugin", "sub_agents")
MCP_NOTE = " A script's input also holds mcp_results: this agent run's MCP tool results, each with tool, arguments, text and truncated."


MAX_TRY_RUNS = 20


def run_order(stream, identifier):
    """This run's order over the stream. The split stays fixed, so a held-out sample stays held out; the order
    differs per run, as in the paper's three runs, and comes from the run id, so a resume keeps it."""
    order = sorted(stream)
    random.Random(f"{identifier}:stream-order").shuffle(order)
    return order


def _folders(run_dir):
    """{sample id: its folder in the workspace}."""
    sources = Path(run_dir)/"dataset"/"controller_truth"/"sources.json"
    return {key: value.get("folder") for key, value in json.loads(sources.read_text(encoding="utf-8")).items()} if sources.is_file() else {}


def _noted(domain, mcp):
    """Tells the improver what a script's input holds; what to do with it stays with the loop (D-021)."""
    if mcp:
        stock = domain.prompts
        domain.prompts = lambda: replace(stock(), work=stock().work+MCP_NOTE)
    return domain


def _adapter(task, run_dir, output, client, truth, check, *, live, limits, tools, perception=None, health=None, mcp=None):
    mode = "live" if live else "offline_preview"
    if is_text(task):
        return TextAdapter(run_dir/"dataset"/"worker_inputs", output, client, truth, domain=_noted(TextDomain(task, check), mcp),
                           mode=mode, limits=limits, health=health, mcp=mcp)
    return InventoryAdapter(run_dir/"dataset"/"worker_inputs", output, client, truth, perception=perception,
                            catalog=tool_catalog(tools), mode=mode, limits=limits, health=health, mcp=mcp,
                            domain=_noted(TaskDomain(task, check, segmentation=SEGMENTATION in tools), mcp))


class Grader:
    """Grades one answer with the check, the judge, or both, and keeps the judge's verdicts for a person to read.
    An answer the judge could not judge gets no score, which is not a zero."""
    def __init__(self, adapter, check, judge, truth, run_dir):
        self.adapter, self.check, self.judge, self.truth = adapter, check, judge, truth
        self.requests = Path(run_dir)/"dataset"/"worker_inputs"/"requests"
        self.verdicts = {}

    def request(self, task):
        path = self.requests/f"{task}.txt"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def named(self, task, answer, where):
        """The named checks for this answer, check first, or raises judging.NotJudged."""
        named = []
        if self.check is not None:
            named += [{"name": item["name"], "passed": item["passed"]} for item in named_checks(self.check, self.truth[task], answer)]
        if self.judge is not None:
            try:
                found = self.judge.verdicts(self.truth.get(task), answer, self.request(task))
            except judging.NotJudged as error:
                self.verdicts[where] = {"not_judged": str(error)}
                raise
            self.verdicts[where] = found
            named += [{"name": item["name"], "passed": item["passed"]} for item in judging.as_checks(found)]
        return named

    def __call__(self, task, answer, where):
        try:
            named = self.named(task, answer, where)
        except judging.NotJudged as error:
            return {"score": None, "passed": None, "checks": None, "not_judged": str(error)}
        return {"score": _score(named), "passed": sum(item["passed"] for item in named), "checks": len(named)}

    def save(self, output):
        if self.judge is not None:
            (Path(output)/"judged.json").write_text(json.dumps(self.verdicts, indent=2, ensure_ascii=False), encoding="utf-8")


def _grader(adapter, check, judge, truth, run_dir):
    """None when the check alone grades, as before judges, so those runs score exactly as they did."""
    return Grader(adapter, check, judge, truth, run_dir) if judge is not None else None


def limitations(spec, report, chosen, live, approved_by="person"):
    """What a reader of this package must not assume; written from the run itself."""
    rows, skipped = report["per_sample"], report["skipped"]
    worker = spec["providers"]["worker"].get("model_id") or spec["roles"]["worker"]
    measured = [f"- Measured on {len(rows)} sample(s), one run per harness, so a single unlucky reply can move a score.",
                "- No held-out samples: every score here comes from samples the loop adapted on, so it reads high."]
    lines = ["# Limitations", "",
             *(measured if report.get("scored", True) else ["- Never scored: this run had no check, so nothing here says whether this harness is better or worse. Read its replies and edits before using it."]),
             *([f"- Partly judged by {(spec['judge']['provider'].get('model_id') or spec['judge']['entry'])}, a model. Its verdicts can vary between runs, "
                "and it may be wrong; read its reasons on the run page before trusting a judged score."] if spec.get("judge") and report.get("scored", True) else []),
             *([f"- {report['not_graded']} answer(s) were not judged, so the means leave them out."] if report.get("not_graded") else []),
             (f"- This harness was adopted on {chosen}, the last sample in this run's order. Scores played no part in that, "
              "and another run's order would give another harness." if chosen else "- The last sample in this run's order kept the base harness, so this is the base harness."),
             f"- Learned with {worker} as the agent. A harness can fail on another model; retest it after a swap.",
             "- A trial can run this harness on one sample in this workspace. It is otherwise plain files you can read and copy.",
             ("- A connected agent set this run up. It could read every file in the workspace, including the answers, "
              "so the task text and check may reflect them." if spec["roles"]["orchestrator"] == EXTERNAL else
              "- The built-in orchestrator can read the file list, sample input text and the shape of the answers, never their values.")]
    given = hint_notes.summary(spec.get("hints"))
    if given:
        lines.append(f"- The improver also read the person's hints ({given}) about how this kind of task is done. They can shape what it learned; the worker never saw them, and the check never read them."
                     + (" A connected agent may have written them, with no person reviewing them." if spec["roles"]["orchestrator"] == EXTERNAL else ""))
    if spec.get("tell_improver_worker"):
        lines.append("- The improver was told which model the worker is, at the person's choice. The Harness² method never tells it, so what it "
                     "learned may suit this model more and travel less well to another.")
    if approved_by == "grant":
        lines.append("- Started by a connected agent under a person's grant. No person reviewed this spec or check.")
    if approved_by == "agent":
        lines.append("- Approved by a connected agent, which accepted the data notice for its owner. No person reviewed this spec or check.")
    servers = sorted({entry["server"] for entry in spec_mcp(spec)})
    if servers:
        lines.append(f"- Needs the MCP server(s) {', '.join(servers)}, and may depend on what they returned. A harness learned from a server's text can carry that text.")
    if skipped:
        lines.append(f"- Not graded, because they produced no reference run: {', '.join(sorted(skipped))}.")
    if not live:
        lines.append("- Preview run: replies came from doubles, so this shows the plumbing works, not what a model can do.")
    return "\n".join(lines)+"\n"


def _text(content):
    return content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content)


def learned(base, final, report, chosen):
    """What the loop changed, for a person or an outside agent to read. `base` and `final` map file names to contents."""
    means = report["means"]
    shown = lambda value: "not judged" if value is None else f"{value:.2f}"
    scores = (f"Mean score, each sample with its own harness: {shown(means['base'])} before, {shown(means['harness2'])} after. "
              "One run cannot separate a gain from noise, and scores chose nothing here.") if means else "Not scored: this run had no check."
    if chosen is None:
        return f"# What was learned\n\nThe last sample in this run's order kept the base harness, so this package is the base harness.\n\n{scores}\n"
    lines = ["# What was learned", "", f"This package is the harness adopted on {chosen}, the last sample in this run's order.", "", scores, "",
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


def harness_sha256(folder):
    return hashlib.sha256(json.dumps({name: _text(body) for name, body in _harness_files(Path(folder)).items()}, sort_keys=True).encode()).hexdigest()


def clean_base(adapter):
    """The fingerprint of the harness a run starts from, refused if it holds anything learned. Each run rebuilds
    its base from code in its own folder, so nothing carries over from an earlier run; this keeps that true."""
    folder = Path(adapter.base_harness())
    files = _harness_files(folder)
    learned = [name for name in files if name.split("/")[0] in LEARNED_FOLDERS]
    if learned:
        raise ValueError("The base harness must start with nothing learned; found "+", ".join(learned[:3]))
    return harness_sha256(folder)


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
    folders = _folders(run_dir)
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
        selection = summary.get("selections", {}).get("seq", {})
        samples.append({"sample": sample.name, "folder": folders.get(sample.name), "done": bool(summary), "kept_base": selection.get("chosen_tree_is_base"),
                        "kept_label": selection.get("chosen_label"), "stages": stages})
    return {"samples": samples}


def token_report(rows, samples):
    """Tokens from base to improved, from the ledger: what the worker used on the base harness, what it used
    trying each edit, and what the improver spent. Input tokens include any the provider served from cache."""
    def spent(row):
        return (row["input_tokens"] or 0)+(row["output_tokens"] or 0)
    worker = {}
    totals = {"base_runs": 0, "edit_runs": 0, "improver": 0}
    for row in rows:
        parts = row["candidate"].split("/")
        if row["role"] == "improver":
            totals["improver"] += spent(row)
        elif row["role"] == "worker" and len(parts) >= 4:
            worker[(parts[2], parts[3])] = worker.get((parts[2], parts[3]), 0)+spent(row)
            totals["base_runs" if parts[1].endswith("-agg1") else "edit_runs"] += spent(row)
    per_sample = [{"sample": sample["sample"], "folder": sample["folder"], "base": worker.get((sample["sample"], "base1"), 0),
                   "kept_label": sample["kept_label"], "kept_base": sample["kept_base"], "kept": worker.get((sample["sample"], sample["kept_label"])) if sample["kept_label"] else None,
                   "stages": {stage["label"]: worker.get((sample["sample"], stage["label"]), 0) for stage in sample["stages"]}} for sample in samples]
    return {**totals, "total": sum(totals.values()), "samples": per_sample}


def last_sample(report):
    """The last sample in this run's order that left a harness. Which sample this is never depends on a score (D-028)."""
    rows = report["per_sample"]
    return next((sample for sample in reversed(report["stream"]) if rows.get(sample, {}).get("harness_dir")), None)


def _package(adapter, report, output, spec, *, live, check, approved_by="person"):
    """Copy the last sample's harness into package/harness, with what graded it. Returns (that sample, or None
    if it left none; the package's sample if it differs from the base, else None)."""
    from harness2.core import tree
    surface = adapter.substrate.surface()
    last = last_sample(report)
    source = adapter.spec.runs_root/report["per_sample"][last]["harness_dir"] if last else adapter.base_harness()
    package = Path(output)/"package"
    package.mkdir(exist_ok=True)
    final = tree.read_tree(surface, source)
    tree.write_tree(surface, final, package/"harness", stubs=False)
    base = tree.read_tree(surface, adapter.base_harness())
    chosen = last if final != base else None
    (package/"learned.md").write_text(learned(base, final, report, chosen), encoding="utf-8")
    (package/"spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    if check is not None:
        (package/"check.py").write_text(check, encoding="utf-8", newline="")
    if spec.get("judge"):
        (package/judging.FILE).write_text(json.dumps(judging.written(spec["judge"]), indent=2), encoding="utf-8")
    (package/"limitations.md").write_text(limitations(spec, report, chosen, live, approved_by), encoding="utf-8")
    return last, chosen


def summarize_report(task, report, last, chosen, live, folders):
    rows, scored = report["per_sample"], report.get("scored", True)
    summary = {"task": task["name"], "live": live, "stream_order": [folders.get(sample) or sample for sample in report["stream"]], "simulated_usage": not live,
               "package_sample": last, "package_is_base": chosen is None, "means": report["means"], "skipped": report["skipped"], "scored": scored, "samples": list(rows),
               "files_added": {t: r["files_added"] for t, r in rows.items()}, "reply_files": {t: r["reply_files"] for t, r in rows.items()}}
    if not scored:
        return {**summary, "grading": "not scored: this run had no check"}
    judged = " and model judge" if report.get("judged") else ""
    return {**summary, "checks": {"base": {t: r["base"]["score"] == 1.0 for t, r in rows.items()},
                                  "harness2": {t: r["harness2"]["score"] == 1.0 for t, r in rows.items()}},
            "scores": {"base": {t: r["base"]["score"] for t, r in rows.items()},
                       "harness2": {t: r["harness2"]["score"] for t, r in rows.items()}},
            "not_graded": report.get("not_graded", 0),
            "grading": f"controller-side check file{judged} after the run; never shown to the improver, and never used to choose a harness"}


def run_task(engine, workspace, job, snapshot, clients, *, live, check, judge=None, health=None):
    spec, identifier = job["spec"], job["id"]
    task = spec["task"]
    run_dir = workspace.run_dir(identifier)
    stream, truth, requests = build_dataset(snapshot, run_dir/"dataset", task, preview=not live, need_truth=needs_truth(spec))
    stream = run_order(stream, identifier)
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
    mcp = engine.mcp_run(spec, limits)
    adapter = _adapter(task, run_dir, output, clients["worker"], truth, check, live=live, limits=limits, tools=tools,
                       perception=perception, health=health, mcp=mcp)
    base = clean_base(adapter)
    hint_notes.stage(adapter, spec.get("hints"), _folders(run_dir), stream)
    if spec.get("tell_improver_worker"):
        hint_notes.stage_worker(adapter, spec["providers"]["worker"], stream)
    try:
        if mcp:
            mcp.check()
        grader = _grader(adapter, check, judge, truth, run_dir)
        report = run_stream(adapter, output, clients["improver"], tasks=stream, limits=limits, health=health,
                            on_grading=lambda: workspace.update_run(identifier, phase="grading"), scored=check is not None or judge is not None, grade=grader)
    finally:
        if mcp:
            mcp.close()
    if grader is not None:
        grader.save(output)
        report["judged"] = True
    last, chosen = _package(adapter, report, output, spec, live=live, check=check, approved_by=job.get("approved_by", "person"))
    return {**summarize_report(task, report, last, chosen, live, _folders(run_dir)), "base_sha256": base}


def run_trial(engine, workspace, job, snapshot, client, *, check, judge=None, health=None):
    """One stream sample on the base harness, or a finished run's package when the job names one, graded by
    the check (D-025). No improver and no package of its own.

    The result holds no trace or answer, because it reaches an outside agent, and
    those would invite procedure into the task text, which the loop learns (D-021).
    """
    spec, identifier = job["spec"], job["id"]
    task = spec["task"]
    run_dir, output = workspace.run_dir(identifier), workspace.run_dir(identifier)/"outputs"
    stream, truth, _ = build_dataset(snapshot, run_dir/"dataset", task, preview=False, need_truth=needs_truth(spec))
    limits, tools = engine.limits_of(spec), spec_tools(spec)
    perception = None if is_text(task) else engine.build_perception(workspace, identifier, job["cap_usd"], run_dir/"perception-cache", limits, tools)
    mcp = engine.mcp_run(spec, limits)
    adapter = _adapter(task, run_dir, output, client, truth, check, live=True, limits=limits, tools=tools, perception=perception, health=health, mcp=mcp)
    sample, base = stream[0], clean_base(adapter)
    try:
        if mcp:
            mcp.check()
        run = adapter.execute(sample, workspace.run_dir(identifier)/"harness" if job.get("from_run") else adapter.base_harness(), "trial")
    finally:
        if mcp:
            mcp.close()
    workspace.update_run(identifier, phase="grading")
    answer, not_judged = json.loads(adapter.deliverable_text(run)), None
    grader = Grader(adapter, check, judge, truth, run_dir)
    try:
        named = grader.named(sample, answer, run.relative_to(adapter.spec.runs_root).as_posix()) if check is not None or judge is not None else []
    except judging.NotJudged as error:
        named, not_judged = [], str(error)
    grader.save(output)
    steps = json.loads((run/"trace.json").read_text(encoding="utf-8"))["trace"]
    calls = {}
    for step in steps:
        if step["type"] == "tool":
            calls[step["name"]] = calls.get(step["name"], 0)+1
    folder = _folders(run_dir).get(sample, sample)
    result = {"kind": "trial", "task": task["name"], "live": True, "sample": folder, "scored": check is not None or judge is not None, "score": _score(named) if named else None,
              **({"not_judged": not_judged} if not_judged else {}),
              "base_sha256": base, "checks": named, "stopped": next((step["reason"] for step in steps if step["type"] == "stopped"), None), "tool_calls": calls,
              "note": "One sample on the base harness: a smoke test, not a measure of what the loop can reach."}
    return {**result, **_tried_harness(workspace, job, folder)} if job.get("from_run") else result


def _tried_harness(workspace, job, folder):
    """What a reader must know about a trial of a package: which harness ran, and whether its run saw this sample."""
    source = job["from_run"]
    folders = _folders(workspace.run_dir(source))
    result = workspace.run(source)["result"]
    seen = folder in {folders.get(sample, sample) for sample in result.get("samples") or result["scores"]["base"]}
    return {"harness": {"from_run": source, "sha256": harness_sha256(workspace.run_dir(job["id"])/"harness"), "sample_seen": seen},
            "note": (f"One sample on the package of run {source}, which saw this sample, so this reads high: a smoke test, not a measure." if seen else
                     f"One sample on the package of run {source}, which never saw this sample. One sample is still a smoke test, not a measure.")}


def saved_answers(run_dir):
    """Each answer a run saved, as (sample id, where it sits, answer)."""
    folder = Path(run_dir)/"outputs"/"runs"/"executions"
    found = []
    for path in sorted(folder.rglob("answer.json")) if folder.is_dir() else []:
        try:
            sample = json.loads(path.with_name("execution.json").read_text(encoding="utf-8"))["task"]
            found.append((sample, path.parent.relative_to(folder).as_posix(), json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, KeyError):
            continue
    return found


def _score(named):
    return sum(item["passed"] for item in named)/len(named)


def _changes(old, new):
    """The checks whose result differs, including those that came or went."""
    before, after = ({item["name"]: item["passed"] for item in named} for named in (old, new))
    return [{"name": name, "old": before.get(name), "new": after.get(name)} for name in {**before, **after} if before.get(name) != after.get(name)]


def _old_checks(source, items):
    """What the run's own check says now; a check that no longer runs is no reason to stop looking at a draft."""
    try:
        return shown_checks(source, items)
    except (CheckError, ValueError):
        return None


def run_truths(workspace, run_dir):
    """{sample id: truth} for a run. A run with no check froze none, so its samples' current truth files stand in, matched by folder."""
    saved = Path(run_dir)/"dataset"/"controller_truth"/"ground_truth.json"
    truth = json.loads(saved.read_text(encoding="utf-8")) if saved.is_file() else {}
    folders, found = _folders(run_dir), {}
    for sample, value in truth.items():
        path = workspace.samples_path/folders.get(sample, "")/"truth.json"
        if value is None and sample in folders and path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
        if value is not None:
            found[sample] = value
    return found


def try_check(workspace, source):
    """Score saved answers with a check and say what changes, against each run's own check. Reads runs and writes
    nothing. Reports scores and check names, never an answer, and only to whoever is setting up the task."""
    tried, skipped, no_truth = [], [], []
    finished = [job for job in workspace.runs() if job["status"] not in {"queued", "running"} and job["spec"].get("task")]
    for job in finished[:MAX_TRY_RUNS]:
        run_dir = workspace.run_dir(job["id"])
        truth, saved = run_truths(workspace, run_dir), saved_answers(run_dir)
        answers = [item for item in saved if item[0] in truth]
        if not answers:
            (no_truth if saved else skipped).append(job["id"])
            continue
        items = [(truth[sample], answer) for sample, _, answer in answers]
        new = shown_checks(source, items)
        own = job["spec"].get("check")
        old = (own and _old_checks(workspace.check_source(own), items)) or [None]*len(items)
        folders = _folders(run_dir)
        tried += [{"run": job["id"], "sample": folders.get(sample, sample), "where": where, "old": old_named and _score(old_named), "new": _score(new_named),
                   "changed": _changes(old_named or [], new_named)} for (sample, where, _), old_named, new_named in zip(answers, old, new)]
    changed = [item for item in tried if item["old"] != item["new"] or item["changed"]]
    return {"answers": len(tried), "changed": len(changed), "results": changed, "no_answers_saved": skipped, "no_truth": no_truth, "runs_left_out": max(0, len(finished)-MAX_TRY_RUNS),
            "note": "Saved answers only, so this shows what an edit changes, not how a harness would do."}


def _kept_verdicts(run_dir):
    path = Path(run_dir)/"outputs"/"judged.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def try_judges(workspace, judge, *, reasons):
    """Judge saved answers with these judges and say what changes against each run's own verdicts. Writes no run;
    new verdicts are kept for the next try. Reasons can quote the expected answer, so only a person gets them."""
    tried, skipped = [], []
    finished = [job for job in workspace.runs() if job["status"] not in {"queued", "running"} and job["spec"].get("task")]
    for job in finished[:MAX_TRY_RUNS]:
        run_dir = workspace.run_dir(job["id"])
        saved = saved_answers(run_dir)
        if not saved:
            skipped.append(job["id"])
            continue
        truth, kept, folders = run_truths(workspace, run_dir), _kept_verdicts(run_dir), _folders(run_dir)
        requests = Path(run_dir)/"dataset"/"worker_inputs"/"requests"
        for sample, where, answer in saved:
            request = requests/f"{sample}.txt"
            old = kept.get(f"executions/{where}")
            old = old if isinstance(old, list) else None
            item = {"run": job["id"], "sample": folders.get(sample, sample), "where": where}
            try:
                found = judge.verdicts(truth.get(sample), answer, request.read_text(encoding="utf-8") if request.is_file() else None)
            except judging.NotJudged as error:
                tried.append({**item, "not_judged": str(error)})
                continue
            shown = [{key: value for key, value in verdict.items() if reasons or key != "reason"} for verdict in found]
            tried.append({**item, "old": old and _score(old), "new": _score(found), "verdicts": shown, "changed": _changes(old or [], found)})
    judged = [item for item in tried if "new" in item]
    return {"answers": len(tried), "judged": len(judged), "changed": sum(bool(item["changed"]) or item["old"] != item["new"] for item in judged),
            "results": tried, "no_answers_saved": skipped, "runs_left_out": max(0, len(finished)-MAX_TRY_RUNS),
            "note": "Saved answers only, so this shows what the judges make of them, not how a harness would do."}


class _NoCalls:
    """For a regrade or an agent's try, which spend nothing: only verdicts already kept count, and any other answer is left not judged."""
    def chat(self, *args, **kwargs):
        from .spark import ModelError
        raise ModelError("this answer has no kept verdict, and only a person can pay for a new one")


def regrade(engine, workspace, job, *, live):
    """Score a run from its saved answers with its check file; makes no model or tool calls."""
    spec, identifier = job["spec"], job["id"]
    task = spec["task"]
    run_dir = workspace.run_dir(identifier)
    output = run_dir/"outputs"
    stream, truth, _ = build_dataset(run_dir/"snapshot", run_dir/"dataset", task, preview=not live)
    stream = run_order(stream, identifier)
    client = SimpleNamespace(profile=engine.registry.profile(spec["roles"]["worker"]))
    check = workspace.check_source(spec["check"]) if spec.get("check") else None
    adapter = _adapter(task, run_dir, output, client, truth, check, live=live, limits=engine.limits_of(spec), tools=spec_tools(spec))
    judge = judging.Judge(spec["judge"], _NoCalls(), workspace.home/"judge-cache", live=live) if spec.get("judge") else None
    grader = _grader(adapter, check, judge, truth, run_dir)
    report = grade_stream(adapter, output, stream, {"regraded": True}, scored=check is not None or judge is not None, grade=grader)
    if grader is not None:
        grader.save(output)
        report["judged"] = True
    last, chosen = _package(adapter, report, output, spec, live=live, check=check, approved_by=job.get("approved_by", "person"))
    return summarize_report(task, report, last, chosen, live, _folders(run_dir))
