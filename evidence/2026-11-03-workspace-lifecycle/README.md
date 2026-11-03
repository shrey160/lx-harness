# Workspace archive, restore and delete

3 November 2026. Completes the unfinished backend checkpoint `578a730`.
No live model calls, new experiments or changes to existing user workspaces.

## Behavior

- Workspace options offers Archive/Restore and Delete. Archived workspaces appear
  separately in the sidebar, with samples and run history accessible. Starting,
  resuming, approval, grants, discussion and paid judge tries are blocked.
- Archive survives reopening and a fresh engine. Restore preserves prior approval.
- Delete requires an exact name, blocks queued/running jobs and reserved/unknown
  calls (including zero-cost calls), and removes only the workspace's `.lx` data
  and its workspace/approval/grant/notice registration. Samples, unrelated files,
  real-spend ledgers and budget records remain.
- The `.lx` root cannot redirect through symlinks or Windows junctions. Deletion
  closes the Windows workspace lock before moving to a unique contained staging
  directory. Starts and reopens are blocked during that move. Failed rename restores
  registration; failed cleanup returns the retained directory and the UI shows a
  persistent message with its location.
- These controls are HTTP/person-facing; no lifecycle MCP tools were added.

## Validation

- Native Windows workspace/task/agent/grant regressions: 101 tests, 3 skips,
  311.331 seconds. Two skips require Windows symlink privileges; one is an existing
  workspace symlink test. No live model calls.
- Final lifecycle suite: 11 tests, 2 symlink-privilege skips, 6.329 seconds. Actual
  Windows junction tests cover both a linked root refusal and a nested link whose
  external target survives deletion.
- Final lifecycle/HTTP/output/UI-page suite: 46 tests, 2 symlink-privilege skips,
  44.499 seconds. Authenticated lifecycle routes and missing-token refusal pass.
- `node display-tests.mjs`: original sample names, score association/order,
  trial/legacy labels, archived sidebar placement, hidden run starts, active-job
  disabled controls and exact-name delete confirmation pass. Frontend build passes.
- Browser: fresh app on port 8765. Completed three-sample run overview and all 12
  trajectory names use the original folder order. Cup base trajectory says source
  sample-01 / saved execution sample-03 and contains the cup request and answer.
- Disposable workspace only: Archive moves it into Archived; opening it shows
  history without start/spec/discuss controls; Restore returns the normal controls.
  Delete form stays disabled for blank/wrong names, enables for exact name and
  cancels correctly. Final UI delete was not clicked; actual deletion was exercised
  through disposable engine and authenticated HTTP tests.

No full-suite or model-capability claim. Earlier model results and artifacts stay
unchanged. Browser checks used the browser connector; no native Windows UI automation.
The disposable browser-check workspace was removed through the engine afterwards,
with its original synthetic samples retained. The updated app is left open on the
completed experiment, with Workspace options expanded. Screenshot retained locally
under `.lx/lifecycle-qa/workspace-options.jpg` (not committed).
