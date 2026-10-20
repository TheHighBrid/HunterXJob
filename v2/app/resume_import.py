"""Turn a plain-text, PDF or DOCX résumé into *unverified* profile fact drafts.

Parsing is heuristic and deliberately conservative: anything it extracts is
only a suggestion that the owner must confirm (verify) or fix before it can be
used. Nothing here ever marks a fact verified.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

from app.profile import FactDraft, ProfileError

MAX_RESUME_BYTES = 5_000_000
MAX_RESUME_CHARS = 60_000

_MONTHS = {name: index for index, names in enumerate((
    ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",),
    ("jun", "june"), ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"),
    ("oct", "october"), ("nov", "november"), ("dec", "december"),
), start=1) for name in names}
_MONTH_WORDS = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DATE = rf"(?:(?:{_MONTH_WORDS})\.? \d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}})"
_RANGE_RE = re.compile(
    rf"(?P<start>{_DATE})\s*(?:-|\u2013|—|to)\s*(?P<end>{_DATE}|present|current|now|today)", re.IGNORECASE
)
_SINGLE_DATE_RE = re.compile(_DATE, re.IGNORECASE)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"\+?\d[\d ().-]{7,18}\d")
_URL_RE = re.compile(r"(?:https?://)?(?:www\.)?[\w-]+(?:\.[\w-]+)+(?:/[\w./%#?=&-]*)?", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*(?:[-*•▪‣◦●\x7f\uf0b7]|\d{1,2}[.)])\s+")
_METRIC_RE = re.compile(r"[$€£]?\d[\d,.]*\s?(?:%|percent|[kKmMbB]\b|x\b|\+)?")
_DEGREE_RE = re.compile(
    r"\b(?:bachelor|master|doctor|ph\.?d|mba|b\.?sc|m\.?sc|b\.?a\b|m\.?a\b|b\.?eng|m\.?eng|b\.?comm?|"
    r"diploma|associate|certificate|degree|dec\b|aec\b)",
    re.IGNORECASE,
)
_SCHOOL_RE = re.compile(r"universit|college|institut|school|polytechni|academy|c[ée]gep|conservator", re.IGNORECASE)
_SECTIONS = {
    "summary": ("summary", "profile", "professional summary", "about", "about me", "objective"),
    "employment": ("experience", "work experience", "professional experience", "employment",
                   "employment history", "work history", "career history"),
    "education": ("education", "academic background", "education and training"),
    "skill": ("skills", "technical skills", "core competencies", "key skills", "competencies", "tools"),
    "certification": ("certifications", "certificates", "licenses", "licenses and certifications",
                      "certifications and licenses"),
    "language": ("languages", "spoken languages"),
    "project": ("projects", "selected projects", "personal projects"),
}
_HEADINGS = {alias: section for section, aliases in _SECTIONS.items() for alias in aliases}


# --------------------------------------------------------------- text extraction


def _docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_RESUME_BYTES * 4:
                raise ProfileError("DOCX document is too large")
            xml = archive.read(info).decode("utf-8", errors="replace")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ProfileError("not a readable DOCX file") from exc
    paragraphs = []
    for chunk in xml.split("</w:p>"):
        runs = re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", chunk)
        tabbed = chunk.count("<w:tab/>")
        text = ("\t" if tabbed else "").join(runs) if tabbed and len(runs) > 1 else "".join(runs)
        paragraphs.append(unescape(text))
    return "\n".join(paragraphs)


def _pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise ProfileError("PDF import needs pypdf (pip install -e .)") from exc
    try:
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages[:10])
    except Exception as exc:  # pypdf raises many error types on damaged files
        raise ProfileError(f"could not read the PDF: {exc.__class__.__name__}") from exc


def extract_text(path: Path) -> tuple[str, str]:
    """Return (text, source) for a .txt/.md/.pdf/.docx résumé."""
    if not path.is_file():
        raise ProfileError(f"{path} is not a file")
    if path.stat().st_size > MAX_RESUME_BYTES:
        raise ProfileError("résumé file is too large")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text, source = _pdf_text(path), "resume_pdf"
    elif suffix == ".docx":
        text, source = _docx_text(path), "resume_docx"
    elif suffix in {".txt", ".md", ".text", ""}:
        text, source = path.read_text(encoding="utf-8", errors="replace"), "resume_text"
    else:
        raise ProfileError("supported résumé formats: .txt, .md, .pdf, .docx")
    return text[:MAX_RESUME_CHARS], source


# ---------------------------------------------------------------------- parsing


def normalize_date(value: str) -> tuple[str | None, bool]:
    """'Mar 2021' -> ('2021-03', False); 'Present' -> (None, True)."""
    text = value.strip().lower().rstrip(".")
    if text in {"present", "current", "now", "today"}:
        return None, True
    if match := re.fullmatch(r"(\d{1,2})/(\d{4})", text):
        month = int(match.group(1))
        return (f"{match.group(2)}-{month:02d}" if 1 <= month <= 12 else match.group(2)), False
    if match := re.fullmatch(rf"({_MONTH_WORDS})\.? (\d{{4}})", text):
        return f"{match.group(2)}-{_MONTHS[match.group(1)]:02d}", False
    if re.fullmatch(r"\d{4}", text):
        return text, False
    return None, False


@dataclass(slots=True)
class _Block:
    header: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)


def _blocks(lines: list[str]) -> list[_Block]:
    blocks: list[_Block] = []
    current = _Block()
    for line in lines:
        if _BULLET_RE.match(line):
            current.bullets.append(_BULLET_RE.sub("", line).strip())
            continue
        if current.bullets:
            blocks.append(current)
            current = _Block()
        current.header.append(line.strip())
    if current.header or current.bullets:
        blocks.append(current)
    return blocks


def _split_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        heading = re.sub(r"[^a-z ]", "", line.lower()).strip()
        if len(line) <= 40 and heading in _HEADINGS:
            current = _HEADINGS[heading]
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _metrics(text: str) -> list[str]:
    found = []
    for match in _METRIC_RE.finditer(text):
        token = match.group(0).strip()
        if any(ch.isdigit() for ch in token) and not re.fullmatch(r"(?:19|20)\d{2}", token):
            found.append(token)
    return found[:10]


def _dates_out(text: str) -> tuple[str, dict[str, object]]:
    match = _RANGE_RE.search(text)
    if not match:
        return text, {}
    start, _ = normalize_date(match.group("start"))
    end, current = normalize_date(match.group("end"))
    rest = (text[:match.start()] + text[match.end():]).strip(" ,|\u2013—-\t")
    return rest, {"start": start, "end": end, "current": current}


def _parts(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s+(?:\||—|\u2013|-|@|at)\s+|\t|,\s+", text) if part.strip()]


def _education_parts(parts: list[str], degree: str, others: list[str]) -> dict[str, str]:
    """Pick the institution (school-like words, else the last part); the part right after the degree is the field."""
    institution = next((part for part in others if _SCHOOL_RE.search(part)), others[-1])
    rest = [part for part in others if part != institution]
    after = parts.index(degree) + 1
    field_of_study = parts[after] if after < len(parts) and parts[after] in rest else ""
    location = [cleaned for part in rest if part != field_of_study and (cleaned := part.strip(" ()[]"))]
    return {"institution": institution, "field_of_study": field_of_study, "location": ", ".join(location)}


class _Parser:
    def __init__(self, origin: str) -> None:
        self.origin = origin
        self.drafts: list[FactDraft] = []
        self.warnings: list[str] = []

    def add(self, category: str, data: dict[str, object], where: str) -> FactDraft | None:
        try:
            draft = FactDraft(category=category, data=data, provenance=f"{self.origin}: {where}",
                              verified=False, position=len(self.drafts))
        except ProfileError as exc:
            self.warnings.append(f"skipped {category} near {where!r}: {exc}")
            return None
        self.drafts.append(draft)
        return draft

    def contact(self, header: list[str], text: str) -> None:
        if header and re.fullmatch(r"[A-Za-zÀ-ÿ'\u2019.-]+(?: [A-Za-zÀ-ÿ'\u2019.-]+){1,3}", header[0]):
            first, *rest = header[0].split()
            self.add("contact", {"field": "first_name", "value": first}, header[0])
            self.add("contact", {"field": "last_name", "value": " ".join(rest)}, header[0])
        if email := _EMAIL_RE.search(text):
            self.add("contact", {"field": "email", "value": email.group(0)}, email.group(0))
        if phone := _PHONE_RE.search(" ".join(header)):
            self.add("contact", {"field": "phone", "value": phone.group(0).strip()}, phone.group(0))
        for url in _URL_RE.findall(" ".join(header)):
            if "@" in url or "." not in url:
                continue
            name = "linkedin_url" if "linkedin." in url else "github_url" if "github." in url else None
            if name:
                self.add("contact", {"field": name, "value": url}, url)

    def achievements(self, bullets: list[str], link: str, parent: FactDraft) -> None:
        for bullet in bullets:
            self.add("achievement", {"text": bullet, "metrics": _metrics(bullet), link: parent.key}, bullet[:60])

    def employment(self, lines: list[str]) -> None:
        for block in _blocks(lines):
            header = " | ".join(block.header)
            rest, dates = _dates_out(header)
            parts = [part for part in _parts(rest) if part]
            if not parts:
                self.warnings.append(f"could not read an employment entry near {header[:60]!r}")
                continue
            title = parts[0]
            employer = parts[1] if len(parts) > 1 else "Unknown employer (edit me)"
            location = ", ".join(parts[2:])
            data = {"title": title, "employer": employer, "location": location, **dates}
            parent = self.add("employment", data, header[:80])
            if parent is not None:
                self.achievements(block.bullets, "employment", parent)

    def education(self, lines: list[str]) -> None:
        for block in _blocks(lines):
            header = " | ".join(block.header + block.bullets)
            rest, dates = _dates_out(header)
            if not dates and (single := _SINGLE_DATE_RE.search(rest)):
                dates = {"end": normalize_date(single.group(0))[0]}
                rest = (rest[:single.start()] + rest[single.end():]).strip(" ,|\u2013—-")
            parts = _parts(rest)
            degree = next((part for part in parts if _DEGREE_RE.search(part)), None)
            others = [part for part in parts if part != degree]
            if degree is None or not others:
                self.warnings.append(f"could not read an education entry near {header[:60]!r}")
                continue
            dates.pop("current", None)
            self.add("education", {"degree": degree, **_education_parts(parts, degree, others), **dates}, header[:80])

    def skills(self, lines: list[str]) -> None:
        for line in lines:
            body = _BULLET_RE.sub("", line)
            body = body.split(":", 1)[1] if ":" in body and len(body.split(":", 1)[0]) < 30 else body
            for item in re.split(r"[,;|•·]", body):
                name = item.strip(" .")
                if 1 <= len(name) <= 40:
                    self.add("skill", {"name": name}, line[:60])

    def certifications(self, lines: list[str]) -> None:
        for line in lines:
            body = _BULLET_RE.sub("", line).strip()
            date_match = _SINGLE_DATE_RE.search(body)
            date = normalize_date(date_match.group(0))[0] if date_match else None
            name = (body[:date_match.start()] + body[date_match.end():]).strip(" ,|\u2013—-()") if date_match else body
            parts = _parts(name)
            if parts:
                self.add("certification", {"name": parts[0], "issuer": ", ".join(parts[1:]), "date": date}, line[:60])

    def languages(self, lines: list[str]) -> None:
        for line in lines:
            for item in re.split(r"[,;|•·]", _BULLET_RE.sub("", line)):
                match = re.match(r"\s*([^()\-\u2013:]+?)\s*(?:[(\-\u2013:]\s*([^)]*)\)?)?\s*$", item)
                if match and match.group(1):
                    self.add("language", {"name": match.group(1), "proficiency": (match.group(2) or "").strip()}, line[:60])

    def projects(self, lines: list[str]) -> None:
        for block in _blocks(lines):
            header = " | ".join(block.header)
            rest, dates = _dates_out(header)
            parts = _parts(rest)
            if not parts:
                continue
            dates.pop("current", None)
            parent = self.add("project", {"name": parts[0], "role": ", ".join(parts[1:]), **dates}, header[:80])
            if parent is not None:
                self.achievements(block.bullets, "project", parent)


def parse_resume_text(text: str, *, origin: str) -> tuple[list[FactDraft], list[str]]:
    """Return (unverified drafts, warnings)."""
    text = (text or "")[:MAX_RESUME_CHARS]
    sections = _split_sections(text.splitlines())
    parser = _Parser(origin)
    parser.contact(sections.get("header", []), text)
    if summary := " ".join(sections.get("summary", [])).strip():
        parser.add("summary", {"text": summary[:1200]}, "summary section")
    handlers = {
        "employment": parser.employment, "education": parser.education, "skill": parser.skills,
        "certification": parser.certifications, "language": parser.languages, "project": parser.projects,
    }
    for name, handler in handlers.items():
        handler(sections.get(name, []))
    if not parser.drafts:
        parser.warnings.append("no facts could be read from this résumé; use a profile YAML file instead")
    return parser.drafts, parser.warnings
