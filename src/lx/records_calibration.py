"""Fixed PROD-002 calibration request. Requires separate explicit user approval.

No live request is made by imports or the offline trial command. The only live
entry point here is the exact fixed synthetic dataset reviewed in
docs/records-calibration-review.md; arbitrary input files are not accepted.
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import tempfile

from .records import build_records_dataset
from .spark import ChatClient
from .trials import calibrate, dataset_hash, experiment_hash
from .usage import BudgetScope, summarize, usage_rows


APPROVED_DATASET = "c4c1917a69aa154fa304a936ec8bf9c1a5a6c596b64b2e95abeb047306ada47a"
APPROVED_EXPERIMENT = "d8bd6759014d6e767c62c951b676975fee0d0f7264c38f1f2219c3e70b229aa2"
SCOPE = "records-calibration-v1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-dataset", required=True,
                        help="Exact dataset SHA256, only after the user approves the dated review")
    args = parser.parse_args()
    if args.approve_dataset != APPROVED_DATASET:
        parser.error("Dataset approval does not match this fixed request")
    from .engine import Engine, ROOT
    # Refuse to initialize an empty replacement for the historical pilot ledger.
    ledger_path = ROOT/".lx"/"live-spike"/"pilot-budget.sqlite3"
    if not ledger_path.is_file() or ledger_path.is_symlink():
        raise ValueError("The existing shared pilot ledger is required")
    app = Engine()
    ledger = app.pilot_ledger
    state = ledger.summary()
    if Decimal(str(state["spent_usd"])) < Decimal("0.036492926") or state["settled_calls"] < 154:
        raise ValueError("Shared pilot ledger is older than the recorded checkpoint")
    if usage_rows(ledger, SCOPE+"/"):
        raise ValueError("This calibration has already dispatched calls; obtain a new reviewed retry")
    output = app.root/SCOPE
    output.mkdir(parents=True, exist_ok=True)
    # Exclusive marker prevents concurrent dispatch or accidental repeat runs.
    with (output/"started.json").open("x", encoding="utf-8") as marker:
        json.dump({"dataset_sha256": APPROVED_DATASET, "run_cap_usd": 0.5, "call_limit": 40}, marker)
    with tempfile.TemporaryDirectory(prefix="lx-records-calibration-") as directory:
        dataset = build_records_dataset(Path(directory), 20, 40, seed=20261028)
        if dataset_hash(dataset) != APPROVED_DATASET:
            raise ValueError("Generated inputs differ from the reviewed synthetic dataset")
        if experiment_hash(dataset) != APPROVED_EXPERIMENT:
            raise ValueError("Contract, controller truth or verifier differs from the reviewed experiment")
        # Runtime-only credential access through the existing registered Spark
        # loader. Fixed Meta transport; no credentials enter files or outputs.
        client = ChatClient(app.make_live_transport(call_limit=40),
                             BudgetScope(ledger, SCOPE, "0.50"), retries=0)
        report = calibrate(dataset, output, client, live=True)
    report.update(usage=summarize(usage_rows(ledger, SCOPE+"/")), ledger_before=state, ledger_after=ledger.summary())
    (output/"calibration.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
