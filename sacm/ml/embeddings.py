import os
from typing import List

import numpy as np


class EmbeddingService:
    """Simple embedding service. Uses random vectors as fallback when no API key is set."""

    def __init__(self, provider: str = "random", dim: int = 1536):
        self.provider = os.getenv("DEFAULT_EMBEDDING_PROVIDER", provider)
        self.dim = int(os.getenv("DEFAULT_CONTEXT_DIM", str(dim)))

    def embed(self, text: str) -> List[float]:
        if self.provider == "openai":
            return self._embed_openai(text)
        return self._embed_random(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(text) for text in texts]

    def _embed_random(self, text: str) -> List[float]:
        rng = np.random.default_rng(hash(text) % (2**32))
        vec = rng.random(self.dim).astype(np.float32)
        norm = np.linalg.norm(vec)
        return (vec / norm).tolist()

    def _embed_openai(self, text: str) -> List[float]:
        try:
            import openai

            client = openai.OpenAI()
            response = client.embeddings.create(
                input=text,
                model="text-embedding-3-small",
            )
            return response.data[0].embedding
        except Exception:
            return self._embed_random(text)
