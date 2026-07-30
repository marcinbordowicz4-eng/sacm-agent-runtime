import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    title: str
    description: str
    target_repo_path: str | None = None
    category: str = "general"


class BenchmarkService:
    """Runs named cases and compares measured execution reports."""

    def run(
        self,
        cases: list[BenchmarkCase],
        execute: Callable[[BenchmarkCase], dict[str, Any]],
    ) -> dict[str, Any]:
        self.validate_suite(cases, minimum_cases=1)
        results: list[dict[str, Any]] = []
        for case in cases:
            started = time.perf_counter()
            output = execute(case)
            elapsed_ms = round((time.perf_counter() - started) * 1_000)
            status = str(output.get("status", "UNKNOWN"))
            results.append(
                {
                    "case": asdict(case),
                    "status": status,
                    "passed": status in {"COMPLETED", "done"},
                    "duration_ms": elapsed_ms,
                    "output": output,
                }
            )
        passed = sum(result["passed"] for result in results)
        return {
            "schema_version": "sacm-benchmark/v1",
            "case_count": len(results),
            "passed_count": passed,
            "pass_rate": passed / len(results),
            "median_duration_ms": statistics.median(
                result["duration_ms"] for result in results
            ),
            "results": results,
        }

    @staticmethod
    def load_suite(path: str) -> list[BenchmarkCase]:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("Benchmark suite must be a JSON array.")
        return [BenchmarkCase(**case) for case in data]

    @staticmethod
    def validate_suite(
        cases: list[BenchmarkCase], *, minimum_cases: int = 50
    ) -> dict[str, Any]:
        if len(cases) < minimum_cases:
            raise ValueError(
                f"Benchmark suite requires at least {minimum_cases} cases; "
                f"received {len(cases)}."
            )
        identifiers = [case.id for case in cases]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Benchmark case IDs must be unique.")
        placeholders = [case.id for case in cases if case.id.startswith("replace-me")]
        if placeholders:
            raise ValueError("Benchmark suite contains placeholder case IDs.")
        categories = sorted({case.category for case in cases})
        return {
            "schema_version": "sacm-benchmark-suite/v1",
            "case_count": len(cases),
            "categories": categories,
            "ready": True,
        }

    @staticmethod
    def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        for report in (baseline, candidate):
            if report.get("schema_version") != "sacm-benchmark/v1":
                raise ValueError("Unsupported benchmark report schema.")
        return {
            "baseline_case_count": baseline["case_count"],
            "candidate_case_count": candidate["case_count"],
            "pass_rate_delta": candidate["pass_rate"] - baseline["pass_rate"],
            "median_duration_ms_delta": (
                candidate["median_duration_ms"] - baseline["median_duration_ms"]
            ),
        }
