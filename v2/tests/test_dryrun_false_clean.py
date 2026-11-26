"""A vault value for a field the rendered page never showed must not count as filled.

Recreates the Magnet Forensics (Lever) false clean from the #73 dry-run batch: the
API listed ``consent[marketing]``, the DOM-verified page never rendered it, and the
dry run still reported it as filled and moved the job to ``form_filled``. Offline.
"""
import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base
from app.flags import ensure_flags
from app.form_engine import ControlType, FormControl
from app.greenhouse_form import RealForm
from app.models import Application, Job, PipelineEvent, PipelineStage, ReviewTask, SubmissionEvidence
from app.pipeline import execute_apply
from app.vault_store import upsert_answer

# The Magnet field was a consent checkbox; a plain text field keeps this test independent of
# the consent-mapping fix (Truth Guard), and the pipeline rule is the same for any control.
NOT_RENDERED_BLOCKER = "field_not_rendered"
MARKETING = "urls[LinkedIn]"


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    ensure_flags(db)
    for key, value in {"first_name": "Test", "email": "test@example.invalid", "linkedin": "https://linkedin.example.invalid/in/test"}.items():
        upsert_answer(db, key=key, value=value, source="user", scope="global")
    job = Job(source="lever", external_id="magnet-1", title="Fixture", company="magnetforensics", location="Ottawa",
              url="https://jobs.lever.co/magnetforensics/magnet-1", stage=PipelineStage.ready_to_apply.value,
              platform="lever")
    db.add(job)
    db.commit()
    application = Application(job_id=job.id, mode="dry_run", adapter_name="lever")
    db.add(application)
    db.commit()
    return db, application


def _form(*, rendered_marketing: bool, dom_verified: bool = True) -> RealForm:
    controls = [
        FormControl(key="first_name", label="First name", control_type=ControlType.TEXT, required=True,
                    section="api", vault_keys=["first_name"]),
        FormControl(key="email", label="Email", control_type=ControlType.TEXT, required=True,
                    section="api", vault_keys=["email"]),
        FormControl(key=MARKETING, label="LinkedIn URL", required=False,
                    control_type=ControlType.TEXT, section="api", vault_keys=["linkedin"]),
    ]
    form = RealForm(platform="lever", source="lever_api", url="https://jobs.lever.co/magnetforensics/magnet-1",
                    title="Fixture", controls=controls)
    if dom_verified:
        form.metadata["dom_verified"] = True
        form.metadata["api_fields_not_rendered"] = [] if rendered_marketing else [MARKETING]
    return form


def _run(db, application, form):
    class Provider:
        def fetch(self, _job):
            return form

    return execute_apply(db, Settings(_env_file=None, automation_enabled=True), application.id, form_provider=Provider())


def test_magnet_unrendered_vault_value_is_not_filled_and_goes_to_review():
    db, application = _db()
    result = _run(db, application, _form(rendered_marketing=False))
    assert result["status"] == "needs_review" and result["submitted"] is False
    assert NOT_RENDERED_BLOCKER in result["blockers"] and MARKETING in result["blocked_fields"]
    assert "filled" not in result
    db.refresh(application)
    assert application.stage != PipelineStage.form_filled.value
    assert application.stage != PipelineStage.validated.value
    assert MARKETING not in json.loads(application.validation_json)["planned"]
    stages = [event.to_stage for event in db.execute(select(PipelineEvent)).scalars()]
    assert PipelineStage.form_filled.value not in stages
    assert [row.kind for row in db.execute(select(SubmissionEvidence)).scalars()] == ["dry_run_review"]
    assert len(db.execute(select(ReviewTask)).scalars().all()) == 1


def test_rendered_fields_still_complete_the_dry_run():
    db, application = _db()
    result = _run(db, application, _form(rendered_marketing=True))
    assert result["status"] == "dry_run_complete" and result["submitted"] is False
    assert MARKETING in result["filled"]


def test_unverified_forms_are_unchanged():
    """No DOM check ran, so there is no rendered/not-rendered signal to act on."""
    db, application = _db()
    result = _run(db, application, _form(rendered_marketing=False, dom_verified=False))
    assert result["status"] == "dry_run_complete"
