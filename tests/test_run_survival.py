"""A run outlives its caller: an agent client ends its server's whole process tree when it closes (P6.1)."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import unittest

from lx import sandbox
from lx.inventory import write_synthetic
from lx.orchestration import Orchestration
from test_tasks import Base

CALLER = """
import sys
from lx.engine import Engine
from lx.orchestration import Orchestration
print(Orchestration(Engine(sys.argv[1])).preview(sys.argv[2])["run_id"], flush=True)
sys.stdin.read()
"""


def end_tree(caller):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(caller.pid), "/T", "/F"], capture_output=True, check=True)
    else:
        os.killpg(caller.pid, signal.SIGKILL)
    caller.wait()


class ReleasesItsFiles(Base):
    def clear_when_released(self, path):
        """The record says completed a moment before the worker exits and lets go of its log files on Windows."""
        deadline = time.time()+60
        while Path(path).exists() and time.time() < deadline:
            shutil.rmtree(path, ignore_errors=True)
            time.sleep(0.5)


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class RunSurvival(ReleasesItsFiles):
    def test_a_preview_finishes_after_its_callers_tree_is_ended(self):
        path = self.folder()
        write_synthetic(Path(path)/"samples", 6)
        Orchestration(self.app).init(path, "photo-request", goal="Count things in photos")
        caller = subprocess.Popen([sys.executable, "-c", CALLER, str(self.app.root), path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  text=True, start_new_session=os.name != "nt")
        self.addCleanup(caller.stdout.close)
        self.addCleanup(caller.stdin.close)
        run_id = caller.stdout.readline().strip()
        self.assertTrue(run_id)
        end_tree(caller)
        workspace = self.app.workspace(self.app.open_workspace(path)["id"])
        deadline = time.time()+400
        while time.time() < deadline and workspace.run(run_id)["status"] in {"queued", "running"}:
            time.sleep(0.5)
        self.assertEqual(workspace.run(run_id)["status"], "completed", workspace.run(run_id).get("error"))
        self.addCleanup(self.clear_when_released, path)


if __name__ == "__main__":
    unittest.main()
