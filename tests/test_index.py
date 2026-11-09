"""Index tests with a fake embedder. They need the database (with pgvector)."""

import hashlib
from collections.abc import Iterator

import psycopg
import pytest

from sin_vueltas.ingest.embed import EMBEDDING_DIMENSIONS
from sin_vueltas.ingest.index import build_index

PAGE_URL = "https://example.test/licencias"


class FakeEmbedder:
    """Deterministic vectors without calling any API."""

    def __init__(self, model: str = "fake/v1") -> None:
        self.model = model
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [digest[i % len(digest)] / 255 for i in range(EMBEDDING_DIMENSIONS)]


@pytest.fixture
def approved_document(conn: psycopg.Connection) -> Iterator[int]:
    """A cited document with one approved version, rolled back after each test."""
    with conn.transaction(force_rollback=True):
        conn.execute(
            "INSERT INTO procedures (id, name, category, institution) "
            "VALUES ('example-licencia', 'Licencia de ejemplo', 'test', 'Example')"
        )
        (document_id,) = conn.execute(
            "INSERT INTO documents (url, format) VALUES (%s, 'html') RETURNING id", (PAGE_URL,)
        ).fetchone()
        conn.execute(
            "INSERT INTO sources (procedure_id, document_id, url, format, status) "
            "VALUES ('example-licencia', %s, %s, 'html', 'ok')",
            (document_id, PAGE_URL),
        )
        add_version(conn, document_id, "# Requisitos\n\nCédula vigente.", "approved")
        yield document_id


def add_version(conn: psycopg.Connection, document_id: int, text: str, status: str) -> int:
    (version_id,) = conn.execute(
        "INSERT INTO document_versions "
        "(document_id, content_hash, storage_path, extracted_text, text_hash, review_status) "
        "VALUES (%s, md5(%s), 'raw.html', %s, md5(%s), %s) RETURNING id",
        (document_id, text, text, text, status),
    ).fetchone()
    return version_id


def chunks_of(conn: psycopg.Connection, document_id: int) -> list[tuple]:
    return conn.execute(
        "SELECT c.heading, c.content, c.embedding IS NOT NULL, c.embedding_model "
        "FROM chunks c JOIN document_versions v ON v.id = c.version_id "
        "WHERE v.document_id = %s ORDER BY c.position",
        (document_id,),
    ).fetchall()


def test_approved_versions_are_chunked_and_embedded(
    conn: psycopg.Connection, approved_document: int
) -> None:
    build_index(conn, FakeEmbedder())

    assert chunks_of(conn, approved_document) == [
        ("Requisitos", "# Requisitos\n\nCédula vigente.", True, "fake/v1")
    ]


def test_running_again_does_nothing_new(conn: psycopg.Connection, approved_document: int) -> None:
    build_index(conn, FakeEmbedder())
    embedder = FakeEmbedder()

    result = build_index(conn, embedder)

    assert (result.new_chunks, result.embedded_chunks, embedder.calls) == (0, 0, 0)


def test_no_embed_only_chunks(conn: psycopg.Connection, approved_document: int) -> None:
    build_index(conn, None)

    assert [row[2:] for row in chunks_of(conn, approved_document)] == [(False, None)]


def test_a_newly_approved_version_replaces_the_old_chunks(
    conn: psycopg.Connection, approved_document: int
) -> None:
    build_index(conn, FakeEmbedder())
    conn.execute(
        "UPDATE document_versions SET review_status = 'superseded' WHERE document_id = %s",
        (approved_document,),
    )
    add_version(conn, approved_document, "Cédula y dictamen médico.", "approved")

    build_index(conn, FakeEmbedder())

    assert [row[1] for row in chunks_of(conn, approved_document)] == ["Cédula y dictamen médico."]


def test_a_new_embedding_model_re_embeds_everything(
    conn: psycopg.Connection, approved_document: int
) -> None:
    build_index(conn, FakeEmbedder("fake/v1"))

    build_index(conn, FakeEmbedder("fake/v2"))

    assert [row[3] for row in chunks_of(conn, approved_document)] == ["fake/v2"]
