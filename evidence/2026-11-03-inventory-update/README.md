# Two-sample inventory and cash dataset, 3 November 2026

User requested two small photo/audio inventory updates for the local Unsloth
worker, then added unit prices and selected cash accounting: purchases spend
cash, sales receive it. Dataset construction only; no live trial authorized.

Built offline with `scripts/inventory_update/build.py`: six 1024 x 768 RGB JPEGs,
two local Kokoro voice requests, starting JSON, controller truth, a standalone
eight-dimension check, per-source attribution, unannotated overview, annotated
HTML review with audio and an allowlisted ZIP without runtime state. Generated
assets remain gitignored under `.lx/inventory-update-local/`.

- Sample 01: buy 2 + 3 cups at $2.50. Stock 4 -> 9; cash $50.00 -> $37.50.
- Sample 02: sell 1 + 2 bottles at $3.25. Stock 10 -> 7; cash $25.00 -> $34.75.
- Each sample has two distinct object scenes, each with one distractor, and a
  third image rendering the inventory and account. The audio contains no count.
- Both samples are stream samples, with no held-out set. No measured baseline,
  improvement or generalization claim. Localization is checked; actual SAM and
  Transcribe usage needs an approved trial and trace review.

Validation on native Windows:

- All six images visually reviewed. The incomplete bottle annotation 88783 was
  excluded and replaced before final validation. Inventory/cash sheets are
  readable, and object scenes have no overlap.
- Local WAVs: mono, 16-bit PCM, 24 kHz, 18.67 s and 19.11 s; duration and signal
  checked. Text was synthesized from kept scripts; no independent transcription
  or listening verification was performed.
- Builder verification: 40 negative cases, good and reordered answers pass,
  blanks fail, quantities/cash independently reconcile, layouts match truth,
  source IDs reconcile and all 12 cutouts are distinct. Includes reversed cash,
  unchanged/wrong balance, changed price, booleans, fractions of cents,
  missing/duplicate/misplaced boxes, extra fields and cross-sample answers.
- LX `check_test`: 2 samples, 8 checks each, 22 cases, blank_score 0,
  wrong_score 0, no weaknesses. `ready`: true, no problems or blocking reason.
- Draft v4 selects the existing LFM3-VL-2B Unsloth worker, Spark improver,
  Transcribe and SAM. No approval, hints or worker note. Default limits/budgets
  left unchanged (600 s per call, 8192 reply tokens on this worker).
- ZIP integrity/allowlist, JSON syntax, Python compile and Git whitespace checks
  passed. No full application suite rerun: no application/loop code changed.
- No provider calls or spending. Unsloth serving state was not tested.

Kokoro initially failed because phonemizer could not write its DLL into the
restricted default temporary directory. Giving only the child speech process a
temporary folder under the generated review directory fixed it without changing
system settings or requiring elevation.
