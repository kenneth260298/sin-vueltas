"""Turn downloaded HTML and PDF files into clean text, and fingerprint that text."""

import hashlib
import io
import re
import unicodedata

import pdfplumber
import trafilatura


class ExtractionError(Exception):
    """The document has no text the assistant could use."""


def extract_text(document_format: str, content: bytes) -> str:
    """Return the readable text of a document, without menus, scripts or forms."""
    if document_format == "html":
        text = clean(_html_text(content))
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


def _html_text(content: bytes) -> str:
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


def _pdf_text(content: bytes) -> str:
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            return "\n\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as error:  # pdfminer raises many error types for damaged files.
        raise ExtractionError(f"unreadable PDF: {error}") from error
