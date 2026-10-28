"""Run five full-size offline checks in native temporary storage; export safe evidence."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile

from lx.budget import BudgetLedger
from lx.records import build_records_dataset
from lx.records_workflow import RecordsPreviewTransport
from lx.spark import SparkClient
from lx.trials import run_trials
from lx.usage import BudgetScope


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lx-records-validation-") as directory:
        root = Path(directory)
        dataset = build_records_dataset(root/"dataset")
        ledger = BudgetLedger(root/"simulated.sqlite3", initial_spend_usd=0)
        def factory(identifier):
            return SparkClient(RecordsPreviewTransport(), BudgetScope(ledger, identifier, "0.4"))
        result = run_trials(dataset, root/"experiment", factory)
        (evidence/"offline-trials.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
        (evidence/"worker_inputs").mkdir(exist_ok=True)
        (evidence/"controller_truth").mkdir(exist_ok=True)
        shutil.copyfile(dataset.root/"worker_inputs"/"manifest.json", evidence/"worker_inputs"/"manifest.json")
        shutil.copyfile(dataset.root/"controller_truth"/"ground_truth.json", evidence/"controller_truth"/"ground_truth.json")
    print(json.dumps({"live_calls": 0, "live_spend_usd": 0, "trials": len(result["trials"]),
                      "dataset_sha256": result["spec"]["dataset_sha256"],
                      "experiment_sha256": result["spec"]["experiment_sha256"],
                      "criterion": result["criterion"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
