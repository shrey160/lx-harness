# No LX worker token cap and arithmetic judge, 3 November 2026

User requested a rerun with a judge, then clarified "no limits on tokens".
Added **worker_reply_tokens = 0** for no LX output-token cap on a free local
worker. ChatClient omits the token-limit field and accepts longer usage without
replacing it with a larger numeric cap. Input/context, response-size, time,
cancellation and budget controls remain. Paid/public calls require finite
output bounds. Other roles keep reply_tokens. Old specs inherit their existing
reply length exactly. New setting is available under Advanced: run limits.

Local Unsloth read-only /props confirms n_predict = -1 and max_tokens = -1,
ignore_eos = false, n_ctx = 80384. Thus omitted limits use this server's
unrestricted prediction default until its natural stop/server/model limits.
Primary references checked: [llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md),
[Unsloth API](https://unsloth.ai/docs/basics/api). No live inference was used to
test this setting before run approval.

Validation:

- **96 WSL regressions pass**, 128.487 s: protocol, limits, generic tasks,
  judges and new no-cap tests. Final expanded local-worker/judge isolation test
  also passes in WSL (5 new tests, 0.268 s).
- **44 Windows tests pass**, 4.256 s, one existing POSIX-symlink skip:
  no-cap, chat protocol, limits and UI pages. Frontend build passes.
- Tests prove no token parameter is sent, reported 50000-token output is
  accepted/accounted at zero cost, paid/public profiles cannot uncap replies,
  input context bounds remain, old finite limits still apply, old specs inherit
  correctly, and improver/judge stay bounded while the local worker is uncapped.
- JSON/compile/whitespace checks pass. No full application suite rerun.

Prepared workspace `.lx/inventory-update-json`, **spec v4**:

- Local LFM3-VL-2B worker, **worker_reply_tokens 0**, call timeout **1800 s**,
  whole run **4 hours**. General reply cap remains **8192** for paid roles.
- Same two samples, independent eight-rule check, concise-small-worker hint
  and worker identity note. No task procedure or dataset changes.
- Spark Contributor judge, **$0.25 separate cap**, four before/after answers;
  each call currently reserves $0.106496, so the cap can admit calls. Existing
  loop cap **$1** retained; maximum combined allowance **$1.25**, not a $0.25
  allocation within $1. Workspace lifetime cap is unchanged.
- Five judged criteria: stock arithmetic consistency; cash arithmetic
  consistency; correct transaction quantity; preserved stock/prices;
  complete usable answer. Exact questions are in local eval/judges.json and
  bound to the spec. Arithmetic consistency is distinct from using the right
  counted quantity. Judge sees answer and controller truth after learning,
  never media, trajectories or the harness. Before/after verdicts explain
  answer changes, not causal proof of harness benefit. Intermediate probes still
  require trajectory review. Model judgments may be wrong.
- Ready true, check self-test 22 cases, zero blank/wrong score, no check issues.
  **Agent approval returns pending**, because LX requires a person to review a
  judged spec. This guard is unchanged; no lower-level/person-labelled approval
  was used. Review/approval in the app is the remaining step before run start.

No paid calls or replacement run started while preparing this configuration.
Earlier e46e9101cc9c remains complete at 0.9375 -> 0.50, unchanged base package,
$0.055223268, no holds. Its outputs and specification are untouched.

## Follow-up: explicitly approved run started

After the exact v4/settings/criteria and $1 + $0.25 caps were shown, the person
replied **"approved"** in chat. The app still had no stored approval. That
specific human confirmation was recorded through the human approval API, by
person; it was **not a GUI click or autonomous agent approval**. The original
weakness gate for unattended agents remains. Chat confirmation provenance is
kept in local uncapped-judged-run.json and project context.

Full run **5a2e37184043** was created once and its native worker launched hidden,
avoiding temporary MCP session teardown. Confirmed running/preflight with 0/2
complete and $0 recorded at the first check. Results and judge verdicts pending;
no further rerun authorized. App was reloaded on port 8765. No runtime changes
were made after the tested no-cap implementation.
