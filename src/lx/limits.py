"""Run limits the user sets (D-017): their defaults and their hard outer bounds.

A spec holds every value, so limits are approved with it and a change makes a
new version. Defaults come from a preset, then the provider entry; the user's
own values go on top. Safety rules are not here and stay fixed: sandbox rules,
check and script run times, the self-test, human approval, request size bounds
and no redirects.

Most fields belong to one role, so that role's provider sets the default. The
rest cover the whole run, and the longest default among its providers wins.
"""
from __future__ import annotations

HOUR = 3600
# name: (label, lowest, highest, role whose provider sets the default, or None for the whole run)
FIELDS = {
    "agent_turns_per_photo": ("Agent turns per photo or text sample (plus 4)", 1, 20, "worker"),
    "agent_tools_per_photo": ("Agent tool calls per photo or text sample (plus 6)", 1, 40, "worker"),
    "stop_after_empty_runs": ("Stop after this many agent runs in a row with no answer", 1, 1000, "worker"),
    "improver_turns": ("Improver turns per session", 1, 100, "improver"),
    "improver_seconds": ("Improver session, seconds", 30, HOUR, "improver"),
    "call_seconds": ("One model call with its retries, seconds", 10, 1800, None),
    "run_seconds": ("Whole run, seconds", 60, 6*HOUR, None),
    "reply_tokens": ("Reply length, tokens", 256, 32768, None),
    "chat_calls": ("Model calls per run", 1, 5000, None),
    "segment_calls": ("SAM calls per run", 1, 5000, None),
    "transcribe_calls": ("Transcribe calls per run", 1, 1000, None),
}
# A text task calls neither tool, so these limits do nothing there.
PHOTO_ONLY = ("segment_calls", "transcribe_calls")


def preset(spark, task=False):
    """Spark keeps its original numbers, but for the improver: at 12 turns 2 of its 26 sessions ran out
    on 31 Oct, and that limits what the loop can learn. Hosted reasoning and local models can take over
    a minute a call, and Glimmer made one tool call per turn (live runs, 30 Oct 2026)."""
    return {"agent_turns_per_photo": 12, "agent_tools_per_photo": 12, "stop_after_empty_runs": 8,
            "improver_turns": 30, "improver_seconds": 900,
            "call_seconds": 90 if spark else 600 if task else 300,
            "run_seconds": 4*HOUR if task else 900 if spark else HOUR,
            "reply_tokens": 4096 if spark else 8192,
            # A task run: about 4 agent runs and 3-4 improver sessions per sample, as measured on the inventory stream.
            "chat_calls": 1400 if task else 200, "segment_calls": 600, "transcribe_calls": 60}


def check(values):
    """Validate user or provider-entry values; any subset of the fields is allowed."""
    if type(values) is not dict:
        raise ValueError("Limits must be a set of named numbers")
    for name, value in values.items():
        if name not in FIELDS:
            raise ValueError(f"Unknown limit: {name}")
        label, low, high, _ = FIELDS[name]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{label} must be a whole number from {low} to {high}")
    return values


def defaults(roles, *, task=False):
    """roles: {role: (is Spark, the provider entry's own limits)}."""
    presets = {role: {**preset(spark, task), **entry} for role, (spark, entry) in roles.items()}
    return {name: presets[field[3]][name] if field[3] else max(values[name] for values in presets.values())
            for name, field in FIELDS.items()}


def resolve(base, chosen):
    """(all limits, the user's own values). A value equal to its default is not the user's own."""
    own = {name: value for name, value in check(chosen).items() if value != base[name]}
    return {**base, **own}, own


def field_list():
    return [{"name": name, "label": field[0], "min": field[1], "max": field[2]} for name, field in FIELDS.items()]
