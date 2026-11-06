from pathlib import Path

import pytest
from pydantic import ValidationError

from sin_vueltas.ingest.catalog import Catalog, Source, load_catalog

CATALOG_PATH = Path(__file__).parent.parent / "data" / "sources.yaml"

PAGE = {"url": "https://example.go.cr/page", "format": "html", "status": "ok"}


def procedure(procedure_id: str, *sources: dict) -> dict:
    return {
        "id": procedure_id,
        "name": "Example procedure",
        "group": "documentos",
        "institution": "Example institution",
        "varies_by_profile": False,
        "sources": list(sources) or [PAGE],
    }


def catalog(*procedures: dict) -> Catalog:
    return Catalog.model_validate({"version": 1, "procedures": list(procedures)})


def test_repository_catalog_is_valid() -> None:
    assert load_catalog(CATALOG_PATH).procedures


def test_document_url_drops_the_fragment() -> None:
    source = Source(url="https://example.go.cr/page#renovacion", format="html", status="ok")

    assert source.document_url == "https://example.go.cr/page"


def test_sections_of_one_page_are_one_document() -> None:
    result = catalog(
        procedure("first", {**PAGE, "url": "https://example.go.cr/page#first"}),
        procedure("second", {**PAGE, "url": "https://example.go.cr/page#second"}),
    )

    assert result.documents() == {"https://example.go.cr/page": "html"}


def test_online_services_are_not_documents() -> None:
    service = {**PAGE, "format": "online_service"}

    assert catalog(procedure("first", service)).documents() == {}


def test_rejects_duplicate_procedure_ids() -> None:
    with pytest.raises(ValidationError, match="unique"):
        catalog(procedure("same"), procedure("same"))


def test_rejects_plain_http() -> None:
    with pytest.raises(ValidationError, match="https"):
        catalog(procedure("first", {**PAGE, "url": "http://example.go.cr/page"}))


def test_rejects_one_page_with_two_formats() -> None:
    with pytest.raises(ValidationError, match="both html and pdf"):
        catalog(procedure("first", PAGE), procedure("second", {**PAGE, "format": "pdf"}))


def test_rejects_an_empty_catalog() -> None:
    with pytest.raises(ValidationError):
        catalog()
