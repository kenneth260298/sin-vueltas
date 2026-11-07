"""Sync the source catalog into the database: procedures, documents and sources.

The catalog file is the source of truth. Running the sync again is safe: rows are
upserted, and procedures or sources removed from the file are deleted. Documents are
kept even when no source points to them anymore, because their versions are history.
"""

from dataclasses import dataclass

import psycopg

from sin_vueltas.ingest.catalog import Catalog

UPSERT_PROCEDURE = """
    INSERT INTO procedures (id, name, category, institution, varies_by_profile)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        category = EXCLUDED.category,
        institution = EXCLUDED.institution,
        varies_by_profile = EXCLUDED.varies_by_profile,
        updated_at = now()
    WHERE (procedures.name, procedures.category, procedures.institution,
           procedures.varies_by_profile)
        IS DISTINCT FROM (EXCLUDED.name, EXCLUDED.category, EXCLUDED.institution,
                          EXCLUDED.varies_by_profile)
"""

UPSERT_DOCUMENT = """
    INSERT INTO documents (url, format)
    VALUES (%s, %s)
    ON CONFLICT (url) DO UPDATE SET format = EXCLUDED.format
    RETURNING id
"""

UPSERT_SOURCE = """
    INSERT INTO sources (procedure_id, document_id, url, format, status, note)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (procedure_id, url) DO UPDATE SET
        document_id = EXCLUDED.document_id,
        format = EXCLUDED.format,
        status = EXCLUDED.status,
        note = EXCLUDED.note
"""

DELETE_REMOVED_SOURCES = """
    DELETE FROM sources AS s
    WHERE NOT EXISTS (
        SELECT 1
        FROM unnest(%s::text[], %s::text[]) AS keep (procedure_id, url)
        WHERE keep.procedure_id = s.procedure_id AND keep.url = s.url
    )
"""

DELETE_REMOVED_PROCEDURES = "DELETE FROM procedures WHERE id <> ALL (%s::text[])"


@dataclass(frozen=True)
class SyncResult:
    procedures: int
    documents: int
    sources: int
    removed_procedures: int
    removed_sources: int


def sync_catalog(conn: psycopg.Connection, catalog: Catalog) -> SyncResult:
    """Write the catalog to the database in a single transaction."""
    document_ids: dict[str, int] = {}
    kept_procedure_ids: list[str] = []
    kept_urls: list[str] = []

    with conn.transaction(), conn.cursor() as cur:
        for url, document_format in catalog.documents().items():
            cur.execute(UPSERT_DOCUMENT, (url, document_format))
            row = cur.fetchone()
            assert row is not None  # RETURNING always yields a row here.
            document_ids[url] = row[0]

        for procedure in catalog.procedures:
            cur.execute(
                UPSERT_PROCEDURE,
                (
                    procedure.id,
                    procedure.name,
                    procedure.category,
                    procedure.institution,
                    procedure.varies_by_profile,
                ),
            )
            for source in procedure.sources:
                document_id = document_ids[source.document_url] if source.is_crawlable else None
                cur.execute(
                    UPSERT_SOURCE,
                    (
                        procedure.id,
                        document_id,
                        source.url,
                        source.format,
                        source.status,
                        source.note,
                    ),
                )
                kept_procedure_ids.append(procedure.id)
                kept_urls.append(source.url)

        cur.execute(DELETE_REMOVED_SOURCES, (kept_procedure_ids, kept_urls))
        removed_sources = cur.rowcount
        cur.execute(DELETE_REMOVED_PROCEDURES, ([p.id for p in catalog.procedures],))
        removed_procedures = cur.rowcount

    return SyncResult(
        procedures=len(catalog.procedures),
        documents=len(document_ids),
        sources=len(kept_urls),
        removed_procedures=removed_procedures,
        removed_sources=removed_sources,
    )
