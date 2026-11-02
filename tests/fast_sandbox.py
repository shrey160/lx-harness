"""A stand-in for Docker in whole-flow tests, which start it dozens of times at about 1.3 s each.

It has no isolation, so it only ever runs this suite's own fixture scripts, and it lives here so that
nothing in the product can reach it. It replaces only Docker: Linux keeps its real sandbox in every test.
Tests about the sandbox itself, and anything in a launched worker process, still use the real one.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from lx import sandbox


def run_python(source, stdin_text, files=None, *, timeout=sandbox.TIMEOUT, max_stdout=sandbox.MAX_STDOUT):
    with tempfile.TemporaryDirectory(prefix="lx-fast-sandbox-") as folder:
        work = Path(folder)
        (work/"script.py").write_text(source, encoding="utf-8")
        for name, data in (files or {}).items():
            target = work/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
        try:
            done = subprocess.run([sys.executable, "-s", "-B", str(work/"script.py")], input=stdin_text.encode("utf-8"), capture_output=True,
                                  cwd=work, env={**environment, "PYTHONUTF8": "1"}, timeout=timeout)
        except subprocess.TimeoutExpired as expired:
            return {"exit_code": None, "timed_out": True, "stdout": (expired.stdout or b"")[:max_stdout].decode("utf-8", "replace"),
                    "stderr": (expired.stderr or b"")[-sandbox.MAX_STDERR:].decode("utf-8", "replace")}
        return {"exit_code": done.returncode, "timed_out": False, "stdout": done.stdout[:max_stdout].decode("utf-8", "replace"),
                "stderr": done.stderr[-sandbox.MAX_STDERR:].decode("utf-8", "replace")}
