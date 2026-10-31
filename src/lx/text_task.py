"""Second task template, `text-request`: a request in text, an answer in JSON.

Each sample is a folder `sample-*/` with request.txt and truth.json for the
check. The agent reads the request in its first message and has only the
harness's own skills and scripts, so a run needs no photos, audio, SAM or
Transcribe. Order normalization (lx.records) is the first task in it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
import re

from .checks import run_check
from .components import COMPONENT_TOOLS, LOAD_SKILL, ComponentTools, compose_prompt
from .inventory_workflow import (ADAPTER_SPLITS, COMPONENT_NOTE, SKILLS_ONLY_NOTE, InventoryAdapter, InventoryDomain,
                                 InventoryPreviewTransport, media_path)
from .records import _make_record, _split_rng
from .spike import wire_reply

TEMPLATE = "text-request"
AGENT_MARKER = "LX_TEXT_AGENT"
MAX_REQUEST = 20_000
PREVIEW_SEED = 20261031
SPLIT_SEED = 20261029
BASE_PROMPT = "Read the request and return JSON."
CONTRACT = ("Return one JSON object with exactly these fields: order_id (string), customer (string, Title Case with "
            "single spaces), date (YYYY-MM-DD), currency (ISO code), amount_minor (integer, the amount in minor "
            "units), phone (E.164 string, or null when not provided), tags (unique, sorted, lower-case strings).")
_NOTE = (("input, photos and segments", "input"), ("Python 3 with Pillow", "Python 3"))


def records_task():
    """Order normalization as a spec. D-013: a user provides 6 samples."""
    return {"template": TEMPLATE, "name": "lx_order_records", "truth_schema": "free", "stream_samples": 6,
            "base_prompt": BASE_PROMPT, "contract": CONTRACT,
            "work": "normalize noisy order records into strict JSON.", "domain_word": "order normalization",
            "yardstick": CONTRACT, "deliverable_word": "order JSON",
            "selection_checks": "Check that every field was read from the request, the formats match the contract, "
                                "and a missing phone is null."}


def records_check_source():
    return (Path(__file__).parent/"records_check.py").read_text(encoding="utf-8")


def sample_folders(root):
    return sorted(p for p in Path(root).iterdir() if p.is_dir() and re.fullmatch(r"sample-[A-Za-z0-9_-]{1,40}", p.name))


def check_sample_folder(folder):
    """(request text, truth) of one sample folder, or a ValueError naming what is wrong."""
    folder = Path(folder)
    try:
        request = (folder/"request.txt").read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValueError(f"{folder.name}: missing request.txt") from None
    if not request.strip() or len(request) > MAX_REQUEST:
        raise ValueError(f"{folder.name}: request.txt must hold 1 to {MAX_REQUEST} characters")
    try:
        return request, json.loads((folder/"truth.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"{folder.name}: missing truth.json") from None
    except ValueError as exc:
        raise ValueError(f"{folder.name}: truth.json is not valid JSON ({exc})") from None


def synthetic_samples(count, *, seed=PREVIEW_SEED):
    """[(request text, truth)]: fictional orders from the records generator."""
    rng = _split_rng(seed, "dev")
    made = [_make_record(rng, index, "dev") for index in range(count)]
    return [(record["text"], truth) for record, truth in made]


def write_samples(root, count, *, seed=PREVIEW_SEED):
    for index, (request, truth) in enumerate(synthetic_samples(count, seed=seed), 1):
        folder = Path(root)/f"sample-{index:02d}"
        folder.mkdir(parents=True)
        (folder/"request.txt").write_text(request, encoding="utf-8", newline="")
        (folder/"truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")


def require_preview_samples(root, count):
    """A preview answers from doubles, so it can only run on samples those doubles know."""
    known = {request for request, _ in synthetic_samples(count)}
    for folder in sample_folders(root):
        if check_sample_folder(folder)[0] not in known:
            raise ValueError("A preview runs only on the synthetic samples; use a live run for your own")


def build_dataset(snapshot, root, task):
    """The run's dataset, frozen once written. Returns (stream ids, truth, requests)."""
    root = Path(root)
    truth_file = root/"controller_truth"/"ground_truth.json"
    if not truth_file.exists():
        folders = sample_folders(snapshot)
        random.Random(f"{SPLIT_SEED}:user-split").shuffle(folders)  # folder order must not bias the split
        checked = [check_sample_folder(folder) for folder in folders]
        (root/"worker_inputs"/"requests").mkdir(parents=True)
        samples, truth = [], {}
        for index, (request, expected) in enumerate(checked, 1):
            sample_id = f"sample-{index:02d}"
            (root/"worker_inputs"/"requests"/f"{sample_id}.txt").write_text(request, encoding="utf-8", newline="")
            samples.append({"sample_id": sample_id, "split": "stream" if index <= task["stream_samples"] else "heldout",
                            "request": f"requests/{sample_id}.txt"})
            truth[sample_id] = expected
        (root/"worker_inputs"/"manifest.json").write_text(json.dumps({"samples": samples}), encoding="utf-8")
        truth_file.parent.mkdir()
        truth_file.write_text(json.dumps(truth), encoding="utf-8")
    manifest = json.loads((root/"worker_inputs"/"manifest.json").read_text(encoding="utf-8"))
    truth = json.loads(truth_file.read_text(encoding="utf-8"))
    return [s["sample_id"] for s in manifest["samples"] if s["split"] == "stream"], truth, {}


class TextDomain(InventoryDomain):
    """A text-request task defined by its spec; answers are scored by its check file."""
    whole_context = True

    def __init__(self, task, check, scripts_enabled=True):
        super().__init__(scripts_enabled)
        self.task, self.check = task, check
        self.name, self.base_prompt, self.task_prompt = task["name"], task["base_prompt"], task["contract"]

    def prompts(self):
        from harness2.core.spec import BenchPrompts
        note = COMPONENT_NOTE if self.scripts_enabled else SKILLS_ONLY_NOTE
        for old, new in _NOTE:
            note = note.replace(old, new)
        return BenchPrompts(work=self.task["work"]+" "+note, domain_word=self.task["domain_word"],
                            yardstick=self.task["yardstick"], deliverable_word=self.task["deliverable_word"],
                            selection_checks=self.task["selection_checks"])

    def verify(self, truth, answer):
        return run_check(self.check, truth, answer)

    @staticmethod
    def samples_from(snapshot):
        root = Path(snapshot)
        manifest = root/"manifest.json"
        if root.is_symlink() or manifest.is_symlink():
            raise ValueError("Linked task inputs rejected")
        samples = json.loads(manifest.read_text(encoding="utf-8"))["samples"]
        if type(samples) is not list or not 1 <= len(samples) <= 2000:
            raise ValueError("Expected 1..2000 samples")
        ids, mapped = set(), []
        for sample in samples:
            if type(sample) is not dict or set(sample) != {"sample_id", "split", "request"}:
                raise ValueError("Invalid manifest fields")
            if sample["split"] not in ADAPTER_SPLITS:
                raise ValueError("Invalid manifest values")
            if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(sample["sample_id"])) is None or sample["sample_id"] in ids:
                raise ValueError("Invalid or duplicate sample identity")
            media_path(root, sample["request"], ".txt")
            ids.add(sample["sample_id"])
            mapped.append(dict(sample, split=ADAPTER_SPLITS[sample["split"]]))
        return root, mapped

    def messages(self, sample, prompt, request):
        return [{"role": "developer", "content": AGENT_MARKER+"\n"+prompt+"\n\nTask contract:\n"+self.task_prompt},
                {"role": "user", "content": json.dumps({"sample_id": sample["sample_id"], "request": request})}]


class _NoTools:
    """The agent's tools here are its harness's own skills and scripts, added by ComponentTools."""

    def __call__(self, name, arguments):
        raise ValueError("unknown tool")


class TextAdapter(InventoryAdapter):
    """Agent executions over text requests; caps count one sample as one unit."""
    protocol = "lx-text-agent-v1"

    def __init__(self, snapshot, output, client, truth, *, domain, mode="offline_preview", limits=None, health=None):
        super().__init__(snapshot, output, client, truth, perception=None, catalog=None, mode=mode, domain=domain,
                         limits=limits, health=health)

    def _agent_tools(self, catalog):
        return COMPONENT_TOOLS if self.scripts_enabled else [LOAD_SKILL]

    def _caps(self, task):
        limits = self.limits or {}
        return {"max_turns": 4 + limits.get("agent_turns_per_photo", 3), "max_tool_calls": 6 + limits.get("agent_tools_per_photo", 6)}

    def _window(self, task):
        return 2

    def _request(self, task):
        return media_path(self.input_root, self.by_id[task]["request"], ".txt").read_text(encoding="utf-8")

    def _inputs(self, task):
        return {"request_sha256": hashlib.sha256(self._request(task).encode()).hexdigest()}

    def _conversation(self, task, surface, harness, run_id):
        messages = self.domain.messages(self.by_id[task], compose_prompt(surface, harness), self._request(task))
        return messages, ComponentTools(_NoTools(), harness, runner=self.script_runner)


# --- offline preview double (plumbing only; not capability evidence) ----------

SKILL_NAME = "order-format"
PREVIEW_SKILL = (f"---\nname: {SKILL_NAME}\ndescription: order fields must match the contract's formats\n---\n"
                 "1. Read every field from the request.\n2. Run the sort_tags script on the tags.\n"
                 "3. Write a missing phone as null.\n")
PREVIEW_SCRIPT = '''"""Lower-case, de-duplicate and sort order tags."""
import json
import sys

data = json.load(sys.stdin)
print(json.dumps({"tags": sorted({tag.lower() for tag in data["input"]["tags"]})}))
'''


class TextPreviewTransport(InventoryPreviewTransport):
    """Deterministic double. The agent copies the truth with bad tags and phone until the skill and
    script fix them; the script still runs in the real sandbox. The run sets `truth` after freezing
    its dataset. This proves the plumbing, not any capability."""
    marker = AGENT_MARKER
    lesson = "Match each field's format; a script normalizes tags."
    cue = "order normalization"
    truth = None

    def improver_files(self):
        files = {f"harness/skills/{SKILL_NAME}/SKILL.md": PREVIEW_SKILL,
                 "harness/guardrails.md": f"- Before answering, load the `{SKILL_NAME}` skill."}
        if self.scripts:
            files["harness/scripts/sort_tags.py"] = PREVIEW_SCRIPT
        return files

    def improver_notes(self):
        return "<notes>Added an order-format skill, its trigger" + (" and a sort_tags script.</notes>" if self.scripts else ".</notes>")

    def _agent(self, messages):
        developer = messages[0]["content"]
        expected = self.truth[json.loads(messages[1]["content"])["sample_id"]]
        noisy = [tag.upper() for tag in expected["tags"]]
        if f"- {SKILL_NAME}:" not in developer:
            return wire_reply(json.dumps({**expected, "tags": noisy, "phone": "unknown"}))
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]

        def call(name, arguments):
            return wire_reply(calls=[{"id": f"step-{len(results)}", "type": "function",
                                      "function": {"name": name, "arguments": json.dumps(arguments)}}])
        if not results:
            return call("load_skill", {"name": SKILL_NAME})
        if "- sort_tags:" not in developer:
            return wire_reply(json.dumps({**expected, "tags": noisy}))
        if len(results) == 1:
            return call("run_script", {"name": "sort_tags", "input": {"tags": noisy}})
        return wire_reply(json.dumps({**expected, "tags": results[-1]["result"]["output"]["tags"]}))


__all__ = ["COMPONENT_TOOLS", "TEMPLATE", "TextAdapter", "TextDomain", "TextPreviewTransport", "build_dataset",
           "check_sample_folder", "records_check_source", "records_task", "require_preview_samples", "sample_folders",
           "synthetic_samples", "write_samples"]
