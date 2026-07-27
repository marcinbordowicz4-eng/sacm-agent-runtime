import os
from hashlib import blake2b
from typing import List

import numpy as np


class EmbeddingService:
    """Embedding provider with a deterministic local mode for development."""

    def __init__(self, provider: str = "hash", dim: int = 1536):
        self.provider = os.getenv("DEFAULT_EMBEDDING_PROVIDER", provider)
        self.dim = dim

    def embed(self, text: str) -> List[float]:
        if self.provider == "openai":
            return self._embed_openai(text)
        if self.provider == "hash":
            return self._embed_hash(text)
        raise ValueError(
            f"Unsupported embedding provider '{self.provider}'. "
            "Set DEFAULT_EMBEDDING_PROVIDER to 'openai' or 'hash'."
        )

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(text) for text in texts]

    def _embed_hash(self, text: str) -> List[float]:
        """Generate stable development vectors without pretending to be semantic."""
        seed = int.from_bytes(blake2b(text.encode(), digest_size=8).digest(), "big")
        rng = np.random.default_rng(seed)
        vec = rng.random(self.dim).astype(np.float32)
        norm = np.linalg.norm(vec)
        return (vec / norm).tolist()

    def _embed_openai(self, text: str) -> List[float]:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError(
                "OPENAI_API_KEY must be set when DEFAULT_EMBEDDING_PROVIDER=openai."
            )
        try:
            import openai
        except ImportError as exc:
            raise RuntimeError(
                "Install the optional OpenAI client to use OpenAI embeddings."
            ) from exc

        client = openai.OpenAI()
        response = client.embeddings.create(
            input=text,
            model="text-embedding-3-small",
            dimensions=self.dim,
        )
        return response.data[0].embedding
