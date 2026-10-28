# S5 live probe request — PROD-003 voicecount (prepared 29 Oct 2026)

Status: **PREPARED, NOT RUN.** Each part needs the user's explicit approval.
Controller: `lx.voicecount_probe` (offline-tested in
`tests/test_voicecount_probe.py`). Authority: D-008 allows live SAM 3.1 and
Muse Voice Transcribe as fixed tools and a registered local provider as the
agent, each live step under its own approval and the shared $20 ledger.

## Why

The plan's live steps depend on facts the docs do not settle:

1. Where the non-streamed SAM reply puts its text, and whether it carries
   confidences (`c`) and boxes sized to the source image.
2. Whether the Transcribe `request` part `{"model": "muse-voice-transcribe-1.0",
   "audioEncoding": "WAV", "mode": "PUSH_TO_TALK"}` is accepted, and how well
   it hears the Kokoro speech.
3. Whether `muse-spark-1.3-contributor` accepts a JPEG plus the agent tool set,
   calls tools, and returns the answer JSON.
4. Whether the local model (Gemma 4 E4B on Unsloth) does tool calling with an
   image through the same loop.

This is not a capability measurement and decides nothing about headroom
(that is S6).

## Fixed inputs

Frozen dataset `voicecount-v1`, `dataset_sha256`
`b306c42f11c867c6746ba9202dcd4208884610d836edcff5a1322460e4fa2146`
(checked at start). Only loop items, so the test and calibration splits stay
unseen:

| Item | Request (spoken) | Truth |
|---|---|---|
| `loop-0005` | "How many geese are there in this picture?" (af_sarah) | geese, whole, 30 (±1) |
| `loop-0036` | "Count the cans in the bottom half of the picture." (af_bella) | cans, bottom, 36 (±1) |

Data sent: FSC-147 photos (public dataset) and synthetic speech. No personal
or client data. Contributor training exposure was accepted in D-006(e).

## Part `meta` (WSL controller, shared pilot ledger)

| Call | Count | Limit |
|---|---|---|
| Transcribe | 2 clips (2.4-3.4 s each) | 4 calls |
| SAM | `geese` on loop-0005, `cans` on loop-0036, absent `giraffes` on loop-0005 | 15 calls in total |
| Spark agent (base harness, no guidance) on both items | at most 8 turns and 6 tool calls per item | 16 chat calls |

The agent's own SAM/Transcribe calls reuse cached results when the arguments
match, so they cost nothing; new noun phrases cost one SAM image each.

- **Budget:** scope `voicecount-probe-v1`, cap **$0.50**. The cap must cover
  the $0.1056768 full-context reservation each Spark call holds while in
  flight. Expected actual spend about $0.02-0.08 (SAM at most 15 x $0.0025 =
  $0.0375; Transcribe under $0.001; Spark a few thousand tokens per call).
- **Guards:** exact dataset approval hash; existing shared ledger at or after
  the R3 checkpoint ($0.049064596, 234 settled); no earlier rows in the scope;
  exclusive `started.json` marker (no repeats); fixed endpoints and models;
  retries off; error bodies withheld; key loaded at runtime only.
- **Command** (from `main_LX`, after approval):
  `wsl -e bash -lc 'cd /mnt/c/Shrey_Projs/hackathons/main_LX && PYTHONPATH=src .venv/bin/python -m lx.voicecount_probe --part meta --approve-dataset b306c42f11c867c6746ba9202dcd4208884610d836edcff5a1322460e4fa2146'`

## Part `local` (Windows controller; needs the local server running)

- Agent: `gemma-4-E4B-it-GGUF` at `http://127.0.0.1:8888/v1`, registered the
  same way as any other provider (no key, $0 tokens), with the same tools and
  base harness, on `loop-0005` only. SAM/Transcribe stay the cloud services;
  results cached by part `meta` are reused.
- Limits: 8 chat calls, 6 SAM, 2 Transcribe; scope `voicecount-probe-local-v1`,
  cap **$0.10** (expected cloud spend $0-0.015). Same guards.
- Prerequisite: Unsloth serving Gemma 4 E4B with tool calling enabled. Run
  after part `meta` (it shares the ledger file).
- **Command** (PowerShell, from the workspace root):
  `$env:PYTHONPATH="main_LX\src"; main_LX\.lx\tts-venv\Scripts\python.exe -m lx.voicecount_probe --part local --approve-dataset b306c42f11c867c6746ba9202dcd4208884610d836edcff5a1322460e4fa2146`

## Outputs

`main_LX/.lx/<scope>/probe.json` and `usage.json`: transcripts with word
error rate, SAM instance counts vs truth, confidence range, box/image size
check, the agent's tool calls, answer and score, and each raw reply's shape
(keys plus a 3,000-character excerpt; no credentials). Evidence is committed
after the run.

## What the results decide

- SAM shape or Transcribe fields wrong → fix the parser or request offline,
  then a new reviewed retry (the scope guard blocks reruns).
- Spark does not call tools or rejects the image/tools → fix before S6.
- Local model cannot use tools → S8 needs another local model or an adapter;
  Stage A is unaffected.
- All good → prepare the S6 calibration request (A0 Spark alone, A1 base
  harness, 20 calibration items) with call ceilings from S4/S5.
