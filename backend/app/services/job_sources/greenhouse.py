"""Greenhouse public Job Board API adapter (no scraping, no ToS issue).

API docs: https://developers.greenhouse.io/job-board.html
Endpoint: https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true
"""
from __future__ import annotations

import re

import httpx

from app.services.job_sources.base import JobPostingDTO, JobSourceAdapter

_BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


class GreenhouseAdapter(JobSourceAdapter):
    def __init__(self, board_tokens: list[str], timeout: float = 20.0):
        self.board_tokens = board_tokens
        self.timeout = timeout

    def search(self, query: str = "", location: str = "") -> list[JobPostingDTO]:
        results: list[JobPostingDTO] = []
        with httpx.Client(timeout=self.timeout) as client:
            for token in self.board_tokens:
                url = _BASE_URL.format(board_token=token)
                try:
                    resp = client.get(url, params={"content": "true"})
                    resp.raise_for_status()
                except httpx.HTTPError:
                    continue
                data = resp.json()
                for job in data.get("jobs", []):
                    dto = self._parse_job(token, job)
                    if query and query.lower() not in dto.title.lower():
                        continue
                    if location and location.lower() not in dto.location.lower():
                        continue
                    results.append(dto)
        return results

    @staticmethod
    def _parse_job(board_token: str, job: dict) -> JobPostingDTO:
        location = ""
        loc_obj = job.get("location")
        if isinstance(loc_obj, dict):
            location = loc_obj.get("name", "") or ""

        description_html = job.get("content", "") or ""
        description = _strip_html(description_html)
        remote = "remote" in description.lower() or "remote" in location.lower()

        return JobPostingDTO(
            source="greenhouse",
            external_id=str(job.get("id")),
            title=job.get("title", ""),
            company=board_token,
            location=location,
            remote=remote,
            description=description,
            url=job.get("absolute_url", ""),
            extra={"board_token": board_token},
        )
