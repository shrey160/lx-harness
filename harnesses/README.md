# harnesses

Frozen harnesses kept for experiments. A harness is the set of files that wrap a model:
always-on prompt, guardrails, memory and tool guidance, plus optional skills and helper
scripts.

| Folder | Purpose |
|---|---|
| `inventory-ea-v1/` | The harness learned on Spark in T8, used in E-B and E-C |

Its hash is pinned by the experiments that use it, so do not edit it. Write a new
version in a new folder. Harnesses from product runs are saved in each workspace, under
`.lx/packages/<id>/`.
