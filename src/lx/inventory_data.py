"""Inventory datasets: user-made samples (D1), COCO-built samples (D2) and FSC-147 multi-photo counts (D4).

Layout of a dataset root (frozen by `finalize_inventory`):

- `worker_inputs/manifest.json`: samples [{sample_id, split, photos: [...],
  audio}] with paths under `worker_inputs/`; the only part a worker sees.
- `worker_inputs/media/<sample_id>/<k>.jpg`, `worker_inputs/media/<sample_id>.wav`,
  and optional `worker_inputs/media/<sample_id>/input.json` task data.
- `controller_truth/ground_truth.json` (truth per sample, `lx.inventory`
  schema), `requests.json` (request text and audio origin), `scripts.json`
  (requests still to be voiced by `scripts/synthesize_voicecount.py`),
  `sources.json` (origin, licence, attribution), `media.json` (hashes).

Intake normalises every photo (EXIF orientation applied, then all metadata
dropped, RGB JPEG, longest side at most 1024 px). User samples must show
everyday objects only (D-010): no people, faces, screens, documents or
addresses; intake cannot check this, so the user confirms it per run.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from io import BytesIO
from pathlib import Path

from .inventory import MAX_PHOTOS, MIN_PHOTOS, request_text, validate_truth
from .perception import wav_duration_ms
from .voicecount import VOICES
from .sample_input import read_input, staged_input

SPLITS = ("stream", "check", "heldout", "calibration")  # check: the user bundle's own check (E-B/E-C)
MAX_SIDE = 1024
JPEG_QUALITY = 90
_PHOTO = re.compile(r"([1-9])\.(jpg|jpeg|png)", re.I)
_SAMPLE = re.compile(r"sample-[A-Za-z0-9_-]{1,40}")


def normalize_photo(data, *, max_side=MAX_SIDE):
    """JPEG bytes with orientation applied and no metadata; raises ValueError for unreadable input."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in ("JPEG", "PNG", "MPO"):
                raise ValueError(f"photos must be JPEG or PNG, not {source.format}")
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError):
        raise ValueError("photo could not be read as an image (export HEIC as JPEG)") from None
    scale = max_side / max(image.size)
    if scale < 1:
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                             Image.Resampling.LANCZOS)
    out = BytesIO()
    image.save(out, "JPEG", quality=JPEG_QUALITY)
    return out.getvalue()


def check_sample_folder(folder, *, inventory=True, need_truth=True, min_photos=MIN_PHOTOS):
    """Validate a user sample folder; returns (photo paths in order, truth, wav path or None, text or None).

    `inventory` applies the inventory truth schema; other tasks' truth is the check file's business.
    A task with no check needs no truth, so a missing file gives None."""
    folder = Path(folder)
    photos_dir = folder / "photos"
    if not photos_dir.is_dir():
        raise ValueError(f"{folder.name}: missing photos/ folder")
    photos = {}
    for path in photos_dir.iterdir():
        match = _PHOTO.fullmatch(path.name)
        if not match:
            raise ValueError(f"{folder.name}: unexpected file photos/{path.name} (use 1.jpg ... 7.jpg)")
        if match.group(1) in photos:
            raise ValueError(f"{folder.name}: two files for photo {match.group(1)}")
        photos[match.group(1)] = path
    numbers = sorted(int(n) for n in photos)
    if not min_photos <= len(numbers) <= MAX_PHOTOS or numbers != list(range(1, len(numbers) + 1)):
        raise ValueError(f"{folder.name}: photos must be numbered 1..N with N from {min_photos} to {MAX_PHOTOS}")
    read_input(folder / "input.json")
    try:
        truth = json.loads((folder / "truth.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        if need_truth:
            raise ValueError(f"{folder.name}: missing truth.json") from None
        truth = None
    except ValueError as exc:
        raise ValueError(f"{folder.name}: truth.json is not valid JSON ({exc})") from None
    if inventory:
        try:
            validate_truth(truth)
        except ValueError as exc:
            raise ValueError(f"{folder.name}: {exc}") from None
        if len(truth["photos"]) != len(numbers):
            raise ValueError(f"{folder.name}: truth.json lists {len(truth['photos'])} photos, folder has {len(numbers)}")
    wav = folder / "request.wav"
    txt = folder / "request.txt"
    text = txt.read_text(encoding="utf-8").strip() if txt.is_file() else None
    if not wav.is_file() and not text:
        raise ValueError(f"{folder.name}: add request.wav (your voice) or request.txt (to be voiced)")
    if wav.is_file():
        try:
            wav_duration_ms(wav.read_bytes())
        except Exception:
            raise ValueError(f"{folder.name}: request.wav must be mono 16-bit PCM at 16 or 24 kHz, e.g. "
                             "ffmpeg -i in.m4a -ac 1 -ar 16000 -sample_fmt s16 request.wav") from None
    return [photos[str(n)] for n in numbers], truth, (wav if wav.is_file() else None), text


class _Dataset:
    """Staging access to an unfrozen dataset root."""

    def __init__(self, root):
        self.root = Path(root)
        self.inputs = self.root / "worker_inputs"
        self.controller = self.root / "controller_truth"
        if (self.controller / "media.json").exists():
            raise ValueError("dataset is frozen; build a new one instead of adding samples")
        self.inputs.mkdir(parents=True, exist_ok=True)
        self.controller.mkdir(parents=True, exist_ok=True)
        self.manifest = self._read(self.inputs / "manifest.json", {"samples": []})
        self.truth = self._read(self.controller / "ground_truth.json", {})
        self.requests = self._read(self.controller / "requests.json", {})
        self.scripts = self._read(self.controller / "scripts.json", {})
        self.sources = self._read(self.controller / "sources.json", {})

    @staticmethod
    def _read(path, default):
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    def split_of(self, sample_id):
        return next(s["split"] for s in self.manifest["samples"] if s["sample_id"] == sample_id)

    def add(self, sample_id, split, photos, truth, *, audio=None, text=None, voice=None, source, input_data=None):
        if split not in SPLITS:
            raise ValueError(f"split must be one of {SPLITS}")
        if sample_id in self.truth:
            raise ValueError(f"sample {sample_id} already exists")
        media = self.inputs / "media" / sample_id
        media.mkdir(parents=True)
        paths = []
        for number, data in enumerate(photos, 1):
            (media / f"{number}.jpg").write_bytes(data)
            paths.append(f"media/{sample_id}/{number}.jpg")
        if audio is not None:
            (self.inputs / "media" / f"{sample_id}.wav").write_bytes(audio)
        else:
            self.scripts[sample_id] = {"text": text, "voice": voice}
        self.manifest["samples"].append({"sample_id": sample_id, "split": split, "photos": paths,
                                         "audio": f"media/{sample_id}.wav"})
        if input_data is not None:
            (media / "input.json").write_text(json.dumps(input_data, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            self.manifest["samples"][-1]["input"] = f"media/{sample_id}/input.json"
        self.truth[sample_id] = truth
        self.requests[sample_id] = {"text": text, "audio": "recorded" if audio is not None else "kokoro"}
        self.sources[sample_id] = source

    def save(self):
        payloads = {self.inputs / "manifest.json": self.manifest, self.controller / "ground_truth.json": self.truth,
                    self.controller / "requests.json": self.requests, self.controller / "scripts.json": self.scripts,
                    self.controller / "sources.json": self.sources}
        for path, payload in payloads.items():
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def add_user_samples(dataset_root, source_root, *, need_truth=True, stream_count=6, calibration_count=0, seed=20261029, prefix="user",
                     inventory=True, min_photos=MIN_PHOTOS):
    """Copy user sample folders into the dataset with a seeded split.

    Folders are shuffled with `seed` (so the order they were made in cannot
    bias the split), then the first `stream_count` form the E-A stream, the
    next `calibration_count` the calibration split, and the rest held-out.
    """
    folders = sorted(p for p in Path(source_root).iterdir() if p.is_dir() and _SAMPLE.fullmatch(p.name))
    if not folders:
        raise ValueError("no sample-* folders found")
    if stream_count + calibration_count > len(folders):
        raise ValueError(f"only {len(folders)} sample folders for {stream_count} stream + {calibration_count} calibration")
    random.Random(f"{seed}:user-split").shuffle(folders)
    checked = [(folder, *check_sample_folder(folder, inventory=inventory, need_truth=need_truth, min_photos=min_photos),
                read_input(folder / "input.json")) for folder in folders]
    # Normalise everything before writing, so a bad photo leaves the dataset untouched.
    normalized = [[normalize_photo(path.read_bytes()) for path in paths] for _, paths, _, _, _, _ in checked]
    dataset = _Dataset(dataset_root)
    rng = random.Random(f"{seed}:user")
    added = []
    for index, ((folder, photo_paths, truth, wav, text, input_data), photos) in enumerate(zip(checked, normalized), 1):
        sample_id = f"{prefix}-{index:02d}"
        split = ("stream" if index <= stream_count else
                 "calibration" if index <= stream_count + calibration_count else "heldout")
        dataset.add(sample_id, split, photos, truth,
                    audio=wav.read_bytes() if wav else None, text=text,
                    voice=None if wav else rng.choice(VOICES), input_data=input_data,
                    source={"origin": "user", "folder": folder.name,
                            "original_sha256": [hashlib.sha256(p.read_bytes()).hexdigest() for p in photo_paths]})
        added.append(sample_id)
    dataset.save()
    return added


# --- D2: COCO count samples ---------------------------------------------------

# COCO category -> kind for everyday movable objects.
COCO_KINDS = {"cup": "cup", "bottle": "bottle", "bowl": "bowl", "wine glass": "glass", "fork": "fork",
              "knife": "knife", "spoon": "spoon", "banana": "banana", "apple": "apple", "orange": "orange",
              "book": "book", "scissors": "scissors", "teddy bear": "teddy bear", "vase": "vase", "remote": "remote"}
# COCO licence ids allowing reuse with attribution: CC BY, CC BY-SA, no known copyright, US government.
COCO_LICENCES = {4, 5, 7, 8}
# Instances smaller than 40x40 px (on ~640 px COCO photos) are hard to see, so their counts are
# unreliable truth; photos with any labelled person are skipped to match the D-010 user-photo rule.
MIN_AREA = 40 * 40
MAX_PER_PHOTO = 15


def coco_index(annotations):
    """Per image: licence, size, url, people and {kind: (count, ok)}; ok is False for crowd or tiny instances."""
    categories = {c["id"]: c["name"] for c in annotations["categories"]}
    images = {i["id"]: {"file_name": i["file_name"], "license": i["license"], "size": [i["width"], i["height"]],
                        "url": i.get("coco_url"), "flickr_url": i.get("flickr_url"), "people": 0, "kinds": {}}
              for i in annotations["images"]}
    for a in annotations["annotations"]:
        name = categories.get(a["category_id"])
        if name == "person" and a["image_id"] in images:
            images[a["image_id"]]["people"] += 1
        if name not in COCO_KINDS or a["image_id"] not in images:
            continue
        kinds = images[a["image_id"]]["kinds"]
        count, ok = kinds.get(COCO_KINDS[name], (0, True))
        kinds[COCO_KINDS[name]] = (count + 1, ok and not a.get("iscrowd") and a["area"] >= MIN_AREA)
    return images


def add_coco_samples(dataset_root, annotations, load_image, count, *, split="heldout", seed=20261029,
                     prefix="coco", licences=None):
    """Build `count` count-requests over COCO photos; each photo is used once.

    A sample asks about one kind across 3-7 photos; at most one photo lacks
    the kind entirely. Photos are skipped when that kind has crowd or tiny
    instances, when they have more than MAX_PER_PHOTO of it, when any person
    is labelled in them, or when their licence is not reusable. Truth lists only the asked kind, so D2 serves
    count requests only (`truth_scope: target`).
    """
    names = {lic["id"]: lic["name"] for lic in annotations.get("licenses", [])}
    images = coco_index(annotations)
    allowed = COCO_LICENCES if licences is None else set(licences)
    rng = random.Random(f"{seed}:coco:{split}")
    usable = sorted(i for i, image in images.items() if image["license"] in allowed and not image["people"])
    rng.shuffle(usable)
    dataset = _Dataset(dataset_root)
    used = {int(s["coco_id"]) for src in dataset.sources.values() for s in src.get("photos", []) if "coco_id" in s}
    kinds = sorted(set(COCO_KINDS.values()))
    added, planned = [], []
    attempts = 0
    while len(added) < count:
        attempts += 1
        if attempts > 50 * max(1, count):
            raise ValueError(f"only {len(added)} usable COCO samples for split {split}")
        kind = rng.choice(kinds)
        n_photos = rng.randint(MIN_PHOTOS, MAX_PHOTOS)
        with_kind = [i for i in usable if i not in used and kind in images[i]["kinds"]
                     and images[i]["kinds"][kind][1] and images[i]["kinds"][kind][0] <= MAX_PER_PHOTO]
        without = [i for i in usable if i not in used and kind not in images[i]["kinds"]]
        empty = 1 if without and rng.random() < 0.3 else 0
        if len(with_kind) < n_photos - empty:
            continue
        chosen = with_kind[:n_photos - empty] + without[:empty]
        rng.shuffle(chosen)
        truth = {"request_type": "count", "kind": kind, "colour": None,
                 "photos": {str(k): ([{"kind": kind, "count": images[i]["kinds"][kind][0]}] if kind in images[i]["kinds"]
                                     else []) for k, i in enumerate(chosen, 1)}}
        validate_truth(truth)
        sample_id = f"{prefix}-{split}-{len(added) + 1:02d}"
        planned.append((sample_id, chosen, truth, request_text(truth, rng), rng.choice(VOICES)))
        used.update(chosen)
        added.append(sample_id)
    # Every sample is planned before anything is written, so a shortfall leaves the dataset untouched.
    for sample_id, chosen, truth, text, voice in planned:
        photos = [normalize_photo(load_image(images[i]["file_name"])) for i in chosen]
        dataset.add(sample_id, split, photos, truth, text=text, voice=voice,
                    source={"origin": "coco", "truth_scope": "target",
                            "photos": [{"coco_id": i, "file_name": images[i]["file_name"],
                                        "license": names.get(images[i]["license"], images[i]["license"]),
                                        "flickr_url": images[i]["flickr_url"]} for i in chosen]})
    dataset.save()
    return added


# --- D4: FSC-147 multi-photo count samples (data plan v2) ---------------------

# FSC-147 class -> kind: everyday movable objects only (no animals, scenery or trays).
FSC_KINDS = {"apples": "apple", "pens": "pen", "cups": "cup", "eggs": "egg", "books": "book", "pencils": "pencil",
             "oranges": "orange", "tomatoes": "tomato", "bottle caps": "bottle cap", "markers": "marker",
             "coins": "coin", "cans": "can", "shoes": "shoe", "balls": "ball", "bowls": "bowl", "plates": "plate",
             "peaches": "peach", "potatoes": "potato", "marbles": "marble", "candles": "candle",
             "strawberries": "strawberry", "boxes": "box", "watches": "watch", "kiwis": "kiwi",
             "bananas": "banana", "crayons": "crayon", "cupcakes": "cupcake", "lipstick": "lipstick",
             "sunglasses": "sunglasses", "spoon": "spoon", "toilet paper rolls": "toilet paper roll",
             "flower pots": "flower pot", "sticky notes": "sticky note", "milk cartons": "milk carton",
             "sauce bottles": "bottle", "alcohol bottles": "bottle", "biscuits": "biscuit", "macarons": "macaron"}
# Kinds that can show up in each other's photos unlabelled; a "none here" photo never comes from the same family.
FSC_FAMILIES = ({"pen", "pencil", "marker", "crayon"}, {"cup", "bowl", "plate", "spoon"},
                {"bottle", "can", "milk carton", "bottle cap"}, {"book"},
                {"apple", "orange", "peach", "tomato", "kiwi", "banana", "strawberry", "potato"},
                {"cupcake", "biscuit", "macaron"})
FSC_DOTS = (3, 40)
FSC_PHOTOS = (3, 5)
NEAR_DUPLICATE_BITS = 10  # FSC-147 holds some crops of one scene; same-sample photos must differ


def _photo_hash(data):
    """64-bit difference hash, for spotting two photos of the same scene."""
    from PIL import Image

    with Image.open(BytesIO(data)) as image:
        small = image.convert("L").resize((9, 8), Image.Resampling.BILINEAR)
        pixels = list(small.getdata())
    return sum(1 << i for i, (a, b) in enumerate((pixels[r * 9 + c], pixels[r * 9 + c + 1])
                                                 for r in range(8) for c in range(8)) if a > b)


def _family(kind):
    return next((f for f in FSC_FAMILIES if kind in f), {kind})


def add_fsc_samples(dataset_root, fsc_root, count, *, split="heldout", seed=20261029, prefix="fsc", exclude=()):
    """Build `count` count-requests over FSC-147 photos; each photo is used once across the dataset.

    A sample asks about one kind across 3-5 photos of that class with 3-40
    dots each; sometimes one more photo comes from an unrelated class as a
    "none here" photo. Truth is the per-photo dot count of the asked kind
    (`truth_scope: target`). `exclude` names images not to use (voicecount).
    Kinds not yet used in this call are preferred, so a split stays varied.
    """
    fsc_root = Path(fsc_root)
    annotations = json.loads((fsc_root / "annotation_FSC147_384.json").read_text(encoding="utf-8"))
    classes = {}
    for line in (fsc_root / "ImageClasses_FSC147.txt").read_text(encoding="utf-8").splitlines():
        if line.strip():
            image, name = line.split("\t", 1)
            classes[image.strip()] = name.strip()
    dataset = _Dataset(dataset_root)
    used = set(exclude) | {p["fsc_image"] for src in dataset.sources.values() for p in src.get("photos", [])
                           if "fsc_image" in p}
    by_kind = {}
    for image, name in sorted(classes.items()):
        dots = len(annotations.get(image, {}).get("points", []))
        if name in FSC_KINDS and image not in used and FSC_DOTS[0] <= dots <= FSC_DOTS[1]:
            by_kind.setdefault(FSC_KINDS[name], []).append((image, dots))
    rng = random.Random(f"{seed}:fsc:{split}")
    for images in by_kind.values():
        rng.shuffle(images)
    hashes = {}

    def photo_hash(image):
        if image not in hashes:
            hashes[image] = _photo_hash((fsc_root / "images" / image).read_bytes())
        return hashes[image]

    def distinct(image, chosen):
        return all(bin(photo_hash(image) ^ photo_hash(other)).count("1") > NEAR_DUPLICATE_BITS for other in chosen)

    planned, kinds_used, attempts = [], set(), 0
    while len(planned) < count:
        attempts += 1
        if attempts > 50 * max(1, count):
            raise ValueError(f"only {len(planned)} usable FSC-147 samples for split {split}")
        n_photos = rng.randint(*FSC_PHOTOS)
        ready = sorted(k for k, images in by_kind.items() if sum(i not in used for i, _ in images) >= n_photos)
        fresh = [k for k in ready if k not in kinds_used]
        if not ready:
            continue
        kind = rng.choice(fresh or ready)
        chosen = []
        for image, dots in by_kind[kind]:
            if len(chosen) < n_photos and image not in used and distinct(image, [c for c, _ in chosen]):
                chosen.append((image, dots))
        if len(chosen) < n_photos:
            continue
        if rng.random() < 0.3 and n_photos < MAX_PHOTOS:
            others = [(i, 0) for k, images in sorted(by_kind.items()) if k not in _family(kind)
                      for i, _ in images if i not in used]
            if others:
                chosen.append(rng.choice(others))
        rng.shuffle(chosen)
        truth = {"request_type": "count", "kind": kind, "colour": None,
                 "photos": {str(n): ([{"kind": kind, "count": dots}] if dots else [])
                            for n, (_, dots) in enumerate(chosen, 1)}}
        validate_truth(truth)
        sample_id = f"{prefix}-{split}-{len(planned) + 1:02d}"
        planned.append((sample_id, chosen, truth, request_text(truth, rng), rng.choice(VOICES)))
        used.update(image for image, _ in chosen)
        kinds_used.add(kind)
    # Every sample is planned before anything is written, so a shortfall leaves the dataset untouched.
    for sample_id, chosen, truth, text, voice in planned:
        photos = [normalize_photo((fsc_root / "images" / image).read_bytes()) for image, _ in chosen]
        dataset.add(sample_id, split, photos, truth, text=text, voice=voice,
                    source={"origin": "fsc147", "truth_scope": "target",
                            "photos": [{"fsc_image": image, "class": classes[image], "dots": dots if dots else None}
                                       for image, dots in chosen]})
    dataset.save()
    return [sample_id for sample_id, *_ in planned]


# --- freeze and load ---------------------------------------------------------

def _media_hashes(root, manifest):
    hashes = {}
    for sample in manifest["samples"]:
        staged_input(Path(root) / "worker_inputs", sample)
        for path in sample["photos"] + [sample["audio"]] + ([sample["input"]] if "input" in sample else []):
            hashes[path] = hashlib.sha256((Path(root) / "worker_inputs" / path).read_bytes()).hexdigest()
    return hashes


def finalize_inventory(root, *, inventory=True):
    """Check every photo and audio file and record hashes; the dataset is then frozen."""
    root = Path(root)
    manifest = json.loads((root / "worker_inputs" / "manifest.json").read_text(encoding="utf-8"))
    truth = json.loads((root / "controller_truth" / "ground_truth.json").read_text(encoding="utf-8"))
    for sample in manifest["samples"]:
        if inventory:
            validate_truth(truth[sample["sample_id"]])
            if len(sample["photos"]) != len(truth[sample["sample_id"]]["photos"]):
                raise ValueError(f"{sample['sample_id']}: photo count does not match truth")
        audio = root / "worker_inputs" / sample["audio"]
        if not audio.is_file():
            raise ValueError(f"{sample['sample_id']}: audio missing; run the speech synthesis first")
        wav_duration_ms(audio.read_bytes())
    hashes = _media_hashes(root, manifest)
    (root / "controller_truth" / "media.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    return hashes


def load_inventory_dataset(root):
    """(manifest samples, truth, requests); refuses a dataset whose media changed since freezing."""
    root = Path(root)
    manifest = json.loads((root / "worker_inputs" / "manifest.json").read_text(encoding="utf-8"))
    controller = root / "controller_truth"
    recorded = json.loads((controller / "media.json").read_text(encoding="utf-8"))
    if _media_hashes(root, manifest) != recorded:
        raise ValueError("dataset media changed since it was frozen")
    truth = json.loads((controller / "ground_truth.json").read_text(encoding="utf-8"))
    requests = json.loads((controller / "requests.json").read_text(encoding="utf-8"))
    return tuple(manifest["samples"]), truth, requests


# --- D3: synthetic shape samples (offline previews and tests only) -----------

def _tone(index, rate=16000):
    """A short distinct WAV per sample; previews map audio hashes to request texts."""
    import struct
    import wave
    out = BytesIO()
    with wave.open(out, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(rate)
        frames = rate // 2 + index * 160
        clip.writeframes(b"".join(struct.pack("<h", (i * (index + 3)) % 2000 - 1000) for i in range(frames)))
    return out.getvalue()


def build_synthetic_dataset(root, count, *, stream_count=None, seed=20261029, types=("count", "colour_count")):
    """Frozen D3 dataset of shape scenes plus SAM-double scenes keyed by the stored JPEG hashes.

    Returns (scenes, requests). Request audio is a placeholder tone; previews
    transcribe it from the controller request texts.
    """
    from .inventory import GRID, CELL, synthetic_sample
    from .perception import media_hash

    if Path(root).exists() and any(Path(root).iterdir()):
        raise ValueError("use an empty dataset directory")
    dataset = _Dataset(root)
    scenes, index, added = {}, 0, 0
    stream_count = count if stream_count is None else stream_count
    while added < count:
        index += 1
        photos, truth, text, layouts = synthetic_sample(seed, index)
        if truth["request_type"] not in types:
            continue
        added += 1
        jpegs = [normalize_photo(p) for p in photos]
        for data, placed in zip(jpegs, layouts):
            scenes[media_hash(data)] = {"size": [GRID[0] * CELL, GRID[1] * CELL], "items": placed}
        dataset.add(f"synth-{added:02d}", "stream" if added <= stream_count else "heldout", jpegs, truth,
                    audio=_tone(added), text=text, source={"origin": "synthetic", "seed": seed, "index": index})
    dataset.save()
    finalize_inventory(root)
    return scenes, dataset.requests


def inventory_dataset_hash(root):
    """One approval hash over the frozen manifest, truth, requests and media hashes."""
    root = Path(root)
    parts = {name: json.loads(path.read_text(encoding="utf-8")) for name, path in (
        ("manifest", root / "worker_inputs" / "manifest.json"),
        ("truth", root / "controller_truth" / "ground_truth.json"),
        ("requests", root / "controller_truth" / "requests.json"),
        ("media", root / "controller_truth" / "media.json"))}
    load_inventory_dataset(root)  # refuses changed media
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()
