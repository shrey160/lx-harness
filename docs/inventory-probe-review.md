# T6 live probe request — PROD-004 inventory (prepared 29 Oct 2026)

Status: **PREPARED, NOT RUN.** Each part needs the user's explicit approval.
Controller: `lx.inventory_probe` (offline-tested in
`tests/test_inventory_probe.py`). Authority: D-008 (live SAM 3.1 and Muse
Voice Transcribe as fixed tools, a registered local provider as the agent,
each live step approved separately under the shared $20 ledger) and D-010
(PROD-004 direction).

## Why

PROD-004 added wire paths no live run has used yet:

1. **Images after tool results:** `view` returns a JPEG that the loop sends as
   a `user` message right after the turn's `tool` messages. Do Spark and the
   local server accept that order and actually look at the image?
2. **Multi-photo SAM:** one SAM call per photo, normalised COCO JPEGs (up to
   1024 px); counts against COCO labels.
3. **Skills and scripts live:** given a harness with a skill and a helper
   script, does Spark read the skill and call `run_script`, and does the
   sandbox return the script's JSON inside a live run?
4. **The local model** (Gemma 4 E2B) with the larger tool set (6 tools) and view images.

Not a capability measurement and no harness changes; T7 calibration
measures the base harness.

## Fixed inputs

Frozen dataset `inventory-coco-v1`, approval hash
`1c673f8d2445afe2c675900a344b94942cacb3a89552d88961d3b96667767fcc`
(manifest, truth, requests and media hashes; checked at start). Two of the
five calibration samples, so held-out stays unseen:

| Sample | Spoken request (Kokoro) | Photos | COCO truth per photo | Answer |
|---|---|---|---|---|
| `coco-calibration-04` (A) | "Count all the bowls in these photos." | 3 | 2, 0, 1 | 3 |
| `coco-calibration-05` (B) | "Can you tell me the total number of bananas?" | 3 | 0, 4, 1 | 5 |

Data sent: public COCO photos (CC BY / CC BY-SA, no labelled people,
metadata stripped) and synthetic speech. No personal or client data. The
SAM reply reports `"store": true`.

## Part `meta` (WSL controller, shared pilot ledger)

| Step | Calls | Limit |
|---|---|---|
| Transcribe both requests | 2 clips (about 2 s each) | 4 calls |
| SAM, requested kind on every photo of A and B | 6 images | 30 SAM calls in total |
| View check on A: "call view for photo 1, then describe it in one sentence" | at most 3 turns, 2 tool calls | — |
| Spark agent, base harness, on A | `caps(3)`: 13 turns, 24 tool calls | — |
| Spark agent, probe harness (preview skill `inventory-check`, its guardrail line, script `sum_counts.py`), on B | same caps; the script runs in the WSL sandbox | — |
| Spark chat calls in total | | 30 |

Repeated SAM/Transcribe calls with the same photo and phrase reuse the
cache (cost nothing); each new phrase costs one SAM image. At most 2 view
images stay in a request (transport `max_images=2`, each at most 400 KB).

- **Budget:** scope `inventory-probe-v1`, cap **$0.40** (covers the
  $0.1056768 reservation of the one Spark call in flight). Expected actual
  spend about $0.03-0.12: SAM at most 30 × $0.0025 = $0.075, Transcribe under
  $0.001, Spark about 30 calls at a few thousand tokens plus images each.
- **Guards:** exact approval hash; shared ledger at or after the checkpoint
  ($0.347744732 spent, 903 settled); no earlier rows in the scope; exclusive
  `started.json` marker (no repeats); fixed endpoints and models; retries off;
  error bodies withheld; key loaded at runtime only; scripts only in the
  sandbox (no network, no host files).
- **Command** (after approval):
  `wsl -e bash -lc 'cd /mnt/c/Shrey_Projs/hackathons/main_LX && PYTHONPATH=src .venv/bin/python -m lx.inventory_probe --part meta --approve-dataset 1c673f8d2445afe2c675900a344b94942cacb3a89552d88961d3b96667767fcc'`

## Part `local` (Windows controller; needs the local server running)

- **Amended 29 Oct 2026 (user: "Use E2B instead")**: the server had
  `gemma-4-E2B-it-GGUF` loaded, the smaller Gemma 4, so the local agent is
  E2B rather than the originally reviewed E4B; nothing else changed.
- Agent: `gemma-4-E2B-it-GGUF` at `http://127.0.0.1:8888/v1` (registry
  provider entry, no key, $0 tokens), same 6 tools: the view check and the
  base-harness run on A. No script run (the sandbox needs WSL).
- Limits: 16 chat calls, 16 SAM, 2 Transcribe; scope
  `inventory-probe-local-v1`, cap **$0.15** (expected cloud spend $0-0.04;
  results cached by part `meta` are reused). Same guards. Run after `meta`.
- Prerequisite: Unsloth serving Gemma 4 E2B with tool calling and image
  input. Pillow 11.3.0 was added to the Windows tools venv for views.
- **Command** (PowerShell, from the workspace root):
  `$env:PYTHONPATH="main_LX\src"; main_LX\.lx\tts-venv\Scripts\python.exe -m lx.inventory_probe --part local --approve-dataset 1c673f8d2445afe2c675900a344b94942cacb3a89552d88961d3b96667767fcc`

## Outputs

`main_LX/.lx/<scope>/probe.json` and `usage.json`: transcripts and word error
rate; SAM instances and confident boxes per photo against COCO truth, and a
box/image size check; the view check (view called, reply text); each agent
run's tool calls (with script exit codes), answer, checklist score and failed
check names; raw reply shapes (keys plus a 3,000-character excerpt, no
credentials). Evidence is committed after the run.

## What the results decide

- View image rejected or ignored → change how images reach the agent (for
  example inline the image in the tool message, or attach all photos up
  front within the body limit), then a new reviewed retry.
- SAM shapes or sizes wrong on these JPEGs → fix offline first.
- Spark ignores the skill or script → expected sometimes; it tells the
  improver where guidance is needed, it does not block T7.
- Local model cannot use the tools or images → E-C needs another local model
  or a simpler tool path; E-A is unaffected.
- All usable → prepare T7 calibration (base harness on calibration and part
  of held-out; gate 0.3-0.8). Note: the 5 calibration samples all have 3
  photos and answers of 2-5, so T7 should add harder samples.
