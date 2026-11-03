# Delete workspaces with unsettled calls

3 November 2026. User explicitly requested removal of the "resolve unsettled calls
before delete" blocker after the prior one-click deletion change.

- Deletion now succeeds with reserved or unknown billing entries, including zero-cost
  entries. It neither settles them nor guesses whether the provider charged. Active
  queued/running jobs still have to stop; original files and user-level ledgers stay.
- The same behavior applies to opted-in agents. Agent deletion remains off by default
  and only a person can enable it. Archive/restore behavior is unchanged.
- Deleted workspaces retain a minimal name/id/deletion-time billing record in user-level
  state. Settings → Usage shows their spent/held totals, even after reopening the app.
  A person can later record held calls as billed or not billed after checking the
  provider's bill. The authenticated endpoint requires an explicit boolean and is not
  exposed as an agent tool. Source workspace files are never recreated for this step.
- State continues to include deleted-workspace usage events. The accounting decision
  is separate from deletion; delete itself remains a single button and empty POST.

Validation: 31 lifecycle/HTTP/UI-page cases, 2 Windows symlink privilege skips.
One existing HTTP lifecycle case hit a Windows snapshot-publish failure during
fixture creation; its isolated rerun passed in0.721s. All deletion/preservation/new
billing-reconciliation cases passed in the combined run16.147s. Actual Windows
junction boundary cases pass. Node display tests cover retained-billing totals and
resolution buttons alongside deletion/permission controls; frontend build and
whitespace checks pass. Full suite not run.

Fresh app reloaded on8765 after checking no jobs were active. Agent deletion setting
stays off. No existing user workspace was deleted and no live model call was made.
All actual deletion/reconciliation tests use disposable data, no real billing
decisions. Prior lifecycle evidence is historical where its unsettled-call refusal
description differs from this behavior.
