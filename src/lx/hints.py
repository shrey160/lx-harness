"""Improver hints: a person's notes on how a kind of task is done, for the improver only.

The spec holds them (`hints = {"all": text, "samples": {folder: text}}`), so a change is a new
version and clears approval. Files named `improver_hints.md` in the samples folder or in a sample
folder seed blank fields. The worker never sees a hint, the check never reads one, and the improver
reads them as `source/hints.md` next to the sample's own notes. A hint describes the work, never an
answer, so edits made from it can hold for any case of the kind (Harness² keeps scores and answers
from the improver; these notes are an extension, not in the paper).
"""
from __future__ import annotations

import json
from pathlib import Path
import re

FILE = "improver_hints.md"
MAX_CHARS = 8000
PREFACE = ("Notes from the person who set up this task. They describe how this kind of task is done, "
           "not this sample. Write edits that would hold for any case of this kind, and keep facts "
           "about one sample out of the harness.\n")
_BOXES = re.compile(r"\[\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\]")


def _text(value, where):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"The hints for {where} must be text")
    value = value.strip()
    if len(value) > MAX_CHARS:
        raise ValueError(f"The hints for {where} must be at most {MAX_CHARS} characters")
    return value or None


def clean(hints):
    """The normal form: None when empty, else {"all": text or None, "samples": {folder: text}}."""
    if hints is None:
        return None
    if not isinstance(hints, dict) or set(hints) - {"all", "samples"}:
        raise ValueError("Hints are one text for all samples and a text for each sample")
    samples = hints.get("samples", {})
    if samples is None:
        samples = {}
    if not isinstance(samples, dict):
        raise ValueError("Hints for samples are a text for each sample folder")
    kept = {}
    for folder, value in samples.items():
        if not isinstance(folder, str) or not re.fullmatch(r"sample-[A-Za-z0-9_-]{1,40}", folder):
            raise ValueError("A sample hint is named by its sample folder, such as sample-01")
        text = _text(value, folder)
        if text:
            kept[folder] = text
    everyone = _text(hints.get("all"), "all samples")
    return {"all": everyone, "samples": kept} if everyone or kept else None


def from_files(root):
    """The hints the sample files hold: {"all": ..., "samples": {...}} in the normal form, or None."""
    root = Path(root)
    read = lambda path: path.read_text(encoding="utf-8", errors="replace") if path.is_file() and not path.is_symlink() else None
    samples = {}
    if root.is_dir():
        for folder in sorted(p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"sample-[A-Za-z0-9_-]{1,40}", p.name)):
            text = read(folder/FILE)
            if text and text.strip():
                samples[folder.name] = text[:MAX_CHARS*4]
    return clean({"all": (read(root/FILE) or "")[:MAX_CHARS*4], "samples": samples})


def fill_blanks(hints, files):
    """Spec hints with the file hints added where the spec has none; the spec wins elsewhere."""
    hints, files = clean(hints), clean(files)
    if not files:
        return hints
    hints = hints or {"all": None, "samples": {}}
    return clean({"all": hints["all"] or files["all"], "samples": {**files["samples"], **hints["samples"]}})


def drift(hints, files):
    """Where a hint file differs from the spec's hints, as text a person can act on."""
    hints, files = clean(hints) or {"all": None, "samples": {}}, clean(files)
    if not files:
        return []
    found = []
    if files["all"] and files["all"] != hints["all"]:
        found.append(f"{FILE} at the top of the samples differs from the spec's hints for all samples")
    for folder, text in sorted(files["samples"].items()):
        if text != hints["samples"].get(folder):
            found.append(f"{folder}/{FILE} differs from the spec's hints for that sample")
    return found


def refuse_answers(hints, truths):
    """A hint describes the work. Refuse one that carries box coordinates or an expected total claim; the
    message never says which, so a guess cannot be checked against the answers."""
    if not hints:
        return
    texts = ([hints["all"]] if hints["all"] else []) + list(hints["samples"].values())
    totals = set()
    for truth in (truths or {}).values():
        total = truth.get("total_claim") if isinstance(truth, dict) else None
        if isinstance(total, (int, float)) and not isinstance(total, bool):
            shown = json.dumps(total)
            if "." in shown or len(shown) >= 3:
                totals.add(shown)
    for text in texts:
        if _BOXES.search(text) or any(re.search(rf"(?<![\d.]){re.escape(total)}(?![\d.])", text) for total in totals):
            raise ValueError("A hint holds box coordinates or an expected total. Hints describe how the task is done, never the answers")


def compose(hints, folder):
    """What the improver reads for one sample, or None when there is nothing for it."""
    hints = clean(hints)
    if not hints:
        return None
    parts = [("For every sample", hints["all"]), (f"For {folder}", hints["samples"].get(folder))]
    body = "\n\n".join(f"## {title}\n\n{text}" for title, text in parts if text)
    return f"# Hints\n\n{PREFACE}\n{body}\n" if body else None


def stage(adapter, hints, folders, samples):
    """Write each adapted sample's hints beside its source notes. Held-out samples get none."""
    wrote = []
    for sample in samples:
        text = compose(hints, folders.get(sample, sample))
        if text:
            target = Path(adapter.task_files(sample))
            target.mkdir(parents=True, exist_ok=True)
            (target/"hints.md").write_text(text, encoding="utf-8")
            wrote.append(sample)
    return wrote


def summary(hints):
    hints = clean(hints)
    if not hints:
        return None
    count = len(hints["samples"])
    return ("all samples" if hints["all"] else "") + (", " if hints["all"] and count else "") + (f"{count} sample(s)" if count else "")
