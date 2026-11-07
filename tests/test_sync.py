"""Integration tests: they need the database from docker compose (CI provides one)."""

from pathlib import Path

import psycopg

from sin_vueltas.ingest.catalog import Catalog, load_catalog
from sin_vueltas.ingest.sync import sync_catalog

CATALOG_PATH = Path(__file__).parent.parent / "data" / "sources.yaml"
FAQ_URL = "https://example.test/faq"


def faq_catalog(extraction: str) -> Catalog:
    source = {"url": FAQ_URL, "format": "html", "status": "ok", "extraction": extraction}
    procedure = {
        "id": "example-faq",
        "name": "Example",
        "group": "test",
        "institution": "Example institution",
        "varies_by_profile": False,
        "sources": [source],
    }
    return Catalog.model_validate({"version": 1, "procedures": [procedure]})


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


def test_changing_the_extraction_mode_forgets_the_cache_validators(
    conn: psycopg.Connection,
) -> None:
    def document_state() -> tuple:
        return conn.execute(
            "SELECT etag, last_modified, extraction FROM documents WHERE url = %s", (FAQ_URL,)
        ).fetchone()

    # Everything runs inside a transaction that is rolled back, so the real catalog stays.
    with conn.transaction(force_rollback=True):
        sync_catalog(conn, faq_catalog("main"))
        conn.execute(
            "UPDATE documents SET etag = '\"v1\"', last_modified = 'yesterday' WHERE url = %s",
            (FAQ_URL,),
        )

        sync_catalog(conn, faq_catalog("main"))
        assert document_state() == ('"v1"', "yesterday", "main")

        sync_catalog(conn, faq_catalog("full"))
        assert document_state() == (None, None, "full")
