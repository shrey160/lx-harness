# G1b probe + G2 live E-A stream — PROD-004 inventory, swapped agent (prepared 30 Oct 2026)

Status: **PREPARED, NOT RUN.** Two separate live requests: the G1b probe,
then the G2 stream. Each needs the user's explicit approval, including the
agent settings below.
Controllers: `lx.inventory_agent_probe` (G1b) and `lx.inventory_stream_local`
(G2), offline-tested in `tests/test_inventory_stream_local.py`. Authority:
D-008 (a local model is a provider like any other), D-010, D-012, D-013
(users provide 6 samples) and D-014 (agent: LFM3-VL-2B). Plan: subplan "E-A
on a swapped agent" (G0–G4).

## Why

E-C showed that the harness Spark learned in T8 does not help a small local
agent: on Gemma 4 E4B, held-out −0.09 [−0.17, −0.02]. It fixes a Spark-only
failure. Here the loop learns from **the swapped agent's own trajectories**,
on the 6 samples a user would hand over. G3/G4 then measure the result on
the hidden held-out samples.

On 30 Oct the user changed the agent to `LiquidAI/LFM3-VL-2B-GGUF` (D-014).
Unsloth reports it loaded: BF16, 111,616-token context.

## The agent is an ordinary provider entry, like the cloud API

Spark is reached as base URL `https://api.meta.ai/v1` + model id + a key read
at runtime from `MODEL_API_KEY`/`META_API_KEY`. The swapped agent is reached
the same way, with every part a **stated input approved with the request**:

| Input | Flag | For this request |
|---|---|---|
| Base URL | `--agent-base-url` | the address WSL can reach (below) |
| Model id | `--agent-model` | `LiquidAI/LFM3-VL-2B-GGUF` |
| Key | `--agent-key-env` (optional) | the **name** of a variable (here `UNSLOTH_API_KEY`), read at runtime from the environment or the core `.env`, the same way as the Meta key, and never shown. Omit it if the provider needs no key. |

- The base URL is checked by the same endpoint rule as any provider: HTTPS
  anywhere, plain HTTP only to local or private-network hosts, no
  credentials in the URL.
- The key variable cannot be `MODEL_API_KEY` or `META_API_KEY`, so the Meta
  key can never be sent to another provider.
- Prices are $0. The reply cap is 1,536 tokens and each call may take up to
  600 s, as in the E-C retry.
- The agent client accepts exactly the agent tools; Spark accepts only the
  improver's file tools. There is no special transport.

**Reachability.** The controller must run in WSL: the core loop uses
`fcntl`, and the script sandbox needs Linux. WSL (NAT networking) cannot
reach a server that listens only on Windows `127.0.0.1`. This is recorded
in the logs, and was confirmed on 30 Oct when the loop failed on Windows. The
base URL is therefore whatever address the provider serves that WSL can
reach. Either:
- Unsloth serves on the network, and the URL is the Windows host address
  from WSL, for example `http://172.x.x.1:8888/v1`. Windows Firewall may need
  to allow it; or
- WSL uses mirrored networking, and the URL stays
  `http://127.0.0.1:8888/v1`; or
- an HTTPS address.

All of these are the user's settings. The controllers change none of them.

## G1b — agent probe (first request)

LFM3-VL-2B is untested here. Gemma E4B had already shown it handles images
and the tools in S5 and T6. Before a 1.5–2 hour run, check the mechanics:
- **View check:** on `fsc-calibration-02`, call `view` once, then describe
  photo 1.
- **Base-harness runs:** one each on `fsc-calibration-02` (count, 3 photos)
  and `mix-calibration-colourcount-01` (colour_count, 3 photos). T7 ran both
  on Spark, so perception is mostly cached.
- **Reported:** whether `view` was called and the image seen; the tools used;
  whether a JSON answer came back; checklist scores; stop reasons, including
  cut-offs and tool-call text returned as content.
- **Limits:**

  | | Limit |
  |---|---|
  | Agent chat calls | 30 |
  | SAM calls | 20 |
  | Transcribe calls | 4 |
- **Budget:** scope `inventory-agent-probe-v1`, cap **$0.10**. Expected
  **about $0–0.05**: the agent is $0, with a few new SAM pairs at most.
- **Wall time:** a few minutes.
- **Reading:**
  - if LFM3 calls the tools and returns JSON on at least one run, proceed to
    G2;
  - if it cannot call tools or see images, stop and choose another agent.

  Not capability evidence.

**Controller amended 30 Oct at the user's direction ("go ahead").** G1b runs no
Harness² loop and no scripts, so it needs neither `fcntl` nor the sandbox. It runs
from Windows, as S5, T6 local and E-C did, where Unsloth is reachable at
`127.0.0.1`. The key comes from the core `.env` via `UNSLOTH_API_KEY`. The loaded
file is Q8_0. Only G2 needs WSL to reach Unsloth.

Command (Windows PowerShell, from `main_LX`, approved):
```
$env:PYTHONPATH="src;..\harness2_portable_core\vendor\harness2-cli"; & .\.lx\tts-venv\Scripts\python.exe -m lx.inventory_agent_probe --approve-dataset db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322 --agent-base-url http://127.0.0.1:8888/v1 --agent-model LiquidAI/LFM3-VL-2B-GGUF --agent-key-env UNSLOTH_API_KEY
```

## G1b v2 — two thinking modes (30 Oct, user "Probe both")

**G1b v1 failed** (`8832d9a`): no tool call. A follow-up 3-request
diagnostic (logs, 30 Oct) found the cause. Unsloth runs LFM3 with thinking
on, and its reasoning (about 3,700 tokens even for the view check) used the
whole 1,536-token cap. The server's tool-call parsing works:
- with thinking on and a 4,096-token cap, LFM3 made a correct `view` call;
- with thinking off, it made a correct `view` call in 0.3 s, but on the
  bare base prompt it skipped the tools.

**Two new settings on the agent entry** (the llama.cpp server's own switch,
the counterpart of Spark's `reasoning_effort`):
- `--agent-thinking on|off`, sent as `chat_template_kwargs.enable_thinking`;
  omitted means the server default;
- `--agent-max-tokens` (1–8192, default 1,536 as in E-C).

Both are recorded in `started.json`.

**The probe is the same as v1:** a view check plus base runs on the same two
samples, $0.10 cap and the same limits. Each mode gets its own scope,
`inventory-agent-probe-v2-thinking-on` and `inventory-agent-probe-v2-thinking-off`.
"answered" now counts only answers in the contract's shape; v1 counted the
echoed input.

| Mode | Cap | Expected time |
|---|---|---|
| thinking on | 8,192 | up to about 20 minutes (about 36 s per turn, at most 29 calls) |
| thinking off | 1,536 | about 1 minute |

Commands (Windows PowerShell, from `main_LX`; one per mode):
```
$env:PYTHONPATH="src;..\harness2_portable_core\vendor\harness2-cli"; & .\.lx\tts-venv\Scripts\python.exe -m lx.inventory_agent_probe --approve-dataset db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322 --agent-base-url http://127.0.0.1:8888/v1 --agent-model LiquidAI/LFM3-VL-2B-GGUF --agent-key-env UNSLOTH_API_KEY --agent-thinking off
```
```
$env:PYTHONPATH="src;..\harness2_portable_core\vendor\harness2-cli"; & .\.lx\tts-venv\Scripts\python.exe -m lx.inventory_agent_probe --approve-dataset db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322 --agent-base-url http://127.0.0.1:8888/v1 --agent-model LiquidAI/LFM3-VL-2B-GGUF --agent-key-env UNSLOTH_API_KEY --agent-thinking on --agent-max-tokens 8192
```

**Interrupted 30 Oct:** the machine shut down during the thinking-on probe.
Scope `…-thinking-on` keeps 3 settled view-check calls and 1 reserved call at $0;
no report was written. At the user's direction ("check and resume"), it is rerun
unchanged with `--attempt 2`, which changes only the scope name, to
`…-thinking-on-attempt2`. The earlier rows stay as they are.

**Reading:** pick the mode for G2 from tools used, contract-shaped answers,
scores, cut-offs and time per turn.
- **Thinking off and the tools skipped:** that is base-harness headroom the
  loop could fix, if the model follows a skill.
- **Thinking on:** slower, but may follow the task unaided.

## G2 — E-A stream (second request; T8's loop, only the agent changed)

- Harness² k=1, sequential mode, experience library carried, on the 6
  stream samples of frozen `inventory-mix-v1` (hash
  `db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322`) in
  T8's interleaved order.
- Per sample: base run, propose (invariant and domain edits), probe, compose,
  reflect. The improver is grade-blind. Afterwards the controller grades base
  and chosen runs with the checklist.
- Editable components as in T8: always-on files, up to 8 skills and 8
  scripts (scripts in the WSL sandbox, no network). No sub-agents or plugins.
- **Roles:**

  | Role | Model |
  |---|---|
  | Agent | the stated provider: `LiquidAI/LFM3-VL-2B-GGUF` |
  | Improver, reflector | Spark (as T8) |
  | Tools | SAM 3.1, Transcribe (cloud, fixed; cache shared) |
- **Limits:**
  - Agent caps per run: `caps(n)`, 13–19 turns. Improver and reflector
    sessions are capped at 12 turns each.
  - Call limits:

    | | Limit |
    |---|---|
    | Agent chat calls | 600 (T8 worker used 255) |
    | Spark chat calls | 500 (T8 used 226) |
    | SAM calls | 300 |
    | Transcribe calls | 40 |
- **Budget:** scope `inventory-ea-local-v1`, cap **$1.00**. Expected **about
  $0.15–0.40**: Spark about $0.11 and SAM about $0.13 in T8.
- **Mode (user, 30 Oct: "Thinking on"):** `--agent-thinking on
  --agent-max-tokens 8192`, as in G1b v2. The key comes from
  `UNSLOTH_API_KEY` in the core `.env`.
- **Wall time:** about **4–5 hours**. G1b v2 measured about 60 s per
  thinking call, and T8 used about 255 agent calls plus Spark's improver.
  The agent call limit (600) allows a worst case of about 10 hours. The
  machine must stay awake with Unsloth running.
- **Guards (G1b and G2):**
  - exact dataset hash;
  - valid agent settings;
  - for G2, the WSL sandbox must be available;
  - the provider must report the model as **loaded** (a GET of
    `<base>/models`, sending the key if one is named; no generation);
  - shared ledger at or after the checkpoint ($2.054275772 spent, 3576
    settled; G1b's spend counts against G2's room);
  - at least the cap left in the ledger;
  - no earlier rows in the scope;
  - an exclusive `started.json` marker recording the agent endpoint, model
    and key-variable name;
  - retries off; keys loaded at runtime only.

Command (WSL, after approval):
```
wsl -e bash -lc 'cd /mnt/c/Shrey_Projs/hackathons/main_LX && PYTHONPATH=src .venv/bin/python -m lx.inventory_stream_local --approve-dataset db6c70f672638848475e9d782e5a40f2df9f8680c68b72b62f76ddecdd01c322 --agent-base-url <APPROVED_URL> --agent-model LiquidAI/LFM3-VL-2B-GGUF --agent-key-env UNSLOTH_API_KEY --agent-thinking on --agent-max-tokens 8192'
```

**Outputs** go to `main_LX/.lx/inventory-ea-local-v1/`:
- `summary.json` and `usage.json`;
- `run/ea_report.json`;
- `run/library/`;
- `run/runs/`;
- the audits.

Evidence is committed after the run, and the last adopted harness is
frozen for G3.

## After G2 (not part of this request)

The E-C base rows were Gemma E4B, so they **cannot be reused**. G3/G4 must
run both arms on the 24 held-out samples with LFM3: base vs the frozen
G2 harness, the same paired criterion as E-B/E-C, about 2–3 hours. This is
a separate reviewed request.

## How to read G2

- **Edits that name the agent's own failures**, such as malformed answers,
  cut-off replies or tool-call text, **and gains on the stream:** freeze,
  then G3/G4.
- **Base kept, or edits that miss those failures:** the improver cannot see
  the problem from the trajectories. Consider D-009, letting the improver
  see failed check names.
- **Limits:** 6 samples, one run per arm, so the result is descriptive only.
  This is a new agent with no E-C baseline.
