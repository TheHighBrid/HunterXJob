"""Ashby discovery normalization, canonical identity, and cross-board duplicate links.

Offline: board payloads are sanitized snapshots (tests/fixtures/ats).
"""
import httpx
import pytest
from conftest import load_ats_json
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import discovery
from app.config import Settings
from app.db import Base
from app.dedup import (
    canonical_id,
    compare,
    duplicate_guard,
    fingerprint,
    group_ids,
    linked_postings,
    normalize_company,
    normalize_title,
    unlink,
)
from app.discovery import JobRecord, ashby_jobs, ashby_record, discover_all, lever_jobs, upsert_records
from app.flags import ensure_flags
from app.models import Application, ApplicationMaterial, Job, JobLink, PipelineStage, ReviewTask, SubmissionEvidence
from app.review_queue import open_task

LONG = ("Investigate fraud alerts, follow AML and compliance controls, document evidence, support credit-card disputes, "
        "communicate with customers in English and French, partner with banking operations and payment-risk teams, "
        "and improve the controls that keep clients safe across Canada every single day of the week.")


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    ensure_flags(db)
    return db


def _record(external_id, *, source="greenhouse", board="maplebank", title="Bilingual Fraud Analyst",
            location="Toronto, Ontario, Canada", description=LONG, company=None):
    return JobRecord(source=source, external_id=external_id, title=title, company=company or board, location=location,
                     remote=False, url=f"https://example.test/{source}/{board}/{external_id}", description=description, board=board)


def _fake_get(payload, seen=None):
    def get(url, **kwargs):
        if seen is not None:
            seen.append((url, kwargs))
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))
    return get


# --- Ashby / Lever discovery -------------------------------------------------------------------

def test_ashby_board_postings_are_normalized_like_other_sources(monkeypatch):
    seen = []
    monkeypatch.setattr(discovery.httpx, "get", _fake_get(load_ats_json("ashby_board.json"), seen))
    records = ashby_jobs("northwind")
    assert seen[0][0] == "https://api.ashbyhq.com/posting-api/job-board/northwind"
    assert seen[0][1]["follow_redirects"] is False
    # The unlisted posting is skipped.
    assert len(records) == 4
    first = records[0]
    assert first.source == "ashby" and first.board == "northwind" and first.company == "northwind"
    assert first.location == "Toronto; Canada"
    assert first.remote is True
    assert first.url == f"https://jobs.ashbyhq.com/northwind/{first.external_id}"
    assert first.description.startswith("About the role")
    assert first.canonical_id == f"ashby:northwind:{first.external_id}"


def test_ashby_record_falls_back_to_cleaned_html_and_workplace_type():
    record = ashby_record("acme", {"id": "1", "title": "Analyst", "location": "Ottawa", "workplaceType": "Remote",
                                   "descriptionHtml": "<p>Hello <b>world</b></p>", "jobUrl": "https://jobs.ashbyhq.com/acme/1"})
    assert record.remote is True and record.description == "Hello world"
    assert ashby_record("acme", {"id": "2", "isListed": False}) is None


def test_lever_postings_carry_board_and_remote_workplace_type(monkeypatch):
    monkeypatch.setattr(discovery.httpx, "get", _fake_get(load_ats_json("lever_postings.json")))
    records = lever_jobs("northwind")
    assert len(records) == 3
    assert all(record.board == "northwind" and record.canonical_id.startswith("lever:northwind:") for record in records)
    assert records[2].remote is True


@pytest.mark.parametrize("slug", ["../etc", "a b", "", "x" * 101, "evil.com/path"])
def test_board_slugs_are_validated_before_any_request(slug, monkeypatch):
    monkeypatch.setattr(discovery.httpx, "get", lambda *a, **k: pytest.fail("no request expected"))
    with pytest.raises(ValueError):
        ashby_jobs(slug)


def test_discover_all_includes_ashby_and_isolates_failures(monkeypatch):
    calls = []
    monkeypatch.setattr(discovery, "_FETCHERS", {
        "greenhouse": lambda board: calls.append(("greenhouse", board)) or [],
        "lever": lambda board: (_ for _ in ()).throw(httpx.ConnectError("down")),
        "ashby": lambda board: calls.append(("ashby", board)) or [_record("a1", source="ashby", board=board)],
    })
    errors = []
    settings = Settings(_env_file=None, greenhouse_board_tokens="maple", lever_companies="globex", ashby_orgs="northwind")
    records = discover_all(settings, errors)
    assert calls == [("greenhouse", "maple"), ("ashby", "northwind")]
    assert [record.source for record in records] == ["ashby"]
    assert errors and errors[0].startswith("lever:globex:")
    assert settings.source_counts()["ashby_orgs"] == 1


# --- canonical identity ----------------------------------------------------------------------

def test_canonical_ids_are_stable_and_board_scoped():
    assert canonical_id("ashby", "Northwind", "abc") == "ashby:northwind:abc"
    assert canonical_id("greenhouse", "d2l", "7696196") == "greenhouse:d2l:7696196"
    db = _db()
    upsert_records(db, [_record("1")])
    upsert_records(db, [_record("1", title="Bilingual Fraud Analyst II")])
    rows = db.execute(select(Job)).scalars().all()
    assert len(rows) == 1 and rows[0].canonical_id == "greenhouse:maplebank:1" and rows[0].title.endswith("II")
    assert rows[0].last_seen_at is not None


def test_normalizers_ignore_legal_suffixes_and_location_suffixes():
    assert normalize_company("Maple Bank Inc.") == normalize_company("maplebank")
    assert normalize_title("Sr. Fraud Analyst - Toronto", "Toronto, ON") == normalize_title("Senior Fraud Analyst", "Montreal")


# --- duplicate detection ----------------------------------------------------------------------

def test_same_role_on_two_boards_is_linked_not_deleted():
    db = _db()
    upsert_records(db, [_record("gh-1")])
    result = upsert_records(db, [_record("ash-1", source="ashby", board="maplebank", title="Bilingual Fraud Analyst - Montreal",
                                         location="Montreal, Quebec")])
    assert result.added == 1 and result.duplicates == 1
    primary = db.execute(select(Job).where(Job.external_id == "gh-1")).scalar_one()
    duplicate = db.execute(select(Job).where(Job.external_id == "ash-1")).scalar_one()
    assert duplicate.stage == PipelineStage.duplicate.value and duplicate.duplicate_of_id == primary.id
    link = db.execute(select(JobLink)).scalar_one()
    assert (link.job_id, link.primary_job_id, link.method, link.status) == (duplicate.id, primary.id, "exact", "linked")
    assert db.execute(select(Job)).scalars().all().__len__() == 2
    assert group_ids(db, duplicate) == {primary.id, duplicate.id}
    assert linked_postings(db, primary)[0]["relation"] == "duplicate"
    assert linked_postings(db, duplicate)[0]["relation"] == "primary"


FULL_POSTING = " ".join([
    LONG,
    "You will review transaction monitoring alerts, escalate suspicious activity, and write clear case notes.",
    "You will work with investigators, product managers, and engineers to tune detection rules and reduce false positives.",
    "You will help train new analysts, maintain playbooks, and keep documentation current for auditors and regulators.",
    "We offer a hybrid schedule, a learning budget, comprehensive health benefits, and a supportive team culture.",
    "Applicants should have two or more years of experience in fraud, AML, KYC, or a related operations role.",
    "Strong written communication, attention to detail, and comfort with spreadsheets and SQL are important.",
    "We welcome applicants from all backgrounds and provide accommodations throughout the hiring process.",
])


def test_fuzzy_match_tolerates_small_description_edits():
    a = Job(title="Bilingual Fraud Analyst", company="maplebank", location="Toronto", description=FULL_POSTING)
    b = Job(title="Bilingual Fraud Analyst", company="maplebank", location="Toronto",
            description=FULL_POSTING.replace("a hybrid schedule, a learning budget", "a flexible hybrid schedule"))
    fingerprint(a)
    fingerprint(b)
    match = compare(a, b)
    assert match is not None and match.method == "fuzzy"


@pytest.mark.parametrize("other", [
    {"title": "Senior Bilingual Fraud Analyst"},                    # level differs
    {"title": "Bilingual Fraud Analyst II"},                        # number differs
    {"title": "Bilingual Compliance Analyst"},                      # different role
    {"title": "Bilingual Fraud Analyst - Payments"},                # extra non-place title word
    {"description": "Build mobile apps in Kotlin and Swift for consumers " * 8},
])
def test_different_roles_are_not_linked(other):
    a = Job(title="Bilingual Fraud Analyst", company="maplebank", location="Toronto", description=LONG)
    b = Job(**{"title": "Bilingual Fraud Analyst", "company": "maplebank", "location": "Toronto", "description": LONG, **other})
    fingerprint(a)
    fingerprint(b)
    assert compare(a, b) is None


def test_fuzzy_match_allows_titles_that_differ_only_by_region():
    a = Job(title="Fraud Analyst (France)", company="maplebank", location="Paris; France", description=FULL_POSTING)
    b = Job(title="Fraud Analyst (Middle East)", company="maplebank", location="Dubai; Middle East",
            description=FULL_POSTING.replace("a hybrid schedule, a learning budget", "a flexible hybrid schedule"))
    fingerprint(a)
    fingerprint(b)
    match = compare(a, b)
    assert match is not None and match.method == "fuzzy"
    c = Job(title="Fraud Analyst - NATO", company="maplebank", location="London", description=b.description)
    fingerprint(c)
    assert compare(b, c) is None


def _rejected_primary_setup():
    db = _db()
    upsert_records(db, [_record("us", location="New York, NY")])
    upsert_records(db, [_record("ca", source="lever", location="Toronto, Ontario, Canada"),
                        _record("mtl", source="ashby", location="Montreal, Quebec, Canada")])
    primary = db.execute(select(Job).where(Job.external_id == "us")).scalar_one()
    copies = db.execute(select(Job).where(Job.external_id.in_(["ca", "mtl"])).order_by(Job.discovered_at)).scalars().all()
    assert all(copy.duplicate_of_id == primary.id for copy in copies)
    return db, primary, copies


@pytest.mark.parametrize("stage", [PipelineStage.rejected, PipelineStage.withdrawn])
def test_rejecting_the_primary_releases_its_copies_and_regroups_them(stage):
    from app.pipeline import transition

    db, primary, copies = _rejected_primary_setup()
    # e.g. the US posting fails the location gate: the Canadian copies must not stay hidden behind it.
    if stage is PipelineStage.withdrawn:
        primary.stage = PipelineStage.shortlisted.value
    transition(db, primary, stage, "gate rejected / owner withdrew")
    first, second = (db.get(Job, copy.id) for copy in copies)
    assert first.stage == PipelineStage.discovered.value and first.duplicate_of_id is None
    # The copies are still the same role as each other: one primary, never prepared twice.
    assert second.duplicate_of_id == first.id and second.stage == PipelineStage.duplicate.value
    statuses = {link.job_id: link.status for link in db.execute(select(JobLink).where(JobLink.primary_job_id == primary.id))
                .scalars()}
    assert set(statuses.values()) == {"released"}
    # A new copy of the role is never linked to the rejected primary.
    upsert_records(db, [_record("van", source="greenhouse", board="maplebank", location="Vancouver, BC")])
    newcomer = db.execute(select(Job).where(Job.external_id == "van")).scalar_one()
    assert newcomer.duplicate_of_id == first.id


def test_closing_the_primary_releases_its_copies():
    from datetime import UTC, datetime, timedelta

    from app.job_liveness import GONE, CheckContext, Observation, record_observation

    db, primary, copies = _rejected_primary_setup()
    settings = Settings(_env_file=None)
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    gone = Observation(GONE, "ats_api_not_found", 404, "removed")
    record_observation(db, settings, primary, gone, CheckContext("test", now=start))
    record_observation(db, settings, primary, gone, CheckContext("test", now=start + timedelta(hours=1)))
    assert primary.stage == PipelineStage.closed.value
    first = db.get(Job, copies[0].id)
    assert first.duplicate_of_id is None and first.stage == PipelineStage.discovered.value


def test_different_employers_are_never_linked():
    db = _db()
    upsert_records(db, [_record("1"), _record("2", board="globex")])
    assert db.execute(select(JobLink)).first() is None


def test_title_and_location_match_needs_a_shared_place_when_descriptions_are_short():
    db = _db()
    upsert_records(db, [_record("1", description="Short.", location="Ottawa, ON"),
                        _record("2", source="lever", description="Short.", location="Ottawa, Ontario")])
    link = db.execute(select(JobLink)).scalar_one()
    assert link.method == "title_location"
    db2 = _db()
    upsert_records(db2, [_record("1", description="Short.", location="Ottawa"),
                         _record("2", source="lever", description="Short.", location="Vancouver")])
    assert db2.execute(select(JobLink)).first() is None


def test_earliest_posting_is_primary_and_unlink_regates():
    db = _db()
    upsert_records(db, [_record("old", description="Short.")])
    upsert_records(db, [_record("new", source="lever", board="maplebank", description="Short.")])
    # Both start fresh; the earliest discovered stays primary.
    old = db.execute(select(Job).where(Job.external_id == "old")).scalar_one()
    new = db.execute(select(Job).where(Job.external_id == "new")).scalar_one()
    assert new.duplicate_of_id == old.id
    # Owner unlinks; later a third posting arrives while "new" has an application and an open task.
    unlink(db, new)
    assert new.stage == PipelineStage.discovered.value and new.duplicate_of_id is None
    new.stage = PipelineStage.shortlisted.value
    db.add(Application(job_id=new.id, mode="dry_run"))
    db.commit()
    open_task(db, reason_code="ambiguous_question", title="t", job=new, hold=False)
    upsert_records(db, [_record("third", source="ashby", board="maplebank", description="Short.")])
    third = db.execute(select(Job).where(Job.external_id == "third")).scalar_one()
    # A newcomer never displaces a posting that is already being worked on.
    assert third.stage == PipelineStage.duplicate.value
    assert third.duplicate_of_id in {old.id, new.id}


def test_unlinked_pairs_are_not_relinked():
    db = _db()
    upsert_records(db, [_record("1"), _record("2", source="lever")])
    duplicate = db.execute(select(Job).where(Job.external_id == "2")).scalar_one()
    unlink(db, duplicate)
    from app.dedup import link_if_duplicate

    assert link_if_duplicate(db, duplicate) is None
    assert db.execute(select(JobLink)).scalar_one().status == "unlinked"


def test_linking_closes_the_demoted_postings_open_review_tasks():
    db = _db()
    upsert_records(db, [_record("1")])
    first = db.execute(select(Job)).scalar_one()
    db.add(_job := Job(source="lever", board="maplebank", external_id="2", title="Bilingual Fraud Analyst", company="maplebank",
                       location="Toronto", url="https://jobs.lever.co/x", description=LONG, canonical_id="lever:maplebank:2"))
    db.commit()
    open_task(db, reason_code="ambiguous_question", title="question", job=_job, hold=False)
    fingerprint(_job)
    from app.dedup import link_if_duplicate

    link_if_duplicate(db, _job)
    task = db.execute(select(ReviewTask).where(ReviewTask.job_id == _job.id)).scalar_one()
    assert task.status == "auto_closed" and "duplicate of" in task.detail
    assert _job.duplicate_of_id == first.id


# --- duplicate guard ---------------------------------------------------------------------------

def _pair(db):
    upsert_records(db, [_record("1"), _record("2", source="ashby")])
    primary = db.execute(select(Job).where(Job.external_id == "1")).scalar_one()
    duplicate = db.execute(select(Job).where(Job.external_id == "2")).scalar_one()
    return primary, duplicate


def test_guard_refuses_linked_duplicates():
    db = _db()
    primary, duplicate = _pair(db)
    assert duplicate_guard(db, primary, "prepare") is None
    assert "duplicate of greenhouse:maplebank:1" in duplicate_guard(db, duplicate, "dry_run")


def _did_work(db, job):
    application = Application(job_id=job.id, mode="dry_run")
    db.add(application)
    db.commit()
    db.add(ApplicationMaterial(application_id=application.id, job_id=job.id, kind="resume", version=1, status="draft",
                               content_json="{}", text="x", content_sha256="0" * 64, profile_sha256="0" * 64))
    db.add(SubmissionEvidence(application_id=application.id, kind="dry_run"))
    db.commit()


def test_guard_allows_the_primary_to_redo_its_own_work():
    db = _db()
    primary, _duplicate = _pair(db)
    _did_work(db, primary)
    assert duplicate_guard(db, primary, "prepare") is None
    assert duplicate_guard(db, primary, "dry_run") is None


def test_guard_refuses_a_second_prepare_or_dry_run_for_the_same_role():
    db = _db()
    primary, duplicate = _pair(db)
    # The duplicate posting was already prepared and dry-run (e.g. it was in flight when the link was made).
    _did_work(db, duplicate)
    assert "already did this" in duplicate_guard(db, primary, "prepare")
    assert "already did this" in duplicate_guard(db, primary, "dry_run")


def test_example_canadian_boards_file_is_valid_and_not_enabled_by_default():
    from pathlib import Path

    from app.discovery import BOARD_SLUG_RE
    from app.runtime_settings import SettingsPatch

    path = Path(__file__).resolve().parents[1] / "examples" / "boards.canada.example.env"
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    values = dict(line.split("=", 1) for line in lines)
    assert set(values) == {"GREENHOUSE_BOARD_TOKENS", "LEVER_COMPANIES", "ASHBY_ORGS"}
    for raw in values.values():
        assert all(BOARD_SLUG_RE.match(item) for item in raw.split(","))
    # The same slugs pass the phone settings validation.
    SettingsPatch(ashby_orgs=values["ASHBY_ORGS"].split(","), lever_companies=values["LEVER_COMPANIES"].split(","),
                  greenhouse_board_tokens=values["GREENHOUSE_BOARD_TOKENS"].split(","))
    defaults = Settings(_env_file=None)
    assert defaults.greenhouse_board_list == [] and defaults.lever_company_list == [] and defaults.ashby_org_list == []


def test_scan_fingerprints_legacy_rows_and_links_oldest_first():
    from app.dedup import scan

    db = _db()
    for external_id, source in (("1", "greenhouse"), ("2", "lever")):
        db.add(Job(source=source, board="maplebank", external_id=external_id, title="Bilingual Fraud Analyst",
                   company="maplebank", location="Toronto", url="", description=LONG))
        db.commit()
    assert scan(db) == {"fingerprinted": 2, "linked": 1}
    assert scan(db) == {"fingerprinted": 0, "linked": 0}
    rows = {job.external_id: job for job in db.execute(select(Job)).scalars()}
    assert rows["2"].duplicate_of_id == rows["1"].id
