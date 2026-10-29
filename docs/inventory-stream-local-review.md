# G2 live E-A stream request — PROD-004 inventory, Gemma as the agent (prepared 30 Oct 2026)

Status: **PREPARED, NOT RUN.** Needs the user's explicit approval, including
the agent base URL, with the provider serving Gemma 4 E4B.
Controller: `lx.inventory_stream_local`, offline-tested in
`tests/test_inventory_stream_local.py`. Authority: D-008 (a local model is a
provider like any other), D-010, D-012 and D-013 (users provide 6 samples).
Plan: subplan "E-A on Gemma" (G0–G4).

## Why

E-C showed that the harness Spark learned in T8 does not help Gemma 4 E4B
(held-out −0.09 [−0.17, −0.02]): it fixes a Spark-only failure. This run lets
the loop learn from **Gemma's own trajectories** on the 6 samples a user
would hand over. G3/G4 then measure the result on the hidden held-out
samples.

## What runs (T8's loop, unchanged except the agent)

- Harness² k=1, sequential mode, experience library carried, on the 6
  stream samples of frozen `inventory-mix-v1` (hash
  `db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322`) in
  T8's interleaved order.
- Per sample: base run, propose (invariant and domain edits), probe, compose,
  reflect. The improver is grade-blind. After the loop, the controller grades
  base and chosen runs with the checklist.
- Editable components as in T8: always-on files, up to 8 skills and 8
  scripts (scripts in the WSL sandbox, no network). No sub-agents or plugins.

| Role | Model |
|---|---|
| **Agent** | `gemma-4-E4B-it-GGUF` (the E-C entry: $0, 1,536-token reply cap, 600 s per call), at the **approved base URL** |
| Improver, reflector | Spark (as T8) |
| Tools | SAM 3.1, Muse Voice Transcribe (cloud, fixed; cache shared) |

**Gemma is an ordinary provider entry.** Its base URL is an input of this
request, passed on the command line and checked by the same endpoint rule as
any provider: HTTPS anywhere, plain HTTP only to local or private-network
hosts, no credentials in the URL. There is no special transport.

- The agent client accepts exactly the agent tools, and the Spark client
  accepts only the improver's file tools.
- The agent's model, tools, cap and bound are the E-C retry's, so G3 can
  reuse those base rows.

## The base URL (decided at approval)

The controller must run in WSL: the core loop uses `fcntl`, and the script
sandbox needs Linux. WSL (NAT networking) cannot reach a server that listens
only on Windows `127.0.0.1`. That is recorded in the logs (PROD-003) and was
confirmed on 30 Oct when the loop failed on Windows.

The URL is therefore whatever address the provider serves that WSL can
reach. Either:
- Unsloth Studio serves on the network, and the URL is the Windows host
  address as seen from WSL, for example `http://172.x.x.1:8888/v1`. Windows
  Firewall may need to allow it; or
- WSL uses mirrored networking, and the URL stays
  `http://127.0.0.1:8888/v1`.

Both are the user's settings. The controller changes neither.

## Limits and budget

- Agent caps per run: `caps(n)`, 13–19 turns. Improver and reflector
  sessions are capped at 12 turns each.
- Call limits:

  | | Limit |
  |---|---|
  | Gemma chat calls | 600 (T8 worker used 255) |
  | Spark chat calls | 500 (T8 improver + reflection used 226) |
  | SAM calls | 300 |
  | Transcribe calls | 40 |
- **Budget:**
  - Scope `inventory-ea-local-v1`, cap **$1.00**.
  - Gemma costs $0. In T8, Spark's improver and reflector cost about $0.11
    and new SAM pairs about $0.13.
  - Expected spend: **about $0.15–0.40**.
- **Wall time:** about **1.5–2 hours**. Gemma's calls took about 15–17 s each
  in the E-C retry, and Spark's about 5–6 s.
- **Guards:**
  - exact dataset hash;
  - a valid agent URL;
  - the WSL sandbox must be available;
  - the provider must report `gemma-4-E4B-it-GGUF` as **loaded** (a GET of
    `<base>/models`, no generation);
  - shared ledger at or after the checkpoint ($2.054275772 spent, 3576
    settled);
  - at least $1.00 left in the ledger;
  - no earlier rows in the scope;
  - an exclusive `started.json` marker that records the agent endpoint;
  - retries off; the Spark/SAM/Transcribe key is loaded at runtime only.

## Command (WSL, after approval, with the approved URL)

```
wsl -e bash -lc 'cd /mnt/c/Shrey_Projs/hackathons/main_LX && PYTHONPATH=src .venv/bin/python -m lx.inventory_stream_local --approve-dataset db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322 --agent-base-url <APPROVED_URL>'
```

## Outputs

The run writes to `main_LX/.lx/inventory-ea-local-v1/`:
- `summary.json` and `usage.json`;
- `run/ea_report.json`, with per-sample base and chosen scores and gains;
- `run/library/`;
- `run/runs/`;
- the improver and reflector audits.

Evidence is committed after the run, and the last adopted harness is
frozen for G3.

## How to read the result

- **Edits that name Gemma's E-C failures** — the `answer` field written as an
  object, cut-off inventory replies, tool-call text the server does not
  parse, non-JSON final answers — **and gains on the stream:** freeze the
  harness, then G3/G4 on the 24 held-out samples.
- **Base kept, or edits that do not touch those failures:** the improver
  cannot see Gemma's problem from the trajectories. Report that, and
  consider letting the improver see failed check names (D-009).
- **Limits:**
  - 6 samples, one run per arm: descriptive, not statistical;
  - a different base URL from E-C, but the same model file and settings;
  - G3 reuses E-C's base rows from another day.
