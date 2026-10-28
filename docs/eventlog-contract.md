# Order event-log reconciliation (PROD-002 E1-R, R1)

The records normalization task saturated live: the base model scored 40/40
records and 280/280 fields. Every field was a single stated transform, and the
task had no difficulty setting. This harder task is generated offline only. No
model has seen it yet, and nothing here is capability evidence.

## Task

Each case is one event log for a target order. The worker replays the log
under the ten explicit rules in `lx.eventlog.CONTRACT`:

- Filter out other orders.
- Count each event ID once.
- Apply events in timestamp order, not log order.
- Convert foreign prices with the listed exchange rates.
- Track the latest discount code.
- Ignore line and discount events while the order is cancelled.
- Cap refunds at the order total.
- Round half-to-even.

The worker returns `order_id`, `status`, `lines`, `subtotal_minor`,
`discount_minor`, `total_minor` and `refunded_minor`. Base and candidate
receive the full contract; nothing is withheld. The controller computes truth
with its own `replay` and stores it outside the worker input directory. The
`verify_case` verifier gives one point per correct, strictly typed field, out of
7 plus any unexpected fields. Its error messages never contain expected values.

## Difficulty levels

| Level | Target events | Rules exercised | Log noise |
|---|---|---|---|
| 1 | 3-4 | add, set quantity | in order |
| 2 | 5-7 | + remove, discount | shuffled |
| 3 | 7-10 | + cancel/reinstate, refund | + 1 duplicate, 2 events from a near-identical order ID |
| 4 | 10-14 | + foreign-currency prices | + 2 duplicates, 4 distractors, rate table |

Seed 20261029, 40 test cases per level:

| Level | Average log lines | Cancelled | Discount applied | Refund cap binds |
|---|---|---|---|---|
| L1 | 4.5 | 0 | 0 | 0 |
| L2 | 7.1 | 0 | 21 | 0 |
| L3 | 12.4 | 17 | 9 | 4 |
| L4 | 18.7 | 17 | 17 | 5 |

## Splits

`build_eventlog_dataset(root, loop_count=40, verify_count=5, test_count=40,
calibration_count=0, difficulty=3, seed=20261029)` takes each count in the
range 0..1000.

- **loop:** adaptation plus retention.
- **verify:** a 5-item check run in each trial. It is too small to carry the
  success criterion.
- **test:** frozen; scored once after all trials. This set decides success.
- **calibration:** used only for the base-only difficulty ladder.

Separation between splits:

- Each split has its own random stream, so changing one count leaves the
  other splits unchanged.
- Each split draws SKUs from a disjoint number range.
- The builder raises an error if an order ID appears in two splits.
- Loop logs use the `pipe` and `prose` wordings. The three held-out splits use
  `kv` and `bracket`, which loop items never see.

The controller directory holds `ground_truth.json`, `events.json` (the parsed
logs and rates) and `dataset.json` (difficulty, seed and counts).

## Plausible wrong answers rejected

`tests/test_eventlog.py` applies seven plausible mistakes to generated level-4
cases and shows the verifier rejects each one:

- log order instead of timestamp order
- counting duplicate events
- applying distractor-order events
- ignoring a cancellation
- uncapped refunds
- ignoring exchange rates
- half-up instead of half-even rounding (a hand-built case)

## Trial wiring (R2)

`lx.eventlog_workflow` plugs the task into the shared adapter. The adapter
only knows `dev` and `heldout`, so the reader maps `loop` to `dev` and
`verify` to `heldout`. `test` and `calibration` keep their names, which means
a trial never stages or evaluates them.

Run it with `run_trials(..., task="eventlog")`, or from the command line:
`python -m lx.trials --task eventlog`.

Each trial:
1. Adapts on the first k loop items.
2. Keeps the candidate only if its mean score on all loop items is strictly
   higher than base.
3. Reports the 5 verify items as a smoke check.

After all trials, `final_test` scores base and each trial's retained package
once on the frozen test split. The success criterion is computed on those
test gains. A package identical to base reuses the base scores.

## Call budget (R2 measurement)

With the offline double, every practice item adds 8 calls: 4 worker
rollouts, 3 improver sessions and 1 reflection. Each trial also has a fixed
90 calls for evaluating both harnesses on 40 loop and 5 verify items.

| k | Calls per trial (measured offline) | Live per trial (estimated) |
|---|---|---|
| 1 | 98 | about 146 |
| 2 | 106 | about 202 |
| 4 | 122 | about 314 |

Live improver sessions take several turns each, up to 12. The estimate
assumes about 13 calls per session. That figure is inferred from the 28 Oct
live images job, which made about 102 improver calls across 8 sessions.

Under the existing 200-call limit, only k=1 fits with margin. Using k=4 would
need the R4 request to raise the per-trial limit, for example to 350. The
dollar cost stays small: the improver cost about $0.012 per practice item on
the old task.

The final test runs in its own budget scope: 40 calls for base plus 40 for
each trial that kept a changed package, so at most 240.

Evidence: `evidence/2026-10-29-eventlog-offline/call-measurement.json`.

## Not done yet

- **R3:** the live base-only difficulty calibration, which needs a new
  approval. It will also measure the worker cost per event-log item.
- **R4:** live trials. The request must fix k and the per-trial call limit.

The previous records domain is unchanged.
