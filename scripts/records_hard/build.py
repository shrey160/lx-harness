"""Harder order records for a small model: the same task and check, in messier records.

Writes `sample-NN/` folders with request.txt and truth.json, the text-request
layout. Each record replaces an older order, gives the customer surname first,
and changes some of its values partway through, so a right answer takes the
latest value and not the first one seen. All names and numbers are fictional.
"""
import argparse
import json
from pathlib import Path
import random
from datetime import date, timedelta

NAMES = ("Mira Solis", "Theo Wren", "Asha Bell", "Niko Vale", "Lena Moss", "Omar Finch", "Rhea Quinn", "Ivo Reed", "Zara North", "Emil Lake")
TAGS = ("backorder", "fragile", "gift", "priority", "subscription", "wholesale")
CURRENCIES = {"USD": ("US$", "dollars"), "INR": ("₹", "rupees"), "EUR": ("€", "euros")}
SEED = 20261102
LAYOUTS = (
    """From the orders desk
Re: {old_id}, cancelled, reissued as {order_id}

Customer on file: {customer}
Placed {date}
First billed {currency} {old_amount}, revised after the credit note to {amount}
Tel {old_phone} is out of service. {phone_line}
Labels: {tags}. {drop}""",
    """{order_id} (replaces {old_id})
{date} / {customer}
{phone_line} Earlier number {old_phone} no longer works.
Charge was {old_amount} {currency}; now {amount} {currency} after the adjustment.
{drop} Flags on the ticket: {tags}.""",
)


def grouped(minor, currency):
    """An amount as this currency's clerks write it; the euro uses a decimal comma."""
    major, fraction = divmod(minor, 100)
    text = f"{major:,}"
    if currency == "INR" and major >= 100000:
        head, tail = str(major)[:-3], str(major)[-3:]
        pairs = [head[max(i-2, 0):i] for i in range(len(head), 0, -2)][::-1]
        text = ",".join(pairs+[tail])
    return f"{text.replace(',', '.')},{fraction:02d}" if currency == "EUR" else f"{text}.{fraction:02d}"


def record(rng, index):
    order_id, old_id = (f"LX-{rng.randrange(10, 100)}{rng.randrange(100000, 1000000)}" for _ in range(2))
    customer = rng.choice(NAMES)
    first, surname = customer.split(" ")
    day = date(2021, 1, 1)+timedelta(days=rng.randrange(365*5))
    code = rng.choice(tuple(CURRENCIES))
    written = rng.choice(CURRENCIES[code])
    minor, old_minor = rng.randint(12500, 987654), rng.randint(12500, 987654)
    new_digits, old_digits = (f"202555{number:04d}" for number in rng.sample(range(100, 200), 2))
    has_phone = rng.random() >= 0.25
    kept = rng.sample(TAGS, rng.randint(2, 3))
    dropped = rng.choice([tag for tag in TAGS if tag not in kept])
    shown = [tag.upper() if rng.randrange(2) else tag.title() for tag in kept+[dropped]]
    rng.shuffle(shown)
    text = LAYOUTS[index % len(LAYOUTS)].format(
        order_id=order_id, old_id=old_id, customer=f"{surname.upper()}, {first.lower()}", date=day.strftime("%d.%m.%y"),
        currency=written, amount=grouped(minor, code), old_amount=grouped(old_minor, code),
        old_phone=f"({old_digits[:3]}) {old_digits[3:6]} {old_digits[6:]}",
        phone_line=f"New number {new_digits[:3]}.{new_digits[3:6]}.{new_digits[6:]}." if has_phone else "No new number given.",
        tags=" / ".join(shown), drop=f"Take off {dropped.upper()}.")
    return text, {"order_id": order_id, "customer": customer, "date": day.isoformat(), "currency": code, "amount_minor": minor,
                  "phone": f"+1{new_digits}" if has_phone else None, "tags": sorted(kept)}


def write_samples(root, count, seed=SEED):
    rng = random.Random(seed)
    for index in range(count):
        request, truth = record(rng, index)
        folder = Path(root)/f"sample-{index+1:02d}"
        folder.mkdir(parents=True)
        (folder/"request.txt").write_text(request, encoding="utf-8", newline="")
        (folder/"truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("target", type=Path, help="the workspace folder for the sample folders")
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    write_samples(args.target, args.count, args.seed)


if __name__ == "__main__":
    main()
