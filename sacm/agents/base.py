from abc import ABC, abstractmethod

from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class Agent(ABC):
    name: str
    role: str

    @abstractmethod
    def run(self, context: AgentContext) -> AgentResult:
        raise NotImplementedError
