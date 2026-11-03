# Connect an agent to LX

LX is an MCP server for a local agent acting as orchestrator. The agent defines
and inspects experiments; LX executes workers, the improver and tools using the
configuration a person saved in the Web UI.

This connection is distinct from giving LX workers tools from other servers.
That other direction is documented in [MCP tools](mcp.md).

## Connect and verify

1. Complete the [installation](installation.md) with `--extra mcp`. Use the same
   environment for the UI and the MCP process.
2. In Settings → Connectivity, ensure **Agent access** is Started.
3. Copy the generated client setup from **Connect an agent**. The app supplies
   setup for Codex/local ChatGPT desktop, Claude Code and Muse Code, using this
   install's actual Python executable. Merge with existing settings, then start
   a fresh LX connection/session.
4. Ask the agent to call `status`, with no approval or run. A successful response
   confirms a working protocol connection. Call `guide` and `environment` next.

The server command is the installed environment's absolute Python executable,
with arguments `-m`, `lx.mcp_server`. It speaks **stdio**, with protocol on stdout
and logs on stderr. The client launches it; do not configure the Web UI URL as
an MCP endpoint or add it as an HTTP server.

For a client accepting the common `mcpServers` JSON shape, this is an illustrative
Windows entry; replace the example path with your installation's interpreter:

```json
{
  "mcpServers": {
    "lx": {
      "command": "C:/projects/my-project/main_LX/.venv-win/Scripts/python.exe",
      "args": ["-m", "lx.mcp_server"]
    }
  }
}
```

On Linux, use the absolute `main_LX/.venv/bin/python` path instead. Client schemas
differ; use the generated TOML or client-specific JSON where offered rather than
assuming this generic wrapper fits every client. Preserve the generated Windows
environment block where supplied: clients with trimmed environments need profile,
system and temporary-directory variables. None of these examples needs API keys.

No working-directory argument is required for the normal editable installation;
the interpreter imports the installed packages. Task paths are separate absolute
`workspace` arguments. If the UI uses a custom state root, its generated setup
includes `--root`; preserve it. A scratch root disables live execution. The normal
UI process does not need to remain running after configuration.

Local stdio setup does not make LX available to a browser-only or remote agent
that cannot launch programs on this machine. No public HTTP gateway is shipped
by these instructions.

## Tool reference

Tool names below are unprefixed; a client may display them as `lx.status` or with
another server prefix. All workspace arguments are absolute task-folder paths,
not the LX install or core directory. `run_id` is returned by start actions.

| Tool | Required arguments | Behavior / useful optional arguments |
| --- | --- | --- |
| `status` | none | Live availability, Meta-key presence, models/entry IDs, deletion permission; never key values |
| `guide` | none | List guide topics; `topic`: `tasks`, `environment`, `mcp` |
| `environment` | none | Sandbox, model tools, agent tools, connected MCP-tool choices, limits and templates |
| `init` | `workspace`, `template` | Create/open task from `samples/`; template `photo-request` or `text-request`; optional `goal`, `name` |
| `samples` | `workspace` | List input files, kinds, sizes and hashes |
| `read_sample` | `workspace`, `path` | Read a listed text input; refuses `truth.json`, binary media and unlisted paths |
| `truth_shape` | `workspace` | Answer field names/types without values |
| `spec_show` | `workspace` | Current spec/check, approvals, budget, grant, data notice and blockers |
| `spec_draft` | `workspace` | Save changed fields as a new version, clearing approval; details below |
| `eval_init` | `workspace` | Create starter `eval/rules.json`, never overwrite an existing check |
| `check_test` | `workspace` | Correct/blank/wrong-answer self-test; optional `check` or `use_eval_file` tests a draft without saving |
| `check_try` | `workspace` | Re-score saved answers with current/draft check; no new model calls; optional `check` or `use_eval_file` |
| `judge_try` | `workspace` | Compare already-kept judge verdicts; optional `judges` or `use_judge_file`; new verdicts require a person, no calls here |
| `ready` | `workspace` | Validate live readiness/sample issues without execution or spending; not a quality verdict |
| `preview` | `workspace` | Start a controlled preview on LX synthetic samples only; returns `run_id` |
| `approve` | `workspace` | Accept eligible live spec/data notice as the agent; only for the requested run; caps unchanged |
| `run_start` | `workspace` | Start approved/granted full run or report pending/blocked |
| `trial_start` | `workspace` | One-sample live trial; optional `from_run`, `from_sample` select a finished package/adopted harness |
| `run_status` | `workspace` | Runs, progress, phase, errors, costs and simulated-cost flag |
| `run_stop` | `workspace`, `run_id` | Stop work; incurred spend remains recorded |
| `edits` | `workspace`, `run_id` | Harness diffs, improver notes and tokens by sample/stage, during or after work |
| `results` | `workspace`, `run_id` | Completed-run results, sample order and bounded `learned.md` text; fails if unfinished |
| `workspace_delete` | `workspace` | Permanent removal of LX-managed workspace state; requires person-enabled deletion permission; refuses active jobs |

### Drafting a spec

Supply only changed fields. Use `spec_show` for the current task field names and
`environment` for valid IDs and limit definitions.

- `goal`, `task`, `mode` (`controlled_preview` or `live`), `worker`, `improver`,
  `tools`, `mcp_tools` and `limits` describe the task and execution choices.
- `check` supplies the whole scoring check; `use_eval_file: true` instead takes
  `eval/rules.json` or `eval/check.py`. Do not supply both. `no_check: true`
  removes scoring and requires person approval for live execution.
- `judges` supplies criteria and optional `sees_request`; `use_judge_file: true`
  reads `eval/judges.json`, while `no_judges: true` removes model judges. These
  are post-loop judgments, with their own configuration/cap and person approval.
- `hints`, `sample_hints`, `use_hint_files` and `tell_improver_worker` are optional
  experimental improver context. They are separate from the worker task; never
  put answers in them. The task states requirements rather than prescribing a
  procedure the loop is meant to learn.

See [task authoring](tasks.md) for full field/check/judge contracts. Task limits
may change within allowed bounds; workspace/run dollar caps remain person-controlled.

## Operation and permissions

Start actions return promptly. Poll `run_status` for the returned ID; only call
`results` once completed. Treat `pending`, `blocked` and protocol/tool errors as
actionable outcomes, read their reasons, and avoid repeated paid starts as retries.
Reconnect after source updates to load fresh tool definitions.

The agent can approve eligible live checked specs, so connection is a meaningful
trust decision. It cannot add providers/keys, raise budgets, create grants, connect
worker-side servers, mark tools read-only, settle held billing or enable deletion.
Judged/unscored work and weak check self-tests wait for a person. Deletion is a
separate opt-in, off by default; original files and real-spend records survive it.

MCP input readers withhold answer files, but a coding agent with ordinary filesystem
access can still read them outside MCP. This is not protection against a hostile
local process. Keep expected answers out of the worker/improver context and review
what your orchestrator shares with its own provider.

## Troubleshooting

- **No tools:** verify `--extra mcp`, interpreter path, client configuration and
  Agent access; reconnect and try `status`.
- **Unexpected settings:** check installation/user/OS/state-root agreement with
  the UI. A second clone or WSL environment has separate default state.
- **Windows startup failure:** preserve generated environment fields and quote
  paths correctly. A command string is not an argument array.
- **Optional server silently skipped in Muse Code:** temporarily use its required
  mode to see the startup error, then restore the intended mode.
- **Live work pending:** use `spec_show`, `check_test` and `ready`; request person
  action where indicated. Do not change a budget or approve an unrequested run.
- **Old tools after updating:** stop/restart the client's MCP process. Reloading
  the Web UI alone does not replace a long-lived imported Python module.

Follow the [worked workflow](agent-workflow.md) after verifying the connection.
