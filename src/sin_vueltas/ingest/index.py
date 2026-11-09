"""Keep the search index in step with the approved versions.

Running it again is safe and cheap:
1. Chunks of versions that are no longer approved (or no longer cited) are deleted.
2. Approved versions without chunks are split into chunks.
3. Only chunks without an embedding, or embedded by another model, are sent to the
   embedding provider.
"""

import logging
from dataclasses import dataclass

import psycopg

from sin_vueltas.ingest.chunk import split_into_chunks
from sin_vueltas.ingest.embed import Embedder, to_pgvector

logger = logging.getLogger(__name__)

EMBED_BATCH_SIZE = 64

DELETE_STALE_CHUNKS = """
    DELETE FROM chunks AS c
    WHERE NOT EXISTS (
        SELECT 1 FROM document_versions AS v
        WHERE v.id = c.version_id
          AND v.review_status = 'approved'
          AND EXISTS (SELECT 1 FROM sources AS s WHERE s.document_id = v.document_id)
    )
"""

LIST_UNCHUNKED_VERSIONS = """
    SELECT v.id, v.extracted_text
    FROM document_versions AS v
    WHERE v.review_status = 'approved'
      AND v.extracted_text IS NOT NULL
      AND EXISTS (SELECT 1 FROM sources AS s WHERE s.document_id = v.document_id)
      AND NOT EXISTS (SELECT 1 FROM chunks AS c WHERE c.version_id = v.id)
    ORDER BY v.id
"""

INSERT_CHUNK = """
    INSERT INTO chunks (version_id, position, heading, content)
    VALUES (%s, %s, %s, %s)
"""

# The procedure names give each chunk context that its own text often lacks
# ("cédula vigente" means little without knowing it is about passports).
LIST_CHUNKS_TO_EMBED = """
    SELECT c.id, c.heading, c.content,
           (SELECT string_agg(DISTINCT p.name, '; ' ORDER BY p.name)
            FROM sources AS s JOIN procedures AS p ON p.id = s.procedure_id
            WHERE s.document_id = v.document_id)
    FROM chunks AS c
    JOIN document_versions AS v ON v.id = c.version_id
    WHERE c.embedding IS NULL OR c.embedding_model IS DISTINCT FROM %s
    ORDER BY c.id
"""

UPDATE_EMBEDDING = """
    UPDATE chunks SET embedding = %s::vector, embedding_model = %s WHERE id = %s
"""


@dataclass(frozen=True)
class IndexResult:
    removed_chunks: int
    chunked_versions: int
    new_chunks: int
    embedded_chunks: int
    embedded_chars: int


def embedding_input(procedures: str | None, heading: str | None, content: str) -> str:
    """The text sent to the embedding model: context first, then the chunk itself."""
    lines = []
    if procedures:
        lines.append(f"Trámite: {procedures}")
    if heading:
        lines.append(f"Sección: {heading}")
    return "\n".join([*lines, "", content]) if lines else content


def build_index(conn: psycopg.Connection, embedder: Embedder | None) -> IndexResult:
    """Chunk new approved versions and embed what is missing. `None` skips embedding."""
    with conn.transaction():
        removed = conn.execute(DELETE_STALE_CHUNKS).rowcount
        versions = conn.execute(LIST_UNCHUNKED_VERSIONS).fetchall()
        new_chunks = 0
        with conn.cursor() as cur:
            for version_id, text in versions:
                chunks = split_into_chunks(text)
                cur.executemany(
                    INSERT_CHUNK,
                    [(version_id, c.position, c.heading, c.content) for c in chunks],
                )
                new_chunks += len(chunks)

    embedded = embedded_chars = 0
    if embedder is not None:
        with conn.transaction():
            pending = conn.execute(LIST_CHUNKS_TO_EMBED, (embedder.model,)).fetchall()
        for start in range(0, len(pending), EMBED_BATCH_SIZE):
            batch = pending[start : start + EMBED_BATCH_SIZE]
            texts = [
                embedding_input(procedures, heading, content)
                for _, heading, content, procedures in batch
            ]
            vectors = embedder.embed(texts)
            # Each batch commits on its own, so an interrupted run keeps its progress.
            with conn.transaction(), conn.cursor() as cur:
                cur.executemany(
                    UPDATE_EMBEDDING,
                    [
                        (to_pgvector(vector), embedder.model, chunk_id)
                        for (chunk_id, *_), vector in zip(batch, vectors, strict=True)
                    ],
                )
            embedded += len(batch)
            embedded_chars += sum(len(text) for text in texts)
            logger.info("embedded %d/%d chunks", embedded, len(pending))

    return IndexResult(
        removed_chunks=removed,
        chunked_versions=len(versions),
        new_chunks=new_chunks,
        embedded_chunks=embedded,
        embedded_chars=embedded_chars,
    )
