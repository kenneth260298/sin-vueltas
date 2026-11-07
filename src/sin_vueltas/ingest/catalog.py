"""Load and validate the source catalog (data/sources.yaml)."""

from pathlib import Path
from typing import Literal, Self
from urllib.parse import urldefrag

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

DEFAULT_CATALOG_PATH = Path("data/sources.yaml")


class UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate keys instead of silently keeping the last one.

    A deleted line can turn two procedures into one with a repeated `sources` key;
    plain YAML would quietly drop the first list.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


class Source(BaseModel):
    """One official page, PDF or online service cited for a procedure."""

    url: str
    format: Literal["html", "pdf", "online_service"]
    status: Literal["ok", "review"]
    note: str | None = None

    @field_validator("url")
    @classmethod
    def require_https(cls, url: str) -> str:
        if not url.startswith("https://"):
            raise ValueError(f"source URLs must use https: {url}")
        return url

    @property
    def is_crawlable(self) -> bool:
        """Online services are cited but never downloaded."""
        return self.format != "online_service"

    @property
    def document_url(self) -> str:
        """URL without the #fragment, so one download serves every section of a page."""
        return urldefrag(self.url).url


class Procedure(BaseModel):
    """A procedure and the sources its requirements come from."""

    id: str
    name: str
    category: str = Field(alias="group")
    institution: str
    varies_by_profile: bool
    sources: list[Source] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_urls(self) -> Self:
        urls = [source.url for source in self.sources]
        if len(urls) != len(set(urls)):
            raise ValueError(f"{self.id} lists the same source URL twice")
        return self


class Catalog(BaseModel):
    """Every procedure in the catalog file."""

    version: int
    procedures: list[Procedure] = Field(min_length=1)

    @model_validator(mode="after")
    def require_consistency(self) -> Self:
        ids = [procedure.id for procedure in self.procedures]
        if len(ids) != len(set(ids)):
            raise ValueError("procedure ids must be unique")
        self.documents()  # Raises if one page is listed with two formats.
        return self

    def documents(self) -> dict[str, str]:
        """Map each crawlable document URL to its format."""
        formats: dict[str, str] = {}
        for procedure in self.procedures:
            for source in procedure.sources:
                if not source.is_crawlable:
                    continue
                known = formats.setdefault(source.document_url, source.format)
                if known != source.format:
                    raise ValueError(
                        f"{source.document_url} is listed as both {known} and {source.format}"
                    )
        return formats


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> Catalog:
    """Read and validate the catalog file."""
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    return Catalog.model_validate(data)
