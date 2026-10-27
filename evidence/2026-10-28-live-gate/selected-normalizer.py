import json
import sys
from decimal import Decimal

with open(sys.argv[1], encoding="utf-8") as source:
    records = json.load(source)["records"]
amounts = [Decimal(row["amount"]) for row in records]
total = sum(amounts, Decimal("0"))
normalized = [
    {"id": row["id"], "amount": format(amount, ".2f")}
    for row, amount in zip(records, amounts)
]
print(json.dumps({"records": normalized, "count": len(normalized), "total": format(total, ".2f")},
                 sort_keys=True, separators=(",", ":")))
