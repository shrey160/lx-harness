# T7 live calibration request — PROD-004 inventory (prepared 29 Oct 2026)

Status: **PREPARED, NOT RUN.** Needs the user's explicit approval.
Controller: `lx.inventory_calibration` (offline-tested in
`tests/test_inventory_calibration.py`). Authority: D-008 (live SAM 3.1 and
Muse Voice Transcribe as fixed tools, each live step approved separately under
the shared $20 ledger), D-010 (PROD-004) and D-012 (built samples).

## Why

Before the paper loop (E-A) spends money, check that the base harness leaves
room to improve. If Spark with the base harness already scores near 1.0, the
loop cannot show gains. If it scores near 0, the cause is more likely broken
tool wiring than a skill gap.

**Gate (pre-registered):** mean checklist score over the 6 samples in
**0.30–0.80**.
- In band → prepare T8 (E-A on the 6 stream samples).
- Above → make the samples harder offline (denser FSC photos, more objects per
  composite).
- Below → read the traces for broken tool binding before changing anything.

## Fixed inputs

Frozen dataset `inventory-mix-v1`, approval hash
`db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322`
(manifest, truth, requests and media hashes; checked at start). Only the
calibration split; the stream, check and held-out samples stay unseen.

| Sample | Kind of request | Photos | Source |
|---|---|---|---|
| `fsc-calibration-01` | count | 5 | FSC-147 (dots) |
| `fsc-calibration-02` | count | 3 | FSC-147 |
| `mix-calibration-colourcount-01` | colour count | 3 | COCO cutout composites |
| `mix-calibration-colourcount-02` | colour count | 4 | composites |
| `mix-calibration-inventory-01` | inventory | 5 | composites |
| `mix-calibration-inventory-02` | inventory | 4 | composites |

The exact requests and answers are on the review page
(`main_LX/.lx/views/inventory-mix-v1.html`). The worker never sees them: the
request reaches it only as Kokoro speech.

Data sent to Meta: FSC-147 photos (public research data, D-006(c)); composites
of COCO objects licensed CC BY, no known copyright or US government, with no
labelled people, on generated backgrounds, metadata stripped; synthetic
speech. No personal or client data. The SAM reply reports `"store": true`.

## Run

- **Arm:** Spark (`muse-spark-1.3-contributor`) with the base harness:
  - the base system prompt;
  - empty guardrails, memory and tool guidance;
  - no skills or scripts. The `load_skill` and `run_script` tools are listed
    but have nothing to load.
- **Per sample:** `caps(n)` = 4+3n turns and 6+6n tool calls (13–19 turns).
  - At most 2 view images stay in a request, each at most 400 KB.
  - Replies cut off at the output limit end the sample as stopped, keeping
    its trace.
- **Scoring:** the checklist score (fraction of checks passed: schema, request,
  coverage, one check per photo, consistency, answer) and exact-answer
  correctness.
- **Errors:** a model error scores 0 for that sample. 3 errors stop the run.
- **Limits:**

  | | Limit |
  |---|---|
  | Spark chat calls | 120 (worst case at the caps is 96) |
  | SAM calls | 120 |
  | Transcribe calls | 8 |

  Repeated SAM calls with the same photo and phrase are cached in
  `.lx/perception-cache/inventory-mix-v1` and cost nothing.
- **Budget:**
  - Scope `inventory-calibration-v1`, cap **$0.60**. This covers the
    $0.1056768 reservation of the one Spark call in flight.
  - Worst case at the limits is about $0.55 (SAM 120 × $0.0025 = $0.30, plus
    Spark).
  - Expected actual spend: **about $0.10–0.30**. T6's two agent runs cost
    $0.0195 in total.
- **Guards:**
  - exact approval hash;
  - shared ledger at or after the checkpoint ($0.36728662 spent, 941
    settled);
  - no earlier rows in the scope;
  - an exclusive `started.json` marker (no repeats);
  - fixed endpoints and models; retries off;
  - error bodies withheld; key loaded at runtime only.
- **Command** (WSL, after approval):
  `wsl -e bash -lc 'cd /mnt/c/Shrey_Projs/hackathons/main_LX && PYTHONPATH=src .venv/bin/python -m lx.inventory_calibration --approve-dataset db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322'`

## Outputs

The run writes `main_LX/.lx/inventory-calibration-v1/calibration.json` and
`usage.json`:
- per sample: answer, score, failed check names, turns, tool calls and the
  reason a run stopped, if it did;
- the mean overall and per request type, the correct count, the gate result
  and the decision;
- usage, cache hits and the ledger before and after.

Evidence is committed after the run.

## Known limits

- 6 samples give a coarse estimate: one sample moves the mean by up to 0.17.
- Colour answers are small (2), and the composites look pasted.
- FSC-147 photos are low resolution (384 px tall) and dense.
- This calibrates Spark only; E-C (Gemma 4 E4B) is checked separately later.
