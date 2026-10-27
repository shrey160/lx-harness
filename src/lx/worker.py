"""Controller subprocess for managed preview jobs; never loads live credentials."""
import argparse
import json
from pathlib import Path
import sys

from .app import Application
from .spark import SparkClient
from .usage import BudgetScope
from .usage import usage_rows, summarize
from .workflow import PreviewTransport, optimize_preview


def execute(app, identifier, snapshot):
    job = app.store.get("job",identifier)
    if job["status"] == "cancelled":
        return {"cancelled":True}
    app.verify_job_approval(job,snapshot)
    job.update(status="running",phase="preflight",launch_pending=False)
    job.pop("error",None)
    app.store.save("job",job)
    client = SparkClient(PreviewTransport(app.demo.controller_truth), BudgetScope(app.preview_ledger,identifier,job["spec"]["budget_usd"]))
    message,_ = client.chat([{"role":"developer","content":"LX_PREFLIGHT"},{"role":"user","content":"Reply PREFLIGHT_OK"}],
                           role="orchestrator",phase="preflight",candidate=identifier)
    if message["content"] != "PREFLIGHT_OK":
        raise ValueError("Controlled preflight failed")
    job.update(phase="improving")
    app.store.save("job",job)
    result = optimize_preview(snapshot,app.root/"outputs"/identifier,client,app.demo.controller_truth,job["spec"])
    package = app.root/"outputs"/identifier/"package"
    result["usage"] = summarize(usage_rows(app.preview_ledger,identifier+"/"))
    result["usage_basis"] = "simulated provider usage; no live calls or charges"
    result["package_version"] = identifier+"-spec-v"+str(job["spec"]["version"])
    (package/"usage.json").write_text(json.dumps(result["usage"],indent=2))
    (package/"result.json").write_text(json.dumps(result,indent=2))
    job.update(status="completed",phase="complete",result=result)
    app.store.save("job",job)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--job",required=True)
    parser.add_argument("--execute",action="store_true")
    args = parser.parse_args()
    app = Application(args.root)
    job = app.store.get("job",args.job)
    try:
        if args.execute:
            execute(app,args.job,Path.cwd())
        else:
            if job["status"] == "cancelled":
                return 0
            from h2portable.jobs import run_job
            state = run_job(args.job,app.root/"core-jobs","local",app.root/"inputs"/args.job,900,
                [sys.executable,"-m","lx.worker","--root",str(app.root),"--job",args.job,"--execute"])
            if state["status"] != "completed":
                job = app.store.get("job",args.job)
                job.update(status=state["status"],phase="stopped" if state["status"]=="cancelled" else "failed",
                           error="Run ended before completion; inspect its reported state and resume where available",launch_pending=False)
                app.store.save("job",job)
        return 0
    except Exception as exc:
        job.update(status="failed",phase="failed",error=str(exc) if isinstance(exc,ValueError) else "Controller failed; no private exception details exposed")
        app.store.save("job",job)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
