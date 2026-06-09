from sacm.infrastructure.db.models import Task
from sacm.schemas.result import AgentResult


class Verifier:
    def is_done(self, task: Task, result: AgentResult) -> bool:
        del task
        if result.next_state_hint == "done":
            return True
        return result.confidence >= 0.95 and result.next_state_hint not in {
            "blocked",
            "debugging",
        }
