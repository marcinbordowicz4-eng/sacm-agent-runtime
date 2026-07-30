import sys
from types import SimpleNamespace

import pytest

from sacm.core.observability import OpenTelemetryService, _embedding_cost_per_token
from sacm.ml.embeddings import EmbeddingService


def test_embedding_usage_uses_provider_reported_prompt_tokens(monkeypatch):
    recorded: list[tuple[str, str, int, str]] = []
    response = SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1, 0.2])],
        usage=SimpleNamespace(prompt_tokens=17, total_tokens=17),
    )
    fake_client = SimpleNamespace(
        embeddings=SimpleNamespace(create=lambda **_: response)
    )
    monkeypatch.setenv("DEFAULT_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=lambda: fake_client))
    service = EmbeddingService(
        dim=2,
        usage_recorder=lambda provider, model, tokens, operation: recorded.append(
            (provider, model, tokens, operation)
        ),
    )

    assert service.embed("input") == [0.1, 0.2]
    assert recorded == [("openai", "text-embedding-3-small", 17, "embedding")]


def test_embedding_cost_requires_a_non_negative_price(monkeypatch):
    monkeypatch.setenv("SACM_OPENAI_EMBEDDING_INPUT_COST_PER_MILLION_USD", "0.02")
    assert _embedding_cost_per_token() == pytest.approx(0.00000002)

    monkeypatch.setenv("SACM_OPENAI_EMBEDDING_INPUT_COST_PER_MILLION_USD", "-1")
    with pytest.raises(ValueError, match="cannot be negative"):
        _embedding_cost_per_token()


def test_model_usage_calculates_input_and_output_cost_without_otel(monkeypatch):
    monkeypatch.setenv("SACM_CODEX_INPUT_COST_PER_MILLION_USD", "10")
    monkeypatch.setenv("SACM_CODEX_OUTPUT_COST_PER_MILLION_USD", "20")

    cost = OpenTelemetryService(False).record_model_usage(
        provider="codex",
        model="gpt-5-codex",
        input_tokens=100,
        output_tokens=20,
        operation="code_execution",
    )

    assert cost == pytest.approx(0.0014)
