"""Policy revalidation service for manual review tasks.

Automatically re-evaluates open review tasks against updated candidate profile facts,
the answer vault catalog, and material claims verification.
When blockers are satisfied, automatically marks the task resolved and promotes the
application to ready_to_apply without requiring manual button clicks for every item.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.autonomous_material_verification import verify_job_materials_claims
from app.config import Settings
from app.decisioning import Decision
from app.models import Application, Job, PipelineStage, ReviewTask
from app.pipeline import deterministic_gate, transition
from app.profile import verified_profile
from app.question_catalog import QUESTION_CATALOG

REVALIDATABLE_REASONS = frozenset({
    "decision_review",
    "ambiguous_question",
    "legal_answer_missing",
    "sensitive_answer_missing",
    "unsupported_control",
    "materials_review",
})


def _get_stored_answers(db: Session) -> dict[str, str]:
    """Fetch all known answers from the answer_policies table."""
    try:
        from app.models import AnswerPolicy
        rows = db.execute(select(AnswerPolicy.key, AnswerPolicy.value)).all()
        return {r[0]: r[1] for r in rows if r[0] and r[1]}
    except Exception:
        return {}


def revalidate_single_task(
    db: Session,
    task: ReviewTask,
    settings: Settings | None = None,
    stored_answers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Revalidate a single open ReviewTask against candidate facts and answers."""
    if task.status != "open":
        return {
            "task_id": task.id,
            "status": task.status,
            "revalidated": False,
            "resolved": False,
            "reason": f"Task is already {task.status}",
        }

    app = task.application or (db.get(Application, task.application_id) if task.application_id else None)
    job = (db.get(Job, task.job_id) if task.job_id else None) or (app.job if app else None)

    if stored_answers is None:
        stored_answers = _get_stored_answers(db)

    reason = task.reason_code
    can_resolve = False
    resolution_note = ""

    # 1. Decision Review Revalidation
    if reason == "decision_review":
        if job is not None:
            # Check if company is excluded
            blacklisted = set()
            if settings and getattr(settings, "blacklisted_company_list", None):
                blacklisted = {name.lower() for name in settings.blacklisted_company_list}
            if job.company.lower() in blacklisted:
                return {
                    "task_id": task.id,
                    "status": "open",
                    "revalidated": True,
                    "resolved": False,
                    "reason": f"Company {job.company} is on exclusion list",
                }

            # Re-evaluate job against current deterministic eligibility & targeting rules
            if settings is not None:
                report = deterministic_gate(job, settings)
                if report.decision == Decision.REJECT:
                    task.status = "rejected"
                    task.resolved_at = datetime.now(UTC)
                    task.resolution_note = f"Auto-rejected on revalidation: {report.reason}"
                    db.add(task)
                    if job.stage in {
                        PipelineStage.review.value,
                        PipelineStage.needs_review.value,
                        PipelineStage.shortlisted.value,
                        PipelineStage.ready_to_apply.value,
                        PipelineStage.validated.value,
                    }:
                        transition(db, job, PipelineStage.rejected, report.reason)
                    db.commit()
                    return {
                        "task_id": task.id,
                        "status": "rejected",
                        "revalidated": True,
                        "resolved": False,
                        "reason": report.reason,
                    }
                if report.decision == Decision.REVIEW:
                    return {
                        "task_id": task.id,
                        "status": "open",
                        "revalidated": True,
                        "resolved": False,
                        "reason": f"Still requires review: {report.reason}",
                    }

            # Check if profile is populated
            profile = verified_profile(db)
            if len(profile.facts) >= 5:
                can_resolve = True
                resolution_note = "Deterministic eligibility and profile facts confirmed; decision review satisfied"

    # 2. Materials Review Revalidation
    elif reason == "materials_review":
        if job is not None:
            claims_check = verify_job_materials_claims(db, job)
            if claims_check["truth_grounded"] and claims_check["unsupported_count"] == 0:
                can_resolve = True
                resolution_note = "Materials verified: 100% truth-grounded claims against profile"

    # 3. Question & Control Revalidations
    elif reason in {"ambiguous_question", "legal_answer_missing", "sensitive_answer_missing", "unsupported_control"}:
        from app.knowledge_book import KnowledgeBook
        from app.option_derivation import derive_option

        task_detail = task.detail or ""
        bfs: list[Any] = []
        if app and app.validation_json:
            try:
                vdata = json.loads(app.validation_json)
                bfs = vdata.get("blocked_fields") or []
            except Exception:
                bfs = []

        stored_lower = {k.lower(): v for k, v in stored_answers.items()}

        if bfs:
            kb = KnowledgeBook.from_database(db)
            all_resolved = True
            for bf in bfs:
                if isinstance(bf, dict):
                    if bf.get("required") is False:
                        continue
                    bk = str(bf.get("key") or bf.get("name") or "").lower()
                    blabel = str(bf.get("label") or bf.get("question") or bk)
                    bopts = list(bf.get("options") or [])
                    breason = str(bf.get("reason") or "")
                else:
                    bk = str(bf).lower()
                    blabel = str(bf)
                    bopts = []
                    breason = ""

                if "unsupported" in breason:
                    # Wait for form re-fetch / adapter support rather than guessing
                    all_resolved = False
                    break

                stored_val = None
                if bk:
                    if bk in stored_lower:
                        stored_val = stored_lower[bk]
                    else:
                        for sa, sv in stored_lower.items():
                            if len(sa) >= 4 and (bk in sa or sa in bk):
                                stored_val = sv
                                break

                if stored_val is not None:
                    if not bopts or derive_option(bk, stored_val, bopts) or kb._match_option(stored_val, bopts):
                        continue

                ans_val, _ans_reason = kb.resolve_screening_question(blabel, bopts or None)
                if ans_val is not None:
                    if not bopts or derive_option(bk or "custom", ans_val, bopts) or kb._match_option(str(ans_val), bopts):
                        continue

                all_resolved = False
                break

            if all_resolved:
                can_resolve = True
                resolution_note = f"All {len(bfs)} blocked field(s) resolved or optional-only"
        else:
            # Match against known catalog keys or answers when no structured blocked_fields list exists
            catalog_keys = {
                (item.get("canonical_key") or item.get("key")): (item.get("label") or item.get("canonical_question", ""))
                for item in QUESTION_CATALOG
                if isinstance(item, dict) and (item.get("canonical_key") or item.get("key"))
            }
            matched_key = None
            for k in stored_answers:
                if k in task_detail.lower() or (catalog_keys.get(k, "") and catalog_keys.get(k, "").lower() in task_detail.lower()):
                    matched_key = k
                    break

            if matched_key:
                can_resolve = True
                resolution_note = f"Answer found in vault for key '{matched_key}'"

    if can_resolve:
        task.status = "resolved"
        task.resolved_at = datetime.now(UTC)
        task.detail = f"{task.detail} [Auto-revalidated: {resolution_note}]"
        db.add(task)

        if app and app.validation_json:
            try:
                vdata = json.loads(app.validation_json)
                vdata["ok"] = True
                vdata["blocked_fields"] = []
                vdata["blockers"] = []
                app.validation_json = json.dumps(vdata)
                db.add(app)
            except Exception:
                pass

        promoted = False
        if job and job.stage == PipelineStage.needs_review.value:
            transition(db, job, PipelineStage.ready_to_apply, f"Revalidation resolved task {task.id}")
            promoted = True
        if app and app.stage == PipelineStage.needs_review.value:
            app.stage = PipelineStage.ready_to_apply.value
            db.add(app)
            promoted = True

        db.commit()
        return {
            "task_id": task.id,
            "status": "resolved",
            "revalidated": True,
            "resolved": True,
            "promoted_to_ready": promoted,
            "reason": resolution_note,
        }

    return {
        "task_id": task.id,
        "status": "open",
        "revalidated": True,
        "resolved": False,
        "reason": "Blocker conditions remain unresolved",
    }


def revalidate_open_tasks(
    db: Session,
    settings: Settings | None = None,
    task_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Scans and revalidates all or targeted open review tasks."""
    query = select(ReviewTask).where(ReviewTask.status == "open")
    if task_ids:
        query = query.where(ReviewTask.id.in_(list(task_ids)))

    tasks = list(db.execute(query).scalars().all())
    stored_answers = _get_stored_answers(db)

    results = []
    resolved_count = 0
    promoted_count = 0

    for task in tasks:
        outcome = revalidate_single_task(db, task, settings=settings, stored_answers=stored_answers)
        results.append(outcome)
        if outcome.get("resolved"):
            resolved_count += 1
        if outcome.get("promoted_to_ready"):
            promoted_count += 1

    return {
        "examined": len(tasks),
        "resolved": resolved_count,
        "retained": len(tasks) - resolved_count,
        "promoted_to_ready": promoted_count,
        "details": results,
    }
