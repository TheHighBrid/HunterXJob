from app.adapter_runtime import GREENHOUSE_FIXTURE, detect_platform, plan_for_html
from app.answer_vault import AnswerRecord, AnswerSource, AnswerVault
from app.form_engine import ControlType, detect_handoff, parse_controls, plan_fill, verify_filled
from app.models import AdapterMaturity


def test_parse_greenhouse_fixture_controls():
    controls = parse_controls(GREENHOUSE_FIXTURE)
    keys = {control.key for control in controls}
    assert {"first_name", "last_name", "email", "work_authorization", "resume"} <= keys
    work = next(control for control in controls if control.key == "work_authorization")
    assert work.control_type is ControlType.SELECT
    assert work.sensitive is True
    assert work.required is True


def test_plan_stops_on_missing_sensitive_answer():
    controls = parse_controls(GREENHOUSE_FIXTURE)
    vault = AnswerVault([
        AnswerRecord("first_name", "Mo", AnswerSource.USER),
        AnswerRecord("last_name", "Alem", AnswerSource.USER),
        AnswerRecord("email", "mo@example.test", AnswerSource.USER),
        AnswerRecord("resume", "/tmp/resume.pdf", AnswerSource.USER),
    ])
    plan = plan_fill(controls, vault)
    assert plan.ready is False
    assert "sensitive_answer_missing" in plan.blockers


def test_plan_fills_when_policy_answers_exist():
    controls = parse_controls(GREENHOUSE_FIXTURE)
    vault = AnswerVault([
        AnswerRecord("first_name", "Mo", AnswerSource.USER),
        AnswerRecord("last_name", "Alem", AnswerSource.USER),
        AnswerRecord("email", "mo@example.test", AnswerSource.USER),
        AnswerRecord("phone", "555-0100", AnswerSource.USER),
        AnswerRecord("resume", "/tmp/resume.pdf", AnswerSource.USER),
        AnswerRecord("cover_letter", "Hello", AnswerSource.USER),
        AnswerRecord("work_authorization", "Authorized to work in Canada", AnswerSource.POLICY),
    ])
    plan = plan_fill(controls, vault)
    assert plan.ready is True
    filled = {item.control.key: item.value for item in plan.items if item.status == "fill"}
    assert filled["work_authorization"] == "Authorized to work in Canada"


def test_verify_filled_and_captcha_handoff():
    from app.form_engine import FormControl

    control = FormControl(key="email", label="Email", control_type=ControlType.EMAIL)
    assert verify_filled(control, "a@b.c", "a@b.c")
    assert not verify_filled(control, "a@b.c", "nope")
    assert detect_handoff("Please complete the captcha to continue") == "captcha_detected"
    assert detect_platform("https://boards.greenhouse.io/acme/jobs/123") == "greenhouse"
    assert detect_platform("https://jobs.lever.co/acme/abc") == "lever"


def test_plan_for_html_ready_path():
    vault = AnswerVault([
        AnswerRecord("first_name", "Mo", AnswerSource.USER),
        AnswerRecord("last_name", "Alem", AnswerSource.USER),
        AnswerRecord("email", "mo@example.test", AnswerSource.USER),
        AnswerRecord("phone", "555-0100", AnswerSource.USER),
        AnswerRecord("resume", "/tmp/resume.pdf", AnswerSource.USER),
        AnswerRecord("cover_letter", "Hello", AnswerSource.USER),
        AnswerRecord("work_authorization", "Authorized to work in Canada", AnswerSource.POLICY),
    ])
    handoff, plan = plan_for_html(GREENHOUSE_FIXTURE, vault)
    assert handoff is None
    assert plan.ready is True
    assert AdapterMaturity.certified_autonomous.value == "certified_autonomous"
