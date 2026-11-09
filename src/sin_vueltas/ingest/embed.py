"""Embedding providers behind one small interface, so the provider can change later."""

from typing import Protocol

from openai import OpenAI

# Must match chunks.embedding (vector(768)) in migration 0001.
EMBEDDING_DIMENSIONS = 768


class Embedder(Protocol):
    @property
    def model(self) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """OpenAI embeddings shortened to EMBEDDING_DIMENSIONS with the `dimensions` parameter."""

    def __init__(self, client: OpenAI, model: str = "text-embedding-3-small") -> None:
        self.client = client
        self._model = model

    @property
    def model(self) -> str:
        return f"openai/{self._model}@{EMBEDDING_DIMENSIONS}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(
            model=self._model, input=texts, dimensions=EMBEDDING_DIMENSIONS
        )
        # The API returns one item per input with its index; keep the input order.
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]


def to_pgvector(values: list[float]) -> str:
    """pgvector's text format: [0.1,0.2,...]."""
    if len(values) != EMBEDDING_DIMENSIONS:
        raise ValueError(f"expected {EMBEDDING_DIMENSIONS} dimensions, got {len(values)}")
    return "[" + ",".join(repr(float(value)) for value in values) + "]"
