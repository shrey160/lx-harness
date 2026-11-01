"""Build the goods receiving audit samples (P6c, D-027) in three steps.

  build  five sample folders from COCO cutouts: photos, paperwork and truth.json
  voice  the supervisors' notes with Kokoro, in the separate tools environment
  sheet  one page showing every sample with its answers, for a person to look over

Run from main_LX with PYTHONPATH=src. Nothing here spends money or calls a
provider. Everything is seeded, so held-out samples can be added later as more
plans. Each truth box is the visible part of a pasted object, in the pixels the
worker sees. Credits are worked out from each plan, never typed.
"""
from __future__ import annotations

import argparse
import base64
import html
import importlib.util
from io import BytesIO
import json
import math
from pathlib import Path
import random
import shutil
import subprocess
import sys

from lx.cutout_art import background
from lx.inventory_cutouts import SCENE_SIZE, _overlap
from lx.inventory_data import normalize_photo

HERE = Path(__file__).parent
LX = Path(".lx")
SAMPLES = LX/"workspaces"/"receiving-audit"/"samples"
WORK = LX/"receiving-audit-build"
SHEET = LX/"views"/"receiving-audit.html"
SEED = 20261031
MIN_VISIBLE = 0.7  # an object hidden further than this could not fairly be asked for
WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven")
ALIASES = {"mouse": ["computer mouse"], "teddy bear": ["bear", "teddy"], "plant": ["potted plant", "pot plant"],
           "remote": ["remote control"], "scissors": ["pair of scissors"]}
BUYER, SUPPLIER = "Harbour Street Homeware", "Northgate Wholesale"

# paper: quantity on the purchase order. ordered: what stands now, if the note changed it. shipped: the slip's claim, if it
# differs. backordered: what the slip says is still to come. shots: how many arrived in each delivery photo. substitute: what
# came instead, by photo. old: the outdated price. avoid: cutouts unfair in this plan alone; rejects.json would reshuffle every sample.
PLANS = [
    {"name": "order-changed", "voice": "af_sarah", "date": "6 April", "overlap": 0.25, "extras": {"plate": 3.10, "lamp": 22.00},
     "note": "Hey, a quick note on this delivery before you start. We phoned the supplier on Tuesday and raised the cups from six to twelve, "
             "then called back on Thursday and settled on nine, so nine is what is on order now. On that same call we cancelled the clocks "
             "completely, so the order for those is zero. Nobody updated the purchase order for any of this. Everything else stands, "
             "but something else may have come in place of some of the bottles. "
             "Check what came in and put together the claim for anything we are owed. Thanks.",
     "lines": [{"item": "cup", "paper": 6, "ordered": 9, "price": 4.50, "shots": [4, 4]},
               {"item": "bowl", "paper": 8, "price": 6.25, "shots": [4, 3]},
               {"item": "clock", "paper": 3, "ordered": 0, "price": 18.00, "shots": [1, 1]},
               {"item": "bottle", "paper": 5, "price": 3.25, "shots": [2, 1], "substitute": {"item": "vase", "shots": [1, 1]}}]},
    {"name": "slip-overclaims", "voice": "am_adam", "date": "13 April", "overlap": 0.25, "discount": 0.10, "extras": {"keyboard": 31.00, "torch": 8.75},
     "note": "Hi, this delivery is from our usual supplier. Under this quarter's agreement we get ten percent off their list prices, so work "
             "out any credit at the list price less ten percent. The list has not been reprinted, so do that sum yourself. One more thing: do "
             "not claim for anything the packing slip marks as back-ordered, because that is still on its way next week. Anything else that is "
             "missing is fair game. Send me the claim when you are done. Thanks.",
     "lines": [{"item": "apple", "paper": 7, "shipped": 7, "price": 1.20, "shots": [3, 2]},
               {"item": "orange", "paper": 8, "shipped": 8, "price": 0.90, "shots": [4, 3]},
               {"item": "donut", "paper": 6, "shipped": 4, "backordered": 2, "price": 2.50, "shots": [1, 2]},
               {"item": "bowl", "paper": 4, "price": 6.00, "shots": [2, 2]}]},
    {"name": "substitution", "voice": "bf_emma", "date": "20 April", "overlap": 0, "free": ["clock"], "extras": {"tray": 7.40, "candle": 2.90}, "avoid": [1671607],
     "note": "Hello, a note on today's delivery. We have not agreed to any substitutions on this order, so if something other than what we "
             "ordered has turned up, it counts against the line. The clocks are a free promotional line this month, so claim nothing on them, "
             "however many came. Everything else follows the normal rule. Check it all and send me the claim, please.",
     "lines": [{"item": "bottle", "paper": 6, "price": 3.25, "shots": [1, 1], "substitute": {"item": "vase", "shots": [2, 2]}},
               {"item": "bowl", "paper": 5, "price": 6.00, "shots": [3, 2]},
               {"item": "clock", "paper": 2, "price": 16.00, "shots": [1, 0]},
               {"item": "plant", "paper": 4, "price": 8.50, "shots": [1, 3]}]},
    {"name": "retake", "voice": "bm_george", "date": "27 April", "overlap": 0, "retake": 1, "extras": {"pear": 0.95, "stapler": 9.20},
     "note": "Hi, before you count anything, one thing about the photos. Photos {original} and {retake} show the same shelf. I knocked things "
             "around after taking photo {original}, so I took it again. Use photo {retake} and leave photo {original} out completely, otherwise "
             "you will count that shelf twice. Nothing else has changed on the order. Check it against the paperwork and send me the claim when it is ready. Thanks.",
     "lines": [{"item": "apple", "paper": 8, "price": 0.80, "shots": [3, 4]},
               {"item": "orange", "paper": 5, "price": 0.70, "shots": [3, 2]},
               {"item": "donut", "paper": 6, "price": 1.50, "shots": [2, 4]},
               {"item": "scissors", "paper": 3, "price": 6.50, "shots": [1, 1]}]},
    {"name": "price-rule", "voice": "af_bella", "date": "4 May", "overlap": 0.2, "min_short": 0.10, "extras": {"mirror": 19.00, "towel": 5.75},
     "note": "Hello, two things on this one. There are two price lists in the pack. Use the April list, because the March list no longer applies "
             "to this order. Also, the supplier only accepts claims on real shortfalls, so only claim a line if it is short by more than ten percent "
             "of the quantity ordered. Anything smaller, still mark it, but the credit is zero. Send me the claim when it is done.",
     "lines": [{"item": "donut", "paper": 20, "price": 1.40, "old": 1.25, "shots": [7, 6, 6]},
               {"item": "vase", "paper": 4, "price": 15.00, "old": 14.00, "shots": [1, 1, 0]},
               {"item": "cup", "paper": 10, "price": 5.20, "old": 4.90, "shots": [3, 2, 3]},
               {"item": "plant", "paper": 6, "price": 9.00, "old": 8.60, "shots": [2, 2, 2]}]},
]


def ordered(line):
    return line.get("ordered", line["paper"])


def credit(line, plan):
    """What this line earns under the contract's rule and the plan's note."""
    short = ordered(line)-sum(line["shots"])-line.get("backordered", 0)
    if short <= 0 or line["item"] in plan.get("free", ()) or short/ordered(line) <= plan.get("min_short", 0):
        return 0
    return round(short*line["price"]*(1-plan.get("discount", 0)), 2)


def compose(objects, rng, overlap, backdrop):
    """(JPEG, boxes) of these objects, or None if a try does not fit. Later objects cover earlier ones by at most `overlap`."""
    from PIL import Image, ImageChops, ImageFilter

    width, height = SCENE_SIZE
    scene = background(width, height, random.Random(backdrop)).convert("RGBA")
    target = min(260, math.sqrt(0.45*width*height/len(objects)))
    margin = 0 if overlap else 12
    placed, masks, areas = [], [], []
    for cutout in objects:
        source = cutout["image"]
        scale = min(target*rng.uniform(0.85, 1.05)/max(source.size), 2.0)
        image = source.resize((max(8, round(source.width*scale)), max(8, round(source.height*scale))), Image.Resampling.LANCZOS)
        for _ in range(300):
            x, y = rng.randint(16, width-image.width-16), rng.randint(16, height-image.height-16)
            box = (x-margin, y-margin, image.width+2*margin, image.height+2*margin)
            if all(_overlap(box, other) <= overlap*min(box[2]*box[3], other[2]*other[3]) for other in placed):
                break
        else:
            return None
        placed.append(box)
        alpha = image.getchannel("A")
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        shadow.putalpha(alpha.point(lambda v: v*35//100).filter(ImageFilter.GaussianBlur(4)))
        scene.alpha_composite(shadow, (x+5, y+7))
        scene.alpha_composite(image, (x, y))
        solid = Image.new("L", (width, height), 0)
        solid.paste(alpha.point(lambda v: 255 if v >= 128 else 0), (x, y))
        masks = [ImageChops.subtract(mask, solid) for mask in masks]+[solid]
        areas.append(solid.histogram()[255])
    if any(mask.histogram()[255] < MIN_VISIBLE*area for mask, area in zip(masks, areas)):
        return None
    out = BytesIO()
    scene.convert("RGB").filter(ImageFilter.GaussianBlur(0.4)).save(out, "JPEG", quality=rng.randint(82, 92))
    edges = [mask.getbbox() for mask in masks]
    return normalize_photo(out.getvalue()), [[left, top, right-1, bottom-1] for left, top, right, bottom in edges]


def paper(title, details, columns, rows, rng):
    """A JPEG of one printed page, slightly crooked on a desk."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    page = Image.new("RGB", (880, 680), (250, 248, 241))
    draw = ImageDraw.Draw(page)
    large, small = ImageFont.load_default(size=38), ImageFont.load_default(size=26)
    draw.text((44, 34), title, fill=(25, 25, 30), font=large)
    for index, detail in enumerate(details):
        draw.text((44, 94+index*34), detail, fill=(70, 70, 75), font=small)
    top = 112+len(details)*34
    draw.text((44, top), columns[0], fill=(25, 25, 30), font=small)
    draw.text((620, top), columns[1], fill=(25, 25, 30), font=small)
    draw.line((44, top+38, 836, top+38), fill=(90, 90, 95), width=2)
    for index, (name, value) in enumerate(rows):
        draw.text((44, top+50+index*40), name, fill=(35, 35, 40), font=small)
        draw.text((620, top+50+index*40), value, fill=(35, 35, 40), font=small)
    desk = Image.new("RGB", SCENE_SIZE, (92, 74, 58))
    page = page.convert("RGBA").rotate(rng.uniform(-1.5, 1.5), expand=True, resample=Image.Resampling.BICUBIC)
    desk.paste(page, ((desk.width-page.width)//2, (desk.height-page.height)//2), page)
    out = BytesIO()
    desk.filter(ImageFilter.GaussianBlur(0.4)).save(out, "JPEG", quality=90)
    return normalize_photo(out.getvalue())


def paperwork(plan, number, rng):
    """{document name: JPEG} for this plan."""
    lines = plan["lines"]
    head = (f"From: {BUYER}", f"To: {SUPPLIER}", f"Date: {plan['date']}")
    names = lambda text: text.capitalize()
    pages = {"order": paper(f"Purchase order PO-{4100+number}", head, ("Item", "Quantity"), [(names(line["item"]), str(line["paper"])) for line in lines], rng),
             "slip": paper("Packing slip", (f"Supplier: {SUPPLIER}", f"Against order PO-{4100+number}"), ("Item", "Shipped"),
                           [(names(line["item"]), str(line.get("shipped", line["paper"]))+(f", {line['backordered']} back-ordered" if "backordered" in line else ""))
                            for line in lines], rng)}
    def prices(key, month):
        rows = {line["item"]: line[key] for line in lines}|plan["extras"]
        return paper(f"Price list, {month}", (f"Supplier: {SUPPLIER}", "Unit prices in dollars"), ("Item", "Unit price"),
                     [(names(item), f"{price:.2f}") for item, price in sorted(rows.items())], rng)
    if "old" in lines[0]:
        pages["old prices"] = prices("old", "March")
    pages["prices"] = prices("price", "April")
    return pages


def _shots(plan):
    """[[(line index, kind)], ...]: which objects each delivery photo holds, with the line they count for."""
    photos = [[] for _ in plan["lines"][0]["shots"]]
    for index, line in enumerate(plan["lines"]):
        for shot, count in enumerate(line["shots"]):
            photos[shot] += [(index, line["item"])]*count
        for shot, count in enumerate((line.get("substitute") or {"shots": ()})["shots"]):
            photos[shot] += [(index, line["substitute"]["item"])]*count
    return photos


def make_sample(plan, number, pools, seed=SEED):
    """{photos: [JPEG, ...], truth, note, layout} for one plan. pools maps a kind to prepared cutouts."""
    rng = random.Random(f"{seed}:{plan['name']}")
    shots, spare = _shots(plan), {kind: rng.sample(pools[kind], len(pools[kind])) for kind in pools}
    spare = {kind: [cutout for cutout in cutouts if cutout["ann_id"] not in plan.get("avoid", ())] for kind, cutouts in spare.items()}
    filled =[[(index, spare[kind].pop()) for index, kind in shot] for shot in shots]
    if plan.get("retake") is not None:
        filled.append(list(filled[plan["retake"]]))
    delivery = []
    for position, shot in enumerate(filled):
        backdrop = f"{seed}:{plan['name']}:{plan.get('retake') if position == len(shots) else position}"
        for _ in range(200):
            rng.shuffle(shot)
            built = compose([cutout for _, cutout in shot], rng, plan["overlap"], backdrop)
            if built:
                break
        else:
            raise ValueError(f"{plan['name']}: photo {position+1} does not fit")
        delivery.append({"jpeg": built[0], "shot": shot, "boxes": built[1]})
    pages = paperwork(plan, number, rng)
    order = [("page", name) for name in pages]+[("shot", index) for index in range(len(delivery))]
    rng.shuffle(order)
    numbers = {entry: position for position, entry in enumerate(order, 1)}
    boxes = [[] for _ in plan["lines"]]
    for index, photo in enumerate(delivery):
        if index != plan.get("retake"):
            for (line, _), box in zip(photo["shot"], photo["boxes"]):
                boxes[line].append({"photo": numbers[("shot", index)], "box": box})
    lines = []
    for line, found in zip(plan["lines"], boxes):
        substitute = line.get("substitute")
        lines.append({"item": line["item"], "ordered": ordered(line), "received": sum(line["shots"]),
                      "substitute": {"item": substitute["item"], "count": sum(substitute["shots"])} if substitute else None,
                      "credit": credit(line, plan), "boxes": found})
    truth = {"lines": lines, "total_claim": round(sum(line["credit"] for line in lines), 2),
             "aliases": {item: names for item, names in ALIASES.items() if any(line["item"] == item for line in lines)}}
    note = plan["note"]
    if plan.get("retake") is not None:
        note = note.format(original=WORDS[numbers[("shot", plan["retake"])]], retake=WORDS[numbers[("shot", len(shots))]])
    photos = [pages[key] if kind == "page" else delivery[key]["jpeg"] for kind, key in order]
    layout = [{"page": key} if kind == "page" else {"delivery": key, "objects": [[cutout["kind"], box] for (_, cutout), box in zip(delivery[key]["shot"], delivery[key]["boxes"])],
                                                    "ignored": key == plan.get("retake"), "ann_ids": [cutout.get("ann_id") for _, cutout in delivery[key]["shot"]]}
              for kind, key in order]
    return {"photos": photos, "truth": truth, "note": note, "layout": layout}


def load_check():
    spec = importlib.util.spec_from_file_location("receiving_audit_check", HERE/"check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build(args):
    sys.path.insert(0, str(HERE.parent))
    import build_inventory_mix

    if SAMPLES.exists():
        raise SystemExit(f"{SAMPLES} exists; remove it to rebuild")
    plans = [{**plan, **({"overlap": args.overlap} if args.overlap is not None else {}), "avoid": [*plan.get("avoid", ()), *args.avoid]} for plan in PLANS[:args.plans]]
    rejected, pools = set(json.loads((HERE/"rejects.json").read_text(encoding="utf-8"))["ann_ids"]), {}
    for cutout in build_inventory_mix.load_cutouts():
        if cutout["ann_id"] not in rejected:
            pools.setdefault(cutout["kind"], []).append(cutout)
    check, scripts, layouts = load_check(), {}, {}
    for number, plan in enumerate(plans, 1):
        sample = make_sample(plan, number, pools)
        check.validate_truth(sample["truth"])
        folder = SAMPLES/f"sample-{number:02d}"
        (folder/"photos").mkdir(parents=True)
        for index, data in enumerate(sample["photos"], 1):
            (folder/"photos"/f"{index}.jpg").write_bytes(data)
        (folder/"truth.json").write_text(json.dumps(sample["truth"], indent=1)+"\n", encoding="utf-8")
        scripts[folder.name] = {"text": sample["note"], "voice": plan["voice"]}
        layouts[folder.name] = {"name": plan["name"], "layout": sample["layout"]}
    (WORK/"controller_truth").mkdir(parents=True, exist_ok=True)
    (WORK/"controller_truth"/"scripts.json").write_text(json.dumps(scripts, indent=1)+"\n", encoding="utf-8")
    (WORK/"layouts.json").write_text(json.dumps(layouts)+"\n", encoding="utf-8")
    print(f"{len(plans)} samples in {SAMPLES}")


def voice(args):
    media = WORK/"worker_inputs"/"media"
    media.mkdir(parents=True, exist_ok=True)
    kokoro = LX/"downloads"/"kokoro"
    subprocess.run([str(LX/"tts-venv"/"Scripts"/"python.exe"), str(HERE.parent/"synthesize_voicecount.py"), str(WORK),
                    str(kokoro/"kokoro-v1.0.int8.onnx"), str(kokoro/"voices-v1.0.bin")], check=True)
    for clip in media.glob("*.wav"):
        shutil.copyfile(clip, SAMPLES/clip.stem/"request.wav")


def _embed(data, kind):
    return f"data:{kind};base64,{base64.b64encode(data).decode('ascii')}"


def _marked(path, entry):
    """A small copy of one photo with its objects outlined; a retaken-over photo is outlined in grey."""
    from PIL import Image, ImageDraw

    image = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(image)
    for kind, box in entry.get("objects", ()):
        draw.rectangle(box, outline=(150, 150, 150) if entry["ignored"] else (255, 60, 60), width=3)
        draw.text((box[0]+4, box[1]+3), kind, fill=(255, 255, 0))
    image.thumbnail((620, 620))
    out = BytesIO()
    image.save(out, "JPEG", quality=80)
    return _embed(out.getvalue(), "image/jpeg")


def sheet(args):
    check = load_check()
    scripts = json.loads((WORK/"controller_truth"/"scripts.json").read_text(encoding="utf-8"))
    layouts = json.loads((WORK/"layouts.json").read_text(encoding="utf-8"))
    parts = ["<!doctype html><meta charset=utf-8><title>Receiving audit samples</title><style>body{font:15px system-ui;margin:16px;background:#111;color:#eee}"
             "section{margin-bottom:36px}.row{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px}figure{margin:0}img{width:100%}"
             "figcaption{font-size:13px;color:#bbb}table{border-collapse:collapse;margin:10px 0}td,th{border:1px solid #555;padding:4px 10px;text-align:left}"
             "blockquote{margin:8px 0;padding:8px 12px;border-left:3px solid #777;max-width:800px}audio{width:100%;max-width:800px}</style>"
             "<h2>Receiving audit: samples with their answers</h2><p>Red boxes are what the check expects. A grey photo is the one the note says to leave out.</p>"]
    for sample, script in sorted(scripts.items()):
        folder = SAMPLES/sample
        truth = json.loads((folder/"truth.json").read_text(encoding="utf-8"))
        parts.append(f"<section><h3>{sample}: {html.escape(layouts[sample]['name'])}</h3><blockquote>{html.escape(script['text'])}</blockquote>")
        if (folder/"request.wav").is_file():
            parts.append(f'<audio controls src="{_embed((folder/"request.wav").read_bytes(), "audio/wav")}"></audio>')
        rows = "".join(f"<tr><td>{html.escape(line['item'])}</td><td>{line['ordered']}</td><td>{line['received']}</td><td>{check.expected_status(line)}</td>"
                       f"<td>{html.escape(str(line['substitute'] or ''))}</td><td>{line['credit']:.2f}</td><td>{len(line['boxes'])}</td></tr>" for line in truth["lines"])
        parts.append(f"<table><tr><th>Item</th><th>Ordered</th><th>Received</th><th>Status</th><th>Substitute</th><th>Credit</th><th>Boxes</th></tr>{rows}</table>"
                     f"<p>Total claim: {truth['total_claim']:.2f}</p><div class=row>")
        for number, entry in enumerate(layouts[sample]["layout"], 1):
            caption = entry.get("page") or f"delivery {entry['delivery']+1}, {len(entry['objects'])} objects"+(", left out" if entry.get("ignored") else "")
            parts.append(f'<figure><img src="{_marked(folder/"photos"/f"{number}.jpg", entry)}"><figcaption>photo {number}: {html.escape(caption)}</figcaption></figure>')
        parts.append("</div></section>")
    SHEET.parent.mkdir(exist_ok=True)
    SHEET.write_text("".join(parts), encoding="utf-8")
    print(f"sheet: {SHEET}, {SHEET.stat().st_size/1e6:.1f} MB")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=("build", "voice", "sheet"))
    parser.add_argument("--overlap", type=float, default=None, help="one overlap for every plan, such as 0 for objects that never touch")
    parser.add_argument("--avoid", type=int, nargs="*", default=[], help="COCO annotation ids to leave out of every plan, such as cutouts a person could not name by colour")
    parser.add_argument("--plans", type=int, default=len(PLANS), help="build only the first N plans")
    parser.add_argument("--samples", type=Path, default=None, help="where the sample folders go, instead of the receiving-audit workspace")
    parser.add_argument("--work", type=Path, default=None, help="where the build files go, instead of .lx/receiving-audit-build")
    args = parser.parse_args(argv)
    global SAMPLES, WORK
    SAMPLES, WORK = args.samples or SAMPLES, args.work or WORK
    {"build": build, "voice": voice, "sheet": sheet}[args.step](args)


if __name__ == "__main__":
    sys.exit(main())
