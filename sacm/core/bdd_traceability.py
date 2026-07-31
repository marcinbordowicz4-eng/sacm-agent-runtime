import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from sacm.adapters.repository_adapter import RepositoryAdapter
from sacm.core.event_service import EventService
from sacm.infrastructure.db.models import Task

_STEP = re.compile(r"^\s*(Given|When|Then|And|But)\s+(.+?)\s*$", re.IGNORECASE)
_SCENARIO = re.compile(r"^\s*Scenario(?: Outline)?:\s*(.+?)\s*$", re.IGNORECASE)
_FEATURE = re.compile(r"^\s*Feature:\s*(.+?)\s*$", re.IGNORECASE)
_PATH = re.compile(r"(?<!\w)(?:[\w.-]+/)+[\w.-]+\.(?:ts|tsx|js|jsx|py|go|java)")
_STOP_WORDS = {"given", "when", "then", "and", "but", "with", "that", "this", "from"}


@dataclass(frozen=True)
class BddScenario:
    name: str
    steps: list[dict[str, str]]


class BddTraceabilityService:
    """Persists BDD requirements and deterministic Git/code impact evidence."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.events = EventService(db)

    @staticmethod
    def parse(description: str) -> tuple[str | None, list[BddScenario]]:
        feature: str | None = None
        scenarios: list[BddScenario] = []
        current: BddScenario | None = None
        for line in description.splitlines():
            if match := _FEATURE.match(line):
                feature = match.group(1)
            elif match := _SCENARIO.match(line):
                current = BddScenario(match.group(1), [])
                scenarios.append(current)
            elif match := _STEP.match(line):
                if current is None:
                    raise ValueError("BDD step must belong to a Scenario.")
                current.steps.append(
                    {"keyword": match.group(1).title(), "text": match.group(2)}
                )
        return feature, scenarios

    def register(self, task: Task, jira_key: str | None = None) -> dict[str, Any]:
        feature, scenarios = self.parse(task.description)
        payload = {
            "jira_key": jira_key,
            "feature": feature,
            "scenarios": [asdict(scenario) for scenario in scenarios],
            "requirement_hash": _sha256(task.description),
        }
        self.events.save(task.id, "bdd_requirement_registered", payload)
        from sacm.core.traceability_service import TraceabilityService

        TraceabilityService(self.db).refresh(task.id)
        return payload

    def analyze_git_impact(
        self, task: Task, base_revision: str, target_revision: str
    ) -> dict[str, Any]:
        if not task.target_repo_path:
            raise ValueError("Business impact analysis requires a target repository.")
        root = RepositoryAdapter(task.target_repo_path).repo_path
        base_commit = self._git(root, "rev-parse", base_revision)
        target_commit = self._git(root, "rev-parse", target_revision)
        changed_files = self._changed_files(root, base_commit, target_commit)
        referenced_paths = sorted(set(_PATH.findall(task.description)))
        related_paths = self._related_paths(root, task.description)
        impacted_paths = sorted(
            set(referenced_paths).intersection(changed_files)
            | set(related_paths).intersection(changed_files)
        )
        payload = {
            "base_revision": base_commit,
            "target_revision": target_commit,
            "base_tree": self._git(root, "rev-parse", f"{base_commit}^{{tree}}"),
            "target_tree": self._git(root, "rev-parse", f"{target_commit}^{{tree}}"),
            "changed_files": changed_files,
            "referenced_paths": referenced_paths,
            "related_paths": related_paths,
            "impacted_paths": impacted_paths,
            "business_logic_affected": bool(impacted_paths),
        }
        self.events.save(task.id, "business_impact_analyzed", payload)
        return payload

    @staticmethod
    def _git(root: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise ValueError(result.stderr.strip() or "Git analysis failed.")
        return result.stdout.strip()

    def _changed_files(self, root: Path, base: str, target: str) -> list[str]:
        output = self._git(root, "diff", "--name-only", base, target)
        return [path for path in output.splitlines() if path]

    @staticmethod
    def _related_paths(root: Path, description: str) -> list[str]:
        terms = {
            term.lower()
            for term in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", description)
            if term.lower() not in _STOP_WORDS
        }
        matches: list[str] = []
        for path in root.rglob("*"):
            if len(matches) >= 100 or not path.is_file() or ".git" in path.parts:
                continue
            if any(part in {"node_modules", "build", "dist"} for part in path.parts):
                continue
            if path.suffix not in {".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".java"}:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")[:1_000_000].lower()
            except OSError:
                continue
            if any(term in content for term in terms):
                matches.append(str(path.relative_to(root)))
        return sorted(matches)


def _sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()
