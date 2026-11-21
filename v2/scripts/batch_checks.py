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
import re
from collections import Counter
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


_HOURLY_RATE_RE = re.compile(r"(?:/\s*(?:hr|hour|h)\b|\b(?:hourly|per\s+hour|per\s+hr)\b)", re.IGNORECASE)
_PERIOD_RATE_RE = re.compile(
    r"(?:/\s*(?:wk|week|mo|month|day|diem)\b|\b(?:weekly|monthly|daily|per\s+(?:week|month|day))\b)",
    re.IGNORECASE,
)
_CURRENCY_PREFIX_RE = re.compile(r"\b(?:cad|usd|aud|eur|gbp)(?=\d|\b)|ca\$|us\$|[$€£]", re.IGNORECASE)
_BONUS_SPLIT_RE = re.compile(r"\b(?:bonus|commission|equity|ote|sign[- ]?on)\b", re.IGNORECASE)
_SALARY_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(k)?\b", re.IGNORECASE)
_SALARY_RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(k)?\s*(?:-|" "\u2013|\u2014" r"|\bto\b)\s*(\d+(?:\.\d+)?)\s*(k)?\b",
    re.IGNORECASE,
)
_SALARY_PLUS_RE = re.compile(
    r"(?:\+|over|above|more\s+than|at\s+least|>=?)\s*(\d+(?:\.\d+)?)\s*(k)?\b"
    r"|(\d+(?:\.\d+)?)\s*(k)?\s*(?:\+|\band\s+above\b|\bor\s+more\b|\bor\s+above\b|\bor\s+higher\b)",
    re.IGNORECASE,
)
_SALARY_UNDER_RE = re.compile(
    r"\b(?:up\s+to|under|less\s+than|below|at\s+most|max(?:imum)?)\s*(\d+(?:\.\d+)?)\s*(k)?\b"
    r"|(?:<=|<)\s*(\d+(?:\.\d+)?)\s*(k)?\b",
    re.IGNORECASE,
)

_TZ_NEGATION_RE = re.compile(r"\bnot\b", re.IGNORECASE)
_TZ_POSITIVE_OR_UNSIGNED_OFFSET_RE = re.compile(r"\b(?:utc|gmt)\s*(?:\+|\d)", re.IGNORECASE)
_TZ_NON_NA_RE = re.compile(
    r"\b(?:australia|australian|china|chinese|arabia|arabian|auckland|india|indian|cuba|cuban|"
    r"europe|european|uk|united\s+kingdom|britain|british|london|tokyo|singapore|brazil|brasil|"
    r"pakistan|beijing|shanghai|hong[ _]?kong|sydney|melbourne|brisbane|perth|adelaide|paris|"
    r"berlin|moscow|dubai|riyadh|cairo|seoul|jakarta|manila|bangkok|taipei|kolkata|mumbai|"
    r"delhi|havana|african|africa|"
    r"eet|cet|wet|bst|ist|jst|kst|sgt|hkt|gst|aest|aedt|acst|acdt|awst|nzst|nzdt|cat|sast|wat|eat|apt)\b"
    r"|(?:asia|europe|australia|africa|indian|antarctica)/"
    r"|pacific/(?:auckland|fiji|guam|honolulu|tahiti|samoa|midway|chatham|tongatapu|port_moresby|noumea|wake)"
    r"|atlantic/(?:azores|canary|cape_verde|faroe|faeroe|jan_mayen|madeira|reykjavik|south_georgia|st_helena|stanley)",
    re.IGNORECASE,
)
_BATCH_TZ_PATTERNS: dict[str, re.Pattern[str]] = {
    "eastern": re.compile(
        r"\beastern\b|\best\b|\bedt\b|\best5edt\b|\btoronto\b|\bnew[ _]york\b|\bmontreal\b|\bottawa\b"
        r"|\b(?:gmt|utc)\s*-\s*0?5(?::?00)?(?![:\d])",
        re.IGNORECASE,
    ),
    "central": re.compile(
        r"\bcentral\b|\bcst\b|\bcdt\b|\bcst6cdt\b|\bchicago\b|\bwinnipeg\b"
        r"|\b(?:gmt|utc)\s*-\s*0?6(?::?00)?(?![:\d])",
        re.IGNORECASE,
    ),
    "mountain": re.compile(
        r"\bmountain\b|\bmst\b|\bmdt\b|\bmst7mdt\b|\bdenver\b|\bedmonton\b|\bcalgary\b"
        r"|\b(?:gmt|utc)\s*-\s*0?7(?::?00)?(?![:\d])",
        re.IGNORECASE,
    ),
    "pacific": re.compile(
        r"\bpacific\b|\bpst\b|\bpdt\b|\bpst8pdt\b|\bvancouver\b|\blos[ _]angeles\b"
        r"|\b(?:gmt|utc)\s*-\s*0?8(?::?00)?(?![:\d])",
        re.IGNORECASE,
    ),
    "atlantic": re.compile(
        r"\batlantic\b|\bast\b|\badt\b|\bhalifax\b"
        r"|\b(?:gmt|utc)\s*-\s*0?4(?::?00)?(?![:\d])",
        re.IGNORECASE,
    ),
    "newfoundland": re.compile(
        r"\bnewfoundland\b|\bnst\b|\bndt\b|\bst[ ._-]*johns\b"
        r"|\b(?:gmt|utc)\s*-\s*0?3:?30(?![:\d])",
        re.IGNORECASE,
    ),
}

_YES_OPTION_RE = re.compile(r"^yes(?:\s|$|[,;:/().!])")
_NO_OPTION_RE = re.compile(r"^no(?:\s|$|[,;:/().!])")
_YES_NO_CONDITIONAL_RE = re.compile(
    r"\b(?:only|if|unless|provided|subject\s+to|except|but|however|conditional(?:ly)?|"
    r"depending|assuming|contingent|bonus|compensation)\b|"
    r"(?:^yes|^no)\s*[,;:\-" "\u2013\u2014" r"(/]+\s*with\b|\$",
    re.IGNORECASE,
)


def _clean_salary_text(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    text = _CURRENCY_PREFIX_RE.sub(" ", text)
    return " ".join(text.split())


def _parse_scalar_salary(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    raw_str = str(raw).strip()
    if not raw_str or re.search(r"\bn/?a\b", raw_str, re.IGNORECASE):
        return None
    if _HOURLY_RATE_RE.search(raw_str) or _PERIOD_RATE_RE.search(raw_str):
        return None
    cleaned = _clean_salary_text(raw_str)
    if re.search(r"^\s*-\s*\d", cleaned):
        return None
    tokens = _SALARY_NUM_RE.findall(cleaned)
    if len(tokens) != 1:
        return None
    num_str, k_suffix = tokens[0]
    val = float(num_str) * (1000.0 if k_suffix else 1.0)
    return val if val > 0 else None


def _scale_salary_bound(num: float, has_k: bool, peer_has_k: bool, peer_num: float | None, is_rate: bool) -> float:
    if has_k:
        return num * 1000.0 if num < 1000 else num
    if not is_rate and num < 1000 and (peer_has_k or peer_num is None or peer_num < 1000):
        return num * 1000.0
    return num


def _parse_salary_bracket(option: str) -> tuple[float, float] | None:
    if re.search(r"\bn/?a\b", option, re.IGNORECASE) or _BONUS_SPLIT_RE.search(option):
        return None
    is_rate = bool(_HOURLY_RATE_RE.search(option) or _PERIOD_RATE_RE.search(option))
    cleaned = _clean_salary_text(option)
    if not cleaned or re.search(r"^\s*-\s*\d", cleaned) or re.search(r"\d\s*\+\s*\d", cleaned):
        return None
    all_nums = _SALARY_NUM_RE.findall(cleaned)
    if not all_nums:
        return None

    range_m = _SALARY_RANGE_RE.search(cleaned)
    if range_m:
        if len(all_nums) != 2:
            return None
        low_num = float(range_m.group(1))
        low_k = bool(range_m.group(2))
        high_num = float(range_m.group(3))
        high_k = bool(range_m.group(4))
        low = _scale_salary_bound(low_num, low_k, high_k, high_num, is_rate)
        high = _scale_salary_bound(high_num, high_k, low_k, low_num, is_rate)
        if low <= 0 or high <= 0 or low > high:
            return None
        return (low, high)

    if len(all_nums) != 1:
        return None

    plus_m = _SALARY_PLUS_RE.search(cleaned)
    if plus_m:
        num = float(plus_m.group(1) or plus_m.group(3))
        has_k = bool(plus_m.group(2) or plus_m.group(4))
        low = _scale_salary_bound(num, has_k, False, None, is_rate)
        return (low, float("inf")) if low > 0 else None

    under_m = _SALARY_UNDER_RE.search(cleaned)
    if under_m:
        num = float(under_m.group(1) or under_m.group(3))
        has_k = bool(under_m.group(2) or under_m.group(4))
        high = _scale_salary_bound(num, has_k, False, None, is_rate)
        return (0.0, high) if high > 0 else None

    num_str, k_suffix = all_nums[0]
    val = float(num_str) * (1000.0 if k_suffix and float(num_str) < 1000 else 1.0)
    return (val, val) if val > 0 else None


def _salary_option_contains(option: Any, resolved: Any) -> bool:
    """A bracket option (e.g. "60-70K") derived from the stored figure: the figure lies inside it."""
    target = _parse_scalar_salary(resolved)
    if target is None or not isinstance(option, str) or not re.search(r"\d", option):
        return False
    if (_HOURLY_RATE_RE.search(option) or _PERIOD_RATE_RE.search(option)) and target >= 1000:
        return False
    bounds = _parse_salary_bracket(option)
    return bounds is not None and bounds[0] <= target <= bounds[1]


def _classify_time_zone(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if (
        not text
        or _TZ_NEGATION_RE.search(text)
        or _TZ_POSITIVE_OR_UNSIGNED_OFFSET_RE.search(text)
        or _TZ_NON_NA_RE.search(text)
    ):
        return None
    matches = [name for name, pattern in _BATCH_TZ_PATTERNS.items() if pattern.search(text)]
    return matches[0] if len(matches) == 1 else None


def _same_time_zone(option: Any, resolved: Any) -> bool:
    """An option derived from the stored time zone: both name one identical North American zone family."""
    if not isinstance(option, str) or not isinstance(resolved, str):
        return False
    stored = _classify_time_zone(resolved)
    opt_zone = _classify_time_zone(option)
    return stored is not None and opt_zone == stored


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
    if norm_res in {"yes", "y", "true"} and _YES_OPTION_RE.match(norm_val) and not _YES_NO_CONDITIONAL_RE.search(norm_val):
        return True
    if norm_res in {"no", "n", "false"} and _NO_OPTION_RE.match(norm_val) and not _YES_NO_CONDITIONAL_RE.search(norm_val):
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
                weaker = min(levels["english"], levels["french"], key=_CEFR_ORDER.index)
                if weaker.lower() in norm_val:
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
        if str(item.get("reason") or "").startswith("prior employment:"):
            continue
        key = item.get("resolved_key")
        if key not in allowed_norm:
            problems.append({**brief, "problem": "answered from a vault key that is not a verified fact or owner answer"})
        elif normalize(item.get("resolved_value")) not in allowed_norm[key]:
            problems.append({**brief, "problem": "value differs from the verified fact / owner answer"})
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
