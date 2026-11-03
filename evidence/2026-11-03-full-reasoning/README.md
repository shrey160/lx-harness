# Preserve full returned worker reasoning

User direction, 3 November 2026: remove the reasoning cutoff before running
Muse Spark 1.3 Contributor on the inventory/cash task.

Removed the 1,500-character slice from the spec-driven worker loop. Returned
reasoning is now preserved in full in the structured trace and the Markdown
trajectory shown to the improver. On tool turns with no ordinary assistant
text, the existing provider-neutral history note carries the full reasoning.
Ordinary assistant content still takes precedence in conversation history.
No task procedure or model reasoning setting was changed. Context, request
size, generation, time and budget checks still apply; this removes silent
reasoning-text clipping, not provider/server boundaries. Earlier truncated
trajectories cannot be reconstructed from their saved prefixes.

Validation: **41 tests passed** on native Windows in **28.517 s**, covering the
whole-context loop and model-tool integration. The long-reasoning regression
uses tool-turn and final reasoning longer than 9,000 characters and verifies
exact history, structured-trace and rendered-trajectory preservation. Existing
tests cover ordinary assistant content, missing reasoning, image retention,
tool errors and paused experiment behavior. No live model calls in validation.

The first integration invocation needed the tests directory on the import path;
the next hit restricted Windows temporary-folder permissions. The complete
passing invocation used normal Windows temporary storage. No full-suite rerun.

Requested Spark experiment setup/results are a separate bounded operation;
see context for its current approval and run state.
