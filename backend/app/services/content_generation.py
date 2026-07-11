"""AI content generation: cover letters and application emails via the pluggable LLM provider.

Prompt templates live inline as Jinja2 strings (few-shot style guidance baked
into the system prompt) — kept in this module rather than separate .j2 files
since they're prompts, not documents to render to HTML/PDF.
"""
from __future__ import annotations

from typing import Any, NamedTuple

from jinja2 import Template

from app.services.llm.base import LLMProvider

_COVER_LETTER_SYSTEM_PROMPT = """You are an expert career coach writing concise, honest, \
specific cover letters. Rules:
- 3-4 short paragraphs, no more than 320 words total.
- Open by naming the role and company, and one concrete reason you're a fit.
- Reference 2-3 specific skills/experiences from the resume that map to the job description's \
requirements — never invent experience that isn't in the resume.
- Avoid generic filler ("I am a hard worker", "I would be a great addition"). Be specific and grounded.
- Close with a brief, confident call to action.
- Do not include a letterhead, date, or "Dear Hiring Manager" — just the body paragraphs. \
The caller adds the header separately.
- Plain text only, no markdown formatting.

Example tone (do not copy content, only the register):
"When I led the migration of our billing pipeline to event-driven processing at Acme Corp, \
throughput tripled and on-call pages dropped by half. Your posting for a Senior Backend Engineer \
emphasizes exactly this kind of distributed-systems ownership, and it's the work I want to keep doing."
"""

_COVER_LETTER_PROMPT_TEMPLATE = Template(
    """Write a cover letter for this application.

CANDIDATE RESUME SUMMARY:
{{ resume_summary }}

CANDIDATE KEY SKILLS:
{{ skills }}

JOB TITLE: {{ job.title }}
COMPANY: {{ job.company }}
JOB DESCRIPTION:
{{ job.description }}

Write only the cover letter body paragraphs."""
)

_EMAIL_SYSTEM_PROMPT = """You write short, professional job-application emails. Rules:
- Subject line: "Application for <Role> - <Candidate Name>" style, under 80 characters.
- Body: 2-3 short paragraphs. Reference the attached resume and cover letter. Mention the role \
and company by name. Professional, warm, not obsequious.
- No markdown, plain text email body.
- Output format EXACTLY as two lines:
Subject: <subject line>
---
<email body>
"""

_EMAIL_PROMPT_TEMPLATE = Template(
    """Candidate name: {{ profile.full_name }}
Job title: {{ job.title }}
Company: {{ job.company }}

Cover letter (for reference/context, do not repeat verbatim):
{{ cover_letter }}

Write the application email subject and body now, following the exact output format."""
)


def _resume_summary_text(resume: dict[str, Any]) -> str:
    parts = []
    if resume.get("summary"):
        parts.append(resume["summary"])
    for job in resume.get("experience", []) or []:
        title = job.get("title", "")
        company = job.get("company", "")
        bullets = "; ".join(job.get("bullets", []) or [])
        parts.append(f"{title} at {company}: {bullets}")
    return "\n".join(parts)


def generate_cover_letter(
    profile: dict[str, Any],
    resume: dict[str, Any],
    job_posting: dict[str, Any],
    llm: LLMProvider,
) -> str:
    """Generate a tailored cover letter body (plain text) for `job_posting`."""
    prompt = _COVER_LETTER_PROMPT_TEMPLATE.render(
        resume_summary=_resume_summary_text(resume),
        skills=", ".join(resume.get("skills", []) or []),
        job=job_posting,
    )
    result = llm.generate(prompt, system=_COVER_LETTER_SYSTEM_PROMPT)
    return result.strip()


class GeneratedEmail(NamedTuple):
    subject: str
    body: str


def generate_application_email(
    profile: dict[str, Any],
    resume: dict[str, Any],
    job_posting: dict[str, Any],
    cover_letter: str,
    llm: LLMProvider,
) -> GeneratedEmail:
    """Generate a subject + body for a direct-email application."""
    prompt = _EMAIL_PROMPT_TEMPLATE.render(
        profile=profile, job=job_posting, cover_letter=cover_letter
    )
    raw = llm.generate(prompt, system=_EMAIL_SYSTEM_PROMPT).strip()

    subject = f"Application for {job_posting.get('title', 'the role')} - {profile.get('full_name', '')}"
    body = raw

    if "---" in raw:
        head, _, tail = raw.partition("---")
        subject_line = head.strip()
        if subject_line.lower().startswith("subject:"):
            subject = subject_line.split(":", 1)[1].strip() or subject
        body = tail.strip()
    elif raw.lower().startswith("subject:"):
        first_line, _, rest = raw.partition("\n")
        subject = first_line.split(":", 1)[1].strip() or subject
        body = rest.strip()

    if not body:
        body = raw

    return GeneratedEmail(subject=subject, body=body)
