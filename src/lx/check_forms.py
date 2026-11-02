"""The forms a check can be written in. Each is saved as one check file that speaks the protocol in checks.py.

- A full check reads stdin itself and is saved as it is, so older checks never change.
- A function, `def score(truth, answer):`, is saved with the kit after it.
- Rules, a JSON object, are saved as a string the kit reads, so no code is needed.

The hash covers the kit too, so a later kit never changes an approved check.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re

from .checks import MAX_CHECKS
from .sandbox import MAX_SOURCE

MARKER = "# --- LX check kit below; edit the text above this line ---"
RULES_LINE = "RULES = "
# Each rule's own fields, beside `field` and `rule`.
RULES = {"equals": (), "same_items": (), "one_of": ("values",), "matches": ("pattern",), "near": ("within",)}


def _kit():
    return (Path(__file__).parent/"check_kit.py").read_text(encoding="utf-8")


def _with_kit(text, head):
    saved = f"{head.rstrip()}\n\n{MARKER}{_kit()}"
    if len(saved) > MAX_SOURCE:
        raise ValueError(f"This check is too long: keep it to at most {MAX_SOURCE-len(saved)+len(text)} characters")
    return saved


def _is_function(text):
    """Whether this text is the function form. A file that reads stdin is a full check, even with a function named score."""
    if "stdin" in text:
        return False
    try:
        return any(isinstance(node, ast.FunctionDef) and node.name == "score" for node in ast.parse(text).body)
    except SyntaxError as error:
        raise ValueError(f"The check has a syntax error on line {error.lineno}: {error.msg}") from None


def _check_rule(number, rule):
    where = f"Rule {number}"
    if type(rule) is not dict or type(rule.get("field")) is not str or not rule["field"].strip(". "):
        raise ValueError(f"{where} needs a field, such as {{\"field\": \"total\", \"rule\": \"equals\"}}")
    kind = rule.get("rule")
    if kind not in RULES:
        raise ValueError(f"{where} needs a rule, one of: {', '.join(RULES)}")
    needed = RULES[kind]
    if set(rule)-{"field", "rule", *needed} or set(needed)-set(rule):
        raise ValueError(f"{where}: {kind} takes "+(f"only field, rule and {needed[0]}" if needed else "only field and rule"))
    if kind == "one_of" and (type(rule["values"]) is not list or not rule["values"]):
        raise ValueError(f"{where}: values must be a list with something in it")
    if kind == "near" and (type(rule["within"]) not in {int, float} or rule["within"] < 0):
        raise ValueError(f"{where}: within must be a number, 0 or more")
    if kind == "matches":
        try:
            re.compile(rule["pattern"])
        except (TypeError, re.error):
            raise ValueError(f"{where}: pattern must be a valid regular expression") from None


def check_rules(text):
    """The rules in this text, or a ValueError a person can act on."""
    try:
        written = json.loads(text)
    except ValueError as error:
        raise ValueError(f"The rules are not valid JSON: {error}") from None
    if type(written) is not dict or set(written)-{"rules", "extra_fields"} or type(written.get("extra_fields", True)) is not bool:
        raise ValueError("Rules are an object with rules, and optionally extra_fields set to true or false")
    rules = written.get("rules")
    if type(rules) is not list or not 1 <= len(rules) < MAX_CHECKS:
        raise ValueError(f"Give 1 to {MAX_CHECKS-1} rules")
    for number, rule in enumerate(rules, 1):
        _check_rule(number, rule)
    if len({(rule["field"], rule["rule"]) for rule in rules}) != len(rules):
        raise ValueError("Each field can have each rule only once")
    return written


def starting_rules(shape):
    """Rules to start from, given what truth_shape says of the answers: one `equals` per field. Field names only, never values."""
    if type(shape) is not dict or not shape:
        raise ValueError("Rules read named fields, and these answers have none; write def score(truth, answer) instead")
    lines = ",\n".join("    "+json.dumps({"field": field, "rule": "equals"}) for field in shape)
    return '{\n  "rules": [\n'+lines+'\n  ]\n}\n'


def saved_form(text):
    """The check file to save for this text; a file already in its saved form stays as it is."""
    if MARKER in text:
        return text
    if text.lstrip().startswith("{"):
        check_rules(text)
        return _with_kit(text, RULES_LINE+json.dumps(text))
    return _with_kit(text, text) if _is_function(text) else text


def author_text(source):
    """What the author wrote: a saved check without its kit."""
    if MARKER not in source:
        return source
    head = source.split(MARKER, 1)[0].rstrip()
    return json.loads(head[len(RULES_LINE):]) if head.startswith(RULES_LINE) else head+"\n"
