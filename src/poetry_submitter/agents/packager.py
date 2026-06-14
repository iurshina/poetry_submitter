"""Agent that generates submission packages (cover letter, bio, formatted poem doc)."""
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from poetry_submitter.agents.model import get_model
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)
from reportlab.platypus.frames import Frame
from reportlab.platypus.doctemplate import PageTemplate, BaseDocTemplate

from poetry_submitter.models import Journal, Poem, Profile


@dataclass
class PackageDeps:
    poem: Poem
    journal: Journal
    profile: Profile


class LineSpacing(str, Enum):
    single = "single"
    one_five = "1.5"
    double = "double"


class FileFormat(str, Enum):
    docx = "docx"
    pdf = "pdf"
    rtf = "rtf"


class FormattingRequirements(BaseModel):
    blind: bool = Field(
        default=False,
        description="Remove author name/contact from the document itself",
    )
    font_name: str = Field(
        default="Times New Roman",
        description="Font family, e.g. 'Times New Roman', 'Courier New', 'Arial'",
    )
    font_size_pt: int = Field(
        default=12,
        description="Font size in points",
    )
    line_spacing: LineSpacing = Field(
        default=LineSpacing.double,
        description="Line spacing for poem body",
    )
    margin_inches: float = Field(
        default=1.0,
        description="Page margin in inches (applied to all sides)",
    )
    title_centered: bool = Field(
        default=False,
        description="Whether the poem title should be centred",
    )
    include_page_numbers: bool = Field(
        default=False,
        description="Add page numbers to the footer",
    )
    file_format: FileFormat = Field(
        default=FileFormat.pdf,
        description=(
            "Required file format. If the journal accepts multiple formats, choose pdf. "
            "Only use docx when pdf is explicitly excluded."
        ),
    )
    max_poems_per_submission: Optional[int] = Field(
        default=None,
        description="How many poems the journal accepts per submission, if specified",
    )
    word_count_limit: Optional[int] = Field(
        default=None,
        description="Word or line count limit if specified",
    )
    extra_notes: Optional[str] = Field(
        default=None,
        description="Any other formatting requirements not captured above",
    )


class DraftedPackage(BaseModel):
    cover_letter: str
    bio: str
    formatting: FormattingRequirements


packager_agent = Agent(
    get_model(),
    deps_type=PackageDeps,
    output_type=DraftedPackage,
    system_prompt="""You are helping a poet prepare a submission package for a literary journal.

COVER LETTER: 3-4 sentences, professional but not stiff. Name the poem and journal.
Do not use phrases like "I am excited to submit" or "I hope you will consider".

BIO: Adapt the poet's default bio to fit this journal's tone (indie, academic, experimental, etc.).
Stay under 75 words unless the guidelines specify a different length.

FORMATTING: Extract every formatting requirement from the guidelines and populate the
FormattingRequirements fields precisely. When guidelines are silent on a field, use the default.
Common things to look for: blind/anonymous submission, font (TNR vs Courier), double-spacing,
margin size, file type (docx/pdf/rtf), poems-per-submission limit.

FILE FORMAT RULE: if the journal accepts multiple formats or is silent on format, always choose pdf.
Only choose docx if pdf is explicitly forbidden.""",
)


@packager_agent.system_prompt
async def add_package_context(ctx) -> str:
    deps: PackageDeps = ctx.deps
    return (
        f"POET: {deps.profile.name} <{deps.profile.email}>\n"
        f"Website: {deps.profile.website or 'none'}\n\n"
        f"DEFAULT SHORT BIO:\n{deps.profile.default_bio_short or 'not set'}\n\n"
        f"DEFAULT LONG BIO:\n{deps.profile.default_bio_long or 'not set'}\n\n"
        f"POEM TITLE: {deps.poem.title}\n"
        f"POEM:\n{deps.poem.body}\n\n"
        f"JOURNAL: {deps.journal.name}\n"
        f"SUBMISSION GUIDELINES:\n{deps.journal.guidelines_text or 'Not available — use defaults.'}"
    )


async def draft_package(deps: PackageDeps) -> DraftedPackage:
    result = await packager_agent.run(
        "Draft the submission package for this poem and journal.",
        deps=deps,
    )
    return result.output


def build_document(poem: Poem, profile: Profile, fmt: FormattingRequirements, output_dir: Path) -> Path:
    safe_title = poem.title.replace("/", "-")
    if fmt.file_format == FileFormat.pdf:
        path = output_dir / f"{safe_title}.pdf"
        return build_pdf(poem, profile, fmt, path)
    else:
        path = output_dir / f"{safe_title}.docx"
        return build_docx(poem, profile, fmt, path)


# --- PDF ---

_FONT_MAP = {
    "times new roman": ("Times-Roman", "Times-Bold"),
    "times": ("Times-Roman", "Times-Bold"),
    "courier new": ("Courier", "Courier-Bold"),
    "courier": ("Courier", "Courier-Bold"),
    "arial": ("Helvetica", "Helvetica-Bold"),
    "helvetica": ("Helvetica", "Helvetica-Bold"),
}

_LEADING_MULTIPLIER = {
    LineSpacing.single: 1.2,
    LineSpacing.one_five: 1.5,
    LineSpacing.double: 2.0,
}


def build_pdf(poem: Poem, profile: Profile, fmt: FormattingRequirements, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rl_font, rl_font_bold = _FONT_MAP.get(fmt.font_name.lower(), ("Times-Roman", "Times-Bold"))
    size = fmt.font_size_pt
    leading = size * _LEADING_MULTIPLIER[fmt.line_spacing]
    margin = fmt.margin_inches * inch

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=LETTER,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
    )

    base_style = ParagraphStyle(
        "poem",
        fontName=rl_font,
        fontSize=size,
        leading=leading,
        spaceBefore=0,
        spaceAfter=0,
    )
    title_style = ParagraphStyle(
        "title",
        parent=base_style,
        fontName=rl_font_bold,
        alignment=1 if fmt.title_centered else 0,
        spaceAfter=leading,
    )
    author_style = ParagraphStyle(
        "author",
        parent=base_style,
        spaceAfter=4,
    )

    story = []

    if not fmt.blind:
        story.append(Paragraph(profile.name, author_style))
        if profile.email:
            story.append(Paragraph(profile.email, author_style))
        if profile.website:
            story.append(Paragraph(profile.website, author_style))
        story.append(Spacer(1, leading))

    story.append(Paragraph(poem.title, title_style))

    stanzas = poem.body.split("\n\n")
    for i, stanza in enumerate(stanzas):
        for line in stanza.strip().splitlines():
            # Preserve leading spaces using non-breaking space trick
            escaped = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if escaped.startswith(" "):
                escaped = escaped.replace(" ", " ", len(escaped) - len(escaped.lstrip(" ")))
            story.append(Paragraph(escaped, base_style))
        if i < len(stanzas) - 1:
            story.append(Spacer(1, leading))

    if fmt.include_page_numbers:
        doc.build(story, onFirstPage=_add_page_number, onLaterPages=_add_page_number)
    else:
        doc.build(story)

    return output_path


def _add_page_number(canvas, doc):
    canvas.saveState()
    canvas.setFont("Times-Roman", 9)
    canvas.drawCentredString(LETTER[0] / 2, 0.5 * inch, str(canvas.getPageNumber()))
    canvas.restoreState()


# --- DOCX ---

def build_docx(poem: Poem, profile: Profile, fmt: FormattingRequirements, output_path: Path) -> Path:
    doc = Document()

    for section in doc.sections:
        m = Inches(fmt.margin_inches)
        section.top_margin = m
        section.bottom_margin = m
        section.left_margin = m
        section.right_margin = m

    style = doc.styles["Normal"]
    style.font.name = fmt.font_name
    style.font.size = Pt(fmt.font_size_pt)

    if fmt.include_page_numbers:
        _add_docx_page_numbers(doc)

    if not fmt.blind:
        doc.add_paragraph(profile.name)
        if profile.email:
            doc.add_paragraph(profile.email)
        if profile.website:
            doc.add_paragraph(profile.website)
        doc.add_paragraph("")

    title_para = doc.add_paragraph(poem.title)
    title_para.runs[0].bold = True
    if fmt.title_centered:
        title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph("")

    spacing = _docx_line_spacing(fmt.line_spacing)
    for stanza in poem.body.split("\n\n"):
        lines = stanza.strip().splitlines()
        for i, line in enumerate(lines):
            para = doc.add_paragraph(line)
            para.paragraph_format.line_spacing = spacing
            para.paragraph_format.space_before = Pt(0)
            para.paragraph_format.space_after = Pt(fmt.font_size_pt) if i == len(lines) - 1 else Pt(0)
        doc.add_paragraph("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    return output_path


def _docx_line_spacing(spacing: LineSpacing) -> float:
    return {
        LineSpacing.single: Pt(12),
        LineSpacing.one_five: Pt(18),
        LineSpacing.double: Pt(24),
    }[spacing]


def _add_docx_page_numbers(doc: Document) -> None:
    for section in doc.sections:
        footer = section.footer
        para = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = para.add_run()
        fldChar1 = OxmlElement("w:fldChar")
        fldChar1.set(qn("w:fldCharType"), "begin")
        instrText = OxmlElement("w:instrText")
        instrText.text = "PAGE"
        fldChar2 = OxmlElement("w:fldChar")
        fldChar2.set(qn("w:fldCharType"), "end")
        run._r.append(fldChar1)
        run._r.append(instrText)
        run._r.append(fldChar2)
