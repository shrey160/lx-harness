"""Scoring check for the photo inventory task: one standalone file, standard library only.

This is the check an inventory workspace approves and the sandbox runs. Like any
orchestrator-written check it follows the protocol in lx.checks: a JSON object
on stdin, a JSON object on stdout. lx.inventory imports the same functions, so
there is one implementation of the score.
"""
import json
import sys


REQUEST_TYPES = ("count", "colour_count", "inventory")
MIN_PHOTOS, MAX_PHOTOS = 3, 7
MAX_COUNT = 500
COLOURS = ("red", "orange", "yellow", "green", "blue", "purple", "pink", "brown",
           "black", "white", "grey", "silver", "gold")
COLOUR_ALIASES = {"gray": "grey", "violet": "purple"}
# Common alternative names; a sample's truth may add its own (`aliases`).
KIND_ALIASES = {"biro": "pen", "ballpoint": "pen", "ballpoint pen": "pen", "felt tip": "marker",
                "felt tip pen": "marker", "rubber": "eraser", "tumbler": "glass", "drinking glass": "glass",
                "tin": "can", "tin can": "can", "soda can": "can", "water bottle": "bottle",
                "paperclip": "paper clip", "clip": "paper clip", "teaspoon": "spoon",
                "tangerine": "orange", "cube": "block", "toy block": "block", "sphere": "ball"}
IRREGULAR = {"knives": "knife", "leaves": "leaf", "mice": "mouse", "dice": "die", "geese": "goose",
             "children": "child", "people": "person", "teeth": "tooth", "feet": "foot",
             "tomatoes": "tomato", "potatoes": "potato"}
INVARIANT = {"scissors", "pliers", "tongs", "headphones", "earphones", "sunglasses", "series"}

FIELDS = ("request", "photos", "answer")


def tolerance(count):
    """Exact below 20, then 5% (floor): user-made counts are small and exact."""
    return count * 5 // 100


def singular(word):
    """Lower-case, single-spaced, singular form of the last word."""
    words = " ".join(str(word).lower().replace("-", " ").split()).split(" ")
    last = words[-1]
    if last in IRREGULAR:
        last = IRREGULAR[last]
    elif last in INVARIANT or len(last) <= 3 or last.endswith(("ss", "us", "is")):
        pass
    elif last.endswith("ies"):
        last = last[:-3] + "y"
    elif last.endswith(("ches", "shes", "xes", "sses", "zes")):
        last = last[:-2]
    elif last.endswith("s"):
        last = last[:-1]
    words[-1] = last
    return " ".join(words)


def plural(kind):
    if kind in INVARIANT:
        return kind
    for word, single in IRREGULAR.items():
        if kind.endswith(single):
            return kind[: -len(single)] + word
    if kind.endswith("y") and kind[-2:-1] not in "aeiou":
        return kind[:-1] + "ies"
    if kind.endswith(("ch", "sh", "x", "z", "s")):
        return kind + "es"
    return kind + "s"


def canonical_kind(name, extra=None):
    """Canonical kind for comparison: singular form, then alias lookup (sample aliases first)."""
    if type(name) is not str or not name.strip():
        return None
    word = singular(name)
    for table in (extra or {}, KIND_ALIASES):
        if word in table:
            return table[word]
    return word


def canonical_colour(name):
    if name is None:
        return None
    if type(name) is not str:
        return False
    word = " ".join(name.lower().split())
    return COLOUR_ALIASES.get(word, word)


def _alias_table(truth):
    table = {}
    for kind, names in (truth.get("aliases") or {}).items():
        for name in names:
            table[singular(name)] = singular(kind)
    return table


def validate_truth(truth):
    """Raise ValueError unless `truth` follows the sample truth schema."""
    if type(truth) is not dict:
        raise ValueError("truth must be an object")
    allowed = {"request_type", "kind", "colour", "photos", "aliases"}
    if set(truth) - allowed:
        raise ValueError(f"unknown truth fields: {sorted(set(truth) - allowed)}")
    kind_type = truth.get("request_type")
    if kind_type not in REQUEST_TYPES:
        raise ValueError(f"request_type must be one of {REQUEST_TYPES}")
    if kind_type == "inventory":
        if truth.get("kind") is not None or truth.get("colour") is not None:
            raise ValueError("inventory requests take no kind or colour")
    elif type(truth.get("kind")) is not str or not truth["kind"].strip():
        raise ValueError("count requests need a kind")
    if kind_type == "colour_count":
        if canonical_colour(truth.get("colour")) not in COLOURS:
            raise ValueError(f"colour must be one of {COLOURS}")
    elif truth.get("colour") is not None:
        raise ValueError("only colour_count requests take a colour")
    aliases = truth.get("aliases", {})
    if type(aliases) is not dict or any(type(k) is not str or type(v) is not list
                                         or any(type(n) is not str for n in v) for k, v in aliases.items()):
        raise ValueError("aliases must map a kind to a list of names")
    photos = truth.get("photos")
    if type(photos) is not dict or not MIN_PHOTOS <= len(photos) <= MAX_PHOTOS:
        raise ValueError(f"photos must map {MIN_PHOTOS}-{MAX_PHOTOS} photo numbers to item lists")
    if sorted(photos) != [str(i) for i in range(1, len(photos) + 1)]:
        raise ValueError("photo numbers must be 1..N")
    for number, items in photos.items():
        if type(items) is not list:
            raise ValueError(f"photo {number}: items must be a list")
        for item in items:
            if type(item) is not dict or set(item) - {"kind", "colour", "count"} or "kind" not in item:
                raise ValueError(f"photo {number}: each item needs kind, count and an optional colour")
            if type(item["kind"]) is not str or not item["kind"].strip():
                raise ValueError(f"photo {number}: kind must be a non-empty string")
            if type(item.get("count")) is not int or not 1 <= item["count"] <= MAX_COUNT:
                raise ValueError(f"photo {number}: count must be an integer from 1 to {MAX_COUNT}")
            if item.get("colour") is not None and canonical_colour(item["colour"]) not in COLOURS:
                raise ValueError(f"photo {number}: colour must be one of {COLOURS}")
    if kind_type != "inventory" and expected_answer(truth) == 0:
        raise ValueError("count requests must have a non-zero answer")
    return truth


def _matches(truth, kind, colour, table):
    if canonical_kind(kind, table) != canonical_kind(truth["kind"], table):
        return False
    return truth["request_type"] != "colour_count" or canonical_colour(colour) == canonical_colour(truth["colour"])


def expected_photos(truth):
    """Per-photo expected values: an int for count requests, {kind: count} for inventory."""
    table = _alias_table(truth)
    out = {}
    for number, items in truth["photos"].items():
        if truth["request_type"] == "inventory":
            kinds = {}
            for item in items:
                kind = canonical_kind(item["kind"], table)
                kinds[kind] = kinds.get(kind, 0) + item["count"]
            out[int(number)] = kinds
        else:
            out[int(number)] = sum(i["count"] for i in items if _matches(truth, i["kind"], i.get("colour"), table))
    return out


def expected_answer(truth):
    photos = expected_photos(truth)
    if truth["request_type"] != "inventory":
        return sum(photos.values())
    total = {}
    for kinds in photos.values():
        for kind, count in kinds.items():
            total[kind] = total.get(kind, 0) + count
    return total


def _count(value):
    return type(value) is int and 0 <= value <= MAX_COUNT * MAX_PHOTOS


def _close(got, want):
    return _count(got) and abs(got - want) <= tolerance(want)


def _close_map(got, want):
    return got is not None and set(got) == set(want) and all(_close(got[k], want[k]) for k in want)


def _items_ok(items):
    return type(items) is list and all(
        type(i) is dict and not set(i) - {"kind", "colour", "count"} and type(i.get("kind")) is str
        and (i.get("colour") is None or type(i["colour"]) is str) and _count(i.get("count")) for i in items)


def _kind_totals(items, table):
    """{kind: count} for a list of candidate items, or None when malformed."""
    if not _items_ok(items):
        return None
    total = {}
    for item in items:
        kind = canonical_kind(item["kind"], table)
        total[kind] = total.get(kind, 0) + item["count"]
    return total


def _photo_entries(candidate):
    photos = candidate.get("photos")
    if type(photos) is not list:
        return None
    entries = {}
    for entry in photos:
        if type(entry) is not dict or set(entry) != {"photo", "items"} or type(entry["photo"]) is not int:
            return None
        if entry["photo"] in entries:
            return None
        entries[entry["photo"]] = entry["items"]
    return entries


def _answer_value(truth, candidate, table):
    answer = candidate.get("answer")
    if truth["request_type"] != "inventory":
        return answer if _count(answer) else None
    if type(answer) is not list or not all(type(a) is dict and set(a) == {"kind", "count"} for a in answer):
        return None
    return _kind_totals(answer, table)


def checklist(truth, candidate):
    """[(check name, passed, error or None)]; the check list depends only on the truth."""
    table = _alias_table(truth)
    n_photos = len(truth["photos"])
    names = ["schema", "request", "coverage"] + [f"photo {i}" for i in range(1, n_photos + 1)] + ["consistency", "answer"]
    if type(candidate) is not dict:
        return [(name, False, f"{name}: output must be a JSON object") for name in names]
    results = []
    request = candidate.get("request")
    request_ok = type(request) is dict and set(request) == {"type", "kind", "colour"}
    entries = _photo_entries(candidate)
    answer = _answer_value(truth, candidate, table)
    schema_ok = (set(candidate) == set(FIELDS) and request_ok and entries is not None
                 and all(_items_ok(items) for items in entries.values()) and answer is not None)
    results.append(("schema", schema_ok, None if schema_ok else "schema: output does not follow the contract"))
    same_request = request_ok and request["type"] == truth["request_type"]
    if same_request and truth["request_type"] != "inventory":
        same_request = canonical_kind(request["kind"], table) == canonical_kind(truth["kind"], table)
    if same_request and truth["request_type"] == "colour_count":
        same_request = canonical_colour(request["colour"]) == canonical_colour(truth["colour"])
    if same_request and truth["request_type"] != "colour_count":
        same_request = request["colour"] is None
    if same_request and truth["request_type"] == "inventory":
        same_request = request["kind"] is None
    results.append(("request", same_request, None if same_request else "request: type, kind or colour does not match"))
    covered = entries is not None and set(entries) == set(range(1, n_photos + 1))
    results.append(("coverage", covered, None if covered else "coverage: photos missing, repeated or unknown"))
    want = expected_photos(truth)
    for number in range(1, n_photos + 1):
        items = entries.get(number) if entries else None
        if truth["request_type"] == "inventory":
            ok = _close_map(_kind_totals(items, table), want[number])
        else:
            ok = _items_ok(items) and _close(
                sum(i["count"] for i in items if _matches(truth, i["kind"], i.get("colour"), table)), want[number])
        results.append((f"photo {number}", ok, None if ok else f"photo {number}: counts do not match"))
    consistent = False
    if answer is not None and entries is not None and all(_items_ok(items) for items in entries.values()):
        listed = [i for items in entries.values() for i in items]
        if truth["request_type"] == "inventory":
            consistent = _kind_totals(listed, table) == answer
        else:
            consistent = sum(i["count"] for i in listed) == answer
    results.append(("consistency", consistent,
                    None if consistent else "consistency: answer is not the total of the per-photo items"))
    final = expected_answer(truth)
    correct = answer is not None and (_close_map(answer, final) if truth["request_type"] == "inventory"
                                      else _close(answer, final))
    results.append(("answer", correct, None if correct else "answer: does not match"))
    return results



def example_answer(truth):
    """A correct answer for this truth; the check's self-test expects it to score 1.0."""
    table = _alias_table(truth)
    photos = []
    for number, items in sorted(truth["photos"].items(), key=lambda pair: int(pair[0])):
        if truth["request_type"] != "inventory":
            items = [i for i in items if _matches(truth, i["kind"], i.get("colour"), table)]
        photos.append({"photo": int(number), "items": [{"kind": i["kind"], "colour": i.get("colour"), "count": i["count"]}
                                                       for i in items]})
    answer = expected_answer(truth)
    if truth["request_type"] == "inventory":
        answer = [{"kind": kind, "count": count} for kind, count in sorted(answer.items())]
    return {"request": {"type": truth["request_type"], "kind": truth.get("kind"), "colour": truth.get("colour")},
            "photos": photos, "answer": answer}


def main():
    request = json.load(sys.stdin)
    if "examples" in request:
        print(json.dumps({"answers": [example_answer(truth) for truth in request["examples"]]}))
        return
    print(json.dumps({"results": [{"checks": [{"name": name, "passed": bool(ok), "error": error}
                                              for name, ok, error in checklist(item["truth"], item["answer"])]}
                                  for item in request["items"]]}))


if __name__ == "__main__":
    main()
