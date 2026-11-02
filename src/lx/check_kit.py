
# Speaks the check protocol for the text above; runs in the sandbox with the standard library only.
import json
import re
import sys
import traceback


def _failed(name, reason):
    return {"name": name, "passed": False, "error": f"{name}: {reason}"}


def _passed(name):
    return {"name": name, "passed": True, "error": None}


def _result(name, value):
    if type(value) in {int, float}:
        raise TypeError("score must say pass or fail: return True or False, or a dict of check names to True or False, not a number")
    return _passed(name) if value else _failed(name, "failed")


def _line(error):
    """The line of the text above where grading stopped; the message is left out, as it may quote the answer."""
    lines = [frame.lineno for frame in traceback.extract_tb(error.__traceback__) if frame.filename == __file__]
    return f"{type(error).__name__} on line {lines[-1]}"


def _read(value, path):
    """(found, the value at a dotted path); a number steps into a list."""
    for step in path.split("."):
        if type(value) is dict and step in value:
            value = value[step]
        elif type(value) is list and step.isdigit() and int(step) < len(value):
            value = value[int(step)]
        else:
            return False, None
    return True, value


def _same(got, want):
    return (type(got) is bool) == (type(want) is bool) and got == want


def _number(value):
    return type(value) in {int, float}


def _items(value):
    return sorted(json.dumps(item, sort_keys=True) for item in value)


def _problem(rule, truth, answer):
    """Why the answer fails this rule, or None. Reasons never carry a value."""
    kind, (found, got) = rule["rule"], _read(answer, rule["field"])
    if not found:
        return "required field is missing"
    if kind == "one_of":
        return None if any(_same(got, value) for value in rule["values"]) else "not one of the allowed values"
    if kind == "matches":
        return None if type(got) is str and re.fullmatch(rule["pattern"], got) else "wrong format"
    known, want = _read(truth, rule["field"])
    if not known:
        return "the expected answer has no such field"
    if kind == "equals":
        return None if _same(got, want) else "does not match"
    if kind == "near":
        return None if _number(got) and _number(want) and abs(got-want) <= rule["within"] else "not close enough"
    return None if type(got) is list and type(want) is list and _items(got) == _items(want) else "does not have the same items"


def _by_rules(truth, answer):
    written, rules = json.loads(RULES), []
    fields = [rule["field"] for rule in written["rules"]]
    for rule in written["rules"]:
        name = rule["field"] if fields.count(rule["field"]) == 1 else f"{rule['field']} {rule['rule']}"
        problem = _problem(rule, truth, answer)
        rules.append(_failed(name, problem) if problem else _passed(name))
    if written.get("extra_fields", True) is False:
        known = type(answer) is dict and set(answer) <= {field.split(".")[0] for field in fields}
        rules.append(_passed("only_known_fields") if known else _failed("only_known_fields", "unknown fields"))
    return rules


def _checks(truth, answer):
    if "RULES" in globals():
        return _by_rules(truth, answer)
    try:
        got = score(truth, answer)
    except Exception as error:
        return [_failed("score", f"could not grade this answer ({_line(error)})")]
    if type(got) is dict and got:
        return [_result(str(name), value) for name, value in got.items()]
    return [_result("score", got)]


def _main():
    request = json.load(sys.stdin)
    if "examples" in request:
        correct = globals().get("example", lambda truth: truth)
        print(json.dumps({"answers": [correct(truth) for truth in request["examples"]]}))
    else:
        print(json.dumps({"results": [{"checks": _checks(item["truth"], item["answer"])} for item in request["items"]]}))


_main()
