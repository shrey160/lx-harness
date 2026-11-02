"""Skills, helper scripts and the script sandbox (offline; sandbox tests need the WSL controller)."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from harness2.substrate.opencode import OpencodeSubstrate

import fast_sandbox
from lx import sandbox
from lx.agent import PhotoSetTools, photo_tools
from lx.budget import BudgetLedger
from lx.components import COMPONENT_TOOLS, ComponentTools, compose_prompt, validate_components
from lx.inventory import FakeShapeSegmentation, fake_scenes, synthetic_sample
from lx.perception import FakeTranscription, PerceptionCache, PerceptionClient

SURFACE = OpencodeSubstrate().surface()
SKILL = "---\nname: reconcile\ndescription: per-photo counts must add up to the answer\n---\nAdd every photo.\n"
SCRIPT = '"""Count boxes per photo and concept above a confidence."""\nimport json, sys\n' \
         'data = json.load(sys.stdin)\nlow = data["input"].get("min", 0)\n' \
         'print(json.dumps({p: {c: sum(b[4] >= low for b in boxes) for c, boxes in cs.items()}\n' \
         '                  for p, cs in data["segments"].items()}))\n'


def harness(**extra):
    tree = {name: "" for name in SURFACE.always_on}
    tree["systemprompt.md"] = "Count items in photos."
    tree["guardrails.md"] = "- When per-photo counts must add up, load the `reconcile` skill."
    tree["skills/reconcile/SKILL.md"] = SKILL
    tree["scripts/count_by_photo.py"] = SCRIPT
    tree.update(extra)
    return tree


needs_sandbox = unittest.skipUnless(sandbox.available(), "script sandbox needs the Linux controller")


@unittest.skipUnless(sandbox.backend() == "docker", "the stand-in replaces only Docker")
class FastSandboxStandsIn(unittest.TestCase):
    def test_it_answers_as_the_real_sandbox_does(self):
        scripts = ['import json, sys\nprint(json.dumps({"got": json.load(sys.stdin)["x"], "file": open("data/in.txt").read()}))',
                   'from PIL import Image\nprint(Image.new("RGB", (2, 3)).size)', 'import sys\nprint("partial")\nsys.exit(3)', 'print("caf\u00e9")']
        for source in scripts:
            with self.subTest(source):
                real = sandbox.run_python(source, '{"x": 1}', {"data/in.txt": b"text"}, timeout=10)
                fast = fast_sandbox.run_python(source, '{"x": 1}', {"data/in.txt": b"text"}, timeout=10)
                self.assertEqual({key: fast[key].replace("\r\n", "\n") if key == "stdout" else fast[key] for key in ("exit_code", "timed_out", "stdout")},
                                 {key: real[key] for key in ("exit_code", "timed_out", "stdout")})


@needs_sandbox
class SandboxTests(unittest.TestCase):
    def run_code(self, source, payload=None, files=None, timeout=10):
        return sandbox.run_json(source, payload or {}, files, timeout=timeout)

    def test_runs_json_in_and_out_with_files_and_pillow(self):
        photos, _, _, _ = synthetic_sample(4, 1)
        result = self.run_code("import json, sys\nfrom PIL import Image\nd = json.load(sys.stdin)\n"
                               "print(json.dumps({'x': d['x'] + 1, 'size': Image.open('/work/photos/1.png').size}))",
                               {"x": 41}, {"photos/1.png": photos[0]})
        self.assertEqual((result["exit_code"], result["timed_out"]), (0, False))
        self.assertEqual(result["output"], {"x": 42, "size": [320, 240]})

    def test_no_network_no_host_files_read_only_system(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as secret:
            secret.write("host secret")
        self.addCleanup(os.unlink, secret.name)
        source = ("import json, socket\nout = {}\ns = socket.socket(); s.settimeout(1)\n"
                  "out['net'] = s.connect_ex(('1.1.1.1', 80))\n"
                  f"try:\n    open({secret.name!r}).read(); out['host'] = 'read'\nexcept OSError:\n    out['host'] = 'blocked'\n"
                  "try:\n    open('/usr/lx-probe', 'w'); out['usr'] = 'writable'\nexcept OSError:\n    out['usr'] = 'read-only'\n"
                  "import os\nout['root'] = sorted(os.listdir('/'))\nprint(json.dumps(out))")
        out = self.run_code(source)["output"]
        self.assertNotEqual(out["net"], 0)
        self.assertEqual((out["host"], out["usr"]), ("blocked", "read-only"))
        self.assertNotIn("mnt", out["root"])
        self.assertNotIn("home", out["root"])

    def test_timeout_kills_and_errors_are_reported(self):
        slow = self.run_code("while True:\n    pass\n", timeout=2)
        self.assertTrue(slow["timed_out"])
        failing = self.run_code("raise SystemExit('bad input')")
        self.assertNotEqual(failing["exit_code"], 0)
        self.assertIn("bad input", failing["stderr"])
        text = self.run_code("print('not json')")
        self.assertEqual(text["output"], "not json\n")

    def test_bad_inputs_and_cleanup(self):
        before = set(Path(tempfile.gettempdir()).glob("lx-sandbox-*"))
        for source, files in (("", None), ("print(1)", {"../escape": b"x"}), ("x" * 20_001, None)):
            with self.subTest(source=source[:10], files=files):
                with self.assertRaises(ValueError):
                    self.run_code(source, files=files)
        self.run_code("print(1)")
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("lx-sandbox-*")), before)


class ValidationTests(unittest.TestCase):
    def test_skill_and_script_are_accepted(self):
        self.assertEqual(validate_components(SURFACE, harness()), {"skills": ["reconcile"], "scripts": ["count_by_photo"]})

    def test_disabled_and_broken_components_are_rejected(self):
        cases = {
            "plugin": {"plugin/guard.ts": "export {}"},
            "sub-agent": {"sub_agents/counter/AGENT.md": "---\nmode: subagent\ndescription: x\n---\n"},
            "skill script": {"skills/reconcile/scripts/x.py": "print(1)"},
            "unknown file": {"notes.txt": "x"},
            "syntax error": {"scripts/broken.py": "def ("},
            "no description": {"skills/reconcile/SKILL.md": "---\nname: reconcile\n---\nbody"},
            "untriggered": {"guardrails.md": ""},
            "too long": {"scripts/big.py": "#" * 20_001},
        }
        for name, extra in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ValueError):
                    validate_components(SURFACE, harness(**extra))

    def test_prompt_lists_skills_and_scripts(self):
        prompt = compose_prompt(SURFACE, harness())
        self.assertIn("systemprompt.md\nCount items in photos.", prompt)
        self.assertIn("AVAILABLE SKILLS (read with load_skill):\n- reconcile: per-photo counts must add up", prompt)
        self.assertIn("HELPER SCRIPTS (run with run_script):\n- count_by_photo: Count boxes per photo", prompt)
        self.assertNotIn("AVAILABLE SKILLS", compose_prompt(SURFACE, {"systemprompt.md": "x"}))


class ComponentToolTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.photos, _, _, layouts = synthetic_sample(9, 1)
        self.layouts = layouts
        perception = PerceptionClient(FakeShapeSegmentation(fake_scenes(self.photos, layouts), miss=0, false_positive=1),
                                      FakeTranscription({}), BudgetLedger(root / "b.sqlite3", initial_spend_usd=0),
                                      PerceptionCache(root / "cache"))
        self.base = PhotoSetTools(perception, photos=self.photos)
        self.calls = []

    def fake_runner(self, source, payload, files, timeout):
        self.calls.append((source, payload, sorted(files), timeout))
        return {"exit_code": 0, "timed_out": False, "stderr": "", "output": {"ok": True}}

    def test_tool_list_and_skill_loading(self):
        self.assertEqual([t["function"]["name"] for t in COMPONENT_TOOLS], ["load_skill", "run_script"])
        self.assertEqual(len(photo_tools([]) + COMPONENT_TOOLS), 3)
        tools = ComponentTools(self.base, harness(), runner=self.fake_runner)
        self.assertEqual(tools("load_skill", {"name": "reconcile"})["content"], SKILL)
        for arguments in ({"name": "missing"}, {"name": "reconcile", "x": 1}):
            with self.assertRaises(ValueError):
                tools("load_skill", arguments)

    def test_run_script_payload_and_delegation(self):
        tools = ComponentTools(self.base, harness(), runner=self.fake_runner)
        shape = self.layouts[0][0][0]
        tools("segment", {"photo": 1, "concept": shape})
        result = tools("run_script", {"name": "count_by_photo", "input": {"min": 0.5}})
        self.assertEqual(result, {"script": "count_by_photo", "exit_code": 0, "output": {"ok": True}})
        source, payload, files, timeout = self.calls[0]
        self.assertEqual(source, SCRIPT)
        self.assertEqual(payload["input"], {"min": 0.5})
        self.assertEqual(payload["photos"], [f"photos/{i}.png" for i in range(1, len(self.photos) + 1)])
        self.assertEqual(list(payload["segments"]), ["1"])
        self.assertEqual(files, sorted(payload["photos"]))
        for arguments in ({"name": "nope", "input": {}}, {"name": "count_by_photo", "input": [1]},
                          {"name": "count_by_photo"}):
            with self.assertRaises(ValueError):
                tools("run_script", arguments)

    @needs_sandbox
    def test_real_script_counts_segment_boxes(self):
        tools = ComponentTools(self.base, harness())
        shape = self.layouts[0][0][0]
        tools("segment", {"photo": 1, "concept": shape})
        result = tools("run_script", {"name": "count_by_photo.py", "input": {"min": 0.5}})
        expected = sum(1 for s, _, _ in self.layouts[0] if s == shape)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["output"], {"1": {shape: expected}})


if __name__ == "__main__":
    unittest.main()
