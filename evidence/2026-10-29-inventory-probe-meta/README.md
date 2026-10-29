# T6 live probe, part meta (29 Oct 2026)

Approved by the user ("Approve, run meta"); run as reviewed in
`docs/inventory-probe-review.md` at `085ef66`. Scope `inventory-probe-v1`:
30 settled calls, **$0.019541888** (SAM 6 × $0.0025, Transcribe $0.000204,
Spark 22 calls $0.004337888), no unknown calls. Shared ledger after:
$0.36728662 spent, $0.1056768 still held by the old S7 call, 933 settled.

## Wire paths (what the probe was for)

- **View images after tool messages work on Spark.** The view check called
  `view` on photo 1 and described it ("two round baked cakes in pans ... on a
  gas stovetop"), which matches the photo. Both agent runs used `view` many
  times without transport errors.
- **Skills and scripts work live.** With the probe harness, Spark loaded the
  `inventory-check` skill first and called `run_script` in the sandbox. Its
  first two calls exited 1 because it guessed the input fields (the skill does
  not document them); the third succeeded. That is exactly the kind of gap the
  improver should fix (document the script's input in the skill).
- **SAM per photo works on normalised COCO JPEGs**; box sizes match the images.
- **Transcribe:** sample A exact; sample B "the total the total number of
  bananas" (WER 0.22, harmless).
- Agent: one out-of-range zoom box (y beyond the 480 px photo) returned an
  error to the agent, which carried on.

## Finding: COCO counts are not reliable truth for this task

| Sample | Photo | COCO | SAM | Seen by eye |
|---|---|---|---|---|
| A bowls | 1 | 2 | 0 | two cake pans on a stove (COCO calls them bowls) |
| A bowls | 2 | 0 | 0 | living room; a small dish on a side table |
| A bowls | 3 | 1 | 0 | a white soup tureen |
| B bananas | 2 | 4 | 14 | a close-up bunch, about 6-7 bananas; SAM adds overlapping duplicate boxes |
| B bananas | 3 | 1 | 1 | one peeled banana in a bowl |

Scores: A (base harness) 0.625, answered 0 (trusted SAM); B (skill +
script) 0.75, answered 15. Both "wrong", but the truth itself is debatable
in 4 of 6 photos: COCO's labels follow its own conventions (pans as bowls,
a bunch as a few instances) and are not exact counts. Exact-count scoring
against COCO would measure label conventions more than the harness.

Not capability evidence; two samples only.
