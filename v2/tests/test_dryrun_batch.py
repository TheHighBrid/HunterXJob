"""Dry-run batch (scripts/dryrun_batch.py) over Greenhouse, Lever and Ashby: runner and checks, offline.

Nothing here contacts a real site: board APIs are an ``httpx.MockTransport``, the runs are
fakes, and the identity is made up. ``--live`` refuses to start under CI; the ``gate1`` CI
job runs ``--rehearse`` (tests/test_dryrun_batch_rehearsal.py) against loopback fixtures.
"""
import importlib
import json
import sys
from pathlib import Path

import httpx
import pytest

V2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2 / "scripts"))
gate2_checks = importlib.import_module("gate2_checks")
checks = importlib.import_module("batch_checks")
batch = importlib.import_module("dryrun_batch")

ASHBY_ID = "6c74631d-0000-4000-8000-000000000001"
LEVER_ID = "da5d7c85-0000-4000-8000-000000000002"
ASHBY_PAGE = f"https://jobs.ashbyhq.com/acme/{ASHBY_ID}/application"
GRAPHQL = "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting"


def _get(url):
    return {"method": "GET", "url": url, "resource_type": "script", "action": "continued"}


def _evidence(**extra):
    evidence = {"requests": [_get(ASHBY_PAGE)], "request_count": 1, "non_get_attempts": 0, "aborted": 0,
                "rewritten_to_get": 0, "submit_events": [], "main_frame_document_requests": 1,
                "main_frame_navigations": [ASHBY_PAGE], "service_workers": "block"}
    evidence.update(extra)
    return evidence


REWRITE = {"method": "POST", "url": GRAPHQL, "resource_type": "fetch", "action": "rewritten_to_get"}
BEACON = {"method": "POST", "url": "https://analytics.example/collect", "resource_type": "beacon", "action": "aborted"}


# --------------------------------------------------------------------------- generalized Gate 2 checks

def test_ashby_graphql_rewrites_are_allowed_only_when_asked_for():
    evidence = _evidence(requests=[_get(ASHBY_PAGE), REWRITE, BEACON], non_get_attempts=2, aborted=1, rewritten_to_get=1)
    assert gate2_checks.evaluate_read_only(evidence, [], "jobs.ashbyhq.com", allow_rewrites=True) == []
    problems = gate2_checks.evaluate_read_only(evidence, [], "jobs.ashbyhq.com")
    assert any("rewritten" in item for item in problems) and any("not aborted" in item for item in problems)


def test_a_rewrite_never_excuses_an_unaborted_write():
    sent = {"method": "POST", "url": GRAPHQL, "resource_type": "fetch", "action": "continued"}
    evidence = _evidence(requests=[_get(ASHBY_PAGE), sent], non_get_attempts=1)
    assert gate2_checks.evaluate_read_only(evidence, [], "jobs.ashbyhq.com", allow_rewrites=True)
    miscounted = _evidence(requests=[_get(ASHBY_PAGE), REWRITE], non_get_attempts=2, rewritten_to_get=1)
    assert any("non-GET attempt" in item for item in
               gate2_checks.evaluate_read_only(miscounted, [], "jobs.ashbyhq.com", allow_rewrites=True))


def test_app_http_may_use_every_platform_host_but_nothing_else():
    log = [{"method": "GET", "host": "api.lever.co", "local_api": False},
           {"method": "GET", "host": "jobs.lever.co", "local_api": False}]
    assert gate2_checks.evaluate_read_only(_evidence(), log, ["api.lever.co", "jobs.lever.co"]) == []
    assert gate2_checks.evaluate_read_only(_evidence(), log, "api.lever.co")


def _data(**extra):
    data = {"job_id": ASHBY_ID, "http_log": [], "response": {"json": {"form": {"metadata": {
        "dom_verified": True, "browser_evidence": {"main_document_status": 200, "dom_fields": [
            {"key": "_systemfield_name"}, {"key": "_systemfield_email"}]}}}}}}
    data.update(extra)
    return data


def test_form_rendered_rule_per_platform():
    ctx = gate2_checks.RunContext(_data(required_dom_key_substrings=["email"]))
    assert gate2_checks.check_form_rendered(ctx)[0]
    ctx = gate2_checks.RunContext(_data())  # Gate 2 default: Greenhouse first_name + email
    assert not gate2_checks.check_form_rendered(ctx)[0]
    ctx = gate2_checks.RunContext(_data(required_dom_key_substrings=["phone"]))
    assert not gate2_checks.check_form_rendered(ctx)[0]


def test_posting_probe_can_carry_the_id_in_the_query():
    probe = {"method": "GET", "host": "jobs.ashbyhq.com", "path": "/api/non-user-graphql",
             "query": f"op=ApiJobPosting&variables=%7B%22jobPostingId%22%3A%22{ASHBY_ID}%22%7D",
             "local_api": False, "status": 200}
    assert gate2_checks.check_posting_live(gate2_checks.RunContext(_data(http_log=[probe], probe_id_in_query=True)))[0]
    assert not gate2_checks.check_posting_live(gate2_checks.RunContext(_data(http_log=[probe])))[0]
    lever = {"method": "GET", "host": "api.lever.co", "path": f"/v0/postings/acme/{LEVER_ID}", "query": "",
             "local_api": False, "status": 200}
    assert gate2_checks.check_posting_live(gate2_checks.RunContext({"job_id": LEVER_ID, "http_log": [lever]}))[0]


def _ledger_ctx(rows, monkeypatch):
    monkeypatch.setattr(gate2_checks, "expected_ledger_payload", lambda ctx, kind: {"kind": kind})
    from app.evidence import payload_hash

    for row in rows:
        if row["kind"] in {"dry_run", "dry_run_review"}:
            row.update(payload_hash=payload_hash({"kind": row["kind"]}), confirmation_text="trace sha256=abc")
    return gate2_checks.RunContext({"ledger": {"evidence": rows}, "trace_sha256": "abc"})


def test_ledger_accepts_material_rows_but_nothing_else_besides_the_one_dry_run_row(monkeypatch):
    def rows(*kinds):
        return [{"kind": kind, "sufficient": False} for kind in kinds]

    ok = rows("materials_draft", "materials_draft", "materials_approved", "materials_rejected", "dry_run_review")
    assert gate2_checks.check_ledger(_ledger_ctx(ok, monkeypatch))[0]
    assert gate2_checks.check_ledger(_ledger_ctx(rows("dry_run_review"), monkeypatch))[0]
    assert not gate2_checks.check_ledger(_ledger_ctx(rows("dry_run_review", "submission"), monkeypatch))[0]
    assert not gate2_checks.check_ledger(_ledger_ctx(rows("dry_run_review", "dry_run_review"), monkeypatch))[0]
    sufficient = rows("materials_approved", "dry_run_review")
    sufficient[0]["sufficient"] = True
    assert not gate2_checks.check_ledger(_ledger_ctx(sufficient, monkeypatch))[0]


# --------------------------------------------------------------------------- one GET per posting

GH = {"platform": "greenhouse", "board": "acme", "id": "123", "company": "Acme"}
LV = {"platform": "lever", "board": "acme", "id": LEVER_ID, "company": "Acme"}
AS = {"platform": "ashby", "board": "acme", "id": ASHBY_ID, "company": "Acme"}


def test_parse_open_posting_per_platform():
    gh = checks.parse_open_posting(GH, 200, {"id": 123, "title": "Fraud Analyst", "location": {"name": "Toronto"},
                                             "content": "&lt;p&gt;Investigate &amp;amp; resolve&lt;/p&gt;"})
    assert gh["url"] == "https://job-boards.greenhouse.io/acme/jobs/123" and gh["description"] == "Investigate & resolve"
    lv = checks.parse_open_posting(LV, 200, {"id": LEVER_ID, "text": "Advisor", "categories": {"location": "Toronto"},
                                             "descriptionPlain": "Help clients"})
    assert lv["url"] == f"https://jobs.lever.co/acme/{LEVER_ID}" and lv["title"] == "Advisor"
    board = {"jobs": [{"id": ASHBY_ID, "title": "Associate", "location": "Toronto", "isListed": True}]}
    assert checks.parse_open_posting(AS, 200, board)["url"] == f"https://jobs.ashbyhq.com/acme/{ASHBY_ID}"
    assert checks.parse_open_posting(AS, 200, {"jobs": [{"id": ASHBY_ID, "isListed": False}]}) is None
    assert checks.parse_open_posting(GH, 404, None) is None
    assert checks.parse_open_posting(GH, 200, {"id": 999}) is None


def _client(*responses):
    calls = []

    def handler(request):
        calls.append(request)
        item = responses[len(calls) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_open_check_is_one_get_with_one_retry_on_transient_errors_only():
    client, calls = _client(httpx.Response(200, json={"id": LEVER_ID, "text": "Advisor"}))
    posting = batch.fetch_open_posting(LV, client=client, sleep=lambda _s: None)
    assert posting["open"] and [(c.method, str(c.url)) for c in calls] == [
        ("GET", f"https://api.lever.co/v0/postings/acme/{LEVER_ID}")]
    client, calls = _client(httpx.Response(503), httpx.Response(200, json={"jobs": [{"id": ASHBY_ID, "title": "A"}]}))
    assert batch.fetch_open_posting(AS, client=client, sleep=lambda _s: None)["open"] and len(calls) == 2
    client, calls = _client(httpx.ConnectError("down"), httpx.ConnectError("down"), httpx.Response(200))
    assert not batch.fetch_open_posting(GH, client=client, sleep=lambda _s: None)["open"] and len(calls) == 2
    client, calls = _client(httpx.Response(404), httpx.Response(200))
    closed = batch.fetch_open_posting(GH, client=client, sleep=lambda _s: None)
    assert not closed["open"] and "404" in closed["reason"] and len(calls) == 1
    assert all(call.method == "GET" for call in calls)


# --------------------------------------------------------------------------- planned answers are backed

ALLOWED = {"first_name": ["Avery"], "email": ["avery@example.com"], "work_authorization_ca": ["Yes"],
           "resume": ["/private/resume.pdf"]}


def _fill(key, value, resolved_key, label="", section="application", resolved_value=None, reason="answer accepted"):
    return {"key": key, "label": label or key, "section": section, "status": "fill", "value": value,
            "resolved_key": resolved_key, "resolved_value": value if resolved_value is None else resolved_value,
            "reason": reason}


def test_backed_answers_pass():
    trace = [_fill("first_name", "Avery", "first_name"), _fill("q1", "Yes", "work_authorization_ca",
                                                                label="Are you legally authorized to work in Canada?"),
             _fill("resume", "/private/resume.pdf", "resume"), {"key": "q2", "status": "review", "label": "Salary?"}]
    assert checks.planned_answer_problems(trace, ALLOWED, decline_policy=False) == []


@pytest.mark.parametrize("item, fragment", [
    (_fill("q", "No", "sponsorship_us"), "not a verified fact"),
    (_fill("first_name", "Avry", "first_name"), "differs from the verified fact"),
    (_fill("first_name", "Avery Q", "first_name", resolved_value="Avery"), "differs from the resolved answer"),
    (_fill("q", "Yes", "work_authorization_ca", label="Are you authorized to work in the United States?"), "us_work"),
    (_fill("q", "Yes", "work_authorization_ca", label="What are your salary expectations?"), "salary"),
    (_fill("q", "Yes", "work_authorization_ca", label="Highest level of education completed"), "education"),
])
def test_unbacked_or_forbidden_answers_are_reported(item, fragment):
    problems = checks.planned_answer_problems([item], ALLOWED, decline_policy=True)
    assert problems and any(fragment in problem["problem"] for problem in problems)


def test_decline_option_needs_the_policy_and_a_voluntary_section():
    item = _fill("gender", "I don't wish to answer", None, section="eeoc", reason=checks.DECLINE_REASON)
    assert checks.planned_answer_problems([item], ALLOWED, decline_policy=True) == []
    assert checks.planned_answer_problems([item], ALLOWED, decline_policy=False)
    assert checks.planned_answer_problems([{**item, "section": "application"}], ALLOWED, decline_policy=True)
    assert checks.planned_answer_problems([{**item, "value": "Female"}], ALLOWED, decline_policy=True)


def test_a_resume_upload_mentioning_a_school_is_not_an_education_answer():
    item = _fill("resume", "/private/resume.pdf", "resume", label="Resume (include education and school)")
    assert checks.planned_answer_problems([item], ALLOWED, decline_policy=False) == []


def test_missing_plan_capture_fails():
    assert not checks.check_planned_answers({})[0]


# --------------------------------------------------------------------------- materials and locks

GUARD = [{"claim": "no PhD", "pattern": r"\bPh\.?D\b"}]


def test_claims_to_avoid_scan():
    assert checks.claims_hits("Holds a PhD in finance", GUARD) == ["no PhD"]
    assert checks.claims_hits("Fraud analyst", GUARD) == []
    clean = {"materials": [{"kind": "resume", "claims_hits": [], "truth_guard": []}], "guardrails_loaded": 1}
    assert checks.check_materials_claims(clean)[0]
    assert not checks.check_materials_claims({**clean, "guardrails_loaded": 0})[0]
    assert not checks.check_materials_claims({"materials": [], "guardrails_loaded": 1})[0]
    hit = {"materials": [{"kind": "resume", "claims_hits": ["no PhD"], "truth_guard": []}], "guardrails_loaded": 1}
    assert not checks.check_materials_claims(hit)[0]
    lie = {"materials": [{"kind": "resume", "claims_hits": [], "truth_guard": ["unverified number"]}], "guardrails_loaded": 1}
    assert not checks.check_materials_claims(lie)[0]


def test_live_submission_lock_check():
    data = {"settings": {"application_mode": "dry_run", "allow_live_submission": False},
            "response": {"json": {"submitted": False}}, "ledger": {"evidence": [{"kind": "dry_run_review"}]}}
    assert checks.check_live_submission_locked(data)[0]
    assert not checks.check_live_submission_locked({**data, "settings": {"application_mode": "live",
                                                                         "allow_live_submission": False}})[0]
    assert not checks.check_live_submission_locked({**data, "ledger": {"evidence": [{"kind": "submission"}]}})[0]
    assert not checks.check_live_submission_locked({**data, "response": {"json": {"submitted": True}}})[0]


def test_the_page_inspector_has_no_input_apis():
    assert checks.check_inspector_has_no_input_apis({})[0]


def _checks(ok=True, **override):
    names = [*gate2_checks.SAFETY_CHECKS, *checks.EXTRA_SAFETY_CHECKS, "posting_confirmed_open",
             "real_form_rendered_without_challenge"]
    result = {name: {"ok": ok} for name in names}
    result.update({name: {"ok": value} for name, value in override.items()})
    return result


def test_batch_outcome_and_safety():
    assert checks.batch_outcome(_checks(), None) == "pass"
    blocked = _checks(real_form_rendered_without_challenge=False, posting_confirmed_open=False)
    assert checks.batch_outcome(blocked, "anti_bot_challenge") == "blocked_by_bot_check"
    assert checks.safety_ok(blocked)
    unsafe = _checks(planned_answers_backed_by_verified_facts=False)
    assert checks.batch_outcome(unsafe, "anti_bot_challenge") == "fail" and not checks.safety_ok(unsafe)
    assert checks.batch_outcome({}, None) == "fail" and not checks.safety_ok({})


# --------------------------------------------------------------------------- summary

def test_rows_and_summary():
    report = {"index": 1, "outcome": "pass", "challenge": None, "checks": _checks(),
              "posting": {"company": "Acme", "title": "Analyst", "platform": "ashby", "url": "u"},
              "field_mapping": {"planned_count": 6, "needs_review_count": 2, "needs_review_required": ["q1"],
                                "needs_review": [{"key": "q1", "label": "What are your salary expectations?"},
                                                 {"key": "q2", "label": "Are you authorized to work in the US?"}]}}
    row = checks.run_row(report)
    assert row["planned_pct"] == 75.0 and row["safety_ok"] and not row["bot_check"]
    assert set(row["top_review_reasons"]) == {"salary expectations", "US work authorization"}
    skipped = {"index": 2, "outcome": "skipped_closed", "posting": {}, "checks": {}}
    summary = checks.summarize([row, checks.run_row(skipped)])
    assert summary["runs"] == 1 and summary["skipped_closed"] == 1 and summary["safety_pass_rate"] == 1.0
    assert summary["average_planned_pct"] == 75.0 and summary["review_fields_by_bucket"]["salary expectations"] == 1
    assert checks.review_bucket("Something unusual") == "other employer-specific question"


# --------------------------------------------------------------------------- orchestration

def _result(outcome="pass", reason=None, detail=None, crashed=False):
    result = {"outcome": outcome, "challenge": None, "checks": _checks(outcome == "pass"), "plan_trace": []}
    result["checks"]["api_completed_cleanly"] = {"ok": outcome == "pass", "detail": {"reason": reason, "detail": detail}}
    if crashed:
        result = {"outcome": "fail", "crashed": True, "error": "run process exited 1 without a result", "checks": {}}
    return result


def _run(tmp_path, results, targets=None, fetch=None):
    calls, sleeps = [], []

    def runner(run_dir, mode, posting, inputs, timeout):
        calls.append(run_dir.name)
        return results.pop(0)

    targets = targets or [GH, LV, AS]
    fetch = fetch or (lambda target: {**target, "open": True, "title": "T", "url": "u", "location": ""})
    reports, stopped = batch.run_batch(targets, {}, tmp_path, mode="live", pause=30, timeout=1, fetch=fetch,
                                       runner=runner, sleep=sleeps.append)
    return reports, stopped, calls, sleeps


def test_sequential_runs_with_a_pause_between_them(tmp_path):
    reports, stopped, calls, sleeps = _run(tmp_path, [_result(), _result(), _result()])
    assert stopped is None and len(calls) == 3 and sleeps == [30, 30]
    assert [report["outcome"] for report in reports] == ["pass"] * 3
    assert sorted(path.name for path in tmp_path.glob("run-*-report.json")) == [
        "run-01-report.json", "run-02-report.json", "run-03-report.json"]


def test_one_retry_only_for_a_transient_network_error(tmp_path):
    transient = _result("fail", "form_fetch_failed", "browser inspection failed: Error: net::ERR_CONNECTION_RESET")
    reports, _, calls, _ = _run(tmp_path, [transient, transient], targets=[GH])
    assert len(calls) == 2 and calls[1].endswith("-retry") and len(reports[0]["attempts"]) == 2
    reports, _, calls, _ = _run(tmp_path, [_result("fail", "ambiguous_question", "check failed")], targets=[GH])
    assert len(calls) == 1 and len(reports[0]["attempts"]) == 1


def test_a_closed_posting_is_skipped_and_never_loaded(tmp_path):
    def fetch(target):
        return {**target, "open": target is not LV, "reason": "not open (HTTP 404)", "title": "T", "url": "u"}

    reports, _, calls, _ = _run(tmp_path, [_result(), _result()], fetch=fetch)
    assert len(calls) == 2 and reports[1]["outcome"] == "skipped_closed"


def test_a_crashed_run_stops_the_batch(tmp_path):
    reports, stopped, calls, _ = _run(tmp_path, [_result(), _result(crashed=True), _result()])
    assert len(calls) == 2 and "crashed" in stopped and len(reports) == 2


def test_live_mode_guards(monkeypatch, tmp_path):
    monkeypatch.setenv("CI", "true")
    with pytest.raises(SystemExit):
        batch.main(["--live", "--targets", "t", "--profile", "p", "--answers", "a", "--guardrails", "g",
                    "--out", str(tmp_path)])
    monkeypatch.delenv("CI")
    with pytest.raises(SystemExit):
        batch.main(["--live", "--targets", "t", "--profile", "p", "--answers", "a", "--guardrails", "g",
                    "--out", str(tmp_path), "--pause", "5"])
    with pytest.raises(SystemExit, match="inside the repository"):
        batch.refuse_repo_output(V2 / "gate-artifacts" / "batch")
    batch.refuse_repo_output(tmp_path)


def test_targets_are_validated(tmp_path):
    path = tmp_path / "targets.json"
    path.write_text(json.dumps([GH, GH]), encoding="utf-8")
    with pytest.raises(SystemExit, match="duplicate"):
        batch.load_targets(path)
    path.write_text(json.dumps([{**GH, "platform": "workday"}]), encoding="utf-8")
    with pytest.raises(SystemExit, match="bad target"):
        batch.load_targets(path)
    path.write_text(json.dumps([GH, LV, AS]), encoding="utf-8")
    assert len(batch.load_targets(path)) == 3


def test_inputs_need_answers_with_values_and_guardrail_patterns(tmp_path):
    profile, answers, guard = tmp_path / "p.yaml", tmp_path / "a.yaml", tmp_path / "g.yaml"
    profile.write_text("verified: true\n", encoding="utf-8")
    answers.write_text("answers:\n  - key: voluntary_self_identification\n    value: decline\n", encoding="utf-8")
    guard.write_text("claims_to_avoid: []\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="claims_to_avoid"):
        batch.load_inputs(profile, answers, guard)
    guard.write_text("claims_to_avoid:\n  - claim: x\n    pattern: y\n", encoding="utf-8")
    assert batch.load_inputs(profile, answers, guard)["answers"][0]["value"] == "decline"
    answers.write_text("answers:\n  - key: only_a_key\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="key and value"):
        batch.load_inputs(profile, answers, guard)


def test_transient_detection():
    assert batch.transient_failure(_result("fail", "form_fetch_failed", "httpx.ConnectTimeout"))
    assert not batch.transient_failure(_result("fail", "liveness_gone", "posting closed"))
    assert not batch.transient_failure(_result("pass"))
    assert not batch.transient_failure(_result(crashed=True))


def test_trace_must_mention_the_form_page_of_its_own_platform(tmp_path):
    import zipfile

    gate1_checks = importlib.import_module("gate1_checks")
    path = tmp_path / "trace.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("trace.trace", "{}")
        archive.writestr("trace.network", f'{{"url": "{ASHBY_PAGE}"}}')
    marker = checks.trace_marker("ashby", "acme", ASHBY_ID)
    assert marker == f"/acme/{ASHBY_ID}/application"
    assert gate1_checks.inspect_trace_zip(path, marker)["ok"]
    assert not gate1_checks.inspect_trace_zip(path)["ok"]  # Gate 1/2 default: the Greenhouse embed page
    assert checks.trace_marker("lever", "acme", LEVER_ID) == f"/acme/{LEVER_ID}/apply"
    assert checks.trace_marker("greenhouse", "acme", "1") == "/embed/job_app"


def test_the_postings_own_title_is_not_a_claim_about_the_applicant():
    guard = [{"claim": "Management title", "pattern": r"\bmanager\b"}]
    letter = "I am applying for the Manager, Fraud Operations position at Acme."
    assert checks.claims_hits(letter, guard) == ["Management title"]
    assert checks.claims_hits(letter, guard, ("Manager, Fraud Operations", "Acme")) == []
    assert checks.claims_hits(letter + " I was a manager.", guard, ("Manager, Fraud Operations",)) == ["Management title"]


def test_material_texts_reads_the_run_database_read_only(tmp_path):
    import sqlite3

    database = tmp_path / "run.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE application_materials (kind TEXT, text TEXT, version INTEGER)")
        connection.executemany("INSERT INTO application_materials VALUES (?, ?, ?)",
                               [("resume", "old", 1), ("resume", "new", 2), ("cover_letter", "letter", 1)])
    assert checks.material_texts(database) == {"resume": "new", "cover_letter": "letter"}
    assert checks.material_texts(tmp_path / "missing.db") == {}
