from types import SimpleNamespace

import pytest

from sin_vueltas.ingest.embed import EMBEDDING_DIMENSIONS, OpenAIEmbedder, to_pgvector
from sin_vueltas.ingest.index import embedding_input


class FakeEmbeddingsAPI:
    """Stands in for client.embeddings; returns items out of order, like a real API may."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        texts = kwargs["input"]
        assert isinstance(texts, list)
        items = [
            SimpleNamespace(index=i, embedding=[float(i)] * EMBEDDING_DIMENSIONS)
            for i in range(len(texts))
        ]
        return SimpleNamespace(data=list(reversed(items)))


def test_openai_embedder_asks_for_768_dimensions_and_keeps_input_order() -> None:
    api = FakeEmbeddingsAPI()
    embedder = OpenAIEmbedder(SimpleNamespace(embeddings=api))  # type: ignore[arg-type]

    vectors = embedder.embed(["primero", "segundo", "tercero"])

    assert [vector[0] for vector in vectors] == [0.0, 1.0, 2.0]
    assert api.calls[0]["dimensions"] == EMBEDDING_DIMENSIONS
    assert api.calls[0]["model"] == "text-embedding-3-small"
    assert embedder.model == "openai/text-embedding-3-small@768"


def test_vectors_use_the_pgvector_text_format() -> None:
    vector = [0.5] * EMBEDDING_DIMENSIONS

    text = to_pgvector(vector)

    assert text.startswith("[0.5,0.5,") and text.endswith("]")


def test_vectors_with_the_wrong_size_are_rejected() -> None:
    with pytest.raises(ValueError, match="expected 768 dimensions"):
        to_pgvector([0.1, 0.2])


def test_the_embedding_input_carries_procedure_and_section_context() -> None:
    text = embedding_input("Pasaporte", "Requisitos", "Cédula vigente")

    assert text == "Trámite: Pasaporte\nSección: Requisitos\n\nCédula vigente"


def test_the_embedding_input_without_context_is_the_content() -> None:
    assert embedding_input(None, None, "Cédula vigente") == "Cédula vigente"
