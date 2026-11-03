# Original sample names in run views

User reported that overview and trajectories showed different sample numbers.
The dataset shuffles internal IDs; source sample-01 in the three-sample run
is internally sample-03. Overview score rows and trajectory headings used
those raw IDs while the Samples view and other result sections used original
folder names.

Run-page state now includes the frozen internal-to-folder map. Score rows use
that map and sort by source folder without changing which scores belong to
which internal sample. Trajectory list/detail metadata reports both names,
groups by original folder/stage, and handles trial paths through execution.json.
Headings use the original folder; a note explains differing IDs inside saved
messages. Older traces without mappings retain their saved-name fallback.
Neither saved artifacts, worker inputs, IDs nor run order is rewritten.
Reasoning display wording also reflects full saved fields and older excerpts.

Validation: **23 run-output/UI-page tests pass** on Windows in23.654s, plus
Node display regressions for original-name headings, score association/order,
trials and legacy fallback. Frontend build/whitespace pass. No model calls.
Existing saved-run mapping and final visual verification are recorded with
the subsequent workspace lifecycle work.
