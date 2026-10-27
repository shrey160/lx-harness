"""Save offline test evidence without machine paths or environment dumps."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, "-I", "-m", "unittest", "discover", "-s", "tests", "-v"],
                            cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    # Successful test output contains case names and assertions only. On failure,
    # retain the log locally for diagnosis; do not create a passing report.
    (evidence/"product-tests.log").write_text(result.stdout, encoding="utf-8")
    report = {"mode": "offline_controlled_responses", "state": "passed" if result.returncode == 0 else "failed",
              "python": sys.version.split()[0], "live_calls": 0, "live_spend_usd": 0,
              "packages": {name: importlib.metadata.version(name) for name in ("lx-harness", "h2portable", "harness2-cli")},
              "log": "product-tests.log"}
    (evidence/"validation.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(result.stdout)
    print(json.dumps(report, indent=2))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
