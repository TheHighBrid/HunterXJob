"""Real-form extraction for Lever and Ashby, and the read-only fetch contract.

Offline: sanitized snapshots of real public pages/payloads (tests/fixtures/ats)
served through httpx.MockTransport.
"""
import json

import httpx
import pytest
from conftest import load_ats_fixture, load_ats_json
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.ashby_form import (
    ASHBY_READ_OPS,
    FORM_QUERY,
    AshbyJobRef,
    ashby_ref_for_job,
    fetch_ashby_form,
    graphql_get,
    parse_ashby_payload,
)
from app.config import Settings
from app.db import Base
from app.flags import ensure_flags
from app.form_engine import ControlType, plan_fill
from app.greenhouse_browser import DomField, DomSnapshot, graphql_get_rewrite
from app.greenhouse_form import FormFetchError
from app.lever_form import LeverJobRef, fetch_lever_form, lever_ref_for_job, parse_lever_apply_page
from app.models import Application, Job, PipelineStage, ReviewTask, SubmissionEvidence
from app.pipeline import execute_apply
from app.real_forms import LiveFormProvider
from app.vault_store import load_vault, upsert_answer

LEVER = LeverJobRef("northwind", "ed663b5f-1b13-5fb7-853b-e4cdf805c6dd")
ASHBY = AshbyJobRef("northwind", "9b99caed-e385-574f-94f7-4be75f67782d")
ASHBY_ENGINEER = AshbyJobRef("northwind", "831c138d-1ab7-5afa-b938-dd5da8882214")


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    ensure_flags(db)
    return db


def _lever_form():
    return parse_lever_apply_page(load_ats_fixture("lever_apply.html"), LEVER, job_location="London")


def _ashby_form(name="ashby_form_advisor.json", ref=ASHBY):
    return parse_ashby_payload(load_ats_json(name)["data"], ref, job_location="Toronto, Ontario")


def _by_key(form):
    return {control.key: control for control in form.controls}


# --- refs ------------------------------------------------------------------------------------

def test_lever_ref_from_hosted_url_or_discovery_fields():
    job = Job(source="feed", url=f"https://jobs.lever.co/northwind/{LEVER.posting_id}", title="t", company="x", external_id="1")
    assert lever_ref_for_job(job) == LEVER
    discovered = Job(source="lever", board="northwind", company="northwind", external_id=LEVER.posting_id, url="https://example.com", title="t")
    assert lever_ref_for_job(discovered) == LEVER
    assert lever_ref_for_job(Job(source="lever", company="north wind", external_id="abc", url="", title="t")) is None
    assert LEVER.apply_url == f"https://jobs.lever.co/northwind/{LEVER.posting_id}/apply"


def test_ashby_ref_from_hosted_url_or_discovery_fields():
    job = Job(source="feed", url=f"https://jobs.ashbyhq.com/northwind/{ASHBY.posting_id}/application", title="t", company="x", external_id="1")
    assert ashby_ref_for_job(job) == ASHBY
    assert ashby_ref_for_job(Job(source="ashby", board="northwind", external_id=ASHBY.posting_id, url="", title="t", company="n")) == ASHBY
    assert ashby_ref_for_job(Job(source="ashby", board="../evil", external_id=ASHBY.posting_id, url="", title="t", company="n")) is None
    with pytest.raises(ValueError):
        AshbyJobRef("northwind", "not-a-uuid")


# --- Lever extraction ------------------------------------------------------------------------

def test_lever_standard_fields_map_to_profile_keys():
    form = _lever_form()
    controls = _by_key(form)
    assert form.platform == "lever" and form.source == "lever_page"
    assert form.title == "Android Engineer - Experience"
    assert controls["name"].required and controls["name"].vault_keys == ["name", "full_name"]
    assert controls["email"].control_type is ControlType.EMAIL and controls["email"].required
    assert controls["phone"].control_type is ControlType.TEL
    assert controls["resume"].control_type is ControlType.FILE and controls["resume"].required
    assert controls["location"].control_type is ControlType.AUTOCOMPLETE
    assert controls["org"].vault_keys[-1] == "current_company"
    assert controls["urls[LinkedIn]"].vault_keys[-1] == "linkedin_url" and not controls["urls[LinkedIn]"].required
    assert controls["urls[Other]"].vault_keys == ["urls[Other]"]
    assert controls["pronouns"].sensitive
    assert form.vault_scopes == ["lever:northwind", f"lever:northwind:{LEVER.posting_id}"]


def test_lever_custom_cards_and_surveys_come_from_base_templates():
    controls = _lever_form().controls
    cards = [c for c in controls if c.key.startswith("cards[")]
    surveys = [c for c in controls if c.key.startswith("surveysResponses[")]
    assert len(cards) == 3 and len(surveys) == 7
    multi = next(c for c in cards if c.control_type is ControlType.MULTISELECT)
    assert multi.required and "Yes - Intern" in multi.options
    # Prior-employment questions are never answered from a shared key.
    assert multi.vault_keys == [multi.key]
    assert all(c.section == "demographic" and c.sensitive and not c.required for c in surveys)
    assert {c.vault_keys[-1] for c in surveys} >= {"gender", "race_ethnicity", "veteran_status"}


def test_lever_marketing_consent_is_optional_and_never_auto_consented():
    consent = _by_key(_lever_form())["consent[marketing]"]
    assert consent.section == "consent" and not consent.required and not consent.legal
    plan = plan_fill([consent], load_vault(_db()))
    assert plan.items[0].status == "skip"


def test_lever_form_records_the_hcaptcha_submit_boundary():
    form = _lever_form()
    assert form.metadata["submit_boundary"] == "captcha_detected"
    assert any("hCaptcha" in warning for warning in form.warnings)


def test_lever_page_without_application_form_is_a_fetch_failure():
    with pytest.raises(FormFetchError) as excinfo:
        parse_lever_apply_page("<html><body><h2>Closed</h2></body></html>", LEVER)
    assert excinfo.value.reason_code == "form_fetch_failed"


def test_lever_fetch_is_one_get_to_the_hosted_apply_page():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text=load_ats_fixture("lever_apply.html"))

    form = fetch_lever_form(LEVER, client=_client(handler))
    assert len(form.controls) > 20
    assert [(r.method, r.url.host, r.url.path) for r in seen] == [("GET", "jobs.lever.co", f"/northwind/{LEVER.posting_id}/apply")]


@pytest.mark.parametrize("response, reason", [
    (httpx.Response(404, text="Not found"), "form_unavailable"),
    (httpx.Response(302, headers={"Location": "https://jobs.lever.co/northwind?error=true"}), "form_fetch_failed"),
    (httpx.Response(503, text="busy"), "form_fetch_failed"),
])
def test_lever_fetch_failures_are_classified(response, reason):
    with pytest.raises(FormFetchError) as excinfo:
        fetch_lever_form(LEVER, client=_client(lambda request: response))
    assert excinfo.value.reason_code == reason


# --- Ashby extraction ------------------------------------------------------------------------

def test_ashby_system_and_custom_fields_are_mapped():
    form = _ashby_form()
    controls = _by_key(form)
    assert form.platform == "ashby" and form.source == "ashby_api"
    assert controls["_systemfield_name"].vault_keys == ["_systemfield_name", "full_name"]
    assert controls["_systemfield_email"].control_type is ControlType.EMAIL
    assert controls["_systemfield_resume"].control_type is ControlType.FILE and controls["_systemfield_resume"].required
    assert controls["currentCompany"].vault_keys[-1] == "current_company"
    assert controls["phone"].control_type is ControlType.TEL
    eligible = next(c for c in form.controls if c.label == "Are you legally eligible to work in Canada?")
    assert eligible.control_type is ControlType.RADIO and eligible.options == ["Yes", "No"]
    assert eligible.vault_keys[-1] == "work_authorization_ca" and eligible.sensitive
    multi = next(c for c in form.controls if c.control_type is ControlType.MULTISELECT)
    assert multi.required and len(multi.options) == 4
    assert form.metadata["submit_boundary"] == "captcha_expected"


def test_ashby_survey_forms_are_voluntary_demographics():
    controls = _by_key(_ashby_form())
    gender = controls["_systemfield_eeoc_gender"]
    assert gender.section == "demographic" and gender.sensitive and not gender.required
    assert gender.vault_keys[-1] == "gender"


def test_ashby_recording_consent_is_a_legal_question_answered_per_question():
    consent = _by_key(_ashby_form())["_systemfield_recording_consent"]
    assert consent.legal and consent.required and consent.vault_keys == ["_systemfield_recording_consent"]


def test_ashby_engineer_form_maps_links_and_cover_letter_text():
    controls = _ashby_form("ashby_form_engineer.json", ASHBY_ENGINEER).controls
    by_label = {c.label: c for c in controls}
    assert by_label["Github"].vault_keys[-1] == "github_url" and by_label["Github"].required
    assert by_label["Cover letter"].vault_keys[-1] == "cover_letter_text"


def test_ashby_missing_posting_is_form_unavailable():
    with pytest.raises(FormFetchError) as excinfo:
        parse_ashby_payload(load_ats_json("ashby_form_missing.json")["data"], ASHBY)
    assert excinfo.value.reason_code == "form_unavailable"


def test_ashby_fetch_is_one_read_only_graphql_get():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=load_ats_json("ashby_form_advisor.json"))

    fetch_ashby_form(ASHBY, client=_client(handler))
    assert len(seen) == 1
    request = seen[0]
    assert (request.method, request.url.host, request.url.path) == ("GET", "jobs.ashbyhq.com", "/api/non-user-graphql")
    assert request.headers["apollo-require-preflight"] == "true"
    assert request.url.params["query"].startswith("query ApiJobPosting")
    assert "mutation" not in request.url.params["query"]
    assert json.loads(request.url.params["variables"]) == {"organizationHostedJobsPageName": "northwind", "jobPostingId": ASHBY.posting_id}
    assert request.content == b""


@pytest.mark.parametrize("response, reason", [
    (httpx.Response(200, json={"errors": [{"message": "bad"}], "data": None}), "form_fetch_failed"),
    (httpx.Response(500, text="boom"), "form_fetch_failed"),
    (httpx.Response(200, text="<html>"), "form_fetch_failed"),
    (httpx.Response(200, json={"data": {"jobPosting": None}}), "form_unavailable"),
])
def test_ashby_fetch_failures_are_classified(response, reason):
    with pytest.raises(FormFetchError) as excinfo:
        fetch_ashby_form(ASHBY, client=_client(lambda request: response))
    assert excinfo.value.reason_code == reason


def test_ashby_graphql_get_refuses_mutations():
    with pytest.raises(FormFetchError):
        graphql_get(ASHBY, "mutation ApiSubmitApplication { submit }", client=_client(lambda r: httpx.Response(200)))
    with pytest.raises(FormFetchError):
        graphql_get(ASHBY, FORM_QUERY + " mutation", client=_client(lambda r: httpx.Response(200)))


# --- browser verification safety ---------------------------------------------------------------

ASHBY_GQL = "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting"


def _body(query, operation="ApiJobPosting"):
    return json.dumps({"operationName": operation, "query": query, "variables": {"jobPostingId": "x"}})


def test_graphql_rewrite_converts_only_allowlisted_read_queries():
    url = graphql_get_rewrite("POST", ASHBY_GQL, _body("query ApiJobPosting { jobPosting { id } }"), ASHBY_READ_OPS)
    assert url is not None and url.startswith("https://jobs.ashbyhq.com/api/non-user-graphql?")
    params = httpx.URL(url).params
    assert params["op"] == "ApiJobPosting" and params["query"].startswith("query ")


@pytest.mark.parametrize("method, url, body, ops", [
    ("POST", ASHBY_GQL, _body("mutation ApiJobPosting { apply }"), ASHBY_READ_OPS),
    ("POST", ASHBY_GQL, _body("query ApiJobPosting { x } mutation Y { z }"), ASHBY_READ_OPS),
    ("POST", ASHBY_GQL, _body("query X { y }", operation="ApiSubmitSingleApplicationFormAction"), ASHBY_READ_OPS),
    ("POST", "https://evil.example/api/non-user-graphql?op=ApiJobPosting", _body("query A { b }"), ASHBY_READ_OPS),
    ("POST", "http://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting", _body("query A { b }"), ASHBY_READ_OPS),
    ("POST", "https://jobs.ashbyhq.com/api/file-upload", _body("query A { b }"), ASHBY_READ_OPS),
    ("PUT", ASHBY_GQL, _body("query ApiJobPosting { a }"), ASHBY_READ_OPS),
    ("POST", ASHBY_GQL, "not json", ASHBY_READ_OPS),
    ("POST", ASHBY_GQL, _body("query ApiJobPosting { a }"), frozenset()),
])
def test_graphql_rewrite_refuses_everything_else(method, url, body, ops):
    assert graphql_get_rewrite(method, url, body, ops) is None


def test_ashby_dom_names_drop_the_form_id_prefix():
    radio = DomField(tag="input", type="radio", id="", name="7a88e434-b3a2-4bc1-ad46-a6c755afa5a7_e2a73ff7-521c-47cb-b7a0-000000000000",
                     role="", label="Eligible?", required=True)
    assert radio.form_key == "e2a73ff7-521c-47cb-b7a0-000000000000"
    assert DomField(tag="input", type="text", id="", name="_systemfield_name", role="", label="Name", required=True).form_key == "_systemfield_name"


# --- provider and pipeline ---------------------------------------------------------------------

def _job(db, *, source, board, external_id, url, title="Senior Advisor", location="Toronto, Ontario"):
    job = Job(source=source, board=board, external_id=external_id, title=title, company=board, location=location,
              url=url, description="Advise clients", stage=PipelineStage.ready_to_apply.value, platform=source)
    db.add(job)
    db.commit()
    application = Application(job_id=job.id, mode="dry_run", adapter_name=source, cover_letter_text="Hi")
    db.add(application)
    db.commit()
    return job, application


def _routes(requests):
    def handler(request):
        requests.append(request)
        if request.url.host == "jobs.lever.co":
            return httpx.Response(200, text=load_ats_fixture("lever_apply.html"))
        if request.url.host == "jobs.ashbyhq.com":
            return httpx.Response(200, json=load_ats_json("ashby_form_advisor.json"))
        return httpx.Response(404)
    return handler


@pytest.mark.parametrize("source, ref", [("lever", LEVER), ("ashby", ASHBY)])
def test_dry_run_plans_against_the_real_lever_and_ashby_forms_without_submitting(source, ref):
    db = _db()
    url = ref.apply_url if source == "lever" else ref.page_url
    job, application = _job(db, source=source, board="northwind", external_id=ref.posting_id, url=url)
    requests = []
    provider = LiveFormProvider(Settings(_env_file=None, automation_enabled=True), client=_client(_routes(requests)))
    result = execute_apply(db, Settings(_env_file=None, automation_enabled=True), application.id, form_provider=provider)
    assert result["submitted"] is False
    # Unanswered required questions stop the dry-run for review; nothing is guessed.
    assert result["status"] == "needs_review"
    assert result["form"]["source"] == f"{source}_{'page' if source == 'lever' else 'api'}"
    assert {r.method for r in requests} == {"GET"}
    assert db.execute(select(SubmissionEvidence).where(SubmissionEvidence.kind == "submitted")).first() is None
    assert db.execute(select(ReviewTask).where(ReviewTask.job_id == job.id)).first() is not None


def test_ashby_dry_run_completes_when_every_required_answer_is_on_file():
    db = _db()
    _job_row, application = _job(db, source="ashby", board="northwind", external_id=ASHBY_ENGINEER.posting_id,
                                 url=ASHBY_ENGINEER.page_url, title="Senior Engineer")
    form = _ashby_form("ashby_form_engineer.json", ASHBY_ENGINEER)
    answers = {"full_name": "Test Candidate", "email": "candidate@example.test", "resume": "~/hunterx/resume.pdf",
               "cover_letter_text": "Hello", "github_url": "https://github.com/example"}
    for control in form.controls:
        if control.required and not any(key in answers for key in control.vault_keys):
            answers[control.key] = control.options[0] if control.options else "Answer"
    for key, value in answers.items():
        upsert_answer(db, key=key, value=value, source="user", scope="global")

    def handler(request):
        return httpx.Response(200, json=load_ats_json("ashby_form_engineer.json"))

    provider = LiveFormProvider(Settings(_env_file=None, automation_enabled=True), client=_client(handler))
    result = execute_apply(db, Settings(_env_file=None, automation_enabled=True), application.id, form_provider=provider)
    assert result["status"] == "dry_run_complete", result
    assert result["submitted"] is False


def test_lever_and_ashby_fetch_failures_open_review_tasks_and_never_fake_a_form():
    db = _db()
    job, application = _job(db, source="lever", board="northwind", external_id=LEVER.posting_id, url=LEVER.apply_url)
    provider = LiveFormProvider(Settings(_env_file=None, automation_enabled=True), client=_client(lambda r: httpx.Response(503)))
    result = execute_apply(db, Settings(_env_file=None, automation_enabled=True), application.id, form_provider=provider)
    assert result["status"] == "needs_review" and result["reason"] == "form_fetch_failed"
    task = db.execute(select(ReviewTask).where(ReviewTask.job_id == job.id)).scalar_one()
    assert "no sample form was used" in task.detail


def test_browser_verification_is_optional_and_reconciles_required_page_fields():
    db = _db()
    job, _application = _job(db, source="ashby", board="northwind", external_id=ASHBY.posting_id, url=ASHBY.page_url)
    snapshot = DomSnapshot(url=ASHBY.page_url, title="t", form_count=1, captcha=True, body_text="", fields=[
        DomField(tag="input", type="text", id="", name="_systemfield_name", role="", label="Name", required=True),
        DomField(tag="input", type="text", id="", name="extra_required", role="", label="Extra", required=True),
    ])
    seen = []

    def inspector(ref):
        seen.append(ref)
        return snapshot

    client = _client(lambda r: httpx.Response(200, json=load_ats_json("ashby_form_advisor.json")))
    off = LiveFormProvider(Settings(_env_file=None), client=client, inspector=inspector).fetch(job)
    assert off.metadata["dom_verified"] is False and seen == []
    on = LiveFormProvider(Settings(_env_file=None, ashby_browser_verify=True), client=client, inspector=inspector).fetch(job)
    assert seen == [ASHBY]
    assert on.metadata["dom_verified"] is True and on.metadata["dom_only_required_fields"] == ["extra_required"]


def test_browser_render_without_captcha_keeps_the_fetched_submit_boundary():
    db = _db()
    job, _application = _job(db, source="lever", board="northwind", external_id=LEVER.posting_id, url=LEVER.apply_url)
    snapshot = DomSnapshot(url=LEVER.apply_url, title="t", form_count=1, captcha=False, body_text="", fields=[
        DomField(tag="input", type="text", id="", name="name", role="", label="Full name", required=True),
    ])
    client = _client(lambda r: httpx.Response(200, text=load_ats_fixture("lever_apply.html")))
    form = LiveFormProvider(Settings(_env_file=None, lever_browser_verify=True), client=client,
                            inspector=lambda ref: snapshot).fetch(job)
    # The hCaptcha found in the HTML is never forgotten because a lazy widget didn't render.
    assert form.metadata["dom_verified"] is True
    assert form.metadata["submit_boundary"] == "captcha_detected"
    assert sum("CAPTCHA" in warning or "hCaptcha" in warning for warning in form.warnings) == 1


def test_browser_verification_failure_fails_closed_for_lever():
    db = _db()
    job, _application = _job(db, source="lever", board="northwind", external_id=LEVER.posting_id, url=LEVER.apply_url)

    def broken(ref):
        raise FormFetchError("form_fetch_failed", "chromium missing")

    client = _client(lambda r: httpx.Response(200, text=load_ats_fixture("lever_apply.html")))
    with pytest.raises(FormFetchError) as excinfo:
        LiveFormProvider(Settings(_env_file=None, lever_browser_verify=True), client=client, inspector=broken).fetch(job)
    assert "browser verification is enabled but failed" in excinfo.value.detail
