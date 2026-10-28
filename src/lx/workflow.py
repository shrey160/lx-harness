"""Images/transcripts adapter and controlled preview, using the real core method.

The preview is an explicit deterministic provider double. It does not establish
live multimodal capability and never loads credentials or opens a network.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import inspect
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
    def __init__(self, snapshot, output, client, *, mode="controlled_preview", domain=None, truth=None):
        self.mode = mode
        self.domain, self.truth = domain, truth
        verifier = domain.verify if domain else verify_output
        self.verifier_identity = hashlib.sha256(inspect.getsource(inspect.getmodule(verifier)).encode()).hexdigest()
        from harness2.core.spec import BenchSpec, BenchPrompts, ChannelSpec
        from harness2.substrate.opencode import OpencodeSubstrate
        from harness2.core import tree
        self.input_root, self.samples = (domain.samples_from(snapshot) if domain else samples_from(snapshot))
        self.by_id = {s["sample_id"]: s for s in self.samples}
        self.root, self.client = Path(output), client
        self.substrate = OpencodeSubstrate()
        self.spec = BenchSpec(name="lx_images_transcripts", runs_root=self.root/"runs", install_mode="workspace_config",
            prompts=BenchPrompts(work="describe colored shapes in images with their pixel boxes and supplied transcript",
                domain_word="visual annotation", yardstick="Return every object and preserve the supplied transcript.",
                deliverable_word="structured JSON", selection_checks="Check pixel-edge conventions and exact transcript text."),
            channels={"proposer": ChannelSpec("opencode", "muse-spark-1.3-contributor", timeout=180),
                      "selector": ChannelSpec("opencode", "muse-spark-1.3-contributor", timeout=180)})
        if domain:
            from dataclasses import replace
            self.spec = replace(self.spec, name=domain.name, prompts=domain.prompts())
        base = {name: "" for name in self.substrate.surface().always_on}
        base["systemprompt.md"] = domain.base_prompt if domain else BASE_PROMPT
        if not self.base_harness().exists():
            tree.write_tree(self.substrate.surface(), base, self.base_harness(), stubs=False)
        for sample in self.samples:
            if sample["split"] != "dev":
                continue
            source = self.task_files(sample["sample_id"])
            source.mkdir(parents=True, exist_ok=True)
            # Only textual metadata is staged for the improver. Vision input stays in
            # the controller-side worker request; verifier truth is never staged.
            (source/"sample.json").write_text(json.dumps({k:v for k,v in sample.items() if k != "image"}))
        self.executions = 0

    def tasks(self, split="all"):
        return [s["sample_id"] for s in self.samples if split == "all" or s["split"] == split]

    def domain_of(self, task): return self.domain.name if self.domain else "visual-annotation"
    def task_prompt(self, task):
        return self.domain.task_prompt if self.domain else "Describe every colored shape with a color_shape label and pixel box; preserve the supplied transcript exactly."
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
        # Fingerprint the whole editable surface, not just systemprompt.md.
        from harness2.core import tree
        content = tree.read_tree(self.substrate.surface(), harness or self.base_harness())
        identity = {"sample": sample, "harness": content, "mode": self.mode, "domain": self.domain_of(task),
                    "task_contract": self.task_prompt(task),
                    "worker_protocol": "lx-instructions-v2",
                    "message_builder": inspect.getsource(self.domain.messages) if self.domain else "images-v2",
                    "verifier_sha256": self.verifier_identity,
                    "truth_sha256": hashlib.sha256(json.dumps(self.truth[task], sort_keys=True).encode()).hexdigest() if self.truth else None}
        if not self.domain:
            identity["image_sha256"] = hashlib.sha256(sample_path(self.input_root, sample["image"]).read_bytes()).hexdigest()
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
        from harness2.core import tree
        harness = tree.read_tree(self.substrate.surface(), harness_dir or self.base_harness())
        prompt = "\n\n".join(f"{name}\n{body}" for name, body in sorted(harness.items()) if body.strip())
        if self.domain:
            messages = self.domain.messages(sample, prompt)
        else:
            encoded = base64.b64encode(sample_path(self.input_root, sample["image"]).read_bytes()).decode()
            messages = [
                {"role": "developer", "content": "LX_WORKER\n"+prompt},
                {"role": "user", "content": [{"type": "text", "text": json.dumps({"sample_id": task, "transcript": sample["transcript"], "image_size": [96,64]})},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,"+encoded}}]}]
        message, usage = self.client.chat(messages,
            role="worker", phase="execute", candidate=run_id, timeout=timeout or 90)
        content = message["content"].strip()
        if content.startswith("```"):
            content = "\n".join(content.splitlines()[1:-1])
        try:
            def reject_constant(value):
                raise ValueError("Nonfinite JSON number")
            answer = json.loads(content, parse_constant=reject_constant)
        except ValueError:
            # An invalid answer is a failed item, not an omitted denominator.
            answer = content
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
    def trajectory(self, run, *, obs_trunc): return "[ACTION] Structured output request\n[OBSERVATION]\n"+self.deliverable_text(run)
    def verify(self, task, answer):
        return (self.domain.verify if self.domain else verify_output)(self.truth[task], answer)

    def score(self, run, task):
        if self.truth is None:
            raise ValueError("Controller truth is required for independent grading")
        result = self.verify(task, json.loads(self.deliverable_text(run)))
        return {"n_passed": getattr(result, "n_passed", int(result.accepted)),
                "n_criteria": getattr(result, "n_criteria", 1), "all_pass": result.accepted}


def optimize_preview(snapshot, output, client, truth, spec, *, live=False):
    adapter = ImagesAdapter(snapshot, output, client, mode="live" if live else "controlled_preview", truth=truth)
    return optimize_adapter(adapter, output, client, spec, live=live)


def optimize_adapter(adapter, output, client, spec, *, live=False, adaptation_count=None):
    """Blind core adaptation, then controller-side mean-dev retention and evaluation."""
    from harness2.core.schedule import Namespace, run_domain
    from harness2.core import tree
    proposer = SparkProposer(client, Path(output)/"proposer_audit")
    reflector = SparkProposer(client, Path(output)/"reflector_audit")
    dev = adapter.tasks("dev")
    if not dev or not adapter.tasks("heldout"):
        raise ValueError("Separate development and held-out samples are required")
    if adaptation_count is not None and (type(adaptation_count) is not int or not 1 <= adaptation_count <= len(dev)):
        raise ValueError("Invalid adaptation item count")
    practice = dev[:adaptation_count] if adaptation_count is not None else dev
    result = run_domain(adapter.domain_of(dev[0]), practice, 1, adapter=adapter, prop=proposer,
        runs_root=adapter.spec.runs_root, library_root=Path(output)/"library", modes=("seq",), reflector_prop=reflector)
    if result["done"] != len(practice):
        raise ValueError("Improvement run did not complete; last working harness is retained")
    selected = []
    for task in practice:
        summary = json.loads((Namespace(adapter.spec.name, adapter.substrate.name, "agg", 1).task_dir(adapter.spec.runs_root,task)/"summary.json").read_text())
        selected.append(summary["selections"]["seq"])
    final_dir = Namespace(adapter.spec.name, adapter.substrate.name, "seq", 1).task_dir(adapter.spec.runs_root,practice[-1])/"harnesses"/selected[-1]["chosen_label"]
    package = Path(output)/"package"
    package.mkdir(exist_ok=True)
    tree.write_tree(adapter.substrate.surface(), tree.read_tree(adapter.substrate.surface(),final_dir), package/"harness", stubs=False)
    frozen = Path(output)/"frozen-candidate"
    tree.write_tree(adapter.substrate.surface(), tree.read_tree(adapter.substrate.surface(),final_dir), frozen, stubs=False)
    checks = {"base": {}, "selected_dev": {}, "heldout": {}}
    scores = {name: {} for name in ("base_dev", "candidate_dev", "base_heldout", "candidate_heldout", "retained_heldout")}
    def evaluate(task, harness, run_id):
        run = adapter.execute(task, harness, run_id)
        checked = adapter.verify(task, json.loads(adapter.deliverable_text(run)))
        return checked, getattr(checked, "score", float(checked.accepted))
    for task in dev:
        base, scores["base_dev"][task] = evaluate(task, None, "independent/base/"+task)
        candidate, scores["candidate_dev"][task] = evaluate(task, frozen, "independent/final/"+task)
        checks["base"][task], checks["selected_dev"][task] = base.accepted, candidate.accepted
    mean = lambda values: sum(values.values())/len(values)
    improved = mean(scores["candidate_dev"]) > mean(scores["base_dev"])
    # Freeze the candidate before opening held-out evaluation. Held-out grades
    # never affect retention and never enter proposer/selector contexts.
    if not improved:
        tree.write_tree(adapter.substrate.surface(), tree.read_tree(adapter.substrate.surface(),adapter.base_harness()), package/"harness", stubs=False)
    for task in adapter.tasks("heldout"):
        base, scores["base_heldout"][task] = evaluate(task, None, "heldout/base/"+task)
        candidate, scores["candidate_heldout"][task] = evaluate(task, frozen, "heldout/candidate/"+task)
        checks["heldout"][task] = candidate.accepted if improved else base.accepted
        scores["retained_heldout"][task] = scores["candidate_heldout" if improved else "base_heldout"][task]
    report = {"mode": "live" if live else "controlled_preview", "live": live, "simulated_usage": not live, "improved": improved,
        "checks": checks, "scores": scores, "means": {name: mean(values) for name, values in scores.items()},
        "adaptation_tasks": practice, "retention_basis": "mean development score only; strict gain required", "retained": "candidate" if improved else "base", "limitations": ([
            "Synthetic task only; results do not establish general capability.",
            "Computed list-price cost from reported usage; not provider billing."] if live else [
            "Deterministic provider double; these gains do not establish model capability.",
            "Synthetic task only; preview results cannot establish live capability."]), "spec": spec,
        "executions_this_invocation": adapter.executions}
    (package/"result.json").write_text(json.dumps(report, indent=2))
    (package/"spec.json").write_text(json.dumps(spec, indent=2))
    return report
