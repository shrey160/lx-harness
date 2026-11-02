
# Speaks the check protocol for the text above; runs in the sandbox with the standard library only.
import json
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


def _checks(truth, answer):
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
