# Writing a task

How to set up a task for LX, for a person or a connected agent acting as orchestrator.
A coding agent can read this with `lx guide tasks`, or the `guide` tool over MCP.

The worked example is the goods receiving audit in `scripts/receiving_audit/`: a
`task.json`, its `check.py`, and `build.py`, which makes the samples.

## What an orchestrator does, and what it leaves alone

The orchestrator understands what the user wants and sets up the run: the task, the
tools and the check. The loop learns the procedure from real runs.

| Say | Leave to the loop |
|---|---|
| What the work is | The order of steps |
| What a good answer looks like | Thresholds and retries |
| What a tool does, and what the task needs from it | How to combine results |

So there is no spec field for skills, scripts or guidance. Keep answers out of the task
text as well: `spec_draft` refuses text that repeats them.

## The flow

1. `status`, to see whether LX can run. `environment`, to see what this install offers.
2. `init` on a folder that holds `samples/`, with a template.
3. `spec_draft`: goal, task text, check, tools, limits, mode.
4. `check_test`: how a correct, a blank and a wrong answer score.
5. `ready`: whether this could run on these samples. Free, with no calls.
6. `preview`: the whole flow on doubles. It works only on LX's synthetic samples.
7. `approve`, then `trial_start` for one real sample, then `run_start`. These spend
   money and send samples to providers, so only do them when the person asked for
   that run.
8. `run_status`, then `results` and `edits`.

The same actions exist as `lx` commands. Run `lx --help`.

## Samples

Two templates exist.

**`photo-request`.** Each sample is a folder `sample-*/` holding:

- `photos/1.jpg` and on, 3 to 7 photos, numbered from 1. JPEG or PNG. Paperwork can be
  a photo.
- `request.wav`, the spoken request. A live run needs it in every sample.
- `truth.json`, read only by the check.

**`text-request`.** A request in text and an answer in JSON. Use `samples` to see how
the folder looks after `init`.

Samples are split with a fixed seed: the first `stream_samples` after the shuffle feed
the loop, and the rest are held out. Folder names are not sample ids, because ids are
given after the shuffle.

## The spec

The task has these fields. `spec_draft` takes only those that change, and every change
makes a new version, which clears any approval.

| Field | Holds |
|---|---|
| `name` | Lower-case letters, digits and underscores |
| `truth_schema` | `free` for your own truth, or `inventory` |
| `stream_samples` | How many samples the loop learns from, 1 to 20 |
| `base_prompt` | The agent's starting prompt. Keep it to a line |
| `contract` | What the answer must contain, exactly |
| `work`, `domain_word`, `yardstick`, `deliverable_word`, `selection_checks` | Wording the loop uses to describe the task |

**Tools.** `tools` lists the model tools to keep on. Leave out `sam-catalog` if the task
does not need segmentation. Transcribe is required on photo tasks, because the request
is audio only. A tool that is off in Settings blocks the run until a person turns it on
or the spec leaves it out.

**Limits.** `environment` lists them with their bounds. The default reply length for
Spark, 4096 tokens, cut off 2 of 18 audit trials and scored them 0. 8192 cut off none
of 60 agent runs. Budgets are a person's to set.

## The scoring check

A check is one Python file, standard library only, run in the sandbox. It reads one
JSON object on stdin and prints one on stdout:

- `{"items": [{"truth": ..., "answer": ...}, ...]}` prints
  `{"results": [{"checks": [{"name": str, "passed": bool, "error": str or null}, ...]}, ...]}`
- `{"examples": [truth, ...]}` prints `{"answers": [a correct answer per truth, ...]}`

The score is the fraction of named checks passed. Make names depend only on the truth,
so that junk gets the same list.

`check_test` proves the check: a correct answer must score 1.0, junk must be graded
without a crash, grading must repeat exactly, and another sample's answer must not pass.

**Rules that catch people out**

- Errors must not hold expected values. Name the check instead, as in `line 2: failed at
  boxes`. A word from the truth in an error fails the self-test.
- Work out what you can from the truth. If `truth.json` holds your status words, the
  contract that lists them is refused for repeating answers.
- Chains are harder than partial marks. A chain passes only if every link does, which
  leaves room below 1.00 but moves the score in large steps.
- A weak check stays pending for a person: one with a high blank score, or one that
  could not try a wrong answer. A single sample cannot try one, so keep at least two
  different truths in the workspace.
- Validate truth in the file, in `example_answer`, so a mistake in the samples fails the
  self-test.

## Trying it

`trial_start` runs one sample on the base harness, with no improver, within a small cap.
It always takes the first sample of the shuffled stream. An agent gets the score, check
names, how it stopped, calls per tool and cost, but not the trace or answer. One sample
is a smoke test, not a measure.

**Aim for base scores of about 0.3 to 0.8.** Above that, the loop has nothing to gain.

From the receiving audit: extra reasoning traps did not lower scores on Spark, while
packed lookalikes did. Test each change with trials rather than judging by eye. And
look at repeated failures: one line that fails every time may be an unfair sample. In
the audit, a substitution that no document mentioned failed 12 of 12.

## Reading a run

- `results` gives means and each sample's score. `edits` shows what the improver wrote,
  and tokens.
- **One run cannot separate a gain from noise.** The same harness moved a sample by 0.2
  or more between runs. Compare at least three runs, as the paper does.
- **Scores choose nothing.** The package is the harness adopted on the last sample in
  the run's order, whatever the scores. Each run has its own order, shown as
  `stream_order`, so read the means across runs before you use a package.
- A script's `exit_code` 0 shows only that it did not crash. Read what it checks.
- Do not copy what the improver learned into the task text. That is procedure, and it
  stays with the loop.

For keys, libraries and system tools, read the environment guide: `lx guide environment`.
