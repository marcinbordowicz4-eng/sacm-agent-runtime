from sacm.agents.architect import ArchitectAgent
from sacm.agents.base import Agent
from sacm.agents.claude_reasoner import ClaudeReasonerAgent
from sacm.agents.cloud_executor import CloudExecutorAgent
from sacm.agents.codex_coder import CodexCoderAgent
from sacm.agents.reviewer import ReviewerAgent
from sacm.agents.security_auditor import SecurityAuditorAgent
from sacm.agents.test_generator import TestGeneratorAgent

AGENT_CLASSES = [
    ClaudeReasonerAgent,
    CodexCoderAgent,
    CloudExecutorAgent,
    ReviewerAgent,
    TestGeneratorAgent,
    SecurityAuditorAgent,
    ArchitectAgent,
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
