"""The built-in orchestrator: a bounded conversation that sets up a run (D-024).

It uses the same actions as an outside agent (lx.orchestration), but only those
below: starting and approving a run stay with the person in the app. It never
gets truth values, only their shape, because the same agent writes the check.
This prompt is product text, not something the loop tunes, so D-021 does not
ask it to stay thin; what it must keep out of the task is what the loop learns.
"""
from __future__ import annotations

import json

from .spike import wire_reply

MAX_TURNS = 10
MAX_HISTORY = 40
SESSION_SECONDS = 300
PROMPT = """LX_DISCUSSION
You help a person set up a task for LX, a harness that learns how to do a task from sample runs.
Use the file list first, and ask only questions whose answer changes the task.
When the task is clear, write it with spec_draft: the goal, the task text, the output contract and the scoring check. Test the check with check_test.

The task text says what the work is and what a good answer looks like. It may say what the task needs from a tool and what a tool does, such as "the request is spoken; transcribe turns it into text".
It must not say how to do the work: no order of steps, thresholds, retries or ways of combining results. The loop learns those from real runs.
Never put an answer from a sample in the task text. You see the shape of the answers, not their values, and the check reads them when it runs.

You cannot approve or start a run. The person does that in the app, and a new spec version clears any approval."""


def _function(name, description, properties=None, required=()):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}}}


TOOLS = [
    _function("samples", "List the sample files with their kinds, sizes and hashes."),
    _function("read_sample", "Read one sample input file as text. Answer files cannot be read.",
              {"path": {"type": "string", "description": "A path from the sample list"}}, ["path"]),
    _function("truth_shape", "The shape of the answers: field names and types, never values."),
    _function("spec_show", "The current spec, its check source and what blocks it."),
    _function("spec_draft", "Write a new spec version. Give only what changes.",
              {"goal": {"type": "string"}, "task": {"type": "object", "description": "Task fields that change"},
               "check": {"type": "string", "description": "The whole scoring check source"},
               "tools": {"type": "array", "items": {"type": "string"}, "description": "Model tools to keep on"},
               "limits": {"type": "object"}}),
    _function("check_test", "Self-test the scoring check on the samples."),
]


def converse(lx, folder, client, project, text, *, tools=True):
    """One user message, answered after at most MAX_TURNS model turns; returns the reply."""
    history = [{"role": "developer", "content": PROMPT}]
    history += [{"role": m["role"], "content": m["content"]} for m in project["messages"][-MAX_HISTORY:]]
    history.append({"role": "user", "content": json.dumps({"goal": project["goal"], "inventory": project["inventory"], "message": text})})

    def handle(name, arguments):
        allowed = next((tool["function"]["parameters"]["properties"] for tool in TOOLS if tool["function"]["name"] == name), None)
        if allowed is None:
            return {"error": "unknown tool"}
        if set(arguments) - set(allowed):
            return {"error": "unknown argument"}
        try:
            return getattr(lx, name)(folder, **arguments)
        except (TypeError, ValueError) as exc:
            return {"error": str(exc)}

    if not tools:
        return client.chat(history, role="orchestrator", phase="discussion", candidate=project["id"])[0]["content"]
    return client.session(history, handle, TOOLS, role="orchestrator", phase="discussion", candidate=project["id"],
                          timeout=SESSION_SECONDS, max_turns=MAX_TURNS)[0]


def preview_turn(body):
    """A scripted conversation for previews: asked to draft, it goes through the tools once. Plumbing, not capability."""
    if "tools" not in body or "draft" not in json.loads(body["messages"][-_turns(body)]["content"])["message"].lower():
        return None
    results = [json.loads(m["content"]).get("result") for m in body["messages"] if m["role"] == "tool"]
    if len(results) >= 4:
        return wire_reply("Controlled preview: the spec is drafted and its check passed its self-test; nothing was sent to a model.")
    if len(results) == 2:
        arguments = {"task": {"selection_checks": results[1]["task"]["selection_checks"]+" Keep every requested photo in view."},
                     "check": results[1]["check"]}
    else:
        arguments = {}
    name = ("samples", "spec_show", "spec_draft", "check_test")[len(results)]
    return wire_reply(calls=[{"id": f"preview-{len(results)}", "type": "function",
                              "function": {"name": name, "arguments": json.dumps(arguments)}}])


def _turns(body):
    """How far back from the end this message's user turn is."""
    return next(index for index, message in enumerate(reversed(body["messages"]), 1) if message["role"] == "user")
