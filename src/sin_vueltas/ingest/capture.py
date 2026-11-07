"""Record a page that a person saved from a browser, for sites that reject bots.

robots.txt and bot filters apply to automated clients, not to people. When a site
blocks the crawler, a person opens the official page, saves it (HTML or PDF via
"Print > Save as PDF") and captures the file. The version is recorded with origin
"manual" and goes through the same review as everything else.
"""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urldefrag

import psycopg

from sin_vueltas.ingest.fetch import (
    Document,
    Downloaded,
    Failed,
    insert_version,
    is_pdf,
    prepare_version,
)
from sin_vueltas.ingest.storage import Storage

FIND_ACTIVE_DOCUMENT = """
    SELECT d.id, d.url, d.format, d.etag, d.last_modified, d.extraction
    FROM documents AS d
    WHERE d.url = %s
      AND EXISTS (SELECT 1 FROM sources AS s WHERE s.document_id = d.id)
"""


class CaptureError(Exception):
    """The file cannot be recorded as a version of that document."""


@dataclass(frozen=True)
class CaptureResult:
    url: str
    is_new: bool
    chars: int


def capture_file(conn: psycopg.Connection, storage: Storage, url: str, path: Path) -> CaptureResult:
    """Record the saved file as a pending version of the catalog document at `url`."""
    document_url = urldefrag(url).url
    with conn.transaction():
        row = conn.execute(FIND_ACTIVE_DOCUMENT, (document_url,)).fetchone()
    if row is None:
        raise CaptureError(
            f"{document_url} is not a document in the catalog; "
            "add it to data/sources.yaml and run sync first"
        )
    document = Document(*row)

    content = path.read_bytes()
    # A page saved with "Print > Save as PDF" is read as a PDF even if the source is HTML.
    file_format = "pdf" if is_pdf(content) else "html"
    if document.format == "pdf" and file_format != "pdf":
        raise CaptureError(f"{document_url} is a PDF, but {path.name} is not a PDF file")

    outcome = prepare_version(
        file_format, Downloaded(content, etag=None, last_modified=None), document.extraction
    )
    if isinstance(outcome, Failed):
        raise CaptureError(f"{path.name}: {outcome.error}")

    with conn.transaction(), conn.cursor() as cur:
        is_new = insert_version(cur, storage, document, outcome, origin="manual")
    return CaptureResult(url=document_url, is_new=is_new, chars=len(outcome.text))
