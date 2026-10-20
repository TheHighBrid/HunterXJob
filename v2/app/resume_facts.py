from __future__ import annotations

from pathlib import Path

RESUME_FACTS_PATH = Path("data/resume_facts.txt")


def read_resume_facts(path: Path = RESUME_FACTS_PATH) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")
