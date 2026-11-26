"""Checks and summaries for ``scripts/dryrun_batch.py`` (read-only dry-runs on real postings).

Pure functions over one run's raw observations, so they are unit-tested offline.
The Gate 2 checks (``gate2_checks``) are reused unchanged; this module adds what a
batch with a real owner profile needs on top: per-platform settings, the one-GET
"is this posting open" parse, "every planned answer is backed by a verified fact",
the claims-to-avoid scan over generated materials, and the batch summary.
"""
from __future__ import annotations

import contextlib
import hashlib
import html
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gate2_checks import OUTCOME_BLOCKED, OUTCOME_OK, SAFETY_CHECKS

V2_ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------- run directories (under --out)

BOARD_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")


def sanitize_board(board: str) -> str:
    """Reject path separators / traversal so a board slug cannot escape the output root."""
    if not isinstance(board, str) or not BOARD_RE.fullmatch(board) or ".." in board:
        raise SystemExit(f"unsafe board identifier: {board!r}")
    return board


def run_directory(out: Path, index: int, platform: str, board: str) -> Path:
    """Build ``out/run-NN-platform-board`` and prove the resolved path stays under ``out``."""
    board = sanitize_board(board)
    if platform not in PLATFORMS:
        raise SystemExit(f"unsupported platform: {platform!r}")
    out_root = out.resolve()
    run_dir = (out_root / f"run-{index:02d}-{platform}-{board}").resolve()
    if run_dir != out_root and out_root not in run_dir.parents:
        raise SystemExit(f"run directory {run_dir} escapes output root {out_root}")
    return run_dir


def ensure_under_output(out: Path, run_dir: Path) -> Path:
    """Re-check before rmtree/mkdir: the resolved run dir must stay under ``out``."""
    out_root = out.resolve()
    resolved = run_dir.resolve()
    if resolved != out_root and out_root not in resolved.parents:
        raise SystemExit(f"run directory {resolved} escapes output root {out_root}")
    return resolved


def clear_reports_from(out: Path, start: int) -> None:
    """Drop reports at/after ``start`` so a reused --out cannot mix a new/partial batch with stale runs."""
    import json

    for path in out.glob("run-*-report.json"):
        try:
            index = int(json.loads(path.read_text(encoding="utf-8")).get("index") or 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            index = 0
        if index >= start:
            path.unlink()


# The hosts the app itself may GET per platform, and the DOM evidence that the form rendered.
PLATFORMS: dict[str, dict[str, Any]] = {
    "greenhouse": {"api_hosts": ["boards-api.greenhouse.io"], "required_dom_keys": ["first_name", "email"],
                   "required_dom_key_substrings": [], "allow_rewrites": False, "probe_id_in_query": False},
    "lever": {"api_hosts": ["api.lever.co", "jobs.lever.co"], "required_dom_keys": [],
              "required_dom_key_substrings": ["email"], "allow_rewrites": False, "probe_id_in_query": False},
    # Ashby's page reads its form through allowlisted GraphQL *queries* that the router re-issues
    # as GET; the posting id travels in the GraphQL variables of the app's liveness probe.
    "ashby": {"api_hosts": ["jobs.ashbyhq.com"], "required_dom_keys": [],
              "required_dom_key_substrings": ["email"], "allow_rewrites": True, "probe_id_in_query": True},
}
# Extra checks every batch run must pass on top of the Gate 2 checks (all are safety checks).
EXTRA_SAFETY_CHECKS = ("live_submission_locked", "inspector_has_no_input_apis",
                       "planned_answers_backed_by_verified_facts", "materials_free_of_claims_to_avoid")
# Added by the parent after the child exits; must count toward safety_ok / exit status.
PARENT_SAFETY_CHECKS = ("browser_gone_after_child_exit", "no_process_outlived_the_run")
DECLINE_REASON = "explicit decline-to-self-identify policy"
VOLUNTARY_SECTIONS = {"eeoc", "demographic"}
_DECLINE_RE = re.compile(r"decline|don.?t wish|do not wish|prefer not|not to (?:say|disclose|answer|self)|choose not|wish not",
                         re.IGNORECASE)
_INPUT_APIS = (".click(", ".fill(", ".type(", "set_input_files", ".press(", ".check(", "select_option", "dispatch_event")
TRANSIENT_PATTERNS = ("net::ERR_", "ConnectError", "ConnectTimeout", "ReadTimeout", "ReadError", "RemoteProtocolError",
                      "WriteTimeout", "PoolTimeout", "Temporary failure in name resolution")

# Topics the owner said are never answered automatically (forbidden or unknown); a planned value
# for a field whose label matches one of these is a bug even if some vault value matched it.
FORBIDDEN_TOPICS: dict[str, re.Pattern[str]] = {
    "salary": re.compile(r"salary|compensation|pay (?:range|expectation)|desired pay|rémunération|salaire", re.IGNORECASE),
    "start_date": re.compile(r"start date|when can you start|earliest .*start|availability to start|date de début", re.IGNORECASE),
    "notice_period": re.compile(r"notice period|préavis", re.IGNORECASE),
    "relocation": re.compile(r"relocat|déménag", re.IGNORECASE),
    "employment_status": re.compile(r"currently employed|current employment status|are you employed", re.IGNORECASE),
    "reason_for_leaving": re.compile(r"reason for leaving|why did you leave|raison de (?:votre )?départ", re.IGNORECASE),
    "education": re.compile(r"degree|education|diploma|school|university|college|gpa|diplôme|études", re.IGNORECASE),
    # (?-i:...) keeps "US"/"U.S."/"USA" case-sensitive so the pronoun "us" never matches.
    "us_work": re.compile(r"(?-i:\bU\.?S\.?A?\b)|(?i:\bunited states\b|\bétats-unis\b)"),
}
# Never-auto-answer topics the owner has since covered with a stored answer (#36, #40). A field on one of
# these topics may be filled only from the matching classifier key, and only with the owner's exact value.
POLICY_TOPIC_KEYS: dict[str, frozenset[str]] = {
    "salary": frozenset({"salary_expectation"}),
    "start_date": frozenset({"start_date"}),
    "relocation": frozenset({"willing_to_relocate"}),
    "employment_status": frozenset({"employment_status"}),
    "education": frozenset({"education_level"}),
}
# Review buckets: what the owner would need to supply to make forms complete.
REVIEW_BUCKETS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("salary expectations", FORBIDDEN_TOPICS["salary"]),
    ("start date / availability", re.compile(r"start date|when can you start|availab|date de début", re.IGNORECASE)),
    ("notice period", FORBIDDEN_TOPICS["notice_period"]),
    ("relocation / commute / on-site", re.compile(r"relocat|commut|on.?site|in.?office|hybrid|office|déménag", re.IGNORECASE)),
    ("US work authorization", FORBIDDEN_TOPICS["us_work"]),
    ("work authorization / sponsorship (wording not matched)", re.compile(r"authori[sz]|sponsor|visa|eligib|permit|legally", re.IGNORECASE)),
    ("education / degree", FORBIDDEN_TOPICS["education"]),
    ("current employer / employment status", re.compile(r"current (?:company|employer|title|role)|currently employed", re.IGNORECASE)),
    ("previously worked here / referral", re.compile(r"previously (?:worked|employed|applied)|former employee|referr|refer", re.IGNORECASE)),
    ("how did you hear", re.compile(r"how did you (?:hear|find|learn)|source", re.IGNORECASE)),
    ("LinkedIn / website / portfolio", re.compile(r"linkedin|website|portfolio|github|url", re.IGNORECASE)),
    ("location / address / city / province", re.compile(r"location|address|city|province|postal|zip|country|where .*based|reside", re.IGNORECASE)),
    ("language proficiency", re.compile(r"french|english|language|bilingu|français|anglais", re.IGNORECASE)),
    ("years / level of experience", re.compile(r"years|experience with|experience in|how much experience|level of", re.IGNORECASE)),
    ("licence / certification", re.compile(r"licen[cs]|certif|llqp|registered|accredit", re.IGNORECASE)),
    ("background / criminal / credit check consent", re.compile(r"background|criminal|credit check|consent|acknowledg|agree|privacy|terms", re.IGNORECASE)),
    ("cover letter / motivation / free text", re.compile(r"cover letter|why (?:do|are) you|tell us|describe|motivation|interest", re.IGNORECASE)),
    ("demographic / EEO (no single decline option)", re.compile(r"gender|race|ethnic|veteran|disabil|pronoun|indigenous|lgbt|sexual|minority|identity", re.IGNORECASE)),
    ("name / phone / contact variant", re.compile(r"name|phone|email|contact", re.IGNORECASE)),
    ("resume / attachment", re.compile(r"resume|résumé|cv|attach|upload|file", re.IGNORECASE)),
)


def normalize(value: Any) -> str:
    if isinstance(value, list | tuple):
        return "|".join(sorted(normalize(item) for item in value))
    return " ".join(str(value if value is not None else "").split()).casefold()


def plain_text(markup: str) -> str:
    """Posting description as plain text (Greenhouse returns HTML-escaped HTML)."""
    text = html.unescape(html.unescape(markup or ""))
    text = re.sub(r"<(?:br|/p|/li|/h\d)[^>]*>", "\n", text, flags=re.IGNORECASE)
    return re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


# --------------------------------------------------------------------------- one GET per posting (parent)

def parse_open_posting(target: dict[str, Any], status: int, payload: Any) -> dict[str, Any] | None:
    """The posting from one public board-API response, or None if it is not open."""
    platform, board, job_id = target["platform"], target["board"], str(target["id"])
    if status != 200 or payload is None:
        return None
    if platform == "greenhouse":
        if str(payload.get("id")) != job_id:
            return None
        return {"title": payload.get("title") or "", "location": (payload.get("location") or {}).get("name") or "",
                "url": f"https://job-boards.greenhouse.io/{board}/jobs/{job_id}",
                "company": target.get("company") or payload.get("company_name") or board,
                "description": plain_text(payload.get("content") or "")}
    if platform == "lever":
        if payload.get("id") != job_id:
            return None
        categories = payload.get("categories") or {}
        return {"title": payload.get("text") or "", "location": categories.get("location") or "",
                "url": f"https://jobs.lever.co/{board}/{job_id}", "company": target.get("company") or board,
                "description": payload.get("descriptionPlain") or plain_text(payload.get("description") or "")}
    if platform == "ashby":
        for job in payload.get("jobs") or []:
            if job.get("id") == job_id and job.get("isListed", True):
                return {"title": job.get("title") or "", "location": job.get("location") or "",
                        "url": f"https://jobs.ashbyhq.com/{board}/{job_id}", "company": target.get("company") or board,
                        "description": job.get("descriptionPlain") or plain_text(job.get("descriptionHtml") or "")}
        return None
    raise ValueError(f"unsupported platform {platform!r}")


def open_check_url(target: dict[str, Any]) -> str:
    platform, board, job_id = target["platform"], target["board"], target["id"]
    if platform == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}"
    if platform == "lever":
        return f"https://api.lever.co/v0/postings/{board}/{job_id}"
    if platform == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{board}"
    raise ValueError(f"unsupported platform {platform!r}")


def trace_marker(platform: str, board: str, job_id: str) -> str:
    """The form page path the browser trace's network log must mention."""
    if platform == "lever":
        return f"/{board}/{job_id}/apply"
    if platform == "ashby":
        return f"/{board}/{job_id}/application"
    return "/embed/job_app"


def is_transient(text: str) -> bool:
    return any(pattern in (text or "") for pattern in TRANSIENT_PATTERNS)


# --------------------------------------------------------------------------- extra checks (child)

def check_live_submission_locked(data: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    settings = data.get("settings") or {}
    rows = (data.get("ledger") or {}).get("evidence") or []
    result = ((data.get("response") or {}).get("json") or {})
    ok = (settings.get("application_mode") == "dry_run" and settings.get("allow_live_submission") is False
          and result.get("submitted") is False and not any(row.get("kind") == "submission" for row in rows))
    return ok, {**settings, "submitted": result.get("submitted"), "ledger_kinds": [row.get("kind") for row in rows]}


def inspector_source_evidence(source: str | None = None) -> dict[str, Any]:
    """Scan (or re-use) the inspector source that produced a run; never re-read the live file on recheck."""
    if source is None:
        source = (V2_ROOT / "app" / "greenhouse_browser.py").read_text(encoding="utf-8")
    found = [api for api in _INPUT_APIS if api in source]
    return {"file": "app/greenhouse_browser.py",
            "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "input_apis_found": found}


def check_inspector_has_no_input_apis(data: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """The page inspector never clicks, types, selects, or attaches files (static check of the code that ran)."""
    evidence = data.get("inspector_source") or inspector_source_evidence()
    found = list(evidence.get("input_apis_found") or [])
    return not found, evidence


_NA_DOLLAR_CURRENCIES = frozenset({"CAD", "USD"})

_COUNTRY_DOLLAR_CODES: dict[str, str] = {
    "C": "CAD",
    "CA": "CAD",
    "CAD": "CAD",
    "US": "USD",
    "USD": "USD",
    "A": "AUD",
    "AU": "AUD",
    "AUD": "AUD",
    "NZ": "NZD",
    "NZD": "NZD",
    "HK": "HKD",
    "HKD": "HKD",
    "S": "SGD",
    "SG": "SGD",
    "SGD": "SGD",
    "MX": "MXN",
    "MXN": "MXN",
    "NT": "TWD",
    "TW": "TWD",
    "TWD": "TWD",
    "R": "BRL",
    "BR": "BRL",
    "BRL": "BRL",
}

_DOLLAR_USING_CURRENCIES = frozenset({
    "CAD", "USD", "AUD", "NZD", "HKD", "SGD", "TWD", "MXN", "BRL",
    "ARS", "CLP", "COP", "UYU", "JMD", "BSD", "BZD", "BBD", "BMD",
    "BND", "FJD", "GYD", "KYD", "LRD", "NAD", "SBD", "SRD", "TTD", "XCD",
})

_SYMBOL_CURRENCY_CODES: dict[str, str] = {
    "\u00a3": "GBP",
    "\u20ac": "EUR",
    "\u20b9": "INR",
    "\u00a5": "JPY",
    "\u20a9": "KRW",
    "\u20bd": "RUB",
    "\u20ba": "TRY",
    "\u20b1": "PHP",
    "\u0e3f": "THB",
    "\u20b4": "UAH",
    "\u20aa": "ILS",
    "\u20ab": "VND",
    "\u20a6": "NGN",
    "\u20a1": "CRC",
    "\u20ae": "MNT",
    "\u20b8": "KZT",
    "\u20bc": "AZN",
    "\u20be": "GEL",
    "\u20bf": "BTC",
}

_NON_CURRENCY_WORDS = frozenset({
    "NO", "IF", "OR", "TO", "AT", "IN", "ON", "BY", "OF", "UP", "AS", "IS",
    "IT", "BE", "DO", "GO", "MY", "SO", "WE", "HE", "ME", "AN", "VS",
    "NOT", "NON", "NOR", "YES", "AND", "FOR", "PER", "MAX", "MIN", "BUT", "YET", "VIA",
    "OTE", "AVG", "NET", "PAY", "TAX", "FEE", "HRS", "WKS", "MOS", "YRS", "DAY",
    "ANY", "OFF", "OUT", "OLD", "NEW", "ONE", "TWO", "SIX",
    "TEN", "EST", "PST", "CST", "MST", "AST", "ETC", "WHO", "WHY", "HOW", "YOU",
})

_UNICODE_DASHES = str.maketrans({
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2015": "-",
    "\u2212": "-",
    "\ufe63": "-",
    "\uff0d": "-",
})

_SC_NON_DOLLAR_CHARS = "".join(
    chr(cp) for cp in range(0x110000)
    if unicodedata.category(chr(cp)) == "Sc" and chr(cp) != "$"
)
_SC_NON_DOLLAR_CLASS = "[" + re.escape(_SC_NON_DOLLAR_CHARS) + "]"

_ANNUAL_UNIT_PAT = (
    r"(?:"
    r"\s*/\s*(?:yr\.?|year|annum)|"
    r"\s+\b(?:per\s+(?:year|annum)|a\s+year|annually|annual|yearly|p\s*/\s*a|p\.?\s*a\.)"
    r")"
)
_TRAILING_PAREN_CURR_RE = re.compile(r"\(\s*([A-Za-z]{3})\s*\)\s*$")
_TRAILING_PAREN_ANNUAL_RE = re.compile(
    r"\(\s*(?:/\s*(?:yr\.?|year|annum)|(?:per\s+(?:year|annum)|a\s+year|annually|annual|yearly|p\s*/\s*a|p\.?\s*a\.))\s*\)\s*$",
    re.IGNORECASE,
)
_TRAILING_ANNUAL_RE = re.compile(rf"{_ANNUAL_UNIT_PAT}\.?\s*$", re.IGNORECASE)

_NUM_PAT = r"(?:[1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]{0,2}(?: [0-9]{3})+|[1-9][0-9]*|0)(?:\.[0-9]{1,2})?"

_CURR_PRE_PAT = (
    rf"(?:(?:U\.?\s*S\.?|[A-Za-z]{{1,3}})\s*\$"
    rf"|\$\s*(?=[A-Za-z]{{2,3}}(?:\s|[0-9]|$))[A-Za-z]{{2,3}}"
    rf"|\$"
    rf"|{_SC_NON_DOLLAR_CLASS}"
    rf"|[A-Za-z]{{3}}(?=\s|[0-9]|$))"
)
_CURR_POST_PAT = (
    rf"(?:(?:U\.?\s*S\.?|[A-Za-z]{{1,3}})\s*\$"
    rf"|\$\s*(?=[A-Za-z]{{2,3}}(?:\s|$))[A-Za-z]{{2,3}}"
    rf"|\$"
    rf"|{_SC_NON_DOLLAR_CLASS}"
    rf"|[A-Za-z]{{2,3}}(?=\s|$))"
)


def _money_pat(prefix: str) -> str:
    return (
        rf"(?:(?P<{prefix}cpre>{_CURR_PRE_PAT})\s*)?"
        rf"(?P<{prefix}num>{_NUM_PAT})"
        rf"(?:\s*(?P<{prefix}k>[kK])(?![A-Za-z]))?"
        rf"(?:(?:\s+|(?<=[0-9kK]))(?P<{prefix}cpost>{_CURR_POST_PAT}))?"
    )


_M1 = _money_pat("m1_")
_M2 = _money_pat("m2_")
_M0 = _money_pat("m0_")

_RANGE_SEP_PAT = r"(?:(?<!\s)-(?!\s)|(?<!\s)-\s+|\s+-\s+|\s+\bto\b\s+)"

_SALARY_RANGE_RES = (
    re.compile(rf"{_M1}{_RANGE_SEP_PAT}{_M2}", re.IGNORECASE),
    re.compile(rf"from\s+{_M1}\s+to\s+{_M2}", re.IGNORECASE),
    re.compile(rf"between\s+{_M1}\s+and\s+{_M2}", re.IGNORECASE),
)
_SALARY_PLUS_INCL_RES = (
    re.compile(rf"{_M0}\s*\+", re.IGNORECASE),
    re.compile(rf"\+\s*{_M0}", re.IGNORECASE),
    re.compile(rf"(?:>=|at\s+least|min(?:imum)?)\s*{_M0}", re.IGNORECASE),
    re.compile(rf"{_M0}\s+(?:and\s+(?:above|up|over)|or\s+(?:more|above|higher))", re.IGNORECASE),
)
_SALARY_PLUS_STRICT_RES = (
    re.compile(rf"(?:>|(?:over|above|more\s+than)\s+){_M0}", re.IGNORECASE),
)
_SALARY_UNDER_INCL_RES = (
    re.compile(rf"(?:<=|(?:up\s+to|at\s+most|max(?:imum)?)\s+){_M0}", re.IGNORECASE),
    re.compile(rf"{_M0}\s+or\s+(?:less|below|under|lower)", re.IGNORECASE),
)
_SALARY_UNDER_STRICT_RES = (
    re.compile(rf"(?:<|(?:under|below|less\s+than)\s+){_M0}", re.IGNORECASE),
)
_SALARY_SINGLE_RE = re.compile(_M0, re.IGNORECASE)


@dataclass(frozen=True)
class _SalaryCurrency:
    explicit: str | None
    has_dollar: bool
    has_non_dollar_symbol: bool


@dataclass(frozen=True)
class _ParsedSalary:
    low: float
    high: float
    low_inclusive: bool
    high_inclusive: bool
    currency: _SalaryCurrency


_TZ_FAMILY_OFFSETS: dict[str, tuple[int, int]] = {
    "eastern": (-300, -240),
    "central": (-360, -300),
    "mountain": (-420, -360),
    "pacific": (-480, -420),
    "atlantic": (-240, -180),
    "newfoundland": (-210, -150),
}
_TZ_STD_OFFSET_TO_FAMILY: dict[int, str] = {
    std_min: family for family, (std_min, _) in _TZ_FAMILY_OFFSETS.items()
}

_TZ_NA_IANA_TO_FAMILY: dict[str, str] = {
    # Eastern (US & Canada)
    "america/toronto": "eastern",
    "america/new_york": "eastern",
    "america/montreal": "eastern",
    "america/ottawa": "eastern",
    "america/detroit": "eastern",
    "america/indianapolis": "eastern",
    "america/fort_wayne": "eastern",
    "america/indiana/indianapolis": "eastern",
    "america/indiana/marengo": "eastern",
    "america/indiana/vevay": "eastern",
    "america/indiana/vincennes": "eastern",
    "america/indiana/winamac": "eastern",
    "america/indiana/petersburg": "eastern",
    "america/louisville": "eastern",
    "america/kentucky/louisville": "eastern",
    "america/kentucky/monticello": "eastern",
    "america/iqaluit": "eastern",
    "america/nipigon": "eastern",
    "america/pangnirtung": "eastern",
    "america/thunder_bay": "eastern",
    "america/atikokan": "eastern",
    "us/eastern": "eastern",
    "us/east-indiana": "eastern",
    "us/michigan": "eastern",
    "canada/eastern": "eastern",
    "est5edt": "eastern",
    # Central (US & Canada)
    "america/chicago": "central",
    "america/winnipeg": "central",
    "america/regina": "central",
    "america/swift_current": "central",
    "america/rainy_river": "central",
    "america/rankin_inlet": "central",
    "america/resolute": "central",
    "america/indiana/knox": "central",
    "america/indiana/tell_city": "central",
    "america/menominee": "central",
    "america/north_dakota/center": "central",
    "america/north_dakota/new_salem": "central",
    "america/north_dakota/beulah": "central",
    "us/central": "central",
    "us/indiana-starke": "central",
    "canada/central": "central",
    "canada/saskatchewan": "central",
    "cst6cdt": "central",
    # Mountain (US & Canada)
    "america/denver": "mountain",
    "america/edmonton": "mountain",
    "america/calgary": "mountain",
    "america/phoenix": "mountain",
    "america/boise": "mountain",
    "america/yellowknife": "mountain",
    "america/cambridge_bay": "mountain",
    "america/inuvik": "mountain",
    "america/creston": "mountain",
    "america/dawson_creek": "mountain",
    "america/fort_nelson": "mountain",
    "america/whitehorse": "mountain",
    "america/dawson": "mountain",
    "us/mountain": "mountain",
    "us/arizona": "mountain",
    "canada/mountain": "mountain",
    "canada/yukon": "mountain",
    "mst7mdt": "mountain",
    # Pacific (US & Canada)
    "america/los_angeles": "pacific",
    "america/vancouver": "pacific",
    "america/seattle": "pacific",
    "america/san_francisco": "pacific",
    "us/pacific": "pacific",
    "canada/pacific": "pacific",
    "pst8pdt": "pacific",
    # Atlantic (Canada)
    "america/halifax": "atlantic",
    "america/moncton": "atlantic",
    "america/glace_bay": "atlantic",
    "america/goose_bay": "atlantic",
    "america/blanc-sablon": "atlantic",
    "canada/atlantic": "atlantic",
    # Newfoundland (Canada)
    "america/st_johns": "newfoundland",
    "canada/newfoundland": "newfoundland",
}

_TZ_ABBR_TO_FAMILY: dict[str, tuple[str, str]] = {
    "est": ("eastern", "std"),
    "edt": ("eastern", "dst"),
    "est/edt": ("eastern", "any"),
    "est5edt": ("eastern", "any"),
    "cst": ("central", "std"),
    "cdt": ("central", "dst"),
    "cst/cdt": ("central", "any"),
    "cst6cdt": ("central", "any"),
    "mst": ("mountain", "std"),
    "mdt": ("mountain", "dst"),
    "mst/mdt": ("mountain", "any"),
    "mst7mdt": ("mountain", "any"),
    "pst": ("pacific", "std"),
    "pdt": ("pacific", "dst"),
    "pst/pdt": ("pacific", "any"),
    "pst8pdt": ("pacific", "any"),
    "ast": ("atlantic", "std"),
    "adt": ("atlantic", "dst"),
    "ast/adt": ("atlantic", "any"),
    "nst": ("newfoundland", "std"),
    "ndt": ("newfoundland", "dst"),
    "nst/ndt": ("newfoundland", "any"),
}
_TZ_SHORT_ABBR_TO_FAMILY: dict[str, str] = {
    "ET": "eastern",
    "CT": "central",
    "MT": "mountain",
    "PT": "pacific",
    "AT": "atlantic",
    "NT": "newfoundland",
}

_TZ_US_CA_SHARED = frozenset({
    "us & canada", "us and canada", "us/canada",
    "canada & us", "canada and us", "canada/us",
    "us", "usa", "u.s.", "u.s.a.", "united states", "canada", "north america",
})
_TZ_CA_SHARED = frozenset({
    "us & canada", "us and canada", "us/canada",
    "canada & us", "canada and us", "canada/us",
    "canada", "north america",
})
_TZ_FAMILY_QUALIFIERS: dict[str, frozenset[str]] = {
    "eastern": _TZ_US_CA_SHARED | frozenset({
        "toronto", "new york", "new york city", "nyc", "montreal", "ottawa",
        "quebec", "ontario", "detroit", "boston", "miami", "atlanta",
        "washington", "washington dc", "washington, dc", "philadelphia",
        "indianapolis", "indiana", "indiana (east)", "east indiana",
        "louisville", "iqaluit", "thunder bay",
    }),
    "central": _TZ_US_CA_SHARED | frozenset({
        "chicago", "winnipeg", "regina", "saskatchewan", "manitoba",
        "dallas", "houston", "austin", "minneapolis", "st. louis", "st louis",
        "kansas city", "new orleans", "nashville",
    }),
    "mountain": _TZ_US_CA_SHARED | frozenset({
        "denver", "edmonton", "calgary", "alberta", "phoenix", "arizona",
        "salt lake city", "utah", "albuquerque", "boise", "yellowknife",
        "whitehorse", "yukon",
    }),
    "pacific": _TZ_US_CA_SHARED | frozenset({
        "vancouver", "los angeles", "san francisco", "seattle", "portland",
        "california", "british columbia", "victoria", "las vegas", "san diego",
    }),
    "atlantic": _TZ_CA_SHARED | frozenset({
        "halifax", "moncton", "nova scotia", "new brunswick",
        "prince edward island", "pei", "charlottetown", "fredericton",
        "saint john",
    }),
    "newfoundland": _TZ_CA_SHARED | frozenset({
        "newfoundland", "newfoundland and labrador", "newfoundland & labrador",
        "labrador", "st. john's", "st john's", "st. johns", "st johns", "st_johns",
    }),
}

_TZ_OFFSET_EXPR = r"(?:UTC|GMT)\s*-\s*(?:0?([45678])(?::?(00))?|0?(3):?(30))"
_TZ_PURE_OFFSET_RE = re.compile(rf"^\(?\s*{_TZ_OFFSET_EXPR}\s*\)?$", re.IGNORECASE)
_TZ_DUAL_OFFSET_RE = re.compile(
    rf"^\s*({_TZ_OFFSET_EXPR})(?:\s*/\s*({_TZ_OFFSET_EXPR}))?\s*$",
    re.IGNORECASE,
)
_TZ_LEADING_OFFSET_RE = re.compile(
    rf"^(?:\(\s*({_TZ_OFFSET_EXPR})\s*\)\s*[:\-\u2013\u2014|]?\s*|({_TZ_OFFSET_EXPR})\s*[:\-\u2013\u2014|\s]\s*)",
    re.IGNORECASE,
)
_TZ_TRAILING_OFFSET_RE = re.compile(
    rf"(?:\s*\(\s*({_TZ_OFFSET_EXPR})(?:\s*/\s*({_TZ_OFFSET_EXPR}))?\s*\)|\s*[-\u2013\u2014]\s*({_TZ_OFFSET_EXPR}))$",
    re.IGNORECASE,
)
_TZ_ZONE_NAME_RE = re.compile(
    r"^(Eastern|Central|Mountain|Pacific|Atlantic|Newfoundland)\s+"
    r"(?:(Standard|Daylight|Standard\s*/\s*Daylight)\s+)?Time$",
    re.IGNORECASE,
)
_TZ_BARE_FAMILY_RE = re.compile(
    r"^(Eastern|Central|Mountain|Pacific|Atlantic|Newfoundland)$",
    re.IGNORECASE,
)

_YES_EXACT = frozenset({
    "yes",
    "oui",
    "yes / oui",
    "yes/oui",
    "oui / yes",
    "oui/yes",
    "yes - oui",
    "oui - yes",
})
_YES_SPLIT_RE = re.compile(r"^(?:yes|oui)\s*(?:,|:|;|\s-\s)\s*(.+)$")
_YES_BILINGUAL_RE = re.compile(
    r"^(?:yes|oui)\s*(?:,|:|;|\s-\s)\s*"
    r"(?:(?:i\s+am\s+)?(?:fluent\s+in\s+both(?:\s+(?:french\s+and\s+english|english\s+and\s+french))?|bilingual)|"
    r"je\s+suis\s+bilingue)$"
)
_YES_SAFE_TAILS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^(?:i\s+am|i'm)\s+(?:legally\s+)?(?:authorized|authorised|eligible)"
        r"(?:\s+to\s+work(?:\s+in\s+canada)?)?(?:\s+without\s+restriction)?$"
    ),
    re.compile(
        r"^(?:i\s+am\s+(?:a\s+)?)?(?:canadian\s+citizen(?:\s*(?:/|or)\s*(?:pr|permanent\s+resident))?|"
        r"permanent\s+resident|citoyen\s+canadien(?:\s+ou\s+r[e\u00e9]sident\s+permanent)?)$"
    ),
    re.compile(r"^(?:i\s+am\s+)?located\s+in\s+canada$"),
    re.compile(r"^(?:i\s+am\s+)?open\s+to\s+it$"),
    re.compile(r"^(?:i\s+am\s+)?available(?:\s+(?:as\s+required|for\s+evenings\s+and\s+weekends|immediately))?$"),
    re.compile(
        r"^(?:(?:i\s+)?(?:consent|agree|accept|acknowledge|certify)"
        r"(?:\s+(?:to\s+(?:be\s+recorded|interview\s+recording)|to\s+the\s+(?:terms|disclaimer|agreement)))?|"
        r"j'accepte(?:\s+(?:les\s+)?conditions)?)$"
    ),
    re.compile(r"^expectations\s+aligned(?:\s+with\s+range)?$"),
    re.compile(
        r"^(?:(?:i\s+am\s+)?(?:fluent\s+in\s+both(?:\s+(?:french\s+and\s+english|english\s+and\s+french))?|bilingual)|"
        r"je\s+suis\s+bilingue)$"
    ),
)

_NO_EXACT = frozenset({
    "no",
    "non",
    "no / non",
    "no/non",
    "non / no",
    "non/no",
    "no - non",
    "non - no",
})
_NO_SPLIT_RE = re.compile(r"^(?:no|non)\s*(?:,|:|;|\s-\s)\s*(.+)$")
_NO_SAFE_TAILS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^i\s+have\s+never$"),
    re.compile(
        r"^i\s+have\s+not\s+(?:previously\s+)?worked\s+(?:for|at)\s+"
        r"(?!.*\b(?:not|never|no|none|excluding|excepting|outside|other|than|save|besides|less|minus|"
        r"recently|currently|previously|since|during|prior|before|after|"
        r"intern|contractor|part[\s-]+time|full[\s-]+time|remote|hybrid|on[\s-]*site|"
        r"if|unless|except|but|only|however|without|with|until|pending|"
        r"requiring|needing|sponsor|sponsorship|visa|permit|or|and)\b)"
        r"[a-z0-9&'.-]+(?:\s+[a-z0-9&'.-]+){0,3}\s+in\s+any\s+capacity$"
    ),
    re.compile(
        r"^i\s+(?:do\s+not|don't|will\s+not|won't)\s+(?:require|need)\s+(?:any\s+)?"
        r"(?:current\s+or\s+future\s+)?(?:visa\s+|immigration\s+)?sponsorship"
        r"(?:\s+(?:now\s+or\s+in\s+the\s+future|in\s+the\s+future|"
        r"to\s+work(?:\s+in\s+(?:canada|the\s+u\.?s\.?a?|the\s+united\s+states))?))?$"
    ),
    re.compile(r"^(?:visa\s+|immigration\s+)?sponsorship\s+(?:is\s+)?not\s+required$"),
    re.compile(
        r"^i\s+am\s+not\s+(?:bilingual|fluent(?:\s+in\s+both"
        r"(?:\s+(?:french\s+and\s+english|english\s+and\s+french))?)?)$"
    ),
    re.compile(r"^i\s+do\s+not\s+hold\s+either$"),
    re.compile(r"^i\s+(?:do\s+not|don't)\s+have\s+a\s+disability$"),
    re.compile(r"^(?:je\s+refuse|i\s+(?:do\s+not|don't)\s+(?:consent|agree))$"),
    re.compile(
        r"^i\s+am\s+not\s+(?:legally\s+)?(?:authorized|authorised|eligible)"
        r"(?:\s+to\s+work(?:\s+in\s+(?:canada|the\s+u\.?s\.?a?|the\s+united\s+states))?)?$"
    ),
)

_CEFR_DESCRIPTORS_BY_CODE: dict[str, str] = {
    "a1": r"no\s+proficiency",
    "a2": r"(?:elementary|basic)(?:\s+proficiency)?",
    "b1": r"(?:limited\s+working|intermediate)(?:\s+proficiency)?",
    "b2": r"(?:professional(?:\s+working)?|upper\s+intermediate)(?:\s+proficiency)?",
    "c1": r"(?:full\s+professional|advanced|fluent)(?:\s+proficiency)?",
    "c2": r"(?:bilingual\s*/\s*native|native\s*/\s*bilingual|native|bilingual|fluent)(?:\s+(?:speaker|proficiency))?",
}
_CEFR_SINGLE_LANG_PREFIX: dict[str, str] = {
    "english": r"(?:english|anglais)",
    "french": r"(?:french|fran[c\u00e7]ais)",
}
_CEFR_BILINGUAL_PREFIX = (
    r"(?:bilingual|bilingue|english\s*(?:&|and|/)\s*french|french\s*(?:&|and|/)\s*english)"
)


def _normalize_salary_raw(raw: Any) -> str | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, float):
        if not math.isfinite(raw):
            return None
        text = str(int(raw)) if raw.is_integer() else f"{raw:.2f}"
    else:
        text = str(raw)
    if any(unicodedata.category(ch) == "Cf" for ch in text):
        return None
    text = " ".join(text.split())
    if not text:
        return None
    return text.translate(_UNICODE_DASHES)


def _decode_currency_token(tok: str, *, is_post: bool) -> _SalaryCurrency | None:
    t = " ".join(tok.split())
    if not t:
        return None
    if t == "$":
        return _SalaryCurrency(explicit=None, has_dollar=True, has_non_dollar_symbol=False)
    if len(t) == 1 and unicodedata.category(t) == "Sc":
        return _SalaryCurrency(
            explicit=_SYMBOL_CURRENCY_CODES.get(t, f"SYM:{t}"),
            has_dollar=False,
            has_non_dollar_symbol=True,
        )
    if "$" in t:
        alpha = re.sub(r"[\s$.]", "", t).upper()
        if not alpha:
            return _SalaryCurrency(explicit=None, has_dollar=True, has_non_dollar_symbol=False)
        if alpha in _NON_CURRENCY_WORDS:
            return None
        mapped = _COUNTRY_DOLLAR_CODES.get(alpha)
        if mapped is not None:
            return _SalaryCurrency(explicit=mapped, has_dollar=True, has_non_dollar_symbol=False)
        if len(alpha) == 3 and alpha.isalpha():
            return _SalaryCurrency(explicit=alpha, has_dollar=True, has_non_dollar_symbol=False)
        return _SalaryCurrency(explicit=f"DOLLAR:{alpha}", has_dollar=True, has_non_dollar_symbol=False)
    alpha = t.upper()
    if alpha in _NON_CURRENCY_WORDS:
        return None
    if len(alpha) == 3 and alpha.isalpha():
        return _SalaryCurrency(explicit=alpha, has_dollar=False, has_non_dollar_symbol=False)
    if is_post and len(alpha) == 2 and alpha in _COUNTRY_DOLLAR_CODES:
        return _SalaryCurrency(explicit=_COUNTRY_DOLLAR_CODES[alpha], has_dollar=False, has_non_dollar_symbol=False)
    return None


def _combine_salary_currencies(infos: list[_SalaryCurrency]) -> _SalaryCurrency | None:
    if not infos:
        return _SalaryCurrency(explicit=None, has_dollar=False, has_non_dollar_symbol=False)
    explicits = {i.explicit for i in infos if i.explicit is not None}
    if len(explicits) > 1:
        return None
    has_dollar = any(i.has_dollar for i in infos)
    has_non_dollar_sym = any(i.has_non_dollar_symbol for i in infos)
    if has_dollar and has_non_dollar_sym:
        return None
    explicit = next(iter(explicits)) if explicits else None
    if explicit is not None and has_dollar and not explicit.startswith("DOLLAR:") and explicit not in _DOLLAR_USING_CURRENCIES:
        return None
    return _SalaryCurrency(explicit=explicit, has_dollar=has_dollar, has_non_dollar_symbol=has_non_dollar_sym)


def _extract_money_match(gd: dict[str, str | None], prefix: str) -> tuple[float, bool, bool, _SalaryCurrency] | None:
    num_str = gd[f"{prefix}num"] or ""
    has_k = bool(gd[f"{prefix}k"])
    has_decimal = "." in num_str
    val = float(num_str.replace(",", "").replace(" ", ""))
    currs: list[_SalaryCurrency] = []
    for key, is_post in ((f"{prefix}cpre", False), (f"{prefix}cpost", True)):
        tok = gd.get(key)
        if tok:
            ci = _decode_currency_token(tok, is_post=is_post)
            if ci is None:
                return None
            currs.append(ci)
    post_tok = gd.get(f"{prefix}cpost")
    if post_tok and len(post_tok.strip()) == 2 and not any(c.has_dollar for c in currs):
        return None
    combined = _combine_salary_currencies(currs)
    if combined is None:
        return None
    return val, has_k, has_decimal, combined


def _scale_salary_bound(num: float, has_k: bool, peer_has_k: bool, has_decimal: bool) -> float:
    if has_k:
        return num * 1000.0
    if num < 1000.0 and peer_has_k and not has_decimal:
        return num * 1000.0
    return num


def _strip_annual_and_paren_currency(text: str) -> tuple[str, list[_SalaryCurrency]] | None:
    extra_currs: list[_SalaryCurrency] = []
    s = text.strip()
    m_pc = _TRAILING_PAREN_CURR_RE.search(s)
    if m_pc:
        ci = _decode_currency_token(m_pc.group(1), is_post=True)
        if ci is None:
            return None
        extra_currs.append(ci)
        s = s[:m_pc.start()].strip()
    m_pa = _TRAILING_PAREN_ANNUAL_RE.search(s)
    if m_pa:
        s = s[:m_pa.start()].strip()
    else:
        m_a = _TRAILING_ANNUAL_RE.search(s)
        if m_a:
            s = s[:m_a.start()].strip()
    if not m_pc:
        m_pc2 = _TRAILING_PAREN_CURR_RE.search(s)
        if m_pc2:
            ci = _decode_currency_token(m_pc2.group(1), is_post=True)
            if ci is None:
                return None
            extra_currs.append(ci)
            s = s[:m_pc2.start()].strip()
    return s, extra_currs


def _parse_salary_single(body: str, extra_currs: list[_SalaryCurrency]) -> _ParsedSalary | None:
    m = _SALARY_SINGLE_RE.fullmatch(body)
    if not m:
        return None
    parsed = _extract_money_match(m.groupdict(), "m0_")
    if parsed is None:
        return None
    val, has_k, _, ci = parsed
    combined = _combine_salary_currencies([ci, *extra_currs])
    if combined is None:
        return None
    scaled = val * (1000.0 if has_k else 1.0)
    if scaled < 1000.0:
        return None
    return _ParsedSalary(scaled, scaled, True, True, combined)


def _parse_salary_range(body: str, extra_currs: list[_SalaryCurrency]) -> _ParsedSalary | None:
    for pat in _SALARY_RANGE_RES:
        m = pat.fullmatch(body)
        if not m:
            continue
        gd = m.groupdict()
        p1 = _extract_money_match(gd, "m1_")
        p2 = _extract_money_match(gd, "m2_")
        if p1 is None or p2 is None:
            return None
        v1, k1, dec1, c1 = p1
        v2, k2, _, c2 = p2
        combined = _combine_salary_currencies([c1, c2, *extra_currs])
        if combined is None:
            return None
        low = _scale_salary_bound(v1, k1, k2, dec1)
        high = v2 * (1000.0 if k2 else 1.0)
        if low < 1000.0 or high < 1000.0 or low > high:
            return None
        return _ParsedSalary(low, high, True, True, combined)
    return None


def _parse_salary_open_bound(
    body: str,
    extra_currs: list[_SalaryCurrency],
    patterns: tuple[tuple[tuple[re.Pattern[str], ...], bool], ...],
    *,
    is_lower_bound: bool,
) -> _ParsedSalary | None:
    for pats, incl in patterns:
        for pat in pats:
            m = pat.fullmatch(body)
            if not m:
                continue
            parsed = _extract_money_match(m.groupdict(), "m0_")
            if parsed is None:
                return None
            val, has_k, _, ci = parsed
            combined = _combine_salary_currencies([ci, *extra_currs])
            if combined is None:
                return None
            bound = val * (1000.0 if has_k else 1.0)
            if bound < 1000.0:
                return None
            if is_lower_bound:
                return _ParsedSalary(bound, float("inf"), incl, False, combined)
            return _ParsedSalary(0.0, bound, False, incl, combined)
    return None


def _parse_salary_grammar(raw: Any, *, scalar_only: bool = False) -> _ParsedSalary | None:
    norm = _normalize_salary_raw(raw)
    if norm is None:
        return None
    stripped = _strip_annual_and_paren_currency(norm)
    if stripped is None:
        return None
    body, extra_currs = stripped
    if not body:
        return None

    single = _parse_salary_single(body, extra_currs)
    if single is not None:
        return single
    if scalar_only:
        return None

    return (
        _parse_salary_range(body, extra_currs)
        or _parse_salary_open_bound(
            body, extra_currs, ((_SALARY_PLUS_INCL_RES, True), (_SALARY_PLUS_STRICT_RES, False)), is_lower_bound=True
        )
        or _parse_salary_open_bound(
            body, extra_currs, ((_SALARY_UNDER_INCL_RES, True), (_SALARY_UNDER_STRICT_RES, False)), is_lower_bound=False
        )
    )


def _salary_currencies_match(opt_c: _SalaryCurrency, res_c: _SalaryCurrency) -> bool:
    if opt_c.explicit is not None and res_c.explicit is not None:
        return opt_c.explicit == res_c.explicit
    if opt_c.explicit is not None:
        return opt_c.explicit in _NA_DOLLAR_CURRENCIES
    if res_c.explicit is not None:
        return res_c.explicit in _NA_DOLLAR_CURRENCIES
    return True


def _extract_salary_currencies(raw: str) -> tuple[set[str], bool]:
    parsed = _parse_salary_grammar(raw, scalar_only=False)
    if parsed is None:
        return set(), False
    currs = {parsed.currency.explicit} if parsed.currency.explicit else set()
    return currs, parsed.currency.has_dollar


def _currencies_compatible(option: str, resolved_str: str) -> bool:
    opt_p = _parse_salary_grammar(option, scalar_only=False)
    res_p = _parse_salary_grammar(resolved_str, scalar_only=True)
    if opt_p is None or res_p is None:
        return False
    return _salary_currencies_match(opt_p.currency, res_p.currency)


def _parse_scalar_salary(raw: Any) -> float | None:
    parsed = _parse_salary_grammar(raw, scalar_only=True)
    return parsed.low if parsed is not None else None


def _parse_salary_bracket(option: str) -> tuple[float, float] | None:
    parsed = _parse_salary_grammar(option, scalar_only=False)
    return (parsed.low, parsed.high) if parsed is not None else None


def _salary_option_contains(option: Any, resolved: Any) -> bool:
    """A bracket option (e.g. "60-70K") derived from the stored figure: the figure lies inside it."""
    if not isinstance(option, str):
        return False
    res_p = _parse_salary_grammar(resolved, scalar_only=True)
    if res_p is None:
        return False
    opt_p = _parse_salary_grammar(option, scalar_only=False)
    if opt_p is None:
        return False
    if not _salary_currencies_match(opt_p.currency, res_p.currency):
        return False
    target = res_p.low
    low_ok = (target >= opt_p.low) if opt_p.low_inclusive else (target > opt_p.low)
    high_ok = (target <= opt_p.high) if opt_p.high_inclusive else (target < opt_p.high)
    return low_ok and high_ok


def _parse_tz_offset_minutes(token: str) -> int | None:
    m = _TZ_PURE_OFFSET_RE.fullmatch(token.strip())
    if not m:
        return None
    h_str = m.group(1) or m.group(3)
    m_str = m.group(2) or m.group(4) or "00"
    return -(int(h_str) * 60 + int(m_str))


def _parse_tz_offset_group(token: str) -> list[int] | None:
    m = _TZ_DUAL_OFFSET_RE.fullmatch(token.strip())
    if not m:
        return None
    out: list[int] = []
    for grp in (1, 6):
        if m.group(grp):
            val = _parse_tz_offset_minutes(m.group(grp))
            if val is None:
                return None
            out.append(val)
    return out


def _normalize_iana_tz(raw: str) -> str:
    parts = [p.strip() for p in raw.strip().split("/")]
    if len(parts) < 2 or not all(parts):
        return raw.strip().casefold()
    norm_parts = []
    for part in parts:
        p_clean = "_".join(part.replace("'", "").replace(".", " ").split()).casefold()
        norm_parts.append(p_clean)
    return "/".join(norm_parts)


def _match_tz_abbr(token: str, *, allow_lower_short: bool = False) -> tuple[str, str] | None:
    cleaned = re.sub(r"\s*/\s*", "/", token.strip())
    if cleaned in _TZ_SHORT_ABBR_TO_FAMILY:
        return (_TZ_SHORT_ABBR_TO_FAMILY[cleaned], "any")
    if allow_lower_short and cleaned.upper() in _TZ_SHORT_ABBR_TO_FAMILY:
        return (_TZ_SHORT_ABBR_TO_FAMILY[cleaned.upper()], "any")
    return _TZ_ABBR_TO_FAMILY.get(cleaned.casefold())


def _split_tz_head_qualifier(body: str) -> tuple[str, str] | None:
    if body.endswith(")") and "(" in body:
        idx = body.find("(")
        head = body[:idx].strip()
        qual = body[idx + 1 : -1].strip()
        if head and qual and "(" not in qual and ")" not in qual:
            return head, qual
    for sep in (" - ", " \u2013 ", " \u2014 "):
        if sep in body:
            head, _, qual = body.partition(sep)
            head, qual = head.strip(), qual.strip()
            if head and qual:
                return head, qual
    return None


def _valid_tz_qualifier(inner: str, family: str) -> tuple[bool, str | None]:
    q = " ".join(inner.strip().split())
    if not q:
        return False, None
    abbr = _match_tz_abbr(q, allow_lower_short=True)
    if abbr is not None:
        return (abbr[0] == family, abbr[1])
    q_cf = q.casefold()
    allowed = _TZ_FAMILY_QUALIFIERS[family]
    if q_cf in allowed:
        return True, None
    parts = [p.strip() for p in re.split(r"[,;/]", q) if p.strip()]
    if len(parts) > 1 and all(
        p.casefold() in allowed
        or ((a := _match_tz_abbr(p, allow_lower_short=True)) is not None and a[0] == family)
        for p in parts
    ):
        return True, None
    return False, None


def _tz_offsets_match_family(offsets: list[int], family: str, kind: str) -> bool:
    if not offsets:
        return True
    std_min, dst_min = _TZ_FAMILY_OFFSETS[family]
    if len(offsets) == 2:
        return set(offsets) == {std_min, dst_min}
    if len(offsets) != 1:
        return False
    off = offsets[0]
    if kind == "std":
        return off == std_min
    if kind == "dst":
        return off == dst_min
    return off in (std_min, dst_min)


def _tz_zone_name_match(text: str) -> tuple[str, str] | None:
    zm = _TZ_ZONE_NAME_RE.fullmatch(text)
    if not zm:
        return None
    mod = (zm.group(2) or "").lower()
    kind = "std" if mod == "standard" else ("dst" if mod == "daylight" else "any")
    return zm.group(1).casefold(), kind


def _classify_tz_standalone(text: str) -> str | None:
    if (fam := _TZ_NA_IANA_TO_FAMILY.get(_normalize_iana_tz(text))) is not None:
        return fam
    if _TZ_BARE_FAMILY_RE.fullmatch(text):
        return text.casefold()
    if (off := _parse_tz_offset_minutes(text)) is not None:
        return _TZ_STD_OFFSET_TO_FAMILY.get(off)
    if (abbr := _match_tz_abbr(text)) is not None:
        return abbr[0]
    return None


def _extract_tz_body_and_offsets(text: str) -> tuple[str, list[int]] | None:
    offsets: list[int] = []
    body = text

    lead_m = _TZ_LEADING_OFFSET_RE.match(body)
    if lead_m:
        off = _parse_tz_offset_minutes(lead_m.group(1) or lead_m.group(6))
        if off is None:
            return None
        offsets.append(off)
        body = body[lead_m.end():].strip()

    trail_m = _TZ_TRAILING_OFFSET_RE.search(body)
    if trail_m and not offsets:
        for grp in (1, 6, 11):
            if trail_m.group(grp):
                off = _parse_tz_offset_minutes(trail_m.group(grp))
                if off is None:
                    return None
                offsets.append(off)
        body = body[:trail_m.start()].strip()

    return (body, offsets) if body else None


def _classify_tz_wrapped_body(body: str, offsets: list[int]) -> str | None:
    if (fam := _TZ_NA_IANA_TO_FAMILY.get(_normalize_iana_tz(body))) is not None:
        return fam if _tz_offsets_match_family(offsets, fam, "any") else None
    if (abbr_info := _match_tz_abbr(body)) is not None:
        fam_a, kind_a = abbr_info
        return fam_a if _tz_offsets_match_family(offsets, fam_a, kind_a) else None
    if offsets and _TZ_BARE_FAMILY_RE.fullmatch(body):
        fam = body.casefold()
        return fam if _tz_offsets_match_family(offsets, fam, "any") else None
    body_cf = body.casefold()
    if body_cf == "indiana (east)":
        return "eastern" if _tz_offsets_match_family(offsets, "eastern", "any") else None
    if body_cf in {"arizona", "saskatchewan"} and offsets:
        fam = "mountain" if body_cf == "arizona" else "central"
        return fam if _tz_offsets_match_family(offsets, fam, "any") else None
    return None


def _classify_tz_head_qualifier(body: str, offsets: list[int]) -> tuple[bool, str | None]:
    split_hq = _split_tz_head_qualifier(body)
    if split_hq is None:
        return False, None
    head, qual = split_hq
    if (fam := _TZ_NA_IANA_TO_FAMILY.get(_normalize_iana_tz(head))) is not None:
        z_match = _tz_zone_name_match(qual)
        if z_match is not None:
            z_fam, z_kind = z_match
            ok = z_fam == fam and _tz_offsets_match_family(offsets, fam, z_kind)
            return True, fam if ok else None
        ok, q_kind = _valid_tz_qualifier(qual, fam)
        return True, fam if (ok and _tz_offsets_match_family(offsets, fam, q_kind or "any")) else None

    if (abbr_info := _match_tz_abbr(head)) is not None:
        fam_a, kind_a = abbr_info
        z_match = _tz_zone_name_match(qual)
        if z_match is not None and z_match[0] == fam_a:
            kind_z = z_match[1]
            if kind_a != "any" and kind_z != "any" and kind_a != kind_z:
                return True, None
            eff_kind = kind_z if kind_z != "any" else kind_a
            if _tz_offsets_match_family(offsets, fam_a, eff_kind):
                return True, fam_a
        return True, None

    return False, None


def _classify_tz_zone_body(body: str, offsets: list[int]) -> str | None:
    zone_part = body
    trailing_quals: list[str] = []

    for sep in (" - ", " \u2013 ", " \u2014 "):
        if sep in zone_part:
            left, _, right = zone_part.partition(sep)
            zone_part = left.strip()
            trailing_quals.append(right.strip())
            break

    while zone_part.endswith(")") and "(" in zone_part:
        idx = zone_part.rfind("(")
        inner = zone_part[idx + 1 : -1].strip()
        if not inner or "(" in inner or ")" in inner:
            return None
        trailing_quals.append(inner)
        zone_part = zone_part[:idx].strip()

    z_match = _tz_zone_name_match(zone_part)
    if z_match is None:
        return None
    family, kind = z_match

    for qual in trailing_quals:
        if not offsets and (parsed_offs := _parse_tz_offset_group(qual)) is not None:
            offsets.extend(parsed_offs)
            continue
        ok, q_kind = _valid_tz_qualifier(qual, family)
        if not ok:
            return None
        if q_kind and q_kind != "any":
            if kind != "any" and kind != q_kind:
                return None
            kind = q_kind

    return family if _tz_offsets_match_family(offsets, family, kind) else None


def _classify_time_zone(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    text = " ".join(raw.replace("\u2212", "-").strip().split())
    if not text:
        return None
    standalone = _classify_tz_standalone(text)
    if standalone is not None:
        return standalone
    extracted = _extract_tz_body_and_offsets(text)
    if extracted is None:
        return None
    body, offsets = extracted
    wrapped = _classify_tz_wrapped_body(body, offsets)
    if wrapped is not None:
        return wrapped
    handled, head_result = _classify_tz_head_qualifier(body, offsets)
    if handled:
        return head_result
    return _classify_tz_zone_body(body, offsets)


def _same_time_zone(option: Any, resolved: Any) -> bool:
    """An option derived from the stored time zone: both name one identical North American zone family."""
    if not isinstance(option, str) or not isinstance(resolved, str):
        return False
    stored = _classify_time_zone(resolved)
    opt_zone = _classify_time_zone(option)
    return stored is not None and opt_zone == stored


def _clean_yn_text(norm_val: str) -> str:
    cleaned = norm_val.replace("\u2019", "'").replace("\u2018", "'").strip()
    return re.sub(r"\s*[.!]+\s*$", "", cleaned)


def _yes_option_matches(norm_val: str) -> bool:
    cleaned = _clean_yn_text(norm_val)
    if cleaned in _YES_EXACT:
        return True
    match = _YES_SPLIT_RE.match(cleaned)
    if not match:
        return False
    tail = match.group(1).strip()
    return any(pattern.fullmatch(tail) for pattern in _YES_SAFE_TAILS)


def _no_option_matches(norm_val: str) -> bool:
    cleaned = _clean_yn_text(norm_val)
    if cleaned in _NO_EXACT:
        return True
    match = _NO_SPLIT_RE.match(cleaned)
    if not match:
        return False
    tail = match.group(1).strip()
    return any(pattern.fullmatch(tail) for pattern in _NO_SAFE_TAILS)


def _cefr_option_matches(
    norm_val: str,
    weaker: str,
    weaker_langs: frozenset[str] = frozenset(),
) -> bool:
    desc = _CEFR_DESCRIPTORS_BY_CODE.get(weaker)
    if not desc:
        return False
    cleaned = re.sub(r"\s*[.!]+\s*$", "", norm_val.strip())
    core = rf"{re.escape(weaker)}(?:(?:\s*[:\-\u2013\u2014/]\s*|\s+){desc}|\s*\(\s*{desc}\s*\))?"
    p_direct = re.compile(rf"^{core}$")
    if p_direct.fullmatch(cleaned):
        return True
    lang_alts = [_CEFR_BILINGUAL_PREFIX]
    for lang in ("english", "french"):
        if lang in weaker_langs:
            lang_alts.append(_CEFR_SINGLE_LANG_PREFIX[lang])
    prefix_pat = f"(?:{'|'.join(lang_alts)})"
    p_prefixed = re.compile(rf"^{prefix_pat}\s*(?:\(\s*{core}\s*\)|[:\-\u2013\u2014]\s*{core})$")
    return bool(p_prefixed.fullmatch(cleaned))


def _value_matches_resolved(item: dict[str, Any]) -> bool:
    val = item.get("value")
    resolved = item.get("resolved_value")
    if normalize(val) == normalize(resolved):
        return True
    val_str = val[0] if isinstance(val, (list, tuple)) and len(val) == 1 else val
    if normalize(val_str) == normalize(resolved):
        return True
    norm_res = normalize(resolved)
    norm_val = normalize(val_str)
    if norm_res in {"yes", "y", "true"} and _yes_option_matches(norm_val):
        return True
    if norm_res in {"no", "n", "false"} and _no_option_matches(norm_val):
        return True
    if item.get("resolved_key") == "salary_expectation" and _salary_option_contains(val_str, resolved):
        return True
    if item.get("resolved_key") == "time_zone" and _same_time_zone(val_str, resolved):
        return True
    if item.get("resolved_key") == "language_proficiency":
        from app.option_derivation import _CEFR_ORDER, parse_languages

        with contextlib.suppress(KeyError, ValueError, AttributeError):
            levels = parse_languages(resolved)
            if levels and "english" in levels and "french" in levels:
                weaker_upper = min(levels["english"], levels["french"], key=_CEFR_ORDER.index)
                weaker = weaker_upper.lower()
                weaker_langs = frozenset(
                    lang for lang in ("english", "french") if levels[lang] == weaker_upper
                )
                if _cefr_option_matches(norm_val, weaker, weaker_langs=weaker_langs):
                    return True
                if _CEFR_ORDER.index(weaker_upper) >= _CEFR_ORDER.index("B2"):
                    cleaned_yn = _clean_yn_text(norm_val)
                    if cleaned_yn in _YES_EXACT or _YES_BILINGUAL_RE.fullmatch(cleaned_yn):
                        return True
    return False


def planned_answer_problems(plan_trace: list[dict[str, Any]], allowed: dict[str, list[Any]],
                            decline_policy: bool, owner_answers: dict[str, list[Any]] | None = None) -> list[dict[str, Any]]:
    """Planned answers not backed by a verified profile fact, an owner-supplied answer, or approved material.

    ``owner_answers`` holds only what the owner stored (no profile facts or materials); a never-auto-answer
    topic in ``POLICY_TOPIC_KEYS`` passes only when its value is exactly one of those stored answers."""
    allowed_norm = {key: {normalize(value) for value in values} for key, values in allowed.items()}
    owner_norm = {key: {normalize(value) for value in values} for key, values in (owner_answers or {}).items()}
    problems = []
    for item in plan_trace:
        if item.get("status") != "fill":
            continue
        label = f"{item.get('label') or ''} {item.get('key') or ''}"
        brief = {"key": item.get("key"), "label": item.get("label"), "value": item.get("value"),
                 "resolved_key": item.get("resolved_key"), "reason": item.get("reason")}
        if item.get("reason") == DECLINE_REASON:
            if not (decline_policy and item.get("section") in VOLUNTARY_SECTIONS and _DECLINE_RE.search(str(item.get("value")))):
                problems.append({**brief, "problem": "decline option used without the policy or not a decline option"})
            continue
        key = item.get("resolved_key")
        derivation = item.get("derivation") or {}
        if derivation.get("rule") == "prior_employment":
            problem = _prior_employment_problem(item, derivation, allowed)
            if problem:
                problems.append({**brief, "problem": problem})
            continue
        if key not in allowed_norm:
            problems.append({**brief, "problem": "answered from a vault key that is not a verified fact or owner answer"})
        elif normalize(item.get("resolved_value")) not in allowed_norm[key]:
            problems.append({**brief, "problem": "value differs from the verified fact / owner answer"})
        elif derivation.get("rule") == "option" and normalize(item.get("value")) != normalize(item.get("resolved_value")):
            problem = _derived_option_problem(item, derivation)
            if problem:
                problems.append({**brief, "problem": problem})
        elif not _value_matches_resolved(item):
            problems.append({**brief, "problem": "planned value differs from the resolved answer"})
        topics = [name for name, pattern in FORBIDDEN_TOPICS.items() if pattern.search(label)
                  and not _topic_allowed(name, key or "") and not _policy_backed(name, item, owner_norm)]
        if topics:
            problems.append({**brief, "problem": f"answered a never-auto-answer topic: {', '.join(topics)}"})
    return problems


def _topic_allowed(topic: str, key: str) -> bool:
    # A résumé/cover-letter upload or LinkedIn field can mention a school or "U.S." in its help text;
    # those keys only ever carry the approved file/text or the verified profile URL.
    if key in {"resume", "resume_text", "cover_letter", "cover_letter_text", "linkedin", "linkedin_url"}:
        return True
    return topic == "us_work" and key.endswith("_us")


def _policy_backed(topic: str, item: dict[str, Any], owner_norm: dict[str, set[str]]) -> bool:
    """The topic's classifier key, answered with exactly the value the owner stored for it."""
    key = item.get("resolved_key") or ""
    if key not in POLICY_TOPIC_KEYS.get(topic, frozenset()) or key not in owner_norm:
        return False
    resolved = normalize(item.get("resolved_value"))
    if resolved not in owner_norm[key]:
        return False
    val = item.get("value")
    if normalize(val) == resolved:
        return True
    # Only salary_expectation may derive a bracket option containing the stored figure;
    # all non-salary policy topics require an exact match against the owner's stored answer.
    if key == "salary_expectation":
        val_str = val[0] if isinstance(val, (list, tuple)) and len(val) == 1 else val
        return _salary_option_contains(val_str, item.get("resolved_value"))
    return False


# --- answers worked out from stored facts (#45). The trace carries the rule's own re-run, but these
# checks re-verify each answer from the facts with their own comparison, never by calling the rule.
_COMPANY_KEY_RE = re.compile(r"^employment_\d+_company$")
_COMPANY_SUFFIXES = frozenset({"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company",
                               "gmbh", "plc", "sa", "ag", "bv", "the", "group", "holdings"})
_CEFR = ("A1", "A2", "B1", "B2", "C1", "C2")
_LEVEL_WORDS: tuple[tuple[str, str], ...] = (
    ("native", "C2"), ("bilingual", "C2"), ("mother tongue", "C2"), ("full professional", "C1"), ("fluent", "C1"),
    ("professional working", "B2"), ("limited working", "B1"), ("intermediate", "B1"), ("elementary", "A2"),
    ("basic", "A2"),
)
_CEFR_PREFIX_RE = re.compile(r"^\s*([ABC][12])\b")


def _values(value: Any) -> list[str]:
    return [normalize(item) for item in value] if isinstance(value, list | tuple) else [normalize(value)]


def _company_words(name: Any) -> list[str]:
    return [word for word in re.findall(r"[a-z0-9]+", str(name).casefold()) if word not in _COMPANY_SUFFIXES]


def _same_or_lookalike(employer: str, known: Any) -> bool:
    """Equal, sharing a word, or one name inside the other: any of these is too close to answer "No"."""
    target, other = _company_words(employer), _company_words(known)
    if not target or not other:
        return True
    joined_target, joined_other = "".join(target), "".join(other)
    return bool(set(target) & set(other)) or joined_target in joined_other or joined_other in joined_target


def _prior_employment_problem(item: dict[str, Any], derivation: dict[str, Any],
                              allowed: dict[str, list[Any]]) -> str | None:
    employer = str(derivation.get("employer") or "")
    history = [value for key, values in allowed.items() if _COMPANY_KEY_RE.match(key) for value in values
               if str(value).strip()]
    if _values(derivation.get("rerun_value")) != _values(item.get("value")):
        return "prior-employment answer differs from the rule's re-run"
    if not all(value.startswith("no") for value in _values(item.get("value"))):
        return "prior-employment answer is not a No"
    if not employer or not set(_company_words(employer)) <= set(_company_words(item.get("label") or "")):
        return "prior-employment answer for an employer the question does not name"
    if not history:
        return "prior-employment No with no verified employment history"
    close = [name for name in history if _same_or_lookalike(employer, name)]
    if close:
        return f"prior-employment No but {employer!r} matches or resembles verified employer {close[0]!r}"
    return None


def _stored_levels(value: Any) -> dict[str, str]:
    """``"English (Native)|French (Professional working)"`` -> {"english": "C2", "french": "B2"}."""
    levels: dict[str, str] = {}
    for part in str(value).split("|"):
        match = re.match(r"^\s*([^()]+?)\s*\(([^)]*)\)\s*$", part)
        if not match:
            continue
        text = match.group(2).casefold()
        code = next((code for word, code in _LEVEL_WORDS if word in text), None)
        code = code or next((code for code in _CEFR if code.casefold() in text.split()), None)
        if code:
            levels[match.group(1).strip().casefold()] = code
    return levels


def _derived_option_problem(item: dict[str, Any], derivation: dict[str, Any]) -> str | None:
    planned = item.get("value")
    if _values(derivation.get("rerun_value")) != _values(planned):
        return "planned value differs from the resolved answer and from the option rule's re-run"
    options = {normalize(option) for option in derivation.get("options") or []}
    chosen = _values(planned)
    if not chosen or not set(chosen) <= options:
        return "derived answer is not a listed option"
    if item.get("resolved_key") == "language_proficiency":
        label = str(item.get("label") or "").casefold()
        levels = _stored_levels(item.get("resolved_value"))
        named = [language for language in ("english", "french") if language in label]
        if not named or any(language not in levels for language in named):
            return "language option for a language with no stored level"
        floor = min((levels[language] for language in named), key=_CEFR.index)
        for value in chosen:
            match = _CEFR_PREFIX_RE.match(value.upper())
            if match and _CEFR.index(match.group(1)) <= _CEFR.index(floor):
                continue
            if not match and value.startswith("yes") and _CEFR.index(floor) >= _CEFR.index("B2"):
                continue
            return f"language option {value!r} is above the stored level ({floor})"
        return None
    # Salary brackets, time-zone families and yes/no wording: the strict independent matchers above.
    if not _value_matches_resolved(item):
        stored = normalize(item.get("resolved_value"))
        if stored in {"yes", "y", "true", "no", "n", "false"}:
            return "derived option does not match the stored yes/no answer"
        return "derived option does not match the stored answer"
    return None



def owner_answer_map(pairs: Any) -> dict[str, list[Any]]:
    """Owner-stored answers by canonical vault key, with the vault's policy aliases (``availability_date`` →
    ``start_date``) applied only where the target key was not stored itself, exactly as ``load_vault`` does."""
    from app.answer_vault import _canonical_key
    from app.vault_store import _POLICY_KEY_ALIASES

    stored: dict[str, list[Any]] = {}
    for key, value in pairs:
        stored.setdefault(_canonical_key(key), []).append(value)
    aliased: dict[str, list[Any]] = {}
    for key, values in stored.items():
        for alias in _POLICY_KEY_ALIASES.get(key, ()):
            if alias not in stored:
                aliased.setdefault(alias, []).extend(values)
    return {**stored, **aliased}


def _owner_answers(data: dict[str, Any], allowed: dict[str, list[Any]]) -> dict[str, list[Any]]:
    if data.get("owner_answers") is not None:
        return data["owner_answers"]
    # Observations saved before owner answers were recorded on their own: the stored keys are in the seed
    # summary, and those keys' values in ``allowed`` came from the owner (profile facts use other keys).
    from app.answer_vault import _canonical_key

    stored = (data.get("seed") or {}).get("answers_stored") or []
    return owner_answer_map((key, value) for key in stored for value in allowed.get(_canonical_key(key), []))


def check_planned_answers(data: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    trace = data.get("plan_trace")
    if trace is None:
        return False, {"error": "no plan was captured"}
    allowed = data.get("allowed_answers") or {}
    owner = _owner_answers(data, allowed)
    allowed = {**{key: list(values) for key, values in owner.items()}, **allowed}
    problems = planned_answer_problems(trace, allowed, bool(data.get("decline_policy")), owner)
    planned = [item for item in trace if item.get("status") == "fill"]
    return not problems, {"planned": len(planned), "unbacked": problems}


def claims_hits(text: str, guardrails: list[dict[str, str]], ignore: tuple[str, ...] = ()) -> list[str]:
    """Claims-to-avoid found in generated text. ``ignore`` removes the posting's own title and
    company first: "the Manager, Fraud Operations position at X" names the job, it is not a claim."""
    scrubbed = text or ""
    for phrase in sorted((item for item in ignore if item), key=len, reverse=True):
        scrubbed = re.sub(re.escape(phrase), " ", scrubbed, flags=re.IGNORECASE)
    return [item["claim"] for item in guardrails if re.search(item["pattern"], scrubbed, re.IGNORECASE)]


def material_texts(database: Path) -> dict[str, str]:
    """Latest generated text per material kind, read straight from a run's (throwaway) database."""
    import sqlite3

    if not database.is_file():
        return {}
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        rows = connection.execute("SELECT kind, text FROM application_materials ORDER BY version").fetchall()
    return dict(rows)


def check_materials_claims(data: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    materials = data.get("materials") or []
    hits = {row["kind"]: row.get("claims_hits") for row in materials if row.get("claims_hits")}
    truth = {row["kind"]: row.get("truth_guard") for row in materials if row.get("truth_guard")}
    ok = bool(materials) and not hits and not truth and data.get("guardrails_loaded", 0) > 0
    return ok, {"materials": [{key: row.get(key) for key in ("kind", "status", "version", "content_sha256", "pdf_sha256")}
                              for row in materials], "claims_to_avoid_hits": hits, "truth_guard_problems": truth,
                "guardrail_patterns": data.get("guardrails_loaded")}


EXTRA_CHECKS = (
    ("live_submission_locked", check_live_submission_locked),
    ("inspector_has_no_input_apis", check_inspector_has_no_input_apis),
    ("planned_answers_backed_by_verified_facts", check_planned_answers),
    ("materials_free_of_claims_to_avoid", check_materials_claims),
)


def run_extra_checks(data: dict[str, Any]) -> dict[str, Any]:
    checks = {}
    for name, check in EXTRA_CHECKS:
        ok, detail = check(data)
        checks[name] = {"ok": bool(ok), "detail": detail}
    return checks


def batch_outcome(checks: dict[str, Any], challenge: str | None) -> str:
    """Gate 2's rule, with the batch's extra and parent-side process checks counted as safety checks."""
    safety = (*SAFETY_CHECKS, *EXTRA_SAFETY_CHECKS, *PARENT_SAFETY_CHECKS)
    if challenge:
        return OUTCOME_BLOCKED if all(checks[name]["ok"] for name in safety if name in checks) else "fail"
    return OUTCOME_OK if checks and all(item["ok"] for item in checks.values()) else "fail"


def safety_ok(checks: dict[str, Any]) -> bool:
    names = [*SAFETY_CHECKS, *EXTRA_SAFETY_CHECKS, *PARENT_SAFETY_CHECKS]
    return bool(checks) and all(checks.get(name, {}).get("ok") for name in names)


# --------------------------------------------------------------------------- summaries

def review_bucket(label: str, key: str = "") -> str:
    text = f"{label} {key}"
    for name, pattern in REVIEW_BUCKETS:
        if pattern.search(text):
            return name
    return "other employer-specific question"


def run_row(report: dict[str, Any]) -> dict[str, Any]:
    """One table row for the batch summary."""
    posting = report.get("posting") or {}
    mapping = report.get("field_mapping") or {}
    needs = mapping.get("needs_review") or []
    planned = int(mapping.get("planned_count") or 0)
    review = int(mapping.get("needs_review_count") or 0)
    buckets = Counter(review_bucket(item.get("label") or "", item.get("key") or "") for item in needs)
    return {
        "index": report.get("index"), "company": posting.get("company"), "title": posting.get("title"),
        "ats": posting.get("platform"), "url": posting.get("url"), "location": posting.get("location"),
        "verdict": report.get("outcome"), "safety_ok": safety_ok(report.get("checks") or {}),
        "planned": planned, "needs_review": review,
        "needs_review_required": len(mapping.get("needs_review_required") or []),
        "planned_pct": round(100 * planned / (planned + review), 1) if planned + review else None,
        "top_review_reasons": [name for name, _ in buckets.most_common(3)],
        "review_buckets": dict(buckets), "bot_check": bool(report.get("challenge")), "challenge": report.get("challenge"),
        "failed_checks": sorted(name for name, item in (report.get("checks") or {}).items() if not item.get("ok")),
        "unbacked_answers": len(((report.get("checks") or {}).get("planned_answers_backed_by_verified_facts") or {})
                                .get("detail", {}).get("unbacked") or []),
        "attempts": report.get("attempts"), "error": report.get("error"),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ran = [row for row in rows if row.get("verdict") != "skipped_closed"]
    pcts = [row["planned_pct"] for row in ran if row.get("planned_pct") is not None]
    buckets: Counter[str] = Counter()
    postings_with: Counter[str] = Counter()
    for row in ran:
        buckets.update(row.get("review_buckets") or {})
        postings_with.update((row.get("review_buckets") or {}).keys())
    return {
        "runs": len(ran),
        "skipped_closed": len(rows) - len(ran),
        "verdicts": dict(Counter(row.get("verdict") for row in ran)),
        "safety_pass": sum(1 for row in ran if row.get("safety_ok")),
        "safety_pass_rate": round(sum(1 for row in ran if row.get("safety_ok")) / len(ran), 3) if ran else None,
        "bot_checks": sum(1 for row in ran if row.get("bot_check")),
        "average_planned_pct": round(sum(pcts) / len(pcts), 1) if pcts else None,
        "unbacked_answers_total": sum(row.get("unbacked_answers") or 0 for row in ran),
        "review_fields_by_bucket": dict(buckets.most_common()),
        "postings_needing_bucket": dict(postings_with.most_common()),
        "by_ats": dict(Counter(row.get("ats") for row in ran)),
    }
