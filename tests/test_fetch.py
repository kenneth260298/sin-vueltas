"""Download the catalog documents politely and record every new version.

Rules the crawler follows:
- It identifies itself with a User-Agent that links to the repository.
- It obeys robots.txt and waits between requests to the same host.
- It asks the server whether a document changed (ETag / Last-Modified) before downloading.
- Files are stored by content hash, so identical content is never stored twice.
- A failed download never deletes anything: the last approved version keeps serving answers.
"""

import hashlib
import logging
import time
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
import psycopg

from sin_vueltas.ingest.storage import Storage

logger = logging.getLogger(__name__)

USER_AGENT = "SinVueltasBot/0.1 (+https://github.com/kenneth260298/sin-vueltas)"
REQUEST_TIMEOUT_SECONDS = 30.0
DELAY_BETWEEN_REQUESTS_SECONDS = 2.0
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024

LIST_DOCUMENTS = """
    SELECT d.id, d.url, d.format, d.etag, d.last_modified
    FROM documents AS d
    WHERE EXISTS (SELECT 1 FROM sources AS s WHERE s.document_id = d.id)
    ORDER BY d.id
"""

INSERT_VERSION = """
    INSERT INTO document_versions (document_id, content_hash, storage_path)
    VALUES (%s, %s, %s)
    ON CONFLICT (document_id, content_hash) DO NOTHING
"""

MARK_CHECKED = """
    UPDATE documents SET
        last_checked_at = now(),
        last_status_code = %s,
        last_error = %s,
        etag = coalesce(%s, etag),
        last_modified = coalesce(%s, last_modified)
    WHERE id = %s
"""


@dataclass(frozen=True)
class Document:
    id: int
    url: str
    format: str
    etag: str | None
    last_modified: str | None


@dataclass(frozen=True)
class Downloaded:
    content: bytes
    etag: str | None
    last_modified: str | None


@dataclass(frozen=True)
class NotModified:
    pass


@dataclass(frozen=True)
class Blocked:
    reason: str


@dataclass(frozen=True)
class Failed:
    error: str
    status_code: int | None = None


FetchOutcome = Downloaded | NotModified | Blocked | Failed


def build_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
        transport=transport,
    )


class RobotsPolicy:
    """Reads robots.txt once per site and answers whether a URL may be fetched."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client
        # Per site: the parsed rules, or the reason robots.txt could not be read.
        self.sites: dict[str, RobotFileParser | str] = {}

    def check(self, url: str) -> str | None:
        """Return why the URL may not be fetched, or None when it may."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.sites:
            self.sites[origin] = self._load(origin)
        site = self.sites[origin]
        if isinstance(site, str):
            return site
        if not site.can_fetch(USER_AGENT, url):
            return "disallowed by robots.txt"
        return None

    def _load(self, origin: str) -> RobotFileParser | str:
        try:
            response = self.client.get(f"{origin}/robots.txt")
        except httpx.HTTPError as error:
            # Unreachable: be conservative and try again on the next run.
            return f"robots.txt unreachable: {type(error).__name__}"
        if response.status_code in (401, 403) or response.status_code >= 500:
            return f"robots.txt unavailable: HTTP {response.status_code}"
        parser = RobotFileParser()
        if response.status_code >= 400:
            parser.allow_all = True  # No robots.txt: the site sets no rules.
        else:
            parser.parse(response.text.splitlines())
        return parser


class HostThrottle:
    """Waits between requests to the same host so the crawler never hammers a site."""

    def __init__(self, delay_seconds: float = DELAY_BETWEEN_REQUESTS_SECONDS) -> None:
        self.delay_seconds = delay_seconds
        self.last_request: dict[str, float] = {}

    def wait(self, url: str) -> None:
        host = urlsplit(url).netloc
        last = self.last_request.get(host)
        if last is not None:
            remaining = self.delay_seconds - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)
        self.last_request[host] = time.monotonic()


def content_error(document_format: str, content: bytes, content_type: str) -> str | None:
    """Catch error pages served with status 200 instead of the expected document."""
    received = content_type or "no content type"
    if document_format == "pdf" and b"%PDF-" not in content[:1024]:
        return f"expected a PDF but got {received}"
    if document_format == "html" and "html" not in content_type:
        head = content[:2048].lower()
        if b"<html" not in head and b"<!doctype html" not in head:
            return f"expected HTML but got {received}"
    return None


def fetch_document(
    client: httpx.Client, document: Document, max_bytes: int = MAX_DOCUMENT_BYTES
) -> FetchOutcome:
    """Download one document unless the server says it has not changed."""
    headers = {}
    if document.etag:
        headers["If-None-Match"] = document.etag
    if document.last_modified:
        headers["If-Modified-Since"] = document.last_modified

    try:
        with client.stream("GET", document.url, headers=headers) as response:
            if response.status_code == 304:
                return NotModified()
            if response.status_code != 200:
                return Failed(f"HTTP {response.status_code}", response.status_code)

            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    return Failed(f"larger than {max_bytes} bytes", 200)
                chunks.append(chunk)
            content = b"".join(chunks)

            error = content_error(
                document.format, content, response.headers.get("content-type", "")
            )
            if error:
                return Failed(error, 200)
            return Downloaded(
                content=content,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )
    except httpx.HTTPError as error:
        return Failed(f"{type(error).__name__}: {error}")


def record_outcome(
    conn: psycopg.Connection, storage: Storage, document: Document, outcome: FetchOutcome
) -> str:
    """Save the result of one fetch and return its name for the run report."""
    with conn.transaction(), conn.cursor() as cur:
        match outcome:
            case Downloaded():
                content_hash = hashlib.sha256(outcome.content).hexdigest()
                key = f"{content_hash}.{document.format}"
                if not storage.exists(key):
                    storage.save(key, outcome.content)
                cur.execute(INSERT_VERSION, (document.id, content_hash, key))
                is_new = cur.rowcount == 1
                cur.execute(
                    MARK_CHECKED, (200, None, outcome.etag, outcome.last_modified, document.id)
                )
                return "new_version" if is_new else "unchanged"
            case NotModified():
                cur.execute(MARK_CHECKED, (304, None, None, None, document.id))
                return "not_modified"
            case Blocked():
                cur.execute(MARK_CHECKED, (None, outcome.reason, None, None, document.id))
                return "blocked"
            case Failed():
                cur.execute(
                    MARK_CHECKED, (outcome.status_code, outcome.error, None, None, document.id)
                )
                return "failed"


def fetch_all(
    conn: psycopg.Connection,
    client: httpx.Client,
    storage: Storage,
    throttle: HostThrottle,
) -> Counter[str]:
    """Fetch every document referenced by a source. Each result is committed on its own."""
    robots = RobotsPolicy(client)
    report: Counter[str] = Counter()
    # Close this read in its own transaction so each record_outcome below commits by itself.
    with conn.transaction():
        documents = [Document(*row) for row in conn.execute(LIST_DOCUMENTS).fetchall()]

    for document in documents:
        outcome: FetchOutcome
        if reason := robots.check(document.url):
            outcome = Blocked(reason)
        else:
            throttle.wait(document.url)
            outcome = fetch_document(client, document)
        result = record_outcome(conn, storage, document, outcome)
        report[result] += 1
        match outcome:
            case Failed(error=detail) | Blocked(reason=detail):
                logger.info("%-12s %s (%s)", result, document.url, detail)
            case _:
                logger.info("%-12s %s", result, document.url)

    return report
