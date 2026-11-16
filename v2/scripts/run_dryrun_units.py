#!/usr/bin/env python3
"""Run automated Playwright browser dry-runs across newly added units.

Platforms tested:
1. Low Difficulty:
   - Workable (apply.workable.com)
   - Breezy HR (breezy.hr)
   - Recruitee (recruitee.com)
   - Lever (jobs.lever.co)
   - Greenhouse (boards.greenhouse.io)

2. Canadian Government & Public Sector Portals 🇨🇦:
   - GC Jobs (Federal - jobs.gc.ca)
   - City of Ottawa Careers (Municipal - ottawa.ca)
   - Ontario Public Service (Provincial - gojobs.gov.on.ca)
   - Ville de Gatineau (Municipal Outaouais - gatineau.ca)
   - Québec Emploi (Provincial Québec - quebecemploi.gouv.qc.ca)

Strict safety contract:
- Operates strictly in dry-run mode (allow_live_submit=False).
- Locates form controls, types values, autocompletes location, checks options, attaches resume PDF.
- Performs DOM read-back verification.
- Enforces Pre-submission Truth Guard.
- Generates pre-submit screenshot and SHA-256 evidence.
- NEVER clicks final submit (submitted=False guaranteed).
"""
from __future__ import annotations

import argparse
import asyncio
import http.server
import json
import logging
import os
import sys
import threading
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

# Add v2 directory to sys.path
V2_DIR = Path(__file__).resolve().parents[1]
if str(V2_DIR) not in sys.path:
    sys.path.insert(0, str(V2_DIR))

from app.browser_filler import fill_application_form
from app.truth_guard import audit_form_answers, enforce_answer_truthfulness

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("hunterx.dryrun_new_units")

FIXTURES_DIR = V2_DIR / "tests" / "fixtures"


class _FixtureHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FIXTURES_DIR), **kwargs)

    def log_message(self, format, *args):
        pass


def _start_fixture_server() -> tuple[http.server.ThreadingHTTPServer, str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    host, port = server.server_address
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, f"http://{host}:{port}"


TARGET_UNITS = [
    {
        "platform": "workable",
        "category": "Commercial ATS (Low Difficulty)",
        "fixture": "/ats/workable_apply.html",
        "answers": {
            "name": "Jordan Lee",
            "email": "jordan.lee@example.com",
            "phone": "+1 613 555 0199",
            "address": "Ottawa, Ontario, Canada",
            "summary": "Experienced engineer excited to contribute to high-reliability platforms.",
            "work_auth": "Yes",
        },
    },
    {
        "platform": "breezy",
        "category": "Commercial ATS (Low Difficulty)",
        "fixture": "/ats/breezy_apply.html",
        "answers": {
            "name": "Jordan Lee",
            "email": "jordan.lee@example.com",
            "phone": "+1 613 555 0199",
            "address": "Ottawa, ON, Canada",
            "cover_letter": "Passionate about building scalable distributed systems.",
            "eligible_canada": "Yes",
        },
    },
    {
        "platform": "recruitee",
        "category": "Commercial ATS (Low Difficulty)",
        "fixture": "/ats/recruitee_apply.html",
        "answers": {
            "name": "Jordan Lee",
            "email": "jordan.lee@example.com",
            "phone": "+1 613 555 0199",
            "cover_letter": "Strong background in cloud native engineering.",
            "open_question": "Excited about modern architecture and continuous automation.",
        },
    },
    {
        "platform": "lever",
        "category": "Commercial ATS (Low Difficulty)",
        "fixture": "/ats/lever_apply.html",
        "answers": {
            "name": "Jordan Lee",
            "email": "jordan.lee@example.com",
            "phone": "+1 613 555 0199",
            "location": "Ottawa, Ontario, Canada",
            "org": "Public Sector Innovation Corp",
            "urls[LinkedIn]": "https://www.linkedin.com/in/jordanlee",
        },
    },
    {
        "platform": "greenhouse",
        "category": "Commercial ATS (Low Difficulty)",
        "fixture": "/ats/greenhouse_apply.html",
        "answers": {
            "first_name": "Jordan",
            "last_name": "Lee",
            "email": "jordan.lee@example.com",
            "phone": "+1 613 555 0199",
            "location": "Ottawa, Ontario, Canada",
            "work_authorization": "Yes",
        },
    },
    {
        "platform": "gc_jobs",
        "category": "Canadian Public Sector 🇨🇦 (Federal)",
        "fixture": "/ats/gc_jobs_apply.html",
        "answers": {
            "first_name": "Jordan",
            "last_name": "Lee",
            "email": "jordan.lee@example.com",
            "phone": "+1 613 555 0199",
            "location": "Ottawa, Ontario, Canada",
            "canadian_citizenship": "Canadian Citizen / Citoyen canadien",
            "language_proficiency": "Bilingual / Bilingue (English & French / Anglais et Français)",
            "security_clearance": "Reliability Status / Cote de fiabilité",
        },
    },
    {
        "platform": "ottawa_careers",
        "category": "Canadian Public Sector 🇨🇦 (Municipal Ottawa)",
        "fixture": "/ats/ottawa_apply.html",
        "answers": {
            "first_name": "Jordan",
            "last_name": "Lee",
            "email": "jordan.lee@example.com",
            "phone": "+1 613 555 0199",
            "location": "Ottawa, Ontario, Canada",
            "language_proficiency": "Bilingual / Bilingue (English & French / Anglais et Français)",
            "ottawa_resident": "Yes / Oui",
        },
    },
    {
        "platform": "ops_careers",
        "category": "Canadian Public Sector 🇨🇦 (Provincial Ontario)",
        "fixture": "/ats/ops_apply.html",
        "answers": {
            "first_name": "Jordan",
            "last_name": "Lee",
            "email": "jordan.lee@example.com",
            "phone": "+1 613 555 0199",
            "location": "Ottawa, Ontario, Canada",
            "canadian_citizenship": "Canadian Citizen / Citoyen canadien",
            "ontario_resident": "Yes / Oui",
        },
    },
    {
        "platform": "gatineau_careers",
        "category": "Canadian Public Sector 🇨🇦 (Municipal Outaouais)",
        "fixture": "/ats/gatineau_apply.html",
        "answers": {
            "first_name": "Jordan",
            "last_name": "Lee",
            "email": "jordan.lee@example.com",
            "phone": "+1 613 555 0199",
            "location": "Gatineau, QC, Canada",
            "canadian_citizenship": "Canadian Citizen / Citoyen canadien",
            "language_proficiency": "Bilingual / Bilingue (English & French / Anglais et Français)",
            "outaouais_resident": "Yes / Oui",
        },
    },
    {
        "platform": "quebec_emploi",
        "category": "Canadian Public Sector 🇨🇦 (Provincial Québec)",
        "fixture": "/ats/quebec_emploi_apply.html",
        "answers": {
            "first_name": "Jordan",
            "last_name": "Lee",
            "email": "jordan.lee@example.com",
            "phone": "+1 613 555 0199",
            "location": "Gatineau, QC, Canada",
            "canadian_citizenship": "Canadian Citizen / Citoyen canadien",
            "language_proficiency": "Bilingual / Bilingue (English & French / Anglais et Français)",
            "quebec_resident": "Yes / Oui",
        },
    },
]


async def _run_single_dryrun(
    unit: dict,
    base_url: str,
    out_dir: Path,
    resume_path: Path,
    headless: bool,
    pause_sec: int,
) -> dict:
    platform = unit["platform"]
    url = f"{base_url}{unit['fixture']}"
    answers = dict(unit["answers"])
    sc_path = out_dir / f"{platform}_presubmit.png"

    logger.info("Executing dry-run for %s (%s)...", platform, unit["category"])

    res = await fill_application_form(
        url=url,
        answers=answers,
        files={"resume": resume_path},
        screenshot_path=sc_path,
        headless=headless,
        headed_pause_ms=pause_sec * 1000,
        allow_live_submit=False,
    )

    passed = res.success and not res.submitted and len(res.unmatched_fields) == 0
    return {
        "platform": platform,
        "category": unit["category"],
        "passed": passed,
        "submitted": res.submitted,
        "fields_attempted": res.fields_attempted,
        "fields_filled": res.fields_filled,
        "unmatched_fields": res.unmatched_fields,
        "files_attached": len(res.files_attached),
        "screenshot_sha256": res.screenshot_sha256,
        "screenshot_path": str(sc_path) if sc_path.exists() else None,
        "error": res.error,
    }


async def run_all_dryruns(
    headless: bool = True,
    pause_sec: int = 2,
    out_dir: Path | None = None,
) -> dict:
    server, base_url = _start_fixture_server()
    out = out_dir or Path("/tmp/hunterx_dryruns")
    out.mkdir(parents=True, exist_ok=True)

    # Prepare dummy resume
    resume_path = out / "sample_resume.pdf"
    resume_path.write_bytes(b"%PDF-1.4 HunterXJob Dry-Run Verification Resume")

    results = []
    try:
        for unit in TARGET_UNITS:
            r = await _run_single_dryrun(unit, base_url, out, resume_path, headless, pause_sec)
            results.append(r)
    finally:
        server.shutdown()
        server.server_close()

    total = len(results)
    passed_count = sum(1 for r in results if r["passed"])
    dossier = {
        "timestamp": datetime.now(UTC).isoformat(),
        "total_units": total,
        "passed": passed_count,
        "failed": total - passed_count,
        "pass_rate": (passed_count / total) * 100.0,
        "dry_run_safety_guarantee": all(not r["submitted"] for r in results),
        "units": results,
    }

    report_path = out / "dryrun_new_units_dossier.json"
    report_path.write_text(json.dumps(dossier, indent=2), encoding="utf-8")
    dossier["report_path"] = str(report_path)
    return dossier


def main() -> int:
    parser = argparse.ArgumentParser(description="HunterXJob v2 Dry-Run Verification on Newly Added Units")
    parser.add_argument("--headed", action="store_true", help="run browser visibly to inspect form filling")
    parser.add_argument("--pause", type=int, default=2, help="pause duration in seconds between actions in headed mode")
    parser.add_argument("--out", type=Path, default=Path("/tmp/hunterx_dryruns"), help="output directory for screenshots and report")
    args = parser.parse_args()

    print("\n" + "=" * 76)
    print("      HUNTERXJOB v2 | AUTOMATED DRY-RUN VERIFICATION ON NEW UNITS")
    print("=" * 76)

    dossier = asyncio.run(run_all_dryruns(headless=not args.headed, pause_sec=args.pause, out_dir=args.out))

    print(f"{'Platform':<18} | {'Category':<32} | {'Fields':<8} | {'Safe':<6} | {'Status'}")
    print("-" * 76)
    for u in dossier["units"]:
        safe_str = "YES" if not u["submitted"] else "NO (SUBMITTED!)"
        status_str = "✓ PASS" if u["passed"] else "✗ FAIL"
        fields_str = f"{u['fields_filled']}/{u['fields_attempted']}"
        print(f"{u['platform']:<18} | {u['category']:<32} | {fields_str:<8} | {safe_str:<6} | {status_str}")

    print("-" * 76)
    print(f"Total: {dossier['total_units']} | Passed: {dossier['passed']} | Failed: {dossier['failed']} | Pass Rate: {dossier['pass_rate']:.1f}%")
    print(f"Dry-Run Safety Guarantee: {'ENFORCED (No submissions made)' if dossier['dry_run_safety_guarantee'] else 'VIOLATED'}")
    print(f"Report written to: {dossier['report_path']}")
    print("=" * 76 + "\n")

    return 0 if dossier["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
