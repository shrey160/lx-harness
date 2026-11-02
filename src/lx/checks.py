"""Run a task's scoring check, always in the script sandbox, and test it before approval.

A check is one Python file, usually written by the orchestrator, so it is
treated as untrusted code: without a sandbox it does not run at all. Protocol,
one JSON object on stdin and one on stdout:

- {"items": [{"truth": ..., "answer": ...}, ...]} prints
  {"results": [{"checks": [{"name": str, "passed": bool, "error": str or null}, ...]}, ...]}
- {"examples": [truth, ...]} prints {"answers": [a correct answer per truth, ...]}

The score is the fraction of named checks passed. Items go in batches because
each sandbox start can take seconds. Errors must not carry expected values.
"""
from __future__ import annotations

import re

from . import sandbox
from .records import RecordVerificationResult

TIMEOUT = 60
MAX_OUTPUT = 2_000_000
MAX_CHECKS = 200
# Answers a check meets in a real run: it must grade them, not crash, and never give them full marks.
JUNK_ANSWERS = (None, {}, [], "", "not json", 7, {"answer": None}, [{}])
# Above these a person should look before approving: the loop has little left to gain, or the score means little.
BLANK_LIMIT, WRONG_LIMIT = 0.5, 0.9


class CheckError(ValueError):
    """The check could not produce a score, so nothing may rely on one."""


def _call(source, payload):
    if not sandbox.available():
        raise CheckError("Scoring checks run only in the script sandbox; start Docker or use the Linux controller")
    result = sandbox.run_json(source, payload, timeout=TIMEOUT, max_stdout=MAX_OUTPUT)
    if result["timed_out"]:
        raise CheckError("The scoring check timed out")
    if result["exit_code"] != 0:
        raise CheckError("The scoring check crashed: "+(result["stderr"].strip()[-300:] or "no message"))
    if type(result["output"]) is not dict:
        raise CheckError("The scoring check must print one JSON object")
    return result["output"]


def _graded(entry):
    checks = entry.get("checks") if type(entry) is dict else None
    if type(checks) is not list or not 1 <= len(checks) <= MAX_CHECKS:
        raise CheckError(f"Each result needs 1 to {MAX_CHECKS} named checks")
    for check in checks:
        if (type(check) is not dict or type(check.get("name")) is not str or not check["name"]
                or type(check.get("passed")) is not bool or not isinstance(check.get("error"), (str, type(None)))):
            raise CheckError("A check needs a name, passed true or false, and an error string or null")
    passed = sum(check["passed"] for check in checks)
    errors = tuple(check["error"] or f"{check['name']}: failed" for check in checks if not check["passed"])
    return RecordVerificationResult(not errors, errors, passed/len(checks), passed, len(checks))


def run_checks(source, items):
    """Score [(truth, answer), ...] in one sandbox run."""
    output = _call(source, {"items": [{"truth": truth, "answer": answer} for truth, answer in items]})
    results = output.get("results")
    if type(results) is not list or len(results) != len(items):
        raise CheckError("The scoring check must return one result per item")
    return [_graded(entry) for entry in results]


def run_check(source, truth, answer):
    return run_checks(source, [(truth, answer)])[0]


def named_checks(source, truth, answer):
    """Each check's name and whether it passed, without its message, which may quote the expected answer."""
    results = _call(source, {"items": [{"truth": truth, "answer": answer}]}).get("results")
    if type(results) is not list or len(results) != 1:
        raise CheckError("The scoring check must return one result per item")
    _graded(results[0])
    return [{"name": check["name"], "passed": check["passed"]} for check in results[0]["checks"]]


def shown_checks(source, items):
    """Each check's name and result for [(truth, answer), ...], for someone who must not learn the truth: a name that reveals it is withheld."""
    results = _call(source, {"items": [{"truth": truth, "answer": answer} for truth, answer in items]}).get("results")
    if type(results) is not list or len(results) != len(items):
        raise CheckError("The scoring check must return one result per item")
    shown = []
    for entry, (truth, _) in zip(results, items):
        _graded(entry)
        shown.append([{"name": f"check {number}" if _reveals(check["name"], [truth]) else check["name"], "passed": check["passed"]}
                      for number, check in enumerate(entry["checks"], 1)])
    return shown


def examples(source, truths):
    answers = _call(source, {"examples": list(truths)}).get("answers")
    if type(answers) is not list or len(answers) != len(truths):
        raise CheckError("The scoring check must return one example answer per sample")
    return answers


def _words(text):
    return re.findall(r"[a-z0-9_]+", text.lower())


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _weaknesses(blank, wrong):
    """What a person should know about a check that passed: plain sentences, empty if none."""
    found = [f"A blank answer scores {blank:.2f}, so much of the score is free."] if blank > BLANK_LIMIT else []
    if wrong is None:
        return found+["No wrong answer could be tried, because the samples all expect the same answer."]
    if wrong >= 1.0:
        return found+["Another sample's answer gets full marks. That is right only if one answer can suit several samples; otherwise the check cannot tell them apart."]
    return found+([f"Another sample's answer scores {wrong:.2f}, so the check barely tells right from wrong."] if wrong >= WRONG_LIMIT else [])


def self_test(source, truths):
    """Prove a check on {sample id: truth}: raises CheckError, or returns a summary.

    A correct answer scores 1.0, junk is graded without crashing and scores
    less, grading repeats exactly, and errors do not reveal expected values.
    The summary says how high blank and wrong answers score, and why that may
    be weak, for the person approving it: another sample's answer can pass, as
    one design can meet two specs, so only a person lets that through. A failure's message reaches whoever
    wrote the check, so one naming an expected value is withheld.
    """
    try:
        return _prove(source, truths)
    except CheckError as exc:
        if "crashed:" in str(exc) and _reveals(str(exc).split("crashed:", 1)[1], truths.values()):
            raise CheckError("The scoring check failed, and its message named an expected value, so it is withheld") from None
        raise


def _reveals(text, truths):
    return bool({word for truth in truths for item in _strings(truth) for word in _words(item) if len(word) >= 4} & set(_words(text)))


def _prove(source, truths):
    if not truths:
        raise CheckError("A check needs at least one sample with an expected answer")
    ids = list(truths)
    good = examples(source, [truths[i] for i in ids])
    items, positions = [], {}
    def add(label, truth, answer):
        positions[label] = len(items)
        items.append((truth, answer))
    for index, sample in enumerate(ids):
        add(("example", sample), truths[sample], good[index])
        add(("again", sample), truths[sample], good[index])
        for number, junk in enumerate(JUNK_ANSWERS):
            add(("junk", sample, number), truths[sample], junk)
        other = ids[(index+1) % len(ids)]
        if truths[other] != truths[sample]:
            add(("other", sample), truths[sample], good[(index+1) % len(ids)])
    graded = run_checks(source, items)
    repeated = run_checks(source, items)
    if graded != repeated:
        raise CheckError("The scoring check gave different results for the same input")
    for label, position in positions.items():
        result, sample = graded[position], label[1]
        if label[0] in {"example", "again"} and result.score != 1.0:
            why = next((error for error in result.errors if not _reveals(error, [truths[sample]])), None)
            raise CheckError(f"{sample}: a correct answer scored {result.score:.2f}, not 1.0"+(f" ({why})" if why else ""))
        if label[0] == "junk" and result.score >= 1.0:
            raise CheckError(f"{sample}: a malformed answer got full marks")
        if label[0] in {"junk", "other"} and _reveals(" ".join(result.errors), [truths[sample]]):
            raise CheckError(f"{sample}: a check error reveals an expected value")
    scores = lambda kind: [graded[position].score for label, position in positions.items() if label[0] == kind]
    blank, wrong = max(scores("junk")), max(scores("other"), default=None)
    return {"samples": len(ids), "checks_per_sample": graded[0].n_criteria, "cases": len(items),
            "blank_score": blank, "wrong_score": wrong, "weaknesses": _weaknesses(blank, wrong)}
