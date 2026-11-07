"""Integration tests: they need the database from docker compose (CI provides one)."""

from pathlib import Path

import psycopg

from sin_vueltas.ingest.catalog import load_catalog
from sin_vueltas.ingest.sync import sync_catalog

CATALOG_PATH = Path(__file__).parent.parent / "data" / "sources.yaml"


def test_sync_loads_the_catalog_and_is_idempotent(conn: psycopg.Connection) -> None:
    catalog = load_catalog(CATALOG_PATH)

    sync_catalog(conn, catalog)
    second = sync_catalog(conn, catalog)

    assert second.removed_procedures == 0
    assert second.removed_sources == 0
    procedures, sources, documents = conn.execute(
        """
        SELECT
            (SELECT count(*) FROM procedures),
            (SELECT count(*) FROM sources),
            (SELECT count(DISTINCT document_id) FROM sources)
        """
    ).fetchone()
    assert procedures == len(catalog.procedures)
    assert sources == sum(len(p.sources) for p in catalog.procedures)
    assert documents == len(catalog.documents())
