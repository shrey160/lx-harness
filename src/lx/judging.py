"""Model judges: yes-or-no criteria a chat model answers about each answer, beside the check or in place of one.

A judge runs on the controller during grading, after the loop has finished, so
the improver never sees it. Its verdicts count as named checks, marked as
judged. The criteria, what the judge sees, its provider entry and its cap are
all in the spec, so an approval binds them.
"""
from __future__ import annotations

import json
import math

MAX_CRITERIA = 50
MAX_NAME = 80
MAX_QUESTION = 1000
FILE = "judges.json"
# What a person or agent writes; the entry and the cap are chosen apart, since they spend money.
WRITTEN = ("criteria", "sees_request")
FIELDS = (*WRITTEN, "entry", "cap_usd")


def _criterion(number, item):
    where = f"Criterion {number}"
    if type(item) is not dict or set(item) != {"name", "question"}:
        raise ValueError(f"{where} needs a name and a question, and nothing else")
    name, question = item["name"], item["question"]
    if type(name) is not str or not name.strip() or len(name) > MAX_NAME:
        raise ValueError(f"{where}: give a name of 1 to {MAX_NAME} characters")
    if type(question) is not str or not question.strip() or len(question) > MAX_QUESTION:
        raise ValueError(f"{where}: give a yes-or-no question of 1 to {MAX_QUESTION} characters")
    return {"name": name.strip(), "question": question.strip()}


def check_written(judge, *, text_task):
    """The criteria and what the judge sees, cleaned, or a ValueError a person can act on."""
    if type(judge) is not dict or set(judge)-set(WRITTEN):
        raise ValueError("Judges are an object with criteria, and optionally sees_request")
    criteria = judge.get("criteria")
    if type(criteria) is not list or not 1 <= len(criteria) <= MAX_CRITERIA:
        raise ValueError(f"Give 1 to {MAX_CRITERIA} criteria")
    cleaned = [_criterion(number, item) for number, item in enumerate(criteria, 1)]
    if len({item["name"] for item in cleaned}) != len(cleaned):
        raise ValueError("Each criterion needs its own name")
    sees = judge.get("sees_request", False)
    if type(sees) is not bool:
        raise ValueError("sees_request is true or false")
    if sees and not text_task:
        raise ValueError("Only a text task's judge can see the request")
    return {"criteria": cleaned, "sees_request": sees}


def read_written(text, *, text_task):
    """The judges in a file's text."""
    try:
        written = json.loads(text)
    except ValueError as error:
        raise ValueError(f"The judges are not valid JSON: {error}") from None
    return check_written(written, text_task=text_task)


def check_cap(cap):
    """The most a run may spend on judging, which the person sets."""
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not math.isfinite(cap) or not 0 < cap <= 20:
        raise ValueError("A judge's cap must be more than $0 and at most $20")
    return cap


def missing(judge):
    """What a judge still needs before it can run, or None."""
    if not judge:
        return None
    if not judge.get("entry"):
        return "Choose a model for the judges."
    if judge.get("cap_usd") is None:
        return "Set the most the judges may spend on one run."
    return None


def written(judge):
    """The part of a spec's judge a person or agent writes."""
    return {key: judge[key] for key in WRITTEN} if judge else None
