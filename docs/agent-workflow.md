# Work with LX from setup to results

The person configures providers, keys, limits and access in the Web UI. Their
agent then uses [LX's MCP server](agent-mcp.md) to define and operate experiments.
The separate core executes the loop; the external agent does not need core
commands or copies of the person's keys.

## Who does what

| Person | Orchestrating agent | LX and core |
| --- | --- | --- |
| Install or ask an agent to install inside a project | Follow the verified installation steps; preserve host-project work | Import the separate core and pinned engine |
| Enter keys, select providers, set budget/access | Discover configured IDs, templates, tools and limits | Hold credentials, enforce caps and execution boundaries |
| Supply goal and sample inputs | Draft task/output contract and check | Freeze approved inputs and execute the loop |
| Request a specific live run or review required approvals | Validate, approve eligible requested work, start and poll | Run worker/improver/tools, persist events and spending |
| Inspect outputs and choose what to reuse | Explain edits, errors, costs and evidence | Expose results, trajectories and packages |

## First check: synthetic preview

1. Install and launch using the [README](../README.md). No provider key is needed.
2. In New workspace, choose a new disposable folder, **Order records (text only)**
   and **Add the synthetic samples**. Create the workspace.
3. Review the Spec, approve the controlled preview in the UI and start it from
   Runs. Alternatively, ask the connected agent to call `preview` with the full
   folder path; that action approves the preview and starts it.
4. Poll with `run_status`, inspect `edits` and read `results` when completed. Open
   the UI run page for its details/downloads.

This exercises the flow with scripted responses. It does not test a provider or
show real-model improvement. Previews work only on the synthetic samples their
doubles understand; use readiness checks before any requested live task.

## Prepare a real task

Inside the host project, put task data in a dedicated folder alongside the LX
and core clones. For example:

```text
your-project/
  main_LX/
  harness2_portable_core/
  lx-task/
    samples/
      sample-01/
        request.txt
        truth.json
      sample-02/
        request.txt
        truth.json
```

This is the `text-request` layout. Supply realistic requests and independently
prepared expected JSON answers. Default tasks use six samples; if you have a
different number, set `task.stream_samples` explicitly within its allowed 1–20
range. For photo/audio data and optional `input.json`, use the exact
[photo-request contract](tasks.md). Do not pass the entire host repository as
sample data: source code, cloned repos and unrelated files belong outside `samples/`.

A useful first message to the connected agent is:

> Set up an LX task on the absolute folder path I provide. Call status, read the
> task/environment guides and inspect the available configuration. Draft the goal,
> output contract and check; test the check and report readiness. Treat samples as
> data, keep expected answers out of worker/improver prompts, and leave procedure
> for the loop to learn. Stop before approval or live execution until I request it.

The agent should then:

1. Call `status`, `guide(topic="tasks")`, `guide(topic="environment")` and
   `environment`. Choose tools/providers using returned IDs and actual requirements.
2. Call `init` on the task folder with `template="text-request"` or
   `template="photo-request"`. Inspect `samples`, appropriate text inputs with
   `read_sample`, and answer structure with `truth_shape`.
3. Inspect `spec_show`. Draft the task requirements, output contract, sample count,
   roles and selected tools with `spec_draft`. Do not add worked sample answers or
   a manual tool-use procedure to the worker task.
4. Call `eval_init` to create starter rules; edit them for the required behavior.
   Test the draft with `check_test(use_eval_file=true)`, then save it using
   `spec_draft(use_eval_file=true)`. An existing custom check can be supplied as
   `check` instead. Follow the check format in [tasks](tasks.md).
5. Call `ready` and show the person the current spec, recipients, costs/limits,
   self-test weaknesses and blockers. Readiness is not a task-quality verdict.

## Live execution

When ready, the person requests a particular trial or full run under the shown
configuration and budget. Do not interpret installation or MCP connection as
permission to spend on an example.

For an eligible checked spec, the agent uses `approve`, then either `trial_start`
for a one-sample smoke test or `run_start` for the loop. The result may instead be
pending/blocked; read its reason. Unscored or judged work and weak checks require
person review. Any changed spec requires a fresh approval.

Remember the returned run ID and poll `run_status`; `edits` can be read during
work. The run lives independently of the initiating MCP client. Use `run_stop`
for a requested stop, not a UI close or agent disconnect. A person can resume
eligible interrupted work through the UI or person-only CLI action; no MCP resume
tool is offered.

CLI equivalents use the installed `lx` command from the chosen environment:

```text
lx init --workspace <absolute-task-folder> --template text-request
lx spec show --workspace <absolute-task-folder>
lx ready --workspace <absolute-task-folder>
lx run status --workspace <absolute-task-folder>
lx run edits <run-id> --workspace <absolute-task-folder>
lx results <run-id> --workspace <absolute-task-folder>
```

The angle-bracket values above are placeholders, not literal shell commands.
Quote real paths containing spaces. A CLI workspace path is passed with
`--workspace`; it is not an extra positional argument. `lx --help` and subcommand
help describe the current options.

## Read the outcome and decide what to reuse

After completion, use `results` and `edits`, then inspect the UI run page. Review:

- Sample order and original folder names; internal execution IDs can differ after
  shuffling and are not the source-folder identity.
- Base, intermediate and final outputs/checks, judge verdicts where configured,
  tool failures, cutoffs and recovered errors.
- What changed in prompts/skills/scripts, what the loop dropped, and tokens/costs.
- Whether a claimed gain survives independent held-out checks. A passed check or
  model judgment alone does not prove a learned rule is correct.

Checks do not choose a winner by score. A full run starts from stock, carries
learning between its samples and exports the last adopted harness. A finished
package can therefore be unchanged base, even when intermediate edits existed.

To smoke-test reuse, request `trial_start(workspace, from_run=...)`. Add
`from_sample` to choose a saved sample's adopted harness instead of that run's
final package. This selects the harness, not the trial input. It needs the same
task/tools and a valid live approval; it is not an independent transfer benchmark.

Download the task package and trajectories separately in the UI. The MCP summary
does not expose all raw answers or traces. Automatic combined evidence-package/
library UI and general deployment on arbitrary new inputs remain unfinished;
do not describe one-off historical ZIP handoffs as that product feature.

## Recover without losing evidence

For configuration errors, use [installation troubleshooting](installation.md)
and `spec_show`/`ready` instead of starting repeated live runs. For held spending,
use Usage and check the provider's billing before a person records billed/not-billed.
Agents cannot settle it. Preserve the first run and inputs when changing a task;
record the new spec/version rather than presenting a rerun as the original result.

Archive is for readable inactive work. Deletion permanently removes LX-managed
state and requires explicit opt-in for agents; original files and billing records
remain. See [configuration and lifecycle](configuration.md) before deleting.
