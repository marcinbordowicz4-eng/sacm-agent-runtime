from sacm.agents.architect import ArchitectAgent
from sacm.agents.backend_agent import BackendAgent
from sacm.agents.base import Agent
from sacm.agents.claude_reasoner import ClaudeReasonerAgent
from sacm.agents.cloud_executor import CloudExecutorAgent
from sacm.agents.codex_coder import CodexCoderAgent
from sacm.agents.codex_executor import CodexExecutorAgent
from sacm.agents.context_agent import ContextAgent
from sacm.agents.eas_workflow import EASWorkflowAgent
from sacm.agents.frontend_agent import FrontendAgent
from sacm.agents.github_delivery import GitHubDeliveryAgent
from sacm.agents.infrastructure_agent import InfrastructureAgent
from sacm.agents.mlflow_experiment_agent import MLflowExperimentAgent
from sacm.agents.mobile_e2e import MobileE2EAgent
from sacm.agents.openai_agents_executor import OpenAIAgentsExecutorAgent
from sacm.agents.otel_cost_agent import OpenTelemetryCostAgent
from sacm.agents.reviewer import ReviewerAgent
from sacm.agents.security_auditor import SecurityAuditorAgent
from sacm.agents.security_delivery import SecurityDeliveryAgent
from sacm.agents.test_generator import TestGeneratorAgent

AGENT_CLASSES = [
    ClaudeReasonerAgent,
    CodexCoderAgent,
    CloudExecutorAgent,
    ReviewerAgent,
    TestGeneratorAgent,
    SecurityAuditorAgent,
    ArchitectAgent,
    BackendAgent,
    FrontendAgent,
    InfrastructureAgent,
    ContextAgent,
    OpenTelemetryCostAgent,
    MLflowExperimentAgent,
    GitHubDeliveryAgent,
    CodexExecutorAgent,
    EASWorkflowAgent,
    MobileE2EAgent,
    SecurityDeliveryAgent,
    OpenAIAgentsExecutorAgent,
]


class AgentRegistry:
    def __init__(self):
        self._agents: dict[str, Agent] = {}
        for cls in AGENT_CLASSES:
            agent = cls()
            self._agents[agent.name] = agent

    def get(self, name: str) -> Agent | None:
        return self._agents.get(name)

    def get_by_index(self, index: int) -> Agent:
        agents = list(self._agents.values())
        return agents[index % len(agents)]

    def all(self) -> list[Agent]:
        return list(self._agents.values())

    def names(self) -> list[str]:
        return list(self._agents.keys())
