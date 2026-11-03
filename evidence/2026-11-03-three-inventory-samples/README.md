# Three inventory samples with user photos and conversational speech

User requested four supplied photographs distributed across three samples,
natural spoken notes, a harder sample and a new Spark1.3/Transcribe run.
Prepared a fresh **inventory-update-three** workspace; previous datasets,
approvals, frozen runs and media are unchanged.

| Source sample | Photo change | Transaction | Expected stock / cash |
|---|---|---|---|
| sample-01 | Add31132 as photo3 (scissors/two cups) | Buy7 cups | Cup4 ->11; cash50 ->32.50 USD |
| sample-02 | Add31133 as photo3 (two chocolates/two bottled drinks) | Sell5 bottles | Bottle10 ->5; cash25 ->41.25 USD |
| sample-03 |31130 stationery +31131 cutlery | Cancel pencil restock; sell2 forks | Fork6 ->4; cash40 ->44.50 USD |

All inventory keys have unit prices. Additional scissors/chocolate keys are
preserved in the first two samples; third input includes earphones (one set),
glue_stick, pencil, spoon and fork. Prices are constructed USD transaction
prices from the starting JSON, not prices read from packaging. Counts are not
spoken. Reviewer scripts and truth are controller-only.

The harder note begins with a proposed pencil restock, then explicitly cancels
it and records a fork cash sale. The model must identify the final request,
distinguish forks from adjacent spoons, and preserve the cancelled pencil stock.
This is a designed challenge; measured difficulty awaits the live result.

The three new voice clips are **offline Kokoro synthetic speech**, with
conversational phrasing and a correction rather than JSON/tool instructions.
Durations11.24/10.33/14.59 seconds, mono16-bit24kHz. Voices af_sarah/am_adam;
naturalness is not established by waveform checks alone. The reviewer page has
audio controls for a person to listen. Actual Transcribe output is tested by
the requested live run, not substituted with reviewer scripts.

User photos are rotated using EXIF orientation, proportionally resized to
768x1024 and stripped of metadata. No crop, content synthesis or changes to
the original files. Source filename/hash and processing are in attribution.json.
Manual visible-object boxes exclude shadows; all four normalized images and
the new cup/bottle/fork target boxes were visually inspected. The original four
1024x768 COCO composites are copied unchanged, with their original attribution.

The standalone eight-rule check accepts controller-only photo_sizes metadata,
allowing photo3 and portrait bounds while retaining the same six-field answer.
Old truth without this metadata defaults to two1024x768 photos. Task text says
supplied photos/photo numbers instead of exactly two; no counting procedure or
sample answers added to the baseline. The new stream has three samples.

Validation: **63 negative cases**, seven malformed dimension cases refused,
valid and reordered evidence pass, blank/wrong zero, image/WAV checks pass.
**Eight previous Spark answers give identical old/new checker results**; all
ten old sample files match the previous frozen snapshot. New LX ready=true,
**33 check self-test cases**, no weaknesses or hint/eval drift. Worker-message
proof confirms photo counts3/3/2 and exact starting JSON with no controller
truth in user data. Builder/check compile and whitespace pass; no full suite.
ZIP CRC and sample file counts pass. One initial ZIP validation ran before
closing the writer; fixed and rebuilt successfully before use.

Offline builder: scripts/inventory_update/expand.py, documented in the adjacent
README. Output relative to repository: `.lx/inventory-update-three/`.
Review assets: review.html, overview.jpg, annotated photos, input proof and
validation.json; export inventory-update-three.zip excludes runtime state and
speech-library temporary files. Input data and generated media are untracked.

Configured **specv3**, Spark Contributor worker/improver/judge, Muse Voice
Transcribe and SAM3.1, same five judge criteria, concise improver hint/worker
identity note. Worker allowance32768, paid improver/judge8192,1800s calls,
four-hour run, **$1 loop plus separate $0.25 judge cap**, unchanged spending
caps. Full returned reasoning remains unclipped. User explicitly requested one
new live run; its launch/outcome is recorded separately. No live model calls
during dataset preparation or validation.
