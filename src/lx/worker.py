"""Controller subprocess for managed jobs; live credentials load only here, never in task workers."""
import argparse
from pathlib import Path
import sys
import traceback

from . import judging
from .engine import ROLES, Engine
from .health import RunHealth, StoppedEarly
from .spark import ModelError
from .taskrun import run_task, run_trial
from .workflow import optimize_images
from .workspace import Workspace


def execute(engine, workspace, identifier, snapshot):
    job = workspace.run(identifier)
    if job["status"] == "cancelled":
        return {"cancelled":True}
    engine.verify_job_approval(workspace,job,snapshot)
    job = workspace.update_run(identifier,status="running",phase="preflight",launch_pending=False,error=None)
    mode = job["spec"].get("mode","controlled_preview")
    live = mode == "live"
    limits = engine.limits_of(job["spec"])
    trial = job.get("kind") == "trial"
    clients = engine.build_clients(workspace, job["spec"], identifier, job["cap_usd"] if trial else job["spec"]["budget_usd"])
    if trial:
        clients = {role: client for role, client in clients.items() if role in {"worker", "judge"}}
    # Once per provider entry: a bad key or endpoint stops the run before the loop starts. A judge on its own entry is
    # tried too, since it is called only after the loop has spent its money.
    entries = {job["spec"]["roles"][r]:r for r in reversed(ROLES) if r in clients}
    if "judge" in clients and job["spec"]["judge"]["entry"] not in entries:
        entries[job["spec"]["judge"]["entry"]] = "judge"
    for role in entries.values():
        message,_ = clients[role].chat([{"role":"developer","content":"LX_PREFLIGHT"},{"role":"user","content":"Reply PREFLIGHT_OK"}],
                                       role=role,phase="preflight",candidate=identifier,
                                       timeout=max(120,limits["call_seconds"]))
        if (message["content"] or "").strip() != "PREFLIGHT_OK":
            raise ValueError("Live preflight failed" if live else "Controlled preflight failed")
    workspace.update_run(identifier,phase="trying" if trial else "improving")
    output = workspace.run_dir(identifier)/"outputs"
    health = RunHealth(workspace.run_dir(identifier)/"health.json",stop_after=limits["stop_after_empty_runs"])
    judge = _judge(workspace,job,clients,live,limits)
    if trial:
        return engine.finish_trial(workspace,job,run_trial(engine,workspace,job,snapshot,clients["worker"],
                                                           check=_check(workspace,job),judge=judge,health=health))
    if job["spec"].get("task"):
        result = run_task(engine,workspace,job,snapshot,clients,live=live,check=_check(workspace,job),judge=judge,health=health)
    else:
        result = optimize_images(snapshot,output,clients["improver"],engine.demo.controller_truth,job["spec"],live=live,
                                  worker_client=clients["worker"],limits=limits,health=health)
    return engine.finish_run(workspace,job,result)


def _check(workspace,job):
    """The run's check, or None for a run with no check."""
    reference = job["spec"].get("check")
    return workspace.check_source(reference) if reference else None


def _judge(workspace,job,clients,live,limits):
    """The run's model judge, or None. Verdicts are kept per workspace, so the same answer is never judged twice."""
    spec = job["spec"].get("judge")
    if not spec or "judge" not in clients:
        return None
    return judging.Judge(spec,clients["judge"],workspace.home/"judge-cache",live=live,timeout=max(120,limits["call_seconds"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--workspace",type=Path,required=True)
    parser.add_argument("--job",required=True)
    parser.add_argument("--execute",action="store_true")
    args = parser.parse_args()
    engine = Engine(args.root)
    workspace = Workspace.open(args.workspace)
    job = workspace.run(args.job)
    try:
        if args.execute:
            execute(engine,workspace,args.job,Path.cwd())
        else:
            if job["status"] == "cancelled":
                return 0
            from h2portable.jobs import read_job, run_job
            try:
                # A resumed job must keep its command identity, including this limit.
                limit = read_job(args.job,workspace.home/"core-jobs")["spec"]["timeout"]
            except ValueError:
                limit = engine.limits_of(job["spec"])["run_seconds"]
            state = run_job(args.job,workspace.home/"core-jobs","local",workspace.run_dir(args.job)/"snapshot",limit,
                [sys.executable,"-m","lx.worker","--root",str(engine.root),"--workspace",str(workspace.folder),"--job",args.job,"--execute"])
            if state["status"] != "completed":
                # Keep the reason the run itself recorded; this is only for one that could not.
                reason = workspace.run(args.job).get("error") or "Run ended before completion; inspect its reported state and resume where available"
                workspace.update_run(args.job,status=state["status"],phase="stopped" if state["status"]=="cancelled" else "failed",
                                     error=reason,launch_pending=False)
        return 0
    except Exception as exc:
        reportable = isinstance(exc,(ValueError,ModelError,StoppedEarly))
        if not reportable:
            traceback.print_exc()  # the attempt's local log; the run record stays free of private detail
        workspace.update_run(args.job,status="failed",phase="failed",error=str(exc) if reportable else "Controller failed; no private exception details exposed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
