from sacm.adapters.codex_executor_adapter import CodexExecutorAdapter
from sacm.agents.base import Agent
from sacm.core.observability import ObservabilityService
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class CodexExecutorAgent(Agent):
    """Implements a task with Codex in an isolated worktree."""

    name = "CodexExecutor"
    role = "code-execution"
    CONTRIBUTES_SKILLS = ["implementation_executed", "verification_evidence_collected"]

    def run(self, context: AgentContext) -> AgentResult:
        if not context.target_repo_path:
            return AgentResult(
                agent_name=self.name,
                summary="Codex execution requires a target repository path.",
                confidence=1.0,
                next_state_hint="blocked",
            )

        result = CodexExecutorAdapter(context.target_repo_path).execute(
            task_id=context.task_id,
            prompt=context.goal,
            verification_commands=[
                command
                for command in (context.build_command, context.test_command)
                if command
            ],
        )
        verification_passed = all(
            command["returncode"] == 0 for command in result["verification"]
        )
        codex_succeeded = result["codex"]["returncode"] == 0
        success = codex_succeeded and verification_passed
        resource_failure = any(
            command.get("failure_reason") == "INFRASTRUCTURE_RESOURCE"
            for command in result["verification"]
        )
        usage = self._usage_artifacts(result["usage"])
        tool_execution = self._tool_artifacts(result)
        summary = (
            f"{result['executor'].title()} completed isolated execution on branch "
            f"{result['branch_name']}."
            if success
            else f"{result['executor'].title()} execution failed on branch "
            f"{result['branch_name']}."
        )
        return AgentResult(
            agent_name=self.name,
            summary=summary,
            actions=[
                {
                    "type": "CODEX_EXECUTION",
                    "branch_name": result["branch_name"],
                    "worktree_path": result["worktree_path"],
                    "returncode": result["codex"]["returncode"],
                    "executor": result["executor"],
                    "provider": result["provider"],
                    "dependency_cache": result["dependency_cache"],
                }
            ]
            + [
                {
                    "type": "VERIFICATION_RETRY",
                    **command["retry_evidence"],
                }
                for command in result["verification"]
                if command.get("retry_evidence")
            ],
            artifacts=[
                {"type": "diff", "content": result["diff"][:20_000]},
                {
                    "type": "verification",
                    "passed": success,
                    "commands": result["verification"],
                },
                {"type": "codex_log", "content": result["codex"]["stdout"]},
                *usage,
                *tool_execution,
            ],
            confidence=1.0 if success else 0.2,
            next_state_hint=(
                "testing"
                if success
                else ("blocked" if resource_failure else "debugging")
            ),
            memory_update=summary,
            skills_contributed=[
                {
                    "skill_name": "implementation_executed",
                    "evidence": f"Codex ran in {result['worktree_path']}.",
                    "agent_name": self.name,
                    "confidence": 1.0 if codex_succeeded else 0.0,
                },
                {
                    "skill_name": "verification_evidence_collected",
                    "evidence": f"Ran {len(result['verification'])} verification commands.",
                    "agent_name": self.name,
                    "confidence": 1.0 if verification_passed else 0.0,
                },
            ],
        )

    @staticmethod
    def _usage_artifacts(records: list[dict]) -> list[dict]:
        observability = ObservabilityService()
        artifacts: list[dict] = []
        for record in records:
            estimated_cost = observability.record_model_usage(
                provider=record["provider"],
                model=record["model"],
                input_tokens=record["input_tokens"],
                output_tokens=record["output_tokens"],
                operation=record["operation"],
            )
            artifacts.append(
                {
                    "type": "usage",
                    **record,
                    "estimated_cost_usd": estimated_cost,
                }
            )
        return artifacts

    @staticmethod
    def _tool_artifacts(result: dict) -> list[dict]:
        observability = ObservabilityService()
        commands = [
            *(
                [
                    {
                        "tool": "dependency_setup",
                        "command": result["dependency_setup"]["command"],
                        "duration_ms": result["dependency_setup"]["duration_ms"],
                        "returncode": result["dependency_setup"]["returncode"],
                    }
                ]
                if result.get("dependency_setup")
                else []
            ),
            {
                "tool": result.get("executor", "codex"),
                "duration_ms": result["codex"]["duration_ms"],
                "returncode": result["codex"]["returncode"],
            },
            *[
                {
                    "tool": "verification",
                    "command": command["command"],
                    "duration_ms": command["duration_ms"],
                    "returncode": command["returncode"],
                }
                for command in result["verification"]
            ],
        ]
        for command in commands:
            observability.record_tool_execution(
                command["tool"], command["duration_ms"], command["returncode"]
            )
        return [{"type": "tool_execution", **command} for command in commands]
