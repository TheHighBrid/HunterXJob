"""Greenhouse + Lever dry-run batch front end (scripts/ats_dryrun_batch.py), offline.

No network: board listings come from tests/fixtures and ``httpx.MockTransport``-free fakes, the
runs are fakes, and the zero-submit proof is computed from synthetic observations.
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

V2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2 / "scripts"))
ats = importlib.import_module("ats_dryrun_batch")

FIXTURES = V2 / "tests" / "fixtures"
LEVER_ID = "ec0fc968-0bb5-4a5e-a874-2cf716ff2668"
GH_URL = "https://job-boards.greenhouse.io/affirm/jobs/7821078003"
LEVER_PAGE = f"https://jobs.lever.co/waveapps/{LEVER_ID}/apply"


# --------------------------------------------------------------------------- targets

@pytest.mark.parametrize(("spec", "expected"), [
    (GH_URL, ("greenhouse", "affirm", "7821078003")),
    ("https://boards.greenhouse.io/affirm/jobs/7821078003?gh_src=x", ("greenhouse", "affirm", "7821078003")),
    ("https://job-boards.greenhouse.io/embed/job_app?for=affirm&token=7821078003", ("greenhouse", "affirm", "7821078003")),
    ("https://boards-api.greenhouse.io/v1/boards/affirm/jobs/7821078003", ("greenhouse", "affirm", "7821078003")),
    (f"https://jobs.lever.co/waveapps/{LEVER_ID}", ("lever", "waveapps", LEVER_ID)),
    (LEVER_PAGE, ("lever", "waveapps", LEVER_ID)),
    (f"https://api.lever.co/v0/postings/waveapps/{LEVER_ID}", ("lever", "waveapps", LEVER_ID)),
    (f"lever:waveapps:{LEVER_ID}", ("lever", "waveapps", LEVER_ID)),
    ("greenhouse:affirm:7821078003", ("greenhouse", "affirm", "7821078003")),
    ("greenhouse:affirm", ("greenhouse", "affirm", None)),
])
def test_parse_target_accepts_urls_specs_and_board_tokens(spec, expected):
    target = ats.parse_target(spec)
    assert (target["platform"], target["board"], target["id"]) == expected


@pytest.mark.parametrize("spec", [
    "https://jobs.ashbyhq.com/acme/6c74631d-0000-4000-8000-000000000001",  # Ashby is out of scope here
    "https://example.com/jobs/1",
    "workday:acme:1",
    "greenhouse:../etc:123456",
    "greenhouse:affirm:not-a-number",
    "lever:waveapps:12345",
])
def test_parse_target_refuses_other_hosts_and_unsafe_ids(spec):
    with pytest.raises(SystemExit):
        ats.parse_target(spec)


def test_select_from_lever_board_filters_title_and_every_location():
    payload = json.loads((FIXTURES / "ats" / "lever_postings.json").read_text(encoding="utf-8"))
    board = {"platform": "lever", "board": "spotify", "id": None, "company": "Spotify"}
    picked = ats.select_from_board(board, payload, keywords="engineer", locations="stockholm", limit=5)
    assert [item["id"] for item in picked] == ["ed663b5f-1b13-5fb7-853b-e4cdf805c6dd"]  # matched via allLocations
    assert ats.select_from_board(board, payload, keywords="engineer", locations="ottawa", limit=5) == []


def test_select_from_greenhouse_board_respects_limit():
    payload = {"jobs": [{"id": 1000 + n, "title": f"Fraud Analyst {n}", "location": {"name": "Remote Canada"}}
                        for n in range(4)] + [{"id": 2000, "title": "Chef", "location": {"name": "Ottawa"}}]}
    board = {"platform": "greenhouse", "board": "acme", "id": None, "company": "Acme"}
    picked = ats.select_from_board(board, payload, keywords="fraud", locations="canada", limit=2)
    assert [item["id"] for item in picked] == ["1000", "1001"]


def test_expand_targets_uses_one_get_per_board_token_and_dedupes():
    calls = []

    def fetch(url):
        calls.append(url)
        return 200, {"jobs": [{"id": 7821078003, "title": "Manager, Fraud Operations",
                               "location": {"name": "Remote Canada"}}]}

    targets, log = ats.expand_targets([GH_URL, "greenhouse:affirm", "lever:waveapps:" + LEVER_ID], fetch,
                                      keywords="fraud", locations="canada", per_board=3)
    assert calls == ["https://boards-api.greenhouse.io/v1/boards/affirm/jobs"]
    assert [(t["platform"], t["id"]) for t in targets] == [("greenhouse", "7821078003"), ("lever", LEVER_ID)]
    assert log == [{"spec": "greenhouse:affirm", "method": "GET", "url": calls[0], "status": 200, "selected": 1}]


def test_expand_targets_records_a_missing_board_without_inventing_jobs():
    targets, log = ats.expand_targets(["lever:nosuchboard"], lambda url: (404, None), keywords=".", locations=".",
                                      per_board=2)
    assert targets == []
    assert log[0]["status"] == 404


# --------------------------------------------------------------------------- per-job summary

def _get(url):
    return {"method": "GET", "url": url, "action": "continued"}


def _observations(*, requests=None, submit_events=None, http_log=None, response=None, dom=None, evidence=None,
                  ledger_kinds=("dry_run_review",)):
    dom = dom if dom is not None else [{"key": "name", "required": True}, {"key": "email", "required": True},
                                       {"key": "cards[x][field3]", "required": True},
                                       {"key": "urls[GitHub]", "required": False}]
    browser = {"requests": requests if requests is not None else [_get(LEVER_PAGE)], "dom_fields": dom,
               "submit_events": submit_events or [], "non_get_attempts": 0, "aborted": 0}
    browser.update(evidence or {})
    form = {"metadata": {"browser_evidence": browser}}
    return {"response": {"json": {"status": "needs_review", "submitted": False, "form": form, **(response or {})}},
            "ledger": {"validation": {"form": form}, "evidence": [{"kind": kind} for kind in ledger_kinds]},
            "http_log": http_log if http_log is not None else [
                {"method": "GET", "host": "api.lever.co", "path": f"/v0/postings/waveapps/{LEVER_ID}",
                 "local_api": False},
                {"method": "POST", "host": "127.0.0.1", "path": "/api/applications/1/apply", "local_api": True}],
            "settings": {"application_mode": "dry_run", "allow_live_submission": False},
            "materials": [{"kind": "resume", "status": "approved", "truth_guard": [], "claims_hits": []}]}


def _report(**mapping):
    field_mapping = {"planned": {"name": "x", "email": "y"}, "needs_review": [{"key": "cards[x][field3]"}],
                     "form_controls": 4}
    field_mapping.update(mapping)
    return {"index": 1, "outcome": "pass", "posting": {"platform": "lever", "board": "waveapps", "id": LEVER_ID},
            "field_mapping": field_mapping, "checks": {}, "failed_checks": []}


def test_clean_read_only_run_proves_zero_submits_and_zero_non_get():
    job = ats.job_summary(_report(), _observations())
    assert job["evidence"].startswith("provisional")
    assert (job["fields_seen"], job["planned"], job["needs_review"]) == (4, 2, 1)
    assert job["unresolved_required"] == ["cards[x][field3]"]
    assert job["required_not_planned_nor_reviewed"] == []
    assert (job["readback_verified"], job["readback_exercised"]) == (0, False)
    assert job["false_clean"] == []
    proof = job["proof"]
    assert (proof["live_submits"], proof["non_get_requests_sent"], proof["ok"]) == (0, 0, True)
    assert proof["app_non_get_sent"] == 0  # the runner's own POST to the loopback API is not an employer request


def test_aborted_beacons_and_refused_page_submit_are_recorded_but_not_sent():
    beacon = {"method": "POST", "url": "https://api.hcaptcha.com/checksiteconfig", "action": "aborted"}
    linkedin = {"via": "form.submit()", "action": "https://www.linkedin.com/talentwidgets/apply-with-linkedin"}
    job = ats.job_summary(_report(), _observations(requests=[_get(LEVER_PAGE), beacon], submit_events=[linkedin],
                                                   evidence={"non_get_attempts": 1, "aborted": 1}))
    proof = job["proof"]
    assert (proof["browser_non_get_attempted"], proof["browser_non_get_aborted"], proof["browser_non_get_sent"]) == (1, 1, 0)
    assert proof["page_submit_attempts_refused"] == [linkedin]
    assert proof["ok"] is True


def test_a_non_get_that_left_the_browser_to_an_apply_endpoint_fails_the_proof():
    post = {"method": "POST", "url": LEVER_PAGE, "action": "continued"}
    job = ats.job_summary(_report(), _observations(requests=[_get(LEVER_PAGE), post],
                                                   evidence={"non_get_attempts": 1, "aborted": 0}))
    proof = job["proof"]
    assert proof["browser_non_get_sent"] == 1
    assert proof["apply_endpoint_non_get_sent"] == 1
    assert proof["live_submits"] == 1
    assert proof["ok"] is False


def test_router_counters_catch_a_sent_non_get_missing_from_a_truncated_request_list():
    job = ats.job_summary(_report(), _observations(evidence={"non_get_attempts": 3, "aborted": 2,
                                                             "requests_truncated": True}))
    assert job["proof"]["browser_non_get_sent"] == 1
    assert job["proof"]["ok"] is False


def test_app_side_post_to_greenhouse_apply_is_counted_as_a_live_submit():
    http_log = [{"method": "POST", "host": "boards-api.greenhouse.io", "path": "/v1/boards/affirm/jobs/7821078003",
                 "local_api": False}]
    job = ats.job_summary(_report(), _observations(http_log=http_log))
    assert job["proof"]["app_non_get_sent"] == 1
    assert job["proof"]["live_submits"] == 1
    assert job["proof"]["ok"] is False


def test_submitted_flag_or_submission_ledger_row_counts_as_a_live_submit():
    job = ats.job_summary(_report(), _observations(response={"submitted": True}, ledger_kinds=("submission",)))
    assert job["proof"]["live_submits"] == 2
    assert job["proof"]["ok"] is False


def test_false_clean_when_a_filled_field_is_not_rendered_on_the_form():
    """Real shape from the 2026-11-26 Lever batch: dry_run_complete listed consent[marketing] as filled,
    while the hosted form never rendered it (the app's own api_fields_not_rendered said so)."""
    response = {"status": "dry_run_complete", "filled": {"name": "x", "email": "y", "consent[marketing]": "1"}}
    report = _report(planned={"name": "x", "email": "y", "consent[marketing]": "1"},
                     needs_review=[{"key": "cards[x][field3]"}])
    obs = _observations(response=response)
    obs["response"]["json"]["form"]["metadata"]["api_fields_not_rendered"] = ["consent[marketing]"]
    job = ats.job_summary(report, obs)
    assert {"key": "consent[marketing]", "problem": "reported filled but the field is not rendered on the hosted form"} \
        in job["false_clean"]
    assert job["planned_not_rendered"] == ["consent[marketing]"]


def test_planned_but_not_rendered_without_a_filled_claim_is_reported_but_not_false_clean():
    """Greenhouse resume_text: planned from the API schema, absent from the DOM, run stopped for review."""
    report = _report(planned={"name": "x", "email": "y", "resume_text": "text"})
    obs = _observations()
    obs["response"]["json"]["form"]["metadata"]["api_fields_not_rendered"] = ["cover_letter_text", "resume_text"]
    job = ats.job_summary(report, obs)
    assert job["planned_not_rendered"] == ["resume_text"]
    assert job["false_clean"] == []


def test_rate_limit_is_reported_as_blocked_not_closed_or_empty():
    closed = ats.job_summary({"index": 4, "outcome": "skipped_closed", "error": "not open (HTTP 429)",
                              "posting": {"platform": "greenhouse"}}, None)
    assert closed["blocked"] == "HTTP 429"
    refused = ats.job_summary({"index": 5, "outcome": "skipped_closed", "error": "not open (HTTP 406)",
                               "posting": {"platform": "greenhouse"}}, None)
    assert refused["blocked"] == "HTTP 406"  # Stripe's board API refused the declared user agent
    report = {**_report(), "checks": {"api_completed_cleanly": {"ok": False, "detail": {
        "status": "deferred", "detail": "http_429: HTTP 429 is not a definitive answer"}}}}
    assert ats.job_summary(report, _observations(dom=[]))["blocked"] == "HTTP 429"
    assert ats.job_summary(_report(), _observations())["blocked"] is None
    summary = ats.batch_summary([closed], {})
    assert summary["blocked_jobs"] == {"4": "HTTP 429"}


def test_false_clean_when_complete_run_skipped_a_required_field():
    response = {"status": "dry_run_complete", "filled": {"name": "x", "email": "y"}}
    job = ats.job_summary(_report(needs_review=[]), _observations(response=response))
    assert job["required_not_planned_nor_reviewed"] == ["cards[x][field3]"]
    assert any("neither planned nor sent to review" in item["problem"] for item in job["false_clean"])


def test_readback_counts_only_matched_readbacks():
    readbacks = [{"key": "name", "matched": True}, {"key": "email", "matched": False}]
    job = ats.job_summary(_report(), _observations(evidence={"readbacks": readbacks}))
    assert (job["readback_verified"], job["readback_exercised"]) == (1, True)


def test_truth_guard_and_unbacked_answers_are_surfaced():
    obs = _observations()
    obs["materials"] = [{"kind": "resume", "status": "approved", "truth_guard": ["unsupported claim: PhD"],
                         "claims_hits": [{"claim": "PhD"}]}]
    report = _report()
    report["checks"] = {"planned_answers_backed_by_verified_facts": {"ok": False, "detail": {"unbacked": [
        {"key": "q1", "label": "Eligible?", "value": "Yes (PR)", "problem": "planned value differs from the resolved answer"}]}}}
    guard = ats.job_summary(report, obs)["truth_guard"]
    assert guard["claims_to_avoid_hits"] == 1
    assert guard["material_findings"][0]["issues"] == ["unsupported claim: PhD"]
    assert guard["unbacked_planned_answers"][0]["key"] == "q1"


def test_skipped_posting_without_observations_still_reports_a_zero_proof():
    job = ats.job_summary({"index": 2, "outcome": "skipped_closed", "posting": {"platform": "lever"}}, None)
    assert job["proof"]["live_submits"] == 0
    assert job["proof"]["browser_evidence"] is False
    assert job["form_rendered"] is False


# --------------------------------------------------------------------------- batch + CLI

def test_batch_summary_totals_and_markdown_label():
    clean = ats.job_summary(_report(), _observations())
    bad = ats.job_summary({**_report(), "index": 2}, _observations(response={"submitted": True}))
    summary = ats.batch_summary([clean, bad], {})
    assert (summary["live_submits"], summary["proof_ok"], summary["ran"]) == (1, False, 2)
    text = ats.summary_markdown(summary, [clean, bad])
    assert "evidence provisional" in text
    assert "live_submits=1" in text


def test_main_refuses_ci_short_pause_and_repo_output(tmp_path, monkeypatch):
    monkeypatch.setenv("CI", "true")
    with pytest.raises(SystemExit, match="never runs in CI"):
        ats.main([GH_URL, "--out", str(tmp_path)])
    monkeypatch.delenv("CI")
    with pytest.raises(SystemExit, match="at least 20"):
        ats.main([GH_URL, "--out", str(tmp_path), "--pause", "1"])
    with pytest.raises(SystemExit, match="inside the repository"):
        ats.main([GH_URL, "--out", str(V2 / "out-test")])


def test_main_runs_the_existing_batch_in_live_mode_and_writes_reports(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    seen = {}

    def fake_run_batch(targets, inputs, out, *, mode, pause, timeout):
        seen.update(targets=targets, mode=mode, pause=pause, profile=inputs["profile"])
        run_dir = out / "run-01-greenhouse-affirm"
        run_dir.mkdir()
        (run_dir / "observations.json").write_text(json.dumps(_observations()), encoding="utf-8")
        return [{**_report(), "run_dir": str(run_dir)}], None

    code = ats.main([GH_URL, "--out", str(tmp_path), "--pause", "20"], fetch_json=lambda url: (500, None),
                    run_batch=fake_run_batch)
    assert code == 0
    assert seen["mode"] == "live"
    assert seen["targets"][0]["id"] == "7821078003"
    assert seen["profile"].endswith("rehearsal-profile.yaml")  # the made-up identity by default
    job = json.loads((tmp_path / "job-01.json").read_text(encoding="utf-8"))
    assert job["proof"]["ok"] is True
    summary = json.loads((tmp_path / "ats-summary.json").read_text(encoding="utf-8"))
    assert summary["evidence"].startswith("provisional")
    assert (summary["live_submits"], summary["non_get_requests_sent"]) == (0, 0)


def test_main_exit_status_is_nonzero_on_a_false_clean(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)

    def fake_run_batch(targets, inputs, out, *, mode, pause, timeout):
        run_dir = out / "run-01"
        run_dir.mkdir()
        obs = _observations(response={"status": "dry_run_complete", "filled": {"name": "x"}})
        (run_dir / "observations.json").write_text(json.dumps(obs), encoding="utf-8")
        return [{**_report(needs_review=[]), "run_dir": str(run_dir)}], None

    assert ats.main([GH_URL, "--out", str(tmp_path)], run_batch=fake_run_batch) == 1


def test_discover_only_stops_before_any_run(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)

    def never(*args, **kwargs):
        raise AssertionError("discover-only must not run the batch")

    assert ats.main([GH_URL, "--out", str(tmp_path), "--discover-only"], run_batch=never) == 0
    assert json.loads((tmp_path / "targets.json").read_text(encoding="utf-8"))["targets"][0]["board"] == "affirm"
