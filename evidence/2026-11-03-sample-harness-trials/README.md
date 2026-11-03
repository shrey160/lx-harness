# Trial a saved sample harness

User requested one local Unsloth trial of an improved harness, then explicitly
selected the Spark cup candidate after clarification that the final Spark
package was unchanged base. Source: run386bafba041f, source sample-01,
internal sample-02, adopted combined harness c1. Candidate remains unedited,
including the previously documented cash-sign wording contradiction.

Added optional **from_sample** beside trial from_run. It identifies a source
sample folder and loads the adopted harness named in that completed run's
saved report. This allows testing a learned sample harness even when the final
package came from a different sample and kept base. It selects a harness, not
the trial input. No scores select a harness. Existing task/tool compatibility,
approval and caps remain; source must be a finished live task run. Unsafe
paths, linked path components, unknown samples and missing harnesses refuse
before job creation. The trial copies its own harness, runs no improver, and
reports source run, source sample and the executed harness fingerprint.

Available through engine, HTTP action, orchestration/MCP and CLI:
`lx run trial WORKSPACE --from RUN --from-sample SAMPLE_FOLDER`.
No new graphical selector. Existing clients need a fresh server to see the
added optional tool argument; this chat's older imports are not used for launch.

Validation on native Windows with normal temporary storage: **53 trial/CLI
tests pass in66.744 s**, including actual candidate execution and prompt
delivery, independent copy after source removal, grant forwarding, missing
source/harness, path escape and trial-only checks. **7 MCP tests pass in61.330 s**,
including the real stdio preview flow. Changed modules compile and whitespace
checks pass. No full suite or live model calls during validation.

Prepared inventory-update-json specv6 with local LFM3-VL-2B worker,
worker_reply_tokens0 (no LX generation cap), full returned reasoning retained,
unchanged task/check/judges/tools and existing $0.25 trial/$0.25 judge caps.
Ready true,22 check self-tests pass. Selected candidate fingerprint:
`fad0d047e65a9b89129e2737893667a36708384e3055ea4e5335695937ad47f7`.
The user's explicit trial request and candidate choice authorize one trial;
live outcome is recorded separately. Previous packages/results are preserved.
