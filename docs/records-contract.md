# Noisy order normalization (PROD-002)

The offline task uses 60 fictional text records: 20 development and 40 held-out.
Seed `20261028` generates unambiguous English-month dates, explicit currency
codes, noisy name whitespace/case, formatted amounts and phones, and duplicated
or mixed-case tags. The two splits use independent random streams and disjoint
rendering-template banks. Increasing development size leaves held-out data intact.
Names and reserved fictional phone numbers are synthetic.

Each answer is one JSON object containing exactly `order_id`, `customer`,
`date`, `currency`, `amount_minor`, `phone`, and `tags`. Names use title case
with single spaces; dates use YYYY-MM-DD; currency is USD/INR/EUR; amounts are
integer minor units (100 per major unit); phones use E.164 with +1 assumed for
national numbers, or null; tags are sorted unique lowercase strings. The full
contract is provided equally to base and candidate workers. We do not withhold
requirements to create an artificial improvement opportunity.

The independent controller verifier awards one point per correct, strictly
typed field. The denominator is seven plus the number of unexpected fields.
Missing/wrong fields receive no point; non-object or malformed JSON receives
zero of seven. Full credit is equivalent to acceptance. Booleans are not
integers. Bad answers remain in the denominator. This is field accuracy, not
record-level accuracy; reports also retain exact pass/fail checks.

Worker input manifests contain only sample/source IDs, split and source text.
Truth is stored separately. The adapter stages only development inputs for the
improver. It sends text-only worker requests and permits edits only to the
existing always-on instruction files. All such files affect both requests and
cache identity. The contract, verifier source, controller truth digest and
execution mode also bind cache/experiment identity.

The core remains blind to grades: its `score()` hook is a separate judge API,
not an optimization feedback channel. Adaptation uses ordinary source/output
evidence and the task contract. No verifier verdict or answer key enters the
proposer context. We preserve this core guarantee; testing supervised grade
feedback would be a separate design decision.

Each trial starts a fresh core/library/cache and adapts on the first development
item, then compares the frozen candidate with the base on **all 20 development
items**. A strictly higher mean development field score retains the candidate;
ties retain base. Both frozen candidate and base are then measured on the same
40 held-out items; those scores cannot change retention. One adaptation item
keeps the planned 200-call trial ceiling plausible: 120 independent worker
evaluations plus about four core rollouts, with the remaining calls available
for improver/reflection sessions. This is a bounded first test, not proof that
one practice example is sufficient.

The five-trial criterion concerns the retained, usable package: at least a
0.10 held-out mean gain in at least four trials and no regression worse than
0.05. Candidate-only gains are reported separately. Fewer than five offline
smoke trials cannot satisfy the criterion. These thresholds are a practical
gate; five runs do not establish broad statistical/general-task validity.

The offline provider double has no truth access: every worker returns `{}` and
the improver makes no edits. Its five zero-gain trials verify reporting and
base retention; they provide no evidence of model capability. Live trials
remain disabled until calibration and a separately approved E4b request.

## Reproduce offline

From the supported installed WSL environment:

```bash
.venv/bin/python scripts/validate_records.py --evidence evidence/local-records
.venv/bin/python -m lx.trials --output /tmp/lx-records-preview --trials 5
```

Use fresh output directories. The validation script uses native temporary
storage for engine artifacts and exports only synthetic inputs/truth and the
path-free report. The existing image app continues to use its existing domain;
the new domain is available through the script/API path, without UI changes.
