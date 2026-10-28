"""Images/transcripts adapter and controlled preview, using the real core method.

The preview is an explicit deterministic provider double. It does not establish
live multimodal capability and never loads credentials or opens a network.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath

from .domain import build_demo_dataset, verify_output
from .proposer import SparkProposer
from .spike import wire_reply

BASE_PROMPT = "LX_BASE\nDescribe the colored objects and their boxes. Return JSON with objects and text."
IMPROVED_PROMPT = (
    "Return only JSON with objects:[{label,box:[x1,y1,x2,y2]}] and text. "
    "The image is 96 by 64 pixels. Boxes use pixel edges with exclusive right/bottom edges. "
    "Use lowercase color_shape labels such as red_square. Preserve the supplied transcript "
    "exactly as text. Include every colored object exactly once. No extra fields or prose."
)


def sample_path(root, relative):
    if not isinstance(relative, str) or "\\" in relative or ":" in relative:
        raise ValueError("Invalid sample image path")
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(p.startswith(".") for p in parts.parts):
        raise ValueError("Sample image must stay inside its snapshot")
    path = Path(root)
    for part in parts.parts:
        path = path/part
        if path.is_symlink():
            raise ValueError("Linked sample image rejected")
    if path.suffix.lower() != ".png" or not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("This demo requires a local PNG")
    return path


def samples_from(snapshot):
    manifest = Path(snapshot)/"manifest.json"
    if not manifest.is_file():
        matches = list(Path(snapshot).glob("*/manifest.json"))
        if len(matches) != 1:
            raise ValueError("Use the demo dataset or its exported manifest and PNGs")
        manifest = matches[0]
    if manifest.is_symlink():
        raise ValueError("Linked manifest rejected")
    samples = json.loads(manifest.read_text())["samples"]
    if not isinstance(samples, list) or not samples or len(samples) > 20:
        raise ValueError("Invalid sample manifest")
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {"sample_id", "source_scene_id", "split", "image", "transcript"}:
            raise ValueError("Invalid sample manifest fields")
        if not all(isinstance(v, str) and len(v) < 10_000 for v in sample.values()):
            raise ValueError("Invalid sample manifest value")
        sample_path(manifest.parent, sample["image"])
    if len({s["sample_id"] for s in samples}) != len(samples):
        raise ValueError("Duplicate sample identity")
    return manifest.parent, samples


class PreviewTransport:
    def __init__(self, truth):
        self.truth = truth

    def __call__(self, body, timeout):
        messages = body["messages"]
        developer = messages[0]["content"]
        if developer.startswith("LX_WORKER"):
            sample_id = json.loads(messages[-1]["content"][0]["text"])["sample_id"]
            answer = copy.deepcopy(self.truth[sample_id])
            if "LX_BASE" in developer:
                answer["objects"][0]["box"][0] += 2
            return wire_reply(json.dumps(answer))
        if developer.startswith("LX_DISCUSSION"):
            return wire_reply("I can structure each image into colored objects and pixel boxes, and preserve its supplied transcript. "
                "For this first demo, use the four constructed scenes. Confirm that exact transcript text and one-pixel box tolerance match your goal; edit the draft below before approving.")
        if developer.startswith("LX_PREFLIGHT"):
            return wire_reply("PREFLIGHT_OK")
        names = messages[1]["content"].split("Available files:\n", 1)[-1].splitlines()
        if "adoption.json" in names:
            return wire_reply("<experience>\ncue: pixel localization\nlesson: Specify exclusive pixel edges and exact transcript.\n"
                              "evidence: Controlled preview outputs followed the contract.\nconfidence: low\n</experience>")
        tools = [m for m in messages if m["role"] == "tool"]
        if not tools:
            return wire_reply(calls=[{"id": "preview-edit", "type": "function", "function": {
                "name": "write_file", "arguments": json.dumps({"path": "harness/systemprompt.md", "content": IMPROVED_PROMPT})}}])
        return wire_reply("<notes>Specify pixel edges, exact text and strict JSON.</notes>")


class ImagesAdapter:
    def __init__(self, snapshot, output, client, *, mode="controlled_preview"):
        self.mode = mode
        from harness2.core.spec import BenchSpec, BenchPrompts, ChannelSpec
        from harness2.substrate.opencode import OpencodeSubstrate
        from harness2.core import tree
        self.input_root, self.samples = samples_from(snapshot)
        self.by_id = {s["sample_id"]: s for s in self.samples}
        self.root, self.client = Path(output), client
        self.substrate = OpencodeSubstrate()
        self.spec = BenchSpec(name="lx_images_transcripts", runs_root=self.root/"runs", install_mode="workspace_config",
            prompts=BenchPrompts(work="describe colored shapes in images with their pixel boxes and supplied transcript",
                domain_word="visual annotation", yardstick="Return every object and preserve the supplied transcript.",
                deliverable_word="structured JSON", selection_checks="Check pixel-edge conventions and exact transcript text."),
            channels={"proposer": ChannelSpec("opencode", "muse-spark-1.3-contributor", timeout=180),
                      "selector": ChannelSpec("opencode", "muse-spark-1.3-contributor", timeout=180)})
        base = {name: "" for name in self.substrate.surface().always_on}
        base["systemprompt.md"] = BASE_PROMPT
        if not self.base_harness().exists():
            tree.write_tree(self.substrate.surface(), base, self.base_harness(), stubs=False)
        for sample in self.samples:
            source = self.task_files(sample["sample_id"])
            source.mkdir(parents=True, exist_ok=True)
            # Only textual metadata is staged for the improver. Vision input stays in
            # the controller-side worker request; verifier truth is never staged.
            (source/"sample.json").write_text(json.dumps({k:v for k,v in sample.items() if k != "image"}))
        self.executions = 0

    def tasks(self, split="all"):
        return [s["sample_id"] for s in self.samples if split == "all" or s["split"] == split]

    def domain_of(self, task): return "visual-annotation"
    def task_prompt(self, task): return "Describe every colored shape with a color_shape label and pixel box; preserve the supplied transcript exactly."
    def task_files(self, task): return self.root/"sources"/task
    def base_harness(self): return self.root/"base"

    def validate_tree(self, value):
        if not value.get("systemprompt.md", "").strip():
            raise ValueError("Keep a nonempty systemprompt.md")
        allowed = set(self.substrate.surface().always_on)
        if any(body.strip() and name not in allowed for name, body in value.items()):
            raise ValueError("This slice edits instructions only; use the existing always-on Markdown files")

    def _identity(self, task, harness):
        sample = self.by_id[task]
        prompt = ((harness or self.base_harness())/"systemprompt.md").read_text()
        identity = {"sample": sample, "prompt": prompt, "mode": self.mode, "image_sha256": hashlib.sha256(sample_path(self.input_root, sample["image"]).read_bytes()).hexdigest()}
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def execute(self, task, harness_dir, run_id, *, timeout=None):
        run = self.spec.runs_root/"executions"/run_id
        identity = self._identity(task, harness_dir)
        if self.artifacts_intact(run):
            record = json.loads((run/"execution.json").read_text())
            if record["identity"] == identity:
                return run
        run.mkdir(parents=True, exist_ok=True)
        sample = self.by_id[task]
        prompt = ((harness_dir or self.base_harness())/"systemprompt.md").read_text()
        encoded = base64.b64encode(sample_path(self.input_root, sample["image"]).read_bytes()).decode()
        message, usage = self.client.chat([
            {"role": "developer", "content": "LX_WORKER\n"+prompt},
            {"role": "user", "content": [{"type": "text", "text": json.dumps({"sample_id": task, "transcript": sample["transcript"], "image_size": [96,64]})},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,"+encoded}}]}],
            role="worker", phase="execute", candidate=run_id, timeout=timeout or 90)
        content = message["content"].strip()
        if content.startswith("```"):
            content = "\n".join(content.splitlines()[1:-1])
        answer = json.loads(content)
        (run/"answer.json").write_text(json.dumps(answer, allow_nan=False))
        (run/"execution.json").write_text(json.dumps({"task": task, "identity": identity,
            "harness": str(harness_dir) if harness_dir else None, "answer_sha256": hashlib.sha256((run/"answer.json").read_bytes()).hexdigest(),
            "usage": {k:float(v) if k == "cost_usd" else v for k,v in usage.items()}}))
        self.executions += 1
        return run

    def artifacts_intact(self, run):
        try:
            record = json.loads((run/"execution.json").read_text())
            harness = Path(record["harness"]) if record["harness"] else None
            return (self._identity(record["task"], harness) == record["identity"] and
                    hashlib.sha256((run/"answer.json").read_bytes()).hexdigest() == record["answer_sha256"])
        except (OSError, KeyError, ValueError):
            return False

    def delivered(self, run): return (run/"answer.json").is_file()
    def deliverable_text(self, run): return (run/"answer.json").read_text()
    def deliverable_files(self, run): return {"answer.json": self.deliverable_text(run)}
    def trajectory(self, run, *, obs_trunc): return "[ACTION] Image and transcript request\n[OBSERVATION]\n"+self.deliverable_text(run)
    def score(self, run, task): return None


def optimize_preview(snapshot, output, client, truth, spec, *, live=False):
    from harness2.core.schedule import Namespace, run_domain
    from harness2.core import tree
    adapter = ImagesAdapter(snapshot, output, client, mode="live" if live else "controlled_preview")
    proposer = SparkProposer(client, Path(output)/"proposer_audit")
    reflector = SparkProposer(client, Path(output)/"reflector_audit")
    dev = adapter.tasks("dev")
    if not dev or not adapter.tasks("heldout"):
        raise ValueError("The demo needs separate dev and held-out source scenes")
    result = run_domain("visual-annotation", dev, 1, adapter=adapter, prop=proposer,
        runs_root=adapter.spec.runs_root, library_root=Path(output)/"library", modes=("seq",), reflector_prop=reflector)
    if result["done"] != len(dev):
        raise ValueError("Improvement run did not complete; last working harness is retained")
    selected = []
    for task in dev:
        summary = json.loads((Namespace(adapter.spec.name, adapter.substrate.name, "agg", 1).task_dir(adapter.spec.runs_root,task)/"summary.json").read_text())
        selected.append(summary["selections"]["seq"])
    final_dir = Namespace(adapter.spec.name, adapter.substrate.name, "seq", 1).task_dir(adapter.spec.runs_root,dev[-1])/"harnesses"/selected[-1]["chosen_label"]
    package = Path(output)/"package"
    package.mkdir(exist_ok=True)
    tree.write_tree(adapter.substrate.surface(), tree.read_tree(adapter.substrate.surface(),final_dir), package/"harness", stubs=False)
    checks = {"base": {}, "selected_dev": {}, "heldout": {}}
    for task in dev:
        base = adapter.execute(task, None, "independent/base/"+task)
        candidate = adapter.execute(task, package/"harness", "independent/final/"+task)
        checks["base"][task] = verify_output(truth[task], json.loads(adapter.deliverable_text(base))).accepted
        checks["selected_dev"][task] = verify_output(truth[task], json.loads(adapter.deliverable_text(candidate))).accepted
    improved = sum(checks["selected_dev"].values()) > sum(checks["base"].values())
    if not improved:
        tree.write_tree(adapter.substrate.surface(), tree.read_tree(adapter.substrate.surface(),adapter.base_harness()), package/"harness", stubs=False)
    for task in adapter.tasks("heldout"):
        run = adapter.execute(task, package/"harness", "heldout/"+task)
        checks["heldout"][task] = verify_output(truth[task], json.loads(adapter.deliverable_text(run))).accepted
    report = {"mode": "live" if live else "controlled_preview", "live": live, "simulated_usage": not live, "improved": improved,
        "checks": checks, "retained": "candidate" if improved else "base", "limitations": ([
            "Four synthetic scenes only; live Spark result for this bounded demo.",
            "Computed list-price cost from reported usage; not provider billing."] if live else [
            "Deterministic provider double; these gains do not establish model capability.",
            "Four synthetic scenes only; live application calls await approval."]), "spec": spec,
        "executions_this_invocation": adapter.executions}
    (package/"result.json").write_text(json.dumps(report, indent=2))
    (package/"spec.json").write_text(json.dumps(spec, indent=2))
    return report
