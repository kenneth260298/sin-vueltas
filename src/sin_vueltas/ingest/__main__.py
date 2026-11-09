"""Command line entry point: uv run python -m sin_vueltas.ingest <command>."""

import argparse
import getpass
import logging
import sys
from pathlib import Path

import psycopg
from openai import OpenAI

from sin_vueltas.config import settings
from sin_vueltas.ingest.capture import CaptureError, capture_file
from sin_vueltas.ingest.catalog import DEFAULT_CATALOG_PATH, load_catalog
from sin_vueltas.ingest.embed import OpenAIEmbedder
from sin_vueltas.ingest.fetch import HostThrottle, build_client, fetch_all
from sin_vueltas.ingest.index import build_index
from sin_vueltas.ingest.review import (
    ReviewError,
    approve,
    get_version,
    list_pending,
    reject,
    render_changes,
)
from sin_vueltas.ingest.storage import LocalStorage
from sin_vueltas.ingest.sync import sync_catalog


class ConfigurationError(Exception):
    """A setting the command needs is missing."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sin_vueltas.ingest",
        description="Ingestion pipeline for the official sources.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    sync = commands.add_parser("sync", help="load the source catalog into the database")
    sync.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)

    commands.add_parser("fetch", help="download new versions of the catalog documents")

    capture = commands.add_parser(
        "capture", help="record a page saved from a browser, for sites that reject bots"
    )
    capture.add_argument("url", help="the document URL as it appears in sources.yaml")
    capture.add_argument("file", type=Path, help="the saved HTML or PDF file")

    index = commands.add_parser("index", help="chunk approved versions and embed them")
    index.add_argument(
        "--no-embed", action="store_true", help="only chunk; do not call the embedding API"
    )

    review = commands.add_parser("review", help="list, inspect, approve or reject versions")
    actions = review.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="show pending versions")
    show = actions.add_parser("show", help="show a version's text, or its diff if one exists")
    show.add_argument("version_id", type=int)
    for name, help_text in [("approve", "approve versions"), ("reject", "reject versions")]:
        action = actions.add_parser(name, help=help_text)
        action.add_argument("version_ids", type=int, nargs="+")
        action.add_argument("--by", default=getpass.getuser(), help="reviewer name")

    return parser


def run_review(conn: psycopg.Connection, args: argparse.Namespace) -> None:
    if args.action == "list":
        pending = list_pending(conn)
        for version in pending:
            kind = "update" if version.replaces_approved else "first"
            print(
                f"{version.id:>5}  {kind:<6}  {version.origin:<7}  "
                f"{version.chars:>7} chars  {version.url}"
            )
        print(f"{len(pending)} pending versions")
    elif args.action == "show":
        detail = get_version(conn, args.version_id)
        print(f"Version {detail.id} ({detail.review_status}, {detail.origin}) of {detail.url}")
        print("-" * 80)
        print(render_changes(detail))
    elif args.action == "approve":
        approve(conn, args.version_ids, args.by)
        print(f"Approved {len(args.version_ids)} versions as {args.by}")
    elif args.action == "reject":
        reject(conn, args.version_ids, args.by)
        print(f"Rejected {len(args.version_ids)} versions as {args.by}")


def openai_embedder() -> OpenAIEmbedder:
    if settings.openai_api_key is None:
        raise ConfigurationError("OPENAI_API_KEY is not set; add it to your .env file")
    client = OpenAI(api_key=settings.openai_api_key.get_secret_value())
    return OpenAIEmbedder(client, settings.embedding_model)


def main() -> None:
    args = build_parser().parse_args()
    # Windows consoles and pipes may not encode every character (for example ₡).
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    storage = LocalStorage(settings.raw_storage_dir)

    try:
        with psycopg.connect(settings.database_url) as conn:
            if args.command == "sync":
                result = sync_catalog(conn, load_catalog(args.catalog))
                print(
                    f"Synced {result.procedures} procedures, {result.sources} sources "
                    f"and {result.documents} documents. Removed {result.removed_procedures} "
                    f"procedures and {result.removed_sources} sources."
                )
            elif args.command == "fetch":
                with build_client() as client:
                    report = fetch_all(conn, client, storage, HostThrottle())
                print("Done: " + ", ".join(f"{k} {v}" for k, v in sorted(report.items())))
            elif args.command == "capture":
                captured = capture_file(conn, storage, args.url, args.file)
                status = "new pending version" if captured.is_new else "same text as before"
                print(f"{status}: {captured.url} ({captured.chars} chars)")
            elif args.command == "index":
                embedder = None if args.no_embed else openai_embedder()
                result = build_index(conn, embedder)
                print(
                    f"Removed {result.removed_chunks} stale chunks. "
                    f"Chunked {result.chunked_versions} versions into {result.new_chunks} chunks. "
                    f"Embedded {result.embedded_chunks} chunks "
                    f"(~{result.embedded_chars // 4:,} tokens)."
                )
            elif args.command == "review":
                run_review(conn, args)
    except (CaptureError, ReviewError, ConfigurationError) as error:
        raise SystemExit(f"error: {error}") from error


if __name__ == "__main__":
    main()
