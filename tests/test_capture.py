"""Manual capture tests. They need the database (docker compose locally, a service in CI)."""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from sin_vueltas.ingest.capture import CaptureError, capture_file
from sin_vueltas.ingest.storage import LocalStorage

PAGE_URL = "https://example.test/requisitos"
PDF_URL = "https://example.test/requisitos.pdf"
SAVED_PAGE = """<html><head><meta charset="utf-8"></head><body>
<nav>Inicio</nav><main><h1>Requisitos</h1><ul><li>Cédula vigente</li></ul></main>
</body></html>""".encode()


@pytest.fixture
def catalog_documents(conn: psycopg.Connection) -> Iterator[psycopg.Connection]:
    """One HTML page and one PDF that a procedure cites, rolled back after each test."""
    with conn.transaction(force_rollback=True):
        conn.execute(
            "INSERT INTO procedures (id, name, category, institution) "
            "VALUES ('example', 'Example', 'test', 'Example institution')"
        )
        for url, document_format in [(PAGE_URL, "html"), (PDF_URL, "pdf")]:
            (document_id,) = conn.execute(
                "INSERT INTO documents (url, format, extraction, last_error) "
                "VALUES (%s, %s, %s, 'HTTP 403') RETURNING id",
                (url, document_format, "full" if document_format == "html" else "main"),
            ).fetchone()
            conn.execute(
                "INSERT INTO sources (procedure_id, document_id, url, format, status) "
                "VALUES ('example', %s, %s, %s, 'ok')",
                (document_id, url, document_format),
            )
        yield conn


def saved_file(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_a_saved_page_becomes_a_pending_manual_version(
    catalog_documents: psycopg.Connection, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path / "raw")
    page = saved_file(tmp_path, "requisitos.html", SAVED_PAGE)

    result = capture_file(catalog_documents, storage, PAGE_URL + "#seccion", page)

    assert result.is_new
    origin, status, text, key = catalog_documents.execute(
        "SELECT v.origin, v.review_status, v.extracted_text, v.storage_path "
        "FROM document_versions v JOIN documents d ON d.id = v.document_id WHERE d.url = %s",
        (PAGE_URL,),
    ).fetchone()
    assert (origin, status) == ("manual", "pending")
    assert "- Cédula vigente" in text
    assert "Inicio" not in text
    assert (tmp_path / "raw" / key).read_bytes() == SAVED_PAGE


def test_capturing_the_same_text_twice_records_one_version(
    catalog_documents: psycopg.Connection, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path / "raw")
    page = saved_file(tmp_path, "requisitos.html", SAVED_PAGE)

    capture_file(catalog_documents, storage, PAGE_URL, page)
    again = capture_file(catalog_documents, storage, PAGE_URL, page)

    assert not again.is_new


def test_capture_does_not_hide_the_crawler_error(
    catalog_documents: psycopg.Connection, tmp_path: Path
) -> None:
    page = saved_file(tmp_path, "requisitos.html", SAVED_PAGE)

    capture_file(catalog_documents, LocalStorage(tmp_path / "raw"), PAGE_URL, page)

    (last_error,) = catalog_documents.execute(
        "SELECT last_error FROM documents WHERE url = %s", (PAGE_URL,)
    ).fetchone()
    assert last_error == "HTTP 403"


def test_rejects_a_url_that_is_not_in_the_catalog(
    catalog_documents: psycopg.Connection, tmp_path: Path
) -> None:
    page = saved_file(tmp_path, "otra.html", SAVED_PAGE)

    with pytest.raises(CaptureError, match="not a document in the catalog"):
        capture_file(catalog_documents, LocalStorage(tmp_path), "https://example.test/x", page)


def test_rejects_a_file_that_is_not_a_pdf_for_a_pdf_document(
    catalog_documents: psycopg.Connection, tmp_path: Path
) -> None:
    page = saved_file(tmp_path, "requisitos.html", SAVED_PAGE)

    with pytest.raises(CaptureError, match="is not a PDF file"):
        capture_file(catalog_documents, LocalStorage(tmp_path), PDF_URL, page)


def test_rejects_a_file_without_text(catalog_documents: psycopg.Connection, tmp_path: Path) -> None:
    empty = saved_file(tmp_path, "vacia.html", b"<html><body><nav>Inicio</nav></body></html>")

    with pytest.raises(CaptureError, match="extraction failed"):
        capture_file(catalog_documents, LocalStorage(tmp_path), PAGE_URL, empty)
