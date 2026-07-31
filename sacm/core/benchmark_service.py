from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, model_validator

from sacm.schemas.contracts import ExternalAgentStepCreate
from sacm.schemas.run import RunCreate

SUITE_SCHEMA = "sacm-benchmark-suite/v2"
REPORT_SCHEMA = "sacm-benchmark-report/v2"
COMPARISON_SCHEMA = "sacm-benchmark-comparison/v2"
FIXTURE_SCHEMA = "sacm-benchmark-fixtures/v2"

CATEGORY_COUNTS = {
    "bug": 20,
    "feature": 20,
    "refactor": 15,
    "migration": 15,
    "security": 10,
    "multi-repo": 10,
    "reliability-recovery": 10,
}
LANGUAGES = ("python", "typescript", "react", "java", "go")
ABLATIONS = (
    "sacm-full",
    "sacm-no-reviewer",
    "sacm-no-policy",
    "sacm-no-replay-recovery",
    "single-agent-baseline",
)
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "BLOCKED", "NOT_RUN"}
FIXED_GIT_DATE = "2026-01-01T00:00:00+00:00"


def ablation_controls(ablation: str) -> dict[str, bool]:
    controls = {
        "reviewer_enabled": True,
        "policy_enabled": True,
        "replay_recovery_enabled": True,
    }
    if ablation == "sacm-no-reviewer":
        controls["reviewer_enabled"] = False
    elif ablation == "sacm-no-policy":
        controls["policy_enabled"] = False
    elif ablation == "sacm-no-replay-recovery":
        controls["replay_recovery_enabled"] = False
    elif ablation == "single-agent-baseline":
        controls = {key: False for key in controls}
    return controls


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


class RepositoryFixtureV2(BaseModel):
    fixture_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]+$")
    source: Literal["local-template"] = "local-template"
    template_version: str = "benchmark-fixture/v2"
    revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    relative_path: str


class BudgetV2(BaseModel):
    timeout_seconds: int = Field(gt=0)
    token_limit: int = Field(gt=0)
    cost_limit_usd: float = Field(gt=0)
    max_attempts: int = Field(default=3, gt=0)


class ExpectedArtifactsV2(BaseModel):
    required: list[str] = Field(min_length=1)
    forbidden: list[str] = Field(default_factory=list)


class BenchmarkCaseV2(BaseModel):
    schema_version: Literal["benchmark-case/v2"] = "benchmark-case/v2"
    id: str = Field(pattern=r"^bench-[0-9]{3}$")
    title: str
    description: str
    repositories: list[RepositoryFixtureV2] = Field(min_length=1, max_length=2)
    language: Literal["python", "typescript", "react", "java", "go"]
    category: Literal[
        "bug",
        "feature",
        "refactor",
        "migration",
        "security",
        "multi-repo",
        "reliability-recovery",
    ]
    acceptance_criteria: list[str] = Field(min_length=2)
    verification_commands: list[list[str]] = Field(min_length=1)
    allowed_commands: list[str] = Field(min_length=1)
    allowed_tools: list[str] = Field(min_length=1)
    denied_tools: list[str] = Field(default_factory=list)
    budget: BudgetV2
    risk: Literal["low", "medium", "high", "critical"]
    expected_artifacts: ExpectedArtifactsV2
    fixture_family: str
    variant: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_repositories(self) -> "BenchmarkCaseV2":
        expected = 2 if self.category == "multi-repo" else 1
        if len(self.repositories) != expected:
            raise ValueError(f"{self.category} cases require {expected} repositories")
        return self


class BenchmarkSuiteV2(BaseModel):
    schema_version: Literal["sacm-benchmark-suite/v2"] = "sacm-benchmark-suite/v2"
    suite_id: Literal["sacm-benchmark-100-v2"] = "sacm-benchmark-100-v2"
    license: Literal["Apache-2.0"] = "Apache-2.0"
    generated_by: str = "sacm benchmark generate"
    cases: list[BenchmarkCaseV2]


class ExecutionConfigV2(BaseModel):
    runner: Literal["baseline-command", "sacm-execution-plane"]
    ablation: Literal[
        "sacm-full",
        "sacm-no-reviewer",
        "sacm-no-policy",
        "sacm-no-replay-recovery",
        "single-agent-baseline",
    ]
    provider: str = ""
    model: str = ""
    model_version: str = ""
    agent_name: str = ""
    agent_version: str = ""
    runtime_version: str = ""
    command: list[str] = Field(default_factory=list)
    result_file: str = ".benchmark-agent-result.json"
    database_url: str | None = Field(default=None, exclude=True)
    project_id: str | None = None
    executor_identity: str | None = None
    required_labels: dict[str, str] = Field(default_factory=dict)
    poll_interval_seconds: float = Field(default=2.0, gt=0)

    @model_validator(mode="after")
    def validate_result_file(self) -> "ExecutionConfigV2":
        result_path = Path(self.result_file)
        if result_path.is_absolute() or ".." in result_path.parts:
            raise ValueError("result_file must be a workspace-relative safe path")
        return self

    def configured(self) -> bool:
        common = self.identity_configured()
        if self.runner == "baseline-command":
            return common and bool(self.command)
        return common and all(
            (self.database_url, self.project_id, self.executor_identity)
        )

    def identity_configured(self) -> bool:
        return all(
            (
                self.provider,
                self.model,
                self.model_version,
                self.agent_name,
                self.agent_version,
                self.runtime_version,
            )
        )

    def configured_for_report(self) -> bool:
        if self.runner == "baseline-command":
            return self.identity_configured() and bool(self.command)
        return self.identity_configured() and all(
            (self.project_id, self.executor_identity)
        )

    def fingerprint(self) -> str:
        safe = self.model_dump(exclude={"database_url"})
        return _canonical_sha256(safe)


class EvidenceReferenceV2(BaseModel):
    kind: str
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class VerificationResultV2(BaseModel):
    command: list[str]
    exit_code: int
    duration_ms: int = Field(ge=0)
    stdout_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    stderr_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CaseResultV2(BaseModel):
    case_id: str
    status: Literal["COMPLETED", "FAILED", "BLOCKED", "NOT_RUN"]
    reason: str
    config_fingerprint: str
    budget: BudgetV2
    simulated: Literal[False] = False
    external_execution: bool
    started_at: datetime | None = None
    completed_at: datetime | None = None
    wall_time_seconds: float = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)
    attempts: int = Field(default=0, ge=0)
    interventions: int = Field(default=0, ge=0)
    recovery_time_seconds: float | None = Field(default=None, ge=0)
    requirement_coverage: float = Field(default=0, ge=0, le=1)
    regression_findings: list[str] = Field(default_factory=list)
    security_findings: list[str] = Field(default_factory=list)
    verification: list[VerificationResultV2] = Field(default_factory=list)
    evidence: list[EvidenceReferenceV2] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    run_id: str | None = None
    job_id: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def solved(self) -> bool:
        return (
            self.status == "COMPLETED"
            and bool(self.verification)
            and all(item.exit_code == 0 for item in self.verification)
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def accepted_pr_proxy(self) -> bool:
        """Strict offline proxy: solved, full coverage, clean regression/security."""
        empty_sha = hashlib.sha256(b"").hexdigest()
        return (
            self.solved
            and self.requirement_coverage == 1.0
            and not self.regression_findings
            and not self.security_findings
            and "git-diff.patch" in self.artifacts
            and any(
                item.kind == "git-diff" and item.sha256 != empty_sha
                for item in self.evidence
            )
        )


class EnvironmentV2(BaseModel):
    os: str
    architecture: str
    python: str
    git: str
    tools: dict[str, str]
    repository_revision: str
    dirty: bool


class BenchmarkReportV2(BaseModel):
    schema_version: Literal["sacm-benchmark-report/v2"] = (
        "sacm-benchmark-report/v2"
    )
    report_id: str
    suite_id: str
    suite_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["COMPLETE", "PARTIAL", "NOT_RUN", "INVALID"]
    truthful_status: str
    created_at: datetime
    configuration: ExecutionConfigV2
    environment: EnvironmentV2
    results: list[CaseResultV2]


class BenchmarkValidityError(ValueError):
    pass


def load_suite(path: str | Path) -> BenchmarkSuiteV2:
    return BenchmarkSuiteV2.model_validate_json(Path(path).read_text(encoding="utf-8"))


def suite_sha256(suite: BenchmarkSuiteV2) -> str:
    return _canonical_sha256(suite.model_dump(mode="json"))


def validate_suite(suite: BenchmarkSuiteV2) -> dict[str, Any]:
    errors: list[str] = []
    if len(suite.cases) != 100:
        errors.append(f"suite must contain exactly 100 cases, found {len(suite.cases)}")
    ids = [case.id for case in suite.cases]
    if len(ids) != len(set(ids)):
        errors.append("case IDs must be unique")
    categories = Counter(case.category for case in suite.cases)
    if dict(categories) != CATEGORY_COUNTS:
        errors.append(
            f"category distribution must be {CATEGORY_COUNTS}, found {dict(categories)}"
        )
    languages = Counter(case.language for case in suite.cases)
    expected_languages = {language: 20 for language in LANGUAGES}
    if dict(languages) != expected_languages:
        errors.append(
            f"language distribution must be {expected_languages}, found {dict(languages)}"
        )
    fixture_ids: set[str] = set()
    for case in suite.cases:
        if not case.verification_commands or any(
            not command for command in case.verification_commands
        ):
            errors.append(f"{case.id}: verification commands are incomplete")
        for repository in case.repositories:
            fixture_ids.add(repository.fixture_id)
            if repository.source != "local-template":
                errors.append(f"{case.id}: repository source is not reproducible")
            if set(repository.revision) == {"0"}:
                errors.append(f"{case.id}: repository revision is unpinned")
    if errors:
        raise BenchmarkValidityError("; ".join(errors))
    return {
        "schema_version": SUITE_SCHEMA,
        "suite_id": suite.suite_id,
        "case_count": len(suite.cases),
        "category_counts": dict(sorted(categories.items())),
        "language_counts": dict(sorted(languages.items())),
        "fixture_count": len(fixture_ids),
        "sha256": suite_sha256(suite),
        "ready": True,
    }


def _git_version() -> str:
    result = subprocess.run(
        ["git", "--version"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "unavailable"


def capture_environment() -> EnvironmentV2:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, check=False
    )
    tools = {}
    for name, command in {
        "node": ["node", "--version"],
        "java": ["java", "-version"],
        "go": ["go", "version"],
    }.items():
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False, timeout=10
            )
            tools[name] = (result.stdout or result.stderr).splitlines()[0]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            tools[name] = "unavailable"
    return EnvironmentV2(
        os=platform.platform(),
        architecture=platform.machine(),
        python=sys.version.split()[0],
        git=_git_version(),
        tools=tools,
        repository_revision=revision.stdout.strip() or "unavailable",
        dirty=bool(dirty.stdout.strip()),
    )


def _fixture_source(case: BenchmarkCaseV2, repo_index: int) -> dict[str, str]:
    category_token = case.category.replace("-", "_").upper()
    target_token = f"{category_token}_IMPLEMENTED_VARIANT_{case.variant}"
    scenario = {
        "bug": {
            "input": {"subtotal_cents": 1099 + case.variant, "discount_percent": 15},
            "expected": "integer cents after percentage discount and half-up rounding",
        },
        "feature": {
            "input": {"attempt": case.variant + 1, "base_ms": 100, "cap_ms": 5000},
            "expected": "bounded exponential retry delay with invalid-attempt rejection",
        },
        "refactor": {
            "input": {"name": "  ADA  ", "email": " ADA@EXAMPLE.COM "},
            "expected": "one reusable normalization helper and unchanged output",
        },
        "migration": {
            "input": {"customer_id": f"new-{case.variant}", "customerId": "legacy"},
            "expected": "new field precedence with legacy-only compatibility",
        },
        "security": {
            "input": {"safe": "reports/daily.json", "unsafe": "../secrets.env"},
            "expected": "canonical containment check rejects traversal",
        },
        "multi-repo": {
            "input": {"event_id": case.variant, "trace_id": f"trace-{case.variant}"},
            "expected": "producer and consumer agree on event-envelope/v2",
        },
        "reliability-recovery": {
            "input": {"attempt": case.variant + 2, "completed_checkpoint": case.variant},
            "expected": "bounded retry plus idempotent resume after checkpoint",
        },
    }[case.category]
    if case.language == "python":
        source_path = "src/service.py"
        implementations = {
            "bug": (
                "def invoice_total(subtotal_cents: int, discount_percent: int) -> int:\n"
                "    return int(subtotal_cents * discount_percent / 100)\n"
            ),
            "feature": (
                "def retry_delay(attempt: int, base_ms: int, cap_ms: int) -> int:\n"
                '    raise NotImplementedError("retry scheduling is not implemented")\n'
            ),
            "refactor": (
                "def normalize_customer(name: str, email: str) -> dict[str, str]:\n"
                "    return {\"name\": name.strip().lower(), "
                "\"email\": email.strip().lower()}\n"
            ),
            "migration": (
                "def customer_id(payload: dict[str, str]) -> str:\n"
                "    return payload[\"customerId\"]\n"
            ),
            "security": (
                "from pathlib import Path\n\n"
                "def repository_path(root: Path, requested: str) -> Path:\n"
                "    return root / requested\n"
            ),
            "multi-repo": (
                "def event_envelope(event_id: int, trace_id: str) -> dict[str, object]:\n"
                "    return {\"version\": 1, \"event_id\": event_id}\n"
            ),
            "reliability-recovery": (
                "def resume_job(attempt: int, checkpoint: int) -> dict[str, int]:\n"
                "    return {\"delay_ms\": 2 ** attempt * 100, \"resume_from\": 0}\n"
            ),
        }
        source = (
            '"""Dependency-free service logic for a focused engineering change."""\n\n'
            f'FIXTURE_ID = "{case.id}"\n'
            'IMPLEMENTATION_STATE = "BENCHMARK_TASK_PENDING"\n'
            f"REPOSITORY_PART = {repo_index + 1}\n\n"
            f"{implementations[case.category]}"
        )
        test_path = "tests/test_acceptance.py"
        test_source = (
            "from pathlib import Path\n\n"
            "SOURCE = Path(__file__).parents[1] / 'src' / 'service.py'\n\n"
            "def test_task_is_implemented():\n"
            f"    assert {target_token!r} in SOURCE.read_text(encoding='utf-8')\n"
        )
    elif case.language == "typescript":
        source_path = "src/service.ts"
        implementations = {
            "bug": "return Math.trunc(subtotalCents * discountPercent / 100);",
            "feature": 'throw new Error("retry scheduling is not implemented");',
            "refactor": "return { name: name.trim().toLowerCase(), email: email.trim().toLowerCase() };",
            "migration": 'return payload.customerId ?? "";',
            "security": "return `${root}/${requested}`;",
            "multi-repo": 'return { version: 1, eventId };',
            "reliability-recovery": "return { delayMs: 2 ** attempt * 100, resumeFrom: 0 };",
        }
        source = (
            "export const fixtureId: string = "
            f'"{case.id}";\nexport const implementationState = '
            '"BENCHMARK_TASK_PENDING";\n'
            f"export const repositoryPart: number = {repo_index + 1};\n\n"
            "export function applyTask(value: Record<string, unknown>): unknown {\n"
            "  const subtotalCents = Number(value.subtotalCents ?? 0);\n"
            "  const discountPercent = Number(value.discountPercent ?? 0);\n"
            "  const attempt = Number(value.attempt ?? 0);\n"
            "  const root = String(value.root ?? '.');\n"
            "  const requested = String(value.requested ?? '');\n"
            "  const eventId = Number(value.eventId ?? 0);\n"
            "  const name = String(value.name ?? '');\n"
            "  const email = String(value.email ?? '');\n"
            "  const payload = value as { customerId?: string };\n"
            f"  {implementations[case.category]}\n"
            "}\n"
        )
        test_path = "tests/acceptance.test.ts"
        test_source = (
            'import { readFileSync } from "node:fs";\n'
            "const source = readFileSync(new URL('../src/service.ts', import.meta.url), 'utf8');\n"
            f"if (!source.includes({target_token!r})) throw new Error('task remains incomplete');\n"
        )
    elif case.language == "react":
        source_path = "src/TaskPanel.tsx"
        source = (
            'import React from "react";\n\n'
            f'export const fixtureId = "{case.id}";\n'
            "type Props = { status: string; retryCount: number; error?: string };\n\n"
            "export function TaskPanel({ status, retryCount, error }: Props) {\n"
            '  return <section aria-label="task-status">\n'
            "    <h2>{status}</h2><p>Retries: {retryCount}</p>\n"
            "    {error ? <p>{error}</p> : null}\n"
            "    <span>BENCHMARK_TASK_PENDING</span>\n"
            "  </section>;\n"
            "}\n"
        )
        test_path = "src/TaskPanel.test.tsx"
        test_source = (
            'import { readFileSync } from "node:fs";\n'
            "const source = readFileSync(new URL('./TaskPanel.tsx', import.meta.url), 'utf8');\n"
            f"if (!source.includes({target_token!r})) throw new Error('task remains incomplete');\n"
        )
    elif case.language == "java":
        source_path = "src/main/java/Service.java"
        source = (
            "public final class Service {\n"
            f'    public static final String FIXTURE_ID = "{case.id}";\n'
            '    public static final String IMPLEMENTATION_STATE = '
            '"BENCHMARK_TASK_PENDING";\n'
            f"    public static final int REPOSITORY_PART = {repo_index + 1};\n"
            "\n"
            "    public static int retryDelay(int attempt, int baseMs, int capMs) {\n"
            "        return baseMs * (1 << attempt);\n"
            "    }\n"
            "}\n"
        )
        test_path = "src/test/java/ServiceAcceptance.java"
        test_source = (
            "import java.nio.file.Files;\n"
            "import java.nio.file.Path;\n\n"
            "public final class ServiceAcceptance {\n"
            "    public static void main(String[] args) throws Exception {\n"
            '        String source = Files.readString(Path.of("src/main/java/Service.java"));\n'
            f'        if (!source.contains("{target_token}")) '
            'throw new AssertionError("task remains incomplete");\n'
            "    }\n"
            "}\n"
        )
    else:
        source_path = "service.go"
        source = (
            "package fixture\n\n"
            f'const FixtureID = "{case.id}"\n'
            'const ImplementationState = "BENCHMARK_TASK_PENDING"\n'
            f"const RepositoryPart = {repo_index + 1}\n\n"
            "func RetryDelay(attempt, baseMS, capMS int) int {\n"
            "\treturn baseMS << attempt\n"
            "}\n"
        )
        test_path = "service_test.go"
        test_source = (
            "package fixture\n\n"
            'import ("os"; "strings"; "testing")\n\n'
            "func TestTaskImplemented(t *testing.T) {\n"
            '\tsource, err := os.ReadFile("service.go"); if err != nil { t.Fatal(err) }\n'
            f'\tif !strings.Contains(string(source), "{target_token}") '
            '{ t.Fatal("task remains incomplete") }\n'
            "}\n"
        )
    return {
        source_path: source,
        test_path: test_source,
        "README.md": (
            f"# {case.title}\n\n{case.description}\n\n"
            "This repository is original Apache-2.0 benchmark fixture code.\n"
        ),
        "TASK.json": json.dumps(
            {
                "case_id": case.id,
                "category": case.category,
                "language": case.language,
                "acceptance_criteria": case.acceptance_criteria,
                "required_token": target_token,
                "repository_part": repo_index + 1,
                "scenario": scenario,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        ".gitignore": "__pycache__/\n*.class\n",
    }


def _verification_script(case: BenchmarkCaseV2) -> str:
    source_globs = {
        "python": ["repo/src/service.py"],
        "typescript": ["repo/src/service.ts"],
        "react": ["repo/src/TaskPanel.tsx"],
        "java": ["repo/src/main/java/Service.java"],
        "go": ["repo/service.go"],
    }
    paths = source_globs[case.language]
    if case.category == "multi-repo":
        paths = [path.replace("repo/", f"repo-{index}/") for index in (1, 2) for path in paths]
    token = f"{case.category.replace('-', '_').upper()}_IMPLEMENTED_VARIANT_{case.variant}"
    return (
        "from pathlib import Path\n\n"
        f"paths = {paths!r}\n"
        f"required = {token!r}\n"
        "missing = []\n"
        "for name in paths:\n"
        "    text = Path(name).read_text(encoding='utf-8')\n"
        "    if required not in text or 'BENCHMARK_TASK_PENDING' in text:\n"
        "        missing.append(name)\n"
        "if missing:\n"
        "    raise SystemExit('acceptance criteria not implemented in: ' + ', '.join(missing))\n"
        "print('fixture acceptance checks passed')\n"
    )


class FixtureGenerator:
    def __init__(self, suite: BenchmarkSuiteV2) -> None:
        self.suite = suite

    def generate(
        self, destination: str | Path, case_ids: set[str] | None = None
    ) -> dict[str, Any]:
        root = Path(destination)
        root.mkdir(parents=True, exist_ok=True)
        generated: list[dict[str, Any]] = []
        for case in self.suite.cases:
            if case_ids and case.id not in case_ids:
                continue
            case_root = root / case.id
            if case_root.exists():
                shutil.rmtree(case_root)
            case_root.mkdir(parents=True)
            (case_root / "TASK.json").write_text(
                case.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
            (case_root / "verify.py").write_text(
                _verification_script(case), encoding="utf-8"
            )
            for index, repository in enumerate(case.repositories):
                repo_root = case_root / repository.relative_path
                for relative, content in _fixture_source(case, index).items():
                    target = repo_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                revision = self._commit(repo_root, repository.fixture_id)
                if revision != repository.revision:
                    raise BenchmarkValidityError(
                        f"{case.id}: generated revision {revision} does not match "
                        f"manifest {repository.revision}"
                    )
            generated.append(
                {
                    "case_id": case.id,
                    "path": str(case_root),
                    "repositories": [
                        repository.model_dump(mode="json")
                        for repository in case.repositories
                    ],
                }
            )
        manifest = {
            "schema_version": FIXTURE_SCHEMA,
            "suite_id": self.suite.suite_id,
            "suite_sha256": suite_sha256(self.suite),
            "generated": generated,
        }
        (root / "fixture-generation.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return manifest

    @staticmethod
    def _commit(repo: Path, fixture_id: str) -> str:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "SACM Benchmark",
            "GIT_AUTHOR_EMAIL": "benchmark@sacm.invalid",
            "GIT_COMMITTER_NAME": "SACM Benchmark",
            "GIT_COMMITTER_EMAIL": "benchmark@sacm.invalid",
            "GIT_AUTHOR_DATE": FIXED_GIT_DATE,
            "GIT_COMMITTER_DATE": FIXED_GIT_DATE,
            "LC_ALL": "C",
        }
        commands = (
            ["git", "init", "-q", "-b", "main"],
            ["git", "config", "core.filemode", "false"],
            ["git", "add", "."],
            ["git", "commit", "-q", "-m", f"Initialize {fixture_id}"],
        )
        for command in commands:
            subprocess.run(command, cwd=repo, env=env, check=True, capture_output=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()


def _evidence(path: Path, kind: str) -> EvidenceReferenceV2:
    return EvidenceReferenceV2(
        kind=kind,
        path=str(path),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _run_verification(
    case: BenchmarkCaseV2, workspace: Path, evidence_dir: Path
) -> tuple[list[VerificationResultV2], list[EvidenceReferenceV2]]:
    results: list[VerificationResultV2] = []
    evidence: list[EvidenceReferenceV2] = []
    for index, command in enumerate(case.verification_commands):
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                timeout=case.budget.timeout_seconds,
                check=False,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            exit_code = completed.returncode
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            stdout = b""
            stderr = str(exc).encode()
            exit_code = 127
        duration = round((time.perf_counter() - started) * 1000)
        stdout_path = evidence_dir / f"verify-{index}.stdout"
        stderr_path = evidence_dir / f"verify-{index}.stderr"
        stdout_path.write_bytes(stdout)
        stderr_path.write_bytes(stderr)
        evidence.extend((_evidence(stdout_path, "verification-stdout"), _evidence(stderr_path, "verification-stderr")))
        results.append(
            VerificationResultV2(
                command=command,
                exit_code=exit_code,
                duration_ms=duration,
                stdout_sha256=hashlib.sha256(stdout).hexdigest(),
                stderr_sha256=hashlib.sha256(stderr).hexdigest(),
            )
        )
    return results, evidence


class BaselineCommandRunner:
    def __init__(self, config: ExecutionConfigV2) -> None:
        self.config = config

    def run_case(self, case: BenchmarkCaseV2, workspace: Path) -> CaseResultV2:
        fingerprint = self.config.fingerprint()
        if not self.config.configured():
            return CaseResultV2(
                case_id=case.id,
                status="NOT_RUN",
                reason="External baseline command and complete model/version config are required.",
                config_fingerprint=fingerprint,
                budget=case.budget,
                external_execution=False,
            )
        executable = shutil.which(self.config.command[0])
        if executable is None:
            return CaseResultV2(
                case_id=case.id,
                status="BLOCKED",
                reason=f"Configured external agent executable is unavailable: {self.config.command[0]}",
                config_fingerprint=fingerprint,
                budget=case.budget,
                external_execution=False,
            )
        evidence_dir = workspace / ".benchmark-evidence"
        evidence_dir.mkdir(exist_ok=True)
        command = [
            part.format(workspace=str(workspace), task_file=str(workspace / "TASK.json"))
            for part in self.config.command
        ]
        prompt = json.dumps(case.model_dump(mode="json"), sort_keys=True).encode()
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                input=prompt,
                capture_output=True,
                timeout=case.budget.timeout_seconds,
                check=False,
                env={
                    **os.environ,
                    "SACM_BENCHMARK_CASE_ID": case.id,
                    "SACM_BENCHMARK_PROVIDER": self.config.provider,
                    "SACM_BENCHMARK_MODEL": self.config.model,
                },
            )
            status: Literal["COMPLETED", "FAILED"] = (
                "COMPLETED" if completed.returncode == 0 else "FAILED"
            )
            reason = f"External agent exited with code {completed.returncode}."
        except subprocess.TimeoutExpired as exc:
            completed = subprocess.CompletedProcess(command, 124, exc.stdout or b"", exc.stderr or b"")
            status = "FAILED"
            reason = "External agent exceeded the case timeout."
        stdout_path = evidence_dir / "agent.stdout"
        stderr_path = evidence_dir / "agent.stderr"
        stdout_path.write_bytes(completed.stdout)
        stderr_path.write_bytes(completed.stderr)
        verification, evidence = _run_verification(case, workspace, evidence_dir)
        result_contract_path = workspace / self.config.result_file
        metadata: dict[str, Any] = {}
        if result_contract_path.is_file():
            try:
                metadata = json.loads(result_contract_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                status = "FAILED"
                reason = "External agent result contract is not valid JSON."
            else:
                expected_identity = {
                    "provider": self.config.provider,
                    "model": self.config.model,
                    "model_version": self.config.model_version,
                }
                if metadata.get("schema_version") != "benchmark-agent-result/v2":
                    status = "FAILED"
                    reason = "External agent omitted benchmark-agent-result/v2 metadata."
                elif metadata.get("simulated") is not False:
                    raise BenchmarkValidityError(
                        f"{case.id}: simulated baseline agent output is invalid"
                    )
                elif any(
                    metadata.get(key) != value
                    for key, value in expected_identity.items()
                ):
                    status = "FAILED"
                    reason = "External agent result model identity does not match config."
                evidence.append(_evidence(result_contract_path, "agent-result-contract"))
        else:
            status = "FAILED"
            reason = (
                f"External agent did not write required result contract "
                f"{self.config.result_file}."
            )
        diff = b"".join(
            subprocess.run(
                ["git", "diff", "--binary"],
                cwd=workspace / repository.relative_path,
                capture_output=True,
                check=False,
            ).stdout
            for repository in case.repositories
        )
        diff_path = evidence_dir / "git-diff.patch"
        diff_path.write_bytes(diff)
        evidence.extend(
            (
                _evidence(stdout_path, "agent-stdout"),
                _evidence(stderr_path, "agent-stderr"),
                _evidence(diff_path, "git-diff"),
            )
        )
        if any(item.exit_code != 0 for item in verification):
            status = "FAILED"
            reason = "One or more required verification commands failed."
        return CaseResultV2(
            case_id=case.id,
            status=status,
            reason=reason,
            config_fingerprint=fingerprint,
            budget=case.budget,
            external_execution=True,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            wall_time_seconds=time.perf_counter() - started,
            input_tokens=int(metadata.get("input_tokens", 0)),
            output_tokens=int(metadata.get("output_tokens", 0)),
            cost_usd=float(metadata.get("cost_usd", 0)),
            attempts=int(metadata.get("attempts", 1)),
            interventions=int(metadata.get("interventions", 0)),
            recovery_time_seconds=metadata.get("recovery_time_seconds"),
            requirement_coverage=(
                float(metadata.get("requirement_coverage", 0))
                if status == "COMPLETED"
                else 0.0
            ),
            regression_findings=list(metadata.get("regression_findings", [])),
            security_findings=list(metadata.get("security_findings", [])),
            verification=verification,
            evidence=evidence,
            artifacts=[
                "git-diff.patch",
                *(
                    [self.config.result_file]
                    if result_contract_path.is_file()
                    else []
                ),
            ],
        )


class SacmExecutionPlaneRunner:
    """Schedules the exact case as AgentTaskV1 on the real durable execution plane."""

    def __init__(self, config: ExecutionConfigV2) -> None:
        self.config = config

    def run_case(self, case: BenchmarkCaseV2, workspace: Path) -> CaseResultV2:
        fingerprint = self.config.fingerprint()
        if not self.config.configured():
            return CaseResultV2(
                case_id=case.id,
                status="NOT_RUN",
                reason="Database, project, enrolled executor, and model/version config are required.",
                config_fingerprint=fingerprint,
                budget=case.budget,
                external_execution=False,
            )
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from sacm.core.execution_plane_service import ExecutionPlaneService
        from sacm.core.external_agent_service import ExternalAgentService
        from sacm.core.run_service import RunService
        from sacm.infrastructure.db.models import ExecutionJob, ExecutorRegistration

        engine = create_engine(str(self.config.database_url))
        with Session(engine) as db:
            executor = (
                db.query(ExecutorRegistration)
                .filter(
                    ExecutorRegistration.project_id == self.config.project_id,
                    ExecutorRegistration.executor_identity
                    == self.config.executor_identity,
                    ExecutorRegistration.status == "ACTIVE",
                )
                .first()
            )
            if executor is None:
                return CaseResultV2(
                    case_id=case.id,
                    status="BLOCKED",
                    reason="The configured active external executor is not enrolled.",
                    config_fingerprint=fingerprint,
                    budget=case.budget,
                    external_execution=False,
                )
            started_at = datetime.now(timezone.utc)
            started = time.perf_counter()
            run = RunService(db).create(
                RunCreate(
                    title=case.title,
                    description=case.description,
                    target_repo_path=str(workspace),
                    source_revision=case.repositories[0].revision,
                    project_id=self.config.project_id,
                )
            )
            RunService(db).transition(run.id, "PLANNING", "BenchmarkRunSubmitted")
            scheduled = ExternalAgentService(db).schedule(
                run.id,
                ExternalAgentStepCreate(
                    framework="sacm-benchmark",
                    agent_name=self.config.agent_name,
                    idempotency_key=f"benchmark:{case.id}:{run.id}",
                    role="coder",
                    objective=case.description,
                    acceptance_criteria=case.acceptance_criteria,
                    context_references=[
                        f"fixture:{repo.fixture_id}@{repo.revision}"
                        for repo in case.repositories
                    ],
                    allowed_tools=case.allowed_tools,
                    denied_tools=case.denied_tools,
                    token_budget=case.budget.token_limit,
                    cost_budget_usd=case.budget.cost_limit_usd,
                    timeout_seconds=case.budget.timeout_seconds,
                    execution_context={
                        "workspace": str(workspace),
                        "verification_commands": case.verification_commands,
                        "benchmark_case_id": case.id,
                        "ablation": self.config.ablation,
                        "provider": self.config.provider,
                        "model": self.config.model,
                        "model_version": self.config.model_version,
                        "ablation_controls": ablation_controls(
                            self.config.ablation
                        ),
                    },
                ),
                trusted_internal=True,
            )
            job = ExecutionPlaneService(db).schedule(
                run_id=run.id,
                run_step_id=scheduled.step.id,
                task=scheduled.task,
                idempotency_key=f"benchmark:{case.id}:{run.id}",
                required_capabilities=["agent-task/v1"],
                required_labels=self.config.required_labels,
                max_attempts=case.budget.max_attempts,
            )
            deadline = time.monotonic() + case.budget.timeout_seconds
            while time.monotonic() < deadline:
                db.expire_all()
                current = db.get(ExecutionJob, job.id)
                if current is not None and current.state in {
                    "COMPLETED",
                    "FAILED",
                    "DEAD_LETTER",
                    "CANCELLED",
                }:
                    break
                time.sleep(self.config.poll_interval_seconds)
            db.expire_all()
            current = db.get(ExecutionJob, job.id)
            state = current.state if current is not None else "FAILED"
            evidence_dir = workspace / ".benchmark-evidence"
            evidence_dir.mkdir(exist_ok=True)
            contract_path = evidence_dir / "agent-task.json"
            contract_path.write_text(
                scheduled.task.model_dump_json(indent=2), encoding="utf-8"
            )
            verification, evidence = _run_verification(case, workspace, evidence_dir)
            evidence.append(_evidence(contract_path, "agent-task-contract"))
            step = RunService(db).get_step(run.id, scheduled.step.id)
            if step is None:
                raise BenchmarkValidityError(
                    f"{case.id}: execution-plane step disappeared before reporting"
                )
            result_path = evidence_dir / "agent-result.json"
            result_path.write_text(
                json.dumps(step.output or {}, indent=2, sort_keys=True, default=str),
                encoding="utf-8",
            )
            evidence.append(_evidence(result_path, "agent-result-contract"))
            diff = b"".join(
                subprocess.run(
                    ["git", "diff", "--binary"],
                    cwd=workspace / repository.relative_path,
                    capture_output=True,
                    check=False,
                ).stdout
                for repository in case.repositories
            )
            diff_path = evidence_dir / "git-diff.patch"
            diff_path.write_bytes(diff)
            evidence.append(_evidence(diff_path, "git-diff"))
            result_payload = (step.output or {}).get("result") or step.output or {}
            usage = result_payload.get("usage", [])
            input_tokens = sum(int(item.get("input_tokens", 0)) for item in usage)
            output_tokens = sum(int(item.get("output_tokens", 0)) for item in usage)
            cost = sum(float(item.get("estimated_cost_usd") or 0) for item in usage)
            findings = result_payload.get("findings", [])
            regression_findings = [
                str(item.get("message") or item)
                for item in findings
                if item.get("category") == "regression"
            ]
            security_findings = [
                str(item.get("message") or item)
                for item in findings
                if item.get("category") == "security"
            ]
            interventions = sum(
                item.get("type") == "human_intervention"
                for item in result_payload.get("actions", [])
            )
            completed = state == "COMPLETED" and all(
                item.exit_code == 0 for item in verification
            )
            status: Literal["COMPLETED", "FAILED"] = (
                "COMPLETED" if completed else "FAILED"
            )
            reason = (
                "External executor completed and all verification commands passed."
                if completed
                else f"Execution job ended as {state} or verification failed."
            )
            return CaseResultV2(
                case_id=case.id,
                status=status,
                reason=reason,
                config_fingerprint=fingerprint,
                budget=case.budget,
                external_execution=True,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                wall_time_seconds=time.perf_counter() - started,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                attempts=current.attempt if current is not None else 0,
                interventions=interventions,
                requirement_coverage=1.0 if completed else 0.0,
                regression_findings=regression_findings,
                security_findings=security_findings,
                verification=verification,
                evidence=evidence,
                artifacts=["git-diff.patch", "agent-task.json", "agent-result.json"],
                run_id=run.id,
                job_id=job.id,
            )


def validate_report(
    report: BenchmarkReportV2, suite: BenchmarkSuiteV2
) -> dict[str, Any]:
    validate_suite(suite)
    errors: list[str] = []
    if report.suite_id != suite.suite_id or report.suite_sha256 != suite_sha256(suite):
        errors.append("report suite identity/hash does not match the suite")
    expected = {case.id: case for case in suite.cases}
    actual = {result.case_id: result for result in report.results}
    if set(actual) != set(expected) or len(report.results) != len(expected):
        errors.append("report must contain exactly one result for every suite case")
    fingerprint = report.configuration.fingerprint()
    for case_id, result in actual.items():
        case = expected.get(case_id)
        if case is None:
            continue
        if result.simulated:
            errors.append(f"{case_id}: simulated output is invalid")
        if result.config_fingerprint != fingerprint:
            errors.append(f"{case_id}: mixed execution configuration")
        if result.budget != case.budget:
            errors.append(f"{case_id}: unequal or altered budget")
        if result.status in {"COMPLETED", "FAILED"}:
            if not result.external_execution:
                errors.append(f"{case_id}: attempted case lacks real external execution")
            if not result.evidence or not result.verification:
                errors.append(f"{case_id}: attempted case is missing evidence")
            for reference in result.evidence:
                path = Path(reference.path)
                if not path.is_file():
                    errors.append(f"{case_id}: evidence file is missing: {path}")
                elif hashlib.sha256(path.read_bytes()).hexdigest() != reference.sha256:
                    errors.append(f"{case_id}: evidence hash mismatch: {path}")
    attempted = sum(
        result.status in {"COMPLETED", "FAILED"} for result in actual.values()
    )
    expected_status = (
        "COMPLETE"
        if attempted == len(expected)
        else "PARTIAL"
        if attempted
        else "NOT_RUN"
    )
    if report.status != expected_status:
        errors.append(
            f"report status must be {expected_status} for {attempted} attempted cases"
        )
    if attempted and not report.configuration.configured_for_report():
        errors.append("attempted cases require complete external model/runner config")
    if errors:
        raise BenchmarkValidityError("; ".join(errors))
    return {
        "valid": True,
        "case_count": len(actual),
        "attempted": attempted,
        "blocked": sum(result.status == "BLOCKED" for result in actual.values()),
        "not_run": sum(result.status == "NOT_RUN" for result in actual.values()),
    }


def _aggregate(results: list[CaseResultV2]) -> dict[str, Any]:
    completed = [item for item in results if item.status in {"COMPLETED", "FAILED"}]
    accepted = sum(item.accepted_pr_proxy for item in results)
    return {
        "solved": sum(item.solved for item in results),
        "accepted_pr_proxy": accepted,
        "accepted_pr_proxy_definition": (
            "All verification commands pass, requirement coverage is 100%, no "
            "regression/security findings exist, and a git-diff artifact is present."
        ),
        "regression_rate": (
            sum(bool(item.regression_findings) for item in completed) / len(completed)
            if completed
            else None
        ),
        "mean_requirement_coverage": (
            statistics.fmean(item.requirement_coverage for item in completed)
            if completed
            else None
        ),
        "interventions": sum(item.interventions for item in results),
        "cost_per_accepted_result_usd": (
            sum(item.cost_usd for item in results) / accepted if accepted else None
        ),
        "median_recovery_time_seconds": (
            statistics.median(
                item.recovery_time_seconds
                for item in results
                if item.recovery_time_seconds is not None
            )
            if any(item.recovery_time_seconds is not None for item in results)
            else None
        ),
        "median_duration_seconds": (
            statistics.median(item.wall_time_seconds for item in completed)
            if completed
            else None
        ),
        "total_tokens": sum(
            item.input_tokens + item.output_tokens for item in results
        ),
        "security_violations": sum(len(item.security_findings) for item in results),
        "attempted": len(completed),
    }


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _bootstrap(
    pairs: list[tuple[CaseResultV2, CaseResultV2]], samples: int = 2000
) -> dict[str, dict[str, float]]:
    rng = random.Random(20260731)
    metrics = {
        "solved_rate_delta": lambda a, b: float(b.solved) - float(a.solved),
        "accepted_pr_proxy_rate_delta": lambda a, b: float(b.accepted_pr_proxy)
        - float(a.accepted_pr_proxy),
        "requirement_coverage_delta": lambda a, b: b.requirement_coverage
        - a.requirement_coverage,
        "duration_seconds_delta": lambda a, b: b.wall_time_seconds
        - a.wall_time_seconds,
        "token_usage_delta": lambda a, b: float(
            b.input_tokens + b.output_tokens - a.input_tokens - a.output_tokens
        ),
        "cost_usd_delta": lambda a, b: b.cost_usd - a.cost_usd,
        "interventions_delta": lambda a, b: float(b.interventions - a.interventions),
        "security_violations_delta": lambda a, b: float(
            len(b.security_findings) - len(a.security_findings)
        ),
        "regression_incidence_delta": lambda a, b: float(
            bool(b.regression_findings)
        )
        - float(bool(a.regression_findings)),
        "recovery_time_seconds_delta": lambda a, b: float(
            b.recovery_time_seconds or 0
        )
        - float(a.recovery_time_seconds or 0),
    }
    output: dict[str, dict[str, float]] = {}
    for name, extractor in metrics.items():
        observed = statistics.fmean(extractor(a, b) for a, b in pairs)
        draws: list[float] = []
        for _ in range(samples):
            sampled = [rng.choice(pairs) for _ in pairs]
            draws.append(statistics.fmean(extractor(a, b) for a, b in sampled))
        output[name] = {
            "estimate": observed,
            "ci95_low": _percentile(draws, 0.025),
            "ci95_high": _percentile(draws, 0.975),
        }
    return output


def compare_reports(
    baseline: BenchmarkReportV2,
    candidate: BenchmarkReportV2,
    suite: BenchmarkSuiteV2,
    *,
    minimum_paired_sample: int = 10,
) -> dict[str, Any]:
    validate_report(baseline, suite)
    validate_report(candidate, suite)
    comparable_fields = ("provider", "model", "model_version")
    mismatched = [
        field
        for field in comparable_fields
        if getattr(baseline.configuration, field)
        != getattr(candidate.configuration, field)
    ]
    if mismatched:
        raise BenchmarkValidityError(
            "reports use unequal comparison configurations: "
            + ", ".join(mismatched)
        )
    baseline_by_id = {item.case_id: item for item in baseline.results}
    candidate_by_id = {item.case_id: item for item in candidate.results}
    pairs: list[tuple[CaseResultV2, CaseResultV2]] = []
    exclusions: list[dict[str, str]] = []
    for case in suite.cases:
        left = baseline_by_id[case.id]
        right = candidate_by_id[case.id]
        if left.status not in {"COMPLETED", "FAILED"}:
            exclusions.append({"case_id": case.id, "reason": f"baseline:{left.status}"})
        elif right.status not in {"COMPLETED", "FAILED"}:
            exclusions.append({"case_id": case.id, "reason": f"candidate:{right.status}"})
        else:
            pairs.append((left, right))
    sufficient = len(pairs) >= minimum_paired_sample
    return {
        "schema_version": COMPARISON_SCHEMA,
        "status": "COMPLETE" if sufficient else "INSUFFICIENT_SAMPLE",
        "truthful_status": (
            f"Paired claims permitted for {len(pairs)} completed pairs."
            if sufficient
            else f"No comparative claims: {len(pairs)} completed pairs is below "
            f"the required minimum of {minimum_paired_sample}."
        ),
        "suite_id": suite.suite_id,
        "baseline": _aggregate(baseline.results),
        "candidate": _aggregate(candidate.results),
        "paired_completed_sample": len(pairs),
        "minimum_paired_sample": minimum_paired_sample,
        "paired_bootstrap_ci95": _bootstrap(pairs) if sufficient else None,
        "exclusions": exclusions,
        "ablations": {
            "baseline": baseline.configuration.ablation,
            "candidate": candidate.configuration.ablation,
            "supported": list(ABLATIONS),
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    status = payload.get("status", "UNKNOWN")
    truthful = payload.get("truthful_status", "No status statement supplied.")
    lines = [
        "# SACM Benchmark Report v2",
        "",
        f"> **STATUS: {status} — {truthful}**",
        "",
        "This report is evidence, not a claim beyond the completed paired sample.",
        "",
        "```json",
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        "```",
        "",
    ]
    return "\n".join(lines)


class BenchmarkService:
    """Compatibility facade for the versioned benchmark implementation."""

    load_suite = staticmethod(load_suite)
    validate_suite = staticmethod(validate_suite)
    validate_report = staticmethod(validate_report)
    compare = staticmethod(compare_reports)

    @staticmethod
    def run(
        suite: BenchmarkSuiteV2,
        config: ExecutionConfigV2,
        fixtures_root: str | Path,
    ) -> BenchmarkReportV2:
        validate_suite(suite)
        root = Path(fixtures_root)
        generator = FixtureGenerator(suite)
        generator.generate(root)
        runner: BaselineCommandRunner | SacmExecutionPlaneRunner
        if config.runner == "baseline-command":
            runner = BaselineCommandRunner(config)
        else:
            runner = SacmExecutionPlaneRunner(config)
        results = [
            runner.run_case(case, root / case.id)
            for case in suite.cases
        ]
        attempted = sum(
            result.status in {"COMPLETED", "FAILED"} for result in results
        )
        status: Literal["COMPLETE", "PARTIAL", "NOT_RUN", "INVALID"]
        status = (
            "COMPLETE"
            if attempted == len(results)
            else "PARTIAL"
            if attempted
            else "NOT_RUN"
        )
        report = BenchmarkReportV2(
            report_id=str(uuid.uuid4()),
            suite_id=suite.suite_id,
            suite_sha256=suite_sha256(suite),
            status=status,
            truthful_status=(
                f"{attempted}/{len(results)} cases executed by a real external agent; "
                f"{len(results) - attempted} were BLOCKED or NOT_RUN."
            ),
            created_at=datetime.now(timezone.utc),
            configuration=config,
            environment=capture_environment(),
            results=results,
        )
        validate_report(report, suite)
        return report


# Deliberately no v1 BenchmarkCase alias: v1 placeholder suites are invalid evidence.
