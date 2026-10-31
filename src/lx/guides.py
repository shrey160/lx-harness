"""The guides an orchestrator can read: the files in docs/, by topic, never by path."""
from __future__ import annotations

from pathlib import Path

DOCS = Path(__file__).resolve().parents[2]/"docs"
GUIDES = {"tasks": ("tasks.md", "Writing a task: samples, the spec, the scoring check, and how to try and read a run"),
          "environment": ("environment.md", "Preparing an environment: keys, hosted tools, Python libraries and system tools")}


def guide_list():
    return [{"topic": topic, "about": about} for topic, (_, about) in GUIDES.items()]


def read_guide(topic, root=DOCS):
    if topic not in GUIDES:
        raise ValueError("Choose a guide: "+", ".join(GUIDES))
    path = Path(root)/GUIDES[topic][0]
    if not path.is_file():
        raise ValueError(f"The {topic} guide is not in this install; it lives in the repository's docs folder")
    return {"topic": topic, "text": path.read_text(encoding="utf-8")}
