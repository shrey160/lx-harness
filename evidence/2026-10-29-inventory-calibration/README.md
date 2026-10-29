# T7 inventory calibration — live run (29 Oct 2026)

Approved by the user ("Approve, run T7"); request reviewed in
[docs/inventory-calibration-review.md](../../docs/inventory-calibration-review.md).
Dataset `inventory-mix-v1`, hash `db6c70f6...c322`. Spark with the base
harness on the 6 calibration samples. Spend $0.13874675 (Spark $0.0205, SAM
47 calls $0.1175, Transcribe 6 calls $0.0008); no errors. Ledger after the run:
$0.50603337 spent, $19.38828983 remaining.

| Sample | Score | Correct | Notes |
|---|---|---|---|
| fsc-calibration-01 (candles, 5 photos) | 0.60 | no | undercounted 3 photos by 1-2 |
| fsc-calibration-02 (cupcakes, 3 photos) | 1.00 | yes | exact |
| mix-calibration-colourcount-01 (orange umbrellas) | 0.75 | no | missed the folded orange umbrella |
| mix-calibration-colourcount-02 (orange remotes) | 0.00 | no | hit the 30-tool-call cap, no answer |
| mix-calibration-inventory-01 | 0.00 | no | hit the 36-tool-call cap, no answer |
| mix-calibration-inventory-02 | 0.00 | no | hit the 30-tool-call cap, no answer |

**Mean 0.392 → in the 0.30-0.80 band (gate passed).** By type: count 0.80,
colour 0.375, inventory 0.00.

Reading: half the samples spent their whole tool budget (mostly `view`, 15-21
calls each) and never answered. That is the kind of failure harness edits can
fix (tool budgeting, "answer before the cap", segment-first strategies), so
the headroom is real rather than a broken wiring sign: every tool call
succeeded but one box argument. Counting errors on dense FSC photos remain a
second, harder target.
