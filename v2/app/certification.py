from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from app.discovery import JobRecord

DEFAULT_REQUIRED_RUNS = 30
DEFAULT_MIN_BOARDS = 5
VALID_PLAN_STATUSES = frozenset({"dry_run_ready", "needs_review"})


@dataclass(frozen=True, slots=True)
class CertificationSummary:
    required_runs: int
    min_boards: int
    attempted_runs: int
    browser_verified_runs: int
    distinct_boards: int
    flagged_runs: int
    passed: bool
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def select_round_robin(records_by_board: Mapping[str, Sequence[JobRecord]], limit: int) -> list[JobRecord]:
    """Select live postings evenly across boards instead of exhausting one employer first."""
    if limit < 1:
        raise ValueError("limit must be at least 1")

    queues: dict[str, deque[JobRecord]] = {}
    for board, records in sorted(records_by_board.items()):
        ordered = sorted(records, key=lambda item: (item.title.casefold(), item.location.casefold(), item.external_id))
        if ordered:
            queues[board] = deque(ordered)

    selected: list[JobRecord] = []
    while queues and len(selected) < limit:
        for board in list(queues):
            queue = queues[board]
            selected.append(queue.popleft())
            if not queue:
                del queues[board]
            if len(selected) >= limit:
                break
    return selected


def browser_verified(result: Mapping[str, object]) -> bool:
    """A review stop still counts as a valid certification plan if the rendered form was verified."""
    return result.get("status") in VALID_PLAN_STATUSES and result.get("dom_verified") is True


def evaluate_results(
    results: Sequence[Mapping[str, object]],
    *,
    required_runs: int = DEFAULT_REQUIRED_RUNS,
    min_boards: int = DEFAULT_MIN_BOARDS,
) -> CertificationSummary:
    if required_runs < 1:
        raise ValueError("required_runs must be at least 1")
    if min_boards < 1:
        raise ValueError("min_boards must be at least 1")

    verified = [result for result in results if browser_verified(result)]
    boards = {str(result.get("board") or "") for result in verified if result.get("board")}
    flagged = sum(1 for result in results if result.get("status") == "flagged")
    reasons: list[str] = []
    if len(verified) < required_runs:
        reasons.append(f"browser-verified plans {len(verified)}/{required_runs}")
    if len(boards) < min_boards:
        reasons.append(f"distinct Greenhouse boards {len(boards)}/{min_boards}")
    if flagged:
        reasons.append(f"flagged runs {flagged}")

    return CertificationSummary(
        required_runs=required_runs,
        min_boards=min_boards,
        attempted_runs=len(results),
        browser_verified_runs=len(verified),
        distinct_boards=len(boards),
        flagged_runs=flagged,
        passed=not reasons,
        reasons=tuple(reasons),
    )
