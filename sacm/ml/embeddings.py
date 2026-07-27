import os
from hashlib import blake2b
from typing import Callable, List

import numpy as np

from sacm.core.observability import ObservabilityService

OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"


class EmbeddingService:
    """Embedding provider with a deterministic local mode for development."""

    def __init__(
        self,
        provider: str = "hash",
        dim: int = 1536,
        usage_recorder: Callable[[str, str, int, str], None] | None = None,
    ):
        self.provider = os.getenv("DEFAULT_EMBEDDING_PROVIDER", provider)
        self.dim = dim
        self._usage_recorder = usage_recorder

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
            model=OPENAI_EMBEDDING_MODEL,
            dimensions=self.dim,
        )
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None)
        if input_tokens is None:
            input_tokens = getattr(usage, "total_tokens", None)
        if input_tokens is not None:
            self._record_usage(input_tokens)
        return response.data[0].embedding

    def _record_usage(self, input_tokens: int) -> None:
        if self._usage_recorder is not None:
            self._usage_recorder(
                "openai", OPENAI_EMBEDDING_MODEL, input_tokens, "embedding"
            )
            return
        ObservabilityService().record_embedding_usage(
            "openai", OPENAI_EMBEDDING_MODEL, input_tokens, "embedding"
        )
