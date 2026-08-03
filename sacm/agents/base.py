from abc import ABC, abstractmethod
from typing import Any, Callable, Literal

from sacm.schemas.context import AgentContext
from sacm.schemas.contracts import (
    AgentResultV1,
    AgentTaskV1,
    ArtifactReference,
    UsageRecord,
)
from sacm.schemas.result import AgentResult


class Agent(ABC):
    name: str
    role: str
    provider: str = "sacm"
    model: str = "deterministic"
    framework: str = "native"

    @abstractmethod
    def run(self, context: AgentContext) -> AgentResult:
        raise NotImplementedError

    def run_v1(
        self,
        task: AgentTaskV1,
        *,
        telemetry_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentResultV1:
        """Execute the legacy implementation through the versioned contract."""
        return self._result_to_v1(
            task,
            self.run_with_telemetry(self._context_from_v1(task), telemetry_sink),
        )

    def run_with_telemetry(
        self,
        context: AgentContext,
        telemetry_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentResult:
        """Run with an ephemeral sink; the versioned task remains serializable."""
        del telemetry_sink
        return self.run(context)

    def result_from_v1(self, result: AgentResultV1) -> AgentResult:
        """Adapt a versioned result for legacy services during the migration."""
        artifacts = [
            {
                "type": artifact.artifact_type,
                **({"uri": artifact.uri} if artifact.uri else {}),
                **({"sha256": artifact.sha256} if artifact.sha256 else {}),
                **artifact.metadata,
            }
            for artifact in result.artifacts
        ]
        return AgentResult(
            agent_name=self.name,
            summary=result.summary,
            actions=result.actions,
            artifacts=artifacts,
            confidence=result.confidence,
            next_state_hint=result.next_state_hint,
            memory_update=result.memory_update,
            skills_contributed=result.skills_contributed,
        )

    @property
    def contract_role(
        self,
    ) -> Literal["reasoner", "coder", "reviewer", "tester", "security"]:
        role_map: dict[
            str, Literal["reasoner", "coder", "reviewer", "tester", "security"]
        ] = {
            "reasoning": "reasoner",
            "agent-sdk-reasoning": "reasoner",
            "architecture": "reasoner",
            "orchestration": "reasoner",
            "coding": "coder",
            "code-execution": "coder",
            "execution": "coder",
            "backend": "coder",
            "frontend": "coder",
            "infrastructure": "coder",
            "review": "reviewer",
            "testing": "tester",
            "mobile-e2e": "tester",
            "security": "security",
            "security-delivery": "security",
        }
        return role_map.get(self.role, "reasoner")

    @staticmethod
    def _context_from_v1(task: AgentTaskV1) -> AgentContext:
        context: dict[str, Any] = task.execution_context
        return AgentContext(
            task_id=str(context.get("task_id", task.run_id)),
            task=str(context.get("task", task.objective)),
            goal=str(context.get("goal", task.objective)),
            current_state=str(context.get("current_state", "planning")),
            target_repo_path=context.get("target_repo_path"),
            relevant_memory=list(context.get("relevant_memory", [])),
            files=dict(context.get("files", {})),
            constraints=list(context.get("constraints", [])),
            previous_findings=list(context.get("previous_findings", [])),
            test_command=context.get("test_command"),
            build_command=context.get("build_command"),
            token_budget=task.token_budget,
            context_package=context.get("context_package"),
            briefing=context.get("briefing"),
            skill_state=dict(context.get("skill_state", {})),
        )

    @staticmethod
    def _result_to_v1(task: AgentTaskV1, result: AgentResult) -> AgentResultV1:
        artifacts = [
            ArtifactReference(
                artifact_type=str(artifact.get("type", "agent_artifact")),
                uri=artifact.get("uri"),
                sha256=artifact.get("sha256"),
                metadata={
                    key: value
                    for key, value in artifact.items()
                    if key not in {"type", "uri", "sha256"}
                },
            )
            for artifact in result.artifacts
        ]
        usage = [
            UsageRecord(
                provider=str(artifact["provider"]),
                model=str(artifact["model"]),
                input_tokens=int(artifact["input_tokens"]),
                output_tokens=int(artifact["output_tokens"]),
                estimated_cost_usd=artifact.get("estimated_cost_usd"),
            )
            for artifact in result.artifacts
            if artifact.get("type") == "usage"
            and {"provider", "model", "input_tokens", "output_tokens"}
            <= artifact.keys()
        ]
        needs_approval = result.next_state_hint in {
            "awaiting_approval",
            "needs_approval",
        }
        failed = result.next_state_hint == "blocked"
        return AgentResultV1(
            run_id=task.run_id,
            step_id=task.step_id,
            status=(
                "NEEDS_APPROVAL"
                if needs_approval
                else "FAILED"
                if failed
                else "COMPLETED"
            ),
            summary=result.summary,
            artifacts=artifacts,
            evidence=[
                artifact
                for artifact in artifacts
                if artifact.artifact_type in {"verification", "test_result", "evidence"}
            ],
            decisions=result.actions,
            usage=usage,
            failure={"reason": result.summary} if failed else None,
            actions=result.actions,
            confidence=result.confidence,
            next_state_hint=result.next_state_hint,
            memory_update=result.memory_update,
            skills_contributed=result.skills_contributed,
        )
