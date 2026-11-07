"""Command line entry point: uv run python -m sin_vueltas.ingest <command>."""

import argparse
import logging
from pathlib import Path

import psycopg

from sin_vueltas.config import settings
from sin_vueltas.ingest.catalog import DEFAULT_CATALOG_PATH, load_catalog
from sin_vueltas.ingest.fetch import HostThrottle, build_client, fetch_all
from sin_vueltas.ingest.storage import LocalStorage
from sin_vueltas.ingest.sync import sync_catalog


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m sin_vueltas.ingest",
        description="Ingestion pipeline for the official sources.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    sync = commands.add_parser("sync", help="load the source catalog into the database")
    sync.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)

    commands.add_parser("fetch", help="download new versions of the catalog documents")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.command == "sync":
        catalog = load_catalog(args.catalog)
        with psycopg.connect(settings.database_url) as conn:
            result = sync_catalog(conn, catalog)
        print(
            f"Synced {result.procedures} procedures, {result.sources} sources "
            f"and {result.documents} documents. Removed {result.removed_procedures} "
            f"procedures and {result.removed_sources} sources."
        )

    elif args.command == "fetch":
        storage = LocalStorage(settings.raw_storage_dir)
        with psycopg.connect(settings.database_url) as conn, build_client() as client:
            report = fetch_all(conn, client, storage, HostThrottle())
        print("Done: " + ", ".join(f"{name} {count}" for name, count in sorted(report.items())))


if __name__ == "__main__":
    main()