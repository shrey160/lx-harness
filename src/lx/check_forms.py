"""The forms a check can be written in. Each is saved as one check file that speaks the protocol in checks.py.

- A full check reads stdin itself and is saved as it is, so older checks never change.
- A function, `def score(truth, answer):`, is saved with the kit after it.

The hash covers the kit too, so a later kit never changes an approved check.
"""
from __future__ import annotations

import ast
from pathlib import Path

from .sandbox import MAX_SOURCE

MARKER = "# --- LX check kit below; edit the text above this line ---"


def _kit():
    return (Path(__file__).parent/"check_kit.py").read_text(encoding="utf-8")


def _with_kit(text):
    saved = f"{text.rstrip()}\n\n{MARKER}{_kit()}"
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


def saved_form(text):
    """The check file to save for this text; a file already in its saved form stays as it is."""
    if MARKER in text or not _is_function(text):
        return text
    return _with_kit(text)


def author_text(source):
    """What the author wrote: a saved check without its kit."""
    return source.split(MARKER, 1)[0].rstrip()+"\n" if MARKER in source else source
