"""Render résumé / cover-letter documents to PDF (ReportLab) and DOCX.

Pure Python, no browser: works on a headless VM and in tests. The layout is
deliberately ATS-friendly: one column, real text (no images or tables),
standard fonts, plain section headings, simple bullets. Output is
byte-for-byte reproducible for the same document (no timestamps).
"""

from __future__ import annotations

import io
import zipfile
from html import escape as _html_escape
from pathlib import Path
from typing import Any

from app.materials import cover_letter_body, resume_lines


def escape(text: str) -> str:
    """Escape text for XML element content (ReportLab paragraphs and DOCX runs)."""
    return _html_escape(text, quote=False)


class RenderUnavailable(RuntimeError):
    """ReportLab is not installed."""


# ------------------------------------------------------------------------- lines


def _letter_lines(doc: dict[str, Any]) -> list[tuple[str, str]]:
    lines = [("name", doc["name"]), ("contact", " | ".join(doc["signature"][1:])), ("space", ""),
             ("body", doc["greeting"])]
    lines += [("para", text) for text in cover_letter_body(doc).split("\n\n")]
    lines += [("para", doc["closing"]), ("body", doc["signature"][0])]
    return lines


def document_lines(doc: dict[str, Any]) -> list[tuple[str, str]]:
    return resume_lines(doc) if doc["kind"] == "resume" else _letter_lines(doc)


# --------------------------------------------------------------------------- PDF


_FONTS = {"regular": "HXSans", "bold": "HXSans-Bold", "italic": "HXSans-Italic"}


def _register_fonts() -> dict[str, str]:
    """Embed Bitstream Vera (bundled with ReportLab) so the PDF carries a Unicode text layer.

    Embedded TrueType fonts get a ToUnicode map, which keeps bullets, dashes and
    accented names intact when an ATS extracts the text. Falls back to the
    built-in Helvetica if the bundled fonts are missing.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFError, TTFont

    if _FONTS["regular"] in pdfmetrics.getRegisteredFontNames():
        return _FONTS
    try:
        for key, filename in (("regular", "Vera.ttf"), ("bold", "VeraBd.ttf"), ("italic", "VeraIt.ttf")):
            pdfmetrics.registerFont(TTFont(_FONTS[key], filename))
    except (OSError, TTFError):  # pragma: no cover - bundled fonts are part of the ReportLab wheel
        return {"regular": "Helvetica", "bold": "Helvetica-Bold", "italic": "Helvetica-Oblique"}
    return _FONTS


def _styles() -> dict[str, Any]:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle

    fonts = _register_fonts()
    base = ParagraphStyle("base", fontName=fonts["regular"], fontSize=9.6, leading=13.2, textColor=HexColor("#111111"))
    return {
        "name": ParagraphStyle("name", parent=base, fontName=fonts["bold"], fontSize=17, leading=21),
        "contact": ParagraphStyle("contact", parent=base, fontSize=9, leading=12, textColor=HexColor("#333333"),
                                  spaceAfter=4),
        "heading": ParagraphStyle("heading", parent=base, fontName=fonts["bold"], fontSize=10.6, leading=14,
                                  spaceBefore=9, spaceAfter=2),
        "entry": ParagraphStyle("entry", parent=base, fontName=fonts["bold"], spaceBefore=4),
        "dates": ParagraphStyle("dates", parent=base, fontName=fonts["italic"], fontSize=8.8, leading=12,
                                textColor=HexColor("#444444")),
        "bullet": ParagraphStyle("bullet", parent=base, leftIndent=12, bulletIndent=2, bulletFontName=fonts["regular"]),
        "body": base,
        "para": ParagraphStyle("para", parent=base, leading=14.5, spaceBefore=8),
        "space": ParagraphStyle("space", parent=base, spaceAfter=8),
    }


def _flowables(lines: list[tuple[str, str]]) -> list[Any]:
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import HRFlowable, Paragraph

    styles = _styles()
    out: list[Any] = []
    for style, text in lines:
        if style == "bullet":
            out.append(Paragraph(escape(text), styles["bullet"], bulletText="•"))
            continue
        out.append(Paragraph(escape(text) or "&nbsp;", styles[style]))
        if style == "heading":
            out.append(HRFlowable(width="100%", thickness=0.6, color=HexColor("#999999"), spaceBefore=1, spaceAfter=3))
    return out


def render_pdf_bytes(doc: dict[str, Any]) -> bytes:
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise RenderUnavailable("PDF rendering needs ReportLab (pip install -e .)") from exc
    buffer = io.BytesIO()
    title = "Résumé" if doc["kind"] == "resume" else "Cover letter"
    template = SimpleDocTemplate(
        buffer, pagesize=LETTER, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.65 * inch, bottomMargin=0.65 * inch,
        title=f"{doc['name']} — {title}", author=doc["name"], subject=title, creator="HunterXJob", invariant=1,
    )
    template.build(_flowables(document_lines(doc)))
    return buffer.getvalue()


# -------------------------------------------------------------------------- DOCX

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)
# style -> (bold, italic, half-point size, space-before twips)
_DOCX_STYLE = {
    "name": (True, False, 34, 0), "contact": (False, False, 18, 0), "heading": (True, False, 22, 200),
    "entry": (True, False, 20, 80), "dates": (False, True, 18, 0), "bullet": (False, False, 20, 0),
    "body": (False, False, 20, 0), "para": (False, False, 20, 160), "space": (False, False, 20, 0),
}


def _docx_paragraph(style: str, text: str) -> str:
    bold, italic, size, before = _DOCX_STYLE[style]
    props = f'<w:spacing w:before="{before}" w:after="40"/>'
    if style == "bullet":
        props += '<w:ind w:left="360" w:hanging="216"/>'
        text = f"• {text}"
    if style == "heading":
        props += '<w:pBdr><w:bottom w:val="single" w:sz="4" w:space="1" w:color="999999"/></w:pBdr>'
    run = ("<w:b/>" if bold else "") + ("<w:i/>" if italic else "") + f'<w:sz w:val="{size}"/>'
    return (f'<w:p><w:pPr>{props}</w:pPr><w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/>{run}</w:rPr>'
            f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>')


def render_docx_bytes(doc: dict[str, Any]) -> bytes:
    body = "".join(_docx_paragraph(style, text) for style, text in document_lines(doc))
    xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{_W}"><w:body>{body}'
           '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
           '<w:pgMar w:top="1000" w:right="1080" w:bottom="1000" w:left="1080"/></w:sectPr></w:body></w:document>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in (("[Content_Types].xml", _CONTENT_TYPES), ("_rels/.rels", _RELS), ("word/document.xml", xml)):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return buffer.getvalue()


def write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
