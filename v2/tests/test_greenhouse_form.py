"""Real Greenhouse form extraction, vault mapping, and fail-closed fetching.

All tests are offline: payloads come from sanitized fixtures and HTTP is
served by httpx.MockTransport.
"""
import httpx
import pytest
from conftest import load_fixture_json
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import adapter_runtime
from app.answer_vault import AnswerRecord, AnswerSource, AnswerVault
from app.config import Settings
from app.db import Base
from app.flags import ensure_flags, set_flag
from app.form_engine import ControlType, plan_fill
from app.greenhouse_browser import DomSnapshot, form_from_dom, reconcile
from app.greenhouse_form import (
    FormFetchError,
    GreenhouseJobRef,
    classify_question,
    fetch_greenhouse_form,
    parse_greenhouse_payload,
    parse_greenhouse_ref,
    ref_for_job,
)
from app.models import Application, Job, PipelineStage, ReviewTask, SubmissionEvidence
from app.pipeline import execute_apply
from app.real_forms import LiveFormProvider
from app.vault_store import load_vault, upsert_answer

ASANA = GreenhouseJobRef("asana", "8165477")
GITLAB = GreenhouseJobRef("gitlab", "8556658002")
D2L = GreenhouseJobRef("d2l", "7696196")


def _form(name, ref):
    return parse_greenhouse_payload(load_fixture_json(name), ref)


def _by_key(form):
    return {control.key: control for control in form.controls}


def _vault(**answers):
    return AnswerVault([AnswerRecord(key, value, AnswerSource.USER) for key, value in answers.items()])


IDENTITY = {
    "first_name": "Test", "last_name": "Candidate", "email": "candidate@example.test",
    "phone": "555-0100", "resume": "~/hunterx/resume.pdf",
}


# --- refs -------------------------------------------------------------------

@pytest.mark.parametrize("url, expected", [
    ("https://boards.greenhouse.io/cloudflare/jobs/7695702?gh_jid=7695702", ("cloudflare", "7695702")),
    ("https://job-boards.greenhouse.io/gitlab/jobs/8556658002", ("gitlab", "8556658002")),
    ("https://job-boards.greenhouse.io/embed/job_app?for=asana&token=8165477", ("asana", "8165477")),
])
def test_parse_greenhouse_ref_from_hosted_urls(url, expected):
    ref = parse_greenhouse_ref(url)
    assert (ref.board, ref.job_id) == expected


def test_parse_greenhouse_ref_needs_board_hint_for_custom_career_pages():
    url = "https://www.asana.com/jobs/apply/8165477?gh_jid=8165477"
    assert parse_greenhouse_ref(url) is None
    ref = parse_greenhouse_ref(url, board_hint="asana")
    assert ref == ASANA
    assert ref.api_url == "https://boards-api.greenhouse.io/v1/boards/asana/jobs/8165477"


def test_ref_for_job_trusts_discovery_fields_only_for_greenhouse_source():
    job = Job(source="greenhouse", external_id="8165477", company="asana", url="https://asana.com/jobs/apply/8165477", title="t")
    assert ref_for_job(job) == ASANA
    other = Job(source="generic", external_id="8165477", company="asana", url="https://asana.com/jobs/apply/8165477", title="t")
    assert ref_for_job(other) is None


def test_invalid_refs_are_rejected():
    assert parse_greenhouse_ref("https://boards.greenhouse.io/bad token/jobs/12") is None
    with pytest.raises(ValueError):
        GreenhouseJobRef("../etc", "1")


# --- extraction ---------------------------------------------------------------

def test_extracts_standard_custom_location_demographic_and_consent_fields():
    form = _form("asana_8165477.json", ASANA)
    controls = _by_key(form)
    assert form.source == "greenhouse_api"
    assert form.title == "Administrative Business Partner"
    assert controls["first_name"].control_type is ControlType.TEXT and controls["first_name"].required
    assert controls["email"].control_type is ControlType.EMAIL
    assert controls["phone"].control_type is ControlType.TEL
    # file + alternative textarea: only the primary is required
    assert controls["resume"].control_type is ControlType.FILE and controls["resume"].required
    assert controls["resume_text"].control_type is ControlType.TEXTAREA and not controls["resume_text"].required
    # custom questions keep their real field names and get canonical vault keys
    assert controls["question_68886292"].vault_keys == ["question_68886292", "current_company"]
    assert controls["question_68886294"].vault_keys == ["question_68886294", "linkedin_url"]
    # sponsorship "in this location" is scoped to the job's country (Vancouver, BC)
    sponsorship = controls["question_68886298"]
    assert sponsorship.control_type is ControlType.SELECT
    assert sponsorship.options == ["Yes", "No"]
    assert sponsorship.vault_keys[-1] == "sponsorship_ca"
    assert sponsorship.sensitive
    # employer-specific and legal questions get no shared key
    assert controls["question_68886299"].vault_keys == ["question_68886299"]
    assert controls["question_68886303"].legal and controls["question_68886303"].vault_keys == ["question_68886303"]
    assert controls["question_68886300"].legal and controls["question_68886300"].sensitive  # export control / citizenship
    # conditional follow-up is never shared
    assert controls["question_68886297"].vault_keys == ["question_68886297"]
    # location section
    assert controls["location"].control_type is ControlType.AUTOCOMPLETE and controls["location"].section == "location"
    assert controls["latitude"].control_type is ControlType.HIDDEN
    # demographic question is sensitive and voluntary
    gender = controls["demographic_52921"]
    assert gender.section == "demographic" and gender.sensitive and gender.required
    assert "I don't wish to answer" in gender.options
    assert gender.vault_keys == ["demographic_52921", "gender"]
    # GDPR demographic consent derived from data_compliance
    consent = controls["gdpr_demographic_data_consent_given"]
    assert consent.control_type is ControlType.CHECKBOX and consent.legal and consent.required


def test_extracts_eeoc_compliance_block_as_sensitive_voluntary_fields():
    controls = _by_key(_form("gitlab_8556658002.json", GITLAB))
    for key in ("veteran_status", "race", "gender"):
        assert controls[key].section == "eeoc"
        assert controls[key].sensitive
        assert not controls[key].required
    assert "Decline To Self Identify" in controls["race"].options
    assert controls["question_36622861002"].vault_keys[-1] == "country_of_residence"
    assert controls["question_36622859002"].vault_keys[-1] == "sponsorship_residence_country"
    agreements = controls["question_36622857002"]
    assert agreements.legal and agreements.vault_keys == ["question_36622857002"]


def test_extracts_multiselect_country_scoped_auth_and_salary():
    controls = _by_key(_form("d2l_7696196.json", D2L))
    language = controls["question_64283526"]  # API name is "question_64283526[]"
    assert language.control_type is ControlType.MULTISELECT and len(language.options) == 6
    auth = controls["question_64283527"]
    assert auth.vault_keys[-1] == "work_authorization_ca" and auth.sensitive
    assert controls["question_64283530"].vault_keys[-1] == "salary_expectation"
    assert controls["question_64283530"].sensitive
    assert controls["question_64283538"].legal and controls["question_64283538"].options == ["Yes"]


def test_classify_question_never_shares_ambiguous_country_answers():
    assert classify_question("Are you authorized to work in the United States?") == "work_authorization_us"
    assert classify_question("Are you authorized to work in Canada?") == "work_authorization_ca"
    assert classify_question("Are you authorized to work?") is None
    assert classify_question("Are you authorized to work in Canada or the United States?") is None
    assert classify_question("Do you require sponsorship to work in this role?", job_location="Remote") is None
    assert classify_question("Have you previously worked for Example?") is None


def test_unknown_field_type_is_unsupported_and_blocks():
    payload = load_fixture_json("d2l_7696196.json")
    payload["questions"].append({"label": "Draw your signature", "required": True, "fields": [{"name": "question_1", "type": "signature_pad", "values": []}]})
    form = parse_greenhouse_payload(payload, D2L)
    plan = plan_fill(form.controls, _vault(**IDENTITY))
    assert "unsupported_control" in plan.blockers
    assert any("not support" in warning for warning in form.warnings)


def test_payload_without_questions_is_rejected():
    with pytest.raises(FormFetchError) as exc:
        parse_greenhouse_payload({"title": "x"}, D2L)
    assert exc.value.reason_code == "form_fetch_failed"


# --- vault mapping ----------------------------------------------------------------

def test_maps_vault_answers_onto_real_form_and_normalizes_option_case():
    form = _form("d2l_7696196.json", D2L)
    vault = AnswerVault([
        *[AnswerRecord(k, v, AnswerSource.USER) for k, v in IDENTITY.items()],
        AnswerRecord("work_authorization_ca", "yes", AnswerSource.POLICY),
        AnswerRecord("willing_to_relocate", "I am already living near this location", AnswerSource.USER),
        AnswerRecord("salary_expectation", "70K+", AnswerSource.USER),
        AnswerRecord("referral_source", "Other", AnswerSource.USER),
        AnswerRecord("time_zone", "GMT-5 :Eastern Standard Time (EST)", AnswerSource.USER),
        AnswerRecord("question_64283526", "C1: Full professional proficiency|C2: Bilingual/Native speaker", AnswerSource.USER),
    ])
    plan = plan_fill(form.controls, vault)
    filled = {item.control.key: item.value for item in plan.items if item.status == "fill"}
    assert filled["first_name"] == "Test"
    assert filled["question_64283527"] == "Yes"  # "yes" matched to the listed option label
    assert filled["question_64283526"] == ["C1: Full professional proficiency", "C2: Bilingual/Native speaker"]
    assert filled["question_64283530"] == "70K+"
    review = {item.control.key for item in plan.items if item.status == "review"}
    # unmapped required custom questions and the legal consent still stop the plan
    assert {"question_64283529", "question_64283531", "question_65529047", "question_64283538"} <= review
    assert plan.ready is False


def test_global_work_authorization_answer_is_not_reused_for_a_country_question():
    form = _form("d2l_7696196.json", D2L)
    plan = plan_fill(form.controls, _vault(**IDENTITY, work_authorization="Yes"))
    item = next(item for item in plan.items if item.control.key == "question_64283527")
    assert item.status == "review"
    assert "sensitive_answer_missing" in plan.blockers


def test_sensitive_answer_from_resume_is_not_accepted():
    form = _form("d2l_7696196.json", D2L)
    vault = AnswerVault([AnswerRecord("work_authorization_ca", "Yes", AnswerSource.RESUME)])
    item = next(item for item in plan_fill(form.controls, vault).items if item.control.key == "question_64283527")
    assert item.status == "review"


def test_option_not_listed_goes_to_review():
    form = _form("d2l_7696196.json", D2L)
    plan = plan_fill(form.controls, _vault(**IDENTITY, salary_expectation="85000"))
    item = next(item for item in plan.items if item.control.key == "question_64283530")
    assert item.status == "review" and "not a listed option" in item.reason


def test_unknown_required_question_stops_for_review():
    form = _form("gitlab_8556658002.json", GITLAB)
    plan = plan_fill(form.controls, _vault(**IDENTITY, country_of_residence="Canada", sponsorship_residence_country="No"))
    bangalore = next(item for item in plan.items if item.control.key == "question_36667493002")
    assert bangalore.status == "review"
    assert plan.ready is False
    assert "ambiguous_question" in plan.blockers


def test_voluntary_fields_use_only_an_explicit_decline_policy():
    form = _form("gitlab_8556658002.json", GITLAB)
    without_policy = plan_fill(form.controls, _vault(**IDENTITY))
    assert "sensitive_answer_missing" in without_policy.blockers
    assert all(item.status == "review" for item in without_policy.items if item.control.section == "eeoc")

    with_policy = plan_fill(form.controls, _vault(**IDENTITY, voluntary_self_identification="decline"))
    eeoc = {item.control.key: item for item in with_policy.items if item.control.section == "eeoc"}
    assert eeoc["race"].value == "Decline To Self Identify"
    assert eeoc["gender"].value == "Decline To Self Identify"
    assert eeoc["veteran_status"].value == "I don't wish to answer"
    assert all(item.status == "fill" for item in eeoc.values())

    imported = AnswerVault([AnswerRecord("voluntary_self_identification", "decline", AnswerSource.IMPORTED)])
    assert all(item.status == "review" for item in plan_fill(form.controls, imported).items if item.control.section == "eeoc")


def test_explicit_demographic_answer_wins_over_decline_policy():
    form = _form("gitlab_8556658002.json", GITLAB)
    plan = plan_fill(form.controls, _vault(**IDENTITY, gender="female", voluntary_self_identification="decline"))
    gender = next(item for item in plan.items if item.control.key == "gender")
    assert gender.value == "Female"


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    ensure_flags(db)
    return db


def test_employer_scoped_per_question_answer_overrides_global():
    db = _db()
    upsert_answer(db, key="question_36667493002", value="Yes", scope="global")
    upsert_answer(db, key="question_36667493002", value="No", scope="greenhouse:gitlab")
    form = _form("gitlab_8556658002.json", GITLAB)
    plan = plan_fill(form.controls, load_vault(db, form.vault_scopes))
    item = next(item for item in plan.items if item.control.key == "question_36667493002")
    assert item.status == "fill" and item.value == "No"


# --- fetching ---------------------------------------------------------------

def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_fetch_uses_read_only_get_against_the_public_boards_api():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=load_fixture_json("asana_8165477.json"))

    form = fetch_greenhouse_form(ASANA, client=_client(handler))
    assert len(form.controls) > 20
    assert [r.method for r in seen] == ["GET"]
    assert seen[0].url.host == "boards-api.greenhouse.io"
    assert seen[0].url.path == "/v1/boards/asana/jobs/8165477"
    assert seen[0].url.params["questions"] == "true"


@pytest.mark.parametrize("response, reason", [
    (httpx.Response(404, json={"status": 404}), "form_unavailable"),
    (httpx.Response(500, text="boom"), "form_fetch_failed"),
    (httpx.Response(301, headers={"Location": "https://example.test"}), "form_fetch_failed"),
    (httpx.Response(200, text="<html>not json</html>"), "form_fetch_failed"),
    (httpx.Response(200, json={"title": "no questions"}), "form_fetch_failed"),
])
def test_fetch_failures_are_flagged_not_faked(response, reason):
    with pytest.raises(FormFetchError) as exc:
        fetch_greenhouse_form(ASANA, client=_client(lambda request: response))
    assert exc.value.reason_code == reason


def test_network_error_is_flagged():
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(FormFetchError) as exc:
        fetch_greenhouse_form(ASANA, client=_client(handler))
    assert exc.value.reason_code == "form_fetch_failed"


# --- browser verification (offline snapshot) ----------------------------------------

def test_dom_snapshot_ignores_captcha_and_widget_internals():
    snapshot = DomSnapshot.from_dict(load_fixture_json("asana_8165477_dom.json"))
    ids = {item.id for item in snapshot.fields}
    assert "iti-0__search-input" not in ids
    assert not any(item.name == "g-recaptcha-response" for item in snapshot.fields)
    assert snapshot.captcha is True
    assert snapshot.challenge is None


def test_reconcile_adds_required_page_only_fields_and_records_captcha_boundary():
    form = reconcile(_form("asana_8165477.json", ASANA), DomSnapshot.from_dict(load_fixture_json("asana_8165477_dom.json")))
    controls = _by_key(form)
    # The phone-country picker is required on the page but absent from the API.
    assert "phone_country" in form.metadata["dom_only_required_fields"]
    assert controls["phone_country"].section == "dom"
    # the page's location combobox and demographic widget map onto API fields
    assert "location" not in form.metadata["dom_only_required_fields"]
    assert "demographic_52921" not in form.metadata["dom_only_required_fields"]
    assert form.metadata["submit_boundary"] == "captcha_detected"
    assert form.handoff is None  # submit-time CAPTCHA does not block a dry-run plan
    plan = plan_fill(form.controls, _vault(**IDENTITY, phone_country="Canada"))
    phone_country = next(item for item in plan.items if item.control.key == "phone_country")
    assert phone_country.status == "review"  # options can't be verified without interacting


def test_challenge_page_becomes_a_handoff():
    snapshot = DomSnapshot.from_dict({"url": "u", "title": "t", "form_count": 0, "captcha": False,
                                      "body_text": "Checking your browser before accessing", "fields": []})
    form = form_from_dom(ASANA, snapshot)
    assert form.handoff == "anti_bot_challenge"


def test_dom_only_fallback_cannot_verify_combobox_options():
    form = form_from_dom(ASANA, DomSnapshot.from_dict(load_fixture_json("asana_8165477_dom.json")))
    assert form.source == "greenhouse_dom"
    plan = plan_fill(form.controls, _vault(**IDENTITY, question_68886298="No"))
    assert "unsupported_control" in plan.blockers


# --- pipeline integration ------------------------------------------------------------

class _FixtureProvider:
    def __init__(self, form=None, error=None):
        self.form, self.error, self.calls = form, error, 0

    def fetch(self, job):
        self.calls += 1
        if self.error:
            raise self.error
        return self.form


def _greenhouse_job(db, *, url="https://job-boards.greenhouse.io/d2l/jobs/7696196", source="greenhouse", platform="greenhouse"):
    job = Job(source=source, external_id="7696196", title="Bilingual Product Support Analyst", company="d2l",
              location="Toronto, Ontario", url=url, description="Support", stage=PipelineStage.ready_to_apply.value,
              platform=platform)
    db.add(job)
    db.commit()
    application = Application(job_id=job.id, mode="dry_run", adapter_name=platform, cover_letter_text="Hi")
    db.add(application)
    db.commit()
    return job, application


def _answer_everything_d2l(db):
    answers = {**IDENTITY,
               "work_authorization_ca": "Yes", "willing_to_relocate": "Yes", "salary_expectation": "70K+",
               "referral_source": "Other", "time_zone": "GMT-5 :Eastern Standard Time (EST)",
               "question_64283526": "C2: Bilingual/Native speaker", "question_64283529": "Yes",
               "question_64283531": "4 - daily SQL for reporting", "question_65529047": "No, I have not worked for D2L in any capacity",
               "question_64283538": "Yes"}
    for key, value in answers.items():
        upsert_answer(db, key=key, value=value, source="user", scope="global")


def test_dry_run_uses_the_real_form_and_never_submits():
    db = _db()
    _answer_everything_d2l(db)
    _job, application = _greenhouse_job(db)
    provider = _FixtureProvider(form=_form("d2l_7696196.json", D2L))
    settings = Settings(automation_enabled=True, application_mode="autonomous", allow_live_submission=True)
    set_flag(db, "allow_live_submission", True)
    set_flag(db, "unattended_mode", True)

    result = execute_apply(db, settings, application.id, form_provider=provider)
    db.refresh(application)

    assert provider.calls == 1
    assert result["status"] == "dry_run_complete"
    assert result["form_source"] == "greenhouse_api"
    assert result["submitted"] is False
    assert "question_64283527" in result["filled"]
    assert application.stage == PipelineStage.validated.value
    evidence = db.execute(select(SubmissionEvidence)).scalars().all()
    assert [row.kind for row in evidence] == ["dry_run"]
    assert evidence[0].sufficient is False


def test_blocked_real_form_lists_the_blocking_fields():
    db = _db()
    for key, value in IDENTITY.items():
        upsert_answer(db, key=key, value=value)
    _job, application = _greenhouse_job(db)
    result = execute_apply(db, Settings(automation_enabled=True), application.id,
                           form_provider=_FixtureProvider(form=_form("d2l_7696196.json", D2L)))
    assert result["status"] == "needs_review"
    assert "question_64283527" in result["blocked_fields"]
    task = db.execute(select(ReviewTask)).scalars().one()
    assert "Are you legally eligible to work in Canada" in task.detail
    assert not db.execute(select(SubmissionEvidence)).scalars().all()


def test_fetch_failure_flags_review_and_does_not_fall_back_to_a_sample():
    db = _db()
    for key, value in IDENTITY.items():
        upsert_answer(db, key=key, value=value)
    job, application = _greenhouse_job(db)
    provider = _FixtureProvider(error=FormFetchError("form_fetch_failed", "could not reach Greenhouse"))

    result = execute_apply(db, Settings(automation_enabled=True), application.id, form_provider=provider)
    db.refresh(application)
    db.refresh(job)

    assert result["status"] == "needs_review"
    assert result["reason"] == "form_fetch_failed"
    assert result["form_source"] is None
    assert result["submitted"] is False
    assert application.stage == PipelineStage.needs_review.value
    assert job.stage == PipelineStage.needs_review.value
    assert "could not reach Greenhouse" in application.last_error
    task = db.execute(select(ReviewTask)).scalars().one()
    assert task.reason_code == "form_fetch_failed"
    assert "no sample form was used" in task.detail
    assert not db.execute(select(SubmissionEvidence)).scalars().all()


def test_default_live_provider_fetches_via_the_api_for_discovered_jobs():
    db = _db()
    _answer_everything_d2l(db)
    # Custom career-page URL: the board token comes from discovery (company=d2l).
    _job, application = _greenhouse_job(db, url="https://www.d2l.com/careers/jobs/?job_id=7696196&gh_jid=7696196", platform=None)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=load_fixture_json("d2l_7696196.json"))

    provider = LiveFormProvider(Settings(automation_enabled=True), client=_client(handler))
    result = execute_apply(db, Settings(automation_enabled=True), application.id, form_provider=provider)
    assert result["status"] == "dry_run_complete"
    assert [str(r.url) for r in requests] == ["https://boards-api.greenhouse.io/v1/boards/d2l/jobs/7696196?questions=true"]


def test_live_provider_refuses_platforms_without_a_real_form_fetcher():
    db = _db()
    _job, application = _greenhouse_job(db, url="https://jobs.lever.co/acme/abc", source="lever", platform="lever")
    result = execute_apply(db, Settings(automation_enabled=True), application.id)
    assert result["status"] == "needs_review"
    assert result["reason"] == "form_unavailable"
    assert "sample form" in result["detail"]


def test_browser_verification_failure_fails_closed():
    db = _db()
    _job, application = _greenhouse_job(db)

    def broken_inspector(ref):
        raise FormFetchError("form_fetch_failed", "Chromium missing")

    provider = LiveFormProvider(
        Settings(automation_enabled=True, greenhouse_browser_verify=True),
        client=_client(lambda request: httpx.Response(200, json=load_fixture_json("d2l_7696196.json"))),
        inspector=broken_inspector,
    )
    result = execute_apply(db, Settings(automation_enabled=True), application.id, form_provider=provider)
    assert result["reason"] == "form_fetch_failed"
    assert "browser verification" in result["detail"]


def test_browser_fallback_only_when_enabled():
    db = _db()
    job, application = _greenhouse_job(db, url="https://job-boards.greenhouse.io/asana/jobs/8165477")
    job.company, job.external_id = "asana", "8165477"
    db.commit()
    down = _client(lambda request: httpx.Response(503))
    snapshot = DomSnapshot.from_dict(load_fixture_json("asana_8165477_dom.json"))
    off = execute_apply(db, Settings(automation_enabled=True), application.id,
                        form_provider=LiveFormProvider(Settings(), client=down, inspector=lambda ref: snapshot))
    assert off["reason"] == "form_fetch_failed"

    provider = LiveFormProvider(Settings(greenhouse_browser_fallback=True), client=down, inspector=lambda ref: snapshot)
    form = provider.fetch(job)
    assert form.source == "greenhouse_dom"
    assert "rendered page" in form.warnings[0]


def test_sample_form_is_no_longer_reachable_from_app_code():
    assert not hasattr(adapter_runtime, "GREENHOUSE_FIXTURE")


def test_api_only_forms_are_marked_unverified():
    provider = LiveFormProvider(Settings(), client=_client(lambda request: httpx.Response(200, json=load_fixture_json("d2l_7696196.json"))))
    job = Job(source="greenhouse", external_id="7696196", company="d2l", url="https://job-boards.greenhouse.io/d2l/jobs/7696196", title="t")
    form = provider.fetch(job)
    assert form.metadata["dom_verified"] is False
    assert any("not cross-checked" in warning for warning in form.warnings)


def test_form_preview_endpoint_is_read_only_and_redacts_sensitive_values(monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    from app import main

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    ensure_flags(db)
    for key, value in {**IDENTITY, "work_authorization_ca": "Yes"}.items():
        upsert_answer(db, key=key, value=value)
    job, _application = _greenhouse_job(db)
    job_id = job.id

    class _Provider:
        def __init__(self, settings):
            pass

        def fetch(self, job):
            return _form("d2l_7696196.json", D2L)

    def _get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(main, "LiveFormProvider", _Provider)
    main.app.dependency_overrides[main.get_db] = _get_db
    try:
        response = TestClient(main.app).get(f"/api/jobs/{job_id}/form")
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    assert body["form"]["source"] == "greenhouse_api"
    assert body["ready"] is False
    fields = {field["key"]: field for field in body["fields"]}
    assert fields["first_name"]["value"] == "Test"
    assert fields["question_64283527"]["status"] == "fill"
    assert fields["question_64283527"]["value"] == "[set]"
    check = Session()
    assert check.get(Job, job_id).stage == PipelineStage.ready_to_apply.value
    assert not check.execute(select(ReviewTask)).scalars().all()
    assert not check.execute(select(SubmissionEvidence)).scalars().all()
