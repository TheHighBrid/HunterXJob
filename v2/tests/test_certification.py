from app.certification import browser_verified, evaluate_results, select_round_robin
from app.discovery import JobRecord


def _job(board: str, job_id: str, title: str) -> JobRecord:
    return JobRecord(
        source="greenhouse",
        external_id=job_id,
        title=title,
        company=board,
        location="Canada",
        remote=False,
        url=f"https://job-boards.greenhouse.io/{board}/jobs/{job_id}",
        description="Representative live-form certification posting",
        board=board,
    )


def test_round_robin_spreads_selection_across_boards() -> None:
    records = {
        "alpha": [_job("alpha", "1", "A1"), _job("alpha", "2", "A2"), _job("alpha", "3", "A3")],
        "beta": [_job("beta", "4", "B1"), _job("beta", "5", "B2")],
        "gamma": [_job("gamma", "6", "C1")],
    }

    selected = select_round_robin(records, 5)

    assert [job.board for job in selected[:3]] == ["alpha", "beta", "gamma"]
    assert len(selected) == 5


def test_needs_review_counts_when_browser_verified() -> None:
    result = {"status": "needs_review", "dom_verified": True, "board": "alpha"}
    assert browser_verified(result) is True


def test_certification_passes_with_verified_plans_across_required_boards() -> None:
    results = [
        {
            "status": "needs_review" if index % 2 else "dry_run_ready",
            "dom_verified": True,
            "board": f"board-{index % 5}",
        }
        for index in range(30)
    ]

    summary = evaluate_results(results)

    assert summary.passed is True
    assert summary.browser_verified_runs == 30
    assert summary.distinct_boards == 5
    assert summary.flagged_runs == 0


def test_certification_fails_on_flagged_or_unverified_run() -> None:
    results = [
        {"status": "dry_run_ready", "dom_verified": True, "board": f"board-{index % 5}"}
        for index in range(29)
    ]
    results.append({"status": "flagged", "dom_verified": False, "board": "board-4"})

    summary = evaluate_results(results)

    assert summary.passed is False
    assert summary.browser_verified_runs == 29
    assert summary.flagged_runs == 1
    assert "browser-verified plans 29/30" in summary.reasons
    assert "flagged runs 1" in summary.reasons
