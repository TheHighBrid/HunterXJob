"""Gate 1 (docs/RECOVERY_CONTRACT.md): loopback fixture allowance, browser evidence, and gate checks.

Offline and browser-free. The end-to-end gate itself (real Chromium) runs in the
``gate1`` CI job through ``scripts/gate1.py`` and ``tests/test_gate1_e2e.py``.
"""
import asyncio
import importlib
import json
import multiprocessing
import os
import sys
import time
import types
import zipfile
from pathlib import Path

import pytest
from conftest import FIXTURES, load_fixture_json
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import fixture_origin, greenhouse_browser
from app.config import Settings
from app.db import Base
from app.flags import ensure_flags
from app.greenhouse_browser import DomSnapshot, reconcile, untrusted_session_reasons
from app.greenhouse_form import GreenhouseJobRef, is_api_request, parse_greenhouse_payload
from app.models import Application, Job, PipelineStage, SubmissionEvidence
from app.pipeline import browser_ledger_entry, execute_apply
from app.runtime_settings import EDITABLE_KEYS
from app.vault_store import upsert_answer

V2 = Path(__file__).resolve().parents[1]
D2L = GreenhouseJobRef("d2l", "7696196")
SAFE = Settings(_env_file=None)


sys.path.insert(0, str(V2 / "scripts"))
gate = importlib.import_module("gate1_checks")
runner = importlib.import_module("gate1")


@pytest.fixture(autouse=True)
def _origin_off():
    fixture_origin.disable()
    yield
    fixture_origin.disable()


def _enable(origin="http://127.0.0.1:8123", settings=SAFE, ack=fixture_origin.GATE1_ACKNOWLEDGEMENT):
    return fixture_origin.enable_for_gate1(origin, settings, acknowledgement=ack)


# --------------------------------------------------------------------------- loopback allowance

def test_loopback_origin_routes_greenhouse_fetches_only_while_enabled():
    assert D2L.api_url.startswith("https://boards-api.greenhouse.io/")
    assert _enable() == "http://127.0.0.1:8123"
    assert D2L.api_url == "http://127.0.0.1:8123/v1/boards/d2l/jobs/7696196"
    assert D2L.embed_url == "http://127.0.0.1:8123/embed/job_app?for=d2l&token=7696196"
    import httpx

    assert is_api_request(httpx.URL(D2L.api_url))
    assert not is_api_request(httpx.URL("http://127.0.0.1:9999/v1/boards/d2l/jobs/7696196"))
    assert not is_api_request(httpx.URL("https://boards-api.greenhouse.io/v1/boards/d2l/jobs/7696196"))
    fixture_origin.disable()
    assert D2L.api_url.startswith("https://boards-api.greenhouse.io/")
    assert D2L.embed_url.startswith("https://job-boards.greenhouse.io/")


def test_ipv6_loopback_is_accepted():
    assert _enable("http://[::1]:8123") == "http://[::1]:8123"


@pytest.mark.parametrize("origin", [
    "https://boards-api.greenhouse.io",
    "https://127.0.0.1:8123",
    "http://10.0.0.5:8123",
    "http://192.168.1.2:8123",
    "http://localhost:8123",
    "http://127.0.0.1",
    "http://127.0.0.1:8123/v1",
    "http://127.0.0.1:8123/?x=1",
    "http://user:pw@127.0.0.1:8123",
    "http://0.0.0.0:8123",
    "",
])
def test_only_a_plain_loopback_ip_origin_is_accepted(origin):
    with pytest.raises(fixture_origin.FixtureOriginRefused):
        _enable(origin)
    assert fixture_origin.active_origin() is None


@pytest.mark.parametrize("settings", [
    Settings(_env_file=None, allow_live_submission=True),
    Settings(_env_file=None, application_mode="autonomous"),
    Settings(_env_file=None, application_mode="review"),
])
def test_refused_unless_dry_run_only(settings):
    with pytest.raises(fixture_origin.FixtureOriginRefused):
        _enable(settings=settings)
    assert fixture_origin.active_origin() is None


def test_refused_without_the_explicit_acknowledgement():
    for ack in ("", "yes", fixture_origin.GATE1_ACKNOWLEDGEMENT.upper()):
        with pytest.raises(fixture_origin.FixtureOriginRefused):
            _enable(ack=ack)
    assert fixture_origin.active_origin() is None


def test_production_config_cannot_enable_the_loopback_origin(monkeypatch, tmp_path):
    names = ("FIXTURE_ORIGIN", "GATE1_FIXTURE_ORIGIN", "HUNTERX_GATE1_FIXTURE_ORIGIN", "GREENHOUSE_API_BASE",
             "GREENHOUSE_FIXTURE_ORIGIN", "LOOPBACK_FIXTURE_ORIGIN")
    (tmp_path / ".env").write_text("\n".join(f"{name}=http://127.0.0.1:8123" for name in names), encoding="utf-8")
    for name in names:
        monkeypatch.setenv(name, "http://127.0.0.1:8123")
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert not [name for name in Settings.model_fields if "fixture" in name or "origin" in name.replace("cors_origins", "")]
    assert not [key for key in EDITABLE_KEYS if "fixture" in key or "origin" in key]
    assert fixture_origin.active_origin() is None
    assert D2L.api_url.startswith("https://boards-api.greenhouse.io/")
    assert settings.application_mode == "dry_run"


def test_no_app_module_enables_the_loopback_origin():
    callers = [path.name for path in (V2 / "app").rglob("*.py")
               if "enable_for_gate1(" in path.read_text(encoding="utf-8") and path.name != "fixture_origin.py"]
    assert callers == []


def test_app_code_never_attaches_to_an_external_browser():
    for path in (V2 / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "connect_over_cdp" not in text, path
        assert "remote-debugging-port" not in text, path


# --------------------------------------------------------------------------- browser evidence in the app

def _evidence(**extra):
    return {
        "launcher": "playwright.chromium.launch", "browser_version": "129", "executable_path": "/x/ms-playwright/chrome",
        "service_workers": "block",
        "request_count": 1, "non_get_attempts": 0, "aborted": 0, "submit_events": [],
        "requests": [{"method": "GET", "url": "http://127.0.0.1:1/embed/job_app", "resource_type": "document",
                      "action": "continued"}],
        "main_frame_navigations": ["http://127.0.0.1:1/embed/job_app"], "main_frame_document_requests": 1,
        "trace": {"path": "trace.zip", "sha256": "ab" * 32, "bytes": 10, "zip_ok": True}, **extra,
    }


def _snapshot(evidence):
    return DomSnapshot.from_dict({"url": "http://127.0.0.1:1/embed/job_app", "title": "t", "form_count": 1,
                                  "captcha": False, "body_text": "", "evidence": evidence, "fields": [
                                      {"tag": "input", "type": "text", "id": "first_name", "name": "first_name",
                                       "role": "", "label": "First Name", "required": True, "options": []}]})


def test_reconcile_keeps_network_evidence_and_dom_fields():
    form = reconcile(parse_greenhouse_payload(load_fixture_json("d2l_7696196.json"), D2L), _snapshot(_evidence()))
    evidence = form.metadata["browser_evidence"]
    assert evidence["trace"]["sha256"] == "ab" * 32
    assert evidence["dom_fields"] == [{"key": "first_name", "id": "first_name", "name": "first_name", "tag": "input",
                                       "type": "text", "required": True, "options": []}]
    assert not [w for w in form.warnings if "non-GET" in w]


def test_reconcile_warns_when_the_page_attempted_a_write():
    form = reconcile(parse_greenhouse_payload(load_fixture_json("d2l_7696196.json"), D2L),
                     _snapshot(_evidence(non_get_attempts=2, aborted=2)))
    assert any("attempted 2 non-GET request(s)" in warning and "all were aborted" in warning
               for warning in form.warnings)
    assert form.metadata["dom_verified"] is True


def test_allowlisted_graphql_rewrites_are_not_reported_as_blocked_writes():
    form = reconcile(parse_greenhouse_payload(load_fixture_json("d2l_7696196.json"), D2L),
                     _snapshot(_evidence(non_get_attempts=3, aborted=1, rewritten_to_get=2)))
    warnings = [warning for warning in form.warnings if "non-GET" in warning]
    assert len(warnings) == 1 and "attempted 1 non-GET request(s)" in warnings[0]
    form = reconcile(parse_greenhouse_payload(load_fixture_json("d2l_7696196.json"), D2L),
                     _snapshot(_evidence(non_get_attempts=2, aborted=0, rewritten_to_get=2)))
    assert not [warning for warning in form.warnings if "non-GET" in warning]


def test_only_get_is_read_only_and_head_is_aborted():
    assert frozenset({"GET"}) == greenhouse_browser.READ_ONLY_METHODS
    network = greenhouse_browser._NetworkLog()
    router = greenhouse_browser._ReadOnlyRouter(network, ())
    calls = []

    class Request:
        def __init__(self, method):
            self.method, self.url, self.post_data, self.resource_type = method, "http://127.0.0.1:1/x", None, "fetch"
            self.frame = types.SimpleNamespace(parent_frame=object())

        def is_navigation_request(self):
            return False

    class Route:
        def __init__(self, method):
            self.request = Request(method)

        async def continue_(self):
            calls.append(("continue", self.request.method))

        async def abort(self):
            calls.append(("abort", self.request.method))

    for method in ("GET", "HEAD", "POST"):
        asyncio.run(router(Route(method)))
    assert calls == [("continue", "GET"), ("abort", "HEAD"), ("abort", "POST")]
    summary = network.summary()
    assert summary["non_get_attempts"] == 2 and summary["aborted"] == 2 and summary["rewritten_to_get"] == 0


def test_submit_monitor_refuses_and_records_every_submit_path():
    script = greenhouse_browser.SUBMIT_MONITOR_JS
    assert "window.addEventListener('submit'" in script and "event.preventDefault()" in script
    # HTMLFormElement.submit() fires no submit event, so the method itself is replaced (in every frame).
    assert "['submit', 'form.submit()']" in script and "['requestSubmit', 'form.requestSubmit()']" in script
    assert "HTMLFormElement.prototype" in script and "configurable: false" in script


def _fake_playwright(captured):
    """A stand-in for playwright.async_api that records how _inspect sets up the browser context."""
    url = "http://127.0.0.1:1/embed/job_app"

    class Frame:
        def __init__(self, events):
            self.url, self.events = url, events

        async def evaluate(self, _script):
            return self.events

    class Page:
        main_frame = object()
        frames = (Frame([]), Frame([{"via": "form.submit()", "action": url, "method": "get"}]))

        def on(self, *_args):
            return None

        async def goto(self, target, **_kwargs):
            return types.SimpleNamespace(status=200, url=target)

        async def evaluate(self, _script):
            return {"url": url, "title": "t", "form_count": 1, "captcha": False, "body_text": "", "fields": []}

    class Context:
        tracing = None

        async def add_init_script(self, script):
            captured["init_script"] = script

        async def route(self, pattern, _handler):
            captured["route"] = pattern

        async def new_page(self):
            return Page()

        async def close(self):
            captured["context_closed"] = True

    class Browser:
        version = "129"

        async def new_context(self, **kwargs):
            captured["context_kwargs"] = kwargs
            return Context()

        async def close(self):
            captured["browser_closed"] = True

    class Chromium:
        executable_path = "/x/ms-playwright/chrome"

        async def launch(self, **kwargs):
            captured["launch_kwargs"] = kwargs
            return Browser()

    class Manager:
        async def __aenter__(self):
            return types.SimpleNamespace(chromium=Chromium())

        async def __aexit__(self, *_exc):
            return False

    return types.SimpleNamespace(async_playwright=Manager, Error=RuntimeError)


def test_inspection_blocks_service_workers_and_reads_submit_attempts_from_every_frame(monkeypatch):
    captured = {}
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", _fake_playwright(captured))
    with pytest.raises(greenhouse_browser.FormFetchError) as failure:
        asyncio.run(greenhouse_browser._inspect("http://127.0.0.1:1/embed/job_app", 1000))
    assert captured["context_kwargs"] == {"accept_downloads": False, "service_workers": "block"}
    assert captured["init_script"] == greenhouse_browser.SUBMIT_MONITOR_JS and captured["route"] == "**/*"
    assert captured["launch_kwargs"] == {} and captured["context_closed"] and captured["browser_closed"]
    # The submit attempt was recorded in a subframe, not the main frame, and still fails the session closed.
    assert "page attempted 1 form submission(s)" in failure.value.detail


def test_snapshot_without_evidence_adds_nothing():
    form = reconcile(parse_greenhouse_payload(load_fixture_json("d2l_7696196.json"), D2L), _snapshot({}))
    assert "browser_evidence" not in form.metadata


@pytest.mark.parametrize("network, submits, final, expected", [
    ({"main_frame_document_requests": 1}, [], "http://h/embed/job_app?for=a", []),
    ({"main_frame_document_requests": 1}, [{"action": "x"}], "http://h/embed/job_app", ["form submission"]),
    ({"main_frame_document_requests": 2}, [], "http://h/embed/job_app", ["extra main-frame navigation"]),
    ({"main_frame_document_requests": 1}, [], "http://h/confirmation", ["instead of the form"]),
    ({"main_frame_document_requests": 1}, [], "chrome-error://chromewebdata/", ["instead of the form"]),
])
def test_untrusted_session_reasons(network, submits, final, expected):
    reasons = untrusted_session_reasons(network, submits, "http://h/embed/job_app?for=a", final)
    assert len(reasons) == len(expected)
    for reason, fragment in zip(reasons, expected, strict=True):
        assert fragment in reason


def test_dry_run_ledger_binds_the_browser_trace_hash():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    ensure_flags(db)
    for key, value in gate.FAKE_ANSWERS.items():
        upsert_answer(db, key=key, value=value, source="user", scope="global")
    job = Job(source="greenhouse", external_id="7696196", title="Fixture", company="d2l", location="Toronto, Ontario",
              url="https://job-boards.greenhouse.io/d2l/jobs/7696196", stage=PipelineStage.ready_to_apply.value,
              platform="greenhouse")
    db.add(job)
    db.commit()
    application = Application(job_id=job.id, mode="dry_run", adapter_name="greenhouse")
    db.add(application)
    db.commit()
    form = reconcile(parse_greenhouse_payload(load_fixture_json("d2l_7696196.json"), D2L), _snapshot(_evidence()))

    class Provider:
        def fetch(self, _job):
            return form

    result = execute_apply(db, Settings(_env_file=None, automation_enabled=True), application.id, form_provider=Provider())
    assert result["status"] == "dry_run_complete"
    row = db.execute(select(SubmissionEvidence)).scalars().one()
    assert row.kind == "dry_run" and row.sufficient is False
    assert f"browser trace sha256={'ab' * 32}" in row.confirmation_text
    entry = browser_ledger_entry(result["form"])
    assert entry["trace_sha256"] == "ab" * 32 and entry["non_get_attempts"] == 0
    from app.evidence import payload_hash

    assert row.payload_hash == payload_hash({"filled": json.loads(application.answers_json), "form_source": "greenhouse_api",
                                             "materials": result["materials"], "browser": entry})


def test_aborted_analytics_beacon_does_not_stop_a_production_dry_run():
    """A real page's analytics beacon (POST, aborted by the router) is recorded and warned, not fatal."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    ensure_flags(db)
    for key, value in gate.FAKE_ANSWERS.items():
        upsert_answer(db, key=key, value=value, source="user", scope="global")
    job = Job(source="greenhouse", external_id="7696196", title="Fixture", company="d2l", location="Toronto, Ontario",
              url="https://job-boards.greenhouse.io/d2l/jobs/7696196", stage=PipelineStage.ready_to_apply.value,
              platform="greenhouse")
    db.add(job)
    db.commit()
    application = Application(job_id=job.id, mode="dry_run", adapter_name="greenhouse")
    db.add(application)
    db.commit()
    beacon = {"method": "POST", "url": "https://www.google-analytics.com/g/collect", "resource_type": "ping",
              "action": "aborted"}
    evidence = _evidence(non_get_attempts=1, aborted=1, request_count=2, requests=[*_evidence()["requests"], beacon])
    form = reconcile(parse_greenhouse_payload(load_fixture_json("d2l_7696196.json"), D2L), _snapshot(evidence))

    class Provider:
        def fetch(self, _job):
            return form

    result = execute_apply(db, Settings(_env_file=None, automation_enabled=True), application.id, form_provider=Provider())
    assert result["status"] == "dry_run_complete" and result["submitted"] is False
    assert any("all were aborted" in warning for warning in result["form"]["warnings"])
    assert browser_ledger_entry(result["form"])["non_get_attempts"] == 1
    assert db.execute(select(SubmissionEvidence)).scalars().one().kind == "dry_run"


def test_browser_trace_dir_is_not_phone_editable():
    assert "browser_trace_dir" not in EDITABLE_KEYS
    assert Settings(_env_file=None).browser_trace_dir == ""


# --------------------------------------------------------------------------- gate checks

def test_expected_fields_come_from_the_fixture_html():
    fields = gate.expected_fields((FIXTURES / "sample_form.html").read_text(encoding="utf-8"))
    assert [item["key"] for item in fields] == ["first_name", "last_name", "email", "phone", "resume", "cover_letter",
                                                "work_authorization"]
    assert fields[-1]["options"] == ["Authorized to work in Canada", "Need sponsorship"]
    assert [item["key"] for item in fields if item["required"]] == ["first_name", "last_name", "email", "resume",
                                                                    "work_authorization"]


def test_compare_fields_reports_missing_extra_and_changed():
    expected = gate.expected_fields((FIXTURES / "sample_form.html").read_text(encoding="utf-8"))
    extracted = [dict(item, id=item["key"]) for item in expected]
    assert gate.compare_fields(expected, extracted) == []
    extracted[0]["required"] = False
    extracted.pop()
    extracted.append({"id": "surprise", "tag": "input", "type": "", "required": False, "options": []})
    problems = gate.compare_fields(expected, extracted)
    assert "missing field work_authorization" in problems
    assert "unexpected field surprise" in problems
    assert any(problem.startswith("first_name.required") for problem in problems)


def _server(path="/embed/job_app", method="GET", expected=True):
    return {"method": method, "path": path, "query": "", "expected": expected}


def test_network_evaluation_passes_only_a_clean_read_only_session():
    origin, embed = "http://127.0.0.1:1", "http://127.0.0.1:1/embed/job_app"
    clean = _evidence()
    assert gate.evaluate_network(clean, [_server()], origin, embed) == []
    bad = _evidence(non_get_attempts=2, aborted=2, submit_events=[{"action": "/apply"}], service_workers="allow",
                    requests=[{"method": "POST", "url": f"{origin}/collect"}, {"method": "HEAD", "url": f"{origin}/x"},
                              {"method": "GET", "url": "https://evil.test/x"}],
                    main_frame_navigations=[embed, f"{origin}/confirmation"])
    problems = gate.evaluate_network(bad, [_server(method="POST", expected=False), _server(method="HEAD")], origin, embed)
    text = "\n".join(problems)
    for fragment in ("non-GET request(s)", "aborted 2", "form submission", "service workers were not blocked",
                     "main frame navigations", "non-GET browser request POST", "non-GET browser request HEAD",
                     "left the fixture origin", "fixture server received POST", "fixture server received HEAD",
                     "unexpected request"):
        assert fragment in text
    missing = gate.evaluate_network({}, [_server()], origin, embed)
    assert missing == ["the API result carries no browser evidence (browser verification did not complete)"]


def test_trace_zip_is_checked_independently(tmp_path):
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as archive:
        archive.writestr("trace.trace", "{}")
        archive.writestr("trace.network", '{"url": "http://127.0.0.1:1/embed/job_app?for=d2l"}')
    result = gate.inspect_trace_zip(good)
    assert result["ok"] and len(result["sha256"]) == 64
    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_bytes(good.read_bytes()[:40])
    assert gate.inspect_trace_zip(corrupt)["ok"] is False
    assert gate.inspect_trace_zip(tmp_path / "missing.zip")["ok"] is False


def test_process_leak_detection_sees_a_stray_child_and_its_exit():
    proc = multiprocessing.get_context("spawn").Process(target=time.sleep, args=(60,))
    proc.start()
    try:
        deadline = time.monotonic() + 5
        seen = []
        while not seen and time.monotonic() < deadline:
            seen = [item for item in gate.live_descendants() if item["pid"] == proc.pid]
        assert seen, "leak detector did not see the child"
        assert gate.still_running(seen) == seen
    finally:
        proc.kill()
        proc.join(timeout=10)
    assert gate.still_running(seen) == []
    assert not [item for item in gate.live_descendants() if item["pid"] == proc.pid]


def _detached_sleeper():
    os.setsid()  # like Playwright's Chromium: its own process group, so killing the run's group misses it
    time.sleep(120)


def _run_that_hangs_with_a_grandchild():
    grandchild = multiprocessing.get_context("spawn").Process(target=_detached_sleeper)
    grandchild.start()
    time.sleep(120)


def test_a_timed_out_run_is_killed_with_every_descendant():
    child = multiprocessing.get_context("spawn").Process(target=_run_that_hangs_with_a_grandchild)
    child.start()
    deadline = time.monotonic() + 20
    while len(gate.live_descendants(child.pid)) < 1 and time.monotonic() < deadline:
        time.sleep(0.1)
    supervision = runner.supervise(child, timeout=0.5)
    assert supervision["timed_out"] is True and not child.is_alive()
    assert supervision["descendants_seen"] >= 1 and supervision["orphans_not_killed"] == []
    survivors = [item for item in supervision["orphans"] if gate.still_running([item])]
    assert survivors == []


def test_report_is_never_green_for_fewer_than_three_runs_or_a_negative_variant():
    run = {"ok": True, "checks": {}}
    assert gate.build_report([run, run], variant="clean", requested=2)["verdict"] == "fail"
    assert gate.build_report([run] * 3, variant="post_beacon", requested=3)["verdict"] == "fail"
    report = gate.build_report([run] * 3, variant="clean", requested=3)
    assert report["verdict"] == "pass"
    assert report["not_proven"]["real_employer_site"] is False
    assert set(report["not_proven"]) == set(report["not_proven_notes"])
    failed = gate.build_report([run, run, {"ok": False, "checks": {}}], variant="clean", requested=3)
    assert failed["verdict"] == "fail" and not any(failed["proven"].values())


def test_child_environment_is_dry_run_only_and_ignores_the_developer_shell(monkeypatch, tmp_path):
    monkeypatch.setenv("ALLOW_LIVE_SUBMISSION", "true")
    monkeypatch.setenv("APPLICATION_MODE", "autonomous")
    monkeypatch.setenv("GREENHOUSE_BOARD_TOKENS", "real-employer")
    env = runner.child_env(tmp_path)
    assert env["ALLOW_LIVE_SUBMISSION"] == "false" and env["APPLICATION_MODE"] == "dry_run"
    assert "GREENHOUSE_BOARD_TOKENS" not in env
    assert env["GREENHOUSE_BROWSER_VERIFY"] == "true" and env["BROWSER_TRACE_DIR"].startswith(str(tmp_path))


def test_gate_uses_existing_fixtures_and_fake_identity_only():
    assert gate.FORM_FIXTURE == FIXTURES / "sample_form.html" and gate.FORM_FIXTURE.is_file()
    assert gate.API_FIXTURE == FIXTURES / "greenhouse" / "d2l_7696196.json" and gate.API_FIXTURE.is_file()
    assert gate.FAKE_ANSWERS["email"].endswith("@example.test")
    for snippet in filter(None, gate.VARIANTS.values()):
        assert (gate.NEGATIVE_DIR / snippet).is_file()
