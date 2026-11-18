"""Validate an application-ledger export offline and print a JSON summary.

Usage: python scripts/ledger_import.py --input export.jsonl
Nothing is written to the database; see docs/LEDGER_IMPORT.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ledger_import import import_lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="JSON Lines export")
    args = parser.parse_args(argv)
    with args.input.open(encoding="utf-8") as handle:
        report = import_lines(handle)
    print(json.dumps(report.summary(), indent=2))
    return 1 if report.rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
