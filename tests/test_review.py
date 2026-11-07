"""Review tests. The diff tests run anywhere; the rest need the database."""

from collections.abc import Callable, Iterator

import psycopg
import pytest

from sin_vueltas.ingest.review import (
    ReviewError,
    VersionDetail,
    approve,
    get_version,
    list_pending,
    reject,
    render_changes,
)

PAGE_URL = "https://example.test/requisitos"

AddVersion = Callable[[str], int]


def detail(text: str, approved_text: str | None) -> VersionDetail:
    return VersionDetail(7, 1, PAGE_URL, "crawler", "pending", text, approved_text)


def test_a_first_version_shows_its_whole_text() -> None:
    assert render_changes(detail("Cédula vigente", None)) == "Cédula vigente"


def test_an_update_shows_the_diff_against_the_approved_text() -> None:
    changes = render_changes(detail("Cédula vigente\nDictamen médico", "Cédula vigente"))

    assert "+Dictamen médico" in changes
    assert " Cédula vigente" in changes


def test_an_update_with_the_same_text_says_so() -> None:
    assert render_changes(detail("Cédula", "Cédula")) == "(no differences in the text)"


@pytest.fixture
def add_version(conn: psycopg.Connection) -> Iterator[AddVersion]:
    """Adds pending versions to a cited document, rolled back after each test."""
    with conn.transaction(force_rollback=True):
        conn.execute(
            "INSERT INTO procedures (id, name, category, institution) "
            "VALUES ('example', 'Example', 'test', 'Example institution')"
        )
        (document_id,) = conn.execute(
            "INSERT INTO documents (url, format) VALUES (%s, 'html') RETURNING id", (PAGE_URL,)
        ).fetchone()
        conn.execute(
            "INSERT INTO sources (procedure_id, document_id, url, format, status) "
            "VALUES ('example', %s, %s, 'html', 'ok')",
            (document_id, PAGE_URL),
        )

        def add(text: str) -> int:
            (version_id,) = conn.execute(
                "INSERT INTO document_versions "
                "(document_id, content_hash, storage_path, extracted_text, text_hash) "
                "VALUES (%s, md5(%s), 'raw.html', %s, md5(%s)) RETURNING id",
                (document_id, text, text, text),
            ).fetchone()
            return version_id

        yield add


def status_of(conn: psycopg.Connection, version_id: int) -> str:
    return get_version(conn, version_id).review_status


def test_pending_versions_are_listed(conn: psycopg.Connection, add_version: AddVersion) -> None:
    first = add_version("Cédula vigente")

    listed = {version.id: version for version in list_pending(conn)}

    assert listed[first].url == PAGE_URL
    assert not listed[first].replaces_approved


def test_approving_supersedes_the_previous_approved_version(
    conn: psycopg.Connection, add_version: AddVersion
) -> None:
    first = add_version("Cédula vigente")
    second = add_version("Cédula vigente y dictamen médico")

    approve(conn, [first], reviewer="kenneth")
    assert {v.id: v for v in list_pending(conn)}[second].replaces_approved
    approve(conn, [second], reviewer="kenneth")

    assert status_of(conn, first) == "superseded"
    assert status_of(conn, second) == "approved"
    (reviewed_by,) = conn.execute(
        "SELECT reviewed_by FROM document_versions WHERE id = %s", (second,)
    ).fetchone()
    assert reviewed_by == "kenneth"


def test_a_failed_approval_changes_nothing(
    conn: psycopg.Connection, add_version: AddVersion
) -> None:
    first = add_version("Cédula vigente")
    second = add_version("Otro texto")
    reject(conn, [second], reviewer="kenneth")

    with pytest.raises(ReviewError, match="is rejected, not pending"):
        approve(conn, [first, second], reviewer="kenneth")

    assert status_of(conn, first) == "pending"


def test_rejecting_keeps_the_approved_version(
    conn: psycopg.Connection, add_version: AddVersion
) -> None:
    first = add_version("Cédula vigente")
    second = add_version("Texto con un error")
    approve(conn, [first], reviewer="kenneth")

    reject(conn, [second], reviewer="kenneth")

    assert status_of(conn, first) == "approved"
    assert status_of(conn, second) == "rejected"


def test_an_unknown_version_is_an_error(conn: psycopg.Connection) -> None:
    with pytest.raises(ReviewError, match="does not exist"):
        get_version(conn, -1)
