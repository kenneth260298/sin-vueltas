"""Crawler tests. HTTP is served by httpx.MockTransport, so no test touches the network."""

from collections.abc import Callable
from pathlib import Path

import httpx
import psycopg

from sin_vueltas.ingest.fetch import (
    USER_AGENT,
    Document,
    Downloaded,
    Failed,
    NewVersion,
    NotModified,
    RobotsPolicy,
    build_client,
    fetch_document,
    prepare_version,
    record_outcome,
)
from sin_vueltas.ingest.storage import LocalStorage

PAGE_URL = "https://example.go.cr/tramites/licencia"
PDF_URL = "https://example.go.cr/requisitos.pdf"
HTML = {"content-type": "text/html; charset=utf-8"}


def client_for(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return build_client(transport=httpx.MockTransport(handler))


def document(url: str = PAGE_URL, format: str = "html", etag: str | None = None) -> Document:
    return Document(id=1, url=url, format=format, etag=etag, last_modified=None)


def test_downloads_a_page_and_keeps_its_etag() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"] == USER_AGENT
        return httpx.Response(200, content=b"<html>v1</html>", headers={**HTML, "etag": '"v1"'})

    outcome = fetch_document(client_for(handler), document())

    assert outcome == Downloaded(content=b"<html>v1</html>", etag='"v1"', last_modified=None)


def test_asks_whether_the_page_changed_and_accepts_304() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == '"v1"'
        return httpx.Response(304)

    assert fetch_document(client_for(handler), document(etag='"v1"')) == NotModified()


def test_http_errors_are_failures() -> None:
    outcome = fetch_document(client_for(lambda request: httpx.Response(404)), document())

    assert outcome == Failed("HTTP 404", 404)


def test_network_errors_are_failures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    outcome = fetch_document(client_for(handler), document())

    assert isinstance(outcome, Failed)
    assert "ConnectError" in outcome.error


def test_an_error_page_served_instead_of_a_pdf_is_a_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>Not found</html>", headers=HTML)

    outcome = fetch_document(client_for(handler), document(url=PDF_URL, format="pdf"))

    assert outcome == Failed("expected a PDF but got text/html; charset=utf-8", 200)


def test_documents_over_the_size_limit_are_failures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 100, headers=HTML)

    outcome = fetch_document(client_for(handler), document(), max_bytes=10)

    assert outcome == Failed("larger than 10 bytes", 200)


def test_robots_txt_rules_are_obeyed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/robots.txt"
        return httpx.Response(200, text="User-agent: *\nDisallow: /tramites/\n")

    robots = RobotsPolicy(client_for(handler))

    assert robots.check(PAGE_URL) == "disallowed by robots.txt"
    assert robots.check(PDF_URL) is None


def test_a_missing_robots_txt_allows_everything() -> None:
    robots = RobotsPolicy(client_for(lambda request: httpx.Response(404)))

    assert robots.check(PAGE_URL) is None


def test_a_forbidden_robots_txt_blocks_the_site_and_says_why() -> None:
    robots = RobotsPolicy(client_for(lambda request: httpx.Response(403)))

    assert robots.check(PAGE_URL) == "robots.txt unavailable: HTTP 403"


def test_an_unreachable_robots_txt_blocks_the_site_and_says_why() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    robots = RobotsPolicy(client_for(handler))

    assert robots.check(PAGE_URL) == "robots.txt unreachable: ConnectError"


def test_a_download_without_readable_text_is_a_failure() -> None:
    empty_page = Downloaded(content=b"<html><body></body></html>", etag=None, last_modified=None)

    outcome = prepare_version("html", empty_page)

    assert outcome == Failed("extraction failed: no main text found in the page", 200)


def test_a_new_version_is_recorded_only_when_the_text_changes(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    # Same text with different bytes, like a page that embeds a token on every visit.
    first_visit = NewVersion(b"<html>token=1</html>", "Cédula vigente", '"v1"', None)
    second_visit = NewVersion(b"<html>token=2</html>", "Cédula vigente", '"v2"', None)
    changed = NewVersion(b"<html>token=3</html>", "Cédula y dictamen médico", '"v3"', None)

    with conn.transaction(force_rollback=True):
        (document_id,) = conn.execute(
            "INSERT INTO documents (url, format) VALUES ('https://example.test/page', 'html') "
            "RETURNING id"
        ).fetchone()
        doc = Document(document_id, "https://example.test/page", "html", None, None)

        assert record_outcome(conn, storage, doc, first_visit) == "new_version"
        assert record_outcome(conn, storage, doc, second_visit) == "unchanged"
        assert record_outcome(conn, storage, doc, NotModified()) == "not_modified"
        assert record_outcome(conn, storage, doc, changed) == "new_version"

        versions = conn.execute(
            "SELECT extracted_text, review_status, storage_path FROM document_versions "
            "WHERE document_id = %s ORDER BY id",
            (document_id,),
        ).fetchall()
        assert [(text, status) for text, status, _ in versions] == [
            ("Cédula vigente", "pending"),
            ("Cédula y dictamen médico", "pending"),
        ]
        # Only the originals of new versions are stored.
        assert sorted(path.name for path in tmp_path.iterdir()) == sorted(
            key for _, _, key in versions
        )
        assert conn.execute(
            "SELECT etag, last_status_code FROM documents WHERE id = %s", (document_id,)
        ).fetchone() == ('"v3"', 200)
