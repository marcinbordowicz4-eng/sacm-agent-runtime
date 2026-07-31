import hashlib
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from cli.main import app
from sacm.core.benchmark_service import (
    BenchmarkReportV2,
    BenchmarkValidityError,
    CaseResultV2,
    EnvironmentV2,
    EvidenceReferenceV2,
    ExecutionConfigV2,
    FixtureGenerator,
    VerificationResultV2,
    compare_reports,
    load_suite,
    suite_sha256,
    validate_report,
    validate_suite,
)

ROOT = Path(__file__).parents[1]
SUITE_PATH = ROOT / "benchmarks" / "suite-v2.json"


def _environment() -> EnvironmentV2:
    return EnvironmentV2(
        os="test",
        architecture="test",
        python="3.11",
        git="git version test",
        tools={"node": "test", "java": "test", "go": "test"},
        repository_revision="a" * 40,
        dirty=False,
    )


def _config(
    runner: str = "baseline-command", ablation: str = "single-agent-baseline"
) -> ExecutionConfigV2:
    return ExecutionConfigV2(
        runner=runner,
        ablation=ablation,
        provider="provider",
        model="model",
        model_version="model-immutable-v1",
        agent_name="agent",
        agent_version="agent-v1",
        runtime_version="runtime-v1",
        command=["real-agent"] if runner == "baseline-command" else [],
        database_url="sqlite:///real.db" if runner == "sacm-execution-plane" else None,
        project_id="project" if runner == "sacm-execution-plane" else None,
        executor_identity="executor" if runner == "sacm-execution-plane" else None,
    )


def _reference(path: Path) -> EvidenceReferenceV2:
    return EvidenceReferenceV2(
        kind="git-diff",
        path=str(path),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _report(
    suite,
    config: ExecutionConfigV2,
    evidence_path: Path,
    completed_ids: set[str],
    *,
    accepted: bool,
) -> BenchmarkReportV2:
    evidence = _reference(evidence_path)
    results = []
    for case in suite.cases:
        if case.id not in completed_ids:
            results.append(
                CaseResultV2(
                    case_id=case.id,
                    status="NOT_RUN",
                    reason="Not selected for the paired test.",
                    config_fingerprint=config.fingerprint(),
                    budget=case.budget,
                    external_execution=False,
                )
            )
            continue
        verification = VerificationResultV2(
            command=["python3", "verify.py"],
            exit_code=0 if accepted else 1,
            duration_ms=5,
            stdout_sha256="0" * 64,
            stderr_sha256="0" * 64,
        )
        results.append(
            CaseResultV2(
                case_id=case.id,
                status="COMPLETED" if accepted else "FAILED",
                reason="Measured external execution.",
                config_fingerprint=config.fingerprint(),
                budget=case.budget,
                external_execution=True,
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                wall_time_seconds=2 if accepted else 3,
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.25,
                attempts=1,
                requirement_coverage=1 if accepted else 0.5,
                verification=[verification],
                evidence=[evidence],
                artifacts=["git-diff.patch"],
            )
        )
    return BenchmarkReportV2(
        report_id=f"report-{config.runner}-{accepted}",
        suite_id=suite.suite_id,
        suite_sha256=suite_sha256(suite),
        status="PARTIAL",
        truthful_status=f"{len(completed_ids)} cases executed.",
        created_at=datetime.now(timezone.utc),
        configuration=config,
        environment=_environment(),
        results=results,
    )


def test_suite_has_exact_required_category_and_language_balance():
    suite = load_suite(SUITE_PATH)
    readiness = validate_suite(suite)

    assert readiness["case_count"] == 100
    assert Counter(case.category for case in suite.cases) == {
        "bug": 20,
        "feature": 20,
        "refactor": 15,
        "migration": 15,
        "security": 10,
        "multi-repo": 10,
        "reliability-recovery": 10,
    }
    assert Counter(case.language for case in suite.cases) == {
        "python": 20,
        "typescript": 20,
        "react": 20,
        "java": 20,
        "go": 20,
    }
    assert all(case.repositories[0].revision != "0" * 40 for case in suite.cases)


def test_fixture_generation_is_deterministic_and_starts_failing(tmp_path):
    suite = load_suite(SUITE_PATH)
    case = suite.cases[0]
    first = tmp_path / "first"
    second = tmp_path / "second"

    FixtureGenerator(suite).generate(first, {case.id})
    FixtureGenerator(suite).generate(second, {case.id})

    for root in (first, second):
        repo = root / case.id / case.repositories[0].relative_path
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert revision == case.repositories[0].revision
    verification = subprocess.run(
        case.verification_commands[0],
        cwd=first / case.id,
        capture_output=True,
        check=False,
    )
    assert verification.returncode != 0


def test_suite_rejects_unpinned_revisions():
    suite = load_suite(SUITE_PATH)
    suite.cases[0].repositories[0].revision = "0" * 40

    with pytest.raises(BenchmarkValidityError, match="unpinned"):
        validate_suite(suite)


def test_simulated_and_incomplete_reports_are_rejected(tmp_path):
    suite = load_suite(SUITE_PATH)
    evidence_path = tmp_path / "evidence.txt"
    evidence_path.write_text("measured", encoding="utf-8")
    report = _report(suite, _config(), evidence_path, {suite.cases[0].id}, accepted=True)
    payload = report.model_dump(mode="json")
    payload["results"][0]["simulated"] = True

    with pytest.raises(ValidationError):
        BenchmarkReportV2.model_validate(payload)

    report.results.pop()
    with pytest.raises(BenchmarkValidityError, match="exactly one result"):
        validate_report(report, suite)


def test_report_rejects_mixed_config_and_missing_evidence(tmp_path):
    suite = load_suite(SUITE_PATH)
    evidence_path = tmp_path / "evidence.txt"
    evidence_path.write_text("measured", encoding="utf-8")
    case_id = suite.cases[0].id
    report = _report(suite, _config(), evidence_path, {case_id}, accepted=True)
    report.results[0].config_fingerprint = "f" * 64
    report.results[0].evidence = []

    with pytest.raises(BenchmarkValidityError) as exc:
        validate_report(report, suite)
    assert "mixed execution configuration" in str(exc.value)
    assert "missing evidence" in str(exc.value)


def test_comparison_math_and_paired_bootstrap(tmp_path):
    suite = load_suite(SUITE_PATH)
    evidence_path = tmp_path / "evidence.txt"
    evidence_path.write_text("measured", encoding="utf-8")
    paired_ids = {case.id for case in suite.cases[:10]}
    baseline = _report(suite, _config(), evidence_path, paired_ids, accepted=False)
    candidate = _report(
        suite,
        _config("sacm-execution-plane", "sacm-full"),
        evidence_path,
        paired_ids,
        accepted=True,
    )

    comparison = compare_reports(baseline, candidate, suite)

    assert comparison["status"] == "COMPLETE"
    assert comparison["paired_completed_sample"] == 10
    assert comparison["baseline"]["solved"] == 0
    assert comparison["candidate"]["accepted_pr_proxy"] == 10
    solved = comparison["paired_bootstrap_ci95"]["solved_rate_delta"]
    assert solved == {"estimate": 1.0, "ci95_low": 1.0, "ci95_high": 1.0}


def test_comparison_refuses_claims_for_insufficient_sample(tmp_path):
    suite = load_suite(SUITE_PATH)
    evidence_path = tmp_path / "evidence.txt"
    evidence_path.write_text("measured", encoding="utf-8")
    paired_ids = {case.id for case in suite.cases[:9]}
    baseline = _report(suite, _config(), evidence_path, paired_ids, accepted=False)
    candidate = _report(
        suite,
        _config("sacm-execution-plane", "sacm-no-reviewer"),
        evidence_path,
        paired_ids,
        accepted=True,
    )

    comparison = compare_reports(baseline, candidate, suite)

    assert comparison["status"] == "INSUFFICIENT_SAMPLE"
    assert comparison["paired_bootstrap_ci95"] is None
    assert comparison["paired_completed_sample"] == 9
    assert comparison["exclusions"]


def test_cli_validate_generate_and_command_surface(tmp_path):
    runner = CliRunner()

    validated = runner.invoke(
        app, ["benchmark", "validate", "--suite", str(SUITE_PATH)]
    )
    assert validated.exit_code == 0
    assert "'case_count': 100" in validated.stdout

    generated = runner.invoke(
        app,
        [
            "benchmark",
            "generate",
            str(tmp_path / "fixtures"),
            "--suite",
            str(SUITE_PATH),
            "--case-id",
            "bench-001",
        ],
    )
    assert generated.exit_code == 0
    assert "'case_count': 1" in generated.stdout

    help_result = runner.invoke(app, ["benchmark", "--help"])
    assert help_result.exit_code == 0
    for command in ("generate", "validate", "run", "compare", "report"):
        assert command in help_result.stdout
