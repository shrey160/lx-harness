"""Every page of the UI opens on a real state. Needs Node and the web packages, so it is skipped where they are missing."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from lx.inventory import write_synthetic
from lx.orchestration import Orchestration
from test_tasks import Base

WEB = Path(__file__).resolve().parents[1]/"web"
TRIAL = {"kind": "trial", "task": "lx_x", "live": True, "sample": "sample-01", "score": 0.5, "base_sha256": "0"*64, "note": "A smoke test.", "stopped": None,
         "checks": [{"name": "a", "passed": True}, {"name": "b", "passed": False}], "tool_calls": {"view": 1}}
FULL = {"task": "lx_x", "means": {"base": 0.0, "harness2": 0.7}, "checks": {"base": {"s": False}, "harness2": {"s": False}}, "stream_order": ["s"], "base_sha256": "0"*64}


@unittest.skipUnless(shutil.which("node") and (WEB/"node_modules"/"esbuild").is_dir(), "needs Node and the web packages")
class Pages(Base):
    def render(self, state):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/"state.json"
            target.write_text(json.dumps(state), encoding="utf-8")
            return subprocess.run([shutil.which("node"), "smoke.mjs", str(target)], cwd=WEB, capture_output=True, encoding="utf-8", timeout=120)

    def test_every_page_opens_for_a_task_with_runs(self):
        path = self.folder()
        write_synthetic(Path(path)/"samples", 6)
        lx = Orchestration(self.app, launch=False)
        lx.init(path, "photo-request")
        lx.preview(path)
        state = self.app.state()
        run = state["jobs"][0]
        state["jobs"] += [{**run, "id": "trial", "kind": "trial", "status": "completed", "result": TRIAL}, {**run, "id": "full", "status": "completed", "result": FULL}]
        done = self.render(state)
        self.assertEqual(done.returncode, 0, done.stdout+done.stderr)
        self.assertIn("pages rendered", done.stdout)

    def test_a_page_that_throws_is_reported(self):
        path = self.folder()
        write_synthetic(Path(path)/"samples", 6)
        Orchestration(self.app, launch=False).init(path, "photo-request")
        state = self.app.state()
        del state["projects"][0]["draft"]["roles"]
        self.assertNotEqual(self.render(state).returncode, 0)


if __name__ == "__main__":
    unittest.main()
