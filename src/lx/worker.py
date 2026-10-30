"""Controller subprocess for managed jobs; live credentials load only here, never in task workers."""
import argparse
from pathlib import Path
import sys

from .engine import Engine
from .usage import usage_rows, summarize
from .workflow import optimize_preview
from .workspace import Workspace, write_json


def execute(engine, workspace, identifier, snapshot):
    job = workspace.run(identifier)
    if job["status"] == "cancelled":
        return {"cancelled":True}
    engine.verify_job_approval(workspace,job,snapshot)
    job = workspace.update_run(identifier,status="running",phase="preflight",launch_pending=False,error=None)
    mode = job["spec"].get("mode","controlled_preview")
    live = mode == "live"
    client = engine.build_client(workspace, mode, identifier, job["spec"]["budget_usd"])
    message,_ = client.chat([{"role":"developer","content":"LX_PREFLIGHT"},{"role":"user","content":"Reply PREFLIGHT_OK"}],
                           role="orchestrator",phase="preflight",candidate=identifier)
    if (message["content"] or "").strip() != "PREFLIGHT_OK":
        raise ValueError("Live preflight failed" if live else "Controlled preflight failed")
    workspace.update_run(identifier,phase="improving")
    output = workspace.run_dir(identifier)/"outputs"
    result = optimize_preview(snapshot,output,client,engine.demo.controller_truth,job["spec"],live=live)
    result["usage"] = summarize(usage_rows(engine.live_ledger if live else workspace.preview_ledger,identifier+"/"))
    result["usage_basis"] = ("live Spark usage at verified Contributor list prices; not provider billing"
                             if live else "simulated provider usage; no live calls or charges")
    result["package_version"] = identifier+"-spec-v"+str(job["spec"]["version"])
    write_json(output/"package"/"usage.json",result["usage"])
    write_json(output/"package"/"result.json",result)
    workspace.publish_package(identifier)
    workspace.update_run(identifier,status="completed",phase="complete",result=result)
    return result


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
            from h2portable.jobs import run_job
            state = run_job(args.job,workspace.home/"core-jobs","local",workspace.run_dir(args.job)/"snapshot",900,
                [sys.executable,"-m","lx.worker","--root",str(engine.root),"--workspace",str(workspace.folder),"--job",args.job,"--execute"])
            if state["status"] != "completed":
                workspace.update_run(args.job,status=state["status"],phase="stopped" if state["status"]=="cancelled" else "failed",
                                     error="Run ended before completion; inspect its reported state and resume where available",launch_pending=False)
        return 0
    except Exception as exc:
        workspace.update_run(args.job,status="failed",phase="failed",error=str(exc) if isinstance(exc,ValueError) else "Controller failed; no private exception details exposed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
