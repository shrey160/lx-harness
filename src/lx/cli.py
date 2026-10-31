"""The `lx` command: the orchestrator's actions in a terminal, with results as JSON on stdout.

`approve`, `grant` and `run resume` are a person's, so they stay out of the
actions and read a typed answer from a real terminal. An agent gets the same
actions over MCP and has no route to these.
"""
import argparse
import json
from pathlib import Path
import sys

from .engine import DEFAULT_ROOT, DEFAULT_ROLE_ENTRY, GRANT_DAYS, GRANT_RUNS, Engine
from .orchestration import TEMPLATES, Orchestration


def confirm(summary):
    """Stops a pipe or an agent's shell, which has no terminal. Not a defence against a hostile local program (D-024)."""
    if not sys.stdin.isatty():
        raise ValueError("This needs a person at a terminal")
    print(summary, file=sys.stderr)
    if input("Type yes to accept: ").strip().lower() != "yes":
        raise ValueError("Not accepted; nothing changed")


def review(project):
    """What a person reads before accepting, the same facts as the GUI's review."""
    draft = project["draft"]
    lines = [f"Version {draft['version']}, {draft['mode']}, ${draft['budget_usd']} for this run, ${project['budget']['cap_usd']} for the workspace",
             *(f"  {entry['who']} ({entry['host']}): {entry['gets']}" for entry in project["notice"])]
    if DEFAULT_ROLE_ENTRY in draft["roles"].values():
        lines.append("Spark Contributor may use approved content for training.")
    if project["blocked"]:
        raise ValueError(project["blocked"])
    return "\n".join(lines)


def approve(engine, lx, args):
    project = engine.open_workspace(args.workspace)
    summary = review(project)
    if project["draft"].get("task"):
        tested = lx.check_test(args.workspace)
        summary += f"\nCheck: a blank answer scores at most {tested['blank_score']}, another sample's answer at most {tested['wrong_score']}."
        summary += "".join("\n  "+text for text in tested["weaknesses"])
    confirm(summary)
    approved = engine.approve(project["id"], {"version": project["draft"]["version"], "data_approved": True, "contributor_accepted": True,
                                              "notice_accepted": True})["approved"]
    return {"approved": approved["version"], "by": approved["by"]}


def grant(engine, lx, args):
    project = engine.open_workspace(args.workspace)
    if args.revoke:
        return {"grant": engine.revoke_grant(project["id"])["grant"]}
    if args.cap is None:
        raise ValueError("Give a spending cap with --cap")
    confirm(review(project)+f"\nA connected agent may start {args.runs} live runs here within ${args.cap}, for {args.days} days. "
            "No person reviews them, and a weak check still waits for you.")
    return {"grant": engine.set_grant(project["id"], {"cap_usd": args.cap, "days": args.days, "runs": args.runs, "data_approved": True,
                                                      "contributor_accepted": True, "notice_accepted": True})["grant"]}


def resume(engine, lx, args):
    identifier = engine.open_workspace(args.workspace)["id"]
    confirm(f"Resume run {args.run} within the budget it was approved with.")
    return {"status": engine.resume(identifier, args.run)["status"]}


def samples(engine, lx, args):
    return lx.read_sample(args.workspace, args.read) if args.read else lx.samples(args.workspace)


def spec_draft(engine, lx, args):
    read = lambda path: Path(path).read_text(encoding="utf-8")
    return lx.spec_draft(args.workspace, goal=args.goal, mode=args.mode, worker=args.worker, improver=args.improver,
                         tools=args.tools.split(",") if args.tools is not None else None,
                         task=json.loads(read(args.task)) if args.task else None, check=read(args.check) if args.check else None,
                         limits=json.loads(read(args.limits)) if args.limits else None)


def parser():
    top = argparse.ArgumentParser(prog="lx", description=__doc__.split("\n")[0])
    top.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="LX's state folder; the default is the app's")
    commands = top.add_subparsers(dest="command", required=True)

    def command(group, name, run, *, folder=True, **options):
        item = group.add_parser(name, **options)
        item.set_defaults(handler=run)
        if folder:
            item.add_argument("--workspace", default=".", help="workspace folder, by default this one")
        return item

    def action(group, name, method, help):
        return command(group, name, lambda engine, lx, args: getattr(lx, method)(args.workspace), help=help)

    command(commands, "status", lambda engine, lx, args: lx.status(), folder=False, help="whether LX can run; never shows a key")
    item = command(commands, "init", lambda engine, lx, args: lx.init(args.workspace, args.template, goal=args.goal, name=args.name),
                   help="start a workspace on a folder of samples")
    item.add_argument("--template", required=True, choices=tuple(TEMPLATES))
    item.add_argument("--goal")
    item.add_argument("--name")
    item = command(commands, "samples", samples, help="list the samples, or read one text file")
    item.add_argument("--read", metavar="PATH")

    spec = commands.add_parser("spec", help="show or change the spec").add_subparsers(dest="spec", required=True)
    action(spec, "show", "spec_show", "the current spec")
    item = command(spec, "draft", spec_draft, help="write a new spec version, which clears any approval")
    item.add_argument("--goal")
    item.add_argument("--mode", choices=("controlled_preview", "live"))
    item.add_argument("--worker")
    item.add_argument("--improver")
    item.add_argument("--tools", help="comma-separated tool ids")
    item.add_argument("--task", metavar="FILE", help="JSON of the task fields to change")
    item.add_argument("--check", metavar="FILE", help="a new check.py")
    item.add_argument("--limits", metavar="FILE", help="JSON of run limits")

    check = commands.add_parser("check", help="test the check").add_subparsers(dest="check", required=True)
    action(check, "test", "check_test", "self-test the check on the samples")
    action(check, "shape", "truth_shape", "field names and types of the answers, never their values")

    action(commands, "ready", "ready", "whether this spec could run live on the samples as they are now; no calls")
    action(commands, "preview", "preview", "run on doubles: no calls, no spending")
    command(commands, "approve", approve, help="accept the current spec, as a person")
    item = command(commands, "grant", grant, help="let a connected agent start live runs here, as a person")
    item.add_argument("--cap", type=float, help="spending cap in USD")
    item.add_argument("--days", type=int, default=GRANT_DAYS)
    item.add_argument("--runs", type=int, default=GRANT_RUNS)
    item.add_argument("--revoke", action="store_true")

    run = commands.add_parser("run", help="start and follow live runs").add_subparsers(dest="run_command", required=True)
    action(run, "start", "run_start", "start an approved or granted live run")
    action(run, "trial", "trial_start", "try the live spec on one sample with the base harness, within the trial cap")
    action(run, "status", "run_status", "this workspace's runs")
    item = command(run, "stop", lambda engine, lx, args: lx.run_stop(args.workspace, args.run), help="stop a run")
    item.add_argument("run")
    item = command(run, "resume", resume, help="resume a stopped run, as a person")
    item.add_argument("run")

    item = command(commands, "results", lambda engine, lx, args: lx.results(args.workspace, args.run), help="scores and what was learned")
    item.add_argument("run")
    return top


def main(argv=None):
    args = parser().parse_args(argv)
    if hasattr(args, "workspace"):
        args.workspace = str(Path(args.workspace).resolve())
    engine = Engine(args.root)
    try:
        print(json.dumps(args.handler(engine, Orchestration(engine), args), indent=2))
    except (ValueError, OSError) as exc:
        print(f"lx: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
