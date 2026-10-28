"""Event-log reconciliation domain plug-in for the shared instruction adapter.

The shared adapter knows two splits: ``dev`` (staged for the improver and used
for retention) and ``heldout`` (evaluated after freezing). This reader maps
``loop`` to ``dev`` and ``verify`` to ``heldout``. ``test`` and ``calibration``
keep their names, so the adapter never stages or evaluates them during a trial;
the controller scores ``test`` once after all trials.
"""
import json
from pathlib import Path
import re

from .eventlog import CONTRACT, SPLITS, verify_case
from .spike import wire_reply
from .workflow import ImagesAdapter


BASE_PROMPT = "Reconcile the order event log carefully and return JSON."
ADAPTER_SPLITS = {"loop": "dev", "verify": "heldout", "test": "test", "calibration": "calibration"}
WORKER_MARKER = "LX_EVENTLOG_WORKER"


class EventLogDomain:
    name = "lx_order_event_log"
    base_prompt = BASE_PROMPT
    task_prompt = CONTRACT
    verify = staticmethod(verify_case)

    @staticmethod
    def prompts():
        from harness2.core.spec import BenchPrompts
        return BenchPrompts(work="reconcile noisy order event logs into the final order state as JSON",
            domain_word="order event-log reconciliation", yardstick=CONTRACT,
            deliverable_word="reconciled order JSON",
            selection_checks="Check order filtering, duplicate IDs, timestamp order, cancellation, "
                             "currency conversion, discounts, rounding and the refund cap.")

    @staticmethod
    def samples_from(snapshot):
        root = Path(snapshot)
        manifest = root/"manifest.json"
        if root.is_symlink() or manifest.is_symlink():
            raise ValueError("Linked event-log inputs rejected")
        samples = json.loads(manifest.read_text(encoding="utf-8"))["samples"]
        if type(samples) is not list or not 1 <= len(samples) <= 4000:
            raise ValueError("Expected 1..4000 event-log samples")
        ids, sources, mapped = set(), set(), []
        for sample in samples:
            if type(sample) is not dict or set(sample) != {"sample_id", "source_id", "split", "text"}:
                raise ValueError("Invalid event-log manifest fields")
            if not all(type(v) is str for v in sample.values()):
                raise ValueError("Event-log inputs must be strings")
            if sample["split"] not in SPLITS or not 1 <= len(sample["text"]) <= 20000:
                raise ValueError("Invalid event-log split or text length")
            if any(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", sample[k]) is None for k in ("sample_id", "source_id")):
                raise ValueError("Invalid event-log identity")
            if sample["sample_id"] in ids or sample["source_id"] in sources:
                raise ValueError("Duplicate event-log or source identity")
            ids.add(sample["sample_id"])
            sources.add(sample["source_id"])
            mapped.append(dict(sample, split=ADAPTER_SPLITS[sample["split"]]))
        return root, mapped

    def messages(self, sample, prompt):
        return [{"role": "developer", "content": WORKER_MARKER+"\n"+prompt+"\n\nTask contract:\n"+self.task_prompt},
                {"role": "user", "content": "Source:\n"+sample["text"]}]


class EventLogAdapter(ImagesAdapter):
    def __init__(self, snapshot, output, client, truth, *, mode="offline_preview"):
        super().__init__(snapshot, output, client, mode=mode, domain=EventLogDomain(), truth=truth)


class EventLogPreviewTransport:
    """Truth-blind constant worker and no-op improver; cannot manufacture a gain.

    Calls are counted per kind so the offline run measures how many worker
    rollouts and improver sessions the core makes per adaptation item. Live
    sessions may take several turns where this double answers in one.
    """
    def __init__(self):
        self.calls = {"worker": 0, "reflection": 0, "improver": 0}

    def __call__(self, body, timeout):
        messages = body["messages"]
        if messages[0]["content"].startswith(WORKER_MARKER):
            self.calls["worker"] += 1
            return wire_reply("{}")
        names = messages[1]["content"].split("Available files:\n", 1)[-1].splitlines()
        if "adoption.json" in names:
            self.calls["reflection"] += 1
            return wire_reply("<experience>\ncue: order event-log reconciliation\nlesson: Apply events by timestamp.\n"
                              "evidence: Offline wiring check only.\nconfidence: low\n</experience>")
        self.calls["improver"] += 1
        return wire_reply("<notes>No instruction changes in this offline wiring check.</notes>")
