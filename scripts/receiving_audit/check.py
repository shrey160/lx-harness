"""Scoring check for the goods receiving audit: one standalone file, standard library only.

Follows the protocol in lx.checks: a JSON object on stdin, a JSON object on
stdout. The named checks are chains, as in JobBench: one for each purchase order
line, and one for the claim. A chain passes only if every link in it passes, so
the score is the fraction of whole chains passed. Errors name the first link
that failed and never an expected value. A line's status is worked out from its
counts, so truth.json holds no status words for the contract to repeat.
"""
import json
import sys


LINE_FIELDS = {"item", "ordered", "received", "status", "substitute", "credit", "boxes"}
MIN_IOU = 0.5
MAX_COUNT = 500
IRREGULAR = {"knives": "knife", "mice": "mouse"}


def singular(word):
    """Lower-case, single-spaced, singular form of the last word."""
    words = " ".join(word.lower().replace("-", " ").split()).split(" ")
    last = words[-1]
    if last in IRREGULAR:
        last = IRREGULAR[last]
    elif len(last) <= 3 or last.endswith(("ss", "us", "is")):
        pass
    elif last.endswith("ies"):
        last = last[:-3]+"y"
    elif last.endswith(("ches", "shes", "xes")):
        last = last[:-2]
    elif last.endswith("s"):
        last = last[:-1]
    words[-1] = last
    return " ".join(words)


def _alias_table(truth):
    return {singular(name): singular(item) for item, names in (truth.get("aliases") or {}).items() for name in names}


def _name(word, table):
    return table.get(singular(word), singular(word)) if type(word) is str and word.strip() else None


def _count(value):
    return type(value) is int and 0 <= value <= MAX_COUNT


def _money(value):
    return type(value) in (int, float) and abs(value) < 1e9 and value == value


def _cents(value):
    return round(value*100)


def _box(value):
    return (type(value) is list and len(value) == 4 and all(_money(part) for part in value)
            and value[0] <= value[2] and value[1] <= value[3])


def _placed(boxes):
    return type(boxes) is list and all(type(entry) is dict and set(entry) == {"photo", "box"} and type(entry["photo"]) is int
                                       and _box(entry["box"]) for entry in boxes)


def iou(a, b):
    """Overlap of two boxes in inclusive pixel corners, as the segment tool gives them."""
    width = min(a[2], b[2])-max(a[0], b[0])+1
    height = min(a[3], b[3])-max(a[1], b[1])+1
    if width <= 0 or height <= 0:
        return 0.0
    area = lambda r: (r[2]-r[0]+1)*(r[3]-r[1]+1)
    return width*height/(area(a)+area(b)-width*height)


def boxes_match(given, want):
    """Each expected box pairs with its own given box, on the same photo, with none left over."""
    if not _placed(given) or len(given) != len(want):
        return False
    fits = [[index for index, entry in enumerate(given) if entry["photo"] == target["photo"] and iou(entry["box"], target["box"]) >= MIN_IOU]
            for target in want]
    taken = {}
    def assign(target, seen):
        for index in fits[target]:
            if index not in seen:
                seen.add(index)
                if index not in taken or assign(taken[index], seen):
                    taken[index] = target
                    return True
        return False
    return all(assign(target, set()) for target in range(len(want)))


def expected_status(line):
    if line["substitute"]:
        return "wrong_item"
    return "short" if line["received"] < line["ordered"] else "over" if line["received"] > line["ordered"] else "complete"


def _same_substitute(given, want, table):
    if want is None or given is None:
        return given is None and want is None
    return (type(given) is dict and set(given) == {"item", "count"} and _name(given["item"], table) == _name(want["item"], table)
            and given["count"] == want["count"] and type(given["count"]) is int)


def _first_fault(line, want, table):
    """The first link of this line's chain to fail, or None. The order is how a clerk would defend the line."""
    links = (("fields", lambda: set(line) == LINE_FIELDS),
             ("ordered", lambda: _count(line["ordered"]) and line["ordered"] == want["ordered"]),
             ("received", lambda: _count(line["received"]) and line["received"] == want["received"]),
             ("substitute", lambda: _same_substitute(line["substitute"], want["substitute"], table)),
             ("boxes", lambda: boxes_match(line["boxes"], want["boxes"])),
             ("status", lambda: line["status"] == expected_status(want)),
             ("credit", lambda: _money(line["credit"]) and _cents(line["credit"]) == _cents(want["credit"])))
    return next((name for name, passed in links if not passed()), None)


def _lines(candidate):
    lines = candidate.get("lines") if type(candidate) is dict else None
    return lines if type(lines) is list and all(type(line) is dict for line in lines) else None


def _claim_fault(truth, candidate, lines, table):
    if lines is None or set(candidate) != {"lines", "total_claim"}:
        return "fields"
    names = [_name(line.get("item"), table) for line in lines]
    if sorted(map(str, names)) != sorted(_name(line["item"], table) for line in truth["lines"]):
        return "lines"
    if not all(_money(line.get("credit")) for line in lines) or not _money(candidate["total_claim"]):
        return "fields"
    if sum(_cents(line["credit"]) for line in lines) != _cents(candidate["total_claim"]):
        return "sum"
    return None if _cents(candidate["total_claim"]) == _cents(truth["total_claim"]) else "total"


def chains(truth, candidate):
    """[(check name, passed, error or None)]; the list of names depends only on the truth."""
    table, lines, results = _alias_table(truth), _lines(candidate), []
    for number, want in enumerate(truth["lines"], 1):
        same = [line for line in lines or [] if _name(line.get("item"), table) == _name(want["item"], table)]
        fault = _first_fault(same[0], want, table) if len(same) == 1 else "lookup"
        results.append((f"line {number}", fault is None, None if fault is None else f"line {number}: failed at {fault}"))
    fault = _claim_fault(truth, candidate, lines, table)
    results.append(("claim", fault is None, None if fault is None else f"claim: failed at {fault}"))
    return results


def validate_truth(truth):
    """Raise ValueError unless `truth` is a sound audit: this catches a mistake in the samples, not in an answer."""
    if type(truth) is not dict or set(truth) - {"lines", "total_claim", "aliases"} or type(truth.get("lines")) is not list or not truth["lines"]:
        raise ValueError("truth needs lines and total_claim")
    for line in truth["lines"]:
        if type(line) is not dict or set(line) != {"item", "ordered", "received", "substitute", "credit", "boxes"}:
            raise ValueError("each line needs item, ordered, received, substitute, credit and boxes")
        if not _name(line["item"], {}) or not _count(line["ordered"]) or not _count(line["received"]) or not _money(line["credit"]) or line["credit"] < 0:
            raise ValueError("a line has a bad item, count or credit")
        extra = 0
        if line["substitute"] is not None:
            substitute = line["substitute"]
            if type(substitute) is not dict or set(substitute) != {"item", "count"} or not _name(substitute["item"], {}) or not _count(substitute["count"]) or not substitute["count"]:
                raise ValueError("a substitute needs an item and a count")
            if line["received"] >= line["ordered"]:
                raise ValueError("a substitute means fewer than ordered arrived")
            extra = substitute["count"]
        if not _placed(line["boxes"]) or len(line["boxes"]) != line["received"]+extra:
            raise ValueError("a line needs one box for each object counted")
        if expected_status(line) in ("complete", "over") and line["credit"]:
            raise ValueError("only a short or wrong item line can earn credit")
    if len({_name(line["item"], _alias_table(truth)) for line in truth["lines"]}) != len(truth["lines"]):
        raise ValueError("each item has one line")
    if not _money(truth.get("total_claim")) or truth["total_claim"] <= 0 or _cents(truth["total_claim"]) != sum(_cents(line["credit"]) for line in truth["lines"]):
        raise ValueError("total_claim must be above 0 and equal the credits")
    return truth


def example_answer(truth):
    """A correct answer for this truth; the check's self-test expects it to score 1.0."""
    validate_truth(truth)
    return {"lines": [dict(line, status=expected_status(line)) for line in truth["lines"]], "total_claim": truth["total_claim"]}


def main():
    request = json.load(sys.stdin)
    if "examples" in request:
        print(json.dumps({"answers": [example_answer(truth) for truth in request["examples"]]}))
        return
    print(json.dumps({"results": [{"checks": [{"name": name, "passed": bool(ok), "error": error}
                                              for name, ok, error in chains(item["truth"], item["answer"])]}
                                  for item in request["items"]]}))


if __name__ == "__main__":
    main()
