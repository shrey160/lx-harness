"""General task spec: what a run does is data in the spec plus a check file, not code.

Two templates exist. `text-request` is in lx.text_task. `photo-request`: each sample is a folder `sample-*/` with
photos/1.jpg ... (3-7 photos), a spoken request (request.wav, or request.txt for
previews) and truth.json for the check. The spec's task holds the contract, the
base prompt and the wording the loop uses; check.py scores answers (lx.checks).
Truth stays on the controller and reaches only the sandboxed check. Inventory
is a task in this template, so its spec and check are ordinary files.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from . import text_task
from .checks import CheckError, run_check
from .inventory import CONTRACT, GRID, CELL, synthetic_sample
from .inventory_data import _tone, add_user_samples, check_sample_folder, finalize_inventory, normalize_photo
from .inventory_workflow import BASE_PROMPT, PHOTOS_NOTE, VIEW_NOTE, InventoryDomain
from .perception import media_hash
from .text_task import sample_folders

TEMPLATE = "photo-request"
TEXT_TEMPLATE = text_task.TEMPLATE
TEXT_FIELDS = ("name", "work", "domain_word", "yardstick", "deliverable_word", "selection_checks", "base_prompt", "contract")
SEGMENTATION, TRANSCRIPTION = "sam-catalog", "transcribe-catalog"
# An outside agent as orchestrator (D-024): its own model and account, so no entry, key or client here.
EXTERNAL = "external-agent"
MODEL_TOOLS = (SEGMENTATION, TRANSCRIPTION)
_TOOL_KINDS = {SEGMENTATION: "segmentation", TRANSCRIPTION: "transcription"}
MAX_TEXT = 8000
MAX_CHECK = 20_000
MAX_SAMPLE_BYTES = 200 * 1024 ** 2
PREVIEW_SEED = 20261029


def validate_task(task):
    if type(task) is not dict or task.get("template") not in {TEMPLATE, TEXT_TEMPLATE}:
        raise ValueError(f"A task needs the {TEMPLATE} or {TEXT_TEMPLATE} template")
    known = {"template", "truth_schema", "stream_samples", *TEXT_FIELDS}
    if set(task) - known or any(field not in task for field in TEXT_FIELDS):
        raise ValueError("A task needs exactly the template fields")
    if any(type(task[field]) is not str or not task[field].strip() or len(task[field]) > MAX_TEXT for field in TEXT_FIELDS):
        raise ValueError(f"Task text fields must be non-empty and at most {MAX_TEXT} characters")
    if re.fullmatch(r"[a-z][a-z0-9_]{1,60}", task["name"]) is None:
        raise ValueError("A task name must be lower-case letters, digits and underscores")
    if task.get("truth_schema", "free") not in {"free", "inventory"}:
        raise ValueError("Unknown truth schema")
    if is_text(task) and task.get("truth_schema") == "inventory":
        raise ValueError("The inventory truth schema needs photos")
    if type(task.get("stream_samples")) is not int or not 1 <= task["stream_samples"] <= 20:
        raise ValueError("The loop needs 1 to 20 samples")
    return task


def is_text(task):
    return bool(task) and task.get("template") == TEXT_TEMPLATE


def uses_tools(task):
    return bool(task) and not is_text(task)


def required_tools(task):
    """A photo task's request is audio only, so it cannot run without transcription; `view` can stand in for segmentation."""
    return (TRANSCRIPTION,) if uses_tools(task) else ()


def spec_tools(spec):
    """The model tools a run may call. A spec from before D-019 has both."""
    return tuple(spec.get("tools", MODEL_TOOLS)) if uses_tools(spec.get("task")) else ()


def check_tools(task, chosen):
    """A valid tool list for this task, in catalog order, so the agent's tools keep their order."""
    if type(chosen) is not list or set(chosen) - set(MODEL_TOOLS):
        raise ValueError("Choose model tools from the list")
    if set(required_tools(task)) - set(chosen):
        raise ValueError("This task needs its transcription tool: its requests are audio only")
    return [tool for tool in MODEL_TOOLS if tool in chosen]


def tool_catalog(tools):
    """The catalog entries of these tools; the agent's tool list is generated from them."""
    from .inventory_probe import CATALOG
    return [entry for entry in CATALOG if entry["kind"] in {_TOOL_KINDS[tool] for tool in tools}]


def validate_check_source(source):
    if type(source) is not str or not source.strip() or len(source) > MAX_CHECK:
        raise ValueError(f"A scoring check must be non-empty and at most {MAX_CHECK} characters")
    return source


def truth_shape(truths):
    """The field names and types of the answers, never their values."""
    def shape(value):
        if isinstance(value, dict):
            return {key: shape(item) for key, item in value.items()}
        if isinstance(value, list):
            return [shape(value[0])] if value else []
        return "boolean" if isinstance(value, bool) else "number" if isinstance(value, (int, float)) else "text" if isinstance(value, str) else "null"
    shapes = [shape(truth) for truth in truths.values()]
    return {"samples": len(shapes), "shape": shapes[0] if shapes else None, "same_shape": all(item == shapes[0] for item in shapes)}


def quotes_answers(text, truths):
    """A backstop, not a proof: true if a text repeats a long answer, or several short ones. Numbers are left out."""
    def strings(value):
        if isinstance(value, dict):
            return [item for part in value.values() for item in strings(part)]
        if isinstance(value, list):
            return [item for part in value for item in strings(part)]
        return [value.lower()] if isinstance(value, str) and len(value) >= 4 else []
    lowered = text.lower()
    found = {value for truth in truths.values() for value in strings(truth) if re.search(r"(?<!\w)"+re.escape(value)+r"(?!\w)", lowered)}
    return len(found) >= 3 or any(len(value) >= 12 for value in found)


def source_hash(source):
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def inventory_task():
    """The inventory task as a spec. D-013: a user provides 6 samples."""
    return {"template": TEMPLATE, "name": "lx_inventory", "truth_schema": "inventory", "stream_samples": 6,
            "base_prompt": BASE_PROMPT, "contract": CONTRACT,
            "work": "answer spoken inventory requests about several photos, calling the available model tools.",
            "domain_word": "spoken photo inventory", "yardstick": CONTRACT, "deliverable_word": "inventory JSON",
            "selection_checks": "Check that the request was heard, every photo was examined, the right kinds and "
                                "colours were counted per photo, tool results were filtered sensibly, and the "
                                "answer equals the per-photo totals."}


def inventory_check_source():
    return (Path(__file__).parent/"inventory_check.py").read_text(encoding="utf-8")


class TaskDomain(InventoryDomain):
    """A photo-request task defined by its spec; answers are scored by its check file."""
    whole_context = True

    def __init__(self, task, check, scripts_enabled=True, segmentation=True):
        super().__init__(scripts_enabled)
        self.task, self.check = task, check
        self.name, self.base_prompt, self.task_prompt = task["name"], task["base_prompt"], task["contract"]
        self.segmentation, self.photos_note = segmentation, PHOTOS_NOTE if segmentation else VIEW_NOTE

    def prompts(self):
        from harness2.core.spec import BenchPrompts
        base = super().prompts()
        note = base.work.split("tools. ", 1)[-1]
        return BenchPrompts(work=self.task["work"]+" "+(note if self.segmentation else note.replace("input, photos and segments", "input and photos")),
                            domain_word=self.task["domain_word"], yardstick=self.task["yardstick"],
                            deliverable_word=self.task["deliverable_word"], selection_checks=self.task["selection_checks"])

    def verify(self, truth, answer):
        return run_check(self.check, truth, answer)


def sample_truths(root, task):
    """{folder name: truth} after validating every sample folder under the task's rules."""
    folders = sample_folders(root)
    if len(folders) < task["stream_samples"]:
        raise ValueError(f"This task needs at least {task['stream_samples']} sample folders; found {len(folders)}")
    if is_text(task):
        return {folder.name: text_task.check_sample_folder(folder)[1] for folder in folders}
    inventory = task.get("truth_schema") == "inventory"
    return {folder.name: check_sample_folder(folder, inventory=inventory)[1] for folder in folders}


def require_audio(root):
    missing = [folder.name for folder in sample_folders(root) if not (folder/"request.wav").is_file()]
    if missing:
        raise ValueError(f"A live run needs request.wav in every sample; missing in {', '.join(missing[:6])}")


def preview_scenes(count, *, seed=PREVIEW_SEED):
    """Segmentation-double scenes for the synthetic samples, keyed by the stored JPEG hashes."""
    scenes = {}
    for index in range(1, count+1):
        photos, _, _, layouts = synthetic_sample(seed, index)
        for data, placed in zip(photos, layouts):
            scenes[media_hash(normalize_photo(data))] = {"size": [GRID[0]*CELL, GRID[1]*CELL], "items": placed}
    return scenes


def require_preview_samples(root, count):
    """A preview answers from doubles, so it can only run on samples those doubles know."""
    scenes = preview_scenes(count)
    for folder in sample_folders(root):
        for path in (folder/"photos").iterdir():
            if media_hash(normalize_photo(path.read_bytes())) not in scenes:
                raise ValueError("A preview runs only on the synthetic demo samples; your own need a live run, which a person decides on")


def build_dataset(snapshot, root, task, *, preview):
    """The run's dataset: worker inputs and controller truth, frozen. Returns (stream ids, truth, requests)."""
    if is_text(task):
        return text_task.build_dataset(snapshot, root, task)
    root = Path(root)
    inventory = task.get("truth_schema") == "inventory"
    if not (root/"controller_truth"/"media.json").exists():
        add_user_samples(root, snapshot, stream_count=task["stream_samples"], prefix="sample", inventory=inventory)
        manifest = json.loads((root/"worker_inputs"/"manifest.json").read_text(encoding="utf-8"))
        for index, sample in enumerate(manifest["samples"], 1):
            audio = root/"worker_inputs"/sample["audio"]
            if not audio.is_file():
                if not preview:
                    raise ValueError("A live run needs request.wav in every sample")
                audio.write_bytes(_tone(index))
        finalize_inventory(root, inventory=inventory)
    manifest = json.loads((root/"worker_inputs"/"manifest.json").read_text(encoding="utf-8"))
    truth = json.loads((root/"controller_truth"/"ground_truth.json").read_text(encoding="utf-8"))
    requests = json.loads((root/"controller_truth"/"requests.json").read_text(encoding="utf-8"))
    stream = [s["sample_id"] for s in manifest["samples"] if s["split"] == "stream"]
    return stream, truth, requests


__all__ = ["CheckError", "TEMPLATE", "TEXT_TEMPLATE", "TaskDomain", "build_dataset", "inventory_check_source", "inventory_task", "is_text",
           "preview_scenes", "require_audio", "require_preview_samples", "sample_folders", "sample_truths",
           "source_hash", "validate_check_source", "validate_task"]
