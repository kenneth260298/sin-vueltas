"""Turn downloaded HTML and PDF files into clean text, and fingerprint that text."""

import hashlib
import io
import re
import unicodedata
from typing import Literal

import pdfplumber
import trafilatura
from lxml import etree
from lxml import html as lxml_html

# main: only the main content, as chosen by trafilatura (the default).
# full: all visible text without page chrome; for pages whose content sits in
#       collapsed sections (FAQ accordions) that main-content detection drops.
ExtractionMode = Literal["main", "full"]

# Page chrome that never holds requirements and often changes on every visit.
CHROME_TAGS = (
    "head", "script", "style", "noscript", "template", "iframe", "svg",
    "nav", "header", "footer", "aside", "form",
)  # fmt: skip
BLOCK_TAGS = (
    "p", "div", "section", "article", "details", "summary", "blockquote",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "dl", "dt", "dd", "table", "tr", "br",
)  # fmt: skip


class ExtractionError(Exception):
    """The document has no text the assistant could use."""


def extract_text(document_format: str, content: bytes, mode: ExtractionMode = "main") -> str:
    """Return the readable text of a document, without menus, scripts or forms."""
    if document_format == "html":
        raw = _full_html_text(content) if mode == "full" else _main_html_text(content)
        text = clean(raw)
        if not text:
            raise ExtractionError("no main text found in the page")
    elif document_format == "pdf":
        text = clean(_pdf_text(content))
        if not text:
            raise ExtractionError("no text layer found (scanned PDF? it needs OCR)")
    else:
        raise ExtractionError(f"unsupported format: {document_format}")
    return text


def clean(text: str) -> str:
    """Normalize Unicode and spacing, keeping paragraph breaks."""
    text = unicodedata.normalize("NFC", text)
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def text_fingerprint(text: str) -> str:
    """Hash of the text that ignores spacing, so only real content changes count."""
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def _main_html_text(content: bytes) -> str:
    # Markdown keeps headings, lists and tables, which helps chunking and the LLM later.
    text = trafilatura.extract(
        content,
        output_format="markdown",
        include_tables=True,
        include_links=False,
        include_comments=False,
        favor_recall=True,
    )
    return text or ""


def _decode_html(content: bytes) -> str | bytes:
    """Decode UTF-8 ourselves: pages often declare their charset only in the HTTP header."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content  # Let lxml use the charset declared in the page.
    # lxml rejects str input that still carries an XML encoding declaration.
    return re.sub(r"^\s*<\?xml[^>]*\?>", "", text)


def _full_html_text(content: bytes) -> str:
    """All visible text of the page, including collapsed sections, without page chrome."""
    try:
        tree = lxml_html.fromstring(_decode_html(content))
    except (etree.ParserError, ValueError):
        return ""
    for element in tree.xpath(" | ".join(f"//{tag}" for tag in CHROME_TAGS)):
        element.drop_tree()
    for element in tree.iter(*BLOCK_TAGS):
        element.tail = "\n" + (element.tail or "")
    for item in tree.iter("li"):
        item.text = "- " + (item.text or "")
    return tree.text_content()


def _pdf_text(content: bytes) -> str:
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            return "\n\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as error:  # pdfminer raises many error types for damaged files.
        raise ExtractionError(f"unreadable PDF: {error}") from error
