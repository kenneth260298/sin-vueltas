"""Shared fixtures. Database tests skip locally without Docker but always run in CI."""

import os
from collections.abc import Iterator

import psycopg
import pytest

from sin_vueltas.config import settings


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
