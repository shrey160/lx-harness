"""Voicecount domain: the agent answers spoken counting requests with model tools.

Same split mapping as the event-log task: ``loop`` is the adapter's ``dev``
(staged for the improver, decides retention), ``verify`` is ``heldout``, and
``test``/``calibration`` are never touched during a trial. Execution runs the
agent tool loop; the improver reads its full trace as the rollout.
"""
import base64
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from .agent import ItemTools, agent_tools, render_trace, run_agent, tool_identity
from .perception import FakeSegmentation, FakeTranscription, media_hash
from .spike import wire_reply
from .voicecount import CONTRACT, SPLITS, WHERE, WORDING, jpeg_size, verify_count
from .workflow import ImagesAdapter

BASE_PROMPT = "Answer the spoken request about the photo and return JSON."
ADAPTER_SPLITS = {"loop": "dev", "verify": "heldout", "test": "test", "calibration": "calibration"}
WORKER_MARKER = "LX_VOICECOUNT_AGENT"
CAPS = {"max_turns": 8, "max_tool_calls": 12}


def media_path(root, relative, suffix):
    parts = PurePosixPath(relative) if isinstance(relative, str) else None
    if (parts is None or "\\" in relative or ":" in relative or parts.is_absolute()
            or any(p.startswith(".") for p in parts.parts) or parts.suffix != suffix):
        raise ValueError("Invalid voicecount media path")
    path = Path(root).joinpath(*parts.parts)
    if path.is_symlink() or not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("Voicecount media must stay inside its snapshot")
    return path


class VoiceCountDomain:
    name = "lx_voicecount"
    base_prompt = BASE_PROMPT
    task_prompt = CONTRACT
    verify = staticmethod(verify_count)

    @staticmethod
    def prompts():
        from harness2.core.spec import BenchPrompts
        return BenchPrompts(work="answer spoken counting requests about photos, calling the available model tools",
            domain_word="spoken photo counting", yardstick=CONTRACT, deliverable_word="count JSON",
            selection_checks="Check that the request was heard, the right objects and region were counted, "
                             "and tool results were filtered sensibly before counting.")

    @staticmethod
    def samples_from(snapshot):
        root = Path(snapshot)
        manifest = root/"manifest.json"
        if root.is_symlink() or manifest.is_symlink():
            raise ValueError("Linked voicecount inputs rejected")
        samples = json.loads(manifest.read_text(encoding="utf-8"))["samples"]
        if type(samples) is not list or not 1 <= len(samples) <= 2000:
            raise ValueError("Expected 1..2000 voicecount samples")
        ids, mapped = set(), []
        for sample in samples:
            if type(sample) is not dict or set(sample) != {"sample_id", "source_id", "split", "image", "audio"}:
                raise ValueError("Invalid voicecount manifest fields")
            if not all(type(v) is str for v in sample.values()) or sample["split"] not in SPLITS:
                raise ValueError("Invalid voicecount manifest values")
            if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", sample["sample_id"]) is None or sample["sample_id"] in ids:
                raise ValueError("Invalid or duplicate voicecount identity")
            media_path(root, sample["image"], ".jpg")
            media_path(root, sample["audio"], ".wav")
            ids.add(sample["sample_id"])
            mapped.append(dict(sample, split=ADAPTER_SPLITS[sample["split"]]))
        return root, mapped

    def messages(self, sample, prompt, image=None):
        width, height = jpeg_size(image) if image else (None, None)
        text = json.dumps({"sample_id": sample["sample_id"], "image_size": [width, height],
                           "photo": "attached" if image else "not viewable by you; use the tools",
                           "spoken_request": "audio only"})
        content = [{"type": "text", "text": text}]
        if image:
            content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image).decode()}})
        return [{"role": "developer", "content": WORKER_MARKER+"\n"+prompt+"\n\nTask contract:\n"+self.task_prompt},
                {"role": "user", "content": content}]


class VoiceCountAdapter(ImagesAdapter):
    """Agent executions with registered tools; identity covers agent, tools and media."""

    def __init__(self, snapshot, output, client, truth, *, perception, catalog, mode="offline_preview", caps=None):
        super().__init__(snapshot, output, client, mode=mode, domain=VoiceCountDomain(), truth=truth)
        self.perception, self.tools = perception, agent_tools(catalog)
        self.caps = dict(CAPS, **(caps or {}))

    def _media(self, task, kind):
        sample = self.by_id[task]
        return media_path(self.input_root, sample[kind], ".jpg" if kind == "image" else ".wav").read_bytes()

    def _identity(self, task, harness):
        profile = self.client.profile
        extra = {"base": super()._identity(task, harness), "worker_protocol": "lx-agent-tools-v1",
                 "agent": {"model": profile.model, "endpoint": profile.endpoint, "images": profile.accepts_images,
                           "tools": profile.supports_tools},
                 "tool_sha256": tool_identity(self.tools), "caps": self.caps,
                 "image_sha256": media_hash(self._media(task, "image")),
                 "audio_sha256": media_hash(self._media(task, "audio"))}
        return hashlib.sha256(json.dumps(extra, sort_keys=True).encode()).hexdigest()

    def execute(self, task, harness_dir, run_id, *, timeout=None):
        run = self.spec.runs_root/"executions"/run_id
        identity = self._identity(task, harness_dir)
        if self.artifacts_intact(run):
            if json.loads((run/"execution.json").read_text(encoding="utf-8"))["identity"] == identity:
                return run
        run.mkdir(parents=True, exist_ok=True)
        from harness2.core import tree
        harness = tree.read_tree(self.substrate.surface(), harness_dir or self.base_harness())
        prompt = "\n\n".join(f"{name}\n{body}" for name, body in sorted(harness.items()) if body.strip())
        image = self._media(task, "image")
        messages = self.domain.messages(self.by_id[task], prompt, image if self.client.profile.accepts_images else None)
        handler = ItemTools(self.perception, image=image, audio=self._media(task, "audio"), candidate=run_id)
        text, trace = run_agent(self.client, messages, self.tools, handler, candidate=run_id,
                                timeout=timeout or 600, **self.caps)
        content = text
        if content.startswith("```"):
            content = "\n".join(content.splitlines()[1:-1])
        try:
            def reject_constant(value):
                raise ValueError("Nonfinite JSON number")
            answer = json.loads(content, parse_constant=reject_constant)
        except ValueError:
            answer = content
        (run/"answer.json").write_text(json.dumps(answer, allow_nan=False), encoding="utf-8")
        (run/"trace.json").write_text(json.dumps({"final_text": text, "trace": trace}, allow_nan=False), encoding="utf-8")
        (run/"execution.json").write_text(json.dumps({"task": task, "identity": identity,
            "harness": str(harness_dir) if harness_dir else None,
            "answer_sha256": hashlib.sha256((run/"answer.json").read_bytes()).hexdigest(),
            "tool_calls": sum(step["type"] == "tool" for step in trace)}), encoding="utf-8")
        self.executions += 1
        return run

    def trajectory(self, run, *, obs_trunc):
        record = json.loads((run/"trace.json").read_text(encoding="utf-8"))
        return render_trace(record["trace"], record["final_text"], obs_trunc=obs_trunc)


def preview_perception(dataset):
    """Wire-format SAM/Transcribe doubles built from controller truth (offline only)."""
    points, scenes, scripts = dataset.points(), {}, {}
    for sample in dataset.worker_inputs:
        sid = sample["sample_id"]
        media = dataset.root/"worker_inputs"
        truth = dataset.controller_truth[sid]
        scenes[media_hash((media/sample["image"]).read_bytes())] = {
            "size": truth["size"], "points": points[sid], "concepts": truth["aliases"]}
        scripts[media_hash((media/sample["audio"]).read_bytes())] = dataset.scripts[sid]["text"]
    # A low miss rate keeps filtered counts mostly within tolerance, so the
    # double separates guided from unguided harnesses; real SAM error is measured live.
    return FakeSegmentation(scenes, miss=0.01), FakeTranscription(scripts)


_WHERE = "|".join(re.escape(phrase) for phrases in WHERE.values() for phrase in phrases)
_REQUEST = re.compile("|".join(
    re.escape(template).replace(r"\{obj\}", "(?P<obj{0}>.+?)").replace(r"\{where\}", "(?P<where{0}>" + _WHERE + ")")
    .replace("{0}", str(i)) for i, template in enumerate(WORDING["loop"] + WORDING["heldout"])))
GUIDANCE = ("Always call transcribe first. Use the requested object (plural noun) as the segment concept. "
            "Then call count_boxes with min_confidence=0.5, dedupe_iou=0.3 and the region the request names; "
            "report that count.")


def parse_request(text):
    """Object and region from a request in the known wording banks (preview double only)."""
    match = _REQUEST.fullmatch(text.strip())
    if not match:
        return None, "whole"
    groups = {re.sub(r"\d+$", "", k): v for k, v in match.groupdict().items() if v}
    region = next((r for r, phrases in WHERE.items() if groups["where"] in phrases), "whole")
    return groups["obj"], region


class VoiceCountPreviewTransport:
    """Deterministic plumbing double for the agent, improver, selector and reflector.

    The agent follows the harness text: without guidance it counts every SAM
    box in the whole image; with the preview guidance it filters and uses the
    region. This exercises routing, traces and retention; it is not capability
    evidence. Calls are counted per kind for the call measurement.
    """

    def __init__(self):
        self.calls = {"worker": 0, "reflection": 0, "improver": 0}

    def __call__(self, body, timeout):
        messages = body["messages"]
        if messages[0]["content"].startswith(WORKER_MARKER):
            self.calls["worker"] += 1
            return self._agent(messages)
        names = messages[1]["content"].split("Available files:\n", 1)[-1].splitlines()
        if "adoption.json" in names:
            self.calls["reflection"] += 1
            return wire_reply("<experience>\ncue: spoken photo counting\nlesson: Transcribe first, filter boxes, "
                              "respect the region.\nevidence: Offline wiring check only.\nconfidence: low\n</experience>")
        self.calls["improver"] += 1
        if not any(m["role"] == "tool" for m in messages):
            return wire_reply(calls=[{"id": "preview-guidance", "type": "function", "function": {
                "name": "write_file", "arguments": json.dumps({"path": "harness/tool_descriptions/tool_guidance.md",
                                                               "content": GUIDANCE})}}])
        return wire_reply("<notes>Added tool guidance: transcribe first, filter, honour the region.</notes>")

    @staticmethod
    def _agent(messages):
        guided = "min_confidence=0.5" in messages[0]["content"]
        results = [json.loads(m["content"]).get("result", {}) for m in messages if m["role"] == "tool"]
        step = len(results)
        def call(name, arguments):
            return wire_reply(calls=[{"id": f"step-{step}", "type": "function",
                                      "function": {"name": name, "arguments": json.dumps(arguments)}}])
        if step == 0:
            return call("transcribe", {})
        obj, region = parse_request(results[0].get("transcript", ""))
        if obj is None:
            return wire_reply(json.dumps({"object": "unknown", "region": "whole", "count": 0}))
        if step == 1:
            return call("segment", {"concept": obj})
        if guided and step == 2:
            return call("count_boxes", {"min_confidence": 0.5, "dedupe_iou": 0.3, "region": region})
        count = results[-1]["count"] if guided else results[-1]["instances"]
        return wire_reply(json.dumps({"object": obj, "region": region if guided else "whole", "count": count}))
