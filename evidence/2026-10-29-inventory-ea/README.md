# T8 E-A inventory stream — live run (29 Oct 2026)

Approved by the user ("Approve, run T8"); request reviewed in
[docs/inventory-stream-review.md](../../docs/inventory-stream-review.md).
Harness² k=1, sequential, library carried; Spark as agent, improver and
reflector; frozen `inventory-mix-v1` (hash `db6c70f6...c322`), 6 stream
samples. 56 minutes, all 6 samples `ok`, no failures.

**Spend $0.315451638** (worker 255 calls $0.0725, improver 179 + reflection
47 calls $0.1098, SAM 53 calls $0.1325, Transcribe 6 calls $0.0007; 93
perception cache hits). Ledger after: $0.821485008 spent, $19.072838192
remaining, 1598 settled, 1 unknown (the old T6 one).

## Result (controller-side checklist; the improver never saw scores)

| Sample | Base | Harness² | Gain |
|---|---|---|---|
| fsc-stream-01 (count) | 0.89 | 0.78 | -0.11 |
| mix-stream-colourcount-01 | 1.00 | 1.00 | 0 |
| mix-stream-inventory-01 | 0.00 | 0.00 | 0 |
| fsc-stream-02 (count) | 0.00 | 1.00 | +1.00 |
| mix-stream-colourcount-02 | 0.00 | 0.67 | +0.67 |
| mix-stream-inventory-02 | 0.00 | 0.50 | +0.50 |
| **Mean** | **0.315** | **0.657** | **+0.34** |

## What the improver did

Every sample adopted an edit (`c1`, the composed final; k=1 needs no
selector). The edit is one on-demand skill (`skills/inventory/SKILL.md`,
`photo-inventory` on the first sample) plus a guardrail trigger line. The
final harness is in `library/mix-stream-colourcount-01/harness/`. The skill:
transcribe first and classify the request; view each photo once; segment
once per photo with the most specific concept (colour-qualified for colour
requests); one bounded retry, then trust the eye count and never loop;
output the exact JSON and reconcile totals before finishing.

This targets the failure T7 found. Base runs on the stream: 4 of 6 stopped
without an answer (turn or tool-call cap, 12-21 `view` calls). Skill
candidates: 16 of 18 rollouts answered (3 per sample: c1, inv1, dom1); only
the 5-photo inventory `mix-stream-inventory-01` failed in all three. Two
candidates tried `run_script` helpers; none was adopted.

## Caveats

- One base rollout and one adopted rollout per sample: a 0 → 1.0 gain mixes
  the skill's effect with run-to-run variance. The candidate pattern (16/18
  answered vs 2/6 base) makes the effect more than luck, but not measured.
- The experience entries mark every edit "confounded" (the trigger line and
  the skill always arrived together).
- The regression on fsc-stream-01 is one photo's count (dense FSC photo).
- Same-sample improvement only; no held-out claim (that is E-B).

Files: `summary.json`, `usage.json`, `started.json`, `ea_report.json`,
`library/<sample>/` (EXPERIENCE.md and adopted harness files).
