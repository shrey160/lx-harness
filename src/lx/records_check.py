"""Scoring check for the order-records task; runs in the sandbox with the standard library only.

One named check per field, plus one for unknown fields. Errors name the field
and never carry the expected value. Truth is the normalized order itself.
"""
import json
import re
import sys

FIELDS = ("order_id", "customer", "date", "currency", "amount_minor", "phone", "tags")


def well_typed(field, value):
    if field == "amount_minor":
        return type(value) is int  # a boolean is not an amount
    if field == "phone":
        return value is None or (type(value) is str and re.fullmatch(r"\+[1-9]\d{7,14}", value) is not None)
    if field == "tags":
        return (type(value) is list and all(type(tag) is str for tag in value)
                and value == sorted(set(value)) and all(tag == tag.lower() for tag in value))
    if field == "date":
        return type(value) is str and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None
    return type(value) is str


def checklist(truth, answer):
    if type(answer) is not dict:
        return [(field, False, f"{field}: the answer is not a JSON object") for field in FIELDS]+[
            ("only_known_fields", False, "schema: the answer is not a JSON object")]
    results = []
    for field in FIELDS:
        if field not in answer:
            results.append((field, False, f"{field}: required field is missing"))
        elif not well_typed(field, answer[field]):
            results.append((field, False, f"{field}: wrong type or format"))
        else:
            correct = answer[field] == truth[field]
            results.append((field, correct, None if correct else f"{field}: does not match"))
    known = set(answer) <= set(FIELDS)
    results.append(("only_known_fields", known, None if known else "schema: unknown fields"))
    return results


def main():
    request = json.load(sys.stdin)
    if "examples" in request:
        print(json.dumps({"answers": request["examples"]}))
        return
    print(json.dumps({"results": [{"checks": [{"name": name, "passed": bool(ok), "error": error}
                                              for name, ok, error in checklist(item["truth"], item["answer"])]}
                                  for item in request["items"]]}))


if __name__ == "__main__":
    main()
