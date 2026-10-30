# Graded inventory rerun on OpenRouter (prepared 31 Oct 2026)

Status: **STOPPED, NO RESULT.** Run `a92a74bbfb4a` started 31 Oct at the user's direction
and was stopped in its first sample when the user paused Glimmer runs to work on Spark.
See `evidence/2026-10-31-openrouter-rerun-stopped`.

## Why

The last graded run (`d52ee4a2114c`, 30 Oct) scored 0.00 for $1.27. The raised limits
have not been tried live, and fix list item 29 needs a live task run. This run answers
one question: with the limits raised and the view loop addressed, does Muse Glimmer
answer the inventory task, and does the loop improve on its base?

It is not a held-out claim. Every score comes from the samples the loop adapts on.

## What the last run showed

Read from its saved traces and ledger, with no calls:

| Finding | Number |
|---|---|
| Agent runs that stopped at a limit with no answer | 15 of 20 |
| Agent runs that ended on one call with broken tool-call text | 5 of 20 |
| Agent runs with a usable answer | 0 of 20 |
| `view` calls, against 15 `transcribe` and 58 `segment` | 201 |

**The view loop is partly ours.** Only the latest 2 view images stay in the
conversation, and each removed image was replaced with "call view again if needed".
On samples of 3 to 7 photos Glimmer did that, cycling through photos 1, 2, 3, 4, 1, 2.
Raising the limits alone would have paid for a longer loop, so that note now tells the
agent it has seen the photo and to look again only for one detail. The 2-image limit is
unchanged. No live call has tested this wording, so it may not be enough.

**The broken replies are not fixed.** Text such as `">\n</atem:invoke>` came back as the
reply on the first turn, and the agent loop reads it as a final answer. About a quarter
of runs may be lost this way again. It also counts as an answer for the early stop.

## Fixed inputs

- **Workspace:** `.lx/workspaces/inventory-openrouter`, spec version 3, in live mode.
- **Samples:** the same 6 `inventory-mix-v1` stream samples: public photos, composites
  and synthetic speech, with no personal data.
- **Roles:** `meta/muse-glimmer-30b` on OpenRouter for all three, $0.30 and $1.20 per
  million. SAM 3.1 and Transcribe stay on the Meta API.
- **Scoring:** check file `51be3b91...4325`, unchanged, after the run. The improver
  never sees scores.

## Limits

All defaults, as the user set them, except the early stop.

| Limit | Value |
|---|---|
| Agent turns per photo | 12, plus 4 (40 to 88 a run) |
| Agent tool calls per photo | 12, plus 6 (42 to 90 a run) |
| **Stop after agent runs in a row with no answer** | **4** (default 8) |
| Improver turns and session | 30 and 900 seconds |
| One model call, whole run | 600 seconds, 4 hours |
| Model, SAM and Transcribe calls | 1,400, 600 and 60 |
| **Run budget** | **$3.00** |

The early stop of 4 is the gate: if the loop repeats, the run ends within about the
first sample. The budget stays at $3 because the workspace has $3.68 left, and the
OpenRouter key had a $5 limit on 30 Oct with about $1.43 used, so about $3.57 left.

## Expected cost and time

Estimates from the last run's per-call figures. Only 13 to 16 turns were measured, so
anything longer is extrapolated, and they use entered prices, not OpenRouter's bill.

| Outcome | Spend | Time |
|---|---|---|
| Loop repeats, early stop fires | $0.50 to $1.00 | 15 to 30 minutes |
| Agents answer, run finishes | $1.50 to $2.50 | 1 to 2 hours |
| Spend reaches the $3 budget | $3.00 | Up to 4 hours |

One agent run that loops to its limit costs about $0.07 to $0.25. One improver session
at 30 turns may cost up to $0.15 to $0.20.

**Risk:** if the run hits its budget, or stops early, the loop has not finished, so
there is no graded score, only health and traces. Broken replies count as answers, so
they can delay the early stop past the first sample.

## How to read the result

Decided before the run:

- **Works:** at least 4 of 6 base runs give a valid answer, and the graded mean is above
  0. The gain on those samples is then the loop's result. Spark's T8 was 0.31 to 0.66.
- **Still looping:** the early stop fires. Next step is the image limit, not more turns.
- **Broken replies dominate:** the next step is handling them in the client.

One run per arm on 6 samples cannot separate a small gain from noise.

## Steps

1. The user rules on the $0.049 held on this workspace, after checking the bill. It only
   reduces what is left, so it does not block the run.
2. Start the app with Docker Desktop running: `.\scripts\serve.ps1`.
3. Open Inventory on Glimmer, Spec tab: version 3 is saved. Review and approve, then
   start the run on the Runs tab. Approval freezes the samples; only a person can do it.
4. Results go to a new evidence folder, as for the last run.
