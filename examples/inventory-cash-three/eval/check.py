"""Standalone inventory-update check; controller truth never enters model context."""
import json
import math
import sys
from decimal import Decimal, InvalidOperation


NAMES = ("changed item", "signed change", "updated quantity", "unchanged stock", "unit prices", "cash movement", "ending account", "object evidence")


def cents(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    try:
        scaled = Decimal(str(value)) * 100
        return int(scaled) if scaled == scaled.to_integral_value() else None
    except (ValueError, InvalidOperation, OverflowError):
        return None


def iou(a, b):
    area = lambda r: (r[2] - r[0] + 1) * (r[3] - r[1] + 1)
    w, h = min(a[2], b[2]) - max(a[0], b[0]) + 1, min(a[3], b[3]) - max(a[1], b[1]) + 1
    overlap = max(0, w) * max(0, h)
    return overlap / (area(a) + area(b) - overlap)


def photo_sizes(truth):
    sizes = truth.get("photo_sizes", [[1024, 768], [1024, 768]])
    assert type(sizes) is list and 2 <= len(sizes) <= 7
    assert all(type(s) is list and len(s) == 2 and all(type(v) is int and 0 < v <= 8192 for v in s) for s in sizes)
    return sizes


def valid_boxes(entries, sizes=None):
    sizes = [[1024, 768], [1024, 768]] if sizes is None else sizes
    if type(entries) is not list:
        return False
    for e in entries:
        if type(e) is not dict or set(e) != {"photo", "box"} or type(e["photo"]) is not int or not 1 <= e["photo"] <= len(sizes):
            return False
        width, height = sizes[e["photo"] - 1]
        b = e["box"]
        if (type(b) is not list or len(b) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in b)
                or not (0 <= b[0] <= b[2] < width and 0 <= b[1] <= b[3] < height)):
            return False
    return True


def boxes_match(given, want):
    if len(given) != len(want):
        return False
    fits = [[i for i, e in enumerate(given) if e["photo"] == w["photo"] and iou(e["box"], w["box"]) >= 0.5] for w in want]
    taken = {}
    def assign(target, seen):
        for i in fits[target]:
            if i not in seen:
                seen.add(i)
                if i not in taken or assign(taken[i], seen):
                    taken[i] = target
                    return True
        return False
    return all(assign(target, set()) for target in range(len(want)))


def example_answer(truth):
    assert type(truth) is dict and set(truth) - {"photo_sizes"} == {"before", "prices", "balance", "item", "change", "evidence"}
    before, item, change = truth["before"], truth["item"], truth["change"]
    assert type(before) is dict and all(type(k) is str and type(v) is int and v >= 0 for k, v in before.items())
    assert item in before and type(change) is int and change != 0
    assert valid_boxes(truth["evidence"], photo_sizes(truth)) and abs(change) == len(truth["evidence"])
    assert set(truth["prices"]) == set(before) and all(cents(p) is not None and cents(p) > 0 for p in truth["prices"].values())
    assert cents(truth["balance"]) is not None and cents(truth["balance"]) >= 0
    inventory = {k: {"quantity": v, "unit_price": truth["prices"][k]} for k, v in before.items()}
    inventory[item]["quantity"] += change
    cash_change = -change * cents(truth["prices"][item])
    balance = cents(truth["balance"]) + cash_change
    assert inventory[item]["quantity"] >= 0 and balance >= 0
    return {"item": item, "change": change, "inventory": inventory, "cash_change": cash_change / 100,
            "account": {"currency": "USD", "balance": balance / 100}, "evidence": truth["evidence"]}


def evaluate(truth, answer):
    want = example_answer(truth)
    schema = (type(answer) is dict and set(answer) == set(want) and type(answer["item"]) is str
              and type(answer["change"]) is int and type(answer["inventory"]) is dict
              and set(answer["inventory"]) == set(want["inventory"])
              and all(type(v) is dict and set(v) == {"quantity", "unit_price"} and type(v["quantity"]) is int and v["quantity"] >= 0
                      and cents(v["unit_price"]) is not None and v["unit_price"] > 0 for v in answer["inventory"].values())
              and cents(answer["cash_change"]) is not None and type(answer["account"]) is dict
              and set(answer["account"]) == {"currency", "balance"} and type(answer["account"]["currency"]) is str
              and cents(answer["account"]["balance"]) is not None and answer["account"]["balance"] >= 0
              and valid_boxes(answer["evidence"], photo_sizes(truth)))
    flags = [False] * len(NAMES)
    if schema:
        same_item = answer["item"] == want["item"]
        flags = [same_item, same_item and answer["change"] == want["change"],
                 same_item and answer["inventory"][want["item"]]["quantity"] == want["inventory"][want["item"]]["quantity"],
                 same_item and all(answer["inventory"][k]["quantity"] == v for k, v in truth["before"].items() if k != want["item"]),
                 same_item and all(cents(answer["inventory"][k]["unit_price"]) == cents(v) for k, v in truth["prices"].items()),
                 same_item and cents(answer["cash_change"]) == cents(want["cash_change"]),
                 same_item and answer["account"]["currency"] == want["account"]["currency"]
                 and cents(answer["account"]["balance"]) == cents(want["account"]["balance"]),
                 same_item and boxes_match(answer["evidence"], want["evidence"])]
    return {"checks": [{"name": name, "passed": passed, "error": None if passed else name + ": failed"}
                       for name, passed in zip(NAMES, flags)]}


def main():
    data = json.load(sys.stdin)
    if "examples" in data:
        print(json.dumps({"answers": [example_answer(t) for t in data["examples"]]}))
    else:
        print(json.dumps({"results": [evaluate(e["truth"], e["answer"]) for e in data["items"]]}))


if __name__ == "__main__":
    main()
