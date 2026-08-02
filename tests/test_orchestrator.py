from unittest.mock import MagicMock

from sacm.agents.mlflow_experiment_agent import MLflowExperimentAgent
from sacm.agents.otel_cost_agent import OpenTelemetryCostAgent
from sacm.schemas.context import AgentContext


def _context() -> AgentContext:
    return AgentContext(
        task_id="task-1",
        task="Analyze costs",
        goal="Analyze task costs",
        current_state="planning",
    )


def test_orchestrator_imports():
    from sacm.core.orchestrator import Orchestrator

    assert Orchestrator is not None


def test_orchestrator_only_initializes_pending_tasks_to_planning():
    from sacm.core.orchestrator import Orchestrator

    assert Orchestrator._should_initialize_planning("pending") is True
    assert Orchestrator._should_initialize_planning("reviewing") is False
    assert Orchestrator._should_initialize_planning("testing") is False


def test_orchestrator_uses_deterministic_agents_for_terminal_phases():
    from sacm.core.orchestrator import Orchestrator

    assert Orchestrator._phase_agent_name("testing") == "CloudExecutor"
    assert Orchestrator._phase_agent_name("reviewing") == "Reviewer"
    assert Orchestrator._phase_agent_name("coding") is None


def test_agent_registry_has_agents():
    from sacm.core.agent_registry import AgentRegistry

    registry = AgentRegistry()
    assert len(registry.all()) == 19
    assert "ClaudeReasoner" in registry.names()
    assert "OpenTelemetryCost" in registry.names()
    assert "MLflowExperiment" in registry.names()
    assert "CodexExecutor" in registry.names()
    assert "GitHubDelivery" in registry.names()
    assert "EASWorkflow" in registry.names()
    assert "MobileE2E" in registry.names()
    assert "SecurityDelivery" in registry.names()
    assert "OpenAIAgentsExecutor" in registry.names()


def test_cost_agent_identifies_missing_telemetry_configuration(monkeypatch):
    monkeypatch.delenv("SACM_OTEL_ENABLED", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv(
        "SACM_OPENAI_EMBEDDING_INPUT_COST_PER_MILLION_USD", raising=False
    )

    result = OpenTelemetryCostAgent().run(_context())

    assert result.actions == [
        {
            "type": "COST_TELEMETRY",
            "otel_enabled": False,
            "collector_configured": False,
            "pricing_configured": False,
        }
    ]
    assert "cannot estimate costs" in result.summary


def test_mlflow_agent_does_not_claim_to_log_when_disabled(monkeypatch):
    monkeypatch.setenv("SACM_MLFLOW_ENABLED", "false")

    result = MLflowExperimentAgent().run(_context())

    assert result.actions == [{"type": "MLFLOW_EXPERIMENT", "logged": False}]
    assert [skill["skill_name"] for skill in result.skills_contributed] == [
        "router_experiment_assessed"
    ]


def test_verifier_done_on_high_confidence():
    from sacm.core.verifier import Verifier
    from sacm.schemas.result import AgentResult

    task = MagicMock()
    result = AgentResult(
        agent_name="test",
        summary="done",
        confidence=0.99,
        next_state_hint="reviewing",
        actions=[{"type": "TEST_RESULT", "passed": True}],
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
