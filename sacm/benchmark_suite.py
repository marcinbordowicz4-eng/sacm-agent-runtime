from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Literal, cast

from sacm.core.benchmark_service import (
    CATEGORY_COUNTS,
    LANGUAGES,
    BenchmarkCaseV2,
    BenchmarkSuiteV2,
    BudgetV2,
    ExpectedArtifactsV2,
    FixtureGenerator,
    RepositoryFixtureV2,
    _fixture_source,
    suite_sha256,
)

CATEGORY_CRITERIA = {
    "bug": (
        "Correct the boundary-condition defect described in TASK.json without "
        "changing the public interface.",
        "Add or update focused regression coverage and preserve valid existing behavior.",
    ),
    "feature": (
        "Implement the requested capability with explicit input validation and "
        "a stable public interface.",
        "Cover the success path and at least one invalid or boundary input.",
    ),
    "refactor": (
        "Remove the identified duplication by extracting a named reusable unit "
        "without changing observable behavior.",
        "Keep the public API compatible and leave focused tests passing.",
    ),
    "migration": (
        "Adopt the new schema field while retaining a documented compatibility "
        "read path for the old field.",
        "Prefer the new representation when both forms are present and test both forms.",
    ),
    "security": (
        "Reject the unsafe input class without relying on string-prefix checks alone.",
        "Preserve valid inputs and add a regression test for the exploit-shaped input.",
    ),
    "multi-repo": (
        "Update the producer and consumer repositories to the same versioned contract.",
        "Demonstrate end-to-end compatibility and retain backward-safe handling.",
    ),
    "reliability-recovery": (
        "Implement bounded retries with deterministic backoff and an explicit terminal state.",
        "Persist or expose enough state to resume safely without duplicating completed work.",
    ),
}

CATEGORY_TASKS = {
    "bug": "Fix incorrect invoice total handling at rounding and empty-line boundaries",
    "feature": "Add configurable retry scheduling with a documented cap",
    "refactor": "Extract duplicated customer normalization while preserving the API",
    "migration": "Migrate customer identifiers to the v2 field with compatibility reads",
    "security": "Prevent repository path traversal and encoded separator bypasses",
    "multi-repo": "Version the event envelope across producer and consumer repositories",
    "reliability-recovery": "Make job retry and checkpoint recovery bounded and idempotent",
}


def _budget(category: str) -> BudgetV2:
    return BudgetV2(
        timeout_seconds=900 if category in {"multi-repo", "migration"} else 600,
        token_limit=18_000 if category == "multi-repo" else 12_000,
        cost_limit_usd=4.0 if category == "multi-repo" else 2.5,
        max_attempts=3,
    )


def _risk(category: str) -> Literal["low", "medium", "high", "critical"]:
    return {
        "bug": "medium",
        "feature": "medium",
        "refactor": "low",
        "migration": "high",
        "security": "critical",
        "multi-repo": "high",
        "reliability-recovery": "high",
    }[category]  # type: ignore[return-value]


def build_suite(
    revisions: dict[str, str] | None = None,
) -> BenchmarkSuiteV2:
    revisions = revisions or {}
    cases: list[BenchmarkCaseV2] = []
    sequence = 1
    language_variants: dict[tuple[str, str], int] = {}
    for category, total in CATEGORY_COUNTS.items():
        per_language = total // len(LANGUAGES)
        for language in LANGUAGES:
            for _ in range(per_language):
                key = (category, language)
                variant = language_variants.get(key, 0) + 1
                language_variants[key] = variant
                case_id = f"bench-{sequence:03d}"
                repo_count = 2 if category == "multi-repo" else 1
                repositories = []
                for repo_index in range(repo_count):
                    suffix = f"-{repo_index + 1}" if repo_count == 2 else ""
                    fixture_id = f"{case_id}-{language}-{category}{suffix}".replace(
                        "_", "-"
                    )
                    repositories.append(
                        RepositoryFixtureV2(
                            fixture_id=fixture_id,
                            revision=revisions.get(fixture_id, "0" * 40),
                            relative_path=(
                                f"repo-{repo_index + 1}" if repo_count == 2 else "repo"
                            ),
                        )
                    )
                criteria = [
                    *CATEGORY_CRITERIA[category],
                    (
                        f"Replace BENCHMARK_TASK_PENDING with "
                        f"{category.replace('-', '_').upper()}_IMPLEMENTED_VARIANT_{variant} "
                        "only after the implementation and tests satisfy the task."
                    ),
                ]
                case = BenchmarkCaseV2(
                    id=case_id,
                    title=f"{CATEGORY_TASKS[category]} ({language} {variant})",
                    description=(
                        f"In an original, locally generated {language} service fixture, "
                        f"{CATEGORY_TASKS[category].lower()}. Variant {variant} changes "
                        "the boundary values and contract examples in TASK.json. Work only "
                        "inside the pinned fixture repositories, keep the solution "
                        "dependency-free, and produce reviewable code plus tests."
                    ),
                    repositories=repositories,
                    language=cast(
                        Literal["python", "typescript", "react", "java", "go"],
                        language,
                    ),
                    category=cast(
                        Literal[
                            "bug",
                            "feature",
                            "refactor",
                            "migration",
                            "security",
                            "multi-repo",
                            "reliability-recovery",
                        ],
                        category,
                    ),
                    acceptance_criteria=criteria,
                    verification_commands=[["python3", "verify.py"]],
                    allowed_commands=list(
                        dict.fromkeys(
                            [
                                "git",
                                "python3",
                                {
                                    "typescript": "node",
                                    "react": "node",
                                    "java": "javac",
                                    "go": "go",
                                }.get(language, "python3"),
                            ]
                        )
                    ),
                    allowed_tools=[
                        "read",
                        "edit",
                        "search",
                        "shell",
                        "tests",
                    ],
                    denied_tools=["network", "package-publish", "credential-access"],
                    budget=_budget(category),
                    risk=_risk(category),
                    expected_artifacts=ExpectedArtifactsV2(
                        required=[
                            "source changes",
                            "focused tests",
                            "verification logs",
                            "git-diff.patch",
                        ],
                        forbidden=[
                            "generated dependencies",
                            "credentials",
                            "network-fetched source",
                        ],
                    ),
                    fixture_family=f"{language}-{category}-v2",
                    variant=variant,
                )
                cases.append(case)
                sequence += 1
    return BenchmarkSuiteV2(cases=cases)


def write_manifests(
    suite_path: str | Path,
    fixture_manifest_path: str | Path,
    work_root: str | Path,
) -> BenchmarkSuiteV2:
    work = Path(work_root)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    provisional = build_suite()
    revisions: dict[str, str] = {}
    fixtures: list[dict[str, Any]] = []
    for case in provisional.cases:
        case_root = work / case.id
        for index, repository in enumerate(case.repositories):
            repo_root = case_root / repository.relative_path
            for relative, content in _fixture_source(case, index).items():
                target = repo_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            revision = FixtureGenerator._commit(repo_root, repository.fixture_id)
            revisions[repository.fixture_id] = revision
            fixtures.append(
                {
                    "fixture_id": repository.fixture_id,
                    "case_id": case.id,
                    "language": case.language,
                    "category": case.category,
                    "relative_path": repository.relative_path,
                    "template_version": repository.template_version,
                    "revision": revision,
                    "license": "Apache-2.0",
                }
            )
    suite = build_suite(revisions)
    suite_file = Path(suite_path)
    suite_file.parent.mkdir(parents=True, exist_ok=True)
    suite_file.write_text(
        json.dumps(suite.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "sacm-benchmark-fixture-manifest/v2",
        "suite_id": suite.suite_id,
        "suite_sha256": suite_sha256(suite),
        "generator": "sacm.core.benchmark_service.FixtureGenerator",
        "deterministic_git": {
            "author": "SACM Benchmark <benchmark@sacm.invalid>",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "branch": "main",
        },
        "fixtures": fixtures,
    }
    fixture_file = Path(fixture_manifest_path)
    fixture_file.parent.mkdir(parents=True, exist_ok=True)
    fixture_file.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    shutil.rmtree(work)
    return suite
