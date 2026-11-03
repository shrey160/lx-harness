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
3. `spec_draft`: goal, task text, tools, limits, mode. `eval_init` starts a check to edit.
4. `check_test`: how a correct, a blank and a wrong answer score. `check_try`: what a
   changed check does to answers already saved.
5. `ready`: whether this could run on these samples. Free, with no calls.
6. `preview`: the whole flow on doubles. It works only on LX's synthetic samples.
7. `approve`, then `trial_start` for one real sample, then `run_start`. These spend
   money and send samples to providers, so only do them when the person asked for
   that run.
8. `run_status`, then `results` and `edits`.
9. `trial_start` with `from_run` set to a finished live run, to try that run's package
   on one sample. It runs the same sample as a plain trial, so the two can be compared.
   Its result says whether that run saw the sample. The task and the agent's tools must
   match the current spec; the worker model and the check may differ. One sample is a
   smoke test, not a measure.

   Add `from_sample` with the source sample folder (for example `sample-01`) to
   try the adopted harness saved for that sample instead of the final package.
   This works even when the final package kept the base harness. The trial
   copies that saved harness and reports its source run, sample and fingerprint.
   It selects the harness, not the trial input. CLI:
   `lx run trial WORKSPACE --from RUN --from-sample sample-01`.

The same actions exist as `lx` commands. Run `lx --help`.

`status.can_delete_workspaces` reports whether a person has enabled workspace deletion
by agents in Settings → Connectivity. When allowed, `workspace_delete(workspace)`
(or `lx workspace delete --workspace FOLDER`) permanently removes only that workspace's
LX settings, runs and harnesses. Original samples, unrelated files and real-spend
records remain, including unsettled calls that a person can reconcile later in Usage.
Active runs block deletion. The permission is off
by default, is checked on every call and can only be changed by a person in Settings;
agents have no tool to turn it on. Stopping Agent access blocks deletion too.

## Samples

Two templates exist.

**`photo-request`.** Each sample is a folder `sample-*/` holding:

- `photos/1.jpg` and on, 2 to 7 photos, numbered from 1. JPEG or PNG. Paperwork can be
  a photo.
- `request.wav`, the spoken request. A live run needs it in every sample.
- Optional `input.json`: starting task data as a UTF-8 JSON object, up to 64 KiB.
  It is delivered directly as `input` in the worker's user message and in the
  improver's sample context. It is frozen and hashed with the photos/audio; never
  put the expected answer here. Duplicate keys, non-finite numbers and links are
  refused. Other JSON files are not automatically exposed. Legacy inventory
  checks still require 3 to 7 photos; general tasks can use 2.
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
of 60 agent runs. Budgets are a person's to set. Agent turns, agent tool calls and
improver turns have no limit until a person sets one: no count is built in, and an empty
value means none. A model that works in many small steps needs room; the call count, the
run time and the budget still end a run.

`limits.worker_reply_tokens` overrides reply length for the worker alone. Set
it to `0` for no LX token cap on a free local model: the request omits the token
limit, leaving the server/model's own limits and LX's time/cancellation bounds.
The improver and judge retain `limits.reply_tokens` and finite cost reservations.
Without an override, the worker inherits the existing reply length. In the GUI,
this is **Worker reply tokens** under Advanced: run limits.

**Improver hints (optional).** `hints` is text for the improver only, and `sample_hints`
maps a sample folder to its own text (empty text removes it). Files named
`improver_hints.md`, in the samples folder or in a sample folder, seed hints that are
blank; the spec wins after that, and a file that later differs blocks approval until
`use_hint_files` takes the files or the file is changed back. The worker and the check
never read hints. The improver reads them as `source/hints.md` for each sample it learns
from, never for a held-out one, under a line telling it they describe the kind of task,
so its edits should hold for any case of that kind.

Write a hint as a person who knows the job would explain the workflow: what to hear or
read first, which source wins over which, what to check before answering, what the reader
of the answer expects. Never write an answer, a count, a total, a price or a box, and
leave out facts about one sample: a hint that holds box coordinates, the expected total
or several of the answers is refused. A package learned with hints says so in its
limitations. Hints are not in the Harness² paper, which keeps scores and answers from
the improver; they are an extension, so treat what the loop learns from them as a
hypothesis to check on samples it has not seen.

**Telling the improver which model the worker is (optional, off by default).**
`tell_improver_worker` (`lx spec draft --improver-knows-worker on`, or the switch under
Hints in the Spec tab) gives the improver `source/worker.md`: the worker's model name and
facts from its provider entry (context window, images, tool calls, thinking), with no
advice on how to edit. The paper's improver prompts never name the executor, and a
harness is meant to carry over to other models, so this is an experiment: a small local
worker may need shorter edits than the loop writes for it. The package says when it was
used, and what is learned may suit that model and travel less well to another.

## The scoring check

A check compares an answer with the sample's `truth.json`. The score is the fraction of
named checks passed; each check is pass or fail. Write it in the simplest form that fits.
All three run in the sandbox with the standard library, and any form can be written in
the Spec tab, kept in `eval/`, or passed to `spec_draft` as `check`.

**Rules.** No code. Each rule is one named check:

```json
{
  "rules": [
    {"field": "order_id", "rule": "equals"},
    {"field": "status", "rule": "one_of", "values": ["open", "closed"]},
    {"field": "phone", "rule": "matches", "pattern": "\\+[1-9][0-9]{7,14}"},
    {"field": "lines.0.total", "rule": "near", "within": 0.01},
    {"field": "tags", "rule": "same_items"}
  ],
  "extra_fields": false
}
```

| Rule | Passes when |
|---|---|
| `equals` | The answer's field equals the truth's |
| `one_of` | It is one of `values`; the truth is not read |
| `matches` | It is text that wholly matches `pattern`; the truth is not read |
| `near` | It is a number within `within` of the truth's |
| `same_items` | It holds the truth's list, in any order |

A dotted field reads inside objects and lists. `"extra_fields": false` adds a check that
the answer has no other top-level fields. The correct answer for the self-test is the
truth itself, so a `one_of` or `matches` field must appear in the truth and pass.

**Function.** For anything rules cannot say:

```python
def score(truth, answer):
    lines = answer["lines"]
    return {"count": len(lines) == len(truth["lines"]),
            "total": sum(line["total"] for line in lines) == truth["total"]}
```

Return a dict of check names to true or false, or one true or false. An answer the
function cannot read, such as a reply that was not JSON, scores 0, so there is no need to
guard against junk. If a correct answer is not simply the truth, add
`def example(truth):` returning one.

**Full check.** Reads one JSON object on stdin and prints one on stdout:

- `{"items": [{"truth": ..., "answer": ...}, ...]}` prints
  `{"results": [{"checks": [{"name": str, "passed": bool, "error": str or null}, ...]}, ...]}`
- `{"examples": [truth, ...]}` prints `{"answers": [a correct answer per truth, ...]}`

Make names depend only on the truth, so that junk gets the same list. A file that reads
stdin is treated as a full check and saved as it is.

**Editing without resending.** `eval_init` (`lx eval init`) writes `eval/rules.json`
beside the samples, with one `equals` per field, to edit from there. It needs a valid
spec first: set `stream_samples` to fit your samples. Keep one of `eval/rules.json` or
`eval/check.py`. A file takes effect only when saved with `spec_draft` and
`use_eval_file`, and a file that differs from the spec blocks approval, so what runs is
never in doubt. Saving a check in the Spec tab counts as differing: change or remove the
file, or load it into the editor.

**Testing.** `check_test` proves a check: a correct answer must score 1.0, junk must be
graded without a crash, and grading must repeat exactly. Another sample's answer passing
is a weakness, so the check waits for a person, who may accept it when one answer can
suit several samples. Give it a `check`, or `use_eval_file`, to test a draft: it makes no
new version and keeps any approval.

`check_try` scores the answers saved by earlier trials and runs, for free, and lists the
scores and named checks that would change. It takes a draft the same way, saves nothing,
and shows scores and check names, never answers. A trial that failed saved no answer.

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

## Running with no check

A check is optional. The loop never sees scores, so it learns the same way with or
without one: the check only reports on a run after it ends. Use `spec_draft` with
`no_check`, `lx spec draft --no-check`, or Run without a check in the Spec tab.

- Samples need no `truth.json` on a live run. A preview still does, because its doubles
  answer from it.
- Nothing says whether the harness got better or worse. A run page shows each sample's
  reply before and after, for a person to judge, and the package says it was never
  scored and holds no `check.py`.
- Only a person can approve it. A connected agent or a grant gets `pending`.
- A check file left in `eval/` blocks approval until it is removed or saved as the check.
- To score such a run later, add `truth.json` to the samples and call `check_try` with a
  draft check. Saving a check turns scoring back on for later runs; a past run keeps its
  result.

## Model judges

Some things no rule can check: whether a reply answers what was asked, or whether a
summary leaves out something that matters. A model judge answers yes-or-no questions
about each answer. Its verdicts count as named checks beside the scoring check, or in
place of one when the spec has no check. The judge runs after the loop, so the loop
never sees it, the same as a check.

```json
{"criteria": [{"name": "answers the request",
               "question": "Does the reply answer what the customer asked, without inventing an order?"}],
 "sees_request": false}
```

- **Where to write them:** the Model judges section in the Spec tab, `spec_draft` with
  `judges`, `lx spec draft --judges FILE`, or `eval/judges.json` taken with
  `use_judge_file`. A judges file that differs from the spec blocks approval, as a check
  file does.
- **What the judge sees:** the criteria, the answer, and the expected answer when the
  sample has one. On a text task, `sees_request` also lets it read the sample's request.
  It sees no photos or audio.
- **Model and cap:** a person picks the judge's model and the most it may spend on one
  run. Approval binds both, and the data notice names the judge's host. Every call holds
  its whole context and reply at the model's prices while it runs, so the cap must cover
  at least one call; a lower cap blocks the spec and says the least it needs. Judge calls
  show under role `judge` in a run's usage.
- **One call per answer:** each call judges every criterion at once. A reply that cannot
  be read is asked once more. An answer still without a verdict is *not judged*: it has
  no score, never a zero, and the means leave it out and say how many.
- **Kept verdicts:** each verdict is kept, keyed by everything the judge saw, so the same
  answer is never paid for twice. A regrade uses kept verdicts only.
- **Who sees what:** the run page shows each reply's verdicts with the judge's reasons,
  for a person. Reasons can quote the expected answer, so agents get only criterion
  names and whether they passed.
- **Approval:** a judged spec always carries the weakness that a model's verdicts can
  vary and may be wrong, so only a person can approve it. A connected agent or a grant
  gets `pending`.
- **Trying judges:** `judge_try` (`lx judge try`) shows what the judges, or draft
  criteria, make of answers earlier runs saved, and what differs from each run's own
  verdicts. On a live spec it never spends for an agent: only kept verdicts count. A
  person can pay for new ones with Try on saved answers in the Spec tab, under a cap,
  after accepting what the judge receives. On a preview spec, a double judges for free,
  which shows the plumbing and nothing about a real judge.

A judge is a model and can be wrong. Before trusting a judged score, read its reasons on
a few answers, and try it on answers you know are right and wrong.

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
