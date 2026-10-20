#!/usr/bin/env python3
"""CLI entry for grouping fixture review/skip controls by gap. See app.fixture_answer_gaps."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.fixture_answer_gaps import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
