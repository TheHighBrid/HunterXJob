"""Gate 2 (docs/RECOVERY_CONTRACT.md): runner and checks, offline.

Nothing here contacts a real site: the boards API is an ``httpx.MockTransport``
and the page is a stand-in. The live run is local/manual only
(``python scripts/gate2.py --live ...``) and refuses to start under CI; the
``gate1`` CI job runs ``--rehearse`` against Gate 1's loopback fixtures.
"""
import asyncio
import importlib
import json
import sys
import types
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app import greenhouse_browser
from app.evidence import payload_hash
from app.greenhouse_browser import reconcile
from app.greenhouse_form import parse_greenhouse_payload
from app.models import SubmissionEvidence
from app.pipeline import browser_ledger_entry

V2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2 / "scripts"))
checks = importlib.import_module("gate2_checks")
runner = importlib.import_module("gate2")
gate1_tests = importlib.import_module("test_recovery_gate1")

FORM = "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=1"
LISTING = {"jobs": [
    {"id": 1, "title": " Backend Engineer ", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
     "location": {"name": "Remote, Canada"}, "updated_at": "2026-10-01"},
    {"id": 2, "title": "Designer", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/2",
     "location": {"name": "Berlin"}},
    {"id": 3, "title": "No URL"},
]}


def _get(url, host="job-boards.cdn.greenhouse.io"):
    return {"method": "GET", "url": url or f"https://{host}/x.js", "resource_type": "script", "action": "continued"}


def _clean(**extra):
    evidence = {"requests": [_get(FORM), _get(None)], "request_count": 2, "non_get_attempts": 0, "aborted": 0,
                "rewritten_to_get": 0, "submit_events": [], "main_frame_document_requests": 1,
                "main_frame_navigations": [FORM], "service_workers": "block"}
    evidence.update(extra)
    return evidence


API_LOG = [{"method": "GET", "host": "boards-api.greenhouse.io", "path": "/v1/boards/acme/jobs/1", "query": "",
            "local_api": False, "status": 200}]
BEACON = {"method": "POST", "url": "https://c.spl.greenhouse.io/com.snowplowanalytics.snowplow/tp2",
          "resource_type": "fetch", "action": "aborted"}


# --------------------------------------------------------------------------- posting choice

def test_choose_posting_by_id_or_location():
    assert checks.choose_posting(LISTING, "1") == {
        "id": "1", "title": "Backend Engineer", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
        "location": "Remote, Canada", "updated_at": "2026-10-01"}
    assert checks.choose_posting(LISTING, location_hint="berlin")["id"] == "2"
    with pytest.raises(ValueError):
        checks.choose_posting(LISTING, "3")  # no absolute_url: not a usable posting
    with pytest.raises(ValueError):
        checks.choose_posting({"jobs": []})


def _client(*responses):
    calls = []

    def handler(request):
        calls.append(request)
        item = responses[len(calls) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)


def test_fetch_posting_confirms_the_posting_is_open_with_one_get(no_sleep):
    client, calls = _client(httpx.Response(200, json=LISTING["jobs"][0]))
    posting = runner.fetch_posting("acme", "1", client=client)
    assert [(call.method, str(call.url)) for call in calls] == [
        ("GET", "https://boards-api.greenhouse.io/v1/boards/acme/jobs/1")]
    assert posting["id"] == "1" and posting["board"] == "acme" and posting["verified_with"].startswith("GET ")


def test_fetch_posting_retries_once_on_a_transient_error_only(no_sleep):
    client, calls = _client(httpx.Response(503), httpx.Response(200, json=LISTING["jobs"][0]))
    assert runner.fetch_posting("acme", "1", client=client)["id"] == "1" and len(calls) == 2
    client, calls = _client(httpx.ConnectError("down"), httpx.ConnectError("down"), httpx.Response(200))
    with pytest.raises(httpx.ConnectError):
        runner.fetch_posting("acme", "1", client=client)
    assert len(calls) == 2
    client, calls = _client(httpx.Response(503), httpx.Response(503), httpx.Response(200))
    with pytest.raises(SystemExit, match="HTTP 503"):
        runner.fetch_posting("acme", "1", client=client)
    assert len(calls) == 2


def test_a_closed_posting_is_not_retried(no_sleep):
    client, calls = _client(httpx.Response(404), httpx.Response(200))
    with pytest.raises(SystemExit, match="not open"):
        runner.fetch_posting("acme", "1", client=client)
    assert len(calls) == 1


def test_live_mode_refuses_to_run_in_ci(monkeypatch, tmp_path):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(runner, "fetch_posting", lambda *_a, **_k: pytest.fail("contacted the boards API"))
    with pytest.raises(SystemExit) as stop:
        runner.main(["--live", "--board", "acme", "--job-id", "1", "--out", str(tmp_path)])
    assert stop.value.code == 2 and not list(tmp_path.iterdir())


def test_live_mode_needs_a_board_and_a_job(monkeypatch, tmp_path):
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(SystemExit):
        runner.main(["--live", "--board", "acme", "--out", str(tmp_path)])


# --------------------------------------------------------------------------- read-only verdict on a real page

def test_a_clean_get_only_session_passes():
    assert checks.evaluate_read_only(_clean(), API_LOG, "boards-api.greenhouse.io") == []


def test_an_aborted_analytics_beacon_is_recorded_but_allowed():
    evidence = _clean(requests=[_get(FORM), BEACON], non_get_attempts=1, aborted=1)
    assert checks.evaluate_read_only(evidence, API_LOG, "boards-api.greenhouse.io") == []
    counts = checks.request_counts({**evidence, "request_count": 2})
    assert counts["by_method"] == {"GET": 1, "POST": 1} and counts["by_action"] == {"continued": 1, "aborted": 1}
    assert counts["by_host"] == {"job-boards.greenhouse.io": 1, "c.spl.greenhouse.io": 1}
    assert counts["non_get_aborted"] == 1 and counts["main_frame_document_requests"] == 1


@pytest.mark.parametrize("extra, fragment", [
    ({"requests": [_get(FORM), {**BEACON, "action": "continued"}], "non_get_attempts": 1, "aborted": 0},
     "non-GET request was not aborted"),
    ({"non_get_attempts": 2, "aborted": 1}, "2 non-GET attempt(s) but only 1 aborted"),
    ({"rewritten_to_get": 1}, "rewritten"),
    ({"submit_events": [{"via": "requestSubmit"}]}, "form submission"),
    ({"main_frame_document_requests": 2}, "loaded 2 document(s)"),
    ({"main_frame_document_requests": 0}, "loaded 0 document(s)"),
    ({"main_frame_navigations": []}, "no main-frame navigation"),
    ({"main_frame_navigations": [FORM, "https://job-boards.greenhouse.io/embed/job_app/confirmation"]}, "left the form"),
    ({"main_frame_navigations": [FORM, "https://evil.example/embed/job_app?for=acme&token=1"]}, "left the form"),
    ({"service_workers": None}, "service workers"),
])
def test_anything_that_could_have_written_fails(extra, fragment):
    problems = checks.evaluate_read_only(_clean(**extra), API_LOG, "boards-api.greenhouse.io")
    assert problems and fragment in problems[0]


def test_same_document_history_updates_on_the_form_are_allowed():
    """The live GitLab form fired framenavigated 3 times for one document load (React history updates)."""
    evidence = _clean(main_frame_navigations=[FORM, FORM, FORM + "&gh_src=x"])
    assert checks.evaluate_read_only(evidence, API_LOG, "boards-api.greenhouse.io") == []


def test_app_http_must_be_get_to_the_boards_api_only():
    log = [*API_LOG, {"method": "POST", "host": "127.0.0.1", "local_api": True},
           {"method": "POST", "host": "boards-api.greenhouse.io", "local_api": False},
           {"method": "GET", "host": "job-boards.greenhouse.io", "local_api": False}]
    problems = checks.evaluate_read_only(_clean(), log, "boards-api.greenhouse.io")
    assert problems == ["app sent POST to boards-api.greenhouse.io",
                        "app HTTP request left the boards API host: job-boards.greenhouse.io"]


def test_missing_browser_evidence_fails():
    assert "no browser evidence" in checks.evaluate_read_only({}, [], "boards-api.greenhouse.io")[0]


# --------------------------------------------------------------------------- challenge and outcome

def _ctx(evidence=None, reason="ambiguous_question", handoff=None):
    form = {"metadata": {"browser_evidence": evidence or {}}, "handoff": handoff}
    return checks.RunContext({"response": {"json": {"reason": reason, "form": form}}})


@pytest.mark.parametrize("evidence, reason, handoff, expected", [
    ({"main_document_status": 200}, "ambiguous_question", None, None),
    ({"main_document_status": 403}, "ambiguous_question", None, "http_403"),
    ({"main_document_status": 429}, "ambiguous_question", None, "http_429"),
    ({"main_document_status": 200}, "anti_bot_challenge", None, "anti_bot_challenge"),
    ({}, "needs_review", "anti_bot_challenge", "anti_bot_challenge"),
])
def test_challenge_detection(evidence, reason, handoff, expected):
    assert checks.challenge_detected(_ctx(evidence, reason, handoff)) == expected


def _all(ok=True, **override):
    names = [name for name, _check in checks.CHECKS]
    result = {name: {"ok": ok} for name in names}
    result.update({name: {"ok": value} for name, value in override.items()})
    return result


def test_outcome():
    assert checks.outcome(_all(), None) == "pass"
    assert checks.outcome(_all(evidence_persisted_in_ledger=False), None) == "fail"
    # A bot check is a valid finding only if the run still stopped safely.
    assert checks.outcome(_all(real_form_rendered_without_challenge=False), "http_403") == "blocked_by_bot_check"
    assert checks.outcome(_all(zero_submit_and_zero_non_get_sent=False), "http_403") == "fail"


def test_report_is_green_only_for_a_live_pass():
    run = {"outcome": "pass", "checks": _all()}
    assert checks.build_report(run, posting={}, mode="live")["proven"]["one_real_public_greenhouse_posting_dry_run"]
    rehearsal = checks.build_report(run, posting={}, mode="rehearse")
    assert not rehearsal["proven"]["one_real_public_greenhouse_posting_dry_run"]
    assert not any(checks.build_report(run, posting={}, mode="live")["not_proven"].values())
    assert set(checks.NOT_PROVEN) == set(checks.NOT_PROVEN_NOTES)


def test_fake_identity_is_fake_and_plans_no_upload():
    assert checks.FAKE_IDENTITY == {"first_name": "Test", "last_name": "Applicant", "email": "test@example.com",
                                    "phone": "555-0100"}
    assert not any("resume" in key or "cv" in key for key in checks.FAKE_IDENTITY)


def test_field_mapping_summary():
    validation = {"planned": {"first_name": "Test"}, "blockers": ["legal_answer_missing"], "blocked_fields": [
        {"key": "q1", "section": "application", "required": True, "reason": "no answer stored"},
        {"key": "gender", "section": "eeoc", "required": False, "reason": "no answer stored"}]}
    result = {"form": {"fields_total": 3, "fields_required": 2, "sections": {"application": 2, "eeoc": 1},
                       "metadata": {"api_fields_not_rendered": ["race"], "dom_only_required_fields": [],
                                    "submit_boundary": "captcha_detected"}}}
    evidence = {"dom_fields": [{"key": "first_name", "type": "text", "required": True},
                               {"key": "resume", "type": "file", "required": True}, {"key": "q1", "tag": "textarea"}]}
    mapping = checks.field_mapping(result, validation, evidence)
    assert mapping["dom_fields_detected"] == 3 and mapping["dom_required_fields"] == 2
    assert mapping["dom_field_kinds"] == {"text": 1, "file": 1, "textarea": 1}
    assert mapping["planned_count"] == 1 and mapping["needs_review_count"] == 2
    assert mapping["needs_review_required"] == ["q1"]
    assert mapping["needs_review_by_section"] == {"application": 1, "eeoc": 1}
    assert mapping["submit_boundary"] == "captcha_detected" and mapping["api_fields_not_rendered"] == ["race"]


# --------------------------------------------------------------------------- production changes the live form exposed

def test_a_review_stopped_dry_run_still_binds_its_browser_evidence_in_the_ledger():
    """Real forms usually stop for review; the trace hash must reach the ledger for them too."""
    db, application = gate1_tests._seeded_dry_run()
    evidence = gate1_tests._evidence(screenshot={"path": "/t/x.png", "sha256": "cd" * 32, "bytes": 3, "exists": True})
    form = reconcile(parse_greenhouse_payload(gate1_tests.load_fixture_json("d2l_7696196.json"), gate1_tests.D2L),
                     gate1_tests._snapshot(evidence))
    form.handoff = "anti_bot_challenge"
    result = gate1_tests._dry_run(db, application, form)
    assert result["status"] == "needs_review" and result["submitted"] is False
    row = db.execute(select(SubmissionEvidence)).scalars().one()
    assert row.kind == "dry_run_review" and row.sufficient is False
    assert f"browser trace sha256={'ab' * 32}" in row.confirmation_text and "not clicked" in row.confirmation_text
    entry = browser_ledger_entry(result["form"])
    assert entry["screenshot_sha256"] == "cd" * 32
    assert row.payload_hash == payload_hash({"planned": {}, "needs_review": [], "blockers": ["anti_bot_challenge"],
                                             "form_source": "greenhouse_api", "browser": entry})
    assert application.stage != "submitted"


class _Page:
    def __init__(self, fail_goto=False, fail_shot=False):
        self.fail_goto, self.fail_shot, self.shots = fail_goto, fail_shot, []
        self.main_frame = object()
        self.frames = ()

    def on(self, *_args):
        return None

    async def goto(self, url, **_kwargs):
        if self.fail_goto:
            raise RuntimeError("Timeout 45000ms exceeded")
        return types.SimpleNamespace(status=403, url=url)

    async def evaluate(self, _script):
        return {"url": FORM, "title": "Just a moment...", "form_count": 0, "captcha": True, "body_text": "", "fields": []}

    async def screenshot(self, path, **kwargs):
        self.shots.append(kwargs)
        if self.fail_shot:
            raise RuntimeError("Target closed")
        Path(path).write_bytes(b"\x89PNG fake")


class _Context:
    def __init__(self, page):
        self.page = page

    async def new_page(self):
        return self.page


@pytest.fixture
def fake_playwright_errors(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", types.SimpleNamespace(Error=RuntimeError))


def _read(page, tmp_path):
    evidence = {}
    path = tmp_path / "trace.png"

    async def go():
        try:
            await greenhouse_browser._read_page(_Context(page), FORM, 1000, greenhouse_browser._NetworkLog(),
                                                evidence, path)
        except greenhouse_browser.FormFetchError as exc:  # the fake page loaded no document: refused, as it should be
            evidence["refused"] = exc.detail

    return evidence, path, go


def test_screenshot_and_status_are_recorded_for_a_block_page(tmp_path, fake_playwright_errors):
    page = _Page()
    evidence, path, go = _read(page, tmp_path)
    asyncio.run(go())
    assert evidence["main_document_status"] == 403
    assert evidence["screenshot"] == greenhouse_browser.file_record(path) and evidence["screenshot"]["bytes"] == 9
    assert page.shots == [{"full_page": True, "animations": "disabled", "timeout": 15000}]


def test_screenshot_is_taken_even_when_the_page_load_fails(tmp_path, fake_playwright_errors):
    evidence, path, go = _read(_Page(fail_goto=True), tmp_path)
    with pytest.raises(RuntimeError, match="Timeout"):
        asyncio.run(go())
    assert evidence["screenshot"]["exists"] is True and path.is_file()


def test_a_failed_screenshot_is_recorded_not_raised(tmp_path, fake_playwright_errors):
    evidence, _path, go = _read(_Page(fail_shot=True), tmp_path)
    asyncio.run(go())
    assert evidence["screenshot"] == {"path": str(tmp_path / "trace.png"), "exists": False, "error": "Target closed"}


# --------------------------------------------------------------------------- recheck from saved observations

def test_recheck_re_evaluates_saved_observations_without_network(monkeypatch, tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    previous = {"mode": "live", "posting": {"id": "1", "board": "acme"}, "outcome": "fail",
                "checks": {"zero_submit_and_zero_non_get_sent": {"ok": False},
                           "no_process_outlived_the_run": {"ok": True, "detail": {"orphans": [], "exit_code": 0}},
                           "browser_gone_after_child_exit": {"ok": True, "detail": {"orphans": []}}}}
    (run_dir / "run.json").write_text(json.dumps(previous))
    (run_dir / "observations.json").write_text(json.dumps({"job_id": "1"}))
    monkeypatch.setattr(runner, "evaluate", lambda data: {"outcome": "pass", "checks": {"api_completed_cleanly": {"ok": True}},
                                                          "challenge": None})
    monkeypatch.setattr(httpx.Client, "send", lambda *_a, **_k: pytest.fail("network during recheck"))
    result = runner.recheck(tmp_path)
    assert result["outcome"] == "pass"
    assert result["rechecked"]["previous_outcome"] == "fail"
    assert result["rechecked"]["previous_failed_checks"] == ["zero_submit_and_zero_non_get_sent"]
    assert set(result["checks"]) == {"api_completed_cleanly", "no_process_outlived_the_run", "browser_gone_after_child_exit"}


def test_recheck_keeps_after_exit_failures(monkeypatch, tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    previous = {"checks": {"no_process_outlived_the_run": {"ok": False, "detail": {"orphans": [{"pid": 9}], "exit_code": 0}},
                           "browser_gone_after_child_exit": {"ok": True, "detail": {"orphans": []}}}}
    (run_dir / "run.json").write_text(json.dumps(previous))
    (run_dir / "observations.json").write_text("{}")
    monkeypatch.setattr(runner, "evaluate", lambda data: {"outcome": "pass", "checks": {}, "challenge": None})
    assert runner.recheck(tmp_path)["outcome"] == "fail"
