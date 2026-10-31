# Spark calibration on the new agent loop (31 Oct 2026)

Live run `4d32acd19c7f`, requested in
[docs/spark-calibration-review.md](../../docs/spark-calibration-review.md). Muse Spark 1.3
Contributor on the Meta API in all roles, 6 stream samples, one run per arm. Spend
$0.3735 over 38.7 minutes, with none held.

| Sample | Type | Base | After the loop | Gain |
|---|---|---|---|---|
| sample-01 | Inventory | 0.44 | 0.44 | 0 |
| sample-02 | Inventory | 0.50 | 0.63 | +0.13 |
| sample-03 | Colour count | 1.00 | 1.00 | 0 |
| sample-04 | Colour count | 1.00 | 1.00 | 0 |
| sample-05 | Count | 1.00 | 1.00 | 0 |
| sample-06 | Count | 1.00 | 0.88 | -0.13 |
| **Mean** | | **0.824** | **0.824** | **0** |

## Against the rules set before the run

- **The loop fix works: met.** All 6 base runs answered, against 2 of 6 in T8, and none
  of the 24 agent runs stopped at a limit.
- **New baseline: 0.824.** That is above the 0.80 line, so these samples leave the loop
  little room.
- **The loop adds: not met.** One sample rose, one fell and four tied.
- **Images:** Spark accepted up to 6 in a request. The cap of 8 was not reached.

## What else it showed

- All lost base points are on the two open-ended inventory samples. Part of that is
  naming: the agent said "computer mouse" and "glass" where the truth says "mouse" and
  "cup". This was not changed, and the check is unchanged.
- Spark returned no reasoning field on any turn. Its written text carried the notes.
- No reply needed a second turn, so broken tool-call text did not occur on Spark.
- SAM was 56% of the spend: 84 calls, $0.21. One candidate made 45 of them in one agent
  run, guessing concepts. Nothing limits SAM per agent run.
- 2 of 26 improver sessions hit the 12-turn limit, on samples 02 and 05. The run went on.
- `run_script` was refused 3 times on a wrong input shape, the same problem as T8.

## Limits

One run per arm on 6 samples cannot separate a small gain from noise. These are samples
the loop adapted on. The comparison with T8 (0.315 to 0.657) is rough: the loop, limits
and controller message all changed. The kept harness comes from the sample with the
largest gain after grading, so it reads high.

Files: `report.json`.
