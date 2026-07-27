import pytest

from sacm.ml.embeddings import EmbeddingService


def test_hash_embeddings_are_stable(monkeypatch):
    monkeypatch.setenv("DEFAULT_EMBEDDING_PROVIDER", "hash")
    service = EmbeddingService(dim=8)

    assert service.embed("same input") == service.embed("same input")


def test_unknown_embedding_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("DEFAULT_EMBEDDING_PROVIDER", "unsupported")
    service = EmbeddingService(dim=8)

    with pytest.raises(ValueError, match="Unsupported embedding provider"):
        service.embed("input")
