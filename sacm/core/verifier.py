from sacm.infrastructure.db.models import Task
from sacm.schemas.result import AgentResult


class Verifier:
    @staticmethod
    def has_successful_verification(result: AgentResult) -> bool:
        verification_action_types = {"BUILD_RESULT", "TEST_RESULT", "VERIFICATION"}
        for action in result.actions:
            if action.get("type") in verification_action_types and (
                action.get("passed") is True or action.get("success") is True
            ):
                return True
        return any(
            artifact.get("type") == "verification" and artifact.get("passed") is True
            for artifact in result.artifacts
        )

    def is_done(self, task: Task, result: AgentResult) -> bool:
        del task
        if not self.has_successful_verification(result):
            return False
        if result.next_state_hint == "done":
            return True
        return result.confidence >= 0.95 and result.next_state_hint not in {
            "blocked",
            "debugging",
        }
