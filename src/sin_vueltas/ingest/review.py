"""Human review of new document versions.

Nothing reaches the assistant until a person approves it. Approving a version
supersedes the previously approved one for the same document, so each document
has at most one approved version (the database enforces it with a partial index).
"""

import difflib
from dataclasses import dataclass
from datetime import datetime

import psycopg

LIST_PENDING = """
    SELECT v.id, d.url, v.origin, v.fetched_at, length(v.extracted_text),
           EXISTS (
               SELECT 1 FROM document_versions AS a
               WHERE a.document_id = v.document_id AND a.review_status = 'approved'
           )
    FROM document_versions AS v
    JOIN documents AS d ON d.id = v.document_id
    WHERE v.review_status = 'pending'
      AND EXISTS (SELECT 1 FROM sources AS s WHERE s.document_id = d.id)
    ORDER BY d.url, v.fetched_at
"""

GET_VERSION = """
    SELECT v.id, v.document_id, d.url, v.origin, v.review_status, v.extracted_text,
           (SELECT a.extracted_text FROM document_versions AS a
            WHERE a.document_id = v.document_id AND a.review_status = 'approved')
    FROM document_versions AS v
    JOIN documents AS d ON d.id = v.document_id
    WHERE v.id = %s
"""

SUPERSEDE_APPROVED = """
    UPDATE document_versions SET review_status = 'superseded'
    WHERE document_id = %s AND review_status = 'approved'
"""

SET_REVIEW_STATUS = """
    UPDATE document_versions
    SET review_status = %s, reviewed_at = now(), reviewed_by = %s
    WHERE id = %s
"""


class ReviewError(Exception):
    """The version cannot be reviewed as requested."""


@dataclass(frozen=True)
class PendingVersion:
    id: int
    url: str
    origin: str
    fetched_at: datetime
    chars: int
    replaces_approved: bool


@dataclass(frozen=True)
class VersionDetail:
    id: int
    document_id: int
    url: str
    origin: str
    review_status: str
    text: str
    approved_text: str | None


def list_pending(conn: psycopg.Connection) -> list[PendingVersion]:
    """Pending versions of documents the catalog still uses."""
    return [PendingVersion(*row) for row in conn.execute(LIST_PENDING).fetchall()]


def get_version(conn: psycopg.Connection, version_id: int) -> VersionDetail:
    row = conn.execute(GET_VERSION, (version_id,)).fetchone()
    if row is None:
        raise ReviewError(f"version {version_id} does not exist")
    return VersionDetail(*row)


def render_changes(detail: VersionDetail) -> str:
    """The diff against the approved text, or the whole text for a first version."""
    if detail.approved_text is None:
        return detail.text
    diff = difflib.unified_diff(
        detail.approved_text.splitlines(),
        detail.text.splitlines(),
        fromfile="approved",
        tofile=f"version {detail.id}",
        lineterm="",
    )
    return "\n".join(diff) or "(no differences in the text)"


def approve(conn: psycopg.Connection, version_ids: list[int], reviewer: str) -> None:
    """Approve pending versions; each one replaces the approved version of its document."""
    with conn.transaction():
        for version_id in version_ids:
            detail = _pending(conn, version_id)
            conn.execute(SUPERSEDE_APPROVED, (detail.document_id,))
            conn.execute(SET_REVIEW_STATUS, ("approved", reviewer, version_id))


def reject(conn: psycopg.Connection, version_ids: list[int], reviewer: str) -> None:
    """Reject pending versions. The approved version, if any, keeps serving answers."""
    with conn.transaction():
        for version_id in version_ids:
            _pending(conn, version_id)
            conn.execute(SET_REVIEW_STATUS, ("rejected", reviewer, version_id))


def _pending(conn: psycopg.Connection, version_id: int) -> VersionDetail:
    detail = get_version(conn, version_id)
    if detail.review_status != "pending":
        raise ReviewError(f"version {version_id} is {detail.review_status}, not pending")
    return detail
