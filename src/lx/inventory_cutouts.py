"""COCO object cutouts pasted on generated backgrounds (D5, PROD-004 data plan v2).

Real objects are cut along their COCO polygon and pasted, without overlap,
onto generated table, cloth or wood backgrounds. Every pasted object is
known, so truth is exact for count, colour_count and inventory requests
(`truth_scope: all`). A cutout carries a colour only when one colour clearly
dominates its pixels (`lx.cutout_art.dominant_colour`); colour requests only
use cutouts of the asked kind whose colour is known.

Cutout rules: one polygon, not crowd, at least MIN_SIDE px each way, clear of
the photo edge, no other labelled object over more than MAX_OVERLAP of its box,
no person anywhere in the photo, licence CC BY / no known copyright / US
government (BY-SA is left out because composites are derivatives).
"""
from __future__ import annotations

import random
from io import BytesIO

from .cutout_art import background, dominant_colour
from .inventory import MAX_PHOTOS, MIN_PHOTOS, REQUEST_TYPES, request_text, validate_truth
from .inventory_data import COCO_KINDS, _Dataset, normalize_photo
from .voicecount import VOICES

# COCO often labels bunches, heads or stacks as one instance for these, so one cutout is not one item.
GROUPED = {"banana", "broccoli", "carrot", "book"}
CUTOUT_KINDS = {name: kind for name, kind in dict(
    COCO_KINDS, **{"donut": "donut", "clock": "clock", "mouse": "mouse", "toothbrush": "toothbrush",
                   "sports ball": "ball", "frisbee": "frisbee", "handbag": "bag", "backpack": "backpack",
                   "umbrella": "umbrella", "potted plant": "plant"}).items() if kind not in GROUPED}
CUTOUT_LICENCES = {4, 7, 8}
MIN_SIDE = 64
EDGE = 2
MAX_OVERLAP = 0.05
MIN_FILL = 0.3  # mask area / box area; thinner masks are mostly broken outlines
MIN_VERTICES = 8  # coarser polygons are mostly crude scraps of the object
SCENE_SIZE = (1024, 768)
SCENE_PHOTOS = (3, 5)
OBJECTS_PER_PHOTO = (2, 5)
MAX_USES = 2  # a cutout may appear in two samples of one split group, never twice in a sample
# Colour pairs a person could name either way; a colour request never mixes them within the asked kind.
CONFUSABLE = ({"orange", "brown"}, {"orange", "yellow"}, {"orange", "red"}, {"red", "pink"}, {"red", "brown"},
              {"pink", "purple"}, {"blue", "purple"}, {"grey", "white"}, {"grey", "black"}, {"grey", "silver"},
              {"white", "silver"}, {"yellow", "gold"}, {"brown", "gold"}, {"yellow", "brown"})


def split_group(split):
    """Cutouts never cross between the user bundle (stream, check) and the hidden sets."""
    return "bundle" if split in ("stream", "check") else "hidden"


def _overlap(a, b):
    x = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    y = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return x * y


def cutout_candidates(annotations, licences=CUTOUT_LICENCES):
    """Clean cutouts in COCO annotations: [{ann_id, image_id, file_name, kind, bbox, polygon, license, ...}]."""
    categories = {c["id"]: c["name"] for c in annotations["categories"]}
    names = {lic["id"]: lic["name"] for lic in annotations.get("licenses", [])}
    images = {i["id"]: i for i in annotations["images"]}
    per_image = {}
    for a in annotations["annotations"]:
        per_image.setdefault(a["image_id"], []).append(a)
    found = []
    for image_id, anns in sorted(per_image.items()):
        image = images.get(image_id)
        if image is None or image["license"] not in licences:
            continue
        if any(categories.get(a["category_id"]) == "person" for a in anns):
            continue
        for a in anns:
            kind = CUTOUT_KINDS.get(categories.get(a["category_id"]))
            segmentation = a.get("segmentation")
            if kind is None or a.get("iscrowd") or type(segmentation) is not list or len(segmentation) != 1:
                continue
            x, y, w, h = a["bbox"]
            if (w < MIN_SIDE or h < MIN_SIDE or len(segmentation[0]) < 2 * MIN_VERTICES or a["area"] < MIN_FILL * w * h
                    or x < EDGE or y < EDGE or x + w > image["width"] - EDGE or y + h > image["height"] - EDGE):
                continue
            if any(_overlap(a["bbox"], b["bbox"]) > MAX_OVERLAP * w * h for b in anns if b is not a):
                continue
            found.append({"ann_id": a["id"], "image_id": image_id, "file_name": image["file_name"], "kind": kind,
                          "bbox": [x, y, w, h], "polygon": segmentation[0],
                          "license": names.get(image["license"], image["license"]),
                          "flickr_url": image.get("flickr_url"), "coco_url": image.get("coco_url")})
    return found


def extract_cutout(image_bytes, candidate):
    """RGBA crop of the candidate's box with everything outside its polygon transparent."""
    from PIL import Image, ImageDraw, ImageFilter

    with Image.open(BytesIO(image_bytes)) as source:
        photo = source.convert("RGB")
    mask = Image.new("L", photo.size, 0)
    points = candidate["polygon"]
    ImageDraw.Draw(mask).polygon([(points[i], points[i + 1]) for i in range(0, len(points) - 1, 2)], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(0.8))  # soften the pasted edge
    x, y, w, h = candidate["bbox"]
    box = (max(0, int(x) - 1), max(0, int(y) - 1), min(photo.width, int(x + w) + 2), min(photo.height, int(y + h) + 2))
    cutout = photo.crop(box)
    cutout.putalpha(mask.crop(box))
    return cutout


def prepare_cutouts(candidates, load_image):
    """Candidates with their RGBA `image` and rule-based `colour` (None when no colour dominates)."""
    prepared, cache = [], {}
    for candidate in candidates:
        if candidate["file_name"] not in cache:
            cache = {candidate["file_name"]: load_image(candidate["file_name"])}  # candidates come grouped by photo
        image = extract_cutout(cache[candidate["file_name"]], candidate)
        prepared.append(dict(candidate, image=image, colour=dominant_colour(image)))
    return prepared


def compose_photo(cutouts, rng, size=SCENE_SIZE):
    """JPEG of the cutouts pasted without overlap on a generated background; None when they do not fit."""
    from PIL import Image, ImageFilter

    scene = background(size[0], size[1], rng).convert("RGBA")
    placed = []
    for cutout in cutouts:
        image = cutout["image"]
        target = rng.uniform(150, 260) * (0.75 if len(cutouts) > 3 else 1)
        scale = min(target / max(image.size), 2.0)
        image = image.resize((max(8, round(image.width * scale)), max(8, round(image.height * scale))),
                             Image.Resampling.LANCZOS)
        for _ in range(200):
            x = rng.randint(16, size[0] - image.width - 16) if size[0] - image.width > 32 else None
            y = rng.randint(16, size[1] - image.height - 16) if size[1] - image.height > 32 else None
            if x is None or y is None:
                return None
            box = (x - 12, y - 12, image.width + 24, image.height + 24)
            if all(_overlap(box, other) == 0 for other in placed):
                break
        else:
            return None
        placed.append(box)
        alpha = image.getchannel("A")
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        shadow.putalpha(alpha.point(lambda v: v * 35 // 100).filter(ImageFilter.GaussianBlur(4)))
        scene.alpha_composite(shadow, (x + 5, y + 7))
        scene.alpha_composite(image, (x, y))
    out = BytesIO()
    scene.convert("RGB").filter(ImageFilter.GaussianBlur(0.4)).save(out, "JPEG", quality=rng.randint(82, 92))
    return normalize_photo(out.getvalue())


def _truth_items(objects, request_type):
    """Truth items for one photo: every pasted object, grouped by kind (and colour for colour requests)."""
    groups = {}
    for cutout in objects:
        key = (cutout["kind"], cutout["colour"] if request_type == "colour_count" else None)
        groups[key] = groups.get(key, 0) + 1
    return [dict({"kind": kind, "count": count}, **({"colour": colour} if colour else {}))
            for (kind, colour), count in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))]


def _plan_sample(request_type, pool, rng):
    """Photos as lists of cutouts plus (kind, colour) of the request, or None when the pool cannot serve it."""
    n_photos = rng.randint(*SCENE_PHOTOS)
    kind = colour = None
    if request_type == "colour_count":
        options = sorted({(c["kind"], c["colour"]) for c in pool if c["colour"]})
        # The asked kind must come in at least one other clearly different colour, so the colour matters.
        options = [(k, col) for k, col in options if sum((c["kind"], c["colour"]) == (k, col) for c in pool) >= 2
                   and any(c["kind"] == k and c["colour"] not in (None, col) and {col, c["colour"]} not in CONFUSABLE
                           for c in pool)]
        if not options:
            return None
        kind, colour = rng.choice(options)
        # Same-kind cutouts without a clear or with a confusable colour would make the answer debatable.
        pool = [c for c in pool if c["kind"] != kind
                or (c["colour"] and (c["colour"] == colour or {c["colour"], colour} not in CONFUSABLE))]
    elif request_type == "count":
        kinds = sorted({c["kind"] for c in pool if sum(d["kind"] == c["kind"] for d in pool) >= 3})
        if not kinds:
            return None
        kind = rng.choice(kinds)
    kinds = sorted({c["kind"] for c in pool})
    focus = set(rng.sample(kinds, min(len(kinds), rng.randint(3, 5)))) | ({kind} if kind else set())
    asked = [c for c in pool if kind and c["kind"] == kind and (colour is None or c["colour"] == colour)]
    others = [c for c in pool if c["kind"] in focus and c not in asked]
    rng.shuffle(asked)
    rng.shuffle(others)
    wants = [rng.randint(*OBJECTS_PER_PHOTO) for _ in range(n_photos)]
    k = rng.randint(2, min(len(asked), n_photos * 2)) if kind else 0  # asked objects spread over the photos
    if colour:  # at least one same-kind object of another colour, when there is one
        decoys = [c for c in others if c["kind"] == kind]
        others = decoys[:1] + [c for c in others if c not in decoys[:1]]
    fill = asked[:k] + others[:sum(wants) - k]
    if len(fill) < sum(wants):
        return None
    rng.shuffle(fill)
    photos, taken = [], 0
    for want in wants:
        photos.append(fill[taken:taken + want])
        taken += want
    return photos, kind, colour


def add_composite_samples(dataset_root, cutouts, count, *, request_type, split="heldout", seed=20261029,
                          prefix="mix"):
    """Build `count` composite samples of one request type from prepared cutouts.

    Each cutout appears at most once per sample, in at most MAX_USES samples,
    and only within one split group (bundle or hidden; see `split_group`).
    """
    if request_type not in REQUEST_TYPES:
        raise ValueError(f"request_type must be one of {REQUEST_TYPES}")
    dataset = _Dataset(dataset_root)
    group = split_group(split)
    uses, groups = {}, {}
    for sample_id, source in dataset.sources.items():
        for photo in source.get("photos", []):
            for ann_id in photo.get("ann_ids", []):
                uses[ann_id] = uses.get(ann_id, 0) + 1
                groups[ann_id] = split_group(dataset.split_of(sample_id))
    rng = random.Random(f"{seed}:mix:{request_type}:{split}")
    planned, attempts = [], 0
    while len(planned) < count:
        attempts += 1
        if attempts > 50 * max(1, count):
            raise ValueError(f"only {len(planned)} usable {request_type} composites for split {split}")
        pool = [c for c in cutouts if uses.get(c["ann_id"], 0) < MAX_USES and groups.get(c["ann_id"], group) == group]
        plan = _plan_sample(request_type, pool, rng)
        if plan is None:
            continue
        photos, kind, colour = plan
        jpegs = [compose_photo(objects, rng) for objects in photos]
        if any(j is None for j in jpegs):
            continue
        truth = {"request_type": request_type, "kind": kind, "colour": colour,
                 "photos": {str(n): _truth_items(objects, request_type) for n, objects in enumerate(photos, 1)}}
        try:
            validate_truth(truth)
        except ValueError:
            continue
        if not MIN_PHOTOS <= len(photos) <= MAX_PHOTOS:
            continue
        sample_id = f"{prefix}-{split}-{request_type.replace('_', '')}-{len(planned) + 1:02d}"
        source = {"origin": "coco-cutouts", "truth_scope": "all", "photos": [
            {"ann_ids": [c["ann_id"] for c in objects],
             "objects": [{"ann_id": c["ann_id"], "kind": c["kind"], "colour": c["colour"], "coco_image": c["file_name"],
                          "license": c["license"], "flickr_url": c["flickr_url"]} for c in objects]}
            for objects in photos]}
        planned.append((sample_id, jpegs, truth, request_text(truth, rng), rng.choice(VOICES), source))
        for objects in photos:
            for c in objects:
                uses[c["ann_id"]] = uses.get(c["ann_id"], 0) + 1
                groups[c["ann_id"]] = group
    # Every sample is planned before anything is written, so a shortfall leaves the dataset untouched.
    for sample_id, jpegs, truth, text, voice, source in planned:
        dataset.add(sample_id, split, jpegs, truth, text=text, voice=voice, source=source)
    dataset.save()
    return [p[0] for p in planned]


def select_photos(candidates, n_images, *, seed=20261029):
    """File names of up to `n_images` photos whose cutouts cover the kinds evenly (round robin over kinds)."""
    rng = random.Random(f"{seed}:select")
    by_kind = {}
    for c in candidates:
        by_kind.setdefault(c["kind"], set()).add(c["file_name"])
    queues = {kind: sorted(files) for kind, files in sorted(by_kind.items())}
    for files in queues.values():
        rng.shuffle(files)
    chosen = set()
    while len(chosen) < n_images and any(queues.values()):
        for kind in sorted(queues):
            while queues[kind] and queues[kind][-1] in chosen:
                queues[kind].pop()
            if queues[kind] and len(chosen) < n_images:
                chosen.add(queues[kind].pop())
    return sorted(chosen)
