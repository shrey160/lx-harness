# Agent permission and one-click workspace deletion

3 November 2026. User requested that agents can delete workspaces when a person has
turned the setting on, and that users can delete with one click. Supersedes the
typed-name/person-only design in the preceding lifecycle implementation.

## Changes

- Settings → Connectivity offers **Allow agents to delete workspaces**, default off
  on both existing and new installs. It is stored in user-level state, accepts only
  a boolean through the authenticated person-facing HTTP API, and has no MCP/CLI
  action that can turn it on.
- `status.can_delete_workspaces` reports effective permission. The new destructive
  MCP action `workspace_delete(workspace)` and `lx workspace delete --workspace
  FOLDER` use the same service guard. Permission and global agent access are checked
  on every deletion call, also under the workspace lock. Revocation affects sessions
  already open. Existing MCP processes must reconnect to load the new tool.
- Workspace options has a direct **Delete workspace** button, with the consequences
  stated above it. No typed name, second button or confirmation dialog. HTTP accepts
  an empty object. Optional name confirmation remains compatible with older API
  clients that send it, but is no longer required.
- Existing active-job, unsettled-call, linked-path containment, original-file and
  billing preservation protections remain. The user's screenshot shows unsettled
  calls; this change does not resolve those calls or bypass that guard.

## Validation

- 74 focused test cases across lifecycle, HTTP, CLI, MCP, UI pages and orchestrator
  boundaries, completed in two runs: the initial combined suite passed the 58
  non-CLI cases (2 Windows symlink privilege skips), but three inherited CLI cases
  used an incorrect positional folder argument. Corrected the test to use the
  existing `--workspace` option and all 16 CLI cases passed in16.019s. Initial
  combined run116.853s. No implementation failure remained.
- Real MCP SDK client: permission denied by default, allowed flag visible after
  enabling, revocation refused on the same connection, enabled deletion succeeds
  on disposable data; tool marked destructive and setter absent from tool list.
- Engine/HTTP tests: sticky permission, strict boolean/token requirements, stopped
  global access, active-run/zero-cost unsettled-call refusal, one-click deletion,
  preservation of source files/ledgers, prior failure and junction protections.
- Node display regressions and frontend build pass. UI renders the flag off/on,
  one direct delete button without any form, and disabled controls during active jobs.
- Fresh app/browser confirms unchecked permission and the direct delete button,
  with no name form. Real setting remains off. No existing user workspace was
  deleted, no final delete button was clicked in the browser, and no live model call
  or new experiment was made. Actual deletion uses disposable engine/HTTP/MCP/CLI
  test fixtures only. Screenshots retained locally under `.lx/lifecycle-qa/`.

Full suite not run. No model-quality claim. Prior lifecycle evidence remains historical.
