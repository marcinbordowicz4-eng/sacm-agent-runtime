import os
import shutil
import subprocess
from pathlib import Path

from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class MobileE2EAgent(Agent):
    """Runs checked-in Maestro flows only when explicitly enabled."""

    name = "MobileE2E"
    role = "mobile-e2e"
    CONTRIBUTES_SKILLS = ["mobile_e2e_executed"]

    def run(self, context: AgentContext) -> AgentResult:
        if not context.target_repo_path:
            return AgentResult(
                agent_name=self.name,
                summary="Mobile E2E execution requires a target repository path.",
                confidence=1.0,
                next_state_hint="blocked",
            )

        flow_dir = Path(context.target_repo_path) / ".maestro"
        flows = sorted(flow_dir.glob("*.yaml")) if flow_dir.is_dir() else []
        if not flows:
            return self._result(
                "No Maestro flows were found in .maestro.", False, [], "planning"
            )
        if os.getenv("SACM_RUN_MOBILE_E2E", "false").lower() != "true":
            return self._result(
                "Maestro flows are present but execution is disabled.", False, flows, "testing"
            )
        if shutil.which("maestro") is None:
            return self._result(
                "Maestro execution is enabled but the maestro CLI is unavailable.",
                False,
                flows,
                "blocked",
            )

        completed = subprocess.run(
            ["maestro", "test", str(flow_dir)],
            cwd=context.target_repo_path,
            capture_output=True,
            text=True,
            timeout=1_200,
            check=False,
        )
        passed = completed.returncode == 0
        return self._result(
            "Maestro flows passed." if passed else "Maestro flows failed.",
            passed,
            flows,
            "reviewing" if passed else "debugging",
            completed.stdout + completed.stderr,
        )

    def _result(
        self,
        summary: str,
        passed: bool,
        flows: list[Path],
        next_state_hint: str,
        log: str = "",
    ) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            summary=summary,
            actions=[
                {
                    "type": "MAESTRO_E2E",
                    "flow_count": len(flows),
                    "executed": bool(log),
                    "passed": passed,
                }
            ],
            artifacts=[
                {"type": "verification", "passed": passed, "flows": [str(flow) for flow in flows]},
                {"type": "maestro_log", "content": log[-20_000:]},
            ],
            confidence=1.0 if passed else 0.0,
            next_state_hint=next_state_hint,
            memory_update=summary,
            skills_contributed=[
                {
                    "skill_name": "mobile_e2e_executed",
                    "evidence": summary,
                    "agent_name": self.name,
                    "confidence": 1.0 if passed else 0.0,
                }
            ],
        )
