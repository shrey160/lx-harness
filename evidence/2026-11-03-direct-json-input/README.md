# Direct JSON input fix, 3 November 2026

Problem: an extra inventory.json file was included in the sample snapshot but
photo workers received only photos/audio. The inventory-and-cash dataset worked
around that by rendering the JSON as photo 3, adding unnecessary visual reading.
User requested planning and fixing direct JSON input. Previous full run
36966e092857 stays stopped; recorded spend $0.0009885.

## Product fix

Runtime commit `cfb6af4` adds optional **input.json**, a UTF-8 JSON object up to
64 KiB, to photo-request samples. It is validated before staging, included in
frozen dataset hashes and execution identity, and supplied as `input` in the
worker user message and improver source/sample.json. The check's truth stays
controller-only. Only this named input is exposed, not arbitrary files.
Duplicate fields, non-finite numbers, oversized data and linked inputs are
refused. Manifest paths are bound to that sample's input file.

General photo tasks accept 2-7 photos. Legacy inventory experiments retain
their 3-7 contract. Samples without input.json retain their existing message
fields and dataset/hash format. No learning algorithm, procedure, tool-order,
provider setting, budget or token/time limit changed.

Approval preserves a frozen copy: edits to the source after approval do not
change what the run receives. Altering the frozen input is refused before
execution. Changed input content invalidates cached executions.

## Corrected dataset

Builder now writes `.lx/inventory-update-json/`, preserving the original
`.lx/inventory-update-local/` and its runs. Each sample has two object JPEGs,
input.json (starting quantities, prices and cash), revised mono audio and
controller-only truth. There is no JSON-sheet photo. Photos and truth match
the original dataset byte for byte; expected stock/cash/evidence are unchanged.

New workspace **Inventory and cash JSON**, draft v3, live mode but unapproved:
existing Unsloth LFM3-VL-2B worker, Spark improver, SAM + Transcribe, worker
identity note and the user's concise-small-worker improver hint. Existing $1
run cap, 8192 reply tokens, 600 s per call and 4-hour limit retained.

## Evidence and validation

- Focused suite: **68 tests on WSL**, 43.254 s, no skips; **68 on Windows**,
  254.665 s, one skip (Windows account cannot create symlinks). Includes intake,
  legacy inventory, generic tasks, text tasks and six new input tests. The
  symlink/linked-parent test passes in WSL. No full application suite rerun.
- Actual adapter executions on doubles prove worker and improver input equality,
  absence of answer sentinel, unchanged-input reuse and changed-input rerun.
  Approval test proves source edits cannot change approved data and snapshot
  tampering is refused. Bad JSON/duplicates/non-finite values/size/path tests pass.
- Rebuilt dataset: **40 negative checker cases**, correct/reordered answers pass;
  inputs/quantities/cash/boxes/sources reconcile; 12 distinct cutouts. Four object
  photos and unannotated overview visually reviewed. New audio is mono 16-bit
  24 kHz, 19.11/19.46 s, signal/duration checked; no independent transcription
  or listening check was performed.
- LX check_test: 2 samples, 8 checks each, **22 cases**, blank/wrong score 0,
  no weaknesses. Ready true in fresh engine, reloaded app HTTP API and fresh
  stdio MCP SDK client (22 tools). No model calls in these checks.
- Frozen-dataset worker messages for both real samples match their input.json
  exactly, with two photo metadata entries and no answer data; kept locally as
  worker-input-proof.json. This is input delivery evidence, not model capability.
- Export ZIP has **19 allowlisted files**, integrity checked, no runtime state.
  Source/task copies, unchanged photos/truth, JSON/Python and whitespace checks pass.
- Native app reloaded on port 8765. The already-open chat's long-lived MCP
  process still has pre-fix imports; reconnect it or use a fresh LX process
  before approving/running these two-photo samples. Fresh MCP readiness passed.

No replacement trial or full run was started or approved. Old trial 02cae81b45e3
remains 0/8 with no tools and $0; its input format differs from this dataset.
Both corrected samples are learning-stream samples, without held-out evidence.

Environment notes: restricted Windows temporary directories prevented the
initial tests from writing/publishing snapshots; normal user temporary storage
fixed that. An early test incorrectly expected source edits to invalidate an
approved snapshot; it was corrected to match the existing freeze-at-approval
contract, without changing approval semantics.
