from unittest.mock import MagicMock


def test_orchestrator_imports():
    from sacm.core.orchestrator import Orchestrator

    assert Orchestrator is not None


def test_agent_registry_has_agents():
    from sacm.core.agent_registry import AgentRegistry

    registry = AgentRegistry()
    assert len(registry.all()) == 11
    assert "ClaudeReasoner" in registry.names()


def test_verifier_done_on_high_confidence():
    from sacm.core.verifier import Verifier
    from sacm.schemas.result import AgentResult

    task = MagicMock()
    result = AgentResult(
        agent_name="test",
        summary="done",
        confidence=0.99,
        next_state_hint="reviewing",
    )
    verifier = Verifier()
    assert verifier.is_done(task, result) is True


def test_verifier_not_done_when_blocked():
    from sacm.core.verifier import Verifier
    from sacm.schemas.result import AgentResult

    task = MagicMock()
    result = AgentResult(
        agent_name="test",
        summary="blocked",
        confidence=0.99,
        next_state_hint="blocked",
    )
    verifier = Verifier()
    assert verifier.is_done(task, result) is False
