# Inventory E-A offline preview (29 Oct 2026)

`preview_stream(count=3)` from `lx.inventory_workflow` at `6dc5adb`: three
synthetic shape samples (D3), the real core Propose-Probe-Compose loop (k=1,
sequential mode, library carried forward), the real script sandbox, and
deterministic doubles for the agent, improver, SAM (5% misses, some
low-confidence false positives) and Transcribe.

- Every sample: the improver added `skills/inventory-check/SKILL.md`, its
  trigger line in `guardrails.md` and `scripts/sum_counts.py`; validation
  accepted them; the agent loaded the skill and ran the script in the sandbox.
- Checklist means: base 0.639, Harness² 0.917 (synth-01 0.625 to 0.75, where the
  SAM double missed one instance; synth-02 and synth-03 0.625/0.667 to 1.0).
- Chat calls (double): 91 worker, 18 improver, 3 reflection. No network, no spend.

Plumbing evidence only: the doubles follow the harness text by construction,
so these numbers say nothing about Spark or any local model.
