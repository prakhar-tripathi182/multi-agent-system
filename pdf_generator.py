"""
Turns the finished report (+ critic review) into a downloadable A4 PDF.

Pipeline: Markdown text --(Python-Markdown)--> HTML --(xhtml2pdf)--> PDF bytes.
xhtml2pdf only understands a subset of CSS, so the template below is kept simple.
"""

import re
from datetime import datetime
from html import escape
from io import BytesIO

import markdown as md
from xhtml2pdf import pisa


class PDFGenerationError(Exception):
    """Raised when xhtml2pdf cannot build the PDF."""


# One template holds all the styling. {{ and }} are literal braces (str.format escaping).
PDF_TEMPLATE = """
<html>
<head>
<meta charset="utf-8" />
<style>
  @page {{
    size: A4;
    margin: 2.4cm 2.2cm;
    @frame footer_frame {{
      -pdf-frame-content: footer_content;
      bottom: 1cm;
      margin-left: 2.2cm;
      margin-right: 2.2cm;
      height: 1cm;
    }}
  }}
  body {{ font-family: "Times New Roman", Times, serif; font-size: 11pt; line-height: 1.5; color: #1a1a1a; }}
  .eyebrow {{ font-family: Helvetica, sans-serif; font-size: 8.5pt; letter-spacing: 2px; color: #697665; }}
  h1.title {{ font-size: 21pt; margin: 6px 0 2px; }}
  .meta {{
    font-family: Helvetica, sans-serif; font-size: 9pt; color: #666666;
    margin-bottom: 18px; border-bottom: 1pt solid #cccccc; padding-bottom: 12px;
  }}
  h1 {{ font-size: 17pt; margin-top: 20px; }}
  h2 {{ font-size: 14pt; color: #393a34; margin-top: 22px; margin-bottom: 6px;
        border-bottom: 0.5pt solid #dddddd; padding-bottom: 4px; }}
  h3 {{ font-size: 12pt; margin-top: 14px; }}
  p {{ margin: 0 0 10px; }}
  ul, ol {{ margin: 0 0 10px; padding-left: 20px; }}
  li {{ margin-bottom: 4px; }}
  a {{ color: #2a5db0; }}
  strong {{ color: #11150c; }}
  .section-label {{
    font-family: Helvetica, sans-serif; font-size: 9pt; letter-spacing: 1.5px;
    text-transform: uppercase; color: #999999; margin-top: 30px; margin-bottom: 8px;
  }}
  .score {{ font-family: Helvetica, sans-serif; font-size: 16pt; color: #11150c; margin-bottom: 8px; }}
  #footer_content {{ font-family: Helvetica, sans-serif; font-size: 8pt; color: #999999; text-align: center; }}
</style>
</head>
<body>
  <div class="eyebrow">MULTI-AGENT RESEARCH PIPELINE</div>
  <h1 class="title">{topic}</h1>
  <div class="meta">Generated on {date}</div>

  {report_html}
  {critique_section}

  <div id="footer_content">Page <pdf:pagenumber /> of <pdf:pagecount /></div>
</body>
</html>
"""


# The PDF built-in Times font only supports Latin-1/WinAnsi characters
# (missing glyphs print as black squares), so swap them for plain equivalents.
_UNSUPPORTED_CHARS = {
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2212": "-",  # odd hyphens / minus
    "\u202f": " ", "\u2009": " ", "\u200a": " ", "\u2007": " ",   # thin / narrow spaces
    "\u200b": "", "\u2060": "", "\ufeff": "",                     # zero-width characters
    # Subscripts & superscripts not in Latin-1 (e.g. chemical formulas, units)
    "\u2070": "0", "\u2074": "4", "\u2075": "5", "\u2076": "6",
    "\u2077": "7", "\u2078": "8", "\u2079": "9", "\u207a": "+", "\u207b": "-",
    "\u2080": "0", "\u2081": "1", "\u2082": "2", "\u2083": "3", "\u2084": "4",
    "\u2085": "5", "\u2086": "6", "\u2087": "7", "\u2088": "8", "\u2089": "9",
    # Common scientific symbols
    "\u2192": "->", "\u2190": "<-", "\u2265": ">=", "\u2264": "<=",
    "\u2248": "~", "\u2260": "!=",
}


_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")


def clean_text(text: str) -> str:
    return text.translate(str.maketrans(_UNSUPPORTED_CHARS))


def ensure_blank_line_before_lists(text: str) -> str:
    """
    Python-Markdown (unlike the browser renderer) only starts a list after a blank
    line. The LLM often writes "Strengths:" directly followed by "- item", which
    would collapse into one paragraph, so insert the missing blank line.
    """
    out = []
    for line in text.splitlines():
        if out and _LIST_ITEM.match(line) and out[-1].strip() and not _LIST_ITEM.match(out[-1]):
            out.append("")
        out.append(line)
    return "\n".join(out)


def extract_score(critique_markdown: str):
    """Find 'Score: 8/10' in the critic text. Returns e.g. '8/10', or None if absent."""
    match = re.search(r"Score:\s*\**\s*(\d+(?:\.\d+)?\s*/\s*10)", critique_markdown, re.IGNORECASE)
    return match.group(1).replace(" ", "") if match else None


def build_report_pdf(topic: str, report_markdown: str, critique_markdown: str = None) -> bytes:
    """Take the markdown from the writer/critic chains and return PDF bytes."""
    if not report_markdown or not report_markdown.strip():
        raise PDFGenerationError("The report is empty.")

    report_markdown = clean_text(report_markdown).replace("<", "&lt;")
    critique_markdown = (
        clean_text(critique_markdown).replace("<", "&lt;")
        if critique_markdown
        else critique_markdown
    )
    topic = clean_text(topic).strip() or "Research Report"
    report_markdown = ensure_blank_line_before_lists(report_markdown)
    if critique_markdown:
        critique_markdown = ensure_blank_line_before_lists(critique_markdown)

    # Markdown -> HTML (tables/fenced code enabled for safety; links/lists/headings are default)
    report_html = md.markdown(report_markdown, extensions=["extra"])

    critique_section = ""
    if critique_markdown and critique_markdown.strip():
        score = extract_score(critique_markdown)
        score_html = f'<div class="score">Critic score: <strong>{escape(score)}</strong></div>' if score else ""
        critique_html = md.markdown(critique_markdown, extensions=["extra"])
        critique_section = (
            f'<div class="section-label">Critic\'s Review</div>{score_html}{critique_html}'
        )

    html = PDF_TEMPLATE.format(
        topic=escape(topic),  # topic is user input: escape so it can't inject HTML
        date=datetime.now().strftime("%B %d, %Y"),
        report_html=report_html,
        critique_section=critique_section,
    )

    output = BytesIO()
    result = pisa.CreatePDF(html, dest=output)
    if result.err:
        raise PDFGenerationError(f"xhtml2pdf reported {result.err} error(s) while building the PDF.")
    return output.getvalue()
