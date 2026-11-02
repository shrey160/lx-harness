"""The `lx` command: the orchestrator's actions in a terminal, with results as JSON on stdout.

`grant`, `run resume` and `approve` are a person's, and read a typed answer from
a real terminal. An agent approves with `approve --as-agent` or over MCP (D-026),
and has no route to the other two.
"""
import argparse
import getpass
import json
from pathlib import Path
import sys

from . import mcp_client
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
    if args.as_agent:
        return lx.approve(args.workspace)
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


def _hidden(variable):
    if not sys.stdin.isatty():
        raise ValueError("A secret needs a real terminal")
    return getpass.getpass(f"Value of {variable}: ")


def mcp_connect(engine, lx, args):
    words = args.words[1:] if args.words[:1] == ["--"] else args.words
    mounts = [dict(zip(("source", "target"), item.rsplit(":", 1))) for item in args.mount]
    config = ({"kind": "docker", "image": args.image, "args": words, "network": args.network, "mounts": mounts} if args.image else
              {"kind": "url", "url": args.url} if args.url else {"kind": "command", "command": words})
    place = {"docker": f"in Docker, from the image {args.image}", "url": f"at {args.url}", "command": "on your machine, outside the sandbox: "+" ".join(words)}[config["kind"]]
    confirm(f"Connect the MCP server {args.name}, which runs {place}. It stays connected for later runs.")
    return _shown(engine, engine.mcp_connect(args.name, config, {variable: _hidden(variable) for variable in args.secret}))


def mcp_secret(engine, lx, args):
    return _shown(engine, engine.mcp_secret(args.name, args.variable, _hidden(args.variable)))


def _shown(engine, server):
    return next(item for item in engine.mcp_view() if item["id"] == server["id"])


def mcp_mark(engine, lx, args):
    confirm(f"Mark as read-only on {args.name}: {', '.join(args.tools) or 'nothing'}. A run repeats calls, so a tool that changes anything must stay unmarked.")
    return _shown(engine, engine.mcp_mark(args.name, args.tools))


def samples(engine, lx, args):
    return lx.read_sample(args.workspace, args.read) if args.read else lx.samples(args.workspace)


def spec_draft(engine, lx, args):
    read = lambda path: Path(path).read_text(encoding="utf-8")
    return lx.spec_draft(args.workspace, goal=args.goal, mode=args.mode, worker=args.worker, improver=args.improver,
                         tools=args.tools.split(",") if args.tools is not None else None,
                         mcp_tools=[item for item in args.mcp_tools.split(",") if item] if args.mcp_tools is not None else None,
                         task=json.loads(read(args.task)) if args.task else None, check=read(args.check) if args.check else None,
                         limits=json.loads(read(args.limits)) if args.limits else None, use_eval_file=args.eval_file, no_check=args.no_check)


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
    item = command(commands, "guide", lambda engine, lx, args: lx.guide(args.topic), folder=False, help="list the guides, or read one")
    item.add_argument("topic", nargs="?")
    command(commands, "environment", lambda engine, lx, args: lx.environment(), folder=False, help="what this install offers a run: sandbox, tools, limits")
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
    item.add_argument("--mcp-tools", help="comma-separated server/tool ids; empty takes them out")
    item.add_argument("--task", metavar="FILE", help="JSON of the task fields to change")
    item.add_argument("--check", metavar="FILE", help="a new check.py")
    item.add_argument("--eval-file", action="store_true", help="take the check from eval/rules.json or eval/check.py")
    item.add_argument("--no-check", action="store_true", help="run unscored; only a person can approve this")
    item.add_argument("--limits", metavar="FILE", help="JSON of run limits")

    check = commands.add_parser("check", help="test the check").add_subparsers(dest="check", required=True)
    item = command(check, "test", lambda engine, lx, args: lx.check_test(args.workspace, Path(args.check).read_text(encoding="utf-8") if args.check else None, args.eval_file),
                   help="self-test the check on the samples")
    item.add_argument("--check", metavar="FILE", help="test this draft without saving it")
    item.add_argument("--eval-file", action="store_true", help="test the check in eval/ without saving it")
    item = command(check, "try", lambda engine, lx, args: lx.check_try(args.workspace, Path(args.check).read_text(encoding="utf-8") if args.check else None, args.eval_file),
                   help="score saved answers with the check and list what changes")
    item.add_argument("--check", metavar="FILE", help="try this draft without saving it")
    item.add_argument("--eval-file", action="store_true", help="try the check in eval/ without saving it")
    action(check, "shape", "truth_shape", "field names and types of the answers, never their values")

    action(commands.add_parser("eval", help="the check kept in eval/").add_subparsers(dest="eval", required=True),
           "init", "eval_init", "write eval/rules.json from the shape of the answers")

    action(commands, "ready", "ready", "whether this spec could run live on the samples as they are now; no calls")
    action(commands, "preview", "preview", "run on doubles: no calls, no spending")
    item = command(commands, "approve", approve, help="accept the current spec, as a person")
    item.add_argument("--as-agent", action="store_true", help="approve for the person who connected this agent, with no prompt")
    item = command(commands, "grant", grant, help="let a connected agent start live runs here, as a person")
    item.add_argument("--cap", type=float, help="spending cap in USD")
    item.add_argument("--days", type=int, default=GRANT_DAYS)
    item.add_argument("--runs", type=int, default=GRANT_RUNS)
    item.add_argument("--revoke", action="store_true")

    run = commands.add_parser("run", help="start and follow live runs").add_subparsers(dest="run_command", required=True)
    action(run, "start", "run_start", "start an approved or granted live run")
    item = command(run, "trial", lambda engine, lx, args: lx.trial_start(args.workspace, args.from_run), help="try the live spec on one sample, within the trial cap")
    item.add_argument("--from", dest="from_run", metavar="RUN", help="use that finished run's package instead of the base harness")
    action(run, "status", "run_status", "this workspace's runs")
    item = command(run, "stop", lambda engine, lx, args: lx.run_stop(args.workspace, args.run), help="stop a run")
    item.add_argument("run")
    item = command(run, "edits", lambda engine, lx, args: lx.edits(args.workspace, args.run), help="what the improver has written, during a run or after it")
    item.add_argument("run")
    item = command(run, "resume", resume, help="resume a stopped run, as a person")
    item.add_argument("run")

    mcp = commands.add_parser("mcp", help="MCP servers whose tools a run's agent can use").add_subparsers(dest="mcp_command", required=True)
    command(mcp, "list", lambda engine, lx, args: {"tools": engine.mcp_choices()}, folder=False, help="connected tools")
    item = command(mcp, "connect", mcp_connect, folder=False, help="connect a server, as a person")
    item.add_argument("name")
    item.add_argument("--image", help="run this Docker image")
    item.add_argument("--url", help="use a server already running at this address")
    item.add_argument("--network", action="store_true", help="let the container use the network")
    item.add_argument("--mount", action="append", default=[], metavar="FOLDER:PATH", help="share a folder with the container, read-only")
    item.add_argument("--secret", action="append", default=[], metavar="NAME", help=f"ask for a secret for the server; for a URL, {mcp_client.URL_TOKEN} is sent as a bearer token")
    item.add_argument("words", nargs=argparse.REMAINDER, help="the program and its arguments, or the image's arguments, after --")
    item = command(mcp, "secret", mcp_secret, folder=False, help="add or replace a server's secret, as a person")
    item.add_argument("name")
    item.add_argument("variable")
    item = command(mcp, "read-only", mcp_mark, folder=False, help="say which tools change nothing, as a person")
    item.add_argument("name")
    item.add_argument("tools", nargs="*")
    item = command(mcp, "refresh", lambda engine, lx, args: _shown(engine, engine.mcp_refresh(args.name)), folder=False, help="read a server's tools again")
    item.add_argument("name")
    item = command(mcp, "remove", lambda engine, lx, args: engine.mcp_remove(args.name), folder=False, help="disconnect a server")
    item.add_argument("name")

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
