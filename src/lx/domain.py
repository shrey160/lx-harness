"""Synthetic image-and-transcript demo domain and independent verifier.

All images are drawn from the same rectangle definitions used to author the
controller truth. The worker input manifest contains no labels or boxes.
"""

from __future__ import annotations

import json
import math
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


IMAGE_WIDTH = 96
IMAGE_HEIGHT = 64
BOX_EDGE_TOLERANCE_PX = 1


@dataclass(frozen=True)
class _Rectangle:
    label: str
    box: tuple[int, int, int, int]
    color: tuple[int, int, int]


@dataclass(frozen=True)
class _Scene:
    scene_id: str
    split: str
    transcript: str
    rectangles: tuple[_Rectangle, ...]


@dataclass(frozen=True)
class DemoDataset:
    """Dataset handle keeping worker inputs and controller truth distinct."""

    root: Path
    worker_inputs: tuple[Mapping[str, str], ...]
    controller_truth: Mapping[str, Mapping[str, object]]


@dataclass(frozen=True)
class VerificationResult:
    accepted: bool
    errors: tuple[str, ...]


_SCENES = (
    _Scene(
        "dev-scene-01",
        "dev",
        "A red square sits beside a blue rectangle.",
        (
            _Rectangle("red_square", (8, 10, 31, 33), (220, 45, 45)),
            _Rectangle("blue_rectangle", (48, 18, 82, 43), (45, 90, 220)),
        ),
    ),
    _Scene(
        "dev-scene-02",
        "dev",
        "A green rectangle is below a yellow square.",
        (
            _Rectangle("green_rectangle", (12, 30, 47, 52), (40, 170, 85)),
            _Rectangle("yellow_square", (58, 7, 82, 31), (230, 195, 35)),
        ),
    ),
    _Scene(
        "heldout-scene-01",
        "heldout",
        "A purple square appears above an orange rectangle.",
        (
            _Rectangle("purple_square", (21, 5, 45, 29), (135, 65, 180)),
            _Rectangle("orange_rectangle", (50, 34, 86, 55), (235, 125, 35)),
        ),
    ),
    _Scene(
        "heldout-scene-02",
        "heldout",
        "A cyan rectangle is next to a black square.",
        (
            _Rectangle("cyan_rectangle", (7, 21, 43, 46), (35, 190, 195)),
            _Rectangle("black_square", (60, 9, 84, 33), (25, 25, 25)),
        ),
    ),
)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def _write_png(path: Path, rectangles: tuple[_Rectangle, ...]) -> None:
    """Write a valid deterministic 8-bit truecolor PNG using only stdlib."""
    background = (245, 245, 245)
    pixels = [[background for _ in range(IMAGE_WIDTH)] for _ in range(IMAGE_HEIGHT)]
    for rectangle in rectangles:
        x1, y1, x2, y2 = rectangle.box
        for y in range(y1, y2):
            for x in range(x1, x2):
                pixels[y][x] = rectangle.color

    scanlines = bytearray()
    for row in pixels:
        scanlines.append(0)  # PNG filter type 0
        for red, green, blue in row:
            scanlines.extend((red, green, blue))

    ihdr = struct.pack(">IIBBBBB", IMAGE_WIDTH, IMAGE_HEIGHT, 8, 2, 0, 0, 0)
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(scanlines), level=9))
        + _chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def build_demo_dataset(root: str | Path) -> DemoDataset:
    """Generate four tiny samples, split by source scene (two per split).

    Worker files live under ``worker_inputs/``. Ground truth is written to a
    separate ``controller_truth/`` directory and is omitted from the worker
    manifest and every worker-input record.
    """
    root = Path(root)
    worker_dir = root / "worker_inputs"
    truth_dir = root / "controller_truth"
    worker_dir.mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)

    worker_records: list[dict[str, str]] = []
    truth_records: dict[str, dict[str, object]] = {}
    for scene in _SCENES:
        sample_id = scene.scene_id.replace("scene", "sample")
        relative_image = f"{scene.split}/{sample_id}.png"
        _write_png(worker_dir / relative_image, scene.rectangles)
        worker_records.append(
            {
                "sample_id": sample_id,
                "source_scene_id": scene.scene_id,
                "split": scene.split,
                "image": relative_image,
                "transcript": scene.transcript,
            }
        )
        truth_records[sample_id] = {
            "objects": [
                {"label": rectangle.label, "box": list(rectangle.box)}
                for rectangle in scene.rectangles
            ],
            "text": scene.transcript,
        }

    (worker_dir / "manifest.json").write_text(
        json.dumps({"samples": worker_records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (truth_dir / "ground_truth.json").write_text(
        json.dumps(truth_records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return DemoDataset(
        root=root,
        worker_inputs=tuple(worker_records),
        controller_truth=truth_records,
    )


def _valid_box(value: object) -> tuple[float, float, float, float] | None:
    if type(value) is not list or len(value) != 4:
        return None
    coordinates: list[float] = []
    for item in value:
        if type(item) not in (int, float):  # rejects bool, Decimal, and numeric strings
            return None
        try:
            number = float(item)
        except OverflowError:
            return None
        if not math.isfinite(number):
            return None
        coordinates.append(number)
    x1, y1, x2, y2 = coordinates
    if x1 < 0 or y1 < 0 or x2 > IMAGE_WIDTH or y2 > IMAGE_HEIGHT or x1 >= x2 or y1 >= y2:
        return None
    return x1, y1, x2, y2


def _objects_match(expected: list[object], actual: list[object]) -> bool:
    """Order-independent one-to-one object matching with per-edge tolerance."""
    if len(expected) != len(actual):
        return False
    expected_items: list[tuple[str, tuple[float, float, float, float]]] = []
    actual_items: list[tuple[str, tuple[float, float, float, float]]] = []
    for item in expected:
        if type(item) is not dict or set(item) != {"label", "box"} or type(item["label"]) is not str:
            return False
        box = _valid_box(item["box"])
        if box is None:
            return False
        expected_items.append((item["label"], box))
    for item in actual:
        if type(item) is not dict or set(item) != {"label", "box"} or type(item["label"]) is not str:
            return False
        box = _valid_box(item["box"])
        if box is None:
            return False
        actual_items.append((item["label"], box))

    def assign(index: int, used: set[int]) -> bool:
        if index == len(expected_items):
            return True
        label, expected_box = expected_items[index]
        for candidate_index, (candidate_label, candidate_box) in enumerate(actual_items):
            if candidate_index in used or candidate_label != label:
                continue
            if all(
                abs(expected_edge - actual_edge) <= BOX_EDGE_TOLERANCE_PX
                for expected_edge, actual_edge in zip(expected_box, candidate_box)
            ):
                if assign(index + 1, used | {candidate_index}):
                    return True
        return False

    return assign(0, set())


def verify_output(expected: Mapping[str, object], candidate: object) -> VerificationResult:
    """Verify the exact ``objects`` + ``text`` contract without coercion.

    Object order is ignored; each box edge may differ by at most one pixel.
    Transcript text must match exactly. Unknown or missing fields are errors.
    """
    errors: list[str] = []
    if type(candidate) is not dict:
        return VerificationResult(False, ("output must be a JSON object",))
    if set(candidate) != {"objects", "text"}:
        errors.append("output must contain exactly 'objects' and 'text'")
    if "objects" not in candidate or "text" not in candidate:
        return VerificationResult(False, tuple(errors))

    expected_objects = expected.get("objects")
    actual_objects = candidate["objects"]
    if type(expected_objects) is not list:
        errors.append("controller truth is malformed")
    elif type(actual_objects) is not list:
        errors.append("objects must be a list")
    elif not _objects_match(expected_objects, actual_objects):
        errors.append("objects or pixel boxes do not match controller truth")

    expected_text = expected.get("text")
    actual_text = candidate["text"]
    if type(expected_text) is not str:
        errors.append("controller truth transcript is malformed")
    elif type(actual_text) is not str:
        errors.append("text must be a string")
    elif actual_text != expected_text:
        errors.append("transcript text does not match exactly")

    return VerificationResult(not errors, tuple(errors))
