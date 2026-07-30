import os

from sacm.adapters.codex_executor_adapter import CodexExecutorAdapter
from sacm.adapters.github_adapter import GitHubAdapter
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
        usage = self._usage_artifacts(result["usage"])
        tool_execution = self._tool_artifacts(result)
        pull_request = self._open_pull_request(context, result, success)
        summary = (
            f"Codex completed isolated execution on branch {result['branch_name']}."
            if success
            else f"Codex execution failed on branch {result['branch_name']}."
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
                }
            ]
            + ([pull_request] if pull_request else []),
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
            next_state_hint="testing" if success else "debugging",
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
            {
                "tool": "codex",
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

    @staticmethod
    def _open_pull_request(
        context: AgentContext, result: dict, success: bool
    ) -> dict | None:
        if os.getenv("SACM_CODEX_AUTO_CREATE_PR", "false").lower() != "true":
            return None
        if not success or not result["verification"]:
            return {
                "type": "PULL_REQUEST",
                "created": False,
                "reason": "Codex and at least one verification command must succeed.",
            }

        title = f"SACM: {context.goal}"[:120]
        pull_request = GitHubAdapter(result["worktree_path"]).commit_push_and_open_pull_request(
            title=title,
            body=(
                f"Automated SACM change for task `{context.task_id}`.\n\n"
                "Codex execution and configured verification commands passed."
            ),
            branch_name=result["branch_name"],
            base=os.getenv("SACM_GITHUB_BASE_BRANCH", "main"),
            draft=True,
        )
        return {
            "type": "PULL_REQUEST",
            "created": pull_request["returncode"] == 0,
            "stdout": pull_request["stdout"],
            "stderr": pull_request["stderr"],
        }
