"""``./hunterx profile ...`` and ``./hunterx materials ...`` commands."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import get_settings


@contextmanager
def _session() -> Iterator[Session]:
    from app.db import SessionLocal, init_db

    init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _print(data: object) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


# ----------------------------------------------------------------------- profile


def _profile_import(args: argparse.Namespace) -> int:
    from app.profile import ProfileError, import_profile_document, parse_profile_text

    path = Path(args.file)
    fmt = "json" if path.suffix.lower() == ".json" else "yaml"
    try:
        document = parse_profile_text(path.read_text(encoding="utf-8"), fmt)
        with _session() as db:
            result = import_profile_document(db, document, origin=path.name)
    except (OSError, ProfileError) as exc:
        return _fail(f"import failed: {exc}")
    _print(result.as_dict())
    return 0


def _profile_import_resume(args: argparse.Namespace) -> int:
    from app.profile import ProfileError, store_drafts
    from app.resume_import import extract_text, parse_resume_text

    path = Path(args.file)
    try:
        text, source = extract_text(path)
        drafts, warnings = parse_resume_text(text, origin=path.name)
        with _session() as db:
            result = store_drafts(db, drafts, source)
    except ProfileError as exc:
        return _fail(f"import failed: {exc}")
    result.warnings.extend(warnings)
    _print({**result.as_dict(), "note": "résumé facts are UNVERIFIED drafts; review and verify each one"})
    return 0


def _profile_list(args: argparse.Namespace) -> int:
    from app.profile import list_facts, readiness, verified_profile

    with _session() as db:
        rows = [row for row in list_facts(db) if not (args.unverified and row.verified)]
        for row in rows:
            mark = "✔" if row.verified else "·"
            print(f"{mark} {row.key:<48} {row.data_json[:90]}  [{row.source}: {row.provenance[:40]}]")
        missing = readiness(verified_profile(db))
    print(f"\n{len(rows)} fact(s). " + ("Profile ready for materials." if not missing else "Missing: " + "; ".join(missing)))
    return 0


def _set_verified(args: argparse.Namespace, verified: bool) -> int:
    from app.models import ProfileFact

    with _session() as db:
        rows = db.execute(select(ProfileFact).where(or_(ProfileFact.key.in_(args.keys), ProfileFact.id.in_(args.keys)))).scalars().all()
        for row in rows:
            row.verified = verified
        db.commit()
        found = {row.key for row in rows} | {row.id for row in rows}
    missing = [key for key in args.keys if key not in found]
    print(f"{'verified' if verified else 'unverified'} {len(rows)} fact(s)" + (f"; not found: {', '.join(missing)}" if missing else ""))
    return 1 if missing else 0


# --------------------------------------------------------------------- materials


def _application_for(db: Session, ident: str):
    from app.models import Application

    return db.get(Application, ident) or db.execute(select(Application).where(Application.job_id == ident)).scalar_one_or_none()


def _materials_generate(args: argparse.Namespace) -> int:
    from app.material_store import MaterialsError, material_view
    from app.material_workflow import generate_for_application

    with _session() as db:
        application = _application_for(db, args.id)
        if application is None:
            return _fail("no application for that job/application id (shortlist the job first)")
        try:
            rows = generate_for_application(db, get_settings(), application.id)
        except MaterialsError as exc:
            return _fail(exc.detail)
        _print([material_view(row) for row in rows])
    return 0


def _materials_list(args: argparse.Namespace) -> int:
    from app.material_store import material_view, materials_for

    with _session() as db:
        application = _application_for(db, args.id)
        if application is None:
            return _fail("no application for that job/application id")
        _print([material_view(row) for row in materials_for(db, application.id)])
    return 0


def _materials_decide(args: argparse.Namespace, approve: bool) -> int:
    from app.material_store import MaterialsError, material_view
    from app.material_workflow import approve_material, reject_material

    with _session() as db:
        try:
            row = (approve_material if approve else reject_material)(db, args.material_id, args.note)
        except MaterialsError as exc:
            return _fail(exc.detail)
        _print(material_view(row))
    return 0


def _materials_preview(args: argparse.Namespace) -> int:
    """Build a résumé + cover letter for a hypothetical job from the verified profile (files only, nothing stored)."""
    from app.materials import JobContext, build_cover_letter, build_resume, cover_letter_text, resume_text
    from app.profile import readiness, verified_profile
    from app.render import render_docx_bytes, render_pdf_bytes, write_file
    from app.truth_guard import check_cover_letter, check_resume

    description = Path(args.description_file).read_text(encoding="utf-8") if args.description_file else ""
    job = JobContext(title=args.title, company=args.company, description=description)
    with _session() as db:
        profile = verified_profile(db)
    if missing := readiness(profile):
        return _fail("profile is not ready: " + "; ".join(missing))
    resume = build_resume(profile, job)
    letter = build_cover_letter(profile, job, resume)
    problems = check_resume(resume, profile) + check_cover_letter(letter, profile, job_description=job.text)
    if problems:
        return _fail("truthfulness guard failed: " + "; ".join(problems))
    out = Path(args.out)
    for name, doc, text in (("resume", resume, resume_text(resume)), ("cover_letter", letter, cover_letter_text(letter))):
        write_file(out / f"{name}.pdf", render_pdf_bytes(doc))
        write_file(out / f"{name}.docx", render_docx_bytes(doc))
        write_file(out / f"{name}.txt", text.encode("utf-8"))
    print(f"wrote résumé and cover letter (pdf, docx, txt) to {out}")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    profile = sub.add_parser("profile", help="verified-facts profile: import, list, verify").add_subparsers(dest="action", required=True)
    commands: list[tuple[argparse._SubParsersAction, str, str, Callable[[argparse.Namespace], int]]] = [
        (profile, "import", "import a YAML/JSON profile file", _profile_import),
        (profile, "import-resume", "parse a .txt/.md/.pdf/.docx résumé into UNVERIFIED facts", _profile_import_resume),
        (profile, "list", "list facts (✔ = verified)", _profile_list),
        (profile, "verify", "mark facts verified (by key or id)", lambda args: _set_verified(args, True)),
        (profile, "unverify", "mark facts unverified (by key or id)", lambda args: _set_verified(args, False)),
    ]
    materials = sub.add_parser("materials", help="résumé/cover-letter drafts per job").add_subparsers(dest="action", required=True)
    commands += [
        (materials, "generate", "draft materials for a job or application id", _materials_generate),
        (materials, "list", "list material versions for a job or application id", _materials_list),
        (materials, "approve", "approve one draft version", lambda args: _materials_decide(args, True)),
        (materials, "reject", "reject one version", lambda args: _materials_decide(args, False)),
        (materials, "preview", "render materials for a hypothetical job (nothing stored)", _materials_preview),
    ]
    for group, name, help_text, handler in commands:
        parser = group.add_parser(name, help=help_text)
        parser.set_defaults(handler=handler)
        _arguments(parser, group is profile, name)


def _arguments(parser: argparse.ArgumentParser, is_profile: bool, name: str) -> None:
    if is_profile and name in {"import", "import-resume"}:
        parser.add_argument("file")
    elif is_profile and name in {"verify", "unverify"}:
        parser.add_argument("keys", nargs="+")
    elif is_profile:
        parser.add_argument("--unverified", action="store_true", help="only show unverified facts")
    elif name in {"generate", "list"}:
        parser.add_argument("id", help="job id or application id")
    elif name in {"approve", "reject"}:
        parser.add_argument("material_id")
        parser.add_argument("--note", default="")
    else:
        parser.add_argument("--title", required=True)
        parser.add_argument("--company", default="")
        parser.add_argument("--description-file")
        parser.add_argument("--out", default="data/materials/preview")
