"""Generic adapter for an arbitrary JSON or RSS/XML job feed URL.

JSON feeds are expected to be either a top-level list of job objects, or an
object with a "jobs"/"items"/"results" list. Field names are matched
flexibly (title/position/name, company/employer, location, description/
summary, url/link/apply_url).

RSS/XML feeds use standard <item> elements with <title>, <link>,
<description>.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import httpx

from app.services.job_sources.base import JobPostingDTO, JobSourceAdapter

_TITLE_KEYS = ["title", "position", "name", "job_title"]
_COMPANY_KEYS = ["company", "employer", "company_name", "organization"]
_LOCATION_KEYS = ["location", "location_name", "city"]
_DESCRIPTION_KEYS = ["description", "summary", "details", "content"]
_URL_KEYS = ["url", "link", "apply_url", "absolute_url"]
_ID_KEYS = ["id", "external_id", "guid", "uuid"]


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def _first(obj: dict, keys: list[str], default: str = "") -> str:
    for key in keys:
        val = obj.get(key)
        if val:
            return str(val)
    return default


class GenericFeedAdapter(JobSourceAdapter):
    def __init__(self, feed_urls: list[str], timeout: float = 20.0):
        self.feed_urls = feed_urls
        self.timeout = timeout

    def search(self, query: str = "", location: str = "") -> list[JobPostingDTO]:
        results: list[JobPostingDTO] = []
        with httpx.Client(timeout=self.timeout) as client:
            for feed_url in self.feed_urls:
                try:
                    resp = client.get(feed_url)
                    resp.raise_for_status()
                except httpx.HTTPError:
                    continue

                content_type = resp.headers.get("content-type", "")
                try:
                    if "json" in content_type or resp.text.strip().startswith(("{", "[")):
                        results.extend(self._parse_json(feed_url, resp.json()))
                    else:
                        results.extend(self._parse_rss(feed_url, resp.text))
                except Exception:
                    continue

        if query:
            results = [r for r in results if query.lower() in r.title.lower()]
        if location:
            results = [r for r in results if location.lower() in r.location.lower()]
        return results

    def _parse_json(self, feed_url: str, data) -> list[JobPostingDTO]:
        if isinstance(data, dict):
            for key in ("jobs", "items", "results", "postings"):
                if key in data and isinstance(data[key], list):
                    data = data[key]
                    break
            else:
                data = []
        if not isinstance(data, list):
            return []

        dtos = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            description = _strip_html(_first(item, _DESCRIPTION_KEYS))
            location = _first(item, _LOCATION_KEYS)
            dtos.append(
                JobPostingDTO(
                    source="generic_feed",
                    external_id=_first(item, _ID_KEYS, default=f"{feed_url}#{i}"),
                    title=_first(item, _TITLE_KEYS),
                    company=_first(item, _COMPANY_KEYS),
                    location=location,
                    remote="remote" in (location + description).lower(),
                    description=description,
                    url=_first(item, _URL_KEYS, default=feed_url),
                    extra={"feed_url": feed_url},
                )
            )
        return dtos

    def _parse_rss(self, feed_url: str, xml_text: str) -> list[JobPostingDTO]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []

        dtos = []
        items = root.findall(".//item")
        for i, item in enumerate(items):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = _strip_html(item.findtext("description") or "")
            location = ""
            for tag in ("location", "{http://jobs.example.com/}location"):
                loc_el = item.find(tag)
                if loc_el is not None and loc_el.text:
                    location = loc_el.text.strip()
                    break

            dtos.append(
                JobPostingDTO(
                    source="generic_feed",
                    external_id=(item.findtext("guid") or link or f"{feed_url}#{i}").strip(),
                    title=title,
                    company="",
                    location=location,
                    remote="remote" in (location + description).lower(),
                    description=description,
                    url=link,
                    extra={"feed_url": feed_url},
                )
            )
        return dtos
