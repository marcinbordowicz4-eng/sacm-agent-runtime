import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from sacm.core.application_context_service import ApplicationContextService
from sacm.core.context_engine_service import ContextEngineService
from sacm.infrastructure.db.models import Task
from sacm.schemas.application_context import ContextExpansionRequest, ContextPackageV2
from sacm.schemas.contracts import AgentRole


@dataclass(frozen=True)
class ContextBriefing:
    package: ContextPackageV2
    metadata: dict[str, Any]


class ContextBriefingService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build(self, task: Task, *, role: AgentRole) -> ContextBriefing:
        application = ApplicationContextService(self.db).build(task.id)
        requirements = self._requirements(task)
        changed_files = self._changed_files(task.target_repo_path)
        package = ContextEngineService(self.db).build(
            task.id,
            ContextExpansionRequest(
                role=role,
                reason="initial_briefing",
                refresh_graph=False,
                changed_files=changed_files[:50],
                affected_requirements=requirements[:50],
                max_depth=2,
                max_nodes=64,
            ),
        )
        node_paths = sorted(
            {node.path for node in package.nodes if node.path}
        )
        test_files = sorted(
            {
                node.path
                for node in package.nodes
                if node.path and node.type in {"test_symbol", "test"}
            }
        )
        manifests = sorted(
            {
                node.path
                for node in package.nodes
                if node.path and node.type == "dependency_manifest"
            }
        )
        return ContextBriefing(
            package=package,
            metadata={
                "schema_version": "task-briefing/v1",
                "acceptance_criteria": requirements,
                "changed_files": changed_files,
                "relevant_files": node_paths,
                "test_files": test_files,
                "dependency_manifests": manifests,
                "repositories": [
                    {
                        "path": repository.resolved_path,
                        "status": repository.status,
                        "file_count": repository.file_count,
                    }
                    for repository in application.repositories
                ],
                "risk": application.risk_analysis.model_dump(mode="json"),
                "impact": application.impact_analysis.model_dump(mode="json"),
            },
        )

    @staticmethod
    def _requirements(task: Task) -> list[str]:
        contract = task.task_contract or {}
        criteria = contract.get("acceptance_criteria")
        if isinstance(criteria, list):
            normalized = [str(item).strip() for item in criteria if str(item).strip()]
            if normalized:
                return normalized
        values = [
            line.strip(" -*\t")
            for line in task.description.replace(". ", ".\n").splitlines()
            if line.strip(" -*\t")
        ]
        return values[:20] or [task.title]

    @staticmethod
    def _changed_files(repository_path: str | None) -> list[str]:
        if not repository_path:
            return []
        root = Path(repository_path).expanduser().resolve()
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "status", "--porcelain", "-z"],
                capture_output=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []
        paths: list[str] = []
        for entry in result.stdout.decode("utf-8", errors="replace").split("\0"):
            if len(entry) < 4:
                continue
            path = entry[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            if path:
                paths.append(path)
        return sorted(set(paths))
