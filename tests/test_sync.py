"""Integration tests: they need the database from docker compose (CI provides one)."""

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from sin_vueltas.config import settings
from sin_vueltas.ingest.catalog import load_catalog
from sin_vueltas.ingest.sync import sync_catalog

CATALOG_PATH = Path(__file__).parent.parent / "data" / "sources.yaml"


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    try:
        connection = psycopg.connect(settings.database_url, connect_timeout=3)
    except psycopg.OperationalError:
        if os.environ.get("CI"):
            raise  # In CI the database must exist; never skip silently.
        pytest.skip("database not available: run `docker compose up -d`")
    with connection:
        yield connection


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
