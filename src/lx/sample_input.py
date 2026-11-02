"""Optional task data for photo samples; only input.json is exposed to workers."""
from __future__ import annotations

import json
import math
from pathlib import Path

MAX_INPUT_BYTES = 64 * 1024


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    return value


def _constant(text):
    raise ValueError("non-finite JSON number")


def read_input(path, *, required=False):
    """Read a bounded, ordinary UTF-8 JSON object; errors never echo its contents."""
    path = Path(path)
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValueError("input.json must not be linked")
    if not path.exists():
        if required:
            raise ValueError("missing input.json")
        return None
    if not path.is_file():
        raise ValueError("input.json must be a file")
    with path.open("rb") as stream:
        data = stream.read(MAX_INPUT_BYTES + 1)
    if not 0 < len(data) <= MAX_INPUT_BYTES:
        raise ValueError("input.json must hold 1 to 65536 bytes")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_object, parse_float=_float, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise ValueError("input.json must be valid UTF-8 JSON with unique fields and finite numbers") from None
    if type(value) is not dict:
        raise ValueError("input.json must hold a JSON object")
    return value


def staged_input(root, sample):
    """A manifest can name only this sample's input, never another file or truth."""
    if "input" not in sample:
        return None
    expected = f"media/{sample['sample_id']}/input.json"
    if sample["input"] != expected:
        raise ValueError("Invalid sample input path")
    return read_input(Path(root) / expected, required=True)
