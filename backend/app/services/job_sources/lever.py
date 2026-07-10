"""Lever public Postings API adapter (no scraping, no ToS issue).

API docs: https://github.com/lever/postings-api
Endpoint: https://api.lever.co/v0/postings/{company}?mode=json
"""
from __future__ import annotations

import re

import httpx

from app.services.job_sources.base import JobPostingDTO, JobSourceAdapter

_BASE_URL = "https://api.lever.co/v0/postings/{company}"


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


class LeverAdapter(JobSourceAdapter):
    def __init__(self, companies: list[str], timeout: float = 20.0):
        self.companies = companies
        self.timeout = timeout

    def search(self, query: str = "", location: str = "") -> list[JobPostingDTO]:
        results: list[JobPostingDTO] = []
        with httpx.Client(timeout=self.timeout) as client:
            for company in self.companies:
                url = _BASE_URL.format(company=company)
                try:
                    resp = client.get(url, params={"mode": "json"})
                    resp.raise_for_status()
                except httpx.HTTPError:
                    continue
                postings = resp.json()
                if not isinstance(postings, list):
                    continue
                for posting in postings:
                    dto = self._parse_posting(company, posting)
                    if query and query.lower() not in dto.title.lower():
                        continue
                    if location and location.lower() not in dto.location.lower():
                        continue
                    results.append(dto)
        return results

    @staticmethod
    def _parse_posting(company: str, posting: dict) -> JobPostingDTO:
        categories = posting.get("categories", {}) or {}
        location = categories.get("location", "") or ""

        description_plain = posting.get("descriptionPlain")
        if description_plain:
            description = description_plain
        else:
            description = _strip_html(posting.get("description", "") or "")
            lists = posting.get("lists", []) or []
            for section in lists:
                description += "\n" + _strip_html(section.get("content", ""))

        remote = "remote" in description.lower() or "remote" in location.lower()

        return JobPostingDTO(
            source="lever",
            external_id=str(posting.get("id", "")),
            title=posting.get("text", ""),
            company=company,
            location=location,
            remote=remote,
            description=description.strip(),
            url=posting.get("hostedUrl", "") or posting.get("applyUrl", ""),
            extra={"company": company, "team": categories.get("team", "")},
        )
