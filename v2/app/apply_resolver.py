"""Resolve job-board and aggregator listings to a direct ATS application URL.

Ported from the idea in JobTomatik's ``apply_resolver.py`` (link scoring only; none of
its submission, handoff, retry or network code). This module is pure and offline:
callers pass the listing URL and, optionally, HTML they already fetched. It never
fetches, never submits, and never decides to apply; it only classifies.

Outcomes:
- ``ats``: a direct posting on a known ATS host (exact host or subdomain match).
- ``email``: no ATS link, but the listing names an application email.
- ``review``: a plausible employer careers link that is not a known ATS host.
- ``unsupported``: an aggregator we do not automate (LinkedIn, Indeed, Glassdoor).
- ``rejected``: unsafe or scraping-trap URL (non-https, credentials, raw IP,
  localhost, javascript:, redirect wrappers, look-alike hosts).
- ``manual``: nothing safe found.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

ATS_HOSTS: tuple[str, ...] = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "smartrecruiters.com",
    "myworkdayjobs.com",
    "workdayjobs.com",
    "icims.com",
    "jobvite.com",
    "breezy.hr",
    "applytojob.com",
    "recruitee.com",
    "workable.com",
    "bamboohr.com",
    "dayforcehcm.com",
    "taleo.net",
    "successfactors.com",
    "successfactors.eu",
    "oraclecloud.com",
    "ultipro.com",
)

# Aggregators and listing sites whose own pages are never an application target.
AGGREGATOR_HOSTS: tuple[str, ...] = (
    "jobbank.gc.ca",
    "guichetemplois.gc.ca",
    "linkedin.com",
    "indeed.com",
    "indeed.ca",
    "glassdoor.com",
    "glassdoor.ca",
    "ziprecruiter.com",
    "monster.ca",
    "monster.com",
    "talent.com",
    "simplyhired.com",
    "simplyhired.ca",
    "eluta.ca",
    "workopolis.com",
    "jooble.org",
)
UNSUPPORTED_HOSTS: tuple[str, ...] = ("linkedin.com", "indeed.com", "indeed.ca", "glassdoor.com", "glassdoor.ca")

NON_JOB_HOSTS: tuple[str, ...] = (
    "canada.ca",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "youtube.com",
    "google.com",
    "tiktok.com",
)

# Query keys that wrap another URL: following them would hand control to a redirector.
REDIRECT_KEYS = frozenset(
    {"url", "u", "redirect", "redirect_uri", "redirect_url", "dest", "destination", "target", "next", "goto", "out"}
)

APPLY_WORDS = ("apply", "application", "careers", "career", "jobs", "postuler", "candidature", "emploi")
NOISE_WORDS = ("privacy", "terms", "cookie", "login", "sign-in", "signin", "unsubscribe", "share", "report")

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
NO_REPLY_PREFIXES = ("noreply@", "no-reply@", "donotreply@", "do-not-reply@")

MAX_HTML_BYTES = 2_000_000
MAX_LINKS = 500


@dataclass(frozen=True)
class ApplyResolution:
    outcome: str
    source_url: str
    apply_url: str = ""
    apply_email: str = ""
    reason: str = ""
    candidates: tuple[str, ...] = field(default_factory=tuple)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def host_matches(host: str, domains: tuple[str, ...]) -> bool:
    """Exact host or true subdomain match; ``greenhouse.io.evil.com`` never matches."""
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)


def is_ats_lookalike(host: str) -> bool:
    """A host that borrows an ATS brand name without being that ATS (phishing/scrape trap)."""
    host = host.lower().rstrip(".")
    if host_matches(host, ATS_HOSTS):
        return False
    return any(d.split(".")[0] in host for d in ATS_HOSTS if len(d.split(".")[0]) >= 5)


def is_ats_url(url: str) -> bool:
    return unsafe_reason(url) is None and host_matches(_host(url), ATS_HOSTS)


def _host_reason(host: str) -> str | None:
    if not host or "." not in host:
        return "missing or bare host"
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return "local host"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return "raw IP address host"
    if host.startswith("xn--") or ".xn--" in host:
        return "punycode host"
    return None


def _redirect_reason(query: str) -> str | None:
    for key, values in parse_qs(query).items():
        if key.lower() in REDIRECT_KEYS and any(v.lower().startswith(("http:", "https:", "//")) for v in values):
            return f"redirect wrapper via {key!r}"
    return None


def _shape_reason(raw: str) -> str | None:
    if not raw:
        return "empty URL"
    if any(ch.isspace() or ord(ch) < 32 for ch in raw):
        return "URL contains whitespace or control characters"
    return None


def unsafe_reason(url: str) -> str | None:
    """Return why ``url`` is unsafe to follow, or ``None`` when it is acceptable."""
    raw = (url or "").strip()
    shape = _shape_reason(raw)
    if shape:
        return shape
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return "malformed URL"
    if parts.scheme != "https":
        return f"scheme {parts.scheme or 'missing'!r} is not https"
    if parts.username or parts.password or "@" in parts.netloc:
        return "credentials embedded in URL"
    if port not in (None, 443):
        return f"non-standard port {port}"
    return _host_reason((parts.hostname or "").rstrip(".")) or _redirect_reason(parts.query)


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.text: list[str] = []
        self._href: str | None = None
        self._label: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript", "template"):
            self._skip_depth += 1
            return
        if tag == "a" and len(self.links) < MAX_LINKS:
            attr = dict(attrs)
            hidden = "hidden" in attr or (attr.get("aria-hidden") or "").lower() == "true"
            style = (attr.get("style") or "").replace(" ", "").lower()
            if hidden or "display:none" in style or "visibility:hidden" in style:
                return  # hidden honeypot links are a scraping trap
            self._href = (attr.get("href") or "").strip()
            self._label = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "template"):
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(" ".join(self._label).split())))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self.text.append(data)
        if self._href is not None:
            self._label.append(data)


def _score(url: str, label: str) -> int:
    hay = f"{url} {label}".lower()
    score = 0
    if host_matches(_host(url), ATS_HOSTS):
        score += 100
    if any(word in hay for word in APPLY_WORDS):
        score += 40
    if any(word in hay for word in NOISE_WORDS):
        score -= 80
    return score


def _precheck(listing_url: str, html: str | None) -> ApplyResolution | None:
    reason = unsafe_reason(listing_url)
    if reason:
        return ApplyResolution("rejected", listing_url, reason=reason)
    host = _host(listing_url)
    if host_matches(host, ATS_HOSTS):
        return ApplyResolution(
            "ats", listing_url, apply_url=listing_url, reason="listing is already on a known ATS host"
        )
    if host_matches(host, UNSUPPORTED_HOSTS):
        return ApplyResolution("unsupported", listing_url, reason="aggregator listing is not an application target")
    if html is None:
        return ApplyResolution("manual", listing_url, reason="no listing HTML supplied to resolve")
    if len(html) > MAX_HTML_BYTES:
        return ApplyResolution("manual", listing_url, reason="listing HTML too large to parse safely")
    return None


def _mailto(href: str) -> str | None:
    addr = href.split(":", 1)[1].split("?", 1)[0].strip()
    return addr if EMAIL_RE.fullmatch(addr) else None


def _candidate_link(listing_url: str, href: str) -> str | None:
    """Absolute off-site link worth scoring, or None for traps and noise."""
    absolute = urljoin(listing_url, href)
    if unsafe_reason(absolute):
        return None
    link_host = _host(absolute)
    if link_host == _host(listing_url) or host_matches(link_host, AGGREGATOR_HOSTS + NON_JOB_HOSTS):
        return None
    if is_ats_lookalike(link_host):
        return None
    return absolute


def _collect(
    listing_url: str, parser: _LinkCollector
) -> tuple[list[tuple[int, str]], list[tuple[int, str]], list[str]]:
    ats: list[tuple[int, str]] = []
    careers: list[tuple[int, str]] = []
    emails: list[str] = []
    for href, label in parser.links:
        if href.lower().startswith("mailto:"):
            addr = _mailto(href)
            if addr:
                emails.append(addr)
            continue
        absolute = _candidate_link(listing_url, href)
        if absolute is None:
            continue
        score = _score(absolute, label)
        if host_matches(_host(absolute), ATS_HOSTS):
            if score >= 100:
                ats.append((score, absolute))
        elif score >= 40:
            careers.append((score, absolute))
    emails += EMAIL_RE.findall(" ".join(parser.text))
    clean = sorted({e.lower() for e in emails if not e.lower().startswith(NO_REPLY_PREFIXES)})
    return ats, careers, clean


def _rank(items: list[tuple[int, str]]) -> list[str]:
    seen: dict[str, int] = {}
    for score, url in items:
        seen[url] = max(score, seen.get(url, score))
    return [u for u, _ in sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))]


def _decide(listing_url: str, ats_ranked: list[str], careers_ranked: list[str], emails: list[str]) -> ApplyResolution:
    if ats_ranked:
        top = tuple(ats_ranked[:5])
        if len({_host(u) for u in ats_ranked}) > 1:
            return ApplyResolution(
                "review", listing_url, reason="listing links to more than one ATS host", candidates=top
            )
        return ApplyResolution(
            "ats", listing_url, apply_url=ats_ranked[0], reason="direct ATS link found on listing", candidates=top
        )
    if careers_ranked:
        return ApplyResolution(
            "review",
            listing_url,
            reason="employer careers link is not a known ATS host",
            candidates=tuple(careers_ranked[:5]),
        )
    if emails:
        return ApplyResolution("email", listing_url, apply_email=emails[0], reason="application email found on listing")
    return ApplyResolution("manual", listing_url, reason="no safe ATS link or application email found")


def resolve_apply_target(listing_url: str, html: str | None = None) -> ApplyResolution:
    """Classify ``listing_url`` (and optional fetched ``html``) into an apply target."""
    early = _precheck(listing_url, html)
    if early is not None:
        return early
    parser = _LinkCollector()
    parser.feed(html or "")
    parser.close()
    ats, careers, emails = _collect(listing_url, parser)
    return _decide(listing_url, _rank(ats), _rank(careers), emails)
