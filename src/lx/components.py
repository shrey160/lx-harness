"""Harness components beyond the always-on files: skills and helper scripts.

PROD-004 (D-010) enables two of the paper's eight components for the
agent's harness, on the core's OpenCode surface:

- `skills/<name>/SKILL.md`: a procedure with `name:`/`description:`
  frontmatter. The prompt lists every skill; the agent reads one with
  `load_skill` when its situation applies.
- `scripts/<name>.py`: a deterministic helper. It reads one JSON object on
  stdin ({"input": the agent's input, "photos": work-dir paths, "segments":
  earlier segment boxes by photo and concept}) and prints JSON. The agent
  runs it with `run_script`; it runs in `lx.sandbox`, never on the host.

Sub-agents and plugin middleware stay disabled. Validation reuses the
core's `tree.validate`, then refuses everything this slice does not run.
"""
from __future__ import annotations

import json
import re

from .agent import _tool, photo_tools

MAX_SKILLS, MAX_SCRIPTS = 8, 8
MAX_SKILL_CHARS = 8_000
MAX_SCRIPT_CHARS = 20_000
SCRIPT_TIMEOUT = 20
_SKILL = re.compile(r"skills/([a-z0-9][a-z0-9-]*)/SKILL\.md")
_SCRIPT = re.compile(r"scripts/([a-z0-9_]+)\.py")

LOAD_SKILL = _tool(
    "load_skill",
    "Read one skill from your harness (listed under AVAILABLE SKILLS): a procedure to follow when its "
    "description applies.",
    {"name": {"type": "string", "description": "Skill name from the list."}},
    ["name"])
RUN_SCRIPT = _tool(
    "run_script",
    "Run one helper script from your harness (listed under HELPER SCRIPTS) in a sandbox without network. "
    "It receives your input plus this item's photos and earlier segment boxes, and returns its JSON output.",
    {"name": {"type": "string", "description": "Script name from the list, without .py."},
     "input": {"type": "object", "description": "JSON object passed to the script as input."}},
    ["name", "input"])
COMPONENT_TOOLS = [LOAD_SKILL, RUN_SCRIPT]
# Tool text that only holds while segmentation is on.
_SEGMENT_TEXT = ((" (pixel corners of that photo, e.g. from segment)", " (pixel corners of that photo)"),
                 ("photos and earlier segment boxes", "photos"))


def task_tools(catalog, scripts=True):
    """The agent's tools on a photo task, the one list the run sends and the live contract allows.

    With segmentation off, nothing in it names segment, so the agent is not sent looking for it."""
    tools = photo_tools(catalog) + (COMPONENT_TOOLS if scripts else [LOAD_SKILL])
    if any(tool["function"]["name"] == "segment" for tool in tools):
        return tools
    text = json.dumps(tools)
    for old, new in _SEGMENT_TEXT:
        text = text.replace(old, new)
    return json.loads(text)


def _frontmatter(text, key):
    match = re.search(rf"^{key}:\s*(.+)$", text[:600], re.M)
    return match.group(1).strip() if match else ""


def skills(tree):
    return {m.group(1): body for rel, body in tree.items() if (m := _SKILL.fullmatch(rel))}


def scripts(tree):
    return {m.group(1): body for rel, body in tree.items() if (m := _SCRIPT.fullmatch(rel))}


def _script_summary(source):
    match = re.match(r'\s*(?:"""|\'\'\')\s*(.+)', source)
    return match.group(1).split('"""')[0].split("'''")[0].strip() if match else "no description"


def validate_components(surface, tree):
    """Core validation plus this slice's limits; raises ValueError listing every problem."""
    from harness2.core import tree as core_tree

    errors = []
    for rel in tree:
        if rel in surface.always_on or _SKILL.fullmatch(rel) or _SCRIPT.fullmatch(rel):
            continue
        errors.append(f"{rel}: only the always-on files, skills/<name>/SKILL.md and scripts/<name>.py "
                      "are enabled (no sub-agents, plugins or skill-local scripts)")
    found_skills, found_scripts = skills(tree), scripts(tree)
    if len(found_skills) > MAX_SKILLS or len(found_scripts) > MAX_SCRIPTS:
        errors.append(f"at most {MAX_SKILLS} skills and {MAX_SCRIPTS} scripts")
    errors += [f"skills/{n}/SKILL.md is over {MAX_SKILL_CHARS} characters"
               for n, body in found_skills.items() if len(body) > MAX_SKILL_CHARS]
    errors += [f"scripts/{n}.py is over {MAX_SCRIPT_CHARS} characters"
               for n, body in found_scripts.items() if len(body) > MAX_SCRIPT_CHARS]
    try:
        core_tree.validate(surface, tree, allow_subagents=False)
    except ValueError as exc:
        errors.append(str(exc))
    if errors:
        raise ValueError(" | ".join(errors))
    return {"skills": sorted(found_skills), "scripts": sorted(found_scripts)}


def compose_prompt(surface, tree):
    """Always-on text (as before) plus an index of skills and helper scripts."""
    prompt = "\n\n".join(f"{name}\n{tree[name]}" for name in sorted(surface.always_on)
                         if tree.get(name, "").strip())
    found_skills, found_scripts = skills(tree), scripts(tree)
    if found_skills:
        prompt += "\n\nAVAILABLE SKILLS (read with load_skill):\n" + "\n".join(
            f"- {name}: {_frontmatter(body, 'description') or 'no description'}" for name, body in sorted(found_skills.items()))
    if found_scripts:
        prompt += "\n\nHELPER SCRIPTS (run with run_script):\n" + "\n".join(
            f"- {name}: {_script_summary(body)}" for name, body in sorted(found_scripts.items()))
    return prompt


class ComponentTools:
    """Adds load_skill and run_script to an item's tool handler."""

    def __init__(self, base, tree, *, runner=None):
        self.base, self.skills, self.scripts = base, skills(tree), scripts(tree)
        if runner is None:
            from .sandbox import run_json as runner
        self.runner = runner

    def __getattr__(self, name):
        return getattr(self.base, name)

    def __call__(self, name, arguments):
        if name == "load_skill":
            if set(arguments) != {"name"} or arguments["name"] not in self.skills:
                raise ValueError("unknown skill; use a name from AVAILABLE SKILLS")
            return {"skill": arguments["name"], "content": self.skills[arguments["name"]]}
        if name == "run_script":
            if set(arguments) != {"name", "input"} or type(arguments["input"]) is not dict:
                raise ValueError("run_script takes name and an input object")
            source = self.scripts.get(str(arguments["name"]).removesuffix(".py"))
            if source is None:
                raise ValueError("unknown script; use a name from HELPER SCRIPTS")
            photos = getattr(self.base, "photos", [])
            files = {f"photos/{i}.{_extension(data)}": data for i, data in enumerate(photos, 1)}
            segments = {}
            for (photo, concept), boxes in getattr(self.base, "segments", {}).items():
                segments.setdefault(str(photo), {})[concept] = [
                    [b["x1"], b["y1"], b["x2"], b["y2"], round(b.get("c", 1.0), 3)] for b in boxes]
            payload = {"input": arguments["input"], "photos": list(files), "segments": segments}
            try:
                json.dumps(payload, allow_nan=False)
            except ValueError:
                raise ValueError("script input must be strict JSON") from None
            result = self.runner(source, payload, files, timeout=SCRIPT_TIMEOUT)
            out = {"script": arguments["name"], "exit_code": result["exit_code"], "output": result["output"]}
            if result["timed_out"]:
                out["error"] = f"timed out after {SCRIPT_TIMEOUT} s"
            if result["exit_code"] != 0 and result["stderr"]:
                out["stderr"] = result["stderr"]
            return out
        return self.base(name, arguments)


def _extension(data):
    return "png" if data.startswith(b"\x89PNG") else "jpg"
