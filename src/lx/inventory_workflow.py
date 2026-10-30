"""Inventory domain: the agent answers spoken requests about 3-7 photos with model tools.

Split mapping: dataset `stream` is the adapter's `dev` (the E-A stream the
loop adapts on, sample by sample), `heldout` stays `heldout`, `calibration`
stays `calibration`. The harness may hold skills and helper scripts
(`lx.components`); scripts run only in `lx.sandbox`.

E-A (paper-faithful, D-010): every stream sample starts from the base
harness; the core runs Propose-Probe-Compose on that same sample with a
library carried forward; the sample's score is the checklist score of the
selected run, compared with the base (reference) run on the same sample.
The improver never sees scores; grading happens here, after the run.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from .agent import PhotoSetTools, photo_tools, render_trace, run_agent, tool_identity
from .components import COMPONENT_TOOLS, LOAD_SKILL, ComponentTools, compose_prompt, scripts, validate_components
from .inventory import (COLOURS, CONTRACT, SHAPES, WORDING, caps, canonical_colour, singular, verify_inventory)
from .perception import FakeTranscription, media_hash
from .spike import wire_reply
from .workflow import ImagesAdapter

BASE_PROMPT = "Answer the spoken request about the photos and return JSON."
ADAPTER_SPLITS = {"stream": "dev", "check": "check", "heldout": "heldout", "calibration": "calibration"}
WORKER_MARKER = "LX_INVENTORY_AGENT"
COMPONENT_NOTE = (
    "The harness may also contain skills/<name>/SKILL.md (frontmatter name and description; the agent "
    "reads one with load_skill) and scripts/<name>.py (Python 3 with Pillow; reads one JSON object on "
    "stdin with input, photos and segments, prints JSON; the agent runs it with run_script in a "
    "sandbox without network). Mention each skill by name in guardrails.md. Sub-agents and plugins "
    "are not available.")
# A controller with no script sandbox (native Windows without Docker, D-016) turns scripts off.
SKILLS_ONLY_NOTE = (
    "The harness may also contain skills/<name>/SKILL.md (frontmatter name and description; the agent "
    "reads one with load_skill). Mention each skill by name in guardrails.md. Helper scripts are turned "
    "off on this controller (no script sandbox), so put procedures in skills and instructions. "
    "Sub-agents and plugins are not available.")
SCRIPTS_OFF = ("helper scripts are turned off on this controller (no script sandbox); keep procedures in "
               "skills/<name>/SKILL.md and the always-on files")


def media_path(root, relative, suffix):
    parts = PurePosixPath(relative) if isinstance(relative, str) else None
    if (parts is None or "\\" in relative or ":" in relative or parts.is_absolute()
            or any(p.startswith(".") for p in parts.parts) or parts.suffix != suffix):
        raise ValueError("Invalid inventory media path")
    path = Path(root).joinpath(*parts.parts)
    if path.is_symlink() or not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("Inventory media must stay inside its snapshot")
    return path


class InventoryDomain:
    name = "lx_inventory"
    base_prompt = BASE_PROMPT
    task_prompt = CONTRACT
    verify = staticmethod(verify_inventory)

    def __init__(self, scripts_enabled=True):
        self.scripts_enabled = scripts_enabled

    def prompts(self):
        from harness2.core.spec import BenchPrompts
        return BenchPrompts(
            work="answer spoken inventory requests about several photos, calling the available model tools. "
                 + (COMPONENT_NOTE if self.scripts_enabled else SKILLS_ONLY_NOTE),
            domain_word="spoken photo inventory", yardstick=CONTRACT, deliverable_word="inventory JSON",
            selection_checks="Check that the request was heard, every photo was examined, the right kinds and "
                             "colours were counted per photo, tool results were filtered sensibly, and the "
                             "answer equals the per-photo totals.")

    @staticmethod
    def samples_from(snapshot):
        root = Path(snapshot)
        manifest = root/"manifest.json"
        if root.is_symlink() or manifest.is_symlink():
            raise ValueError("Linked inventory inputs rejected")
        samples = json.loads(manifest.read_text(encoding="utf-8"))["samples"]
        if type(samples) is not list or not 1 <= len(samples) <= 2000:
            raise ValueError("Expected 1..2000 inventory samples")
        ids, mapped = set(), []
        for sample in samples:
            if type(sample) is not dict or set(sample) != {"sample_id", "split", "photos", "audio"}:
                raise ValueError("Invalid inventory manifest fields")
            if sample["split"] not in ADAPTER_SPLITS or type(sample["photos"]) is not list:
                raise ValueError("Invalid inventory manifest values")
            if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(sample["sample_id"])) is None or sample["sample_id"] in ids:
                raise ValueError("Invalid or duplicate inventory identity")
            if not 3 <= len(sample["photos"]) <= 7:
                raise ValueError("Each inventory sample needs 3-7 photos")
            for photo in sample["photos"]:
                media_path(root, photo, ".jpg")
            media_path(root, sample["audio"], ".wav")
            ids.add(sample["sample_id"])
            mapped.append(dict(sample, split=ADAPTER_SPLITS[sample["split"]]))
        return root, mapped

    def messages(self, sample, prompt, sizes):
        text = json.dumps({"sample_id": sample["sample_id"],
                           "photos": [{"photo": k, "width": w, "height": h} for k, (w, h) in enumerate(sizes, 1)],
                           "photos_note": "not attached; use view to look and segment to find instances",
                           "spoken_request": "audio only; use transcribe"})
        return [{"role": "developer", "content": WORKER_MARKER+"\n"+prompt+"\n\nTask contract:\n"+self.task_prompt},
                {"role": "user", "content": text}]


def _size(data):
    from io import BytesIO

    from PIL import Image
    with Image.open(BytesIO(data)) as image:
        return image.size


class InventoryAdapter(ImagesAdapter):
    """Agent executions over photo sets; identity covers agent, tools, caps, harness and media."""

    def __init__(self, snapshot, output, client, truth, *, perception, catalog, mode="offline_preview",
                 script_runner=None):
        from . import sandbox
        self.scripts_enabled = script_runner is not None or sandbox.available()
        super().__init__(snapshot, output, client, mode=mode, domain=InventoryDomain(self.scripts_enabled),
                         truth=truth)
        self.perception = perception
        self.tools = photo_tools(catalog) + (COMPONENT_TOOLS if self.scripts_enabled else [LOAD_SKILL])
        self.script_runner = script_runner

    def validate_tree(self, value):
        if not value.get("systemprompt.md", "").strip():
            raise ValueError("Keep a nonempty systemprompt.md")
        if not self.scripts_enabled and scripts(value):
            raise ValueError(SCRIPTS_OFF)
        validate_components(self.substrate.surface(), value)

    def _photos(self, task):
        return [media_path(self.input_root, p, ".jpg").read_bytes() for p in self.by_id[task]["photos"]]

    def _audio(self, task):
        return media_path(self.input_root, self.by_id[task]["audio"], ".wav").read_bytes()

    def _identity(self, task, harness):
        profile = self.client.profile
        extra = {"base": super()._identity(task, harness), "worker_protocol": "lx-inventory-agent-v1",
                 "agent": {"model": profile.model, "endpoint": profile.endpoint, "images": profile.accepts_images,
                           "tools": profile.supports_tools},
                 "tool_sha256": tool_identity(self.tools), "caps": caps(len(self.by_id[task]["photos"])),
                 "photos_sha256": [media_hash(p) for p in self._photos(task)],
                 "audio_sha256": media_hash(self._audio(task))}
        return hashlib.sha256(json.dumps(extra, sort_keys=True).encode()).hexdigest()

    def execute(self, task, harness_dir, run_id, *, timeout=None):
        run = self.spec.runs_root/"executions"/run_id
        identity = self._identity(task, harness_dir)
        if self.artifacts_intact(run):
            if json.loads((run/"execution.json").read_text())["identity"] == identity:
                return run
        run.mkdir(parents=True, exist_ok=True)
        from harness2.core import tree
        surface = self.substrate.surface()
        harness = tree.read_tree(surface, harness_dir or self.base_harness())
        photos = self._photos(task)
        messages = self.domain.messages(self.by_id[task], compose_prompt(surface, harness), [_size(p) for p in photos])
        handler = ComponentTools(PhotoSetTools(self.perception, photos=photos, audio=self._audio(task), candidate=run_id),
                                 harness, runner=self.script_runner)
        text, trace = run_agent(self.client, messages, self.tools, handler, candidate=run_id,
                                timeout=timeout or 600, **caps(len(photos)))
        content = text
        if content.startswith("```"):
            content = "\n".join(content.splitlines()[1:-1])
        try:
            def reject_constant(value):
                raise ValueError("Nonfinite JSON number")
            answer = json.loads(content, parse_constant=reject_constant)
        except ValueError:
            answer = content
        (run/"answer.json").write_text(json.dumps(answer, allow_nan=False))
        (run/"trace.json").write_text(json.dumps({"final_text": text, "trace": trace}, allow_nan=False))
        (run/"execution.json").write_text(json.dumps({"task": task, "identity": identity,
            "harness": str(harness_dir) if harness_dir else None,
            "answer_sha256": hashlib.sha256((run/"answer.json").read_bytes()).hexdigest(),
            "tool_calls": sum(step["type"] == "tool" for step in trace)}))
        self.executions += 1
        return run

    def trajectory(self, run, *, obs_trunc):
        record = json.loads((run/"trace.json").read_text())
        return render_trace(record["trace"], record["final_text"], obs_trunc=obs_trunc)


def run_stream(adapter, output, client, *, tasks=None):
    """E-A: Harness² k=1 (sequential mode) over the stream, then controller-side grading per sample."""
    from harness2.core import tree
    from harness2.core.schedule import Namespace, run_domain

    from .proposer import SparkProposer

    output = Path(output)
    stream = list(tasks) if tasks is not None else adapter.tasks("dev")
    if not stream:
        raise ValueError("The stream needs at least one sample")
    proposer = SparkProposer(client, output/"proposer_audit")
    reflector = SparkProposer(client, output/"reflector_audit")
    result = run_domain(adapter.domain_of(stream[0]), stream, 1, adapter=adapter, prop=proposer,
                        runs_root=adapter.spec.runs_root, library_root=output/"library", modes=("seq",),
                        reflector_prop=reflector)
    surface = adapter.substrate.surface()
    base_tree = tree.read_tree(surface, adapter.base_harness())
    rows = {}
    for task in stream:
        summary = json.loads((Namespace(adapter.spec.name, adapter.substrate.name, "agg", 1)
                              .task_dir(adapter.spec.runs_root, task)/"summary.json").read_text())
        selection = summary["selections"].get("seq", {})
        base_run = adapter.spec.runs_root/summary["base"][0]["run_dir"]
        chosen_dir = Namespace(adapter.spec.name, adapter.substrate.name, "seq", 1).task_dir(
            adapter.spec.runs_root, task)/"harnesses"/selection.get("chosen_label", "")
        chosen_run = adapter.spec.runs_root/selection["chosen_run_dir"] if selection.get("chosen_run_dir") else base_run
        def graded(run):
            answer = json.loads(adapter.deliverable_text(run))
            result_ = adapter.verify(task, answer)
            return {"score": result_.score, "passed": result_.n_passed, "checks": result_.n_criteria}
        chosen_tree = tree.read_tree(surface, chosen_dir) if selection.get("chosen_label") and chosen_dir.is_dir() else base_tree
        rows[task] = {"base": graded(base_run), "harness2": graded(chosen_run),
                      "chosen_label": selection.get("chosen_label"), "chosen_is_base": selection.get("chosen_tree_is_base"),
                      "files_added": sorted(set(chosen_tree) - {k for k, v in base_tree.items()}),
                      "status": summary.get("status")}
    mean = lambda key: sum(r[key]["score"] for r in rows.values())/len(rows)
    report = {"experiment": "E-A", "k": 1, "mode": "seq", "stream": stream, "run": result,
              "per_sample": rows, "means": {"base": mean("base"), "harness2": mean("harness2")},
              "grading": "controller-side checklist after the run; never shown to the improver",
              "executions_this_invocation": adapter.executions}
    (output/"ea_report.json").write_text(json.dumps(report, indent=2))
    return report


# --- offline preview doubles (plumbing only; not capability evidence) --------

SKILL_NAME = "inventory-check"
PREVIEW_SKILL = (f"---\nname: {SKILL_NAME}\ndescription: every photo must be checked and low-confidence boxes "
                 "dropped before counting\n---\n1. Transcribe the request.\n2. Segment the asked kind on every "
                 "photo.\n3. Run the sum_counts script with min_confidence 0.5.\n4. Answer with its per-photo "
                 "items and total.\n")
PREVIEW_SCRIPT = '''"""Sum confident boxes per photo for one concept and return the inventory JSON parts."""
import json
import sys

data = json.load(sys.stdin)
concept = data["input"]["concept"]
low = data["input"].get("min_confidence", 0.5)
photos = []
for number in range(1, len(data["photos"]) + 1):
    boxes = data["segments"].get(str(number), {}).get(concept, [])
    count = sum(1 for box in boxes if box[4] >= low)
    photos.append({"photo": number, "items": [{"kind": data["input"]["kind"], "colour": data["input"].get("colour"),
                                                "count": count}] if count else []})
print(json.dumps({"photos": photos, "answer": sum(p["items"][0]["count"] for p in photos if p["items"])}))
'''
PREVIEW_GUARDRAIL = f"- When counting across photos, load the `{SKILL_NAME}` skill."


def _request_pattern():
    # Colour wordings first, with a closed colour list, so "green pens" is never read as a kind.
    colours = "|".join(COLOURS)
    out = []
    for kind in ("colour_count", "count", "inventory"):
        for template in WORDING[kind]:
            pattern = (re.escape(template).replace(r"\{kinds\}", r"(?P<kinds>[a-z ]+?)")
                       .replace(r"\{colour\}", rf"(?P<colour>{colours})"))
            out.append((kind, re.compile(pattern)))
    return out


_REQUESTS = _request_pattern()


def parse_request(text):
    """(type, kind, colour) from a request in the known wording banks (preview double only)."""
    for kind, pattern in _REQUESTS:
        match = pattern.fullmatch(text.strip())
        if match:
            groups = match.groupdict()
            return kind, singular(groups["kinds"]) if groups.get("kinds") else None, canonical_colour(groups.get("colour"))
    return None, None, None


class InventoryPreviewTransport:
    """Deterministic double for agent, improver, selector and reflector.

    Without the skill the agent counts every SAM box (false positives
    included) on all but the last photo; with the preview skill it segments
    every photo and sums confident boxes with the preview script, which runs
    in the real sandbox. The improver writes the skill, its trigger and the
    script. This proves the plumbing, not any capability.
    """

    def __init__(self, scripts=True):
        self.calls = {"worker": 0, "reflection": 0, "improver": 0}
        self.scripts = scripts

    def __call__(self, body, timeout):
        messages = body["messages"]
        if messages[0]["content"].startswith(WORKER_MARKER):
            self.calls["worker"] += 1
            return self._agent(messages)
        names = messages[1]["content"].split("Available files:\n", 1)[-1].splitlines()
        if "adoption.json" in names:
            self.calls["reflection"] += 1
            return wire_reply("<experience>\ncue: spoken photo inventory\nlesson: Check every photo and drop "
                              "low-confidence boxes; a script sums per-photo counts.\nevidence: Offline wiring "
                              "check only.\nconfidence: low\n</experience>")
        self.calls["improver"] += 1
        if not any(m["role"] == "tool" for m in messages):
            files = {f"harness/skills/{SKILL_NAME}/SKILL.md": PREVIEW_SKILL, "harness/guardrails.md": PREVIEW_GUARDRAIL}
            if self.scripts:
                files["harness/scripts/sum_counts.py"] = PREVIEW_SCRIPT
            return wire_reply(calls=[{"id": f"preview-{i}", "type": "function", "function": {
                "name": "write_file", "arguments": json.dumps({"path": path, "content": content})}}
                for i, (path, content) in enumerate(files.items())])
        return wire_reply("<notes>Added an inventory-check skill, its trigger"
                          + (" and a sum_counts script.</notes>" if self.scripts else ".</notes>"))

    @staticmethod
    def _agent(messages):
        developer = messages[0]["content"]
        has_skill = f"- {SKILL_NAME}:" in developer
        skilled = has_skill and "- sum_counts:" in developer
        n_photos = len(json.loads(messages[1]["content"])["photos"])
        tool_results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        step = len(tool_results)

        def call(name, arguments):
            return wire_reply(calls=[{"id": f"step-{step}", "type": "function",
                                      "function": {"name": name, "arguments": json.dumps(arguments)}}])
        if step == 0:
            return call("transcribe", {})
        request_type, kind, colour = parse_request(tool_results[0]["result"]["transcript"])
        if request_type is None:
            return wire_reply("{}")
        concept = f"{colour} {kind}" if colour else (kind or "shape")
        if skilled:
            if step == 1:
                return call("load_skill", {"name": SKILL_NAME})
            if step - 2 < n_photos:
                return call("segment", {"photo": step - 1, "concept": concept})
            if step - 2 == n_photos:
                return call("run_script", {"name": "sum_counts", "input": {"concept": concept, "kind": kind or "shape",
                                                                           "colour": colour, "min_confidence": 0.5}})
            output = tool_results[-1]["result"]["output"]
            return wire_reply(json.dumps({"request": {"type": request_type, "kind": kind, "colour": colour},
                                          "photos": output["photos"], "answer": output["answer"]}))
        if has_skill and step == 1:  # skill without its script: check every photo, count every box
            return call("load_skill", {"name": SKILL_NAME})
        first = 2 if has_skill else 1
        covered = n_photos if has_skill else max(1, n_photos - 1)
        if step - first < covered:
            return call("segment", {"photo": step - first + 1, "concept": concept})
        counts = [r["result"]["instances"] for r in tool_results[first:]]
        photos = [{"photo": k, "items": [{"kind": kind or "shape", "colour": colour, "count": c}] if c else []}
                  for k, c in enumerate(counts, 1)]
        return wire_reply(json.dumps({"request": {"type": request_type, "kind": kind, "colour": colour},
                                      "photos": photos, "answer": sum(counts)}))


def preview_transcription(dataset_root, requests):
    """Transcribe double from controller request texts (offline only)."""
    root = Path(dataset_root)/"worker_inputs"
    manifest = json.loads((root/"manifest.json").read_text())
    return FakeTranscription({media_hash((root/s["audio"]).read_bytes()): requests[s["sample_id"]]["text"]
                              for s in manifest["samples"]})


def preview_stream(output, *, count=3, seed=20261029):
    """Offline E-A over D3 shape samples with wire-format doubles; real core loop and sandbox."""
    from .budget import BudgetLedger
    from .usage import BudgetScope
    from .inventory import FakeShapeSegmentation
    from .inventory_data import build_synthetic_dataset, load_inventory_dataset
    from .perception import PerceptionCache, PerceptionClient
    from .spark import SparkClient
    from .trials import VOICECOUNT_CATALOG

    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("use an empty output directory")
    scenes, requests = build_synthetic_dataset(output/"dataset", count, seed=seed)
    _, truth, _ = load_inventory_dataset(output/"dataset")
    ledger = BudgetLedger(output/"simulated-usage.sqlite3", initial_spend_usd=0)
    from . import sandbox
    transport = InventoryPreviewTransport(scripts=sandbox.available())
    client = SparkClient(transport, BudgetScope(ledger, "inventory-preview", "5"))
    perception = PerceptionClient(FakeShapeSegmentation(scenes), preview_transcription(output/"dataset", requests),
                                  ledger, PerceptionCache(output/"perception-cache"))
    adapter = InventoryAdapter(output/"dataset"/"worker_inputs", output/"run", client, truth,
                               perception=perception, catalog=VOICECOUNT_CATALOG)
    report = run_stream(adapter, output/"run", client)
    report["preview"] = {"live": False, "chat_calls": transport.calls,
                         "note": "deterministic doubles; plumbing evidence only"}
    (output/"run"/"ea_report.json").write_text(json.dumps(report, indent=2))
    return report


__all__ = ["InventoryAdapter", "InventoryDomain", "InventoryPreviewTransport", "run_stream", "parse_request",
           "preview_transcription", "SHAPES"]
