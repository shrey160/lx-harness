"""Colour rule for object cutouts and generated backgrounds for composite photos (PROD-004 data plan v2).

Colour names come from HSV thresholds; backgrounds are wood, cloth or table
textures built only from the supplied `random.Random`, so they are repeatable.
"""
from __future__ import annotations

import colorsys
import math
import random
from collections import Counter

from PIL import Image, ImageDraw, ImageFilter

from .inventory import COLOURS  # noqa: F401 - every name returned here is in COLOURS

BACKGROUND_STYLES = ("wood", "cloth", "table")


def colour_name(r: int, g: int, b: int) -> str | None:
    """Name an RGB colour (0-255) as one of COLOURS, or None when washed-out or ambiguous."""
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    hue = h * 360
    if v < 0.2:
        return "black"
    if s < 0.15:
        return "white" if v > 0.85 else "grey"
    if s < 0.3:
        return None
    if hue < 15 or hue >= 345:
        if s < 0.5 and v > 0.7:
            return "pink"
        return "brown" if v < 0.45 and hue < 15 else "red"  # dark warm reds read as brown (chocolate, wood)
    if hue < 40:
        return "brown" if v < 0.6 else "orange"
    if hue < 70:
        return "brown" if v < 0.45 else "yellow"
    if hue < 170:
        return "green"
    if hue < 255:
        return "blue"
    if hue < 290:
        return "purple"
    return "pink"


def dominant_colour(image: Image.Image, min_share: float = 0.6, min_pixels: int = 50) -> str | None:
    """Most common colour name of the opaque pixels, or None if no name dominates enough."""
    img = image.convert("RGBA")
    img.thumbnail((64, 64), Image.NEAREST)
    counts: Counter = Counter()
    for r, g, b, a in img.getdata():
        if a >= 128:
            counts[colour_name(r, g, b)] += 1
    total = sum(counts.values())
    if not counts or total < min_pixels:
        return None
    name, n = counts.most_common(1)[0]
    if name is None or n / total < min_share:
        return None
    return name


def _jitter(rng: random.Random, base: tuple[int, int, int], spread: int) -> tuple[int, int, int]:
    return tuple(max(0, min(255, c + rng.randint(-spread, spread))) for c in base)  # type: ignore[return-value]


def _wood(width: int, height: int, rng: random.Random) -> Image.Image:
    base = _jitter(rng, (150, 110, 70), 8)
    img = Image.new("RGB", (width, height), base)
    draw = ImageDraw.Draw(img)
    for _ in range(max(1, height // 6)):
        y0 = rng.uniform(0, height)
        shade = rng.choice((-1, 1)) * rng.randint(12, 25)
        colour = tuple(max(0, min(255, c + shade)) for c in base)
        amp, freq, phase = rng.uniform(1, 4), rng.uniform(0.005, 0.02), rng.uniform(0, 2 * math.pi)
        pts = [(x, y0 + amp * math.sin(freq * x * 2 * math.pi + phase)) for x in range(0, width + 16, 16)]
        draw.line(pts, fill=colour, width=rng.randint(1, 2))
    return img.filter(ImageFilter.GaussianBlur(1))


def _cloth(width: int, height: int, rng: random.Random) -> Image.Image:
    base = rng.choice(((190, 205, 225), (225, 215, 195), (205, 205, 205), (190, 215, 195)))
    base = _jitter(rng, base, 4)
    dark = tuple(max(0, c - rng.randint(6, 12)) for c in base)
    img = Image.new("RGB", (width, height), base)
    draw = ImageDraw.Draw(img)
    for y in range(0, height, 4):
        draw.line((0, y, width, y), fill=dark)
    for x in range(0, width, 4):
        draw.line((x, 0, x, height), fill=dark)
    return img.filter(ImageFilter.GaussianBlur(0.6))


def _table(width: int, height: int, rng: random.Random) -> Image.Image:
    base = _jitter(rng, rng.choice(((225, 215, 195), (210, 210, 210))), 6)
    small = Image.new("RGB", (32, 24))
    px = small.load()
    for y in range(24):
        for x in range(32):
            d = min(1.0, math.hypot((x - 15.5) / 16, (y - 11.5) / 12) / math.sqrt(2))
            k = int(25 * d * d)
            px[x, y] = tuple(max(0, c - k) for c in base)
    return small.resize((width, height), Image.BICUBIC)


def background(width: int, height: int, rng: random.Random, style: str | None = None) -> Image.Image:
    """RGB background of exactly (width, height), deterministic for the same rng state."""
    style = style or rng.choice(BACKGROUND_STYLES)
    img = {"wood": _wood, "cloth": _cloth, "table": _table}[style](width, height, rng)
    # Subtle grain: a coarse noise field scaled up and blended in lightly.
    gw, gh = max(1, width // 8), max(1, height // 8)
    noise = Image.new("L", (gw, gh))
    noise.putdata([rng.randint(118, 138) for _ in range(gw * gh)])
    noise = noise.resize((width, height), Image.BICUBIC).convert("RGB")
    return Image.blend(img, noise, 0.08)
