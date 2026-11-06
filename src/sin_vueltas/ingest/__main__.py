"""Command line entry point: uv run python -m sin_vueltas.ingest <command>."""

import argparse
from pathlib import Path

import psycopg

from sin_vueltas.config import settings
from sin_vueltas.ingest.catalog import DEFAULT_CATALOG_PATH, load_catalog
from sin_vueltas.ingest.sync import sync_catalog


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m sin_vueltas.ingest",
        description="Ingestion pipeline for the official sources.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    sync = commands.add_parser("sync", help="load the source catalog into the database")
    sync.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)

    args = parser.parse_args()

    if args.command == "sync":
        catalog = load_catalog(args.catalog)
        with psycopg.connect(settings.database_url) as conn:
            result = sync_catalog(conn, catalog)
        print(
            f"Synced {result.procedures} procedures, {result.sources} sources "
            f"and {result.documents} documents. Removed {result.removed_procedures} "
            f"procedures and {result.removed_sources} sources."
        )


if __name__ == "__main__":
    main()
